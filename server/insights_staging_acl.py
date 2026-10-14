"""Read-only Windows ACL guard for the four fixed staging material paths.

The child emits anonymous security metadata only. It never reads credential
contents. The public guard exposes only a fixed success value or failure code;
this check does not replace the transport's canonical-path and hardlink guards.
"""

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess


POWERSHELL = Path(r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe')
POWERSHELL_SHA256 = '8bb6fa8c283b4d92120b1ef249a9b311b0f804d4cabbe9981159976c8be76a5e'
MAX_METADATA_BYTES = 8192
TIMEOUT_SECONDS = 10
FULL_CONTROL = 2032127
PRINCIPALS = frozenset(('current', 'system', 'administrators'))
ENTRIES = frozenset(('root', 'private_key', 'host_pin', 'binding'))
ENTRY_FIELDS = frozenset(('plain', 'owner_is_current', 'dacl_present', 'protected',
                          'raw_ace_count', 'raw_aces_supported', 'rules'))
RULE_FIELDS = frozenset(('principal', 'access', 'rights', 'inherited', 'inheritance', 'propagation'))
SAFE_ENV = {
    'SystemRoot': r'C:\Windows',
    'WINDIR': r'C:\Windows',
    'PATH': r'C:\Windows\System32;C:\Windows',
    'PSModulePath': r'C:\Windows\System32\WindowsPowerShell\v1.0\Modules',
}

# No caller-supplied path, SID, script fragment or environment value is inserted.
# Get-Acl reads the security descriptor, not the contents of the target file.
ACL_METADATA_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
try {
    $heRoot = 'C:\ProgramData\HomeEnergyReporterStaging'
    $heCurrent = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $hePaths = [ordered]@{
        root = $heRoot
        private_key = 'C:\ProgramData\HomeEnergyReporterStaging\id_ed25519'
        host_pin = 'C:\ProgramData\HomeEnergyReporterStaging\known_hosts'
        binding = 'C:\ProgramData\HomeEnergyReporterStaging\integration-binding.json'
    }
    $heResult = [ordered]@{}
    foreach ($heName in $hePaths.Keys) {
        $hePath = $hePaths[$heName]
        $heItem = Microsoft.PowerShell.Management\Get-Item -LiteralPath $hePath -Force
        $hePlain = [string]::Equals($heItem.FullName, $hePath, [StringComparison]::OrdinalIgnoreCase)
        if ($heName -eq 'root') {
            $hePlain = $hePlain -and ($heItem -is [System.IO.DirectoryInfo])
        } else {
            $hePlain = $hePlain -and ($heItem -is [System.IO.FileInfo])
            $hePlain = $hePlain -and [string]::Equals($heItem.Directory.FullName, $heRoot, [StringComparison]::OrdinalIgnoreCase)
        }
        $heAncestor = $heItem
        while ($null -ne $heAncestor) {
            if ($heAncestor.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                $hePlain = $false
            }
            $heAncestor = if ($heAncestor -is [System.IO.DirectoryInfo]) { $heAncestor.Parent } else { $heAncestor.Directory }
        }
        $heAcl = Microsoft.PowerShell.Security\Get-Acl -LiteralPath $hePath
        $heRaw = [System.Security.AccessControl.RawSecurityDescriptor]::new($heAcl.GetSecurityDescriptorBinaryForm(), 0)
        $hePresent = [bool]($heRaw.ControlFlags -band [System.Security.AccessControl.ControlFlags]::DiscretionaryAclPresent)
        $hePresent = $hePresent -and (-not [object]::ReferenceEquals($null, $heRaw.DiscretionaryAcl))
        # GetAccessRules is a projection: independently reject raw ACEs that
        # could be omitted or lose callback/object/custom semantics there.
        $heRawCount = -1
        $heRawSupported = $false
        if ($hePresent) {
            $heRawCount = $heRaw.DiscretionaryAcl.Count
            $heRawSupported = $true
            foreach ($heRawAce in $heRaw.DiscretionaryAcl) {
                if ($heRawAce.GetType() -ne [System.Security.AccessControl.CommonAce]) {
                    $heRawSupported = $false
                    continue
                }
                # 19 allows only ObjectInherit (1), ContainerInherit (2),
                # and Inherited (16); the projected policy narrows them further.
                if ($heRawAce.AceType -ne [System.Security.AccessControl.AceType]::AccessAllowed -or
                    $heRawAce.IsCallback -or $heRawAce.OpaqueLength -ne 0 -or
                    (([int]$heRawAce.AceFlags -band (-bnot 19)) -ne 0)) {
                    $heRawSupported = $false
                }
            }
        }
        $heRules = @()
        foreach ($heRule in $heAcl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])) {
            $heSid = $heRule.IdentityReference.Value
            $hePrincipal = if ($heSid -eq $heCurrent) { 'current' } elseif ($heSid -eq 'S-1-5-18') { 'system' } elseif ($heSid -eq 'S-1-5-32-544') { 'administrators' } else { 'other' }
            $heRules += [ordered]@{
                principal = $hePrincipal
                access = $heRule.AccessControlType.ToString()
                rights = [int]$heRule.FileSystemRights
                inherited = [bool]$heRule.IsInherited
                inheritance = [int]$heRule.InheritanceFlags
                propagation = [int]$heRule.PropagationFlags
            }
        }
        $heResult[$heName] = [ordered]@{
            plain = [bool]$hePlain
            owner_is_current = ($heAcl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -eq $heCurrent)
            dacl_present = [bool]$hePresent
            protected = [bool]$heAcl.AreAccessRulesProtected
            raw_ace_count = [int]$heRawCount
            raw_aces_supported = [bool]$heRawSupported
            rules = $heRules
        }
    }
    $heJson = Microsoft.PowerShell.Utility\ConvertTo-Json -InputObject $heResult -Depth 5 -Compress
    if ($heJson.Length -gt 8192) { throw 'oversize' }
    [Console]::Out.Write($heJson)
} catch {
    [Console]::Error.Write('acl_metadata_unavailable')
    exit 1
}
"""


class AclFailure(ValueError):
    """A fixed code only; no path, identity or subprocess content is exposed."""


def require(condition, code):
    if not condition:
        raise AclFailure(code)


def validate_snapshot(snapshot):
    """Validate anonymous metadata, including exact effective ACE semantics."""
    require(type(snapshot) is dict and set(snapshot) == ENTRIES, 'acl_metadata_shape')
    for name in ('root', 'private_key', 'host_pin', 'binding'):
        entry = snapshot[name]
        require(type(entry) is dict and set(entry) == ENTRY_FIELDS, 'acl_metadata_shape')
        for flag in ('plain', 'owner_is_current', 'dacl_present', 'protected', 'raw_aces_supported'):
            require(type(entry[flag]) is bool, 'acl_metadata_shape')
        require(entry['plain'], 'acl_nonplain_path')
        require(entry['owner_is_current'], 'acl_owner_rejected')
        require(entry['dacl_present'], 'acl_missing_dacl')
        require(type(entry['raw_ace_count']) is int and entry['raw_ace_count'] == 3,
                'acl_raw_ace_count')
        require(entry['raw_aces_supported'], 'acl_raw_ace_unsupported')
        if name in ('root', 'private_key'):
            require(entry['protected'], 'acl_unprotected_private_material')
        rules = entry['rules']
        require(type(rules) is list and len(rules) == 3, 'acl_rule_count')
        seen = set()
        for rule in rules:
            require(type(rule) is dict and set(rule) == RULE_FIELDS, 'acl_metadata_shape')
            require(type(rule['principal']) is str and rule['principal'] in PRINCIPALS,
                    'acl_principal_rejected')
            require(rule['principal'] not in seen, 'acl_duplicate_principal')
            seen.add(rule['principal'])
            require(rule['access'] == 'Allow' and type(rule['access']) is str,
                    'acl_access_rejected')
            require(type(rule['rights']) is int and rule['rights'] == FULL_CONTROL,
                    'acl_rights_rejected')
            require(type(rule['inherited']) is bool, 'acl_metadata_shape')
            for field in ('inheritance', 'propagation'):
                require(type(rule[field]) is int, 'acl_metadata_shape')
            require(rule['propagation'] == 0, 'acl_propagation_rejected')
            require(rule['inheritance'] == (3 if name == 'root' else 0),
                    'acl_inheritance_rejected')
            # The two inheritable files are fixed immediate children of the
            # verified protected root. Mixed explicit/inherited DACLs fail closed.
            require(rule['inherited'] is (not entry['protected']), 'acl_inherited_rule_rejected')
        require(seen == PRINCIPALS, 'acl_principal_rejected')
    return {'acl_verified': True}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'acl_metadata_duplicate_key')
        result[key] = value
    return result


def parse_snapshot(body):
    require(type(body) is bytes and 0 < len(body) <= MAX_METADATA_BYTES, 'acl_metadata_size')
    try:
        return json.loads(body.decode('ascii'), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise AclFailure('acl_metadata_encoding') from None


def _verify_powershell():
    try:
        require(POWERSHELL.is_absolute() and POWERSHELL.resolve(strict=True) == POWERSHELL,
                'acl_probe_executable_rejected')
        for item in (POWERSHELL, *POWERSHELL.parents):
            require(not getattr(item.lstat(), 'st_file_attributes', 0)
                    & stat.FILE_ATTRIBUTE_REPARSE_POINT, 'acl_probe_executable_rejected')
        require(stat.S_ISREG(POWERSHELL.stat().st_mode), 'acl_probe_executable_rejected')
        # Windows services its system executable through hardlinks; only its
        # exact reviewed binary is accepted. Private files retain strict checks.
        require(hashlib.sha256(POWERSHELL.read_bytes()).hexdigest() == POWERSHELL_SHA256,
                'acl_probe_executable_rejected')
    except OSError:
        raise AclFailure('acl_probe_executable_rejected') from None


def verify_permissions():
    """Read four fixed ACLs; return fixed success or raise a sanitized failure."""
    require(os.name == 'nt', 'acl_windows_required')
    _verify_powershell()
    try:
        result = subprocess.run(
            [str(POWERSHELL), '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', ACL_METADATA_SCRIPT],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=SAFE_ENV.copy(), timeout=TIMEOUT_SECONDS, check=False,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except (OSError, subprocess.SubprocessError):
        raise AclFailure('acl_probe_failed') from None
    require(result.returncode == 0 and result.stderr == b'', 'acl_probe_failed')
    return validate_snapshot(parse_snapshot(result.stdout))
