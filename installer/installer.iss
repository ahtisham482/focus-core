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

; AppVersion is passed on the ISCC command line (/DAppVersion="1.3.0").
; The #ifndef guard matters: a plain #define here would silently beat the
; command-line value (Inno gives the script the last word).
#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

[Setup]
AppId={{C7A3F2E1-8B4D-4F6A-9E2C-1D5A7B3F9E2C4}
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
DefaultDirName={localappdata}\Programs\Focus Core
DefaultGroupName=Focus Core
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#SourcePath}
; The offline flavor (built with /DWebView2Offline) bundles the ~200 MB
; standalone WebView2 installer, so it gets its own artifact name.
#ifdef WebView2Offline
OutputBaseFilename=FocusCore-Setup-{#AppVersion}-offline
#else
OutputBaseFilename=FocusCore-Setup-{#AppVersion}
#endif
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
Name: "{autoprograms}\Focus Core"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m focuscore.launcher"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"
Name: "{autodesktop}\Focus Core"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m focuscore.launcher"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon
Name: "{userstartup}\Focus Core"; Filename: "{app}\python\pythonw.exe"; Parameters: "-m focuscore.launcher"; WorkingDir: "{app}"; IconFilename: "{app}\icon.ico"; Tasks: startup

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
Filename: "{app}\python\pythonw.exe"; Parameters: "-m focuscore.launcher"; WorkingDir: "{app}"; Description: "Launch Focus Core now"; Flags: nowait postinstall skipifsilent
; One-click updates run this installer silently; reopen the app afterwards.
Filename: "{app}\python\pythonw.exe"; Parameters: "-m focuscore.launcher"; WorkingDir: "{app}"; Flags: nowait skipifnotsilent

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
