; Focus Core installer (Inno Setup 6).
; Build the staging folder first:  python installer/build.py --version 1.3.0
; Then compile:
;   ISCC.exe installer\installer.iss /DAppVersion="1.3.0"
; AppVersion can also be edited below; the /D flag overrides it.
;
; Offline flavor (bundles the full WebView2 standalone installer, so the
; app installs on machines with no network): stage with
;   python installer\build.py --version 1.3.0 --webview2-offline
; then compile with the extra define:
;   ISCC.exe installer\installer.iss /DAppVersion="1.3.0" /DWebView2Offline
; The setup exe is then named FocusCore-Setup-1.3.0-offline.exe.

; Per-machine flavor (fleet installs by IT, once per machine): compile
; with the extra define:
;   ISCC.exe installer\installer.iss /DAppVersion="1.3.0" /DPerMachine
; The setup exe is then named FocusCore-Setup-1.3.0-machine.exe.
; The two defines are orthogonal; both together give
; FocusCore-Setup-1.3.0-machine-offline.exe.

; AppVersion is passed on the ISCC command line (/DAppVersion="1.3.0").
; The #ifndef guard matters: a plain #define here would silently beat the
; command-line value (Inno gives the script the last word).
#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

[Setup]
; Roadmap 2.6: the per-machine flavor is a separate application
; identity. The same AppId would make Inno treat per-user and
; per-machine as one app -- a machine install over a user install
; would fight over "already installed" state. Separate installers,
; separate identities (VS Code does exactly this with its User vs
; System installers).
#ifdef PerMachine
AppId={{FAFD1C6B-54C9-4A1A-AD3C-8BE1DD383D28}
#else
AppId={{C7A3F2E1-8B4D-4F6A-9E2C-1D5A7B3F9E2C4}
#endif
AppName=Focus Core
AppVersion={#AppVersion}
AppVerName=Focus Core {#AppVersion}
AppPublisher=Focus Core
AppPublisherURL=https://github.com/ahtisham482/focus-core
AppSupportURL=https://github.com/ahtisham482/focus-core/issues
AppUpdatesURL=https://github.com/ahtisham482/focus-core/releases
AppCopyright=Copyright (C) 2026 Focus Core
UninstallDisplayIcon={app}\icon.ico
; MIT license shown as a proper license page during setup.
LicenseFile={#SourcePath}\..\LICENSE
#ifdef PerMachine
DefaultDirName={autopf}\Focus Core
; 64-bit install mode: the staged runtime is amd64 embedded Python, so
; the machine flavor is 64-bit only. Without this, a 32-bit Setup would
; resolve {autopf} to "C:\Program Files (x86)" and registry writes to
; the WOW6432Node view -- wrong home for a 64-bit fleet install.
ArchitecturesInstallIn64BitMode=x64
#else
DefaultDirName={localappdata}\Programs\Focus Core
#endif
DefaultGroupName=Focus Core
DisableProgramGroupPage=yes
#ifdef PerMachine
PrivilegesRequired=admin
#else
PrivilegesRequired=lowest
#endif
OutputDir={#SourcePath}
; The offline flavor (built with /DWebView2Offline) bundles the ~200 MB
; standalone WebView2 installer, so it gets its own artifact name. The
; two defines are orthogonal: -machine and -offline compose into four
; artifact names (FocusCore-Setup-<ver>[-machine][-offline].exe).
#ifdef PerMachine
  #define MachineSuffix "-machine"
#else
  #define MachineSuffix ""
#endif
#ifdef WebView2Offline
  #define OfflineSuffix "-offline"
#else
  #define OfflineSuffix ""
#endif
OutputBaseFilename=FocusCore-Setup-{#AppVersion}{#MachineSuffix}{#OfflineSuffix}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Focus Core
; Python + packages are bundled, so the user needs nothing pre-installed.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "Start Focus Core when Windows starts"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "{#SourcePath}\staging\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
; {autoprograms} and {autodesktop} auto-resolve: per-user under
; PrivilegesRequired=lowest, all-users under admin. Only the startup
; entry needs a per-flavor branch (Roadmap 2.6): {userstartup} is
; always per-user, even in an admin install.
Name: "{autoprograms}\Focus Core"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m focuscore.launcher"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"
Name: "{autodesktop}\Focus Core"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m focuscore.launcher"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon
#ifdef PerMachine
Name: "{commonstartup}\Focus Core"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m focuscore.launcher"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; Tasks: startup
#else
Name: "{userstartup}\Focus Core"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m focuscore.launcher"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; Tasks: startup
#endif

[Run]
; WebView2 shows the app window. Most PCs already have it (it ships with
; Windows/Edge/Office); install it only when missing. The staging step
; puts exactly one of the two installers in {app} -- the online
; bootstrapper for the default flavor, the full standalone installer for
; the offline flavor -- and the FileExists guard picks the one that is
; really there, so a mixed staging can never run the wrong one.
Filename: "{app}\webview2bootstrapper.exe"; Parameters: "/silent /install"; StatusMsg: "Installing the WebView2 window component..."; Flags: waituntilterminated; Check: NeedsWebView2 and FileExists(ExpandConstant('{app}\webview2bootstrapper.exe'))
Filename: "{app}\webview2standalone.exe"; Parameters: "/silent /install"; StatusMsg: "Installing the WebView2 window component (offline installer)..."; Flags: waituntilterminated; Check: NeedsWebView2 and FileExists(ExpandConstant('{app}\webview2standalone.exe'))
; Offer to start the app at the end of setup.
Filename: "{app}\python\pythonw.exe"; Parameters: "-m focuscore.launcher"; WorkingDir: "{app}"; Description: "Launch Focus Core now"; Flags: nowait postinstall skipifsilent runasoriginaluser
; One-click updates run this installer silently; reopen the app afterwards.
; runasoriginaluser on both launch entries (roadmap 2.6 repair): the
; machine installer runs elevated, and without it the app would
; first-launch as admin/SYSTEM with %LOCALAPPDATA% resolving to the
; wrong profile. Under Intune System context there is no
; original-user token, so the flag is ignored there -- see
; docs/enterprise-deploy.md ("Intune (System context)").
Filename: "{app}\python\pythonw.exe"; Parameters: "-m focuscore.launcher"; WorkingDir: "{app}"; Flags: nowait skipifnotsilent runasoriginaluser

[UninstallDelete]
; Remove the program files. The user's data (%LOCALAPPDATA%\Focus Core)
; is deliberately kept, so uninstalling never deletes tracked history.
Type: filesandordirs; Name: "{app}"

[Code]
// The WebView2 runtime registers under this client GUID whether it was
// installed per-machine or per-user. This installer is per-user
// (PrivilegesRequired=lowest), so it must also check HKCU: a per-user
// WebView2 leaves no HKLM trace, and without the HKCU check every
// silent update would pointlessly reinstall it.
function NeedsWebView2(): Boolean;
begin
  Result := not RegValueExists(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv')
    and not RegValueExists(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv')
    and not RegValueExists(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv');
end;

// Roadmap 2.4: opt-in data wipe at uninstall. The default uninstall
// keeps the user's data folder (%LOCALAPPDATA%\Focus Core) -- see
// [UninstallDelete]. The wipe fires only when the user ticks the
// checkbox on the uninstall wizard page (interactive mode only,
// unchecked by default) or when IT passes /DELETEDATA to the silent
// uninstaller for enterprise offboarding. Nothing outside {app} and
// the data folder is ever touched, and a wipe that fails partway is
// logged, never allowed to fail the uninstall itself.
var
  DeleteDataCheckBox: TNewCheckBox;
  DeleteDataViaSwitch: Boolean;

function DeleteDataSwitchGiven(): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
  begin
    if CompareText(ParamStr(I), '/DELETEDATA') = 0 then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

function InitializeUninstall(): Boolean;
var
  WipePage: TWizardPage;
begin
  Result := True;
  DeleteDataViaSwitch := DeleteDataSwitchGiven();
  // The checkbox is an interactive-only offer: a silent uninstall
  // shows no UI at all (and keeps the data unless /DELETEDATA was
  // given), and the page is pointless when the switch already made
  // the decision.
  if (not DeleteDataViaSwitch) and (not UninstallSilent) then
  begin
    WipePage := CreateCustomPage(wpWelcome, 'Remove Focus Core',
      'Your tracked history is kept when you uninstall, unless you choose to delete it below.');
    DeleteDataCheckBox := TNewCheckBox.Create(WipePage);
    DeleteDataCheckBox.Parent := WipePage.Surface;
    DeleteDataCheckBox.Left := ScaleX(0);
    DeleteDataCheckBox.Top := ScaleY(0);
    DeleteDataCheckBox.Width := WipePage.SurfaceWidth;
    DeleteDataCheckBox.Height := ScaleY(20);
    DeleteDataCheckBox.Caption := 'Also delete my Focus Core data (history, settings, backups)';
    DeleteDataCheckBox.Checked := False;
  end;
end;

#ifdef PerMachine
// Roadmap 2.6: machine-flavor data wipe target. The IT-managed
// HKLM\Software\Focus Core\DataDir value wins when set (the 1.12
// machine tier); otherwise {commonappdata}\Focus Core when it exists.
// Per-user profile folders (C:\Users\*\AppData\Local\Focus Core) are
// deliberately out of scope: enumerating every profile's AppData is
// fragile and surprising -- IT wipes those with their own tooling.
function MachineDataDir(): String;
var
  Configured: String;
begin
  Result := '';
  if RegQueryStringValue(HKLM, 'Software\Focus Core', 'DataDir', Configured)
    and (Configured <> '') then
    Result := Configured
  else if DirExists(ExpandConstant('{commonappdata}\Focus Core')) then
    Result := ExpandConstant('{commonappdata}\Focus Core');
end;
#endif

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if DeleteDataViaSwitch or
       (Assigned(DeleteDataCheckBox) and DeleteDataCheckBox.Checked) then
    begin
      // Roadmap 2.6: in the machine flavor the uninstaller runs as
      // admin, so {localappdata} would resolve to the ADMIN's profile
      // -- the wrong target. The machine flavor wipes the
      // machine-level data dir instead (see MachineDataDir below).
#ifdef PerMachine
      DataDir := MachineDataDir();
#else
      DataDir := ExpandConstant('{localappdata}\Focus Core');
#endif
      if DataDir = '' then
      begin
        Log('Data wipe requested but no data dir was found; nothing to wipe.');
      end
      else
      begin
        Log('Data wipe requested: deleting ' + DataDir);
        try
          if DirExists(DataDir) and
             not DelTree(DataDir, True, True, True) then
            Log('Data wipe incomplete; some files may remain in ' + DataDir);
        except
          Log('Data wipe failed; the uninstall continues.');
        end;
      end;
    end;
  end;
end;
