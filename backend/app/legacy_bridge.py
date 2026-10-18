"""算法层桥接：把原样拷贝过来的算法模块接进新架构，**一行都不改**。

为什么需要这层：
  这些模块的缓存路径是 `os.path.dirname(__file__) + "/data/..."`。打包成 exe 后
  `__file__` 指向安装目录，普通用户没有写权限 → 缓存写不进去 → 每次更新重新抓 →
  慢十倍 + 被接口封 IP。所以 import 之后重新赋值路径常量。

为什么不用包导入：
  算法模块之间是**平级 import**（`from scoring_engine import ...`），
  所以必须把 algo/ 目录本身放进 sys.path，不能当子包用。

三条纪律：
  1. 算法文件只拷贝不编辑（sha256 校验，改了拒绝启动）
  2. 路径只能指向数据目录（断言，越界拒绝启动）
  3. 需要改行为一律在外层新文件里做，不碰原文件
"""

from __future__ import annotations

import hashlib
import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from app import paths

ALGO_DIR = Path(__file__).resolve().parent / "algo"
MANIFEST_NAME = "algo_manifest.json"


def algo_dir() -> Path:
    """算法目录。打包后是 _internal/app/algo（作为数据文件打进去的）。

    为什么要把 .py 当数据文件打而不是编译进 PYZ：
      指纹校验需要读源码算 sha256。编译进 PYZ 后磁盘上没有源码文件，
      校验就形同虚设。所以宁可让源码明文躺着，也要保住"能验证没被改"这件事。
    """
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base / "app" / "algo"
    return ALGO_DIR


class AlgoIntegrityError(RuntimeError):
    """算法文件被改动过。"""


class AlgoPathError(RuntimeError):
    """算法模块的路径跑出了数据目录。"""


# ------------------------------------------------------------------
# 指纹
# ------------------------------------------------------------------

def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def algo_files() -> list[Path]:
    return sorted(p for p in algo_dir().glob("*.py") if p.is_file())


def compute_manifest() -> dict[str, Any]:
    files = {}
    for p in algo_files():
        files[p.name] = {"sha256": sha256_file(p), "size": p.stat().st_size}
    digest = hashlib.sha256(
        "".join(f"{k}:{v['sha256']}" for k, v in sorted(files.items())).encode()
    ).hexdigest()
    return {
        "version": f"v1+{digest[:8]}",
        "digest": digest,
        "files": files,
    }


def write_manifest(path: Path | None = None) -> Path:
    """生成/刷新指纹清单。只在算法文件**确实被有意升级**时执行。"""
    target = path or (algo_dir() / MANIFEST_NAME)
    data = compute_manifest()
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    return target


def read_manifest() -> dict | None:
    f = algo_dir() / MANIFEST_NAME
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8"))


def verify_integrity(raise_on_missing: bool = False) -> dict:
    """校验算法文件没被改动。返回 {"ok":..,"version":..,"problems":[...]}"""
    expected = read_manifest()
    if expected is None:
        if raise_on_missing:
            raise AlgoIntegrityError(f"缺少指纹清单 {MANIFEST_NAME}，拒绝启动")
        return {"ok": False, "version": None, "problems": ["缺少指纹清单"]}

    problems = []
    actual = compute_manifest()

    for name, meta in expected.get("files", {}).items():
        f = algo_dir() / name
        if not f.exists():
            problems.append(f"{name} 不见了")
            continue
        got = sha256_file(f)
        if got != meta["sha256"]:
            problems.append(f"{name} 内容被改动（期望 {meta['sha256'][:12]}，实际 {got[:12]}）")

    for name in actual["files"]:
        if name not in expected.get("files", {}):
            problems.append(f"{name} 是新增文件，不在清单里")

    if problems and raise_on_missing:
        raise AlgoIntegrityError("算法文件校验失败：" + "；".join(problems) +
                                 "。请重新安装，不要手改算法文件。")

    return {
        "ok": not problems,
        "version": expected.get("version"),
        "problems": problems,
    }


# ------------------------------------------------------------------
# 导入与路径重绑
# ------------------------------------------------------------------

def _ensure_sys_path() -> None:
    p = str(algo_dir())
    if p not in sys.path:
        sys.path.insert(0, p)


def _rebind(module, attr: str, new_value: str, root: Path, paths_seen: list) -> None:
    old = getattr(module, attr, None)
    setattr(module, attr, new_value)
    paths_seen.append((f"{module.__name__}.{attr}", old, new_value))


def _assert_inside(p: str, root: Path, label: str) -> None:
    rp = Path(p).resolve()
    rr = root.resolve()
    if rr != rp and rr not in rp.parents:
        raise AlgoPathError(f"{label} 指向数据目录之外：{rp}（数据目录 {rr}）")


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    """导入算法模块并把路径常量重绑到数据目录。返回模块字典。"""
    verify_integrity(raise_on_missing=False)
    _ensure_sys_path()

    import barrier_engine
    import config as algo_config
    import fin_fetcher
    import flow_fetcher
    import pool_manager
    import pool_rotation
    import popularity_engine
    import scoring_engine
    import sector_intel

    root = paths.data_dir()
    cache_dir = root / "cache"
    intel_dir = root / "intel"
    cache_dir.mkdir(parents=True, exist_ok=True)
    intel_dir.mkdir(parents=True, exist_ok=True)

    seen: list[tuple[str, Any, str]] = []
    _rebind(popularity_engine, "CACHE", str(cache_dir / "popularity_cache.json"), root, seen)
    _rebind(fin_fetcher, "CACHE", str(cache_dir / "fin_cache.json"), root, seen)
    _rebind(flow_fetcher, "CACHE_FLOW", str(cache_dir / "flow_cache.json"), root, seen)
    _rebind(flow_fetcher, "CACHE_RANK", str(cache_dir / "rank_cache.json"), root, seen)
    _rebind(sector_intel, "BASE_DIR", str(root), root, seen)
    _rebind(sector_intel, "INTEL_DIR", str(intel_dir), root, seen)

    # 断言：所有路径都必须落在数据目录内，否则打包后写不进去
    for label, _old, new in seen:
        _assert_inside(new, root, label)

    return {
        "config": algo_config,
        "scoring": scoring_engine,
        "barrier": barrier_engine,
        "popularity": popularity_engine,
        "pool_manager": pool_manager,
        "pool_rotation": pool_rotation,
        "fin_fetcher": fin_fetcher,
        "flow_fetcher": flow_fetcher,
        "sector_intel": sector_intel,
        "_rebound": [{"module_attr": s[0], "old": s[1], "new": s[2]} for s in seen],
    }


def algo_version() -> str:
    m = read_manifest()
    return m.get("version") if m else "unknown"


def check_paths(root: Path | None = None) -> list[dict]:
    """给自检页用：列出所有被重绑的路径，确认都在数据目录内。"""
    mods = load()
    r = (root or paths.data_dir()).resolve()
    out = []
    for item in mods["_rebound"]:
        p = Path(item["new"]).resolve()
        inside = r == p or r in p.parents
        out.append({**item, "inside_data_dir": inside})
    return out


def reset() -> None:
    """测试用：清掉 lru_cache，让下次 load 重新走一遍。"""
    load.cache_clear()
