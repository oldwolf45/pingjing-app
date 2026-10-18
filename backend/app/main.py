"""应用入口：起 FastAPI（只绑 127.0.0.1 随机端口）+ pywebview 窗口。

两个特殊入口：
  --selftest  自检并写 selftest.json 后退出（打包后验证资源与数据目录用）
  --port N    指定端口（默认随机）
启动时会把运行信息写到 <数据目录>/runtime.json，退出时删除。
这样外部工具（冒烟脚本、单实例检测）能知道窗口开在哪个端口。
"""

from __future__ import annotations

import atexit
import json
import os
import socket
import sys
import threading
import time
from datetime import datetime

import uvicorn
from fastapi import FastAPI

from app import paths

APP_TITLE = "紫苏叶策略"
APP_VERSION = "0.1.0"
WINDOW_SIZE = (1200, 800)
MIN_SIZE = (1000, 680)
RUNTIME_FILE = "runtime.json"


class DesktopApi:
    """暴露给前端 JS 的桌面能力。第一版只需要一个"前端就绪"信号。"""

    def __init__(self) -> None:
        self.app_ready_payload: dict | None = None
        self.ready_at: str | None = None

    def app_ready(self, payload: dict | None = None) -> dict:
        # 前端渲染完成。生产环境写日志；冒烟测试用它判断窗口是否真的起来了。
        self.app_ready_payload = payload or {}
        self.ready_at = datetime.now().astimezone().isoformat(timespec="seconds")
        return {"ok": True, "version": APP_VERSION}


# 前端就绪状态。走 HTTP 而不是 pywebview 的 JS 桥：
# 6.2.1 的 edgechromium 后端在 on_navigation_completed 里只起线程注入桥脚本，
# 而注入本身要调 ExecuteScriptAsync，在这个组合下会卡住，桥永远不出现。
# HTTP 这条路上不依赖 webview 的任何回调，可靠得多。
READY_STATE: dict = {"ready": False, "payload": None, "at": None}


def create_app() -> FastAPI:
    READY_STATE.update(ready=False, payload=None, at=None)
    app = FastAPI(title=APP_TITLE, version=APP_VERSION, docs_url=None, redoc_url=None)

    # 上次进程退出时没跑完的更新，标记成中断，否则进度面板会一直停在半路
    try:
        from app.services.update_job import mark_interrupted
        mark_interrupted()
    except Exception:
        pass   # 库里还没有 jobs 表之类的情况，不影响启动

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "version": APP_VERSION}

    @app.get("/api/meta/paths")
    def meta_paths() -> dict:
        return paths.describe()

    @app.post("/api/meta/client-ready")
    def client_ready(payload: dict | None = None) -> dict:
        """前端挂载完成的自报。窗口冒烟测试靠它判断界面真的渲染出来了。"""
        READY_STATE.update(
            ready=True,
            payload=payload or {},
            at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        return {"ok": True}

    @app.get("/api/meta/ready")
    def meta_ready() -> dict:
        return dict(READY_STATE)

    @app.post("/api/meta/client-log")
    def client_log(payload: dict) -> dict:
        """前端异常上报。界面白屏时这里是唯一能查的线索。"""
        line = json.dumps(
            {**payload, "at": datetime.now().astimezone().isoformat(timespec="seconds")},
            ensure_ascii=False,
        )
        try:
            f = paths.log_dir() / "client.log"
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass
        return {"ok": True}

    from app.api import register as register_api

    register_api(app)

    # 静态资源（前端构建产物）挂在最后，避免盖住 /api 与 /health
    dist = paths.resource_dir()
    if dist.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=str(dist), html=True), name="static")
    else:

        @app.get("/")
        def placeholder() -> dict:
            return {"ok": True, "hint": "前端未构建：npm run build 后重启"}

    return app


def pick_port() -> int:
    """让系统分配一个空闲端口，避免固定端口冲突。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# 自检要点名的表。不数总数——每加一张表就得改一次数字，改漏了自检就假绿。
REQUIRED_TABLES = (
    "app_meta", "app_settings", "audit_log", "schema_migrations",
    "stocks", "stock_profiles",
    "pool_states", "pool_events",
    "score_runs", "score_snapshots", "barrier_snapshots",
    "financial_periods", "financial_manual", "sector_intel", "trading_calendar", "quotes",
    "recommendations", "recommendation_tracks", "recommendation_evaluations",
    "portfolios", "trades", "decision_notes",
    "data_sources", "source_calls", "jobs",
)


def start_server(port: int) -> uvicorn.Server:
    config = uvicorn.Config(
        create_app(),
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    t.start()
    # 等端口真正可连，避免窗口加载时后端还没起来
    for _ in range(100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.1)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return server
        time.sleep(0.05)
    raise RuntimeError("后端服务启动超时")


def _write_runtime(port: int) -> None:
    f = paths.data_dir() / RUNTIME_FILE
    payload = {
        "pid": os.getpid(),
        "port": port,
        "url": f"http://127.0.0.1:{port}/",
        "version": APP_VERSION,
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "frozen": paths.is_frozen(),
    }
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(f)
    atexit.register(_cleanup_runtime, f)


def _cleanup_runtime(f=None) -> None:
    target = f or (paths.data_dir() / RUNTIME_FILE)
    try:
        target.unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------- 单实例
#
# PRD 验收第 4 条：双击第二次只激活已有窗口，不新开进程。
# 两个进程同时开同一个库，虽然 WAL 撑得住，但池子判定和更新任务会打架，
# 所以第二次启动必须自己退出。
#
# 判据不是「runtime.json 存在」——上次崩溃会留下这个文件，
# 拿它当锁会让用户永远开不了。必须是「进程还活着 **且** 后端还应答」。


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(h)


def _health_ok(port: int, timeout: float = 1.0) -> bool:
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8")).get("ok") is True
    except Exception:  # noqa: BLE001
        return False


def _focus_window_by_pid(pid: int) -> bool:
    """把该进程的可见窗口调到前台。尽力而为：失败也不该拦住启动判断。"""
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    SW_RESTORE = 9
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lp):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            if user32.GetWindowTextLengthW(hwnd):
                found.append(int(hwnd))
        return True

    user32.EnumWindows(cb, 0)
    for hwnd in found:
        try:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def existing_instance() -> dict:
    """看有没有已经在跑的实例。返回 {alive, focused, pid, port, stale}。"""
    out = {"alive": False, "focused": False, "pid": None, "port": None, "stale": False}
    f = paths.data_dir() / RUNTIME_FILE
    if not f.is_file():
        return out
    try:
        info = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        out["stale"] = True
        return out

    pid = int(info.get("pid") or 0)
    port = int(info.get("port") or 0)
    out.update(pid=pid or None, port=port or None)
    if not pid or not port:
        out["stale"] = True
        return out

    # 自己的 pid 不算（重入同进程的测试场景）
    if pid != os.getpid() and _pid_alive(pid) and _health_ok(port):
        out["alive"] = True
        out["focused"] = _focus_window_by_pid(pid)
        return out

    out["stale"] = True
    try:
        f.unlink(missing_ok=True)  # 上次崩溃留下的，清掉，别让它挡住这次启动
    except OSError:
        pass
    return out


FAILURE_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>紫苏叶策略 · 界面加载失败</title>
<style>
  body { font: 14px/1.7 "Microsoft YaHei", system-ui, sans-serif; color: #2b2b33;
         background: #faf9fd; margin: 0; padding: 48px 56px; }
  h1 { font-size: 18px; margin: 0 0 8px; }
  p  { color: #55505f; margin: 6px 0; }
  code { background: #f0edf7; padding: 1px 5px; border-radius: 3px; font-size: 12px; }
  ul { color: #55505f; margin: 12px 0 0 18px; padding: 0; }
</style></head><body>
<h1>界面没能加载出来</h1>
<p>后端服务是正常的，是内嵌浏览器这次没有把页面渲染出来。</p>
<ul>
  <li>先关掉窗口重开一次，多数情况会恢复。</li>
  <li>数据目录：<code>%DATA_DIR%</code></li>
  <li>日志目录：<code>%LOG_DIR%</code>（<code>client.log</code> 里有前端上报）</li>
</ul>
</body></html>
"""


def _wait_frontend_ready(seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if READY_STATE.get("ready"):
            return True
        time.sleep(0.25)
    return False


def start_frontend_watchdog(window, url: str) -> threading.Thread:
    """前端迟迟不自报就绪时重载一次，还不行就显示可读的失败页。

    内嵌浏览器在某些会话里会把首次导航丢掉，用户看到的就是一块纯白。
    与其让人对着白屏猜，不如自己重试一次，失败后把数据目录和日志路径摆出来。

    pywebview 的 load_url / load_html 都被事件门控（shown / loaded /
    _pywebviewready），而这些事件本身就是出问题的那一环，所以这里先把事件
    置位再调用，避免看门狗自己也卡在 wait(20) 上。
    """
    def force_events() -> None:
        for name in ("shown", "loaded", "before_load", "_pywebviewready"):
            ev = getattr(window.events, name, None)
            if ev is not None and not ev.is_set():
                ev.set()

    def run() -> None:
        if _wait_frontend_ready(20):
            return
        force_events()
        try:
            window.load_url(url)
        except Exception:  # noqa: BLE001
            pass
        if _wait_frontend_ready(25):
            return
        try:
            window.load_html(
                FAILURE_HTML.replace("%DATA_DIR%", str(paths.data_dir()))
                            .replace("%LOG_DIR%", str(paths.log_dir()))
            )
        except Exception:  # noqa: BLE001
            pass

    t = threading.Thread(target=run, name="frontend-watchdog", daemon=True)
    t.start()
    return t


def disable_js_bridge() -> None:
    """跳过 pywebview 的 JS 桥注入（默认不启用，留作排查开关）。

    背景：pywebview 6.2.1 的 edgechromium 后端在导航完成时起线程注入桥脚本，
    注入内部用 WebView2.ExecuteScriptAsync + Control.Invoke 回 UI 线程。
    在无可见桌面的会话里这条链路会卡住，loaded / _pywebviewready 永不触发，
    evaluate_js 一律抛 'Main window failed to start'。

    但实测把这个注入跳掉之后行为没有变化（页面照样不挂载），
    说明卡住的不是它一个。所以默认保持 pywebview 原样，不用这个开关，
    免得关掉自己本来就需要的能力。
    """
    from webview.platforms import edgechromium

    cls = edgechromium.EdgeChrome
    if getattr(cls, "_zisuye_bridge_disabled", False):
        return

    def on_navigation_completed(self, sender, _args):
        self.url = None if self.ishtml else str(sender.Source)
        self.pywebview_window.events.loaded.set()

    cls.on_navigation_completed = on_navigation_completed
    cls._zisuye_bridge_disabled = True


def run_window(api: DesktopApi | None = None, title: str = APP_TITLE,
               port: int | None = None) -> None:
    """起后端 + 开窗口。"""
    import webview  # 延迟导入：跑测试时不需要 GUI

    # 自动化测试要拿真实 DOM，只能走 CDP。默认不开放端口。
    debug_port = os.environ.get("ZISUYE_REMOTE_DEBUG")
    if debug_port:
        webview.settings["REMOTE_DEBUGGING_PORT"] = int(debug_port)

    api = api or DesktopApi()
    port = port or pick_port()
    start_server(port)
    _write_runtime(port)
    url = f"http://127.0.0.1:{port}/"
    window = webview.create_window(
        title,
        url,
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=MIN_SIZE,
        text_select=True,
        js_api=api,
    )
    start_frontend_watchdog(window, url)
    webview.start(gui="edgechromium", debug=bool(os.environ.get("ZISUYE_DEBUG")))
    return window


def run_selftest() -> int:
    """打包后的自检：资源是否打进去、数据目录能不能写、算法指纹是否匹配。

    不依赖任何外部服务，写完 <数据目录>/selftest.json 就退出。
    判据：ok=true 才算打包成功。
    """
    from app import legacy_bridge
    from app.db import session
    from app.services import migrator

    result: dict = {
        "frozen": paths.is_frozen(),
        "version": APP_VERSION,
        "paths": paths.describe(),
        "checks": {},
    }

    try:
        dist = paths.resource_dir()
        result["checks"]["frontend_dist"] = (dist / "index.html").is_file()
        result["checks"]["schema_sql"] = (dist.parent.parent / "app" / "db" / "schema.sql").is_file() \
            if paths.is_frozen() else True

        # 数据目录可写
        probe = paths.data_dir() / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        result["checks"]["data_dir_writable"] = True

        # 数据库能建表
        conn = session.connect()
        session.init_schema(conn)
        migrator.run_migrations(conn)
        have = set(session.table_names(conn))
        missing = sorted(set(REQUIRED_TABLES) - have)
        result["checks"]["db_tables"] = not missing
        result["missing_tables"] = missing
        result["db_path"] = str(paths.db_path())
        conn.close()

        # 算法指纹
        integ = legacy_bridge.verify_integrity()
        result["checks"]["algo_integrity"] = integ["ok"]
        result["algo_version"] = integ["version"]
        result["algo_problems"] = integ["problems"]

        mods = legacy_bridge.load()
        result["checks"]["algo_import"] = bool(mods.get("scoring"))
        rebound = legacy_bridge.check_paths()
        result["checks"]["algo_paths_inside"] = all(r["inside_data_dir"] for r in rebound)
        result["rebound"] = rebound
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"

    result["ok"] = bool(result["checks"]) and all(result["checks"].values()) and "error" not in result

    out = paths.data_dir() / "selftest.json"
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out)
    return 0 if result["ok"] else 1


def main() -> None:
    if "--selftest" in sys.argv:
        sys.exit(run_selftest())

    # 控制面板点「卸载」走的是这里：命令必须是装进去的 exe 自己，
    # 目标机器上没有 Python，指向 .py 的卸载命令等于没有。
    if "--uninstall" in sys.argv:
        from app.services import installer

        # 把自己这个标记补回去：切掉之后 argparse 就不知道要卸载了
        rest = sys.argv[sys.argv.index("--uninstall") + 1:]
        sys.exit(installer.main(["--uninstall", *rest]))

    # 已经有窗口在跑：把它调到前台，自己直接退出（PRD 验收第 4 条）
    if "--allow-multi" not in sys.argv:
        ex = existing_instance()
        if ex["alive"]:
            print(json.dumps({"single_instance": True, **ex}, ensure_ascii=False))
            sys.exit(0)

    port = None
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    run_window(port=port)


if __name__ == "__main__":
    main()
