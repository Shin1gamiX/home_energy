"""Secret-free, create-once startup diagnostics in the existing private directory.

Independent of the reporting bootstrap so a changed runtime/configuration can
still be diagnosed. Never imports project modules, reads a key, or runs a model.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess


POWERSHELL = Path(r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe')
POWERSHELL_SHA256 = '8bb6fa8c283b4d92120b1ef249a9b311b0f804d4cabbe9981159976c8be76a5e'
APPROVAL = 'runtime-repair-20261016-approval.json'
ACTIVATION = 'runtime-repair-20261016-activation.json'
RECOVERY_REVIEW = 'runtime-recovery-20261016-review.json'
RECOVERY_ACTIVATION = 'runtime-recovery-20261016-activation.json'
READABLE_RECORDS = (APPROVAL, ACTIVATION, RECOVERY_REVIEW, RECOVERY_ACTIVATION,
    'startup-20261016T090010504462Z-81acdccdc612b3d6.json',
    'startup-20261016T090010907253Z-8d8d7c5ec366b288.json')
MODES = frozenset(('check', 'preflight', 'os-check', 'storage-check', 'logging-check',
                   'run-due', 'worker', 'unknown'))
PHASES = frozenset(('bootstrap', 'authorization', 'runtime', 'os', 'execute', 'complete'))
CODES = frozenset((
    'reviewed_input_changed', 'runtime_file_missing', 'runtime_file_changed',
    'runtime_config_changed', 'runtime_python_changed', 'runtime_companion_changed',
    'runtime_policy_mismatch', 'runtime_profile_mismatch', 'runtime_capability_mismatch',
    'runtime_features_mismatch', 'runtime_apps_mismatch', 'runtime_model_mismatch',
    'runtime_notify_mismatch', 'runtime_instructions_mismatch', 'runtime_servers_mismatch',
    'runtime_server_config_mismatch', 'runtime_server_environment_mismatch',
    'runtime_instruction_sources_mismatch', 'runtime_timeout', 'runtime_rpc_failed',
    'production_runtime_rpc_failed', 'production_runtime_version', 'production_runtime_profile',
    'production_runtime_policy', 'production_runtime_provider', 'production_runtime_app_override',
    'production_trial_runtime_rpc_failed', 'production_trial_chatgpt_auth_required',
    'production_trial_runtime_timeout', 'production_thread_profile', 'production_inventory_servers',
    'production_inventory_tools', 'production_tool_contract', 'production_os_denial_not_verified',
    'recurring_source_changed', 'recurring_source_path', 'recurring_source_type',
    'recurring_source_reparse', 'recurring_installation', 'recurring_preloaded_module',
    'recurring_authorization_expired', 'recurring_authorization_ending', 'recurring_not_authorized',
    'recurring_authorization_invalid', 'recurring_authorization_limits', 'recurring_bundle_changed',
    'recurring_prior_run_needs_review', 'recurring_run_limit', 'recurring_busy',
    'recurring_evidence_changed', 'recurring_evidence_chain_changed', 'recurring_receipt_changed',
    'recurring_worker_failed_review_evidence', 'recurring_repair_approval_required',
    'recurring_repair_approval_changed', 'recurring_repair_not_activated',
    'recurring_repair_history_changed', 'recurring_repair_activation_changed',
    'recurring_repair_binding_changed', 'recurring_repair_handoff_changed', 'recurring_live_probe_refused',
    'recurring_no_model_only', 'synthetic_startup_check', 'unexpected_failure',
    'recurring_recovery_evidence_changed', 'recurring_recovery_review_changed',
    'recurring_recovery_activation_changed', 'recurring_recovery_handoff_changed',
    'recurring_recovery_prior_repair_changed', 'recurring_recovery_review_required',
))

# Only fixed filenames and a bounded, separately encoded JSON payload cross
# stdin. Nothing from an exception, model, environment or CLI enters script text.
PRIVATE_IO_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
try {
    $root = 'C:\ProgramData\HomeEnergyReporterProduction'
    $current = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $expected = @($current, 'S-1-5-18', 'S-1-5-32-544')
    function Assert-Acl($acl, [bool]$directory) {
        if ($acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $current) { throw 'owner' }
        if ($acl.AreAccessRulesProtected -ne $directory) { throw 'inheritance' }
        $raw = [System.Security.AccessControl.RawSecurityDescriptor]::new($acl.GetSecurityDescriptorSddlForm('All'))
        if (-not ($raw.ControlFlags -band 4) -or $null -eq $raw.DiscretionaryAcl -or $raw.DiscretionaryAcl.Count -ne 3) { throw 'dacl' }
        $seen = @()
        foreach ($ace in $raw.DiscretionaryAcl) {
            $flags = if ($directory) { 3 } else { 16 }
            if ($ace.AceType -ne 0 -or $ace.IsCallback -or $ace.OpaqueLength -ne 0 -or [int]$ace.AceFlags -ne $flags -or $ace.AccessMask -ne 2032127) { throw 'ace' }
            $sid = $ace.SecurityIdentifier.Value
            if ($sid -notin $expected -or $sid -in $seen) { throw 'principal' }
            $seen += $sid
        }
    }
    for ($parent = [IO.DirectoryInfo]::new($root); $null -ne $parent; $parent = $parent.Parent) {
        if (-not $parent.Exists -or ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'parent' }
    }
    Assert-Acl (Get-Acl -LiteralPath $root) $true
    $inputText = [Console]::In.ReadToEnd()
    if ($inputText.Length -gt 8192) { throw 'size' }
    $request = ConvertFrom-Json -InputObject $inputText
    $name = [string]$request.name
    if ($request.operation -eq 'log') {
        if ($name -cnotmatch '^startup-[0-9]{8}T[0-9]{12}Z-[a-f0-9]{16}\.json$') { throw 'name' }
        if (@(Get-ChildItem -LiteralPath $root -Filter 'startup-*.json' -File).Count -ge 1024) { throw 'limit' }
        $body = [Text.Encoding]::UTF8.GetBytes([string]$request.body)
        if ($body.Length -gt 2048 -or $body.Length -eq 0) { throw 'size' }
        $stream = [IO.FileStream]::new([IO.Path]::Combine($root, $name), [IO.FileMode]::CreateNew,
            [IO.FileAccess]::ReadWrite, [IO.FileShare]::None, 4096, [IO.FileOptions]::WriteThrough)
        try {
            Assert-Acl ($stream.GetAccessControl()) $false
            $stream.Write($body, 0, $body.Length)
            $stream.Flush($true)
        } finally { $stream.Dispose() }
        [Console]::Out.Write('saved')
    } elseif ($request.operation -eq 'read') {
        if ($name -cnotin @('runtime-repair-20261016-approval.json', 'runtime-repair-20261016-activation.json',
            'runtime-recovery-20261016-review.json', 'runtime-recovery-20261016-activation.json',
            'startup-20261016T090010504462Z-81acdccdc612b3d6.json',
            'startup-20261016T090010907253Z-8d8d7c5ec366b288.json')) { throw 'name' }
        $path = [IO.Path]::Combine($root, $name)
        if (-not [IO.File]::Exists($path)) { [Console]::Out.Write('missing'); exit 0 }
        if ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'link' }
        $stream = [IO.FileStream]::new($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::None)
        try {
            Assert-Acl ($stream.GetAccessControl()) $false
            if ($stream.Length -le 0 -or $stream.Length -gt 4096) { throw 'size' }
            $buffer = [byte[]]::new([int]$stream.Length)
            if ($stream.Read($buffer, 0, $buffer.Length) -ne $buffer.Length) { throw 'read' }
            [Console]::Out.Write([Convert]::ToBase64String($buffer))
        } finally { $stream.Dispose() }
    } else { throw 'operation' }
} catch { [Console]::Error.Write('private_startup_io_failed'); exit 1 }
'''


def private_io(request):
    if os.name != 'nt' or POWERSHELL.resolve(strict=True) != POWERSHELL:
        raise ValueError('startup_log_unavailable')
    if hashlib.sha256(POWERSHELL.read_bytes()).hexdigest() != POWERSHELL_SHA256:
        raise ValueError('startup_log_unavailable')
    result = subprocess.run([str(POWERSHELL), '-NoLogo', '-NoProfile', '-NonInteractive',
        '-Command', PRIVATE_IO_SCRIPT], input=json.dumps(request).encode('ascii'),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=False,
        env={'SystemRoot': r'C:\Windows', 'WINDIR': r'C:\Windows',
             'PATH': r'C:\Windows\System32;C:\Windows',
             'PSModulePath': r'C:\Windows\System32\WindowsPowerShell\v1.0\Modules'},
        creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode != 0 or result.stderr or len(result.stdout) > 8192:
        raise ValueError('startup_log_unavailable')
    return result.stdout


def read_record(name):
    import base64
    if name not in READABLE_RECORDS:
        raise ValueError('recurring_repair_binding_changed')
    result = private_io({'operation': 'read', 'name': name})
    if result == b'missing':
        return None
    raw = base64.b64decode(result, validate=True)
    value = json.loads(raw)
    if json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii') + b'\n' != raw:
        raise ValueError('recurring_repair_binding_changed')
    return value


def error_record(error, mode, phase, *, synthetic=False):
    if type(synthetic) is not bool:
        raise ValueError('startup_log_unavailable')
    code = error.args[0] if isinstance(error, ValueError) and len(error.args) == 1 else None
    return {'schema_version': 1, 'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        'event': 'startup_or_run_rejected', 'synthetic': synthetic,
        'mode': mode if mode in MODES else 'unknown',
        'phase': phase if phase in PHASES else 'bootstrap',
        'code': code if type(code) is str and code in CODES else 'unexpected_failure'}


def record_failure(error, mode, phase, *, synthetic=False):
    value = error_record(error, mode, phase, synthetic=synthetic)
    name = 'startup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ-') + secrets.token_hex(8) + '.json'
    body = json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n'
    if private_io({'operation': 'log', 'name': name, 'body': body}) != b'saved':
        raise ValueError('startup_log_unavailable')
    return name
