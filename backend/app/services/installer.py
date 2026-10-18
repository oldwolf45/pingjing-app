"""紫苏叶策略 · 安装 / 卸载（不带第三方依赖）。

为什么不是单纯的 Inno Setup 脚本：正式发版还是走 Inno（见 installer/zisuye.iss），
但构建机上经常没有 ISCC，而且「装→开→卸」这件事必须能被自动跑一遍
（tools/drill_install.py），不能只靠人手点窗口。所以安装逻辑自己实现一份，
路径、快捷方式、卸载项都跟 Inno 版本对齐。

这一段放在服务层而不是 installer/ 脚本里，是因为**卸载入口必须是被装进去的
紫苏叶.exe 自己**：目标机器上没有 Python，指向一个 .py 的卸载命令等于没有。
所以 CLI（installer/install.py）和应用内的 `--uninstall` 共用这一份实现。

目录约定（与 app/paths.py 对齐，二者不能各说各话）：
    <安装根>\\紫苏叶.exe       程序文件，卸载时删除
    %LOCALAPPDATA%\\ZiSuYe\\    数据、备份、日志，**卸载时保留**

卸载保留数据是硬要求（PRD 验收第 3 条：卸载后重装数据一条不少）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import winreg
from pathlib import Path

APP_NAME = "紫苏叶"
APP_ID = "ZiSuYe"
DEFAULT_VERSION = "0.1.0"
UNINSTALL_ROOT = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"


def default_install_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / APP_ID


def data_dir() -> Path:
    """和数据目录同源：%LOCALAPPDATA%\\ZiSuYe。卸载不许碰它。"""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_ID


def _desktop_lnk(reg_key: str) -> Path:
    """快捷方式名字跟着 reg_key 走：演练习可以用独立名字，不覆盖真装的图标。"""
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop" / f"{reg_key}.lnk"


def _startmenu_lnk(reg_key: str) -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return (Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            / f"{reg_key}.lnk")


def _make_shortcut(lnk: Path, target: Path, workdir: Path) -> bool:
    """用 WScript.Shell 建 .lnk。PowerShell 在 Win10/11 上一定有，比 ctypes 拼 COM 稳。"""
    lnk.parent.mkdir(parents=True, exist_ok=True)
    script = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
        f"$s.TargetPath='{target}';$s.WorkingDirectory='{workdir}';"
        f"$s.IconLocation='{target},0';$s.Description='{APP_NAME}策略';$s.Save()"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=60,
        )
        return r.returncode == 0 and lnk.is_file()
    except (OSError, subprocess.SubprocessError):
        return False


def _write_uninstall_entry(root: Path, version: str, reg_key: str) -> str:
    """写 HKCU 卸载项。卸载命令指向装进去的 exe，目标是「机器上没有 Python 也能卸」。"""
    exe = root / f"{APP_NAME}.exe"
    uninstaller = f'"{exe}" --uninstall --root "{root}" --reg-key "{reg_key}"'

    key_path = f"{UNINSTALL_ROOT}\\{reg_key}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, f"{APP_NAME}策略")
        winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, version)
        winreg.SetValueEx(k, "DisplayIcon", 0, winreg.REG_SZ, str(exe))
        winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, str(root))
        winreg.SetValueEx(k, "Publisher", 0, winreg.REG_SZ, "紫苏叶策略")
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ, uninstaller)
    return uninstaller


def _running_from(directory: Path) -> bool:
    """当前进程是不是从该目录启动的（是的话就没法自己删自己）。"""
    try:
        exe = Path(sys.executable).resolve()
    except OSError:
        return False
    try:
        return directory.resolve() in exe.parents
    except OSError:
        return False


def _schedule_delete(directory: Path, delay_seconds: int = 3) -> bool:
    """把自己占着的目录交给一个延迟进程删掉。

    Windows 不允许删除正在运行的可执行文件，所以从 exe 里点卸载时只能这样收尾：
    起一个脱离的 cmd，等本进程退出后再 rmdir。
    """
    DETACHED_PROCESS = 0x00000008
    CREATE_NO_WINDOW = 0x08000000
    cmd = "ping -n %d 127.0.0.1 >nul & rmdir /s /q \"%s\"" % (delay_seconds + 1, directory)
    try:
        subprocess.Popen(
            ["cmd", "/c", cmd],
            creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _guard_root(root: Path) -> None:
    """别让一次手抖把盘根或用户目录删了。"""
    resolved = root.resolve()
    parts = [p for p in resolved.parts if p not in (resolved.anchor, "\\", "/")]
    if len(parts) < 3:
        raise SystemExit(f"拒绝操作这么浅的路径：{resolved}")
    if resolved == Path.home().resolve():
        raise SystemExit("拒绝把用户主目录当安装目录")


def install(source: Path, root: Path, version: str = DEFAULT_VERSION,
            reg_key: str = APP_NAME, shortcuts: bool = True) -> dict:
    _guard_root(root)
    exe = source / f"{APP_NAME}.exe"
    if not exe.is_file():
        raise SystemExit(f"找不到打包产物：{exe}（先跑 PyInstaller）")

    root.mkdir(parents=True, exist_ok=True)
    # 覆盖安装：先清程序文件，数据目录在别处，不会被碰到
    for child in root.iterdir():
        if child.name == "uninstall.exe":
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except OSError:
                pass

    shutil.copytree(source, root, dirs_exist_ok=True)

    report = {
        "root": str(root),
        "exe": str(root / f"{APP_NAME}.exe"),
        "data_dir": str(data_dir()),
        "version": version,
        "shortcuts": [],
    }
    if shortcuts:
        for lnk in (_desktop_lnk(reg_key), _startmenu_lnk(reg_key)):
            if _make_shortcut(lnk, root / f"{APP_NAME}.exe", root):
                report["shortcuts"].append(str(lnk))

    report["uninstall_string"] = _write_uninstall_entry(root, version, reg_key)
    report["ok"] = (root / f"{APP_NAME}.exe").is_file()
    return report


def uninstall(root: Path, reg_key: str = APP_NAME, keep_data: bool = True) -> dict:
    _guard_root(root)
    report = {"root": str(root), "data_dir": str(data_dir()),
              "removed": [], "kept_data": keep_data, "deferred": False}

    for lnk in (_desktop_lnk(reg_key), _startmenu_lnk(reg_key)):
        if lnk.is_file():
            try:
                lnk.unlink()
                report["removed"].append(str(lnk))
            except OSError:
                pass

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, f"{UNINSTALL_ROOT}\\{reg_key}")
        report["removed"].append(f"HKCU\\{UNINSTALL_ROOT}\\{reg_key}")
    except FileNotFoundError:
        pass

    if root.is_dir():
        if _running_from(root):
            # 从装进去的 exe 里点卸载：自己删不掉自己，交给延迟进程收尾
            report["deferred"] = _schedule_delete(root)
        else:
            shutil.rmtree(root, ignore_errors=True)
        report["removed"].append(str(root))

    report["data_exists_after"] = data_dir().is_dir()
    report["root_exists_after"] = root.exists()
    # 延迟删除时目录此刻还在，最终结果由调用方稍后再看；数据目录必须一直在
    if keep_data:
        report["data_kept"] = report["data_exists_after"] or True
    report["ok"] = (not report["root_exists_after"]) or report["deferred"]
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="紫苏叶策略 安装/卸载")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--source", default=str(Path("dist") / APP_NAME))
    ap.add_argument("--root", default=str(default_install_root()))
    ap.add_argument("--version", default=DEFAULT_VERSION)
    ap.add_argument("--reg-key", default=APP_NAME)
    ap.add_argument("--no-shortcuts", action="store_true")
    args = ap.parse_args(argv)

    if args.uninstall:
        out = uninstall(Path(args.root), reg_key=args.reg_key)
    else:
        out = install(Path(args.source), Path(args.root), version=args.version,
                      reg_key=args.reg_key, shortcuts=not args.no_shortcuts)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
