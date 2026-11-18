# Dumps one elevated snapshot of every sensor (no personal data) to D:\Proyectos\monitor\sensors-admin.jsonl
# Run from an ELEVATED PowerShell:
#   powershell -ExecutionPolicy Bypass -File D:\Proyectos\monitor\tools\dump_sensors.ps1
$out = Join-Path (Split-Path -Parent $PSScriptRoot) 'sensors-admin.jsonl'
$exe = 'C:\Program Files\HardwareMonitorWidget\_internal\hardware_monitor_widget\native\sensorhost\SensorHost.exe'
Push-Location (Split-Path $exe)
& $exe --once | Set-Content -Encoding utf8 $out
Pop-Location
Write-Host "Saved $out"
