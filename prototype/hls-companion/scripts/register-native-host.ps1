param(
  [Parameter(Mandatory = $true)]
  [ValidatePattern('^[a-p]{32}$')]
  [string]$ExtensionId,
  [switch]$Unregister
)

$ErrorActionPreference = 'Stop'
$HostName = 'com.lingerlens.hls_companion'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Launcher = (Resolve-Path (Join-Path $PSScriptRoot 'native-host.cmd')).Path
$ManifestPath = Join-Path $PSScriptRoot 'com.lingerlens.hls_companion.json'
$RegistryPaths = @(
  "HKCU:\Software\Google\Chrome\NativeMessagingHosts\$HostName",
  "HKCU:\Software\Microsoft\Edge\NativeMessagingHosts\$HostName"
)

if ($Unregister) {
  foreach ($Path in $RegistryPaths) { Remove-Item $Path -Recurse -Force -ErrorAction SilentlyContinue }
  Remove-Item $ManifestPath -Force -ErrorAction SilentlyContinue
  Write-Host "Unregistered $HostName for Chrome and Edge."
  exit 0
}

$Manifest = @{
  name = $HostName
  description = 'LingerLens Prototype 2 local cookie bridge'
  path = $Launcher
  type = 'stdio'
  allowed_origins = @("chrome-extension://$ExtensionId/")
} | ConvertTo-Json -Depth 4

[System.IO.File]::WriteAllText($ManifestPath, $Manifest, [System.Text.UTF8Encoding]::new($false))
foreach ($Path in $RegistryPaths) {
  New-Item $Path -Force | Out-Null
  Set-Item $Path $ManifestPath
}

Write-Host "Registered $HostName for Chrome and Edge."
Write-Host "Manifest: $ManifestPath"
Write-Host "Extension: chrome-extension://$ExtensionId/"
