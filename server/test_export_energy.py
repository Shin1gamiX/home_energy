import sqlite3
import unittest
from export_energy import FIELDS, PREFIX, snapshot


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript('CREATE TABLE states_meta(metadata_id INTEGER,entity_id TEXT);'
                             'CREATE TABLE state_attributes(attributes_id INTEGER,shared_attrs TEXT);'
                             'CREATE TABLE states(metadata_id INTEGER,state TEXT,last_updated_ts REAL,attributes_id INTEGER);')
        for index, (key, (suffix, unit)) in enumerate(FIELDS.items(), 1):
            self.db.execute('INSERT INTO states_meta VALUES(?,?)', (index, PREFIX + suffix))
            self.db.execute('INSERT INTO state_attributes VALUES(?,?)', (index, '{"unit_of_measurement":"' + unit + '"}'))
            value = '84' if key == 'soc' else '-500' if key == 'battery' else '0'
            self.db.execute('INSERT INTO states VALUES(?,?,?,?)', (index, value, 1, index))
        self.db.execute('INSERT INTO states_meta VALUES(101,?)', (PREFIX + 'inverter_time',))
        self.db.execute("INSERT INTO states VALUES(101,'17:00:00',990,NULL)")
        self.db.execute('INSERT INTO states_meta VALUES(102,?)', (PREFIX + 'grid_to_battery_power',))
        self.db.execute('INSERT INTO states VALUES(102,?,?,1)', ('181', 990))

    def test_grid_includes_house_and_battery(self):
        self.db.execute("UPDATE states SET state='678' WHERE metadata_id=1")
        self.assertEqual(snapshot(self.db, 1000)['values']['grid'], 859)

    def test_missing_grid_component_is_not_zero(self):
        self.db.execute('DELETE FROM states WHERE metadata_id=102')
        self.assertIsNone(snapshot(self.db, 1000)['values']['grid'])

    def test_constant_values_are_fresh_when_heartbeat_is_fresh(self):
        result = snapshot(self.db, 1000)
        self.assertEqual(result['status'], 'live')
        self.assertEqual(result['values']['battery'], -500)
        self.assertEqual(result['values']['soc'], 84)
        self.assertEqual(set(result['values']), set(FIELDS))

    def test_stale_heartbeat_is_not_live(self):
        self.assertEqual(snapshot(self.db, 1200)['status'], 'stale')

    def test_invalid_value_is_null(self):
        self.db.execute("UPDATE states SET state='unavailable' WHERE metadata_id=4")
        result = snapshot(self.db, 1000)
        self.assertEqual(result['status'], 'partial')
        self.assertIsNone(result['values']['soc'])

    def test_invalid_unit_is_rejected(self):
        self.db.execute('UPDATE state_attributes SET shared_attrs=? WHERE attributes_id=1', ('{"unit_of_measurement":"V"}',))
        self.assertIsNone(snapshot(self.db, 1000)['values']['grid'])


if __name__ == '__main__':
    unittest.main()
