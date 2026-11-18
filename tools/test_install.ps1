#requires -Version 5.1
# Offline failure tests: wrap web requests, with no test hooks in the installer.
$ErrorActionPreference = 'Stop'
$nativeWindows = $env:OS -eq 'Windows_NT'
$installer = Join-Path (Split-Path $PSScriptRoot -Parent) 'install.ps1'
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('draftwright-test-' + [guid]::NewGuid().ToString('N'))
$null = [IO.Directory]::CreateDirectory($testRoot)
$destination = Join-Path $testRoot 'bin with spaces'
$null = [IO.Directory]::CreateDirectory($destination)
$downloadTemp = Join-Path $testRoot 'temp'
$null = [IO.Directory]::CreateDirectory($downloadTemp)
$saved = @{}
foreach ($key in @('OS', 'PROCESSOR_ARCHITECTURE', 'PROCESSOR_ARCHITEW6432', 'DRAFTWRIGHT_VERSION', 'DRAFTWRIGHT_INSTALL_DIR', 'TMPDIR', 'TMP', 'TEMP', 'USERPROFILE')) {
    $saved[$key] = [Environment]::GetEnvironmentVariable($key)
}
function Get-UserPathState {
    if (-not $nativeWindows) {
        return [pscustomobject]@{ Exists = $false; Value = [Environment]::GetEnvironmentVariable('Path', 'User'); Kind = $null }
    }
    $userKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment')
    try {
        if ($userKey -and ($userKey.GetValueNames() -contains 'Path')) {
            return [pscustomobject]@{
                Exists = $true
                Value = $userKey.GetValue('Path', $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
                Kind = $userKey.GetValueKind('Path')
            }
        }
        return [pscustomobject]@{ Exists = $false; Value = $null; Kind = $null }
    } finally {
        if ($userKey) { $userKey.Dispose() }
    }
}

# USERPROFILE changes below; expanded REG_EXPAND_SZ reads would change even
# without a registry write. Snapshot the raw value, presence and registry kind.
$originalUserPath = Get-UserPathState
$name = 'draftwright-v0.6.0-x86_64-pc-windows-msvc'
$asset = "$name.zip"
$payload = Join-Path $testRoot $name
$null = [IO.Directory]::CreateDirectory($payload)
[IO.File]::WriteAllText((Join-Path $payload 'draftwright.exe'), 'not executed')
$fixture = Join-Path $testRoot $asset
Compress-Archive -Path $payload -DestinationPath $fixture
[IO.File]::WriteAllText((Join-Path $payload 'draftwright-mcp.exe'), 'not executed')
$completeFixture = Join-Path $testRoot 'complete.zip'
Compress-Archive -Path $payload -DestinationPath $completeFixture
$testState = @{ ManifestMode = 'mismatch'; Marker = '*'; DestinationLink = $false; DestinationPath = $destination; NativeArchitecture = $null; Fixture = $fixture; Requests = @(); Output = @() }

if (-not $nativeWindows) {
    # Native Windows CI exercises CreateFile DELETE-sharing checks. Other hosts
    # simulate that native boundary, retaining real file streams and preservation checks.
    Add-Type -TypeDefinition @'
using System;
using System.IO;
namespace DraftwrightInstaller {
    public static class NativeWindows {
        public static string BlockedPath;
        public static IDisposable OpenForReplacement(string path) {
            if (path == BlockedPath) throw new IOException("simulated sharing violation");
            return File.Open(path, FileMode.Open, FileAccess.ReadWrite, FileShare.ReadWrite | FileShare.Delete);
        }
        public static void BroadcastEnvironmentChange() { }
    }
}
'@
}

function Get-ItemPropertyValue {
    [CmdletBinding()]
    param([string]$LiteralPath, [string]$Name)
    return $testState.NativeArchitecture
}

function Get-Item {
    param([string]$LiteralPath, [switch]$Force)
    # Simulate reparse-point metadata without requiring Windows symlink privileges.
    if ($testState.DestinationLink -and $LiteralPath -eq (Join-Path $testState.DestinationPath 'draftwright.exe')) {
        return [pscustomobject]@{ Attributes = [IO.FileAttributes]::ReparsePoint }
    }
    Microsoft.PowerShell.Management\Get-Item -LiteralPath $LiteralPath -Force:$Force
}

function Invoke-WebRequest {
    param([switch]$UseBasicParsing, [string]$Uri, [int]$MaximumRedirection, [string]$OutFile)
    $testState.Requests += $Uri
    if (-not $OutFile) {
        $latest = [uri]'https://github.com/pzfreo/draftwright/releases/tag/v0.6.0'
        return [pscustomobject]@{ BaseResponse = [pscustomobject]@{
            ResponseUri = $latest; RequestMessage = [pscustomobject]@{ RequestUri = $latest }
        } }
    }
    if ($Uri.EndsWith('.sha256')) {
        $hash = (Get-FileHash -LiteralPath $testState.Fixture -Algorithm SHA256).Hash
        $filename = $asset
        if ($testState.ManifestMode -eq 'mismatch') { $hash = '0' * 64 }
        if ($testState.ManifestMode -eq 'filename') { $filename = 'other.zip' }
        [IO.File]::WriteAllText($OutFile, "$hash $($testState.Marker)$filename`n")
    } else {
        Copy-Item -LiteralPath $testState.Fixture -Destination $OutFile
    }
}

function Test-Refusal {
    param([string]$Expected, [switch]$LockMcp)
    $testState.Requests = @()
    $testState.Output = @()
    $failure = $null
    foreach ($binary in @('draftwright.exe', 'draftwright-mcp.exe')) {
        [IO.File]::WriteAllText((Join-Path $destination $binary), 'previous installation')
    }
    $lock = $null
    if ($LockMcp) {
        # Allow read/write but deny DELETE: a write-only preflight would miss this lock.
        $lock = [IO.File]::Open((Join-Path $destination 'draftwright-mcp.exe'), 'Open', 'Read', 'ReadWrite')
        if (-not $nativeWindows) { [DraftwrightInstaller.NativeWindows]::BlockedPath = Join-Path $destination 'draftwright-mcp.exe' }
    }
    try {
        try { & $installer | ForEach-Object { $testState.Output += "$_" } } catch { $failure = $_.Exception.Message }
    } finally {
        if ($lock) { $lock.Dispose() }
        if (-not $nativeWindows) { [DraftwrightInstaller.NativeWindows]::BlockedPath = $null }
    }
    if (-not $failure -or -not $failure.Contains($Expected)) { throw "Expected '$Expected', got '$failure'" }
    foreach ($binary in @('draftwright.exe', 'draftwright-mcp.exe')) {
        if ([IO.File]::ReadAllText((Join-Path $destination $binary)) -ne 'previous installation') {
            throw "Refusal changed $binary"
        }
    }
    if (@(Get-ChildItem -LiteralPath $destination -Force).Count -ne 2) { throw 'Staging was not cleaned' }
    if (@(Get-ChildItem -LiteralPath $downloadTemp -Force).Count) { throw 'Download staging was not cleaned' }
    $currentUserPath = Get-UserPathState
    if ($currentUserPath.Exists -ne $originalUserPath.Exists -or
        $currentUserPath.Kind -ne $originalUserPath.Kind -or
        -not [string]::Equals($currentUserPath.Value, $originalUserPath.Value, [StringComparison]::Ordinal)) {
        throw 'Refusal changed USER PATH'
    }
    Write-Output "PASS: $Expected; existing binaries and USER PATH preserved"
}

try {
    $env:OS = 'Windows_NT'
    $env:PROCESSOR_ARCHITECTURE = 'AMD64'
    $env:PROCESSOR_ARCHITEW6432 = ''
    $env:DRAFTWRIGHT_VERSION = '0.6.0'
    $env:DRAFTWRIGHT_INSTALL_DIR = $destination
    $env:TMPDIR = $downloadTemp
    $env:TMP = $downloadTemp
    $env:TEMP = $downloadTemp
    $env:USERPROFILE = $testRoot
    Test-Refusal 'Checksum mismatch'
    if ($testState.Requests.Count -ne 2 -or $testState.Requests[0] -notlike '*/download/v0.6.0/*') { throw 'Unprefixed version was not normalized' }
    $env:DRAFTWRIGHT_VERSION = ''
    Test-Refusal 'Checksum mismatch'
    if ($testState.Requests.Count -ne 3 -or $testState.Requests[0] -notlike '*/releases/latest') { throw 'Latest redirect was not resolved' }
    $env:DRAFTWRIGHT_VERSION = 'v0.6.0'
    $testState.ManifestMode = 'filename'
    Test-Refusal 'Invalid checksum file'
    $testState.ManifestMode = 'valid'
    Test-Refusal 'Archive is missing a regular file: draftwright-mcp.exe'
    $testState.Marker = ' '
    Test-Refusal 'Archive is missing a regular file: draftwright-mcp.exe'
    $testState.DestinationLink = $true
    Test-Refusal 'Destination is a link: draftwright.exe'
    $testState.DestinationLink = $false
    $testState.Fixture = $completeFixture
    Test-Refusal 'close any MCP client using draftwright-mcp' -LockMcp
    $testState.Fixture = $fixture
    $testState.ManifestMode = 'mismatch'
    $testState.NativeArchitecture = 'ARM64'
    Test-Refusal 'Checksum mismatch'
    if (($testState.Output -join "`n") -notlike '*ARM64*x64*emulation*') { throw 'Emulated x64 session hid the ARM64 host' }
    $testState.NativeArchitecture = $null
    $env:PROCESSOR_ARCHITECTURE = 'ARM64'
    Test-Refusal 'Checksum mismatch'
    if (($testState.Output -join "`n") -notlike '*ARM64*x64*emulation*') { throw 'ARM64 emulation notice was not printed' }
    $env:PROCESSOR_ARCHITECTURE = 'x86'
    Test-Refusal 'Unsupported Windows architecture'
    if ($testState.Requests.Count) { throw 'Unsupported architecture downloaded files' }
    $env:PROCESSOR_ARCHITECTURE = 'AMD64'
    $env:DRAFTWRIGHT_VERSION = '../outside'
    Test-Refusal 'Invalid release version'
    if ($testState.Requests.Count) { throw 'Invalid version downloaded files' }
    & $installer -Help
    Write-Output 'PASS: all 11 Windows refusal tests and help'

    # Test the actual production helpers without executing a Windows binary.
    $tokens = $null
    $parseErrors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($installer, [ref]$tokens, [ref]$parseErrors)
    if ($parseErrors.Count) { throw 'Installer parse errors' }
    foreach ($function in $ast.FindAll({ param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -in @('Test-PathEntry', 'Add-UserPathEntry')
    }, $true)) { Invoke-Expression $function.Extent.Text }
    $key = [pscustomobject]@{ Raw = '%USERPROFILE%\Existing'; Kind = [Microsoft.Win32.RegistryValueKind]::ExpandString }
    $key | Add-Member ScriptMethod GetValue {
        param($name, $default, $options)
        if ($options -ne [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames) { throw 'Read expanded USER PATH' }
        return $this.Raw
    }
    $key | Add-Member ScriptMethod GetValueNames { return @('Path') }
    $key | Add-Member ScriptMethod GetValueKind { param($name) return $this.Kind }
    $key | Add-Member ScriptMethod SetValue { param($name, $value, $kind) $this.Raw = $value; $this.Kind = $kind }
    if (-not (Add-UserPathEntry $key $destination)) { throw 'PATH entry was not added' }
    if ($key.Raw -cne ('%USERPROFILE%\Existing;' + $destination)) { throw 'Unexpanded PATH entry changed' }
    if ($key.Kind -ne [Microsoft.Win32.RegistryValueKind]::ExpandString) { throw 'ExpandString kind changed' }
    if (Add-UserPathEntry $key $destination) { throw 'Duplicate USER PATH entry' }
    Write-Output 'PASS: unexpanded USER PATH and ExpandString kind preserved; entry deduplicated'

    if ($nativeWindows) {
        $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment')
        $hadPath = $key.GetValueNames() -contains 'Path'
        $originalRaw = $key.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        $originalKind = if ($hadPath) { $key.GetValueKind('Path') } else { [Microsoft.Win32.RegistryValueKind]::ExpandString }
        try {
            $key.SetValue('Path', '%USERPROFILE%\Existing', [Microsoft.Win32.RegistryValueKind]::ExpandString)
            if (-not (Add-UserPathEntry $key $destination)) { throw 'Real USER PATH was not updated' }
            if ($key.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames) -cne ('%USERPROFILE%\Existing;' + $destination)) { throw 'Real registry expanded USER PATH' }
            if ($key.GetValueKind('Path') -ne [Microsoft.Win32.RegistryValueKind]::ExpandString) { throw 'Real registry value kind changed' }
            Write-Output 'PASS: real HKCU Environment Path retains %USERPROFILE% and REG_EXPAND_SZ'
        } finally {
            if ($hadPath) { $key.SetValue('Path', $originalRaw, $originalKind) } else { $key.DeleteValue('Path', $false) }
            $key.Dispose()
        }
    }
} finally {
    foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $saved[$key]) }
    Remove-Item -LiteralPath $testRoot -Recurse -Force
}
