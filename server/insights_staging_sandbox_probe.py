"""Separate OS-profile canary probe; not a model turn or credential-access test.

Uses only existing synthetic canaries and one absent CreateNew target. An
unexpected write is preserved for administrator inspection, never deleted here.
No model tool is enabled and no reporting or SSH operation is called.
"""
import argparse
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import time

# The isolated interpreter excludes the script directory and ambient PYTHONPATH.
# Import only from this fixed administrator-reviewed source location.
SOURCE = Path(r'C:\Users\anest\Documents\New project\home-energy-repo\server')
if Path(__file__).resolve().parent != SOURCE:
    raise RuntimeError('Synthetic probe installation mismatch')
sys.path.insert(0, str(SOURCE))
import insights_staging_launcher as launcher


POWERSHELL = Path(r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe')
TARGET = launcher.WORKSPACE / 'unexpected-synthetic-write.txt'
ROOT_CANARY = launcher.REPO / 'deploy/windows-report-staging-root-canary.txt'
MAX_STDOUT = 8192
MAX_STDERR = 8192
PROCESS_SECONDS = 45
FILE_OUTCOMES = frozenset(('access_denied', 'unexpectedly_allowed', 'inconclusive'))
NETWORK_OUTCOMES = FILE_OUTCOMES | {'timeout', 'not_tested'}


def require(condition, code):
    launcher.require(condition, code)


def ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def probe_script(port=None):
    """Fixed code and fixed canary paths; only a validated local port varies."""
    require(port is None or type(port) is int and 1 <= port <= 65535, 'invalid_loopback_port')
    values = {
        '__ALLOWED__': ps_literal(launcher.WORKSPACE / 'allowed.txt'),
        '__DENIED__': ps_literal(launcher.WORKSPACE / 'denied.txt'),
        '__ROOT_DENIED__': ps_literal(ROOT_CANARY),
        '__TARGET__': ps_literal(TARGET), '__PORT__': str(port or 0),
    }
    script = r"""
$ErrorActionPreference = 'Stop'
function Is-AccessDenied($Exception) {
    for ($depth = 0; $null -ne $Exception -and $depth -lt 5; $depth++) {
        if ($Exception -is [System.UnauthorizedAccessException]) { return $true }
        $Exception = $Exception.InnerException
    }
    return $false
}
function Network-Outcome($Exception) {
    for ($depth = 0; $null -ne $Exception -and $depth -lt 5; $depth++) {
        if ($Exception -is [System.Net.Sockets.SocketException] -and
            $Exception.SocketErrorCode -eq [System.Net.Sockets.SocketError]::AccessDenied -and
            $Exception.NativeErrorCode -eq 10013) { return 'access_denied' }
        $Exception = $Exception.InnerException
    }
    return 'inconclusive'
}
$result = [ordered]@{ allowed_read = $false; denied_read = 'inconclusive'; root_read = 'inconclusive';
                       write = 'inconclusive'; network = 'not_tested' }
try {
    $result.allowed_read = [IO.File]::ReadAllText(__ALLOWED__).Trim() -eq 'HOME_ENERGY_STAGING_ALLOWED_CANARY'
} catch { $result.allowed_read = $false }
try {
    $null = [IO.File]::ReadAllText(__DENIED__)
    $result.denied_read = 'unexpectedly_allowed'
} catch {
    if (Is-AccessDenied $_.Exception) { $result.denied_read = 'access_denied' }
}
try {
    $null = [IO.File]::ReadAllText(__ROOT_DENIED__)
    $result.root_read = 'unexpectedly_allowed'
} catch {
    if (Is-AccessDenied $_.Exception) { $result.root_read = 'access_denied' }
}
$handle = $null
try {
    $handle = [IO.File]::Open(__TARGET__, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    $result.write = 'unexpectedly_allowed'
} catch {
    if (Is-AccessDenied $_.Exception) { $result.write = 'access_denied' }
} finally { if ($null -ne $handle) { $handle.Dispose() } }
$port = __PORT__
if ($port -gt 0) {
    $client = [Net.Sockets.TcpClient]::new()
    try {
        $attempt = $client.ConnectAsync('127.0.0.1', $port)
        if (-not $attempt.Wait(1500)) { $result.network = 'timeout' }
        elseif ($client.Connected) { $result.network = 'unexpectedly_allowed' }
        else { $result.network = 'inconclusive' }
    } catch { $result.network = Network-Outcome $_.Exception }
    finally { $client.Dispose() }
}
$result | ConvertTo-Json -Compress
"""
    for token, value in values.items():
        script = script.replace(token, value)
    return script


def command(config, port=None):
    profile = config['permissions'][launcher.PROFILE]
    require(config.get('default_permissions') == launcher.PROFILE and 'sandbox_mode' not in config,
            'incorrect_permission_selection')
    require(config['features'].get('shell_tool') is False and
            config['features'].get('unified_exec') is False and
            profile['network'].get('enabled') is False, 'model_capabilities_changed')
    flags = [part for key, value in config.items()
             for part in ('-c', key + '=' + launcher.toml_value(value))]
    return [str(launcher.CODEX), '--no-daemon', *flags, 'sandbox', '--include-managed-config',
            '--permission-profile', launcher.PROFILE, '--', str(POWERSHELL), '-NoLogo',
            '-NoProfile', '-NonInteractive', '-Command', probe_script(port)]


def bounded_probe(argv):
    """Own the sandbox process tree; bounded pipes and one probe deadline."""
    tree = launcher.OwnedRuntimeTree()
    process = None
    workers = []
    results = queue.Queue(maxsize=2)
    deadline = time.monotonic() + PROCESS_SECONDS

    def read(pipe, name, limit):
        count = 0
        output = bytearray()
        ok = True
        try:
            while chunk := pipe.read(4096):
                count += len(chunk)
                if count > limit:
                    ok = False
                    tree.close()
                    break
                if name == 'stdout':
                    output.extend(chunk)
        except (OSError, ValueError):
            ok = False
            tree.close()
        finally:
            results.put_nowait((name, ok, bytes(output)))

    try:
        process = subprocess.Popen(argv, cwd=launcher.WORKSPACE,
            env=launcher.clean_environment(os.environ), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0, close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW | launcher.CREATE_SUSPENDED)
        tree.assign_and_resume(process)
        for name, pipe, limit in (('stdout', process.stdout, MAX_STDOUT),
                                  ('stderr', process.stderr, MAX_STDERR)):
            worker = threading.Thread(target=read, args=(pipe, name, limit), daemon=True)
            workers.append(worker)
            worker.start()
        captured = {}
        for _ in workers:
            remaining = deadline - time.monotonic()
            require(remaining > 0, 'probe_timeout')
            try:
                name, ok, body = results.get(timeout=remaining)
            except queue.Empty:
                raise launcher.GuardFailure('probe_timeout') from None
            require(ok, 'probe_output_failed')
            captured[name] = body
        remaining = deadline - time.monotonic()
        require(remaining > 0, 'probe_timeout')
        require(process.wait(timeout=remaining) == 0, 'probe_process_failed')
        return captured['stdout']
    finally:
        cleanup_failed = False
        try:
            tree.close()
        except (OSError, ValueError):
            cleanup_failed = True
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                cleanup_failed = True
            for worker in workers:
                if worker.ident is not None:
                    worker.join(timeout=2)
            for pipe in (process.stdout, process.stderr):
                try:
                    pipe.close()
                except OSError:
                    cleanup_failed = True
            for worker in workers:
                if worker.ident is not None:
                    worker.join(timeout=1)
                    cleanup_failed |= worker.is_alive()
        require(not cleanup_failed, 'probe_cleanup_failed')


def parse_result(raw):
    require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_STDOUT, 'invalid_probe_result')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, 'invalid_probe_result')
            value[key] = item
        return value
    result = json.loads(raw, object_pairs_hook=unique,
                        parse_constant=lambda _: require(False, 'invalid_probe_result'))
    require(isinstance(result, dict) and set(result) == {'allowed_read', 'denied_read', 'root_read', 'write', 'network'},
            'invalid_probe_result')
    require(type(result['allowed_read']) is bool, 'invalid_probe_result')
    require(isinstance(result['denied_read'], str) and result['denied_read'] in FILE_OUTCOMES and
            isinstance(result['root_read'], str) and result['root_read'] in FILE_OUTCOMES and
            isinstance(result['write'], str) and result['write'] in FILE_OUTCOMES and
            isinstance(result['network'], str) and result['network'] in NETWORK_OUTCOMES,
            'invalid_probe_result')
    return result


def positive_loopback(listener):
    try:
        with socket.create_connection(('127.0.0.1', listener.getsockname()[1]), timeout=2):
            connection, _ = listener.accept()
            connection.close()
        return True
    except OSError:
        return False


def run_probe(config, *, with_loopback=False):
    require(not TARGET.exists(), 'unexpected_synthetic_file_present')
    canaries = {
        launcher.WORKSPACE / 'allowed.txt': 'HOME_ENERGY_STAGING_ALLOWED_CANARY',
        launcher.WORKSPACE / 'denied.txt': 'HOME_ENERGY_STAGING_DENIED_CANARY',
        ROOT_CANARY: 'HOME_ENERGY_STAGING_ROOT_DENIED_CANARY',
    }
    for path, expected in canaries.items():
        launcher.plain_file(path)
        require(path.read_text(encoding='ascii').strip() == expected, 'canary_changed')
    launcher.plain_file(POWERSHELL)
    listener, before, after = None, None, None
    try:
        if with_loopback:
            try:
                listener = socket.socket()
                listener.settimeout(2)
                listener.bind(('127.0.0.1', 0))
                listener.listen(4)
                before = positive_loopback(listener)
            except OSError:
                before = False
        port = listener.getsockname()[1] if listener is not None and before else None
        result = parse_result(bounded_probe(command(config, port)))
        if with_loopback:
            after = positive_loopback(listener) if listener is not None and before else False
    finally:
        if listener is not None:
            listener.close()
        absent = not TARGET.exists()
    file_passed = result['allowed_read'] and result['denied_read'] == 'access_denied' and \
        result['root_read'] == 'access_denied' and \
        result['write'] == 'access_denied' and absent
    conclusion = 'not_tested'
    if with_loopback:
        conclusion = result['network'] if before and after and result['network'] in (
            'access_denied', 'unexpectedly_allowed') else 'inconclusive'
    require(with_loopback or result['network'] == 'not_tested', 'invalid_probe_result')
    return {'scope': 'local_synthetic_only', 'profile': launcher.PROFILE,
            'evidence_boundary': 'separate_os_profile_probe',
            'filesystem_probe_passed': bool(file_passed),
            'filesystem': {'allowed_read': result['allowed_read'], 'denied_read': result['denied_read'],
                           'root_read': result['root_read'],
                           'write': result['write'], 'unexpected_write_absent': absent},
            'network': {'requested': with_loopback, 'host_before': before, 'host_after': after,
                        'sandbox_result': result['network'], 'conclusion': conclusion},
            'model_called': False, 'ssh_called': False, 'report_calls': 0,
            'model_shell_tools_enabled': False, 'schedule_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-loopback', action='store_true', help='Also measure a temporary local listener')
    args = parser.parse_args()
    try:
        require(sys.flags.isolated == 1 and sys.dont_write_bytecode, 'isolated_interpreter_required')
        config, _ = launcher.load_inputs()
        result = run_probe(config, with_loopback=args.with_loopback)
        print(json.dumps(result, sort_keys=True))
        return 0 if result['filesystem_probe_passed'] and result['network']['conclusion'] != 'unexpectedly_allowed' else 1
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        print(json.dumps({'error': 'Synthetic OS-profile probe failed; no denial was certified',
                          'unexpected_write_absent': not TARGET.exists(),
                          'model_called': False, 'ssh_called': False, 'report_calls': 0}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
