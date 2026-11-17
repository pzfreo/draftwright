#requires -Version 5.1
# Offline failure tests: wrap web requests, with no test hooks in the installer.
$ErrorActionPreference = 'Stop'
$installer = Join-Path (Split-Path $PSScriptRoot -Parent) 'install.ps1'
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('draftwright-test-' + [guid]::NewGuid().ToString('N'))
$null = [IO.Directory]::CreateDirectory($testRoot)
$destination = Join-Path $testRoot 'bin with spaces'
$null = [IO.Directory]::CreateDirectory($destination)
$saved = @{}
foreach ($key in @('OS', 'PROCESSOR_ARCHITECTURE', 'PROCESSOR_ARCHITEW6432', 'DRAFTWRIGHT_VERSION', 'DRAFTWRIGHT_INSTALL_DIR')) {
    $saved[$key] = [Environment]::GetEnvironmentVariable($key)
}
$originalUserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$name = 'draftwright-v0.6.0-x86_64-pc-windows-msvc'
$asset = "$name.zip"
$payload = Join-Path $testRoot $name
$null = [IO.Directory]::CreateDirectory($payload)
[IO.File]::WriteAllText((Join-Path $payload 'draftwright.exe'), 'not executed')
$fixture = Join-Path $testRoot $asset
Compress-Archive -Path $payload -DestinationPath $fixture
$testState = @{ ManifestMode = 'mismatch'; Requests = @(); Output = @() }

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
        $hash = (Get-FileHash -LiteralPath $fixture -Algorithm SHA256).Hash
        $filename = $asset
        if ($testState.ManifestMode -eq 'mismatch') { $hash = '0' * 64 }
        if ($testState.ManifestMode -eq 'filename') { $filename = 'other.zip' }
        [IO.File]::WriteAllText($OutFile, "$hash  $filename`n")
    } else {
        Copy-Item -LiteralPath $fixture -Destination $OutFile
    }
}

function Test-Refusal {
    param([string]$Expected)
    $testState.Requests = @()
    $testState.Output = @()
    $failure = $null
    foreach ($binary in @('draftwright.exe', 'draftwright-mcp.exe')) {
        [IO.File]::WriteAllText((Join-Path $destination $binary), 'previous installation')
    }
    try { & $installer | ForEach-Object { $testState.Output += "$_" } } catch { $failure = $_.Exception.Message }
    if (-not $failure -or -not $failure.Contains($Expected)) { throw "Expected '$Expected', got '$failure'" }
    foreach ($binary in @('draftwright.exe', 'draftwright-mcp.exe')) {
        if ([IO.File]::ReadAllText((Join-Path $destination $binary)) -ne 'previous installation') {
            throw "Refusal changed $binary"
        }
    }
    if (@(Get-ChildItem -LiteralPath $destination -Force).Count -ne 2) { throw 'Staging was not cleaned' }
    if ([Environment]::GetEnvironmentVariable('Path', 'User') -cne $originalUserPath) { throw 'Refusal changed USER PATH' }
    Write-Output "PASS: $Expected; existing binaries and USER PATH preserved"
}

try {
    $env:OS = 'Windows_NT'
    $env:PROCESSOR_ARCHITECTURE = 'AMD64'
    $env:PROCESSOR_ARCHITEW6432 = ''
    $env:DRAFTWRIGHT_VERSION = '0.6.0'
    $env:DRAFTWRIGHT_INSTALL_DIR = $destination
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
    $testState.ManifestMode = 'mismatch'
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
    Write-Output 'PASS: all 7 Windows refusal tests and help'
} finally {
    foreach ($key in $saved.Keys) { [Environment]::SetEnvironmentVariable($key, $saved[$key]) }
    Remove-Item -LiteralPath $testRoot -Recurse -Force
}
