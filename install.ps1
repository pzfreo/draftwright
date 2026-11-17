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
    $arch = $env:PROCESSOR_ARCHITEW6432
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

    $oldProtocol = [Net.ServicePointManager]::SecurityProtocol
    $tempDir = $null
    $stageDir = $null
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
            $source = Join-Path $payload $binary
            $destination = Join-Path $installDir $binary
            if ([IO.File]::Exists($destination)) {
                # PowerShell coerces $null to an empty string for this .NET overload.
                [IO.File]::Replace($source, $destination, [NullString]::Value)
            } else {
                [IO.File]::Move($source, $destination)
            }
        }
        & (Join-Path $installDir 'draftwright.exe') --version
        if ($LASTEXITCODE -ne 0) { throw 'Installed draftwright --version failed.' }

        $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        if (-not (Test-PathEntry $userPath $installDir)) {
            $newPath = if ($userPath) { "$($userPath.TrimEnd(';'));$installDir" } else { $installDir }
            [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
            Write-Output "Added $installDir to the USER PATH. Open a new terminal to use it there."
        }
        if (-not (Test-PathEntry $env:Path $installDir)) { $env:Path = "$env:Path;$installDir" }
        Write-Output "Installed draftwright and draftwright-mcp to $installDir"
        Write-Output 'PowerShell completions (add this line to $PROFILE to persist):'
        Write-Output 'draftwright completions powershell | Out-String | Invoke-Expression'
    } finally {
        if ($stageDir -and (Test-Path -LiteralPath $stageDir)) { Remove-Item -LiteralPath $stageDir -Recurse -Force }
        if ($tempDir -and (Test-Path -LiteralPath $tempDir)) { Remove-Item -LiteralPath $tempDir -Recurse -Force }
        [Net.ServicePointManager]::SecurityProtocol = $oldProtocol
    }
} ([bool]$Help)
