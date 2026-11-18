# One-shot build: tests -> icon -> SensorHost (self-contained) -> PyInstaller (onedir) -> Inno Setup
#                 -> portable ZIP -> dist\ + SHA256SUMS.txt
# Usage: powershell -ExecutionPolicy Bypass -File packaging\build.ps1 [-SkipTests]
# The version comes ONLY from hardware_monitor_widget.__version__ (version_info.txt is rendered from a template,
# the installer receives /DAppVersion).
param([switch]$SkipTests)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Python: project venv if present, otherwise whatever "python" is on PATH (CI).
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { $py = (Get-Command python -ErrorAction Stop).Source }

# Inno Setup compiler: $env:ISCC override, standard install locations, then PATH.
function Find-Iscc {
    $cands = @()
    if ($env:ISCC) { $cands += $env:ISCC }
    foreach ($base in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, (Join-Path $env:LOCALAPPDATA 'Programs'))) {
        if ($base) { $cands += (Join-Path $base 'Inno Setup 6\ISCC.exe') }
    }
    foreach ($c in $cands) { if (Test-Path -LiteralPath $c) { return $c } }
    $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw 'ISCC.exe not found. Install Inno Setup 6 or set the ISCC environment variable.'
}
$iscc = Find-Iscc

function Run($exe, [string[]]$a) {
    & $exe @a
    if ($LASTEXITCODE -ne 0) { throw "Failed: $exe $($a -join ' ') (exit $LASTEXITCODE)" }
}

$version = (& $py -c "import hardware_monitor_widget as t; print(t.__version__)").Trim()
if ($version -notmatch '^(\d+)\.(\d+)\.(\d+)$') { throw "Unexpected version '$version' (expected MAJOR.MINOR.PATCH)" }
$maj, $min, $pat = $Matches[1], $Matches[2], $Matches[3]
Write-Host "Building version $version (ISCC: $iscc)"

# Render version_info.txt from the template.
$tpl = Get-Content -LiteralPath (Join-Path $root 'packaging\version_info.template.txt') -Raw
$tpl = $tpl.Replace('@MAJOR@', $maj).Replace('@MINOR@', $min).Replace('@PATCH@', $pat).Replace('@VERSION@', $version)
[System.IO.File]::WriteAllText((Join-Path $root 'packaging\version_info.txt'), $tpl, (New-Object System.Text.UTF8Encoding($false)))

if (-not $SkipTests) { Run $py @('-m', 'pytest', '-q') } else { Write-Host 'Skipping tests (-SkipTests)' }
Run $py @('packaging\make_icon.py')

$buildDir = Join-Path $root 'packaging\build'
if (Test-Path -LiteralPath $buildDir) { Remove-Item -LiteralPath $buildDir -Recurse -Force }

# SensorHost: self-contained publish (no .NET runtime needed on the target machine) into a staging folder.
$hostDir = Join-Path $buildDir 'sensorhost'
Run 'powershell' @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $root 'native\build.ps1'), '-SelfContained', '-OutDir', $hostDir)
if (-not (Test-Path -LiteralPath (Join-Path $hostDir 'coreclr.dll'))) { throw 'SensorHost publish is not self-contained (coreclr.dll missing)' }
$env:HMW_SENSORHOST_DIR = $hostDir

# PyInstaller onedir (never --onefile: see docs/SECURITY_NOTES.md)
Run $py @('-m', 'PyInstaller', '--noconfirm', '--clean', '--distpath', 'packaging\build\dist',
          '--workpath', 'packaging\build\work', 'packaging\hardware_monitor_widget.spec')
Remove-Item Env:HMW_SENSORHOST_DIR -ErrorAction SilentlyContinue
$appDir = Join-Path $buildDir 'dist\HardwareMonitorWidget'
if (-not (Test-Path -LiteralPath (Join-Path $appDir 'HardwareMonitorWidget.exe'))) { throw 'PyInstaller did not produce HardwareMonitorWidget.exe' }

# Installer
Run $iscc @("/DAppVersion=$version", 'packaging\installer.iss')

New-Item -ItemType Directory -Force dist | Out-Null
$setupName = "HardwareMonitorWidget-Setup-$version.exe"
$zipName = "HardwareMonitorWidget-portable-$version.zip"
Copy-Item -LiteralPath "packaging\build\installer\$setupName" 'dist\' -Force

# Portable ZIP: <zip>\HardwareMonitorWidget\... (onedir) + LEEME-portable.txt
$stage = Join-Path $buildDir 'portable'
New-Item -ItemType Directory -Force $stage | Out-Null
Copy-Item -LiteralPath $appDir $stage -Recurse -Force
$readme = @"
Hardware Monitor Widget $version - versión portable
=====================================================

Uso
  1. Extraiga el ZIP completo (no ejecute el .exe desde dentro del ZIP).
  2. Ejecute HardwareMonitorWidget\HardwareMonitorWidget.exe.

Administrador
  La aplicación SIEMPRE se ejecuta como administrador y Windows mostrará el aviso de UAC al abrirla.
  Lo necesita para leer las temperaturas de CPU y placa base (controlador PawnIO, https://pawnio.eu/;
  no se incluye ni se instala automáticamente).

Seguridad: dónde extraer
  Como la aplicación se ejecuta elevada, extraiga la carpeta en una ubicación que SOLO los administradores
  puedan modificar, por ejemplo dentro de C:\Program Files, o una carpeta con permisos restringidos
  (quite el permiso de escritura a "Usuarios" y "Usuarios autenticados"). Si la carpeta es escribible por
  usuarios normales, cualquier programa sin privilegios podría sustituir el código que se ejecuta como
  administrador.

Iniciar con Windows
  En la versión portable, "Iniciar con Windows" usa la clave Run del usuario y pedirá UAC en cada inicio de
  sesión. Para un inicio sin aviso de UAC use el instalador (crea una tarea programada).

Desinstalar
  Cierre la aplicación (menú de la bandeja > Salir), desactive "Iniciar con Windows" y borre la carpeta.
  La configuración queda en %APPDATA%\HardwareMonitorWidget.

Licencias de terceros: THIRD_PARTY_NOTICES.md (dentro de la carpeta de la aplicación).
"@
[System.IO.File]::WriteAllText((Join-Path $stage 'LEEME-portable.txt'), $readme, (New-Object System.Text.UTF8Encoding($true)))
$zipPath = Join-Path $root "dist\$zipName"
if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
# Python's zipfile (not Compress-Archive): forward-slash entry names that every extractor understands.
$zipCode = @'
import sys, zipfile
from pathlib import Path
src, dst = Path(sys.argv[1]), sys.argv[2]
with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for f in sorted(src.rglob("*")):
        if f.is_file():
            z.write(f, f.relative_to(src).as_posix())
'@
$zipScript = Join-Path $buildDir 'make_zip.py'
[System.IO.File]::WriteAllText($zipScript, $zipCode, (New-Object System.Text.UTF8Encoding($false)))
Run $py @($zipScript, $stage, $zipPath)

# SHA256SUMS.txt: "<hash>  <name>" (sha256sum format, LF, no BOM), only this version's artifacts.
$lines = foreach ($n in @($setupName, $zipName)) {
    '{0}  {1}' -f (Get-FileHash -LiteralPath (Join-Path $root "dist\$n") -Algorithm SHA256).Hash.ToLower(), $n
}
[System.IO.File]::WriteAllText((Join-Path $root 'dist\SHA256SUMS.txt'), (($lines -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding($false)))

foreach ($n in @($setupName, $zipName)) {
    '{0}  {1:N1} MB' -f $n, ((Get-Item -LiteralPath (Join-Path $root "dist\$n")).Length / 1MB)
}
$dirBytes = (Get-ChildItem -LiteralPath $appDir -Recurse -File | Measure-Object Length -Sum).Sum
'onedir  {0:N1} MB' -f ($dirBytes / 1MB)
Write-Host '--- dist\SHA256SUMS.txt ---'
Get-Content -LiteralPath dist\SHA256SUMS.txt
