"""Local charging release checks with synthetic Recorder data and mocked services."""
from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_flow_observations_deploy as fixtures


path = Path(__file__).resolve().parents[1] / 'deploy/activate-charging-status.py'
spec = importlib.util.spec_from_file_location('charging_release', path)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ChargingReleaseTests(unittest.TestCase):
    def test_nine_credible_sources_must_advance_before_switch(self):
        original = fixtures.FlowReleaseTests()
        original.setUp()
        self.addCleanup(original.doCleanups)
        db = original.db
        extra = {}
        for index, (entity, unit) in enumerate(release.CHARGING.items(), 101):
            value = '597' if unit is None else '0'
            extra[entity] = [value, 10, 30]
            db.execute('INSERT INTO states_meta VALUES (?,?)', (index, entity))
            db.execute('INSERT INTO state_attributes VALUES (?,?)',
                       (index, json.dumps({'unit_of_measurement': unit} if unit is not None else {})))
            db.execute('INSERT INTO states VALUES (?,?,10,?)', (index, value, index))

        def helper(stamp):
            db.execute('INSERT INTO state_attributes VALUES (?,?)',
                       (stamp, json.dumps(dict(reports=original.reports, charging_reports=extra))))
            db.execute("INSERT INTO states VALUES (200,'1',?,?)", (stamp, stamp))

        helper(31)
        for observation in original.reports.values():
            observation[2] = 60
        extra[next(iter(extra))][2] = 60
        helper(61)
        self.assertFalse(release.checked_reports(db, 20, 62))
        for observation in extra.values():
            observation[2] = 75
        helper(76)
        self.assertEqual(len(release.checked_reports(db, 20, 80)), 9)
        db.execute("UPDATE state_attributes SET shared_attrs=? WHERE attributes_id=101",
                   ('{"unit_of_measurement":"W"}',))
        self.assertFalse(release.checked_reports(db, 20, 80))

    def test_validation_accepts_both_flags_with_enough_pv_to_cover_battery(self):
        routing = dict(mains_active=True, solar_active=True, persistent=False, discrepancy_w=0)
        values = dict(grid=1500, bridge=-500, load=1000, battery=600, pv=900, grid_house=1000,
                      grid_battery=300, solar_battery=300)
        packet = dict(status='live', updated_at=100, charging_routing=routing, values=values)
        self.assertTrue(release.usable_routing(packet, 95, 106))
        routing['mains_active'] = False
        with self.assertRaisesRegex(RuntimeError, 'routes contradict'):
            release.usable_routing(packet, 95, 106)
        values.update(grid_battery=0, solar_battery=600)
        self.assertTrue(release.usable_routing(packet, 95, 106))
        routing['persistent'] = True
        with self.assertRaisesRegex(RuntimeError, 'Invalid charging discrepancy'):
            release.usable_routing(packet, 95, 106)

    def test_template_timeout_rolls_back_before_exporter_or_ui_switch(self):
        directory = Path('private-test-backup')
        originals = {name: {'sha256': 'old'} for name in release.FILES}
        bodies = dict.fromkeys(release.FILES, b'new')
        with ExitStack() as stack:
            stack.enter_context(patch.object(release, 'preflight', return_value=(bodies, originals, 'container')))
            stack.enter_context(patch.object(release, 'backup', return_value=(directory, {})))
            write = stack.enter_context(patch.object(release, 'write'))
            stack.enter_context(patch.object(release.HA, 'validate_configuration'))
            restart_ha = stack.enter_context(patch.object(release.HA, 'restart_ha'))
            stack.enter_context(patch.object(release, 'wait_for_reports', side_effect=RuntimeError('timeout')))
            restart_exporter = stack.enter_context(patch.object(release.INSTALL, 'restart_exporter'))
            restore = stack.enter_context(patch.object(release, 'restore'))
            with self.assertRaisesRegex(RuntimeError, 'timeout'):
                release.activate(True)
            write.assert_called_once_with(release.TEMPLATE, 'old', b'new', {'sha256': 'old'})
            restart_ha.assert_called_once_with('container')
            restart_exporter.assert_not_called()
            restore.assert_called_once_with(directory, True)


if __name__ == '__main__':
    unittest.main()
