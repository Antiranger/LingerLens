[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765,

    [string]$Python = "python",

    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$serverRelative = "prototype/hls-companion/companion/server.py"
$serverPath = (Join-Path $projectRoot $serverRelative)

function Get-ListeningProcessIds {
    param([int]$LocalPort)

    return @(
        Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
}

function Test-IsLagLingoCompanion {
    param($Process)

    if ($null -eq $Process -or $Process.Name -notmatch '^python(?:\.exe)?$') {
        return $false
    }

    $commandLine = [string]$Process.CommandLine
    return (
        $commandLine -match 'prototype[\\/]hls-companion[\\/]companion[\\/]server\.py' -or
        $commandLine -match 'hls-companion[\\/]companion[\\/]server\.py'
    )
}

$listenerIds = Get-ListeningProcessIds -LocalPort $Port
$listenerProcesses = @()
foreach ($processId in $listenerIds) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId"
    if (-not (Test-IsLagLingoCompanion -Process $process)) {
        $description = if ($null -eq $process) {
            "PID $processId"
        } else {
            "PID $processId ($($process.Name)): $($process.CommandLine)"
        }
        throw "Port $Port is occupied by a non-LagLingo process. Refusing to terminate it: $description"
    }
    $listenerProcesses += $process
}

if ($CheckOnly) {
    if ($listenerProcesses.Count -eq 0) {
        Write-Host "[LagLingo] Port $Port is free. A new Companion can start safely."
    } else {
        foreach ($process in $listenerProcesses) {
            Write-Host "[LagLingo] Would replace old Companion PID $($process.ProcessId) on port $Port."
        }
    }
    exit 0
}

foreach ($process in $listenerProcesses) {
    Write-Host "[LagLingo] Stopping old Companion process PID $($process.ProcessId)..."
    Stop-Process -Id $process.ProcessId -Force
}

if ($listenerIds.Count -gt 0) {
    $deadline = (Get-Date).AddSeconds(5)
    do {
        Start-Sleep -Milliseconds 100
        $remaining = Get-ListeningProcessIds -LocalPort $Port
    } while ($remaining.Count -gt 0 -and (Get-Date) -lt $deadline)

    if ($remaining.Count -gt 0) {
        throw "Port $Port did not become available after stopping the old Companion."
    }
}

if (-not (Test-Path $serverPath -PathType Leaf)) {
    throw "Companion server not found: $serverPath"
}

Write-Host "[LagLingo] Starting a fresh Companion on port $Port..."
Set-Location $projectRoot
& $Python $serverRelative --port $Port
exit $LASTEXITCODE
