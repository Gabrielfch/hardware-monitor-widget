; Inno Setup script. Compile with: ISCC.exe /DAppVersion=0.1.0 packaging\installer.iss
#ifndef AppVersion
  #error AppVersion is required: run packaging/build.ps1 (it passes /DAppVersion from __version__)
#endif
#define AppName "Hardware Monitor Widget"
#define AppExe "HardwareMonitorWidget.exe"
#define TaskName "HardwareMonitorWidget"

[Setup]
AppId={{C3A7E1D4-6B52-4F0A-9E8D-2F4B7A1C9D35}
AppName={#AppName}
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
; Program Files is writable only by administrators: required because the app runs elevated.
DefaultDirName={autopf}\HardwareMonitorWidget
DefaultGroupName={#AppName}
; Fixed install location (Program Files): the elevated app must never live in a user-writable folder.
DisableDirPage=yes
DisableProgramGroupPage=yes
UsePreviousAppDir=no
PrivilegesRequired=admin
OutputDir=build\installer
OutputBaseFilename=HardwareMonitorWidget-Setup-{#AppVersion}
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
; The startup task default is derived from the real current state in InitializeWizard, so updating
; cannot silently re-enable a disabled entry or drop an enabled one.
UsePreviousTasks=no

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
spanish.StartWithWindows=Iniciar con Windows
english.StartWithWindows=Start with Windows
spanish.PawnIoMissing=PawnIO no está instalado. Sin este controlador no se mostrarán las temperaturas de CPU y placa base (el resto de sensores funciona).%n%nPuede instalarlo desde https://pawnio.eu/ (este instalador no lo descarga ni lo instala).
english.PawnIoMissing=PawnIO is not installed. Without this driver CPU and motherboard temperatures will not be shown (other sensors still work).%n%nYou can install it from https://pawnio.eu/ (this installer does not download or install it).

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startup"; Description: "{cm:StartWithWindows}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Dirs]
; No extra permissions: the folder inherits the Program Files ACL (administrators only).
Name: "{app}"
; Shared data folders of the elevated app (ADR-5/ADR-6: downloaded installers and custom icons). Created here, while
; the installer already runs elevated, so a normal user cannot pre-create them. Inno adds explicit ACEs only; the
; icacls entries of the Run section drop the ACEs inherited from %ProgramData% (Users could create/write there), leaving
; SYSTEM and Administrators full control and Users read/execute. The app re-validates owner and DACL anyway.
Name: "{commonappdata}\HardwareMonitorWidget"; Permissions: admins-full system-full users-readexec
Name: "{commonappdata}\HardwareMonitorWidget\icons"; Permissions: admins-full system-full users-readexec
Name: "{commonappdata}\HardwareMonitorWidget\updates"; Permissions: admins-full system-full users-readexec

[Files]
Source: "build\dist\HardwareMonitorWidget\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
#if FileExists("..\LICENSE")
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
#endif

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{sys}\icacls.exe"; Parameters: """{commonappdata}\HardwareMonitorWidget"" /inheritance:r"; Flags: runhidden waituntilterminated
Filename: "{sys}\icacls.exe"; Parameters: """{commonappdata}\HardwareMonitorWidget\icons"" /inheritance:r"; Flags: runhidden waituntilterminated
Filename: "{sys}\icacls.exe"; Parameters: """{commonappdata}\HardwareMonitorWidget\updates"" /inheritance:r"; Flags: runhidden waituntilterminated
; Creates the scheduled task (logon, highest privileges, current user, no UAC prompt at each start).
Filename: "{app}\{#AppExe}"; Parameters: "--enable-startup"; Flags: runhidden waituntilterminated; Tasks: startup
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent shellexec
; Relaunch after the silent install started by the in-app updater (it passes /LAUNCHAPP=1). The entry above is
; skipped when silent, so the app is never started twice; interactive installs are not affected.
Filename: "{app}\{#AppExe}"; Flags: nowait shellexec; Check: ShouldRelaunchApp

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#AppExe}"; Flags: runhidden; RunOnceId: "KillApp"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM SensorHost.exe"; Flags: runhidden; RunOnceId: "KillSensorHost"
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN {#TaskName} /F"; Flags: runhidden; RunOnceId: "DeleteStartupTask"

[UninstallDelete]
; User config in %APPDATA% is intentionally left untouched.
Type: filesandordirs; Name: "{app}"

[Code]
const
  PawnIoKey = 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO';

function PawnIoInstalled: Boolean;
begin
  Result := RegKeyExists(HKLM64, PawnIoKey) or RegKeyExists(HKLM32, PawnIoKey);
end;

{ True when the scheduled task already exists (keeps "Start with Windows" ticked on updates) }
function StartupTaskExists: Boolean;
var
  Code: Integer;
begin
  Result := Exec(ExpandConstant('{sys}\schtasks.exe'), '/Query /TN {#TaskName}', '', SW_HIDE, ewWaitUntilTerminated, Code)
            and (Code = 0);
end;

{ True only for a silent install started with /LAUNCHAPP=1 (the in-app updater) }
function ShouldRelaunchApp: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:LAUNCHAPP|0}') = '1');
end;

procedure InitializeWizard;
begin
  if StartupTaskExists then
    WizardSelectTasks('startup');
end;

{ The Qt app does not register with Restart Manager: stop it explicitly before replacing files }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM SensorHost.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and (not WizardSilent) and (not PawnIoInstalled) then
    MsgBox(CustomMessage('PawnIoMissing'), mbInformation, MB_OK);
end;
