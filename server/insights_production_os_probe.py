"""Fixed synthetic canaries for the production profile, without a model turn.

The existing audited sandbox process owner/parser are reused unchanged. No real
key, history, report, account or credential contents are probed or written.
"""
import socket
import sys

if __name__ == '__main__':
    sys.exit('Use the production launcher no-model check.')

import insights_staging_sandbox_probe as sandbox


def require(condition, code):
    if not condition:
        raise ValueError(code)


def production_paths(runtime):
    workspace = runtime.REPO / 'deploy/windows-report-production-candidate'
    root_canary = runtime.REPO / 'deploy/windows-report-production-root-canary.txt'
    return workspace, root_canary, workspace / 'unexpected-synthetic-write.txt'


def command(runtime, config, port):
    workspace, root_canary, target = production_paths(runtime)
    profile = 'home-energy-report-production-preflight'
    require(config.get('default_permissions') == profile and 'sandbox_mode' not in config
            and config['features'].get('shell_tool') is False
            and config['features'].get('unified_exec') is False
            and config['permissions'][profile]['network'] == {'enabled': False},
            'production_probe_policy')
    script = sandbox.probe_script(port)
    substitutions = {
        str(runtime.WORKSPACE): str(workspace),
        str(sandbox.ROOT_CANARY): str(root_canary),
        'HOME_ENERGY_STAGING_ALLOWED_CANARY': 'HOME_ENERGY_PRODUCTION_ALLOWED_CANARY',
    }
    for source, replacement in substitutions.items():
        require(source in script, 'production_canary_template')
        script = script.replace(source, replacement)
    require(str(target) in script and str(runtime.MATERIAL) not in script, 'production_canary_template')
    flags = [part for key, value in config.items()
             for part in ('-c', key + '=' + runtime.toml_value(value))]
    return [str(runtime.CODEX), '--no-daemon', '--cd', str(workspace), *flags,
            'sandbox', '--include-managed-config', '--permission-profile', profile, '--',
            str(sandbox.POWERSHELL), '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script]


def run_probe(runtime, config):
    workspace, root_canary, target = production_paths(runtime)
    require(not target.exists(), 'production_unexpected_canary_present')
    for path, marker in ((workspace / 'allowed.txt', 'ALLOWED'),
                         (workspace / 'denied.txt', 'DENIED'), (root_canary, 'ROOT_DENIED')):
        runtime.plain_file(path)
        require(path.read_text(encoding='ascii').strip() == 'HOME_ENERGY_PRODUCTION_' + marker + '_CANARY',
                'production_canary_changed')
    listener = socket.socket()
    listener.settimeout(2)
    try:
        listener.bind(('127.0.0.1', 0))
        listener.listen(4)
        before = sandbox.positive_loopback(listener)
        require(before, 'production_probe_positive_control')
        result = sandbox.parse_result(sandbox.bounded_probe(command(runtime, config, listener.getsockname()[1])))
        after = sandbox.positive_loopback(listener)
    finally:
        listener.close()
    require(after, 'production_probe_positive_control')
    require(result == {'allowed_read': True, 'denied_read': 'access_denied',
                       'root_read': 'access_denied', 'write': 'access_denied', 'network': 'access_denied'}
            and not target.exists(), 'production_os_denial_not_verified')
    return {'os_profile_probe_passed': True, 'scope': 'synthetic_canaries_only',
            'evidence_boundary': 'separate_os_profile_probe', 'filesystem_and_network': result,
            'loopback_positive_before': before, 'loopback_positive_after': after,
            'unexpected_write_absent': True, 'model_started': False, 'ssh_connected': False,
            'report_calls_enabled': False, 'schedule_changed': False}
