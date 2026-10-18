; 紫苏叶策略 · Inno Setup 脚本（正式发版用）
;
; 构建机上跑：
;   ISCC.exe installer\zisuye.iss /DAppVersion=0.1.0
; 产物：installer\out\紫苏叶策略_Setup_0.1.0.exe
;
; 为什么数据目录不在安装目录下：Program Files 普通用户没写权限，
; 而且 SQLite 的 WAL 在 OneDrive 同步盘上会损坏。数据一律放 %LOCALAPPDATA%\ZiSuYe，
; 卸载时**不删**（PRD 验收第 3 条：卸载后重装数据一条不少）。

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

#define AppName "紫苏叶策略"
#define AppExe "紫苏叶.exe"
#define AppId "ZiSuYe"

[Setup]
AppId={{8F2B9C41-6D1E-4B77-9C3A-2E4A5D6B1F02}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=紫苏叶策略
DefaultDirName={localappdata}\Programs\{#AppId}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=out
OutputBaseFilename={#AppName}_Setup_{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}
ArchitecturesInstallIn64BitMode=x64compatible
; 只允许 64 位 Win10 1809 以上（WebView2 + pywebview 的下限）
MinVersion=10.0.17763

[Languages]
Name: "chinese"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
; onedir 产物整目录打包
Source: "..\dist\紫苏叶\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "立即启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只清程序文件；数据目录 %LOCALAPPDATA%\ZiSuYe 故意不在这里出现
Type: filesandordirs; Name: "{app}"

[Code]
function InitializeUninstall(): Boolean;
begin
  Result := True;
end;
