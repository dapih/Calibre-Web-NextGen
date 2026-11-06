; Development preview: first install only. Removal is unavailable until every
; supported maintenance entry point shares the installation gate.
; Do not publish until the installer and maintenance work is complete.
#ifndef CWNGPayload
  #error CWNGPayload must name a prepared payload
#endif
#ifndef CWNGBootstrap
  #error CWNGBootstrap must name the compiled native bootstrap
#endif
#ifndef CWNGVersion
  #error CWNGVersion must name the payload version
#endif
#ifdef CWNGFixtureRoot
  #define CWNGInstallDir CWNGFixtureRoot + "\program"
  #define CWNGProfileDir CWNGFixtureRoot + "\profile"
  #define CWNGAppId "CWNG-dapih-First-Install-Fixture"
#else
  #define CWNGInstallDir "{localappdata}\Programs\CWNG-dapih"
  #define CWNGProfileDir "{localappdata}\CWNG"
  #define CWNGAppId "CWNG-dapih"
#endif

[Setup]
AppId={#CWNGAppId}
AppName=Calibre-Web-NextGen Windows Preview
AppVersion={#CWNGVersion}
AppPublisher=Calibre-Web-NextGen
DefaultDirName={#CWNGInstallDir}
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.22000
DisableProgramGroupPage=yes
Uninstallable=no
CreateUninstallRegKey=no
OutputBaseFilename=CWNG-Windows-Setup-Preview
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern

[Messages]
WelcomeLabel2=This development preview supports a first installation only. Upgrade, repair and uninstall are not available.

[Files]
Source: "{#CWNGBootstrap}"; DestDir: "{app}"; DestName: "CWNG.exe"; Flags: ignoreversion
Source: "{#CWNGPayload}\*"; DestDir: "{app}\versions\{#CWNGVersion}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
#ifndef CWNGFixtureRoot
Name: "{autoprograms}\Calibre-Web-NextGen"; Filename: "{app}\CWNG.exe"
#endif

[Code]
function GetFileAttributesW(Path: String): Cardinal;
  external 'GetFileAttributesW@kernel32.dll stdcall';
function GetLastError(): Cardinal;
  external 'GetLastError@kernel32.dll stdcall';
function ExistsOrInaccessible(Path: String): Boolean;
var ErrorCode: Cardinal;
begin
  Result := True;
  if GetFileAttributesW(Path) <> $FFFFFFFF then exit;
  ErrorCode := GetLastError();
  Result := (ErrorCode <> 2) and (ErrorCode <> 3);
end;

function UnsafeInstallPath(Path: String): Boolean;
var Current, Parent: String; Attrs, ErrorCode: Cardinal;
begin
  Result := True;
  Current := RemoveBackslashUnlessRoot(ExpandFileName(Path));
  while Current <> '' do begin
    Attrs := GetFileAttributesW(Current);
    if Attrs = $FFFFFFFF then begin
      ErrorCode := GetLastError();
      if (ErrorCode <> 2) and (ErrorCode <> 3) then exit;
    end else if ((Attrs and $400) <> 0) or ((Attrs and $10) = 0) then exit;
    Parent := RemoveBackslashUnlessRoot(ExtractFileDir(Current));
    if (Parent = '') or (CompareText(Parent, Current) = 0) then break;
    Current := Parent;
  end;
  Result := False;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Target: String;
begin
  Result := '';
  Target := ExpandConstant('{#CWNGInstallDir}');
  if CompareText(RemoveBackslashUnlessRoot(ExpandFileName(ExpandConstant('{app}'))),
                 RemoveBackslashUnlessRoot(ExpandFileName(Target))) <> 0 then
    Result := 'This preview installs only into its default location.'
  else if UnsafeInstallPath(Target) then
    Result := 'The installation location is not a safe local directory.'
  else if ExistsOrInaccessible(Target) then
    Result := 'An installation already exists. Upgrade and repair are not available.'
  else if ExistsOrInaccessible(ExpandConstant('{#CWNGProfileDir}')) then
    Result := 'An existing profile was found. This preview only supports a new installation.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and
     not SaveStringToFile(ExpandConstant('{app}\active-version.txt'), '{#CWNGVersion}' + #13#10, False) then
    RaiseException('Could not activate the installed payload.');
end;

