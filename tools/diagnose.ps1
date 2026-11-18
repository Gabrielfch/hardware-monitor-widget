# Diagnostics for the installed Hardware Monitor Widget. Run from an ELEVATED PowerShell:
#   powershell -ExecutionPolicy Bypass -File D:\Proyectos\monitor\tools\diagnose.ps1
# Writes D:\Proyectos\monitor\diag.txt (no personal data besides the Windows user/profile paths).
$out = Join-Path (Split-Path -Parent $PSScriptRoot) 'diag.txt'
$host_exe = 'C:\Program Files\HardwareMonitorWidget\_internal\hardware_monitor_widget\native\sensorhost\SensorHost.exe'
$lines = @()
$lines += "date: $(Get-Date -Format o)"
$lines += "whoami: $(whoami)"
$lines += "elevated: $(([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))"
$lines += "LOCALAPPDATA: $env:LOCALAPPDATA"
$lines += "--- processes"
$lines += (Get-CimInstance Win32_Process -Filter "Name='SensorHost.exe' or Name='HardwareMonitorWidget.exe'" |
    ForEach-Object { "$($_.Name) pid=$($_.ProcessId) parent=$($_.ParentProcessId) started=$($_.CreationDate) path=$($_.ExecutablePath)" })
$lines += "--- app.log (tail)"
$log = Join-Path $env:LOCALAPPDATA 'HardwareMonitorWidget\logs\app.log'
if (Test-Path $log) { $lines += Get-Content $log -Tail 40 } else { $lines += "(no log at $log)" }
$lines += "--- SensorHost timing (30 s, 1000 ms interval)"
$psi = New-Object System.Diagnostics.ProcessStartInfo $host_exe, '--interval 1000'
$psi.UseShellExecute = $false; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardInput = $true
$psi.WorkingDirectory = Split-Path $host_exe
$sw = [Diagnostics.Stopwatch]::StartNew()
$p = [Diagnostics.Process]::Start($psi)
while ($sw.Elapsed.TotalSeconds -lt 30) {
    $t = $p.StandardOutput.ReadLineAsync()
    if (-not $t.Wait(30000 - [int]$sw.Elapsed.TotalMilliseconds)) { $lines += ("{0,6:N1}s (no more output)" -f $sw.Elapsed.TotalSeconds); break }
    $l = $t.Result
    if ($null -eq $l) { $lines += "host exited code=$($p.ExitCode)"; break }
    $kind = if ($l -match '"type":"(\w+)"') { $Matches[1] } else { '?' }
    $n = ([regex]::Matches($l, '"hw":')).Count
    $extra = if ($kind -ne 'snapshot') { $l.Substring(0, [Math]::Min(200, $l.Length)) } else { "sensors=$n" }
    $lines += ("{0,6:N1}s {1} {2}" -f $sw.Elapsed.TotalSeconds, $kind, $extra)
}
try { $p.StandardInput.WriteLine('quit'); $p.WaitForExit(3000) | Out-Null } catch {}
if (-not $p.HasExited) { $p.Kill() }
$lines | Set-Content -Encoding utf8 $out
Write-Host "Saved $out"
