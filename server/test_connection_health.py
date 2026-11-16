import json
import os
import subprocess
import threading
import tempfile
from pathlib import Path
import sqlite3
from contextlib import closing
import unittest
from unittest.mock import patch

from connection_health import HealthMonitor, fresh
from connection_probe import observe, tcp_observation

NOW = 1800000000


class HealthTests(unittest.TestCase):
    def test_three_failures_then_immediate_recovery(self):
        monitor = HealthMonitor()
        for count in range(3):
            monitor.accept(dict(vpn='down', dongle='unknown', collector='unknown'), NOW)
            self.assertEqual(monitor.states['vpn'], 'down' if count == 2 else 'checking')
        monitor.accept(dict.fromkeys(('vpn', 'dongle', 'collector'), 'up'), NOW)
        self.assertEqual(monitor.states['vpn'], 'up')

    def test_probe_errors_are_unknown_and_reset_failure_streak(self):
        monitor = HealthMonitor()
        for _ in range(2):
            monitor.accept(dict.fromkeys(('vpn', 'dongle', 'collector'), 'down'), NOW)
        monitor.accept({'unexpected': 'secret must not escape'}, NOW)
        monitor.accept(dict.fromkeys(('vpn', 'dongle', 'collector'), 'down'), NOW)
        self.assertEqual(monitor.states['vpn'], 'checking')

    def test_expired_health_and_future_times_are_unknown(self):
        monitor = HealthMonitor()
        monitor.accept(dict.fromkeys(('vpn', 'dongle', 'collector'), 'up'), NOW - 91)
        result = monitor.snapshot(NOW, {'status': 'stale', 'updated_at': NOW - 200})
        self.assertEqual(result['checks']['vpn']['state'], 'unknown')
        for value in (None, True, float('nan'), NOW + 31):
            self.assertFalse(fresh(value, NOW, 90))

    def test_long_gap_resets_consecutive_failures(self):
        monitor = HealthMonitor()
        for _ in range(2):
            monitor.accept(dict.fromkeys(('vpn', 'dongle', 'collector'), 'down'), NOW)
        monitor.accept(dict.fromkeys(('vpn', 'dongle', 'collector'), 'down'), NOW + 91)
        self.assertEqual(monitor.states['vpn'], 'checking')

    def test_fresh_readings_override_lagging_failure_probes(self):
        monitor = HealthMonitor()
        for _ in range(3):
            monitor.accept(dict.fromkeys(('vpn', 'dongle', 'collector'), 'down'), NOW)
        result = monitor.snapshot(NOW, {'status': 'live', 'updated_at': NOW - 2})
        self.assertEqual(result['checks']['inverter']['state'], 'up')
        self.assertEqual(result['checks']['vpn']['state'], 'unknown')

    def test_one_probe_in_background_and_cached_between_visitors(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def probe():
            calls.append(1)
            entered.set()
            release.wait(2)
            return dict.fromkeys(('vpn', 'dongle', 'collector'), 'up')
        monitor = HealthMonitor(probe, monotonic=lambda: 100, clock=lambda: NOW)
        self.addCleanup(monitor.close)
        try:
            for _ in range(10):
                monitor.snapshot(NOW, {})
            self.assertTrue(entered.wait(1))
            self.assertEqual(len(calls), 1)
        finally:
            release.set()
        monitor.future.result(timeout=2)
        self.assertEqual(monitor.snapshot(NOW, {})['checks']['vpn']['state'], 'up')
        self.assertEqual(len(calls), 1)

    def test_disabled_by_default_and_no_private_fields(self):
        with patch.dict(os.environ, {}, clear=True):
            monitor = HealthMonitor.from_environment()
        result = monitor.snapshot(NOW, {})
        self.assertIsNone(monitor.executor)
        self.assertEqual(set(result), {'version', 'checks'})
        for value in result['checks'].values():
            self.assertEqual(set(value), {'state', 'checked_at'})

    def test_configuration_is_not_shell_input(self):
        with patch.dict(os.environ, {'ENERGY_HEALTH_CONTAINER': 'ha;reboot', 'ENERGY_HEALTH_ROUTER': '10.8.0.5'}, clear=True):
            with self.assertRaises(ValueError):
                HealthMonitor.from_environment()
        with patch.dict(os.environ, {'ENERGY_HEALTH_CONTAINER': 'homeassistant', 'ENERGY_HEALTH_ROUTER': '10.8.0.5'}, clear=True):
            monitor = HealthMonitor.from_environment()
            with patch('connection_health.subprocess.run') as run:
                run.return_value.stdout = json.dumps(dict.fromkeys(('vpn', 'dongle', 'collector'), 'up'))
                monitor.probe()
                args, kwargs = run.call_args
                self.assertEqual(args[0][:5], ['/usr/bin/docker', 'exec', '-i', 'homeassistant', 'python3'])
                self.assertNotIn('shell', kwargs)
                self.assertLessEqual(kwargs['timeout'], 8)
                self.assertNotIn('set>server', kwargs['input'])

    def test_health_metadata_does_not_change_recording_or_exports(self):
        from history_store import record
        payload = dict(status='live', updated_at=NOW, generated_at=NOW,
                       values=dict(pv=1500, grid=0, battery=500, load=1000, soc=84, battery_voltage=53.2))
        health = HealthMonitor().snapshot(NOW, payload)
        results = []
        for extra in ({}, {'health': health}):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                record({**payload, **extra}, root)
                with closing(sqlite3.connect(root / 'history.sqlite3')) as db:
                    database = '\n'.join(db.iterdump())
                exports = {path.name: path.read_bytes() for path in (root / 'history').glob('*.json')}
                results.append((database, exports))
        self.assertEqual(results[0], results[1])


class ProbeTests(unittest.TestCase):
    TABLE = 'sl local_address rem_address st\n0: 00000000:22C3 00000000:0000 0A\n1: 0100080A:22C3 0500080A:BB00 01\n'

    def test_exact_peer_listener_and_established_state(self):
        self.assertEqual(tcp_observation(self.TABLE, '10.8.0.5', 8899), (True, True))
        self.assertEqual(tcp_observation(self.TABLE, '10.8.0.2', 8899), (True, False))
        self.assertEqual(tcp_observation(self.TABLE.replace(' 01', ' 06'), '10.8.0.5', 8899), (True, False))
        self.assertEqual(tcp_observation(self.TABLE, '10.8.0.5', 9999), (False, False))

    def test_probe_uses_vpn_route_and_readonly_commands(self):
        commands = []
        def run(args, **kwargs):
            commands.append(args)
            return subprocess.CompletedProcess(args, 0, '10.8.0.5 dev wg0 src 10.8.0.1', '')
        self.assertEqual(observe('10.8.0.5', 'wg0', 8899, run=run, read=lambda _: self.TABLE),
                         dict(vpn='up', dongle='up', collector='up'))
        self.assertEqual([args[0] for args in commands], ['ip', 'ping'])

    def test_wrong_namespace_does_not_probe_home_over_another_route(self):
        run = lambda args, **kwargs: subprocess.CompletedProcess(args, 0, '10.8.0.5 dev eth0', '')
        result = observe('10.8.0.5', 'wg0', 8899, run=run, read=lambda _: self.TABLE)
        self.assertEqual(result, dict(vpn='down', dongle='unknown', collector='unknown'))

    def test_missing_tools_are_not_reported_as_equipment_failure(self):
        def missing(*args, **kwargs):
            raise FileNotFoundError()
        self.assertEqual(observe('10.8.0.5', 'wg0', 8899, run=missing),
                         dict(vpn='unknown', dongle='unknown', collector='unknown'))


if __name__ == '__main__':
    unittest.main()
