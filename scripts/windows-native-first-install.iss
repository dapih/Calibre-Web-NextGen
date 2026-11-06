; Development preview: first install, same-version repair/reinstall and uninstall.
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
  #define CWNGGatePath CWNGFixtureRoot + "\CWNG-install.lock"
  #define CWNGAppId "CWNG-dapih-First-Install-Fixture"
#else
  #define CWNGInstallDir "{localappdata}\Programs\CWNG-dapih"
  #define CWNGProfileDir "{localappdata}\CWNG"
  #define CWNGGatePath "{localappdata}\CWNG-install.lock"
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
#ifdef CWNGFixtureRoot
CreateUninstallRegKey=no
#endif
OutputBaseFilename=CWNG-Windows-Setup-Preview
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern

[Messages]
WelcomeLabel2=This development preview supports first install and same-version repair or reinstall. Upgrade is not available. Uninstall keeps your profile and books.

[Files]
Source: "{#CWNGBootstrap}"; DestDir: "{app}"; DestName: "CWNG.exe"; Flags: ignoreversion
Source: "{#CWNGPayload}\*"; DestDir: "{app}\versions\{#CWNGVersion}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
#ifndef CWNGFixtureRoot
Name: "{autoprograms}\Calibre-Web-NextGen"; Filename: "{app}\CWNG.exe"
#endif

[UninstallDelete]
Type: files; Name: "{app}\active-version.txt"
Type: files; Name: "{app}\repair-version.txt"

[Code]
var InstallGate: THandle;

function GetFileAttributesW(Path: String): Cardinal;
  external 'GetFileAttributesW@kernel32.dll stdcall';
function GetLastError(): Cardinal;
  external 'GetLastError@kernel32.dll stdcall';
function CreateFileW(Name: String; Access, Share: Cardinal; Security: LongWord;
  Creation, Flags: Cardinal; Template: THandle): THandle;
  external 'CreateFileW@kernel32.dll stdcall';
function CloseHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

function InitializeSetup(): Boolean;
var GatePath: String; Attrs: Cardinal;
begin
  Result := False;
  InstallGate := THandle(-1);
  GatePath := ExpandConstant('{#CWNGGatePath}');
  Attrs := GetFileAttributesW(GatePath);
  if (Attrs <> $FFFFFFFF) and ((Attrs and ($400 or $10)) <> 0) then begin
    MsgBox('The installation lock is not a regular file.', mbError, MB_OK);
    exit;
  end;
  InstallGate := CreateFileW(GatePath, $C0000000, 0, 0, 4, $00200080, 0);
  if InstallGate = THandle(-1) then begin
    MsgBox('Close CWNG and any other setup before installing.', mbError, MB_OK);
    exit;
  end;
  Result := True;
end;

procedure DeinitializeSetup();
begin
  if InstallGate <> THandle(-1) then CloseHandle(InstallGate);
end;
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
var Target, Profile: String; RetainedVersion, InstalledVersion: AnsiString;
begin
  Result := '';
  Target := ExpandConstant('{#CWNGInstallDir}');
  Profile := ExpandConstant('{#CWNGProfileDir}');
  if CompareText(RemoveBackslashUnlessRoot(ExpandFileName(ExpandConstant('{app}'))),
                 RemoveBackslashUnlessRoot(ExpandFileName(Target))) <> 0 then
    Result := 'This preview installs only into its default location.'
  else if UnsafeInstallPath(Target) then
    Result := 'The installation location is not a safe local directory.'
  else if ExistsOrInaccessible(Profile + '\maintenance-journal.json') then
    Result := 'Finish or recover the pending maintenance operation before setup.'
  else if ExistsOrInaccessible(Target) then begin
    if not LoadStringFromFile(Target + '\repair-version.txt', InstalledVersion) then
      LoadStringFromFile(Target + '\active-version.txt', InstalledVersion);
    if Trim(InstalledVersion) = '' then begin
      if not LoadStringFromFile(Profile + '\retained-install-version.txt', RetainedVersion) or
         (Trim(RetainedVersion) <> '{#CWNGVersion}') then
        Result := 'The existing installation cannot be reinstalled with this version.';
    end else if Trim(InstalledVersion) <> '{#CWNGVersion}' then
      Result := 'The existing installation cannot be repaired with this version.';
  end
  else if ExistsOrInaccessible(Profile) then begin
    if not LoadStringFromFile(Profile + '\retained-install-version.txt', RetainedVersion) or
       (Trim(RetainedVersion) <> '{#CWNGVersion}') then
      Result := 'This profile cannot be reinstalled with this version. Existing data has not been changed.';
  end;
end;

function InitializeUninstall(): Boolean;
var GatePath: String; Attrs: Cardinal;
begin
  Result := False;
  InstallGate := THandle(-1);
  GatePath := ExpandConstant('{#CWNGGatePath}');
  Attrs := GetFileAttributesW(GatePath);
  if (Attrs <> $FFFFFFFF) and ((Attrs and ($400 or $10)) <> 0) then begin
    MsgBox('The installation lock is not a regular file.', mbError, MB_OK);
    exit;
  end;
  InstallGate := CreateFileW(GatePath, $C0000000, 0, 0, 4, $00200080, 0);
  if InstallGate = THandle(-1) then begin
    MsgBox('Close CWNG and any maintenance operation before uninstalling.', mbError, MB_OK);
    exit;
  end;
  if ExistsOrInaccessible(ExpandConstant('{#CWNGProfileDir}\maintenance-journal.json')) then begin
    CloseHandle(InstallGate);
    InstallGate := THandle(-1);
    MsgBox('Finish or recover the pending maintenance operation before uninstalling.', mbError, MB_OK);
    exit;
  end;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var Command, Profile: String;
begin
  if CurUninstallStep <> usPostUninstall then exit;
  Profile := ExpandConstant('{#CWNGProfileDir}');
  if DirExists(Profile) and
     not SaveStringToFile(Profile + '\retained-install-version.txt', '{#CWNGVersion}' + #13#10, False) then
    MsgBox('The profile was retained, but the same-version reinstall marker could not be written.', mbError, MB_OK);
  if RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'CWNG-dapih', Command) and
     (CompareText(Command, '"' + ExpandConstant('{app}\CWNG.exe') + '" --state "' + Profile + '"') = 0) then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'CWNG-dapih');
end;

procedure DeinitializeUninstall();
begin
  if InstallGate <> THandle(-1) then CloseHandle(InstallGate);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var Target: String;
begin
  Target := ExpandConstant('{app}');
  if CurStep = ssInstall then begin
    if not ForceDirectories(Target) or
       not SaveStringToFile(Target + '\repair-version.txt', '{#CWNGVersion}' + #13#10, False) then
      RaiseException('Could not prepare the installation.');
    if FileExists(Target + '\active-version.txt') and not DeleteFile(Target + '\active-version.txt') then
      RaiseException('Could not suspend the installed version for repair.');
  end else if CurStep = ssPostInstall then begin
    if not SaveStringToFile(Target + '\active-version.txt', '{#CWNGVersion}' + #13#10, False) then
      RaiseException('Could not activate the installed payload.');
    DeleteFile(Target + '\repair-version.txt');
  end;
end;

