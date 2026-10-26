; -- AetherGazer_Server Inno Setup Script Template --
#define MyAppName "深空之眼 离线单机服务"
#define MyAppVersion "5.0.2"
#define MyAppPublisher "TwilightStation"
#define MyAppExeName "LocalServer.exe"
#define MySourceDir "__SOURCE_DIR__"

[Setup]
AppId={{6C39F80A-5A71-46B9-A2EB-25588D169123}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} v{#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\AetherGazer_Server
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
OutputDir=__DIST_DIR__
OutputBaseFilename=__OUTPUT_SETUP_NAME__
SetupIconFile={#MySourceDir}\Assets\AppIcon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
DisableDirPage=no
DisableProgramGroupPage=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "installcert"; Description: "自动导入并信任仿真服务根证书 (强烈推荐，解除游戏客户端与充值连接证书告警)"; GroupDescription: "安全与证书配置:"; Flags: checkedonce

[Files]
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\控制台启动模式 (调试)"; Filename: "{app}\v5_server\run_server.bat"; WorkingDir: "{app}\v5_server"
Name: "{group}\一键安装Windows证书"; Filename: "{app}\一键安装Windows证书.bat"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "certutil.exe"; Parameters: "-addstore -f ""ROOT"" ""{app}\v5_server\sdk_ca.crt"""; StatusMsg: "正在将本地仿真根证书安装至系统受信任颁发机构..."; Tasks: installcert; Flags: runhidden
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "taskkill.exe"; Parameters: "/F /IM LocalServer.exe /T"; Flags: runhidden
Filename: "taskkill.exe"; Parameters: "/F /IM RestartAgent.exe /T"; Flags: runhidden
Filename: "powershell.exe"; Parameters: "-NoProfile -WindowStyle Hidden -Command ""Get-Process python -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -like '{app}*' } | Stop-Process -Force"""; Flags: runhidden

[UninstallDelete]
; 彻底清理动态生成的日志、缓存、PID与临时文件，做到卸载零残留
Type: filesandordirs; Name: "{app}\v5_server\logs"
Type: filesandordirs; Name: "{app}\v5_server\__pycache__"
Type: filesandordirs; Name: "{app}\v5_server\cdn_cache"
Type: files; Name: "{app}\v5_server\.server.pid"
Type: files; Name: "{app}\v5_server\game.db"
Type: filesandordirs; Name: "{localappdata}\LocalServer"
Type: filesandordirs; Name: "{localappdata}\TwilightStation"
Type: filesandordirs; Name: "{app}"

[Code]
// 安装前环境检查：若服务正在运行，自动安全结束
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  Exec('taskkill.exe', '/F /IM LocalServer.exe /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/F /IM RestartAgent.exe /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

// 卸载前环境检查：确保进程全部终止
function InitializeUninstall(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  Exec('taskkill.exe', '/F /IM LocalServer.exe /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/F /IM RestartAgent.exe /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('powershell.exe', '-NoProfile -WindowStyle Hidden -Command "Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like ''' + ExpandConstant('{app}') + '*'' } | Stop-Process -Force"', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

// 卸载后深度清理
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    // 强制清理主安装根目录
    DelTree(ExpandConstant('{app}'), True, True, True);
  end;
end;
