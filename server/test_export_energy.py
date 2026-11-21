import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from export_energy import (FIELDS, CHANNEL_FIELDS, SNAPSHOT_KEYS, PREFIX, PV_PREFIX,
                           estimate_grid_split, estimate_house_sources, snapshot)
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

    def field_id(self, key):
        return list(FIELDS).index(key) + 1

    def set_readings(self, **values):
        for key, value in values.items():
            self.db.execute('UPDATE states SET state=? WHERE metadata_id=?', (str(value), self.field_id(key)))

    def set_time(self, key, timestamp):
        self.db.execute('UPDATE states SET last_updated_ts=? WHERE metadata_id=?', (timestamp, self.field_id(key)))

    def test_reference_cycle_records_split_and_independent_import(self):
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
        self.assertEqual(estimate_grid_split(990, None, 0, 1000, 0), (0, None))
        self.assertEqual(estimate_grid_split(990, None, 800, -155, 0), (None, 0))
        self.assertEqual(estimate_grid_split(100, -300, 0, 900, 0), (0, 100))
        self.assertEqual(estimate_grid_split(800, -300, 600, None, None), (500, None))
        self.assertEqual(estimate_grid_split(None, 900, 600, None, None), (0, 0))
        self.assertEqual(estimate_house_sources(None, None, None, 710, None, None), (None, None))

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
