"""Explicit October runtime revision; preserves the original grant and evidence.

The older staging/trial sources stay unchanged. Only reviewed installation
metadata is overridden in a new process before their existing guards run.
There is no auto-discovery, download, permission fallback or approval writer.
"""
import hashlib
import importlib
import os
from pathlib import Path
import subprocess
import sys
import time
import types


REPO = Path(r'C:\Users\anest\Documents\New project\home-energy-repo')
SOURCE = REPO / 'server'
CODEX = Path(r'C:\Users\anest\AppData\Local\OpenAI\Codex\bin\e78bad9887fa50cf\codex.exe')
VERSION = 'codex-cli 0.163.0-alpha.10.2'
CONFIG_SHA256 = '8416490849ba3e5aa0a4d298c48b7f15c10e32262b09b035349a1c5dea7e3a95'
PYTHON_SHA256 = 'b847bb9f4e48c19f585c8bf7d8b355f3f8c6d2125717ece9c9d7e16fb9f09de8'
PYTHONW_SHA256 = '5ecb41b3b1cd7cf54fa0e5d8f47ed9a6def303cb9f36ef15be10cfd8f6c24257'
BINARIES = {
    'codex.exe': '9ecb9df2ecafcc00542ef0966c6f1737a81397dc9531b0a4b178238e2bf4a46d',
    'codex-code-mode-host.exe': 'b1b21c0dc7f2cb72d27970479403ae8a10fef6ef4af417fe7fe9f960f8c2755a',
    'codex-command-runner.exe': '0de2b882d1f763ecdf4fcc3873fec18ddcf20646b6291dac54d33a9f9116d4c4',
    'codex-windows-sandbox-setup.exe': '192827caa1e5dfaee2805849e4a8bf9fb8b52126acf334172fb4de19cc883cfc',
}
LEGACY_BUNDLE = '24998eee162fd7b6160c1fb41daf33708cf2a3f68a1b14d66b9d1de72ff4c6f9'
LEGACY_AUTHORIZATION = '74f438fc5970f8c7f17df618a913cb7131646440e3c5380afeef6dd1c008accf'
LEGACY_RUNS = 2
LEGACY_TIP = 'c98d61af5081f3b1d43481c0f4c26c3b5e76135e274579dc8660b0b9173da168'


def require(condition, code):
    if not condition:
        raise ValueError(code)


def configure_runtime(runtime):
    """Change metadata only, in memory; all original enforcement functions remain."""
    pins = dict(runtime.FILE_PINS)
    del pins[runtime.CODEX]
    runtime.CODEX, runtime.VERSION = CODEX, VERSION
    replacements = {
        runtime.USER_CONFIG: (CONFIG_SHA256, 'runtime_config_changed'),
        runtime.PYTHON: (PYTHON_SHA256, 'runtime_python_changed'),
        runtime.PYTHON.with_name('pythonw.exe'): (PYTHONW_SHA256, 'runtime_python_changed'),
        **{CODEX.parent / name: (pin, 'runtime_file_changed' if name == 'codex.exe'
                                else 'runtime_companion_changed') for name, pin in BINARIES.items()},
    }
    for path, (pin, code) in replacements.items():
        require(path.is_file(), 'runtime_file_missing')
        runtime.plain_file(path)
        require(path.stat().st_nlink == 1, code)
        require(hashlib.sha256(path.read_bytes()).hexdigest() == pin, code)
        pins[path] = pin
    runtime.FILE_PINS = pins


def bootstrap(trial, read_source, runner_hash, startup_hash, store_pin):
    """Reuse the source-only loader, policy verifiers and report protocols intact."""
    path = SOURCE / 'insights_private_review_run.py'
    helper = types.ModuleType('_recurring_revision_source_helpers')
    helper.__file__ = str(path)
    exec(compile(read_source(path, trial.HELPER_PIN), str(path), 'exec', dont_inherit=True), helper.__dict__)
    imports = helper.source_loader(SOURCE, {'insights_production_launcher': trial.CANDIDATE_PIN})
    sys.meta_path.insert(0, imports)
    candidate = importlib.import_module('insights_production_launcher')
    modules = candidate.MODULE_PINS | trial.PINS | {'insights_recurring_store': store_pin}
    imports.extend(helper.pinned_sources(SOURCE, modules))
    runtime = importlib.import_module('insights_staging_launcher')
    configure_runtime(runtime)
    base, _ = runtime.load_inputs()
    adapter = importlib.import_module('insights_production_mcp')
    storage = importlib.import_module('insights_recurring_store')
    sessions = importlib.import_module('insights_production_trial_session')
    for path, pin in candidate.FILE_PINS.items():
        candidate.read_pinned(path, pin)
    for parent in (candidate.WORKSPACE, *candidate.WORKSPACE.parents):
        path = parent / '.codex/config.toml'
        require(path == runtime.USER_CONFIG or not path.exists(), 'production_trial_ancestor_config')
    require({p.name for p in candidate.WORKSPACE.iterdir()} == {'AGENTS.md', 'allowed.txt', 'denied.txt'},
            'production_trial_workspace_changed')
    guidance_path = REPO / 'docs/REPORTING_SKILL.md'
    guidance = candidate.read_pinned(guidance_path, candidate.FILE_PINS[guidance_path]).decode('utf-8')
    config = candidate.build_config(runtime, base, 'Closed preflight; no turn is authorized.')
    connection = importlib.import_module('insights_production_connection')
    connection.check_integration()
    imports.verify_loaded()
    bundle = storage.digest({'revision': '20261016', 'runner_sha256': runner_hash,
        'revision_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'startup_sha256': startup_hash, 'trial_source_sha256': hashlib.sha256(Path(trial.__file__).read_bytes()).hexdigest(),
        'helper_sha256': trial.HELPER_PIN, 'candidate_sha256': trial.CANDIDATE_PIN,
        'modules': modules, 'files': {str(p): h for p, h in candidate.FILE_PINS.items()},
        'runtime_files': {str(p): h for p, h in runtime.FILE_PINS.items()},
        'binding_sha256': connection.BINDING_SHA256})
    return types.SimpleNamespace(candidate=candidate, runtime=runtime, adapter=adapter,
        storage=storage, original_storage=storage, trial=trial, sessions=sessions, config=config,
        guidance=guidance, bundle=bundle, imports=imports)


def approval_body(bundle, approved_at):
    return {'schema_version': 1, 'scope': 'recurring_runtime_repair', 'revision': '20261016',
        'original_authorization_sha256': LEGACY_AUTHORIZATION, 'original_bundle_sha256': LEGACY_BUNDLE,
        'bundle_sha256': bundle, 'legacy_runs': LEGACY_RUNS, 'legacy_terminal_sha256': LEGACY_TIP,
        'approved_unix': approved_at, 'requires_separate_activation': True}


def validate_approval(value, bundle, grant, storage):
    require(type(value) is dict and type(value.get('approved_unix')) is int
            and type(value.get('schema_version')) is int and type(value.get('legacy_runs')) is int
            and value.get('requires_separate_activation') is True
            and grant['issued_unix'] <= value['approved_unix'] <= time.time()
            and value['approved_unix'] < grant['expires_unix']
            and value == approval_body(bundle, value['approved_unix']), 'recurring_repair_approval_changed')
    require(storage.digest(grant) == LEGACY_AUTHORIZATION, 'recurring_repair_binding_changed')


def validate_activation(value, approval, bundle, grant, storage):
    if value is None:
        return False
    require(approval is not None and type(value) is dict
            and type(value.get('schema_version')) is int
            and type(value.get('activated_unix')) is int
            and approval['approved_unix'] <= value['activated_unix'] <= time.time()
            and value['activated_unix'] < grant['expires_unix']
            and value == {'schema_version': 1, 'scope': 'recurring_runtime_activation',
                'approval_sha256': storage.digest(approval), 'bundle_sha256': bundle,
                'activated_unix': value['activated_unix']}, 'recurring_repair_activation_changed')
    return True


def revised_store(storage, bundle, approval):
    class Store(storage.Store):
        def __init__(self, current_bundle, run_id=None, *, files=None):
            require(current_bundle == bundle, 'recurring_repair_binding_changed')
            super().__init__(current_bundle, run_id, files=files)

        def _grant(self):
            require(storage.filename('authorization') in self.files.names(), 'recurring_not_authorized')
            grant = self.files.read(storage.filename('authorization'), 4096)
            storage.validate_authorization(grant, LEGACY_BUNDLE, time.time())
            validate_approval(approval, self.bundle, grant, storage)
            return grant

        def _receipt(self, run_id, grant):
            receipt = self.files.read(storage.filename('receipt', run_id), 4096)
            expected_bundle = LEGACY_BUNDLE if int(run_id) <= LEGACY_RUNS else self.bundle
            earliest = grant['not_before_unix'] if int(run_id) <= LEGACY_RUNS else max(
                grant['not_before_unix'], approval['approved_unix'])
            require(set(receipt) == {'schema_version', 'scope', 'run_id', 'bundle_sha256',
                'authorization_sha256', 'token_sha256', 'created_unix', 'previous_terminal_sha256', 'outcome'}
                and type(receipt['schema_version']) is int and receipt['schema_version'] == 1
                and receipt['scope'] == 'production' and receipt['run_id'] == run_id
                and receipt['bundle_sha256'] == expected_bundle
                and receipt['authorization_sha256'] == storage.digest(grant)
                and type(receipt['created_unix']) is int
                and earliest <= receipt['created_unix'] < grant['expires_unix']
                and receipt['outcome'] == 'pending', 'recurring_receipt_changed')
            storage.trial.hash_value(receipt['token_sha256'])
            storage.trial.hash_value(receipt['previous_terminal_sha256'])
            return receipt

        def history(self, grant):
            result = super().history(grant)
            require(result['count'] >= LEGACY_RUNS, 'recurring_repair_history_changed')
            tip = self.files.read(storage.filename('terminal', f'{LEGACY_RUNS:06d}'))
            require(storage.digest(tip) == LEGACY_TIP, 'recurring_repair_history_changed')
            return result

    return Store


def handoff_history(storage, store, grant, token, *, armed):
    """Check the completed prefix and exactly one authenticated in-progress run.

    A worker/MCP process starts after its supervisor reserves a receipt. That
    receipt is intentionally not complete yet; it must never be mistaken for a
    completed run or ignored by a subsequent supervisor's normal history check.
    """
    run_id = store.run_id
    require(LEGACY_RUNS < int(run_id) <= grant['max_runs'], 'recurring_repair_handoff_changed')
    receipt = store.active(token, armed=armed)
    files = store.files

    def completed_names():
        names = set()
        for name in files.names():
            parsed = storage.validate_name(name)
            if parsed is None or parsed[0] != run_id:
                names.add(name)
        return names

    try:
        store.files = types.SimpleNamespace(names=completed_names, read=files.read, open=files.open)
        history = store.history(grant)
    finally:
        store.files = files
    require(run_id == f"{history['count'] + 1:06d}"
            and receipt['previous_terminal_sha256'] == history['previous']
            and receipt['created_unix'] - history['created'] >= storage.INTERVAL_SECONDS
            and receipt['created_unix'] >= history['completed'], 'recurring_repair_handoff_changed')
    return history


def bind(context, startup, *, active_run=None, token=None, armed=True):
    """Read-only approval/history check, including before any no-model probe."""
    storage = context.original_storage
    approval = startup.read_record(startup.APPROVAL)
    if approval is None:
        require(active_run is None, 'recurring_repair_approval_required')
        store = storage.Store(LEGACY_BUNDLE)
    else:
        store_type = revised_store(storage, context.bundle, approval)
        store = store_type(context.bundle, active_run)
    try:
        grant = store._grant()
        require(storage.digest(grant) == LEGACY_AUTHORIZATION, 'recurring_repair_binding_changed')
        history = (store.history(grant) if active_run is None else
                   handoff_history(storage, store, grant, token, armed=armed))
        require(history['count'] >= LEGACY_RUNS, 'recurring_repair_history_changed')
        tip = store.files.read(storage.filename('terminal', f'{LEGACY_RUNS:06d}'))
        require(storage.digest(tip) == LEGACY_TIP, 'recurring_repair_history_changed')
        if approval is None:
            require(history['count'] == LEGACY_RUNS, 'recurring_repair_history_changed')
    finally:
        store.close()
    activation = startup.read_record(startup.ACTIVATION)
    if approval is not None:
        context.storage = types.SimpleNamespace(Store=store_type, digest=storage.digest,
            run_identifier=storage.run_identifier)
    context.repair_active = validate_activation(activation, approval, context.bundle, grant, storage)
    return {'repair_approval_present': approval is not None, 'repair_active': context.repair_active,
        'completed_attempts': history['count'], 'remaining_attempts': grant['max_runs'] - history['count'],
        'authorization_expires_unix': grant['expires_unix'], 'bundle_sha256': context.bundle}


def check_version(runtime):
    result = subprocess.run([str(runtime.CODEX), '--version'], capture_output=True, timeout=10,
        env=runtime.clean_environment(os.environ), creationflags=subprocess.CREATE_NO_WINDOW)
    require(result.returncode == 0 and result.stdout.decode().strip() == VERSION, 'production_runtime_version')
