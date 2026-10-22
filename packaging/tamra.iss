; Inno Setup 6 script for the per-user Tamra installer.
; Build with ./scripts/build.ps1 -Installer (it runs PyInstaller first and passes the version):
;   ISCC /DAppVersion=<tamra.__version__> packaging\tamra.iss
; The uninstaller removes only the install folder. User data in %LOCALAPPDATA%\Tamra stays.

#ifndef AppVersion
  #error Pass the version: ISCC /DAppVersion=<tamra.__version__> packaging\tamra.iss
#endif

[Setup]
; Fixed forever, so an upgrade replaces the same install. "{{" is a literal "{".
AppId={{37AE9ABF-480B-41DB-8EE2-2B274A74F4DD}
AppName=Tamra
AppVersion={#AppVersion}
AppPublisher=The Tamra contributors
AppPublisherURL=https://github.com/EARTH157/tamra
AppSupportURL=https://github.com/EARTH157/tamra/issues
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Tamra
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
; The Restart Manager closes a running Tamra (and its llama-server.exe) before files are replaced.
CloseApplications=yes
; Not reopened by the Restart Manager: the postinstall "Launch Tamra" box does that.
RestartApplications=no
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=Tamra-Setup-{#AppVersion}
SetupIconFile=tamra.ico
UninstallDisplayIcon={app}\Tamra.exe
WizardStyle=modern
SolidCompression=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; PyInstaller's folder from an older version: files it no longer ships must not linger.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\Tamra\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Tamra"; Filename: "{app}\Tamra.exe"
Name: "{autodesktop}\Tamra"; Filename: "{app}\Tamra.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Tamra.exe"; Description: "{cm:LaunchProgram,Tamra}"; Flags: nowait postinstall skipifsilent

[Code]
const
  WebView2ClientKey = 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WebView2Url = 'https://developer.microsoft.com/microsoft-edge/webview2/';

function HasWebView2Version(RootKey: Integer; SubKey: String): Boolean;
var
  Version: String;
begin
  Version := '';
  Result := RegQueryStringValue(RootKey, SubKey, 'pv', Version)
    and (Version <> '') and (Version <> '0.0.0.0');
end;

{ The Evergreen WebView2 Runtime, installed per machine (32-bit registry view) or per user. }
function WebView2Installed: Boolean;
begin
  Result := HasWebView2Version(HKLM, 'SOFTWARE\WOW6432Node\' + WebView2ClientKey)
    or HasWebView2Version(HKCU, 'Software\' + WebView2ClientKey);
end;

{ A missing runtime never blocks the install, and a silent install only logs it. }
function InitializeSetup: Boolean;
var
  ErrorCode: Integer;
begin
  Result := True;
  if WebView2Installed then
    Log('Microsoft Edge WebView2 Runtime found.')
  else if WizardSilent then
    Log('Microsoft Edge WebView2 Runtime not found. Tamra needs it to show its window: ' + WebView2Url)
  else if MsgBox('Tamra needs the Microsoft Edge WebView2 Runtime, which was not found on this computer.'
      + #13#10#13#10 + 'Open the download page now? Setup continues either way.',
      mbConfirmation, MB_YESNO) = IDYES then
  begin
    if not ShellExec('open', WebView2Url, '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode) then
      Log('Could not open ' + WebView2Url + ': ' + SysErrorMessage(ErrorCode));
  end;
end;
