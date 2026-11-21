"""Exercise first-time template activation with an in-memory Recorder and mocked services."""
from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch


helper = Path(__file__).resolve().parents[1] / 'deploy/activate-flow-observations.py'
spec = importlib.util.spec_from_file_location('flow_release', helper)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class FlowReleaseTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript('CREATE TABLE states(metadata_id,state,last_updated_ts,attributes_id);'
                              'CREATE TABLE state_attributes(attributes_id,shared_attrs);'
                              'CREATE TABLE states_meta(metadata_id,entity_id);')
        self.db.execute('INSERT INTO states_meta VALUES (200,?)', (release.ENTITY,))
        self.db.execute('INSERT INTO state_attributes VALUES (1,?)', (json.dumps({'unit_of_measurement': 'W'}),))
        self.reports = {}
        for i, entity in enumerate(release.SENSORS, 1):
            self.db.execute('INSERT INTO states_meta VALUES (?,?)', (i, entity))
            self.db.execute('INSERT INTO states VALUES (?,?,10,1)', (i, str(i)))
            self.reports[entity] = [str(i), 10, 30]

    def add_reports(self, stamp, reports):
        self.db.execute('INSERT INTO state_attributes VALUES (?,?)', (stamp, json.dumps({'reports': reports})))
        self.db.execute("INSERT INTO states VALUES (200,'1',?,?)", (stamp, stamp))

    def test_witness_must_be_post_install_and_advance_for_every_sensor(self):
        self.add_reports(31, self.reports)
        self.assertFalse(release.checked_reports(self.db, 20, 40))
        self.add_reports(61, self.reports)
        self.assertFalse(release.checked_reports(self.db, 20, 62))
        for entry in self.reports.values():
            entry[2] = 75
        self.add_reports(76, self.reports)
        self.assertEqual(release.checked_reports(self.db, 20, 80), dict.fromkeys(release.SENSORS, 75))
        self.assertFalse(release.checked_reports(self.db, 70, 80))
        self.assertFalse(release.checked_reports(self.db, 20, 190))

    def test_six_sensors_different_revision_or_stale_one_cannot_pass(self):
        self.add_reports(31, self.reports)
        for entry in self.reports.values():
            entry[2] = 60
        key = release.SENSORS[0]
        for invalid in (None, ['999', 10, 60], [self.reports[key][0], 20, 60], [self.reports[key][0], 10, 30]):
            with self.subTest(observation=invalid):
                revised = dict(self.reports)
                if invalid is None:
                    del revised[key]
                else:
                    revised[key] = invalid
                self.add_reports(61, revised)
                self.assertFalse(release.checked_reports(self.db, 20, 64))
                self.db.execute('DELETE FROM states WHERE attributes_id=61')
                self.db.execute('DELETE FROM state_attributes WHERE attributes_id=61')

    def test_high_rate_source_can_change_after_it_was_sampled(self):
        self.add_reports(31, self.reports)
        for entry in self.reports.values():
            entry[2] = 59
        self.add_reports(60, self.reports)
        self.db.execute("INSERT INTO states VALUES (1,'101',61,1)")
        self.assertEqual(len(release.checked_reports(self.db, 20, 73)), 7)

    def test_config_appends_once_and_preserves_every_existing_byte(self):
        old = b'default_config:\nscene: !include scenes.yaml'
        self.assertEqual(release.configuration(old), old + b'\n\ntemplate: !include flow_observations.yaml\n')
        for occupied in (b'template:\n', b'template more:\n', release.configuration(old)):
            with self.assertRaisesRegex(RuntimeError, 'Existing template'):
                release.configuration(occupied)

    def test_failed_native_validation_rolls_back_without_restarting_ha_or_exporter(self):
        self.check_activation(validate_error=True, expect_ha_restart=False)

    def test_opt_in_restart_failure_restores_original_running_configuration(self):
        self.check_activation(validate_error=False, expect_ha_restart=True)

    def check_activation(self, *, validate_error, expect_ha_restart):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            directory = Path(tmp)
            manifest = {'configuration_before': 'config', 'exporter_before': 'code'}
            record = {'before': {'configuration.yaml': {'sha256': 'config'}, 'export_energy.py': {'sha256': 'code'}}}
            candidates = {'configuration.yaml': b'new', 'flow_observations.yaml': b'helper'}
            stack.enter_context(patch.object(release, 'preflight', return_value=(candidates, 'id')))
            stack.enter_context(patch.object(release, 'backup', return_value=(directory, record)))
            writes = stack.enter_context(patch.object(release, 'replace_checked'))
            stack.enter_context(patch.object(release, 'validate_configuration',
                                             side_effect=RuntimeError('config') if validate_error else None))
            restart = stack.enter_context(patch.object(release, 'restart_ha', side_effect=RuntimeError('startup')))
            restore = stack.enter_context(patch.object(release, 'restore'))
            finish = stack.enter_context(patch.object(release, 'finish'))
            with self.assertRaises(RuntimeError):
                release.activate(manifest, {}, True)
            self.assertEqual([call.args[0] for call in writes.call_args_list], [release.OBSERVATIONS, release.CONFIG])
            self.assertEqual(restart.called, expect_ha_restart)
            restore.assert_called_once_with(directory, record, expect_ha_restart)
            finish.assert_not_called()

    def test_container_id_and_config_mount_must_match(self):
        cid = 'a' * 64
        for line, expected in ((f'{cid} true {release.CONFIG_DIR}', 'b' * 64),
                               (f'{cid} false {release.CONFIG_DIR}', cid), (f'{cid} true /tmp/config', cid)):
            with self.subTest(line=line), patch.object(subprocess, 'check_output', return_value=line):
                with self.assertRaises(RuntimeError):
                    release.container(expected)


if __name__ == '__main__':
    unittest.main()
