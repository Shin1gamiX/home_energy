"""Observe fixed functions.exec MCP calls using a loopback synthetic responder.

No external model, SSH, live data, approval receipt or public reporting. Reuses
the reviewed simulator unchanged; both report calls always encounter a closed
fixture. Only metadata names/types and fixed rejection codes are retained.
"""
import importlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import insights_staging_launcher as launcher


ENTRY = launcher.SOURCE / 'insights_staging_metadata_probe.py'
HELPER_PINS = {
    'insights_staging_rejection_probe': '5f85f5f89709d07499217c0f4724e9d8e308b8fada4f654fe3399cd21db3ee88',
    'insights_staging_runtime_probe': '2a3f3e420a5db89b4dcbf4257b2a1567d570a23363d0fc042ec717a431644c77',
}
JSON_TYPES = frozenset(('null', 'boolean', 'integer', 'number', 'string', 'object', 'array'))


def require(condition, code):
    launcher.require(condition, code)


def load_helpers():
    # Check both files before importing either; these are diagnostic-only helpers.
    for name, pin in HELPER_PINS.items():
        launcher.check_pin(launcher.SOURCE / (name + '.py'), pin)
    modules = []
    for name in HELPER_PINS:
        module = importlib.import_module(name)
        require(Path(module.__file__).resolve() == launcher.SOURCE / (name + '.py'),
                'metadata_probe_import_origin')
        modules.append(module)
    return tuple(modules)


def fixed_calls():
    """Exact literals from the pinned runtime simulator; never caller supplied."""
    return [('prepare', {}), ('publish', {
        'analysis_id': 'a' * 64, 'summary': {
            'en': 'Synthetic blocked-call check only.',
            'el': 'Μόνο συνθετικός έλεγχος αποκλεισμένης κλήσης.',
            'ru': 'Только синтетическая проверка блокировки вызова.'}, 'notes': []})]


def verify_plan(runtime):
    expected = [runtime.custom('he_blocked_' + name,
        'text(await tools.mcp__home_energy_staging__' + name
        + '(' + runtime.encoded(arguments).decode() + '));')
        for name, arguments in fixed_calls()]
    require(runtime.plans()[-2:] == expected, 'metadata_probe_plan_changed')


def fixture_config(capture, config, directory):
    result = capture.fixture_config(config, directory)
    result['mcp_servers'][launcher.SERVER]['args'] = ['-I', '-B', str(ENTRY), '--mcp-fixture']
    return result


def serve_fixture(capture, adapter, source, destination, path):
    class RuntimeMetadataSource(capture.MetadataSource):
        def expected_calls(self):
            return fixed_calls()

    capture.serve_file_fixture(adapter, source, destination, path,
                               source_type=RuntimeMetadataSource)


def read_shapes(capture, path):
    shapes = []
    for name, _ in fixed_calls():
        target = path.with_name(capture.METADATA_NAME.format(tool=name))
        launcher.plain_file(target)
        with target.open('rb') as stream:
            body = stream.read(capture.MAX_EVIDENCE_BYTES + 1)
        require(len(body) <= capture.MAX_EVIDENCE_BYTES, 'metadata_probe_evidence_limit')
        shape = json.loads(body)
        require(type(shape) is dict and set(shape) == {
            'tool', 'metadata_present', 'metadata_type', 'fields'}, 'metadata_probe_shape_invalid')
        require(shape['tool'] == name and type(shape['metadata_present']) is bool
                and shape['metadata_type'] in JSON_TYPES
                and type(shape['fields']) is list and len(shape['fields']) <= 16,
                'metadata_probe_shape_invalid')
        require(not shape['fields'] or shape['metadata_type'] == 'object', 'metadata_probe_shape_invalid')
        for field in shape['fields']:
            require(type(field) is dict and set(field) == {'name', 'type'}
                    and type(field['name']) is str
                    and (field['name'] == '[redacted-key]' or re.fullmatch(
                        r'[A-Za-z][A-Za-z_./:-]{0,95}', field['name']))
                    and field['type'] in JSON_TYPES, 'metadata_probe_shape_invalid')
        shapes.append(shape)
    return shapes


def run_probe():
    capture, runtime = load_helpers()
    config, adapter = launcher.load_inputs()
    launcher.plain_file(ENTRY)
    verify_plan(runtime)
    # A new diagnostic directory, never an old one-shot receipt or evidence path.
    directory = tempfile.mkdtemp(prefix=capture.PREFIX)
    path = capture.evidence_path(directory)
    result = runtime.run_probe(candidate_config=fixture_config(capture, config, directory))
    require(result.get('local_synthetic_probe_passed') is True
            and result.get('external_model_called') is False
            and result.get('report_transport_enabled') is False
            and result.get('matched_blocked_report_calls') == 2
            and result.get('only_parent_thread_loaded') is True, 'metadata_probe_runtime_incomplete')
    events = capture.read_evidence(path, adapter.REJECTION_CODES)
    shapes = read_shapes(capture, path)
    launcher.check_pin(launcher.USER_CONFIG, launcher.FILE_PINS[launcher.USER_CONFIG])
    codes = [event['code'] for event in events]
    return {
        'local_metadata_probe_completed': True, 'client_path': 'functions_exec_simulated_response',
        'metadata_shapes': shapes, 'rejection_codes': codes,
        'closed_gate_reached': all(code == capture.GATE_CODE for code in codes),
        'metadata_shape_rejection_reproduced': all(code == 'protocol_metadata_shape' for code in codes),
        'external_model_called': False, 'original_request_captured': False,
        'ssh_connected': False, 'report_transport_enabled': False,
        'public_publishing_enabled': False, 'schedule_changed': False,
        'synthetic_evidence_directory': directory,
    }


def main():
    require(sys.flags.isolated == 1 and sys.dont_write_bytecode,
            'metadata_probe_isolated_invocation_required')
    require(Path(__file__).resolve() == ENTRY, 'metadata_probe_installation_invalid')
    require(sys.argv[1:] in ([], ['--mcp-fixture']), 'metadata_probe_arguments_refused')
    if sys.argv[1:] == ['--mcp-fixture']:
        capture, runtime = load_helpers()
        verify_plan(runtime)
        adapter = launcher.load_adapter()
        path = capture.evidence_path(os.environ.pop(capture.DIRECTORY_ENV, None))
        serve_fixture(capture, adapter, sys.stdin.buffer, sys.stdout.buffer, path)
    else:
        print(json.dumps(run_probe(), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Never reveal raw metadata, requests, exception text or internal paths.
        print('{"error":"Local metadata-path diagnostic failed; no reporting enabled"}', file=sys.stderr)
        sys.exit(1)
