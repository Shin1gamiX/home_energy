"""Offline production-key checks; never generate or inspect real credentials."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'deploy/prepare-production-key.ps1'
PWSH = shutil.which('pwsh')
FUNCTION_LOADER = r'''
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:HOMEENERGY_TEST_SOURCE, [ref]$tokens, [ref]$errors)
if ($errors.Count -ne 0) { throw 'Production credential helper has syntax errors.' }
$functions = $ast.FindAll({param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst]
}, $false)
foreach ($function in $functions) {
    . ([scriptblock]::Create($function.Extent.Text))
}
'''


def run_powershell(script, **extra_environment):
    environment = os.environ.copy()
    environment.update(HOMEENERGY_TEST_SOURCE=str(SOURCE), **extra_environment)
    result = subprocess.run(
        [PWSH, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script],
        capture_output=True, text=True, timeout=30, env=environment,
    )
    if result.returncode:
        raise AssertionError(f'Offline PowerShell test failed: {result.stderr}')
    return json.loads(result.stdout)


class ProductionKeyStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text(encoding='utf-8')

    def test_paths_and_comment_are_fixed_and_production_only(self):
        self.assertIn("'C:\\ProgramData\\HomeEnergyReporterProduction'", self.source)
        self.assertIn("'C:\\Windows\\System32\\OpenSSH\\ssh-keygen.exe'", self.source)
        self.assertIn("-C 'homeenergy-production-only'", self.source)
        self.assertNotIn('HomeEnergyReporterStaging', self.source)
        self.assertNotIn('codex_access_key', self.source)

    def test_no_network_scheduler_or_private_content_read(self):
        for forbidden in ('Get-Content', 'ReadAllBytes', 'ReadAllText', 'ReadFile(',
                          'Remove-Item', 'Set-Content', 'Invoke-WebRequest',
                          'Start-Process', 'ssh.exe', 'scp.exe', 'schtasks', 'codex.exe'):
            self.assertNotIn(forbidden, self.source)
        self.assertIn('CreateFileW(path, 0x80, 0x1, IntPtr.Zero, 3, 0x02200000', self.source)

    def test_creation_supplies_descriptor_atomically_and_no_reuse(self):
        self.assertIn('CreateDirectoryW(path, ref security)', self.source)
        self.assertIn('Descriptor = pinned.AddrOfPinnedObject()', self.source)
        self.assertIn('if (!CreateDirectoryW', self.source)
        self.assertNotIn('Directory.CreateDirectory(', self.source)
        self.assertNotIn('New-Item', self.source)
        self.assertIn("$generationOutput = 'n' | & $script:keygen", self.source)

    def test_files_must_be_plain_single_link_bounded_and_nonempty(self):
        self.assertIn('info.NumberOfLinks != 1', self.source)
        self.assertIn('info.Attributes & (0x400U | 0x10U)', self.source)
        self.assertIn('info.FileSizeLow == 0', self.source)
        self.assertIn('info.FileSizeLow > 4096', self.source)
        self.assertIn('info.FileSizeHigh != 0', self.source)

    def test_acl_is_owner_pinned_protected_and_allowlisted(self):
        self.assertIn("'S-1-5-18', 'S-1-5-32-544'", self.source)
        self.assertIn('$rules.Count -ne 3', self.source)
        self.assertIn('$rule.IsInherited', self.source)
        self.assertIn('-not $acl.AreAccessRulesProtected', self.source)
        self.assertIn('$acl.SetAccessRuleProtection($true, $false)', self.source)
        self.assertIn('$acl.SetOwner($script:userSid)', self.source)
        self.assertIn('Unexpected access-rule inheritance.', self.source)

    def test_fingerprint_uses_public_file_and_never_derives_from_private(self):
        self.assertIn('-lf $PublicPath', self.source)
        self.assertIn('Get-ProductionKeyFingerprint "$script:keyPath.pub"', self.source)
        self.assertNotRegex(self.source, r'\s-y(?:\s|$)')
        self.assertIn("^256 (SHA256:[A-Za-z0-9+/]{43}) homeenergy-production-only", self.source)


@unittest.skipUnless(os.name == 'nt' and PWSH, 'Windows PowerShell 7 is required')
class ProductionKeyFlowTests(unittest.TestCase):
    def flow(self, scenario='create', verify_only=False):
        return run_powershell(FUNCTION_LOADER + r'''
$script:events = [System.Collections.Generic.List[string]]::new()
function Initialize-ProductionCredentialMetadata { $script:events.Add('initialize') }
function Assert-PlainPath([string] $Path) {
    $script:events.Add('plain:' + $Path)
    if ($env:HOMEENERGY_TEST_SCENARIO -eq 'reparse') { throw 'reparse denied' }
}
function Assert-PrivateAcl([string] $Path) {
    $script:events.Add('acl:' + $Path)
    if ($env:HOMEENERGY_TEST_SCENARIO -eq 'acl') { throw 'acl denied' }
}
function Test-Path { return $env:HOMEENERGY_TEST_SCENARIO -eq 'exists' }
function New-PrivateCredentialDirectory([string] $Path) {
    $script:events.Add('create:' + $Path)
    if ($env:HOMEENERGY_TEST_SCENARIO -eq 'race') { throw 'already exists' }
}
function Get-ChildItem {
    if ($env:HOMEENERGY_TEST_SCENARIO -eq 'nonempty') { return 'unexpected' }
    return @()
}
function Invoke-ProductionKeyGeneration([string] $Path) {
    $script:events.Add('generate:' + $Path)
    if ($env:HOMEENERGY_TEST_SCENARIO -eq 'keygen') { throw 'generation failed' }
}
function Protect-GeneratedKey([string] $Path) { $script:events.Add('protect:' + $Path) }
function Assert-KeyFile([string] $Path) {
    $script:events.Add('metadata:' + $Path)
    if ($env:HOMEENERGY_TEST_SCENARIO -eq 'hardlink') { throw 'hardlink denied' }
}
function Get-ProductionKeyFingerprint([string] $PublicPath) {
    $script:events.Add('fingerprint:' + $PublicPath)
    if ($env:HOMEENERGY_TEST_SCENARIO -eq 'fingerprint') { throw 'fingerprint denied' }
    return 'SHA256:SYNTHETIC'
}
$failed = $false
try {
    $result = @(Invoke-ProductionCredentialPreparation -VerifyOnly:($env:HOMEENERGY_TEST_VERIFY -eq 'true'))
} catch { $failed = $true; $result = @() }
[pscustomobject]@{ failed=$failed; events=@($script:events); output=$result } | ConvertTo-Json -Compress
''', HOMEENERGY_TEST_SCENARIO=scenario, HOMEENERGY_TEST_VERIFY=str(verify_only).lower())

    def test_creation_workflow_uses_exactly_one_new_directory_and_keypair(self):
        result = self.flow()
        self.assertFalse(result['failed'])
        events = result['events']
        for name, count in [('create:', 1), ('generate:', 1), ('protect:', 2),
                            ('metadata:', 2), ('fingerprint:', 1)]:
            self.assertEqual(sum(value.startswith(name) for value in events), count)
        self.assertLess(next(i for i, event in enumerate(events) if event.startswith('acl:')),
                        next(i for i, event in enumerate(events) if event.startswith('generate:')))
        self.assertTrue(events[-1].endswith('id_ed25519.pub'))

    def test_verify_only_never_creates_generates_or_changes_acl(self):
        result = self.flow(verify_only=True)
        self.assertFalse(result['failed'])
        for event in result['events']:
            self.assertFalse(event.startswith(('create:', 'generate:', 'protect:')))
        self.assertEqual(sum(x.startswith('metadata:') for x in result['events']), 2)

    def test_preexisting_root_is_never_reused(self):
        result = self.flow('exists')
        self.assertTrue(result['failed'])
        self.assertFalse(any(x.startswith(('create:', 'generate:', 'protect:')) for x in result['events']))

    def test_raced_root_is_never_used_after_atomic_creation_refuses(self):
        result = self.flow('race')
        self.assertTrue(result['failed'])
        self.assertFalse(any(x.startswith(('generate:', 'protect:')) for x in result['events']))

    def test_reparse_and_acl_denial_stop_before_key_generation(self):
        for scenario in ('reparse', 'acl', 'nonempty'):
            with self.subTest(scenario=scenario):
                result = self.flow(scenario)
                self.assertTrue(result['failed'])
                self.assertFalse(any(x.startswith('generate:') for x in result['events']))

    def test_generation_failure_retains_output_without_acl_or_metadata_operations(self):
        result = self.flow('keygen')
        self.assertTrue(result['failed'])
        self.assertFalse(any(x.startswith(('protect:', 'metadata:', 'fingerprint:')) for x in result['events']))

    def test_metadata_failure_prevents_fingerprinting(self):
        result = self.flow('hardlink', verify_only=True)
        self.assertTrue(result['failed'])
        self.assertFalse(any(x.startswith('fingerprint:') for x in result['events']))

    def test_fingerprint_failure_produces_no_success_output(self):
        result = self.flow('fingerprint', verify_only=True)
        self.assertTrue(result['failed'])
        self.assertEqual(result['output'], [])


@unittest.skipUnless(os.name == 'nt' and PWSH, 'Windows PowerShell 7 is required')
class ProductionKeyNativeMetadataTests(unittest.TestCase):
    def test_private_directory_creation_and_file_acl_normalization_on_synthetic_files(self):
        with tempfile.TemporaryDirectory(prefix='he-production-key-acl-') as folder:
            result = run_powershell(FUNCTION_LOADER + r'''
$script:userSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$script:allowedSids = @($script:userSid.Value, 'S-1-5-18', 'S-1-5-32-544')
Initialize-ProductionCredentialMetadata
$directory = Join-Path $env:HOMEENERGY_TEST_FIXTURE 'synthetic-only'
New-PrivateCredentialDirectory $directory
Assert-PlainPath $directory
Assert-PrivateAcl $directory
$aclBefore = (Get-Acl -LiteralPath $directory).Sddl
$duplicateDenied = $false
try { New-PrivateCredentialDirectory $directory } catch { $duplicateDenied = $true }
$duplicateUnchanged = $aclBefore -eq (Get-Acl -LiteralPath $directory).Sddl
$file = Join-Path $directory 'not-a-key.txt'
[System.IO.File]::WriteAllBytes($file, [System.Text.Encoding]::ASCII.GetBytes('SYNTHETIC; NOT A KEY'))
Protect-GeneratedKey $file
Assert-KeyFile $file
Assert-PrivateAcl $file
[pscustomobject]@{
    duplicateDenied=$duplicateDenied; duplicateUnchanged=$duplicateUnchanged; fileAclVerified=$true
} | ConvertTo-Json -Compress
''', HOMEENERGY_TEST_FIXTURE=folder)
            self.assertEqual(result, dict(duplicateDenied=True, duplicateUnchanged=True,
                                          fileAclVerified=True))

    def test_metadata_accepts_only_small_regular_single_link_synthetic_files(self):
        with tempfile.TemporaryDirectory(prefix='he-production-key-metadata-') as folder:
            root = Path(folder)
            (root / 'plain').write_bytes(b'NOT A KEY; SYNTHETIC TEST ONLY')
            (root / 'empty').touch()
            (root / 'large').write_bytes(b'x' * 4097)
            (root / 'directory').mkdir()
            (root / 'linked').write_bytes(b'SYNTHETIC')
            os.link(root / 'linked', root / 'second-name')
            result = run_powershell(FUNCTION_LOADER + r'''
Initialize-ProductionCredentialMetadata
$results = @{}
foreach ($name in @('plain', 'empty', 'large', 'directory', 'linked', 'missing')) {
    try {
        [HomeEnergyProductionCredentialMetadata]::VerifyKeyFile((Join-Path $env:HOMEENERGY_TEST_FIXTURE $name))
        $results[$name] = $true
    } catch { $results[$name] = $false }
}
$results | ConvertTo-Json -Compress
''', HOMEENERGY_TEST_FIXTURE=folder)
            self.assertEqual(result, dict(plain=True, empty=False, large=False,
                                          directory=False, linked=False, missing=False))


if __name__ == '__main__':
    unittest.main()
