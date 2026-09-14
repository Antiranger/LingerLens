[CmdletBinding()]
param(
    [string]$Python = "python",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$failures = New-Object System.Collections.Generic.List[string]

function Add-Failure([string]$Message) {
    $failures.Add($Message)
    Write-Host "[missing] $Message" -ForegroundColor Red
}

function Require-Command([string]$Name, [string]$InstallUrl) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        Add-Failure "$Name was not found. Install it from $InstallUrl and reopen the terminal."
        return $false
    }
    Write-Host "[ok] ${Name}: $($command.Source)"
    return $true
}

Set-Location $root
Write-Host "LagLingo Windows bootstrap"

if (Require-Command $Python "https://www.python.org/downloads/windows/") {
    $versionText = & $Python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
    $parts = $versionText.Trim().Split('.')
    if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 11)) {
        Add-Failure "Python 3.11+ is required; found $versionText. Install from https://www.python.org/downloads/windows/."
    } else {
        Write-Host "[ok] Python $versionText"
    }
}

if (Require-Command "node" "https://nodejs.org/en/download/") {
    $nodeMajor = [int]((& node -p "process.versions.node.split('.')[0]").Trim())
    $nodeMinor = [int]((& node -p "process.versions.node.split('.')[1]").Trim())
    if ($nodeMajor -lt 22 -or ($nodeMajor -eq 22 -and $nodeMinor -lt 12)) { Add-Failure "Node.js 22.12+ is required; found $(& node --version)." }
    else { Write-Host "[ok] Node.js $(& node --version)" }
}
$npmAvailable = Require-Command "npm" "https://nodejs.org/en/download/"
$ffmpegAvailable = Require-Command "ffmpeg" "https://ffmpeg.org/download.html#build-windows"
$ffprobeAvailable = Require-Command "ffprobe" "https://ffmpeg.org/download.html#build-windows"

$browserPaths = @(
    (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
    (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe"),
    (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe")
) | Where-Object { $_ -and (Test-Path $_ -PathType Leaf) }
if ($browserPaths.Count -eq 0 -and -not (Get-Command chrome.exe, msedge.exe -ErrorAction SilentlyContinue)) {
    Add-Failure "Chrome or Edge was not found. Install Chrome (https://www.google.com/chrome/) or Edge (https://www.microsoft.com/edge/download)."
} else {
    Write-Host "[ok] Chrome/Edge detected"
}

$ytDlp = Join-Path $root "prototype\hls-companion\vendor\yt-dlp\yt-dlp.exe"
$checksumFile = "$ytDlp.sha256"
if (-not (Test-Path $ytDlp -PathType Leaf) -or -not (Test-Path $checksumFile -PathType Leaf)) {
    Add-Failure "Vendored yt-dlp or its checksum file is missing."
} else {
    $expected = ((Get-Content $checksumFile -Raw).Trim().Split()[0]).ToLowerInvariant()
    $actual = (Get-FileHash $ytDlp -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { Add-Failure "Vendored yt-dlp checksum mismatch. Restore the tracked binary before continuing." }
    else { Write-Host "[ok] vendored yt-dlp SHA-256 verified ($(& $ytDlp --version))" }
}

if ($failures.Count -gt 0) {
    Write-Host "Bootstrap stopped: $($failures.Count) required dependency check(s) failed." -ForegroundColor Red
    exit 2
}

if ($CheckOnly) {
    Write-Host "All LagLingo dependency and integrity checks passed."
    exit 0
}

Write-Host "Installing locked Python dependencies..."
& $Python -m pip install --requirement "prototype\hls-companion\companion\requirements.txt"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($npmAvailable) {
    Write-Host "Installing locked Node dependencies..."
    & npm ci --ignore-scripts
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "Running deterministic release guard..."
& node "scripts\release-guard.js"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "LagLingo bootstrap completed. Start with .\start-laglingo.cmd"
