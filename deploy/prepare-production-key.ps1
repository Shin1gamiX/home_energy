# Run only after approval to generate a dedicated production reporting credential.
# No server connection, model run, schedule change or private-key read occurs.
# Failed/partial output is retained for review; existing paths are never reused.
#requires -Version 7.0
[CmdletBinding()]
param([switch] $VerifyOnly)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$PSNativeCommandArgumentPassing = 'Standard'

function Initialize-ProductionCredentialMetadata {
    if ('HomeEnergyProductionCredentialMetadata' -as [type]) { return }
    # Metadata-only handles: no READ_DATA access and no private bytes are read.
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class HomeEnergyProductionCredentialMetadata {
    [StructLayout(LayoutKind.Sequential)]
    private struct SecurityAttributes {
        public int Length;
        public IntPtr Descriptor;
        public int InheritHandle;
    }
    [StructLayout(LayoutKind.Sequential)]
    private struct FileInformation {
        public uint Attributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CreateDirectoryW(string path, ref SecurityAttributes security);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFileW(string path, uint access, uint share,
        IntPtr security, uint disposition, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileInformationByHandle(SafeFileHandle file, out FileInformation info);

    public static void CreatePrivateDirectory(string path, byte[] descriptor) {
        GCHandle pinned = GCHandle.Alloc(descriptor, GCHandleType.Pinned);
        try {
            var security = new SecurityAttributes {
                Length = Marshal.SizeOf(typeof(SecurityAttributes)),
                Descriptor = pinned.AddrOfPinnedObject(), InheritHandle = 0
            };
            // Atomic creation with the private DACL. An existing path is an error,
            // even if another process creates it after the caller's preflight.
            if (!CreateDirectoryW(path, ref security))
                throw new Win32Exception(Marshal.GetLastWin32Error());
        } finally { pinned.Free(); }
    }

    public static void VerifyKeyFile(string path) {
        // FILE_READ_ATTRIBUTES, FILE_SHARE_READ, OPEN_EXISTING,
        // FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS.
        using (var file = CreateFileW(path, 0x80, 0x1, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero)) {
            if (file.IsInvalid) throw new Win32Exception(Marshal.GetLastWin32Error());
            FileInformation info;
            if (!GetFileInformationByHandle(file, out info))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            if ((info.Attributes & (0x400U | 0x10U)) != 0 || info.NumberOfLinks != 1 ||
                info.FileSizeHigh != 0 || info.FileSizeLow == 0 || info.FileSizeLow > 4096)
                throw new InvalidOperationException("Key must be a small, plain, single-link file.");
        }
    }
}
'@
}

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
    if ($acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $script:userSid.Value -or
        -not $acl.AreAccessRulesProtected) {
        throw 'Unexpected credential owner or inherited permissions.'
    }
    $rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
    if ($rules.Count -ne 3) { throw 'Unexpected credential access rules.' }
    $seen = @()
    foreach ($rule in $rules) {
        if ($rule.IdentityReference.Value -notin $script:allowedSids -or $rule.IsInherited -or
            $rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow -or
            $rule.FileSystemRights -ne [System.Security.AccessControl.FileSystemRights]::FullControl -or
            $rule.PropagationFlags -ne [System.Security.AccessControl.PropagationFlags]::None) {
            throw 'Unexpected credential access rule.'
        }
        $expectedInheritance = if ((Get-Item -LiteralPath $Path -Force) -is [System.IO.DirectoryInfo]) {
            [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
        } else { [System.Security.AccessControl.InheritanceFlags]::None }
        if ($rule.InheritanceFlags -ne $expectedInheritance) { throw 'Unexpected access-rule inheritance.' }
        $seen += $rule.IdentityReference.Value
    }
    if (@($seen | Select-Object -Unique).Count -ne 3) { throw 'Duplicate credential access rule.' }
}

function New-PrivateCredentialDirectory([string] $Path) {
    $acl = [System.Security.AccessControl.DirectorySecurity]::new()
    $acl.SetOwner($script:userSid)
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in $script:allowedSids) {
        $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
            [System.Security.Principal.SecurityIdentifier]::new($sid),
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit',
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow))
    }
    [HomeEnergyProductionCredentialMetadata]::CreatePrivateDirectory($Path, $acl.GetSecurityDescriptorBinaryForm())
}

function Assert-KeyFile([string] $Path) {
    Assert-PlainPath $Path
    [HomeEnergyProductionCredentialMetadata]::VerifyKeyFile($Path)
}

function Protect-GeneratedKey([string] $Path) {
    # Reject links before modifying any ACL, including on the public key.
    Assert-KeyFile $Path
    $acl = Get-Acl -LiteralPath $Path
    if ($acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $script:userSid.Value) {
        throw 'Unexpected generated-key owner.'
    }
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($rule in @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))) {
        [void] $acl.RemoveAccessRuleSpecific($rule)
    }
    foreach ($sid in $script:allowedSids) {
        $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
            [System.Security.Principal.SecurityIdentifier]::new($sid),
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            [System.Security.AccessControl.AccessControlType]::Allow))
    }
    [System.IO.FileSystemAclExtensions]::SetAccessControl([System.IO.FileInfo]::new($Path), $acl)
}

function Invoke-ProductionKeyGeneration([string] $Path) {
    # Empty passphrase is required for unattended use. The private DACL and
    # server-side forced command are the protections, not key-file encryption.
    # Any unexpected overwrite prompt receives "no", never interactive consent.
    $generationOutput = 'n' | & $script:keygen -q -t ed25519 -N '' -C 'homeenergy-production-only' -f $Path 2>&1
    if ($LASTEXITCODE -ne 0) { throw 'Key generation failed; retain the directory for administrator review.' }
    $generationOutput = $null
}

function Get-ProductionKeyFingerprint([string] $PublicPath) {
    $fingerprintOutput = @(& $script:keygen -lf $PublicPath 2>&1)
    if ($LASTEXITCODE -ne 0 -or $fingerprintOutput.Count -ne 1 -or
        $fingerprintOutput[0] -notmatch '^256 (SHA256:[A-Za-z0-9+/]{43}) homeenergy-production-only \(ED25519\)$') {
        throw 'Production public-key fingerprint verification failed.'
    }
    return $Matches[1]
}

function Invoke-ProductionCredentialPreparation([switch] $VerifyOnly) {
    if (-not $IsWindows) { throw 'Windows is required.' }
    $script:materialRoot = 'C:\ProgramData\HomeEnergyReporterProduction'
    $script:keyPath = Join-Path $script:materialRoot 'id_ed25519'
    $script:keygen = 'C:\Windows\System32\OpenSSH\ssh-keygen.exe'
    $script:userSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $script:allowedSids = @($script:userSid.Value, 'S-1-5-18', 'S-1-5-32-544')
    Initialize-ProductionCredentialMetadata
    Assert-PlainPath 'C:\ProgramData'
    Assert-PlainPath $script:keygen
    if (-not $VerifyOnly) {
        if (Test-Path -LiteralPath $script:materialRoot) {
            throw 'Production credential directory already exists; use -VerifyOnly. Never overwrite it.'
        }
        New-PrivateCredentialDirectory $script:materialRoot
    }
    Assert-PlainPath $script:materialRoot
    Assert-PrivateAcl $script:materialRoot
    if (-not $VerifyOnly) {
        if (@(Get-ChildItem -LiteralPath $script:materialRoot -Force).Count -ne 0) {
            throw 'New credential directory is not empty; retain it for administrator review.'
        }
        Invoke-ProductionKeyGeneration $script:keyPath
        Protect-GeneratedKey $script:keyPath
        Protect-GeneratedKey "$script:keyPath.pub"
    }
    foreach ($path in @($script:keyPath, "$script:keyPath.pub")) {
        Assert-KeyFile $path
        Assert-PrivateAcl $path
    }
    $fingerprint = Get-ProductionKeyFingerprint "$script:keyPath.pub"
    Write-Output "Production credential permissions and single-link metadata verified in $script:materialRoot."
    Write-Output "Public-key fingerprint: $fingerprint"
    Write-Output 'No private bytes were read or displayed. No server, model, schedule or publishing changes.'
}

Invoke-ProductionCredentialPreparation -VerifyOnly:$VerifyOnly
