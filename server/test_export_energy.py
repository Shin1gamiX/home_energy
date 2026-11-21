import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from export_energy import FIELDS, CHANNEL_FIELDS, PREFIX, PV_PREFIX, snapshot
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
            value = {'soc': '84', 'battery': '-500', 'grid_battery': '181'}.get(key, '0')
            self.db.execute('INSERT INTO states VALUES(?,?,?,?)', (index, value, 1, index))
        self.db.execute('INSERT INTO states_meta VALUES(101,?)', (PREFIX + 'inverter_time',))
        self.db.execute("INSERT INTO states VALUES(101,'17:00:00',990,NULL)")

    def field_id(self, key):
        return list(FIELDS).index(key) + 1

    def test_grid_includes_house_and_battery(self):
        self.db.execute("UPDATE states SET state='678' WHERE metadata_id=?", (self.field_id('grid_house'),))
        payload = snapshot(self.db, 1000)
        values = payload['values']
        self.assertEqual((values['grid_house'], values['grid_battery'], values['grid']), (678, 181, 859))
        with TemporaryDirectory() as tmp:
            record(payload, tmp)
            day = json.loads((Path(tmp) / 'history' / '1970-01-01.json').read_text())
            recorded = day['points'][0]['values']
            self.assertEqual(recorded['grid'], 859)
            self.assertEqual((recorded['grid_house'], recorded['grid_battery']), (678, 181))

    def test_grid_can_charge_without_supplying_the_house(self):
        values = snapshot(self.db, 1000)['values']
        self.assertEqual((values['grid_house'], values['grid_battery'], values['grid']), (0, 181, 181))

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

    def test_missing_grid_component_is_not_zero(self):
        self.db.execute('DELETE FROM states WHERE metadata_id=?', (self.field_id('grid_battery'),))
        result = snapshot(self.db, 1000)
        self.assertIsNone(result['values']['grid'])
        self.assertIsNone(result['values']['grid_battery'])
        self.assertEqual(result['values']['grid_house'], 0)
        self.assertEqual(result['status'], 'partial')

    def test_invalid_grid_destination_keeps_the_other_reading(self):
        for key, other, expected in [('grid_battery', 'grid_house', 0), ('grid_house', 'grid_battery', 181)]:
            for invalid in ['-1', 'NaN', 'Infinity', 'unavailable']:
                with self.subTest(key=key, invalid=invalid):
                    index = self.field_id(key)
                    self.db.execute('UPDATE states SET state=? WHERE metadata_id=?', (invalid, index))
                    result = snapshot(self.db, 1000)
                    self.assertIsNone(result['values'][key])
                    self.assertIsNone(result['values']['grid'])
                    self.assertEqual(result['values'][other], expected)
                    self.assertEqual(result['status'], 'partial')
            self.db.execute('UPDATE states SET state=? WHERE metadata_id=?', ('0' if key == 'grid_house' else '181', index))

    def test_constant_values_are_fresh_when_heartbeat_is_fresh(self):
        result = snapshot(self.db, 1000)
        self.assertEqual(result['status'], 'live')
        self.assertEqual(result['values']['battery'], -500)
        self.assertEqual(result['values']['soc'], 84)
        self.assertEqual(set(result['values']), set(FIELDS) | {'grid'})

    def test_stale_heartbeat_is_not_live(self):
        self.assertEqual(snapshot(self.db, 1200)['status'], 'stale')

    def test_invalid_value_is_null(self):
        self.db.execute("UPDATE states SET state='unavailable' WHERE metadata_id=?", (self.field_id('soc'),))
        result = snapshot(self.db, 1000)
        self.assertEqual(result['status'], 'partial')
        self.assertIsNone(result['values']['soc'])

    def test_invalid_unit_is_rejected(self):
        self.db.execute('UPDATE state_attributes SET shared_attrs=? WHERE attributes_id=?',
                        ('{"unit_of_measurement":"V"}', self.field_id('grid_house')))
        values = snapshot(self.db, 1000)['values']
        self.assertIsNone(values['grid_house'])
        self.assertIsNone(values['grid'])
        self.assertEqual(values['grid_battery'], 181)

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
