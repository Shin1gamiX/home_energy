import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from export_energy import (FIELDS, CHANNEL_FIELDS, FLOW_OBSERVATIONS_ENTITY, SNAPSHOT_KEYS, PREFIX, PV_PREFIX,
                           ChargingDiscrepancy, estimate_charging, estimate_grid_house, estimate_house_sources, snapshot)
from history_store import record


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript('CREATE TABLE states_meta(metadata_id INTEGER,entity_id TEXT);'
                             'CREATE TABLE state_attributes(attributes_id INTEGER,shared_attrs TEXT);'
                             'CREATE TABLE states(metadata_id INTEGER,state TEXT,last_updated_ts REAL,attributes_id INTEGER);')
        for index, (key, (suffix, unit)) in enumerate(FIELDS.items(), 1):
            prefix = PV_PREFIX if key in CHANNEL_FIELDS else PREFIX
            self.db.execute('INSERT INTO states_meta VALUES(?,?)', (index, prefix + suffix))
            self.db.execute('INSERT INTO state_attributes VALUES(?,?)', (index, '{"unit_of_measurement":"' + unit + '"}'))
            value = {'soc': '84', 'battery': '-500'}.get(key, '0')
            self.db.execute('INSERT INTO states VALUES(?,?,?,?)', (index, value, 990, index))
        self.db.execute('INSERT INTO states_meta VALUES(101,?)', (PREFIX + 'inverter_time',))
        self.db.execute("INSERT INTO states VALUES(101,'17:00:00',990,NULL)")
        self.db.execute('INSERT INTO states_meta VALUES(301,?)', (PREFIX + 'power_flow_status',))
        self.db.execute("INSERT INTO states VALUES(301,'853',990,NULL)")

    def field_id(self, key):
        return list(FIELDS).index(key) + 1

    def set_readings(self, **values):
        for key, value in values.items():
            self.db.execute('UPDATE states SET state=? WHERE metadata_id=?', (str(value), self.field_id(key)))

    def set_time(self, key, timestamp):
        self.db.execute('UPDATE states SET last_updated_ts=? WHERE metadata_id=?', (timestamp, self.field_id(key)))

    def add_flow_helper(self, attributes, timestamp=999, state='1'):
        self.db.execute('INSERT INTO states_meta VALUES(201,?)', (FLOW_OBSERVATIONS_ENTITY,))
        self.db.execute('INSERT INTO state_attributes VALUES(201,?)', (json.dumps(attributes),))
        self.db.execute('INSERT INTO states(metadata_id,state,last_updated_ts,attributes_id) VALUES(201,?,?,201)',
                        (state, timestamp))

    def test_repeated_value_with_no_new_recorder_row_can_supply_the_house(self):
        self.db.execute('ALTER TABLE states ADD COLUMN last_reported_ts REAL')
        self.set_readings(grid=0, bridge=569, load=657, battery=-37, pv=482)
        self.set_time('battery', 411.123456)
        before = snapshot(self.db, 1000)
        self.assertEqual(before['status'], 'partial')
        self.assertIsNone(before['values']['battery_house'])
        self.add_flow_helper({'reports': {PREFIX + 'battery_power': ['-37', 411.123456, 990]}})
        after = snapshot(self.db, 1000)
        self.assertEqual(after['status'], 'live')
        self.assertEqual([after['values'][k] for k in ('solar_house', 'battery_house', 'grid_house', 'grid_battery')],
                         [482, 37, 0, 0])

    def test_fresh_helper_cannot_replace_missing_or_invalid_sensor_evidence(self):
        self.set_readings(grid=0, bridge=569, load=657, battery=-37, pv=482)
        self.set_time('battery', 400)
        self.add_flow_helper({})
        key = PREFIX + 'battery_power'
        for description, attributes in (
            ('wrong entity', {'reports': {PREFIX + 'load_power': ['-37', 400, 998]}}),
            ('same value from a different revision', {'reports': {key: ['-37', 390, 998]}}),
            ('value mismatch', {'reports': {key: ['37', 400, 998]}}),
            ('source went silent', {'reports': {key: ['-37', 400, 909]}}),
            ('report after helper sampled', {'reports': {key: ['-37', 400, 999.5]}}),
            ('future report', {'reports': {key: ['-37', 400, 1040]}}),
            ('report before revision', {'reports': {key: ['-37', 400, 399]}}),
            ('restored helper', {'restored': True, 'reports': {key: ['-37', 400, 998]}}),
            ('invalid timestamps', {'reports': {key: ['-37', 400, '998']}}),
            ('invalid entry', {'reports': {key: '998'}}),
            ('invalid map', {'reports': []}),
        ):
            with self.subTest(description=description):
                self.db.execute('UPDATE state_attributes SET shared_attrs=? WHERE attributes_id=201',
                                (json.dumps(attributes),))
                result = snapshot(self.db, 1000)
                self.assertEqual(result['status'], 'partial')
                self.assertIsNone(result['values']['battery_house'])
                self.assertIsNone(result['values']['solar_house'])

    def test_freshness_boundary_and_explicit_outage(self):
        self.set_readings(grid=0, bridge=569, load=657, battery=-37, pv=482)
        self.set_time('battery', 400)
        self.add_flow_helper({'reports': {PREFIX + 'battery_power': ['-37', 400, 910]}})
        self.assertEqual(snapshot(self.db, 1000)['values']['battery_house'], 37)
        self.assertIsNone(snapshot(self.db, 1000.001)['values']['battery_house'])
        self.set_readings(battery='unavailable')
        self.set_time('battery', 999)
        self.assertIsNone(snapshot(self.db, 1000)['values']['battery_house'])

    def test_unavailable_helper_falls_back_to_each_sensors_own_recorder_row(self):
        self.set_readings(grid=0, bridge=569, load=657, battery=-37, pv=482)
        self.set_time('battery', 400)
        self.add_flow_helper({'reports': {PREFIX + 'battery_power': ['-37', 400, 998]}}, state='unavailable')
        self.assertIsNone(snapshot(self.db, 1000)['values']['battery_house'])
        self.db.execute('ALTER TABLE states ADD COLUMN last_reported_ts REAL')
        self.db.execute('UPDATE states SET last_reported_ts=998 WHERE metadata_id=?', (self.field_id('battery'),))
        self.assertEqual(snapshot(self.db, 1000)['values']['battery_house'], 37)

    def test_reference_cycle_records_split_and_independent_import(self):
        self.db.execute("UPDATE states SET state='597' WHERE metadata_id=301")
        self.set_readings(grid=720, bridge=-68, load=662, battery=2964, pv=3125)
        payload = snapshot(self.db, 1000)
        values = payload['values']
        self.assertEqual((values['grid_house'], values['grid_battery'], values['grid']), (652, 0, 720))
        self.assertEqual((values['solar_house'], values['battery_house'], values['solar_battery']), (0, 0, 2964))
        with TemporaryDirectory() as tmp:
            record(payload, tmp)
            day = json.loads((Path(tmp) / 'history' / '1970-01-01.json').read_text())
            recorded = day['points'][0]['values']
            self.assertEqual((recorded['grid_house'], recorded['grid_battery'], recorded['grid']), (652, 0, 720))

    def test_solar_and_grid_charge_together(self):
        self.set_readings(grid=4657, bridge=-2900, load=1679, battery=5483, pv=2958)
        values = snapshot(self.db, 1000)['values']
        self.assertEqual((values['grid_house'], values['grid_battery'], values['solar_battery']), (1679, 2525, 2958))
        self.assertEqual((values['solar_house'], values['battery_house']), (0, 0))

    def test_forward_bridge_caps_house_source_allocation(self):
        self.set_readings(grid=450, bridge=570, load=1000, battery=-250, pv=900)
        values = snapshot(self.db, 1000)['values']
        self.assertEqual((values['grid_house'], values['grid_battery']), (430, 0))
        self.assertEqual((values['solar_house'], values['battery_house']), (320, 250))

    def test_zero_import_preserves_offgrid_routes_without_bridge(self):
        self.set_readings(grid=-30, bridge='unavailable', load=662, battery=-250, pv=480)
        values = snapshot(self.db, 1000)['values']
        self.assertEqual((values['grid'], values['grid_house'], values['grid_battery']), (0, 0, 0))
        self.assertEqual((values['solar_house'], values['battery_house']), (412, 250))

    def test_independent_zeros_and_missing_inputs(self):
        for grid, bridge, load, battery, pv, result in (
            (990, None, 0, 1000, 0, (0, None)), (990, None, 800, -155, 0, (None, 0)),
            (100, -300, 0, 900, 0, (0, 100)), (800, -300, 600, None, None, (500, None)),
            (None, 900, 600, None, None, (0, 0)),
        ):
            with self.subTest(grid=grid, battery=battery):
                self.assertEqual((estimate_grid_house(grid, bridge, load),
                                  estimate_charging(grid, bridge, battery, pv, True, True)[0]), result)
        self.assertEqual(estimate_house_sources(None, None, None, 710, None, None), (None, None))

    def test_pv_only_uses_actual_status_despite_grid_import_and_inward_bridge(self):
        self.set_readings(grid=1037, bridge=-72, load=992, battery=1046, pv=996)
        self.db.execute("UPDATE states SET state='597' WHERE metadata_id=301")
        payload = snapshot(self.db, 1000)
        self.assertEqual(payload['charging_routing'], {'mains_active': False, 'solar_active': True,
                                                       'discrepancy_w': 50, 'persistent': False})
        values = payload['values']
        self.assertEqual((values['grid'], values['grid_house'], values['grid_battery'], values['solar_battery']),
                         (1037, 965, 0, 996))
        self.assertEqual((values['solar_house'], values['battery_house']), (0, 0))

    def test_charging_modes_and_bounds(self):
        cases = [
            # Both flags, independently reported charger watts, enough PV to cover all demand.
            (1000, -600, 700, 900, True, True, 200, (200, 500, 0)),
            (1000, -600, 700, 900, True, True, None, (None, None, 0)),
            (1000, -600, 700, 900, True, True, 2000, (600, 100, 0)),
            (1500, -1200, 1300, 500, True, True, 100, (100, 500, 700)),
            (1000, -600, 700, 120, True, True, None, (580, 120, 0)),
            (1000, -600, 700, 900, True, False, None, (600, 0, 100)),
            (1000, -600, 700, 900, False, False, None, (0, 0, 700)),
            (1000, -600, 700, 900, None, None, None, (None, None, None)),
            (0, None, 700, 900, None, None, None, (0, 700, None)),
            (None, 150, 700, 900, None, None, None, (0, 700, None)),
            (1000, -600, 700, 0, None, None, None, (None, 0, None)),
            (1000, -600, -25, 900, None, None, None, (0, 0, None)),
            (1000, None, 700, 900, False, True, None, (0, 700, 0)),
            (1000, -600, 700, None, True, False, None, (600, 0, 100)),
        ]
        for *inputs, result in cases:
            with self.subTest(inputs=inputs):
                self.assertEqual(estimate_charging(*inputs), result)

    def test_unavailable_or_invalid_charging_status_cannot_attribute_grid_watts(self):
        self.set_readings(grid=1000, bridge=-600, load=400, battery=700, pv=100)
        for raw in ('unknown', 'unavailable', '-1', '65536', '597.5', 'NaN', 'Infinity'):
            with self.subTest(raw=raw):
                self.db.execute("UPDATE states SET state=? WHERE metadata_id=301", (raw,))
                payload = snapshot(self.db, 1000)
                self.assertEqual(payload['charging_routing'], dict(mains_active=None, solar_active=None,
                                                                    discrepancy_w=None, persistent=False))
                self.assertEqual(payload['values']['grid_house'], 400)
                self.assertIsNone(payload['values']['grid_battery'])
        self.db.execute("UPDATE states SET state='597',attributes_id=? WHERE metadata_id=301", (self.field_id('pv'),))
        self.assertIsNone(snapshot(self.db, 1000)['charging_routing']['mains_active'])

    def test_charging_status_and_charger_watts_have_their_own_freshness_evidence(self):
        self.set_readings(grid=1000, bridge=-600, load=400, battery=700, pv=900)
        self.db.execute("UPDATE states SET last_updated_ts=400 WHERE metadata_id=301")
        self.db.execute('INSERT INTO states_meta VALUES(302,?)', (PREFIX + 'inverter_charging_power',))
        self.db.execute("INSERT INTO states VALUES(302,'200',420,?)", (self.field_id('grid'),))
        self.assertIsNone(snapshot(self.db, 1000)['values']['grid_battery'])
        status = {PREFIX + 'power_flow_status': ['853', 400, 990]}
        self.add_flow_helper({'charging_reports': status})
        self.assertIsNone(snapshot(self.db, 1000)['values']['grid_battery'])
        status[PREFIX + 'inverter_charging_power'] = ['200', 420, 992]
        self.db.execute('UPDATE state_attributes SET shared_attrs=? WHERE attributes_id=201',
                        (json.dumps({'charging_reports': status}),))
        result = snapshot(self.db, 1000)
        self.assertEqual((result['values']['grid_battery'], result['values']['solar_battery']), (200, 500))
        self.db.execute("UPDATE states SET state='unavailable',last_updated_ts=995 WHERE metadata_id=301")
        self.assertIsNone(snapshot(self.db, 1000)['values']['grid_battery'])

    def test_discrepancy_requires_continuous_independent_reports_for_two_minutes(self):
        self.set_readings(grid=1100, bridge=-900, load=200, battery=1500, pv=1350)
        self.db.execute("UPDATE states SET state='597' WHERE metadata_id=301")
        tracker = ChargingDiscrepancy()

        def sample(now, advance=True):
            if advance:
                self.db.execute("UPDATE states SET last_updated_ts=?", (now,))
            return snapshot(self.db, now, tracker)['charging_routing']

        for stamp in (1000, 1030, 1060, 1090, 1119):
            self.assertFalse(sample(stamp)['persistent'])
        self.assertFalse(sample(1120, advance=False)['persistent'])
        self.assertTrue(sample(1120)['persistent'])
        self.assertTrue(sample(1125, advance=False)['persistent'])
        self.set_readings(battery=2000, pv=1801)  # Above 150 W, just below 10%.
        self.assertFalse(sample(1130)['persistent'])
        self.set_readings(battery=2000, pv=1800)
        for stamp in (1140, 1180, 1220):
            self.assertFalse(sample(stamp)['persistent'])
        self.assertFalse(sample(1270)['persistent'])  # 50 s gap
        self.db.execute("UPDATE states SET state='85' WHERE metadata_id=301")  # source regime changed
        self.assertFalse(sample(1310)['persistent'])
        self.assertFalse(sample(1350)['persistent'])
        self.db.execute("UPDATE states SET state='unavailable' WHERE metadata_id=301")
        self.assertIsNone(sample(1390)['discrepancy_w'])
        self.db.execute("UPDATE states SET state='85' WHERE metadata_id=301")
        for stamp in (1400, 1440, 1480):
            self.assertFalse(sample(stamp)['persistent'])
        self.assertTrue(sample(1520)['persistent'])
        self.assertFalse(snapshot(self.db, 1520, ChargingDiscrepancy())['charging_routing']['persistent'])
        self.assertFalse(sample(1620, advance=False)['persistent'])

    def test_heartbeat_alone_cannot_extend_discrepancy(self):
        self.set_readings(grid=1100, bridge=-900, load=200, battery=1500, pv=1000)
        self.db.execute("UPDATE states SET state='597' WHERE metadata_id=301")
        tracker = ChargingDiscrepancy()
        for stamp in range(990, 1200, 10):
            self.db.execute("UPDATE states SET last_updated_ts=? WHERE metadata_id=101", (stamp,))
            self.assertFalse(snapshot(self.db, stamp, tracker)['charging_routing']['persistent'])

    def test_persistent_dual_source_discrepancy_requires_repeated_charger_observations(self):
        self.set_readings(grid=1500, bridge=-1200, load=300, battery=1300, pv=500)
        self.db.execute('INSERT INTO states_meta VALUES(302,?)', (PREFIX + 'inverter_charging_power',))
        self.db.execute("INSERT INTO states VALUES(302,'100',990,?)", (self.field_id('grid'),))
        tracker = ChargingDiscrepancy()
        for stamp in range(990, 1111, 30):
            self.db.execute('UPDATE states SET last_updated_ts=? WHERE metadata_id<>302', (stamp,))
            state = snapshot(self.db, stamp, tracker)['charging_routing']
            self.assertEqual(state['discrepancy_w'], 700 if stamp <= 1080 else 0)
            self.assertFalse(state['persistent'])
        for stamp in range(1140, 1261, 30):
            self.db.execute('UPDATE states SET last_updated_ts=?', (stamp,))
            state = snapshot(self.db, stamp, tracker)['charging_routing']
            self.assertEqual(state['discrepancy_w'], 700)
            self.assertEqual(state['persistent'], stamp == 1260)

    def test_channels_do_not_double_count_total(self):
        for key, value in [('pv', 3000), ('pv1_power', 1800), ('pv2_power', 1200)]:
            index = list(FIELDS).index(key) + 1
            self.db.execute('UPDATE states SET state=? WHERE metadata_id=?', (str(value), index))
        values = snapshot(self.db, 1000)['values']
        self.assertEqual(values['pv'], 3000)
        self.assertEqual(values['pv1_power'], 1800)
        self.assertEqual(values['pv2_power'], 1200)

    def test_missing_channel_is_not_zero(self):
        index = list(FIELDS).index('pv2_power') + 1
        self.db.execute('DELETE FROM states WHERE metadata_id=?', (index,))
        result = snapshot(self.db, 1000)
        self.assertIsNone(result['values']['pv2_power'])
        self.assertEqual(result['status'], 'partial')

    def test_missing_grid_input_is_not_zero(self):
        self.db.execute('DELETE FROM states WHERE metadata_id=?', (self.field_id('grid'),))
        self.set_readings(load=900, battery=700, bridge=-450, pv=100)
        result = snapshot(self.db, 1000)
        self.assertIsNone(result['values']['grid'])
        self.assertIsNone(result['values']['grid_battery'])
        self.assertIsNone(result['values']['grid_house'])
        self.assertEqual(result['status'], 'partial')

    def test_invalid_bridge_never_falls_back_to_eybond_allocation(self):
        self.set_readings(grid=800, load=720, battery=1000, pv=70)
        self.db.execute("INSERT INTO states_meta VALUES(202,?)", (PREFIX + 'grid_to_battery_power',))
        self.db.execute("INSERT INTO states VALUES(202,'800',999,?)", (self.field_id('grid'),))
        for invalid in ('unknown', 'unavailable', 'NaN', 'Infinity'):
            with self.subTest(value=invalid):
                self.set_readings(bridge=invalid)
                values = snapshot(self.db, 1000)['values']
                self.assertEqual((values['grid_house'], values['grid_battery'], values['solar_house']),
                                 (None, None, None))
                self.assertEqual(values['grid'], 800)

    def test_stale_bridge_produces_gaps_until_reported_again(self):
        self.set_readings(grid=700, bridge=-100, load=602, battery=1000, pv=420)
        self.set_time('bridge', 100)
        old = snapshot(self.db, 1000)['values']
        self.assertEqual((old['grid_house'], old['grid_battery'], old['bridge']), (None, None, None))
        self.db.execute('ALTER TABLE states ADD COLUMN last_reported_ts REAL')
        self.db.execute('UPDATE states SET last_reported_ts=992 WHERE metadata_id=?', (self.field_id('bridge'),))
        fresh = snapshot(self.db, 1000)['values']
        self.assertEqual((fresh['grid_house'], fresh['grid_battery'], fresh['bridge']), (600, 100, -100))

    def test_old_nonzero_auxiliary_input_cannot_invent_charging(self):
        self.set_readings(grid=700, bridge=-100, load=602, battery=1000, pv=420)
        self.set_time('pv', 100)
        values = snapshot(self.db, 1000)['values']
        self.assertEqual(values['grid_house'], 600)
        self.assertIsNone(values['grid_battery'])
        self.assertIsNone(values['solar_battery'])
        self.set_time('grid', 100)
        values = snapshot(self.db, 1000)['values']
        self.assertIsNone(values['grid'])
        self.assertIsNone(values['grid_house'])

    def test_restored_and_future_measurements_are_unknown(self):
        self.set_readings(grid=700, bridge=-100, load=602, battery=1000, pv=420)
        self.db.execute('UPDATE state_attributes SET shared_attrs=? WHERE attributes_id=?',
                        ('{"unit_of_measurement":"W","restored":true}', self.field_id('bridge')))
        self.set_time('load', 2000)
        values = snapshot(self.db, 1000)['values']
        self.assertIsNone(values['bridge'])
        self.assertIsNone(values['grid_battery'])
        self.assertIsNone(values['load'])

    def test_corroborated_offgrid_and_nighttime_zeros(self):
        self.set_readings(grid=0, load=850, battery=-700, pv=0)
        self.db.execute('INSERT INTO states_meta VALUES(102,?)', (PREFIX + 'operating_mode',))
        self.db.execute("INSERT INTO states VALUES(102,'Off-Grid',1,NULL)")
        for key in ('bridge', 'grid', 'pv'):
            self.set_time(key, 1)
        values = snapshot(self.db, 1000)['values']
        self.assertEqual((values['grid_house'], values['grid_battery'], values['solar_house'], values['battery_house']),
                         (0, 0, 0, 700))

        self.set_readings(grid=900, bridge=-300, load=580, battery=400)
        self.set_time('grid', 990)
        self.set_time('bridge', 990)
        values = snapshot(self.db, 1000)['values']
        self.assertEqual((values['grid_house'], values['grid_battery']), (580, 300))
        for key in ('pv1_power', 'pv2_power'):
            self.set_time(key, 1)
        values = snapshot(self.db, 1000)['values']
        self.assertIsNone(values['grid_battery'])
        self.assertIsNone(values['solar_battery'])

    def test_restored_mode_cannot_confirm_an_old_zero_import(self):
        self.set_readings(grid=0, bridge='unavailable', load=350, battery=1000, pv=400)
        self.set_time('grid', 1)
        self.db.execute('INSERT INTO states_meta VALUES(102,?)', (PREFIX + 'operating_mode',))
        self.db.execute("""INSERT INTO state_attributes VALUES(102,'{"restored":true}')""")
        self.db.execute("INSERT INTO states VALUES(102,'Off-Grid',999,102)")
        values = snapshot(self.db, 1000)['values']
        self.assertIsNone(values['grid_house'])
        self.assertIsNone(values['grid_battery'])

    def test_constant_soc_is_fresh_when_heartbeat_is_fresh(self):
        self.set_time('soc', 1)
        result = snapshot(self.db, 1000)
        self.assertEqual(result['status'], 'live')
        self.assertEqual(result['values']['battery'], -500)
        self.assertEqual(result['values']['soc'], 84)
        self.assertEqual(set(result['values']), set(SNAPSHOT_KEYS))

    def test_stale_heartbeat_is_not_live(self):
        self.assertEqual(snapshot(self.db, 1200)['status'], 'stale')

    def test_invalid_value_is_null(self):
        self.db.execute("UPDATE states SET state='unavailable' WHERE metadata_id=?", (self.field_id('soc'),))
        result = snapshot(self.db, 1000)
        self.assertEqual(result['status'], 'partial')
        self.assertIsNone(result['values']['soc'])

    def test_invalid_unit_is_rejected(self):
        self.set_readings(load=355)
        self.db.execute('UPDATE state_attributes SET shared_attrs=? WHERE attributes_id=?',
                        ('{"unit_of_measurement":"V"}', self.field_id('grid')))
        values = snapshot(self.db, 1000)['values']
        self.assertIsNone(values['grid_house'])
        self.assertIsNone(values['grid'])
        self.assertEqual(values['grid_battery'], 0)

    def test_battery_voltage_is_measured_not_derived(self):
        index = list(FIELDS).index('battery_voltage') + 1
        self.assertEqual(FIELDS['battery_voltage'], ('battery_voltage', 'V'))
        for value in ('51.6', '0'):
            self.db.execute('UPDATE states SET state=? WHERE metadata_id=?', (value, index))
            self.assertEqual(snapshot(self.db, 1000)['values']['battery_voltage'], float(value))

    def test_unavailable_or_invalid_battery_voltage_is_not_zero(self):
        index = list(FIELDS).index('battery_voltage') + 1
        for value in ('unknown', 'unavailable', 'NaN', 'Infinity', ''):
            with self.subTest(value=value):
                self.db.execute('UPDATE states SET state=? WHERE metadata_id=?', (value, index))
                result = snapshot(self.db, 1000)
                self.assertIsNone(result['values']['battery_voltage'])
                self.assertEqual(result['status'], 'partial')
        self.db.execute('DELETE FROM states WHERE metadata_id=?', (index,))
        self.assertIsNone(snapshot(self.db, 1000)['values']['battery_voltage'])

    def test_battery_voltage_rejects_wrong_unit(self):
        index = list(FIELDS).index('battery_voltage') + 1
        self.db.execute('UPDATE state_attributes SET shared_attrs=? WHERE attributes_id=?',
                        ('{"unit_of_measurement":"mV"}', index))
        self.assertIsNone(snapshot(self.db, 1000)['values']['battery_voltage'])


if __name__ == '__main__':
    unittest.main()
