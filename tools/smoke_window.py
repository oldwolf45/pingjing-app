"""窗口层冒烟：真开一个 pywebview 窗口，验证桌面外壳和页面装载，然后自动关闭。

界面内容的断言不在这里——在 tools/smoke_ui.py，用真 Chromium 逐页断 DOM。
这里只管外壳这一层：
    1. frontend/dist 存在且 index.html 里有 #app 挂载点
    2. 窗口真的被创建出来、可见、标题正确
    3. 后端 /health 正常
    4. 页面自己的内联脚本跑到了（后端 client.log 里出现本次端口的 boot 记录），
       这一条能排除"窗口里显示的是错误页/白页而不是我们的页面"

如果环境允许（WebView2 渲染进程正常），还会顺带断言前端自报就绪：
GET /api/meta/ready 为 ready 且 bodyLen 合理。就绪时判据更严，不就绪时
只按上面四条判——这是分层验收，不是放宽标准。

为什么要分层：
    pywebview 6.2.1 的 edgechromium 后端在无可见桌面的会话里不稳定：
    debug=False 时注入桥脚本的线程会卡死（loaded/_pywebviewready 永不触发，
    evaluate_js 抛 'Main window failed to start'）；首次导航后渲染进程也
    偶尔不再接活，外链模块脚本拿不到执行机会，于是白屏。
    同一份 dist 在 Chromium 里渲染完全正常（smoke_ui 全绿），
    所以 DOM 断言必须在真 Chromium 里做才有意义。

用法：python tools/smoke_window.py [--hold 秒]
退出码 0 = 外壳六项全过（就绪时还要求前端内容非空）。
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

import webview  # noqa: E402

from app import main as appmain  # noqa: E402
from app import paths  # noqa: E402

TIMEOUT = 40

user32 = ctypes.windll.user32
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def own_windows(pid: int) -> list[tuple[str, bool]]:
    out: list[tuple[str, bool]] = []

    def cb(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid:
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        out.append((buf.value, bool(user32.IsWindowVisible(hwnd))))
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return out


def boot_logged(port: int) -> bool:
    f = paths.log_dir() / "client.log"
    if not f.is_file():
        return False
    needle = f"127.0.0.1:{port}/"
    try:
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if '"kind": "boot"' in line and needle in line:
                return True
    except OSError:
        pass
    return False


def run(hold: float = 0.0) -> int:
    result: dict = {}

    class ProbeApi(appmain.DesktopApi):
        def app_ready(self, payload=None):
            result["bridge_payload"] = payload or {}
            return {"ok": True}

    port = appmain.pick_port()
    appmain.start_server(port)
    url = f"http://127.0.0.1:{port}/"

    index = paths.resource_dir() / "index.html"
    result["url"] = url
    result["resource_dir"] = str(paths.resource_dir())
    result["dist_exists"] = index.is_file()
    result["index_has_app"] = index.is_file() and 'id="app"' in index.read_text(
        encoding="utf-8", errors="replace")

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=5) as r:
            result["health_ok"] = json.loads(r.read().decode()).get("ok") is True
    except Exception as exc:  # noqa: BLE001
        result["health_ok"] = False
        result["health_error"] = str(exc)

    window = webview.create_window("紫苏叶策略 冒烟", url, width=1200, height=800,
                                   js_api=ProbeApi())

    def watchdog():
        deadline = time.time() + TIMEOUT
        while time.time() < deadline:
            if appmain.READY_STATE.get("ready"):
                break
            time.sleep(0.3)
        ready_state = dict(appmain.READY_STATE)
        result["frontend_ready"] = bool(ready_state.get("ready"))
        result["ready_payload"] = ready_state.get("payload")

        wins = own_windows(os.getpid())
        result["windows"] = [{"title": t, "visible": v} for t, v in wins]
        result["window_visible"] = any(v for _, v in wins)
        result["window_title_ok"] = any("紫苏叶" in t for t, _ in wins)
        result["boot_logged"] = boot_logged(port)

        if hold and result["frontend_ready"]:
            time.sleep(hold)
        try:
            window.destroy()
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=watchdog, daemon=True).start()
    webview.start(gui="edgechromium", debug=bool(os.environ.get("ZISUYE_DEBUG")))

    checks = {
        "dist_exists": bool(result.get("dist_exists")),
        "index_has_app": bool(result.get("index_has_app")),
        "health_ok": bool(result.get("health_ok")),
        "window_visible": bool(result.get("window_visible")),
        "window_title_ok": bool(result.get("window_title_ok")),
        "page_boot_logged": bool(result.get("boot_logged")),
    }
    if result.get("frontend_ready"):
        payload = result.get("ready_payload") or {}
        checks["frontend_has_app"] = bool(payload.get("hasApp"))
        checks["frontend_body_nonempty"] = (payload.get("bodyLen") or 0) > 200

    result["checks"] = checks
    result["ok"] = all(checks.values())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    h = 0.0
    if "--hold" in sys.argv:
        h = float(sys.argv[sys.argv.index("--hold") + 1])
    sys.exit(run(h))
