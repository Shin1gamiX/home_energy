"""Cached, bounded health checks for the Overview payload; no history writes."""
from concurrent.futures import ThreadPoolExecutor
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time

INTERVAL = 30
MAX_AGE = 90
FAILURES_REQUIRED = 3
CHECKS = ('vpn', 'dongle', 'collector')


def fresh(timestamp, now, seconds):
    return (isinstance(timestamp, (float, int)) and not isinstance(timestamp, bool)
            and math.isfinite(timestamp) and -30 <= now - timestamp <= seconds)


class HealthMonitor:
    """One background probe, shared by every visitor. No probe on HTTP requests.

    Explicit opt-in keeps unchanged deployments compatible. Three consecutive
    failed observations are needed to report unreachable; probe/tool errors are
    unknown. Timeouts never block the five-second recording loop.
    """
    def __init__(self, probe=None, monotonic=time.monotonic, clock=time.time):
        self.probe = probe
        self.monotonic = monotonic
        self.clock = clock
        self.executor = None
        self.future = None
        self.next_probe = 0
        self.checked_at = None
        self.states = dict.fromkeys(CHECKS, 'unknown')
        self.failures = dict.fromkeys(CHECKS, 0)

    @classmethod
    def from_environment(cls):
        container = os.environ.get('ENERGY_HEALTH_CONTAINER', '')
        router = os.environ.get('ENERGY_HEALTH_ROUTER', '')
        interface = os.environ.get('ENERGY_HEALTH_INTERFACE', 'wg0')
        port = os.environ.get('ENERGY_HEALTH_LISTENER_PORT', '8899')
        if not container and not router:
            return cls()
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,62}', container):
            raise ValueError('Invalid health container name')
        router = str(ipaddress.IPv4Address(router))
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', interface) or not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError('Invalid health network configuration')
        script = Path(__file__).with_name('connection_probe.py').read_text(encoding='utf-8')

        def probe():
            result = subprocess.run(
                ['/usr/bin/docker', 'exec', '-i', container, 'python3', '-I', '-B', '-',
                 '--router', router, '--interface', interface, '--port', port],
                input=script, capture_output=True, text=True, timeout=8, check=True)
            if len(result.stdout) > 1024:
                raise ValueError('Oversized health response')
            return json.loads(result.stdout)
        return cls(probe)

    def accept(self, observation, checked_at):
        if not fresh(self.checked_at, checked_at, MAX_AGE):
            self.failures = dict.fromkeys(CHECKS, 0)
        if (not isinstance(observation, dict) or set(observation) != set(CHECKS)
                or any(value not in ('up', 'down', 'unknown') for value in observation.values())):
            observation = dict.fromkeys(CHECKS, 'unknown')
        for key in CHECKS:
            state = observation[key]
            self.failures[key] = self.failures[key] + 1 if state == 'down' else 0
            self.states[key] = ('down' if self.failures[key] >= FAILURES_REQUIRED else 'checking') if state == 'down' else state
        self.checked_at = checked_at

    def collect(self):
        try:
            observation = self.probe()
        except (OSError, ValueError, subprocess.SubprocessError):
            observation = dict.fromkeys(CHECKS, 'unknown')
        return observation, self.clock()

    def snapshot(self, now, payload):
        if self.future is not None and self.future.done():
            try:
                observation, checked_at = self.future.result()
            except Exception:
                observation, checked_at = dict.fromkeys(CHECKS, 'unknown'), self.clock()
            self.accept(observation, checked_at)
            self.future = None
        if self.probe and self.future is None and self.monotonic() >= self.next_probe:
            if self.executor is None:
                self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='connection-health')
            self.future = self.executor.submit(self.collect)
            self.next_probe = self.monotonic() + INTERVAL
        recent = fresh(self.checked_at, now, MAX_AGE)
        checks = {key: {'state': self.states[key] if recent else 'unknown',
                        'checked_at': self.checked_at if recent else None} for key in CHECKS}
        telemetry = fresh(payload.get('updated_at'), now, 90) and payload.get('status') in ('live', 'partial')
        checks['inverter'] = {'state': 'up' if telemetry else 'unknown' if payload.get('status') == 'offline' else 'stale',
                              'checked_at': now}
        # A newly received reading outweighs a failed/lagging auxiliary probe.
        # Do not claim which transport works when observations contradict it.
        if telemetry:
            for key in CHECKS:
                if checks[key]['state'] in ('down', 'checking'):
                    checks[key]['state'] = 'unknown'
        return {'version': 1, 'checks': checks}

    def close(self):
        if self.executor:
            self.executor.shutdown(wait=True, cancel_futures=True)
