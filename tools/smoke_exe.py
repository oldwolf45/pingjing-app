"""打包后的 exe 冒烟测试：真启动进程、真检测窗口、真请求后端。

这不是"文件存在就算过"。判据：
  1. exe 进程活着（没崩）
  2. runtime.json 出现 → 后端起来了
  3. GET /health 返回 ok
  4. GET / 返回含 id="app" 的页面
  5. 系统里有该进程的可见窗口（EnumWindows 按 pid 过滤）

用法：python tools/smoke_exe.py
退出码 0 = 全过。
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / "紫苏叶" / "紫苏叶.exe"

user32 = ctypes.windll.user32
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def windows_of_pid(pid: int) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []

    def cb(hwnd, _lp):
        wpid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == pid and user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            found.append((int(hwnd), buf.value))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def main() -> int:
    result: dict = {"exe": str(EXE)}
    if not EXE.exists():
        print(f"找不到 exe：{EXE}")
        return 2

    data_dir = ROOT / ".smoke_data"
    if data_dir.exists():
        shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    result["data_dir"] = str(data_dir)

    env = dict(os.environ)
    env["ZISUYE_DATA_DIR"] = str(data_dir)

    proc = subprocess.Popen([str(EXE)], env=env, cwd=str(EXE.parent))
    result["pid"] = proc.pid

    try:
        runtime_file = data_dir / "runtime.json"
        deadline = time.time() + 40
        info = None
        while time.time() < deadline:
            if proc.poll() is not None:
                result["error"] = f"进程提前退出，code={proc.returncode}"
                break
            if runtime_file.exists():
                try:
                    info = json.loads(runtime_file.read_text(encoding="utf-8"))
                    break
                except json.JSONDecodeError:
                    pass
            time.sleep(0.3)

        if info:
            result["runtime"] = info
            port = info["port"]
            base = f"http://127.0.0.1:{port}"

            for _ in range(40):
                try:
                    r = httpx.get(base + "/health", timeout=2)
                    if r.status_code == 200:
                        result["health"] = r.json()
                        break
                except Exception:
                    time.sleep(0.3)

            r = httpx.get(base + "/", timeout=5)
            result["index_status"] = r.status_code
            result["index_has_app"] = 'id="app"' in r.text
            result["index_has_title"] = "紫苏叶" in r.text

            # 等窗口出现
            wins: list[tuple[int, str]] = []
            for _ in range(40):
                wins = windows_of_pid(proc.pid)
                if wins:
                    break
                time.sleep(0.3)
            result["windows"] = [w[1] for w in wins]
            result["has_window"] = bool(wins)
            result["window_title_ok"] = any("紫苏叶" in (w[1] or "") for w in wins)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    ok = (
        result.get("health", {}).get("ok") is True
        and result.get("index_status") == 200
        and result.get("index_has_app") is True
        and result.get("has_window") is True
        and result.get("window_title_ok") is True
    )
    result["ok"] = bool(ok)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
