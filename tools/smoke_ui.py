"""界面冒烟：起真实后端，用真实 Chromium 打开构建产物，逐页断 DOM。

为什么不用 pywebview 的窗口来测：
  pywebview 6.2.1 的 edgechromium 后端在本机（无可见桌面的会话）有两个硬伤：
    1. debug=False 时注入桥脚本的线程会卡死，loaded / _pywebviewready 永不触发，
       evaluate_js 一律抛 'Main window failed to start'
    2. 渲染进程偶尔在首次导航后不再接活，外链模块脚本拿不到执行机会 → 白屏
  这是环境层面的问题，不是前端代码问题（同一份 dist 在 Chromium 里渲染完全正常）。
  所以 DOM 断言放在真 Chromium 里跑，稳定可重复；窗口层由 tools/smoke_window.py 负责。

检查项（对应 DEV_PLAN T3.1~T3.7）：
  仪表盘   → 五个池子卡片、名额文案、排行有行
  股票池   → 6 个 tab、候选池 2 行、储备池 15 行
  个股详情 → 总分、六维拆解、雷达 canvas、操作参数、壁垒明细
  其余页面 → 标题都在
  顶栏搜索 → 能搜到广立微

用法：python tools/smoke_ui.py
退出码 0 = 全过。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

SMOKE_DATA = ROOT / ".smoke_ui_data"
os.environ["ZISUYE_DATA_DIR"] = str(SMOKE_DATA)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(k, None)

SOURCE_ROOT = Path("D:/云吞下载/紫苏叶跟踪")

import requests  # noqa: E402
from websocket import create_connection  # noqa: E402

from app import legacy_bridge  # noqa: E402
from app import main as appmain  # noqa: E402
from app.db import session  # noqa: E402
from app.services import importer, migrator  # noqa: E402

SESSION = requests.Session()
SESSION.trust_env = False
results: dict = {}

EDGE_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]


# ----------------------------------------------------------------------
# 数据准备
# ----------------------------------------------------------------------
def bootstrap() -> dict:
    session.reset_connections()
    legacy_bridge.reset()
    from app.services import intel
    intel._patched = False

    conn = session.connect()
    session.init_schema(conn)
    migrator.run_migrations(conn)

    n = conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
    info = {"data_dir": str(SMOKE_DATA), "imported": False, "stocks_before": n}
    if n == 0:
        if not (SOURCE_ROOT / "data" / "tracking_state.json").exists():
            raise SystemExit(f"没有现成数据，且找不到旧项目源数据：{SOURCE_ROOT}")
        report = importer.run_full_import(conn, SOURCE_ROOT, algo_version="v1+63356068")
        info["imported"] = True
        info["parse_failures"] = len(report.parse_failures)
        info["stocks_after"] = report.final_counts.get("stocks")

    info["demo_position"] = seed_demo_position(conn)
    conn.close()
    results["bootstrap"] = info
    return info


def seed_demo_position(conn) -> dict | None:
    """给持仓页造一条可核对的持仓样例（冒烟夹具，不是业务数据）。

    为什么需要：持仓表在空仓时整块不渲染，仓位占比和超限红标就没法断。
    只造一次，之后跑冒烟复用同一条，不会越积越多。
    """
    from app.services import portfolio

    exists = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    if exists:
        return None
    row = conn.execute(
        """SELECT s.code FROM stocks s JOIN pool_states p ON p.code=s.code
            WHERE s.status <> 'delisted' ORDER BY s.code LIMIT 1""").fetchone()
    if not row:
        return None
    code = row["code"]
    pid = portfolio.ensure_default(conn)["id"]
    portfolio.add_trade(conn, portfolio_id=pid, code=code, side="BUY",
                        shares=1000, price_cents=1000, fee_cents=500,
                        trade_date="2026-01-05", note="冒烟夹具")
    # 现价 20 元、现金 0 → 单票占比 100%，用来验证「超 10%」的渲染
    conn.execute(
        """INSERT INTO quotes(code, price, quoted_at) VALUES (?,?,?)
           ON CONFLICT(code) DO UPDATE SET price=excluded.price,
               quoted_at=excluded.quoted_at""",
        (code, 20.0, "2026-10-17T15:00:00+08:00"))
    portfolio.set_cash(conn, pid, 0)
    conn.commit()
    return {"code": code, "shares": 1000, "costCents": 1_000_500, "price": 20.0}


# ----------------------------------------------------------------------
# 极简 CDP 客户端
# ----------------------------------------------------------------------
class Cdp:
    def __init__(self, ws_url: str) -> None:
        self.ws = create_connection(ws_url, timeout=20)
        self._id = 0

    def send(self, method: str, params: dict | None = None, timeout: float = 20):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method} -> {msg['error']}")
                return msg.get("result")
        raise TimeoutError(method)

    def eval(self, expr: str, timeout: float = 20):
        res = self.send(
            "Runtime.evaluate",
            {"expression": expr, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        if res.get("exceptionDetails"):
            det = res["exceptionDetails"]
            raise RuntimeError(det.get("exception", {}).get("description") or str(det))
        return (res.get("result") or {}).get("value")

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def find_edge() -> Path:
    for p in EDGE_CANDIDATES:
        if p.is_file():
            return p
    raise SystemExit("找不到 msedge.exe，无法跑界面冒烟")


def launch_edge(edge: Path, debug_port: int, profile: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [
            str(edge), "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", "--disable-extensions",
            "--window-size=1400,900",
            f"--remote-debugging-port={debug_port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={profile}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_target(debug_port: int, timeout: float = 25) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            items = SESSION.get(f"http://127.0.0.1:{debug_port}/json/list", timeout=2).json()
            for it in items:
                if it.get("type") == "page" and it.get("webSocketDebuggerUrl"):
                    return it["webSocketDebuggerUrl"]
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    raise SystemExit("Chromium 调试端口没起来")


# ----------------------------------------------------------------------
# 断言脚本
# ----------------------------------------------------------------------
DASHBOARD_JS = """(function () {
    var body = document.body.innerText;
    var cards = [].slice.call(document.querySelectorAll('.pool-card'))
        .map(function (c) { return c.innerText.replace(/\\n/g, '|'); });
    return {
      cards: cards,
      rowButtons: document.querySelectorAll('.card .row-btn').length,
      hasOverview: body.indexOf('池子总览') >= 0,
      hasCapacity: body.indexOf('名额') >= 0,
      bodyLen: body.length,
    };
})()"""

POOLS_JS = """(function () {
    var tabs = [].slice.call(document.querySelectorAll('.tab'))
        .map(function (t) { return t.innerText.replace(/\\n/g, ' ').trim(); });
    var rows = document.querySelectorAll('table.tbl tbody tr').length;
    var first = document.querySelector('table.tbl tbody tr');
    return { tabs: tabs, rows: rows,
             firstRow: first ? first.innerText.replace(/\\n/g, '|') : null };
})()"""

DETAIL_JS = """(function () {
    var txt = document.body.innerText;
    var dims = [].slice.call(document.querySelectorAll('.dim'))
        .map(function (d) { return d.innerText.replace(/\\n/g, ' '); });
    return {
      hasTotal: /\\/ 9\\.0/.test(txt),
      totalText: (document.querySelector('.total') || {}).innerText || '',
      dimCount: dims.length,
      dims: dims,
      radarCanvas: document.querySelectorAll('.radar canvas').length,
      hasParams: txt.indexOf('操作参数') >= 0,
      hasBarrier: txt.indexOf('壁垒明细') >= 0,
      bodyLen: txt.length,
    };
})()"""

SEARCH_JS = """(async function () {
    var inp = document.querySelector('.search-box input');
    if (!inp) return { items: [], reason: 'no search box' };
    inp.value = '\\u5e7f\\u7acb';
    inp.dispatchEvent(new Event('input', {bubbles: true}));
    await new Promise(function (r) { setTimeout(r, 900); });
    var items = [].slice.call(document.querySelectorAll('.search-pop button'))
        .map(function (b) { return b.innerText.replace(/\\n/g, '|'); });
    return { items: items };
})()"""

# 进度面板：只断 DOM，不点「更新数据」——那会真的去抓数据，冒烟不该依赖外网。
# 改成往 jobs 表塞一条任务行再刷新页面：面板「挂载时接回正在跑的更新」这条
# 逻辑本身就是功能的一部分（跑到一半刷新不该丢进度），顺手也把它测了。
PANEL_JS = """(function () {
    var p = document.querySelector('.upd-panel');
    if (!p) return { exists: false, bodyLen: document.body.innerText.length };
    var bar = document.querySelector('.upd-bar-in');
    var rect = p.getBoundingClientRect();
    return {
      exists: true,
      title: (document.querySelector('.upd-title') || {}).innerText || '',
      stageIdx: (document.querySelector('.stage-idx') || {}).innerText || '',
      stageName: (document.querySelector('.stage-name') || {}).innerText || '',
      stageCount: (document.querySelector('.stage-count') || {}).innerText || '',
      msg: (document.querySelector('.upd-msg') || {}).innerText || '',
      barWidth: bar ? bar.style.width : '',
      buttons: [].slice.call(p.querySelectorAll('button'))
          .map(function (b) { return b.innerText.trim(); }),
      text: p.innerText,
      left: Math.round(rect.left), top: Math.round(rect.top),
      right: Math.round(rect.right), bottom: Math.round(rect.bottom),
      width: Math.round(rect.width),
      vw: window.innerWidth, vh: window.innerHeight
    };
})()"""


def set_job(*, stage: str, progress: int, total: int, status: str,
            result: dict | None = None) -> None:
    """直接往冒烟库里写一条任务行，模拟「正在跑」和「刚跑完」。"""
    conn = session.connect()
    try:
        conn.execute("DELETE FROM jobs")
        conn.execute(
            """INSERT INTO jobs(kind, status, stage, progress, total, message,
                   result, started_at, finished_at)
               VALUES ('manual', ?, ?, ?, ?, ?, ?, datetime('now','localtime'), ?)""",
            (status, stage, progress, total,
             "更新中" if status == "running" else "完成",
             json.dumps(result, ensure_ascii=False) if result else None,
             None if status == "running" else "2026-10-17T10:00:00+08:00"),
        )
        conn.commit()
    finally:
        conn.close()


# ---- M5 页面：不只看标题在不在，看有没有真数据、真交互 ----

DECISIONS_JS = """(function () {
    var rows = [].slice.call(document.querySelectorAll('.decision-tbl tbody tr'));
    var first = rows[0];
    return {
      rows: rows.length,
      picksPerRow: first ? first.querySelectorAll('.pick').length : 0,
      labels: first ? [].slice.call(first.querySelectorAll('.pick'))
          .map(function (b) { return b.innerText.trim(); }) : [],
      decidedText: (document.querySelector('.tag') || {}).innerText || '',
      empty: !!document.querySelector('.empty')
    };
})()"""

# 点一下「加仓」，然后整页重载，看它还在不在（T5.1 的核心要求）
PICK_DECISION_JS = """(function () {
    var row = document.querySelector('.decision-tbl tbody tr');
    if (!row) return { ok: false, reason: 'no row' };
    var btn = row.querySelector('.pick.add');
    if (!btn) return { ok: false, reason: 'no add button' };
    btn.click();
    return { ok: true, code: row.querySelector('td').innerText.trim() };
})()"""

DECISION_PERSISTED_JS = """(function () {
    var row = document.querySelector('.decision-tbl tbody tr');
    if (!row) return { active: false };
    var btn = row.querySelector('.pick.add');
    return { active: !!btn && btn.classList.contains('active'),
             decidedText: (document.querySelector('.tag') || {}).innerText || '' };
})()"""

PORTFOLIO_JS = """(function () {
    var summary = document.querySelectorAll('.summary > div');
    var txt = document.body.innerText;
    var rows = document.querySelectorAll('table.tbl tbody tr');
    var over = document.querySelectorAll('tr.over');
    var heads = [].slice.call(document.querySelectorAll('table.tbl thead th'))
        .map(function (th) { return th.innerText.trim(); });
    return {
      summaryKeys: [].slice.call(summary).map(function (d) {
          return (d.querySelector('.k') || {}).innerText || '';
      }),
      hasFormButton: txt.indexOf('录入交易') >= 0,
      hasEmptyOrTable: !!document.querySelector('.tbl') || !!document.querySelector('.empty'),
      rows: rows.length,
      overRows: over.length,
      hasWeightHead: heads.indexOf('仓位占比') >= 0,
      hasOverBadge: txt.indexOf('超10%') >= 0,
      hasPriceStamp: txt.indexOf('价格时间') >= 0,
      hasCostPrice: txt.indexOf('成本价') >= 0
    };
})()"""

RECS_JS = """(function () {
    var cards = [].slice.call(document.querySelectorAll('.stat'));
    var txt = document.body.innerText;
    return {
      cards: cards.length,
      titles: cards.map(function (c) { return (c.querySelector('.k') || {}).innerText || ''; }),
      values: cards.map(function (c) { return (c.querySelector('.v') || {}).innerText || ''; }),
      hasSampleNote: txt.indexOf('/ 需要 10') >= 0,
      hasThreeTiers: txt.indexOf('硬失误') >= 0 && txt.indexOf('到期不赚') >= 0
          && txt.indexOf('跑输大盘') >= 0,
      hasEmptyNotNumbers: cards.filter(function (c) {
          var v = (c.querySelector('.v') || {}).innerText || '';
          return v.trim() === '\\u2014';
      }).length
    };
})()"""

SOURCES_JS = """(function () {
    var lamps = document.querySelectorAll('.lamp');
    var rules = document.querySelectorAll('.rules tbody tr');
    return {
      lamps: lamps.length,
      ruleRows: rules.length,
      colors: [].slice.call(lamps).map(function (l) { return l.className; }),
      hasLimits: document.body.innerText.indexOf('熔断') >= 0
    };
})()"""

SETTINGS_JS = """(function () {
    var txt = document.body.innerText;
    return {
      paths: [].slice.call(document.querySelectorAll('.path')).map(function (p) {
          return p.innerText.trim();
      }).filter(function (s) { return s.length > 3; }),
      hasPrivacyWord: txt.indexOf('不上传、不联网同步') >= 0,
      hasBackupBtn: txt.indexOf('立即备份') >= 0,
      hasVersions: txt.indexOf('数据库结构') >= 0 && txt.indexOf('算法') >= 0,
      hasExport: txt.indexOf('导出 JSON') >= 0 && txt.indexOf('导出 CSV') >= 0
    };
})()"""

ADD_STOCK_JS = """(async function () {
    var btn = [].slice.call(document.querySelectorAll('.tabs button'))
        .filter(function (b) { return b.innerText.indexOf('\\u6dfb\\u52a0\\u6807\\u7684') >= 0; })[0];
    if (!btn) return { hasButton: false };
    btn.click();
    // Vue 的 v-if 是异步渲染，点完要等一帧才能查到弹层
    await new Promise(function (r) { setTimeout(r, 300); });
    var box = document.querySelector('.add-box');
    if (!box) return { hasButton: true, modalOpened: false };
    var txt = box.innerText;
    var close = [].slice.call(box.querySelectorAll('button'))
        .filter(function (b) { return b.innerText.trim() === '\\u53d6\\u6d88'; })[0];
    if (close) close.click();
    return {
      hasButton: true,
      modalOpened: true,
      saysReserve: txt.indexOf('\\u50a8\\u5907\\u6c60') >= 0,
      saysNoPrice: txt.indexOf('\\u4e0d\\u7ed9\\u4e70\\u5165\\u4ef7') >= 0,
      hasCodeInput: !!box.querySelector('input')
    };
})()"""


def run_checks(cdp: Cdp, base: str) -> None:
    cdp.send("Page.enable")

    def goto(hash_path: str, wait: float = 1.0) -> None:
        cdp.eval(f"location.hash = '{hash_path}'")
        time.sleep(wait)

    # 等首屏渲染出来（等 #app 里有内容）
    deadline = time.time() + 30
    while time.time() < deadline:
        n = cdp.eval("document.querySelectorAll('#app > *').length")
        if n:
            break
        time.sleep(0.3)
    results["mounted"] = bool(n)

    goto("#/")
    results["dashboard"] = cdp.eval(DASHBOARD_JS)

    goto("#/pools?pool=candidate")
    results["pools"] = cdp.eval(POOLS_JS)

    goto("#/pools?pool=reserve")
    results["pools_reserve_rows"] = cdp.eval(
        "document.querySelectorAll('table.tbl tbody tr').length")

    goto("#/stocks/301095", wait=1.8)
    results["detail"] = cdp.eval(DETAIL_JS)

    others = {}
    for path, title in [
        ("#/decisions", "决策清单"),
        ("#/portfolio", "持仓"),
        ("#/recommendations", "推荐追踪"),
        ("#/sources", "数据源"),
        ("#/settings", "设置"),
    ]:
        goto(path, wait=0.9)
        others[title] = bool(cdp.eval(
            "document.body.innerText.indexOf({}) >= 0".format(
                json.dumps(title, ensure_ascii=False))))
    results["other_pages"] = others

    # ---- M5 各页：真数据、真交互 ----
    goto("#/decisions", wait=1.2)
    results["decisions"] = cdp.eval(DECISIONS_JS)
    results["decision_picked"] = cdp.eval(PICK_DECISION_JS)
    time.sleep(1.2)
    cdp.eval("location.reload()")
    time.sleep(2.2)
    results["decision_persisted"] = eval_retry(cdp, DECISION_PERSISTED_JS)

    goto("#/portfolio", wait=1.0)
    results["portfolio"] = cdp.eval(PORTFOLIO_JS)

    goto("#/recommendations", wait=1.0)
    results["recommendations"] = cdp.eval(RECS_JS)

    goto("#/sources", wait=1.0)
    results["sources"] = cdp.eval(SOURCES_JS)

    goto("#/settings", wait=1.0)
    results["settings"] = cdp.eval(SETTINGS_JS)

    goto("#/pools?pool=reserve", wait=1.0)
    results["add_stock"] = cdp.eval(ADD_STOCK_JS)

    goto("#/", wait=0.8)
    results["search"] = cdp.eval(SEARCH_JS)

    # 进度面板（T4.5）：running → 面板浮出、阶段/计数正确、有取消、不遮内容
    set_job(stage="popularity", progress=12, total=36, status="running")
    cdp.eval("location.reload()")
    time.sleep(2.5)
    results["update_panel_running"] = eval_retry(cdp, PANEL_JS)

    # 跑完 → 同一块面板（不刷新）自己切到结果态和「关闭」
    set_job(stage="persist", progress=36, total=36, status="ok",
            result={"okCount": 26, "total": 26, "failCount": 0, "movedCount": 2})
    time.sleep(1.8)
    results["update_panel_done"] = cdp.eval(PANEL_JS)

    results["console_errors"] = cdp.eval(
        "window.__errs ? window.__errs.slice(0,5) : []")


def eval_retry(cdp: Cdp, expr: str, tries: int = 6):
    """刷新页面后执行上下文会换一茬，第一次取值可能落空，重试几次。"""
    last = None
    for _ in range(tries):
        try:
            return cdp.eval(expr)
        except Exception as exc:      # noqa: BLE001  上下文切换期的正常抖动
            last = exc
            time.sleep(0.4)
    raise RuntimeError(f"页面刷新后取不到结果：{last}")


def judge() -> dict:
    checks = {}
    checks["mounted"] = bool(results.get("mounted"))

    d = results.get("dashboard") or {}
    checks["dashboard_cards_5"] = len(d.get("cards") or []) == 5
    checks["dashboard_has_overview"] = bool(d.get("hasOverview"))
    checks["dashboard_has_rows"] = (d.get("rowButtons") or 0) >= 7

    p = results.get("pools") or {}
    checks["pools_tabs_6"] = len(p.get("tabs") or []) == 6
    checks["pools_candidate_rows_2"] = p.get("rows") == 2
    checks["pools_reserve_rows_15"] = results.get("pools_reserve_rows") == 15

    s = results.get("detail") or {}
    checks["detail_total_shown"] = bool(s.get("hasTotal"))
    checks["detail_six_dims"] = s.get("dimCount") == 6
    checks["detail_radar_drawn"] = (s.get("radarCanvas") or 0) >= 1
    checks["detail_params"] = bool(s.get("hasParams"))
    checks["detail_barrier_block"] = bool(s.get("hasBarrier"))

    others = results.get("other_pages") or {}
    checks["all_pages_switch"] = len(others) == 5 and all(others.values())

    sr = results.get("search") or {}
    checks["search_finds_stock"] = any("广立微" in x for x in (sr.get("items") or []))

    # ---- M5 决策清单 ----
    dd = results.get("decisions") or {}
    checks["decisions_rows"] = (dd.get("rows") or 0) >= 20
    checks["decisions_four_picks"] = dd.get("picksPerRow") == 4
    checks["decisions_labels"] = dd.get("labels") == ["加仓", "持有", "减仓", "观察"]
    picked = results.get("decision_picked") or {}
    checks["decisions_click_works"] = bool(picked.get("ok"))
    persisted = results.get("decision_persisted") or {}
    checks["decisions_saved_without_submit"] = bool(persisted.get("active"))

    # ---- M5 持仓 ----
    pf = results.get("portfolio") or {}
    checks["portfolio_summary_4"] = len(pf.get("summaryKeys") or []) == 4
    checks["portfolio_has_trade_form"] = bool(pf.get("hasFormButton"))
    checks["portfolio_body_ok"] = bool(pf.get("hasEmptyOrTable"))
    checks["portfolio_position_row"] = (pf.get("rows") or 0) >= 1
    checks["portfolio_cost_column"] = bool(pf.get("hasCostPrice"))
    # 仓位占比 >10% 必须变红（T5.4）：夹具里单票占比 100%
    checks["portfolio_weight_column"] = bool(pf.get("hasWeightHead"))
    checks["portfolio_over_limit_red"] = bool(pf.get("hasOverBadge")) and \
        (pf.get("overRows") or 0) >= 1
    # 价格旁必须有时间戳（PRD 验收 30）
    checks["portfolio_price_timestamp"] = bool(pf.get("hasPriceStamp"))

    # ---- M5 推荐 ----
    rc = results.get("recommendations") or {}
    checks["recs_four_cards"] = (rc.get("cards") or 0) >= 4
    checks["recs_three_tiers"] = bool(rc.get("hasThreeTiers"))
    checks["recs_sample_note"] = bool(rc.get("hasSampleNote"))
    # 样本不足时分数位上必须是「—」
    checks["recs_dash_when_small_sample"] = (rc.get("hasEmptyNotNumbers") or 0) >= 3

    # ---- M5 数据源 ----
    so = results.get("sources") or {}
    checks["sources_four_rules"] = (so.get("ruleRows") or 0) == 4
    checks["sources_has_lamps"] = (so.get("lamps") or 0) >= 4
    checks["sources_limits_text"] = bool(so.get("hasLimits"))

    # ---- M5 设置 ----
    st = results.get("settings") or {}
    checks["settings_paths"] = len(st.get("paths") or []) >= 5
    checks["settings_privacy_promise"] = bool(st.get("hasPrivacyWord"))
    checks["settings_backup_button"] = bool(st.get("hasBackupBtn"))
    checks["settings_versions"] = bool(st.get("hasVersions"))
    checks["settings_export_buttons"] = bool(st.get("hasExport"))

    # ---- M5 添加标的 ----
    ad = results.get("add_stock") or {}
    checks["add_stock_button"] = bool(ad.get("hasButton"))
    checks["add_stock_modal"] = bool(ad.get("modalOpened"))
    checks["add_stock_says_reserve"] = bool(ad.get("saysReserve"))
    checks["add_stock_says_no_price"] = bool(ad.get("saysNoPrice"))

    # ---- 更新进度面板 ----
    up = results.get("update_panel_running") or {}
    checks["update_panel_shows_when_running"] = bool(up.get("exists"))
    checks["update_panel_title_running"] = up.get("title") == "更新中"
    checks["update_panel_stage_3_of_7"] = (up.get("stageIdx") == "3/7"
                                           and up.get("stageName") == "冷门度")
    checks["update_panel_count_12_of_36"] = up.get("stageCount") == "12/36"
    checks["update_panel_bar_moves"] = up.get("barWidth") not in ("", "0%", None)
    checks["update_panel_has_cancel"] = any("取消" in b for b in (up.get("buttons") or []))
    checks["update_panel_cancel_tip"] = "不会留下半截数据" in (up.get("text") or "")
    # 不遮内容：贴在右下角，宽度不超过 340，且整块都在视口内
    checks["update_panel_corner"] = bool(
        up.get("exists")
        and up.get("right", 0) <= up.get("vw", 0)
        and up.get("bottom", 0) <= up.get("vh", 0)
        and up.get("left", 0) > up.get("vw", 0) / 2
        and up.get("top", 0) > up.get("vh", 0) / 2
        and 0 < up.get("width", 0) <= 340
    )

    dn = results.get("update_panel_done") or {}
    checks["update_panel_done_title"] = dn.get("title") == "更新完成"
    checks["update_panel_done_result"] = "完成 26/26" in (dn.get("text") or "")
    checks["update_panel_done_close"] = any("关闭" in b for b in (dn.get("buttons") or []))
    checks["update_panel_done_no_cancel"] = not any(
        "取消" in b for b in (dn.get("buttons") or []))

    # 前端自报就绪。bodyLen 必须过 200：如果在挂载瞬间就报，那时路由组件
    # 还没渲染，只有一个导航骨架十几二十个字，外壳会把白屏放过去（踩过）。
    rd = results.get("ready") or {}
    pl = rd.get("payload") or {}
    checks["client_ready_reported"] = bool(rd.get("ready"))
    checks["client_ready_has_app"] = bool(pl.get("hasApp"))
    checks["client_ready_body_len"] = (pl.get("bodyLen") or 0) > 200

    results["checks"] = checks
    results["ok"] = all(checks.values()) and "error" not in results
    return checks


def main() -> int:
    bootstrap()

    port = appmain.pick_port()
    appmain.start_server(port)
    base = f"http://127.0.0.1:{port}/"
    results["base"] = base

    edge = find_edge()
    debug_port = appmain.pick_port()
    profile = Path(tempfile.mkdtemp(prefix="zisuye-edge-"))
    proc = launch_edge(edge, debug_port, profile)
    cdp = None
    try:
        ws_url = wait_target(debug_port)
        cdp = Cdp(ws_url)
        cdp.send("Page.navigate", {"url": base})
        time.sleep(1.0)
        ok = SESSION.get(base + "health", timeout=5).json().get("ok")
        results["health"] = ok
        run_checks(cdp, base)

        # 前端自报就绪：桌面外壳和窗口冒烟靠它区分「页面真渲染出来了」和「白屏」。
        # 这条只能在真 Chromium 里验——这正是页面自身的行为，在外壳层验不了。
        ready = SESSION.get(base + "api/meta/ready", timeout=5).json()
        results["ready"] = ready
    except Exception as exc:  # noqa: BLE001
        results["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if cdp:
            cdp.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)

    judge()
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if results["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
