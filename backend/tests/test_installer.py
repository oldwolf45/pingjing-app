"""安装 / 卸载（PRD 验收第 1、3 条）。

这里是快的部分：拿假产物跑安装逻辑，验证文件落位、卸载项、快捷方式、
以及「卸载不删数据」这条硬要求。真 exe 的端到端演练在 tools/drill_install.py。

所有路径都改到临时目录：LOCALAPPDATA / USERPROFILE / APPDATA 全部重定向，
免得测试往真实用户桌面丢快捷方式。
"""

from __future__ import annotations

import json
import winreg
from pathlib import Path

import pytest

from app.services import installer


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """把 Windows 的那几个用户目录全部指到临时目录里。"""
    local = tmp_path / "local"
    roaming = tmp_path / "roaming"
    profile = tmp_path / "profile"
    for p in (local, roaming, profile, profile / "Desktop"):
        p.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setenv("USERPROFILE", str(profile))
    return {"tmp": tmp_path, "local": local, "roaming": roaming, "profile": profile}


@pytest.fixture()
def fake_dist(tmp_path) -> Path:
    """假的打包产物：exe + 一份前端 dist，够验落位就行。"""
    d = tmp_path / "dist" / installer.APP_NAME
    (d / "_internal" / "frontend" / "dist").mkdir(parents=True)
    (d / f"{installer.APP_NAME}.exe").write_bytes(b"MZ-fake")
    (d / "_internal" / "frontend" / "dist" / "index.html").write_text(
        '<div id="app"></div>', encoding="utf-8")
    (d / "_internal" / "app" / "db").mkdir(parents=True)
    (d / "_internal" / "app" / "db" / "schema.sql").write_text("-- schema", encoding="utf-8")
    return d


@pytest.fixture()
def reg_key() -> str:
    return "紫苏叶_unittest"


@pytest.fixture(autouse=True)
def cleanup_registry(reg_key):
    """测试挂了也别把卸载项留在真实注册表里。"""
    yield
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER,
                         f"{installer.UNINSTALL_ROOT}\\{reg_key}")
    except FileNotFoundError:
        pass


def _install(fake_dist, sandbox, reg_key) -> dict:
    return installer.install(fake_dist, sandbox["tmp"] / "Programs" / "ZiSuYe",
                             version="9.9.9", reg_key=reg_key)


def test_install_copies_program_files(fake_dist, sandbox, reg_key):
    report = _install(fake_dist, sandbox, reg_key)
    root = Path(report["root"])

    assert report["ok"] is True
    assert (root / f"{installer.APP_NAME}.exe").is_file()
    assert (root / "_internal" / "frontend" / "dist" / "index.html").is_file()
    assert (root / "_internal" / "app" / "db" / "schema.sql").is_file()


def test_install_writes_uninstall_entry(fake_dist, sandbox, reg_key):
    report = _install(fake_dist, sandbox, reg_key)
    key = f"{installer.UNINSTALL_ROOT}\\{reg_key}"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
        name = winreg.QueryValueEx(k, "DisplayName")[0]
        version = winreg.QueryValueEx(k, "DisplayVersion")[0]
        cmd = winreg.QueryValueEx(k, "UninstallString")[0]

    assert "紫苏叶" in name
    assert version == "9.9.9"
    # 卸载命令必须指向装进去的 exe，并且带 --uninstall
    assert installer.APP_NAME + ".exe" in cmd
    assert "--uninstall" in cmd
    assert Path(report["root"]).as_posix().split("/")[-1] in cmd
    assert report["uninstall_string"] == cmd


def test_install_creates_shortcuts(fake_dist, sandbox, reg_key):
    report = _install(fake_dist, sandbox, reg_key)
    desktop = sandbox["profile"] / "Desktop" / f"{reg_key}.lnk"
    startmenu = (sandbox["roaming"] / "Microsoft" / "Windows" / "Start Menu"
                 / "Programs" / f"{reg_key}.lnk")

    assert desktop.is_file(), f"桌面快捷方式没建出来：{report}"
    assert startmenu.is_file(), f"开始菜单快捷方式没建出来：{report}"
    assert set(report["shortcuts"]) == {str(desktop), str(startmenu)}


def test_reinstall_replaces_program_files(fake_dist, sandbox, reg_key):
    _install(fake_dist, sandbox, reg_key)
    root = sandbox["tmp"] / "Programs" / "ZiSuYe"
    leftover = root / "old_dll_should_be_gone.dll"
    leftover.write_text("x", encoding="utf-8")

    report = _install(fake_dist, sandbox, reg_key)
    assert report["ok"] is True
    assert not leftover.exists(), "覆盖安装要清掉上一版残留文件，否则旧 dll 会被加载"


def test_uninstall_keeps_user_data(fake_dist, sandbox, reg_key):
    _install(fake_dist, sandbox, reg_key)

    data = installer.data_dir()
    data.mkdir(parents=True, exist_ok=True)
    (data / "zisuye.db").write_text("用户数据", encoding="utf-8")
    (data / "backups").mkdir(exist_ok=True)

    report = installer.uninstall(sandbox["tmp"] / "Programs" / "ZiSuYe", reg_key=reg_key)

    assert report["ok"] is True
    assert not Path(report["root"]).exists(), "程序文件要删干净"
    assert (data / "zisuye.db").read_text(encoding="utf-8") == "用户数据"
    assert (data / "backups").is_dir()


def test_uninstall_removes_shortcuts_and_registry(fake_dist, sandbox, reg_key):
    _install(fake_dist, sandbox, reg_key)
    installer.uninstall(sandbox["tmp"] / "Programs" / "ZiSuYe", reg_key=reg_key)

    assert not (sandbox["profile"] / "Desktop" / f"{reg_key}.lnk").exists()
    assert not (sandbox["roaming"] / "Microsoft" / "Windows" / "Start Menu"
                / "Programs" / f"{reg_key}.lnk").exists()
    with pytest.raises(FileNotFoundError):
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"{installer.UNINSTALL_ROOT}\\{reg_key}")


def test_uninstall_then_reinstall_data_intact(fake_dist, sandbox, reg_key):
    """PRD 第 3 条：卸载后重装，数据一条不少。"""
    root = sandbox["tmp"] / "Programs" / "ZiSuYe"
    _install(fake_dist, sandbox, reg_key)
    data = installer.data_dir()
    data.mkdir(parents=True, exist_ok=True)
    (data / "zisuye.db").write_text("36 只票", encoding="utf-8")

    installer.uninstall(root, reg_key=reg_key)
    _install(fake_dist, sandbox, reg_key)

    assert (data / "zisuye.db").read_text(encoding="utf-8") == "36 只票"
    assert (root / f"{installer.APP_NAME}.exe").is_file()


def test_refuses_shallow_root(fake_dist, sandbox, reg_key):
    """手抖把 --root 指到盘根或用户主目录时，必须直接拒绝。"""
    for bad in ("C:\\", str(sandbox["profile"])):
        with pytest.raises(SystemExit):
            installer.install(fake_dist, Path(bad), reg_key=reg_key)
        with pytest.raises(SystemExit):
            installer.uninstall(Path(bad), reg_key=reg_key)


def test_install_without_exe_fails_loudly(sandbox, reg_key, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SystemExit) as ei:
        installer.install(empty, sandbox["tmp"] / "Programs" / "ZiSuYe", reg_key=reg_key)
    assert "找不到打包产物" in str(ei.value)


def test_cli_wrapper_installs(sandbox, fake_dist, tmp_path, capsys, monkeypatch):
    """installer/install.py 要能当命令行用（构建机上就这么调）。"""
    import subprocess
    import sys

    root = tmp_path / "Programs" / "ZiSuYe"
    script = Path(__file__).resolve().parent.parent.parent / "installer" / "install.py"
    r = subprocess.run(
        [sys.executable, str(script), "--source", str(fake_dist), "--root", str(root),
         "--reg-key", "紫苏叶_unittest", "--version", "1.2.3"],
        capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is True
    assert (root / f"{installer.APP_NAME}.exe").is_file()
