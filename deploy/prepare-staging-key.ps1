# Run only after approval to generate a new dedicated staging credential.
# No server connection, model run, scheduler change or private-key read occurs.
# Existing paths are never reused/overwritten; failed output is retained for review.
#requires -Version 7.0
[CmdletBinding()]
param([switch] $VerifyOnly)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$PSNativeCommandArgumentPassing = 'Standard'

if (-not $IsWindows) { throw 'Windows is required.' }
$materialRoot = 'C:\ProgramData\HomeEnergyReporterStaging'
$keyPath = Join-Path $materialRoot 'id_ed25519'
$keygen = 'C:\Windows\System32\OpenSSH\ssh-keygen.exe'
$userSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$allowedSids = @($userSid.Value, 'S-1-5-18', 'S-1-5-32-544')

function Assert-PlainPath([string] $Path) {
    $entry = Get-Item -LiteralPath $Path -Force
    while ($null -ne $entry) {
        if ($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw 'Reparse points are not allowed in credential paths.'
        }
        $entry = if ($entry -is [System.IO.DirectoryInfo]) { $entry.Parent } else { $entry.Directory }
    }
}

function Assert-PrivateAcl([string] $Path) {
    $acl = Get-Acl -LiteralPath $Path
    if ($acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $userSid.Value) {
        throw 'Unexpected credential owner.'
    }
    $rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
    if ($rules.Count -ne 3) { throw 'Unexpected credential access rules.' }
    $seen = @()
    foreach ($rule in $rules) {
        if ($rule.IdentityReference.Value -notin $allowedSids -or
            $rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow -or
            $rule.FileSystemRights -ne [System.Security.AccessControl.FileSystemRights]::FullControl) {
            throw 'Unexpected credential access rule.'
        }
        $seen += $rule.IdentityReference.Value
    }
    if (@($seen | Select-Object -Unique).Count -ne 3) { throw 'Duplicate credential access rule.' }
}

Assert-PlainPath 'C:\ProgramData'
Assert-PlainPath $keygen
if (-not $VerifyOnly -and (Test-Path -LiteralPath $materialRoot)) {
    throw 'Credential directory already exists; refusing to overwrite it. Use -VerifyOnly to inspect it.'
}

$directoryAcl = [System.Security.AccessControl.DirectorySecurity]::new()
$directoryAcl.SetOwner($userSid)
$directoryAcl.SetAccessRuleProtection($true, $false)
foreach ($sid in $allowedSids) {
    $identity = [System.Security.Principal.SecurityIdentifier]::new($sid)
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $identity, [System.Security.AccessControl.FileSystemRights]::FullControl,
        [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit',
        [System.Security.AccessControl.PropagationFlags]::None,
        [System.Security.AccessControl.AccessControlType]::Allow)
    $directoryAcl.AddAccessRule($rule)
}

# Supply the DACL during directory creation, not after a broadly readable mkdir.
if (-not $VerifyOnly) {
    [System.IO.FileSystemAclExtensions]::Create([System.IO.DirectoryInfo]::new($materialRoot), $directoryAcl)
}
Assert-PlainPath $materialRoot
Assert-PrivateAcl $materialRoot
if (-not (Get-Acl -LiteralPath $materialRoot).AreAccessRulesProtected) { throw 'Inheritance is not disabled.' }

# An empty passphrase supports unattended SSH. Protection is the private DACL,
# restricted model profile and server-only forced-command key, not encryption.
if (-not $VerifyOnly) {
    $generationOutput = & $keygen -q -t ed25519 -N '' -C 'homeenergy-staging-only' -f $keyPath 2>&1
    if ($LASTEXITCODE -ne 0) { throw 'Dedicated key generation failed; retained directory requires inspection.' }
    $generationOutput = $null
    # OpenSSH supplies its own file ACLs (including Everyone on the public key).
    # Normalize both inside the already private directory; never read private bytes.
    foreach ($path in @($keyPath, "$keyPath.pub")) {
        Assert-PlainPath $path
        $fileAcl = Get-Acl -LiteralPath $path
        if ($fileAcl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $userSid.Value) {
            throw 'Unexpected generated-key owner.'
        }
        $fileAcl.SetAccessRuleProtection($true, $false)
        foreach ($existingRule in @($fileAcl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))) {
            [void] $fileAcl.RemoveAccessRuleSpecific($existingRule)
        }
        foreach ($sid in $allowedSids) {
            $fileAcl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
                [System.Security.Principal.SecurityIdentifier]::new($sid),
                [System.Security.AccessControl.FileSystemRights]::FullControl,
                [System.Security.AccessControl.AccessControlType]::Allow))
        }
        # Persist only the modified DACL; Set-Acl can request audit privileges
        # that an ordinary Windows user does not possess.
        [System.IO.FileSystemAclExtensions]::SetAccessControl([System.IO.FileInfo]::new($path), $fileAcl)
    }
}
foreach ($path in @($keyPath, "$keyPath.pub")) {
    Assert-PlainPath $path
    Assert-PrivateAcl $path
    if ((Get-Item -LiteralPath $path).Length -le 0) { throw 'Empty key output.' }
}

Write-Output "Staging key permissions verified in $materialRoot. No private-key contents were read or displayed."
& $keygen -lf "$keyPath.pub"
if ($LASTEXITCODE -ne 0) { throw 'Public-key fingerprint check failed.' }
