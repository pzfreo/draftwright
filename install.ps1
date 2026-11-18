#requires -Version 5.1
[CmdletBinding()]
param([switch]$Help)

# Keep preferences and helper functions local when this script is piped to iex.
& {
    param([bool]$ShowHelp)
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'
    $releases = 'https://github.com/pzfreo/draftwright/releases'

    if ($ShowHelp) {
        Write-Output @'
Install draftwright and draftwright-mcp on Windows x64 (ARM64 uses x64 emulation).
Usage: .\install.ps1 [-Help]
  DRAFTWRIGHT_VERSION      Release, e.g. v0.6.0 or 0.6.0 (default: latest stable)
  DRAFTWRIGHT_INSTALL_DIR  Destination (default: LOCALAPPDATA\Programs\draftwright\bin)
The destination is added to the USER PATH; no administrator access is needed.
'@
        return
    }
    if ($env:OS -ne 'Windows_NT') {
        throw 'Use install.sh on macOS or Linux.'
    }
    # An x64 PowerShell session under ARM64 emulation can report AMD64 in its environment.
    try {
        $arch = Get-ItemPropertyValue -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment' -Name PROCESSOR_ARCHITECTURE -ErrorAction Stop
    } catch {
        $arch = $null
    }
    if (-not $arch) { $arch = $env:PROCESSOR_ARCHITEW6432 }
    if (-not $arch) { $arch = $env:PROCESSOR_ARCHITECTURE }
    switch ($arch) {
        'AMD64' { }
        'ARM64' { Write-Output 'Windows ARM64: installing the x64 binary for Windows emulation.' }
        default { throw "Unsupported Windows architecture: $arch. See $releases or use uv tool install draftwright / cargo install draftwright." }
    }

    function Test-PathEntry {
        param([string]$PathValue, [string]$Directory)
        foreach ($entry in ($PathValue -split ';')) {
            $expanded = [Environment]::ExpandEnvironmentVariables($entry.Trim().Trim('"'))
            if ($expanded.TrimEnd('\') -ieq $Directory.TrimEnd('\')) { return $true }
        }
        return $false
    }

    function Add-UserPathEntry {
        param($Key, [string]$Directory)
        # Preserve expandable references and the original REG_SZ / REG_EXPAND_SZ kind.
        $rawPath = [string]$Key.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        $kind = [Microsoft.Win32.RegistryValueKind]::ExpandString
        if ($Key.GetValueNames() -contains 'Path') { $kind = $Key.GetValueKind('Path') }
        if ($kind -ne [Microsoft.Win32.RegistryValueKind]::String -and
            $kind -ne [Microsoft.Win32.RegistryValueKind]::ExpandString) { throw 'USER PATH is not a string registry value.' }
        if (Test-PathEntry $rawPath $Directory) { return $false }
        $newPath = if ($rawPath) { "$($rawPath.TrimEnd(';'));$Directory" } else { $Directory }
        $Key.SetValue('Path', $newPath, $kind)
        return $true
    }

    function Add-InstallerType {
        param([string]$Source, [string]$TemporaryDirectory)
        $compilerFiles = $null
        $typeOptions = @{}
        try {
            # Windows PowerShell uses CodeDOM. Its default temp collection can
            # leave a separate directory until finalization, even for in-memory
            # assemblies. Own it explicitly and keep its files inside our temp dir.
            if ((Get-Command Add-Type).Parameters.ContainsKey('CompilerParameters')) {
                $compilerFiles = New-Object System.CodeDom.Compiler.TempFileCollection $TemporaryDirectory, $false
                $parameters = New-Object System.CodeDom.Compiler.CompilerParameters
                $parameters.GenerateInMemory = $true
                $parameters.CompilerOptions = '/optimize+'
                $parameters.TreatWarningsAsErrors = $true
                $parameters.TempFiles = $compilerFiles
                $null = $parameters.ReferencedAssemblies.Add('System.dll')
                $typeOptions.CompilerParameters = $parameters
            }
            Add-Type -TypeDefinition $Source @typeOptions
        } finally {
            if ($null -ne $compilerFiles) { ([IDisposable]$compilerFiles).Dispose() }
        }
    }

    function Initialize-WindowsInterop {
        if ('DraftwrightInstaller.NativeWindows' -as [type]) { return }
        Add-InstallerType -TemporaryDirectory $tempDir -Source @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
namespace DraftwrightInstaller {
    public static class NativeWindows {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFile(string name, uint access, uint share,
            IntPtr security, uint creation, uint flags, IntPtr template);
        [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr SendMessageTimeout(IntPtr window, uint message,
            UIntPtr wParam, string lParam, uint flags, uint timeout, out UIntPtr result);
        public static SafeFileHandle OpenForReplacement(string path) {
            // Request READ, WRITE and DELETE, allowing sharing of all three.
            // This checks delete-sharing locks as well as running executable images.
            SafeFileHandle handle = CreateFile(path, 0xc0010000, 7, IntPtr.Zero, 3, 0x80, IntPtr.Zero);
            if (handle.IsInvalid) {
                int error = Marshal.GetLastWin32Error();
                handle.Dispose();
                throw new Win32Exception(error);
            }
            return handle;
        }
        public static void BroadcastEnvironmentChange() {
            UIntPtr result;
            SendMessageTimeout(new IntPtr(0xffff), 0x1a, UIntPtr.Zero, "Environment", 2, 5000, out result);
        }
    }
}
'@
    }

    function Move-InstallerFile {
        param([string]$Source, [string]$Destination, [string]$Backup)
        if ([IO.File]::Exists($Destination)) {
            if ($Backup) {
                [IO.File]::Replace($Source, $Destination, $Backup)
            } else {
                # PowerShell otherwise coerces $null to an empty backup filename.
                [IO.File]::Replace($Source, $Destination, [NullString]::Value)
            }
        } else {
            [IO.File]::Move($Source, $Destination)
        }
    }

    function Install-BinaryPair {
        param([string]$Payload, [string]$Directory, $Handles = @())
        # Keep backups on the same volume, separate from disposable extraction
        # staging so a failed rollback can retain its recovery copy.
        $backupDir = Join-Path $Directory ('.draftwright-backup-' + [guid]::NewGuid().ToString('N'))
        $cli = Join-Path $Directory 'draftwright.exe'
        $cliBackup = Join-Path $backupDir 'draftwright.exe'
        $cliInstalled = $false
        $keepBackup = $false
        try {
            # A replaced file becomes the backup while its probe handle stays
            # open. Windows ReplaceFile opens the rollback source without sharing,
            # so even a FILE_SHARE_DELETE probe would block restoring that backup.
            # Both destinations have been checked; release probes before any swap.
            foreach ($handle in $Handles) { $handle.Dispose() }
            $null = [IO.Directory]::CreateDirectory($backupDir)
            foreach ($binary in @('draftwright.exe', 'draftwright-mcp.exe')) {
                Move-InstallerFile -Source (Join-Path $Payload $binary) -Destination (Join-Path $Directory $binary) -Backup (Join-Path $backupDir $binary)
                if ($binary -eq 'draftwright.exe') { $cliInstalled = $true }
            }
        } catch {
            $failure = $_.Exception.Message
            if ($cliInstalled) {
                try {
                    if ([IO.File]::Exists($cliBackup)) {
                        Move-InstallerFile -Source $cliBackup -Destination $cli
                    } else {
                        [IO.File]::Delete($cli)
                    }
                } catch {
                    $keepBackup = $true
                    throw "Cannot restore draftwright.exe; recovery copies retained in $backupDir. Close any MCP client using draftwright-mcp. $($_.Exception.Message)"
                }
            }
            throw "Cannot replace $binary; close any MCP client using draftwright-mcp. $failure"
        } finally {
            foreach ($handle in $Handles) { $handle.Dispose() }
            if (-not $keepBackup -and (Test-Path -LiteralPath $backupDir)) {
                Remove-Item -LiteralPath $backupDir -Recurse -Force
            }
        }
    }

    $oldProtocol = [Net.ServicePointManager]::SecurityProtocol
    $tempDir = $null
    $stageDir = $null
    $replaceHandles = @()
    try {
        [Net.ServicePointManager]::SecurityProtocol = $oldProtocol -bor [Net.SecurityProtocolType]::Tls12
        $version = $env:DRAFTWRIGHT_VERSION
        if (-not $version) {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "$releases/latest" -MaximumRedirection 10
            if ($PSVersionTable.PSVersion.Major -ge 6) {
                $latestUri = $response.BaseResponse.RequestMessage.RequestUri.AbsoluteUri
            } else {
                $latestUri = $response.BaseResponse.ResponseUri.AbsoluteUri
            }
            if (-not $latestUri.StartsWith("$releases/tag/", [StringComparison]::Ordinal)) {
                throw "Could not read a release tag from $latestUri"
            }
            $version = $latestUri.Substring("$releases/tag/".Length)
        }
        if (-not $version.StartsWith('v', [StringComparison]::Ordinal)) { $version = "v$version" }
        if ($version -cnotmatch '\Av[0-9][a-zA-Z0-9.+-]*\z') { throw "Invalid release version: $version" }

        $installDir = $env:DRAFTWRIGHT_INSTALL_DIR
        if (-not $installDir) {
            $installDir = Join-Path $env:LOCALAPPDATA 'Programs\draftwright\bin'
        }
        $installDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($installDir)
        $tempDir = Join-Path ([IO.Path]::GetTempPath()) ('draftwright-install-' + [guid]::NewGuid().ToString('N'))
        $null = [IO.Directory]::CreateDirectory($tempDir)
        $name = "draftwright-$version-x86_64-pc-windows-msvc"
        $asset = "$name.zip"
        $archive = Join-Path $tempDir $asset
        $checksumFile = "$archive.sha256"
        Write-Output "Installing draftwright $version for x86_64-pc-windows-msvc"
        Invoke-WebRequest -UseBasicParsing -Uri "$releases/download/$version/$asset" -OutFile $archive
        Invoke-WebRequest -UseBasicParsing -Uri "$releases/download/$version/$asset.sha256" -OutFile $checksumFile

        # sha256sum uses a '*' binary-mode marker on Windows, a space in text mode.
        $pattern = '\A([a-fA-F0-9]{64})[ \t]+\*?' + [regex]::Escape($asset) + '\r?\n?\z'
        $manifest = [regex]::Match([IO.File]::ReadAllText($checksumFile), $pattern)
        if (-not $manifest.Success) { throw "Invalid checksum file for $asset" }
        $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
        if ($actual -ine $manifest.Groups[1].Value) { throw "Checksum mismatch for $asset; nothing installed." }

        $null = [IO.Directory]::CreateDirectory($installDir)
        # Stage on the destination volume so File.Replace / File.Move are atomic.
        $stageDir = Join-Path $installDir ('.draftwright-install-' + [guid]::NewGuid().ToString('N'))
        Expand-Archive -LiteralPath $archive -DestinationPath $stageDir
        $payload = Join-Path $stageDir $name
        foreach ($binary in @('draftwright.exe', 'draftwright-mcp.exe')) {
            $source = Join-Path $payload $binary
            if (-not [IO.File]::Exists($source) -or
                ((Get-Item -LiteralPath $source).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw "Archive is missing a regular file: $binary"
            }
            $destination = Join-Path $installDir $binary
            if ([IO.Directory]::Exists($destination)) {
                throw "Destination is a directory: $binary"
            }
            if ((Test-Path -LiteralPath $destination) -and
                ((Get-Item -LiteralPath $destination -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw "Destination is a link: $binary"
            }
        }
        foreach ($binary in @('draftwright.exe', 'draftwright-mcp.exe')) {
            $destination = Join-Path $installDir $binary
            if ([IO.File]::Exists($destination) -and
                ((Get-Item -LiteralPath $destination -Force).Attributes -band [IO.FileAttributes]::ReadOnly)) {
                throw "Cannot replace $binary; close any MCP client using draftwright-mcp and check file permissions."
            }
        }
        $interopAvailable = $true
        try { Initialize-WindowsInterop } catch {
            $interopAvailable = $false
            Write-Output "Native compilation unavailable; relying on file replacement checks. $($_.Exception.Message)"
        }
        if ($interopAvailable) {
            foreach ($binary in @('draftwright.exe', 'draftwright-mcp.exe')) {
                $destination = Join-Path $installDir $binary
                if ([IO.File]::Exists($destination)) {
                    try { $replaceHandles += [DraftwrightInstaller.NativeWindows]::OpenForReplacement($destination) }
                    catch { throw "Cannot replace $binary; close any MCP client using draftwright-mcp. $($_.Exception.Message)" }
                }
            }
        }
        # Probe both destinations before any changes. The pair installer releases
        # the probes before replacement so they cannot block rollback. File.Replace
        # detects locks acquired after preflight and the pair rolls back on failure.
        Install-BinaryPair -Payload $payload -Directory $installDir -Handles $replaceHandles
        $replaceHandles = @()
        & (Join-Path $installDir 'draftwright.exe') --version
        if ($LASTEXITCODE -ne 0) { throw 'Installed draftwright --version failed.' }

        $userKey = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment')
        try { $pathChanged = Add-UserPathEntry $userKey $installDir } finally { $userKey.Dispose() }
        if ($pathChanged) {
            if ($interopAvailable) {
                try { [DraftwrightInstaller.NativeWindows]::BroadcastEnvironmentChange() }
                catch { Write-Output 'Could not broadcast the PATH change; open a new terminal.' }
            }
            Write-Output "Added $installDir to the USER PATH. Open a new terminal to use it there."
        }
        if (-not (Test-PathEntry $env:Path $installDir)) { $env:Path = "$env:Path;$installDir" }
        Write-Output "Installed draftwright and draftwright-mcp to $installDir"
        Write-Output 'PowerShell completions (add this line to $PROFILE to persist):'
        Write-Output 'draftwright completions powershell | Out-String | Invoke-Expression'
    } finally {
        try {
            foreach ($handle in $replaceHandles) { $handle.Dispose() }
        } finally {
            try {
                if ($stageDir -and (Test-Path -LiteralPath $stageDir)) { Remove-Item -LiteralPath $stageDir -Recurse -Force }
            } finally {
                try {
                    if ($tempDir -and (Test-Path -LiteralPath $tempDir)) { Remove-Item -LiteralPath $tempDir -Recurse -Force }
                } finally {
                    [Net.ServicePointManager]::SecurityProtocol = $oldProtocol
                }
            }
        }
    }
} ([bool]$Help)
