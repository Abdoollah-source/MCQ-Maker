#define MyAppName "MCQ Maker"
#define MyAppVersion "0.1.0"
#define MyAppExeName "MCQ Maker.exe"

[Setup]
AppId={{B4FD0CC6-EB2F-48C8-B755-EC148AA6109D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=MCQ Maker
DefaultDirName={localappdata}\Programs\MCQ Maker
DefaultGroupName=MCQ Maker
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=MCQ-Maker-Setup-{#MyAppVersion}
SetupIconFile=..\mcq_maker\assets\app_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoDescription=Installer for {#MyAppName}

[Files]
Source: "..\release\MCQ Maker\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\THIRD-PARTY-NOTICES.txt"; DestDir: "{app}"; Flags: ignoreversion

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Icons]
Name: "{group}\MCQ Maker"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "MCQMaker.Desktop"
Name: "{autodesktop}\MCQ Maker"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; AppUserModelID: "MCQMaker.Desktop"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Open MCQ Maker"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'MCQ Maker');
end;
