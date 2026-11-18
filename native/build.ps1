# Builds the SensorHost helper.
#   default:            framework-dependent publish next to the Python package (development).
#   -SelfContained:     win-x64 self-contained publish (no .NET runtime needed on the target machine)
#                       into -OutDir (used by packaging\build.ps1).
param(
    [switch]$SelfContained,
    [string]$OutDir
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if ($SelfContained) {
    if (-not $OutDir) { throw "-OutDir is required with -SelfContained" }
    $out = $OutDir
    $sc = "true"
} else {
    $out = if ($OutDir) { $OutDir } else { Join-Path $root "hardware_monitor_widget\native\sensorhost" }
    $sc = "false"
}
dotnet publish (Join-Path $PSScriptRoot "SensorHost") -c Release -r win-x64 --self-contained $sc -o $out
if ($LASTEXITCODE -ne 0) { throw "dotnet publish failed" }
Write-Host "Published to $out (self-contained=$sc)"
