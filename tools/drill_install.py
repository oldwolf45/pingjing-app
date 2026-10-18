"""装机演练：装 → 开 → 双击第二次 → 卸 → 重装，全程自动跑一遍。

对应 PRD 验收第 1、3、4 条：
    1  装完有图标、双击出窗口、全中文
    3  卸载后重装，数据一条不少
    4  双击第二次只激活已有窗口，不新开进程

为什么不手工点：手工点只证明「这次能过」，改一行启动代码就未必了。
这里每一步都有判据，任何一步退化成白屏/多进程/丢数据都会直接红。

所有用户目录（LOCALAPPDATA / APPDATA / USERPROFILE）都重定向到临时目录，
不会往真实桌面上丢快捷方式，也不会碰真实数据。

用法：python tools/drill_install.py
退出码 0 = 全过。
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import winreg
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE_NAME = "紫苏叶.exe"
APP_NAME = "紫苏叶"
REG_KEY = "紫苏叶_演习"
DATA_INSTALLER = ROOT / "installer" / "install.py"
DIST = ROOT / "dist" / APP_NAME

user32 = ctypes.windll.user32
EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def windows_of(pid: int) -> list[tuple[int, str, bool]]:
    out: list[tuple[int, str, bool]] = []

    def cb(hwnd, _lp):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            out.append((int(hwnd), buf.value, bool(user32.IsWindowVisible(hwnd))))
        return True

    user32.EnumWindows(cb, 0)
    return out


def opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def wait_ready(data_dir: Path, timeout: float = 90.0) -> dict:
    """等 runtime.json + /health 就绪。返回 {ok, port, seconds, error}。"""
    started = time.time()
    runtime = data_dir / "runtime.json"
    while time.time() - started < timeout:
        if runtime.is_file():
            try:
                info = json.loads(runtime.read_text(encoding="utf-8"))
                port = int(info["port"])
                with opener().open(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                    if json.loads(r.read().decode()).get("ok") is True:
                        return {"ok": True, "port": port, "pid": info.get("pid"),
                                "seconds": round(time.time() - started, 2)}
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.25)
    return {"ok": False, "seconds": round(time.time() - started, 2),
            "error": "等不到 runtime.json / health"}


def wait_window(pid: int, timeout: float = 30.0) -> dict:
    started = time.time()
    while time.time() - started < timeout:
        wins = [w for w in windows_of(pid) if w[2] and w[1]]
        if wins:
            title = wins[0][1]
            return {"ok": "紫苏叶" in title, "title": title,
                    "seconds": round(time.time() - started, 2)}
        time.sleep(0.2)
    return {"ok": False, "title": "", "error": "没有可见窗口"}


def get_health(port: int) -> bool:
    try:
        with opener().open(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            return json.loads(r.read().decode()).get("ok") is True
    except Exception:  # noqa: BLE001
        return False


def registry_entry() -> dict | None:
    key = f"Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{REG_KEY}"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            out = {}
            for name in ("DisplayName", "DisplayVersion", "InstallLocation", "UninstallString"):
                try:
                    out[name] = winreg.QueryValueEx(k, name)[0]
                except FileNotFoundError:
                    out[name] = None
            return out
    except FileNotFoundError:
        return None


def run_installer(args: list[str], env: dict) -> dict:
    r = subprocess.run([sys.executable, str(DATA_INSTALLER), *args],
                       capture_output=True, text=True, timeout=300, env=env)
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {"ok": False, "stdout": r.stdout[-800:], "stderr": r.stderr[-800:]}


def seed_marker(db: Path, code: str = "600519") -> None:
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("INSERT OR IGNORE INTO stocks(code, name, market) VALUES (?,?,?)",
                     (code, "演练票", "sh"))
        conn.execute(
            "INSERT INTO app_settings(key, value, updated_at) VALUES ('drill','1',"
            "datetime('now','localtime')) ON CONFLICT(key) DO UPDATE SET value='1'")
        conn.commit()
    finally:
        conn.close()


def marker_present(db: Path, code: str = "600519") -> bool:
    if not db.is_file():
        return False
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT COUNT(*) FROM stocks WHERE code=?", (code,)).fetchone()
        return bool(row and row[0] == 1)
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def main() -> int:
    checks: dict[str, bool] = {}
    info: dict = {}

    exe_src = DIST / EXE_NAME
    if not exe_src.is_file():
        print(json.dumps({"ok": False, "error": f"没有打包产物：{exe_src}"},
                         ensure_ascii=False, indent=2))
        return 1

    work = Path(tempfile.mkdtemp(prefix="zisuye-drill-"))
    local = work / "local"
    roaming = work / "roaming"
    profile = work / "profile"
    for p in (local, roaming, profile, profile / "Desktop"):
        p.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env.update(LOCALAPPDATA=str(local), APPDATA=str(roaming), USERPROFILE=str(profile))
    env.pop("ZISUYE_DATA_DIR", None)      # 让数据目录跟着 LOCALAPPDATA 走
    env.pop("ZISUYE_REMOTE_DEBUG", None)

    install_root = work / "Programs" / "ZiSuYe"
    data_dir = local / "ZiSuYe"
    db = data_dir / "zisuye.db"
    desktop_lnk = profile / "Desktop" / f"{REG_KEY}.lnk"
    startmenu_lnk = (roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs"
                     / f"{REG_KEY}.lnk")

    info["work"], info["install_root"], info["data_dir"] = str(work), str(install_root), str(data_dir)

    proc: subprocess.Popen | None = None

    def stop(p: subprocess.Popen | None, timeout: float = 15.0) -> None:
        if p is None:
            return
        if p.poll() is None:
            p.terminate()
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout=5)

    try:
        # ---------------------------------------------------------- 装
        rep = run_installer(["--source", str(DIST), "--root", str(install_root),
                             "--reg-key", REG_KEY, "--version", "0.1.0"], env)
        info["install"] = rep
        checks["install_ok"] = bool(rep.get("ok"))
        exe = install_root / EXE_NAME
        checks["install_exe_present"] = exe.is_file()
        checks["install_resources_present"] = (
            install_root / "_internal" / "frontend" / "dist" / "index.html").is_file()
        checks["install_desktop_icon"] = desktop_lnk.is_file()
        checks["install_startmenu_icon"] = startmenu_lnk.is_file()

        reg = registry_entry()
        info["registry"] = reg
        checks["install_registry_entry"] = bool(reg and reg.get("DisplayName"))
        checks["install_uninstall_points_to_exe"] = bool(
            reg and reg.get("UninstallString") and EXE_NAME in reg["UninstallString"]
            and "--uninstall" in reg["UninstallString"])

        # ---------------------------------------------------------- 开
        proc = subprocess.Popen([str(exe)], env=env, cwd=str(install_root))
        ready = wait_ready(data_dir)
        info["first_launch"] = ready
        checks["launch_backend_ready"] = ready["ok"]

        port = int(ready.get("port") or 0)
        win = wait_window(proc.pid) if port else {"ok": False}
        info["first_window"] = win
        checks["launch_window_visible"] = bool(win.get("ok"))
        checks["launch_window_chinese_title"] = "紫苏叶策略" in (win.get("title") or "")
        info["startup_seconds"] = round((ready.get("seconds") or 0)
                                        + (win.get("seconds") or 0), 2)
        checks["launch_data_dir_created"] = db.is_file()

        # ------------------------------------------- 双击第二次：不能新开进程
        t0 = time.time()
        second = subprocess.run([str(exe)], env=env, cwd=str(install_root),
                                capture_output=True, text=True, timeout=90)
        elapsed = round(time.time() - t0, 2)
        info["second_launch"] = {"returncode": second.returncode, "seconds": elapsed,
                                 "stdout": (second.stdout or "").strip()[:300]}
        checks["second_instance_exits_zero"] = second.returncode == 0
        checks["second_instance_exits_fast"] = elapsed < 30
        checks["second_instance_keeps_first_alive"] = get_health(port) if port else False
        checks["second_instance_reports_single"] = "single_instance" in (second.stdout or "")

        # ------------------------------------------------------ 数据标记
        seed_marker(db)
        checks["data_marker_before_uninstall"] = marker_present(db)

        # ---------------------------------------------------------- 卸
        stop(proc)
        proc = None
        time.sleep(1.0)

        rep = run_installer(["--uninstall", "--root", str(install_root),
                             "--reg-key", REG_KEY], env)
        info["uninstall"] = rep
        checks["uninstall_ok"] = bool(rep.get("ok"))
        checks["uninstall_program_files_gone"] = not install_root.exists()
        checks["uninstall_desktop_icon_gone"] = not desktop_lnk.exists()
        checks["uninstall_registry_gone"] = registry_entry() is None
        checks["uninstall_kept_data"] = db.is_file() and marker_present(db)

        # ------------------------------------------------------- 重装
        rep = run_installer(["--source", str(DIST), "--root", str(install_root),
                             "--reg-key", REG_KEY, "--version", "0.1.0"], env)
        info["reinstall"] = rep
        checks["reinstall_ok"] = bool(rep.get("ok"))

        proc = subprocess.Popen([str(exe)], env=env, cwd=str(install_root))
        ready2 = wait_ready(data_dir)
        info["second_start"] = ready2
        checks["reinstall_launch_ready"] = ready2["ok"]
        checks["data_survives_reinstall"] = marker_present(db)

    except Exception as exc:  # noqa: BLE001
        info["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        stop(proc)
        time.sleep(0.5)
        try:
            run_installer(["--uninstall", "--root", str(install_root),
                           "--reg-key", REG_KEY], env)
        except Exception:  # noqa: BLE001
            pass
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER,
                             f"Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{REG_KEY}")
        except FileNotFoundError:
            pass
        shutil.rmtree(work, ignore_errors=True)

    info["checks"] = checks
    info["ok"] = all(checks.values()) and "error" not in info
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0 if info["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
