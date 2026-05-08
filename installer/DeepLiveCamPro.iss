; ======================================================================
; Deep-Live-Cam Pro — Inno Setup script
; ======================================================================
; Builds a signed .exe installer that:
;   - installs to %ProgramFiles%\DeepLiveCamPro
;   - lays down the PyInstaller onefolder output (incl. _runtime/cuda/bin)
;   - installs VC++ redistributable if missing
;   - registers Start-menu + desktop shortcuts
;   - writes uninstall entries
;   - launches the bootstrapper (launch.py compiled to DeepLiveCamPro.exe)
; ======================================================================

#define MyAppName "Deep-Live-Cam Pro"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Deep-Live-Cam Pro"
#define MyAppURL "https://deeplivecam.pro/"
#define MyAppExeName "DeepLiveCamPro.exe"

[Setup]
AppId={{4A4F8E6C-4B0F-4F4D-9A76-DLC-PRO-2026}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\DeepLiveCamPro
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist\installer
OutputBaseFilename=DeepLiveCamPro-Setup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=..\media\DLC.ico
; Signing happens post-build via signtool; uncomment when cert is available:
; SignTool=signtool
; SignedUninstaller=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
    GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; --- payload built by PyInstaller into ../dist/DeepLiveCamPro/* ---
Source: "..\dist\DeepLiveCamPro\*"; DestDir: "{app}"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; --- VC++ redistributable bundled but only run if missing ---
Source: "prerequisites\VC_redist.x64.exe"; DestDir: "{tmp}"; \
    Flags: deleteafterinstall; Check: VCRedistNeeded

; --- bundled CUDA/cuDNN runtime payload (~800MB) ---
; PyInstaller already collects these into dist\DeepLiveCamPro\_runtime\cuda\bin
; via the spec's binaries list, so no extra Files entries needed here.

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    Tasks: desktopicon

[Run]
Filename: "{tmp}\VC_redist.x64.exe"; \
    Parameters: "/install /quiet /norestart"; \
    StatusMsg: "Installing Visual C++ Redistributable..."; \
    Check: VCRedistNeeded; Flags: waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Wipe per-user state on uninstall — license stays bound on server until
; user clicks "Deactivate" from the app first.
Type: filesandordirs; Name: "{localappdata}\DeepLiveCamPro"

[Code]
function VCRedistNeeded(): Boolean;
var version: Cardinal;
begin
  Result := True;
  if RegQueryDWordValue(HKLM, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64',
                        'Bld', version) then
    Result := version < 30000;
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  // Refuse to install on 32-bit Windows.
  if not IsWin64() then begin
    MsgBox('Deep-Live-Cam Pro requires 64-bit Windows.',
           mbCriticalError, MB_OK);
    Result := False;
  end;
end;
