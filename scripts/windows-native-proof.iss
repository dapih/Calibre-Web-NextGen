; Disposable installer proof only. It is deliberately a different AppId and
; install/state directory from the future product. Do not publish this package.
; Existing installations and data are refused: upgrades/repair/uninstall need
; the separate maintenance transaction before a production package is safe.
#ifndef CWNGProofPayload
  #error CWNGProofPayload must name an explicitly prepared proof payload
#endif
#ifndef CWNGProofBootstrap
  #error CWNGProofBootstrap must name the compiled proof bootstrap
#endif
#ifndef CWNGProofVersion
  #error CWNGProofVersion must name the prepared payload version
#endif
#ifndef CWNGProofTarget
  #error CWNGProofTarget must name a dedicated disposable target
#endif

[Setup]
AppId=CWNG-dapih-Windows-Installer-Proof
AppName=CWNG Windows Installer Proof
AppVersion={#CWNGProofVersion}
AppPublisher=Calibre-Web-NextGen development proof
DefaultDirName={#CWNGProofTarget}
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.22000
DisableProgramGroupPage=yes
Uninstallable=no
CreateUninstallRegKey=no
OutputBaseFilename=CWNG-Windows-Installer-PROOF
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern

[Messages]
WelcomeLabel2=This is an unsigned, disposable development proof, not a supported installer. It only installs into a new, empty location. It cannot upgrade, repair or uninstall an existing installation.%n%nIt uses a separate test profile and must not be used with a real book collection.

[Files]
Source: "{#CWNGProofBootstrap}"; DestDir: "{app}"; DestName: "CWNG.exe"; Flags: ignoreversion
Source: "{#CWNGProofPayload}\*"; DestDir: "{app}\versions\{#CWNGProofVersion}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Code]
function GetFileAttributesW(lpFileName: String): Cardinal;
  external 'GetFileAttributesW@kernel32.dll stdcall';
function GetLastError(): Cardinal;
  external 'GetLastError@kernel32.dll stdcall';

function ProofPathHasUnsafeAncestor(Path: String): Boolean;
var
  Current, Parent: String;
  Attrs, ErrorCode: Cardinal;
  IsTarget: Boolean;
begin
  Result := True;
  Current := RemoveBackslashUnlessRoot(ExpandFileName(Path));
  IsTarget := True;
  while Current <> '' do begin
    Attrs := GetFileAttributesW(Current);
    if Attrs = $FFFFFFFF then begin
      { Only the final, new proof directory may be absent. }
      ErrorCode := GetLastError();
      if (not IsTarget) or ((ErrorCode <> 2) and (ErrorCode <> 3)) then exit;
    end else if ((Attrs and $400) <> 0) or ((Attrs and $10) = 0) then
      exit;
    Parent := RemoveBackslashUnlessRoot(ExtractFileDir(Current));
    if (Parent = '') or (CompareText(Parent, Current) = 0) then break;
    Current := Parent;
    IsTarget := False;
  end;
  Result := False;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if CompareText(RemoveBackslashUnlessRoot(ExpandFileName(ExpandConstant('{app}'))),
                 RemoveBackslashUnlessRoot(ExpandFileName('{#CWNGProofTarget}'))) <> 0 then
    Result := 'This proof may only install into its designated disposable location.'
  else if ProofPathHasUnsafeAncestor(ExpandConstant('{app}')) then
    Result := 'The proof location or one of its ancestors is not a safe local directory.'
  else if DirExists(ExpandConstant('{app}')) or FileExists(ExpandConstant('{app}')) then
    Result := 'The proof only installs into a new location. No existing program files will be changed.'
  else if DirExists(ExpandConstant('{localappdata}\CWNG-Installer-Proof')) then
    Result := 'A previous proof profile exists. Choose a clean test environment; existing data will not be changed.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep <> ssPostInstall then exit;
  if not SaveStringToFile(ExpandConstant('{app}\active-version.txt'), '{#CWNGProofVersion}' + #13#10, False) then
    RaiseException('Could not activate the disposable proof payload.');
end;
