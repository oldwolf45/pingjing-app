"""PRD 验收第 4 条：双击第二次只激活已有窗口，不新开进程。

判据不能是「runtime.json 存在」——崩溃会留下这个文件，拿它当锁会让用户永远开不了。
所以这里逐条压：
  1. 没有 runtime.json              → 自由启动
  2. runtime.json 指向死进程         → 当陈旧文件清掉，照常启动
  3. 进程活着但后端不应答            → 当没人在跑（卡死的进程不该拦住用户）
  4. 进程活着 + 后端应答             → 认成已有实例，主入口直接退出
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app import main as appmain
from app import paths


@pytest.fixture()
def runtime_file(data_home) -> Path:
    return paths.data_dir() / appmain.RUNTIME_FILE


def _write_runtime(runtime_file: Path, pid: int, port: int) -> None:
    runtime_file.write_text(json.dumps({"pid": pid, "port": port, "url": "x"}),
                            encoding="utf-8")


@pytest.fixture()
def live_server():
    """起一个真的后端，用来提供 /health。"""
    port = appmain.pick_port()
    server = appmain.start_server(port)
    yield port
    server.should_exit = True
    time.sleep(0.2)


@pytest.fixture()
def other_process():
    """一个活着但不是本进程的 pid（用 sleep 顶着）。"""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    time.sleep(0.2)
    yield proc.pid
    proc.kill()
    proc.wait(timeout=5)


def test_no_runtime_file_means_free(runtime_file):
    assert appmain.existing_instance()["alive"] is False


def test_stale_runtime_from_crash_is_cleaned(runtime_file):
    """上次崩溃留下的文件不能挡住启动。"""
    _write_runtime(runtime_file, pid=999999, port=1)
    info = appmain.existing_instance()
    assert info["alive"] is False
    assert info["stale"] is True
    assert not runtime_file.exists(), "陈旧文件应当被清掉，否则每次启动都要白等一轮探测"


def test_broken_runtime_file_is_ignored(runtime_file):
    runtime_file.write_text("这不是 json", encoding="utf-8")
    info = appmain.existing_instance()
    assert info["alive"] is False and info["stale"] is True


def test_live_process_with_dead_backend_does_not_block(runtime_file, other_process):
    """进程还在但后端不应答：不能拿它当锁，用户会永远开不了。"""
    _write_runtime(runtime_file, pid=other_process, port=appmain.pick_port())
    assert appmain.existing_instance()["alive"] is False


def test_live_process_and_healthy_backend_counts_as_running(runtime_file, other_process,
                                                            live_server):
    _write_runtime(runtime_file, pid=other_process, port=live_server)
    info = appmain.existing_instance()
    assert info["alive"] is True
    assert info["pid"] == other_process
    assert info["port"] == live_server
    # 没有可见窗口时 focused 为 False 是正常的，重点是别把 alive 判成 False


def test_main_exits_without_opening_window(runtime_file, other_process, live_server,
                                           monkeypatch):
    """第二次双击：主入口直接退出，不建窗口、不占端口。"""
    _write_runtime(runtime_file, pid=other_process, port=live_server)
    monkeypatch.setattr(sys, "argv", ["zisuye.exe"])

    called = {"window": False}

    def spy(**kwargs):
        called["window"] = True

    monkeypatch.setattr(appmain, "run_window", spy)

    with pytest.raises(SystemExit) as ei:
        appmain.main()
    assert ei.value.code == 0
    assert called["window"] is False, "已有实例在跑时不许再开一个窗口"


def test_allow_multi_bypasses_check(runtime_file, other_process, live_server, monkeypatch):
    """排查问题时得能强行开第二个（--allow-multi）。"""
    _write_runtime(runtime_file, pid=other_process, port=live_server)
    monkeypatch.setattr(sys, "argv", ["zisuye.exe", "--allow-multi"])

    called = {"window": False}
    monkeypatch.setattr(appmain, "run_window", lambda **kw: called.update(window=True))

    appmain.main()
    assert called["window"] is True


def test_self_check_table_list_matches_schema():
    """自检别数总数：加一张表就得改一次数字，改漏了就假绿。

    这里反过来对 schema.sql 核一遍：点名的表必须真的存在，
    而且 schema 里的表不能有漏在清单外的（漏了就等于自检不覆盖）。
    """
    import re

    from app.db import session

    sql = (Path(session.__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8")
    declared = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql))

    assert set(appmain.REQUIRED_TABLES) <= declared, "自检点名的表在 schema 里不存在"
    assert declared - set(appmain.REQUIRED_TABLES) == set(), "schema 里有表没进自检清单"
    assert "quotes" in appmain.REQUIRED_TABLES
