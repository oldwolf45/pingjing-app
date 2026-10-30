#!/usr/bin/env python
"""版本号管理：一处修改，处处同步。

用法：
    python scripts/bump_version.py 0.2.0      # 设为指定版本
    python scripts/bump_version.py --bump patch   # 补丁 +1
    python scripts/bump_version.py --bump minor   # 次版本 +1
    python scripts/bump_version.py --bump major   # 主版本 +1
    python scripts/bump_version.py --current      # 只显示当前版本

同步的文件：
  - pyproject.toml (version = "x.y.z")
  - installer/version_info.txt (filevers / prodvers / FileVersion / ProductVersion)
  - installer/zisuye.iss (AppVersion define)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def current_version() -> str:
    """从 pyproject.toml 读当前版本号。"""
    content = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if not m:
        raise RuntimeError("pyproject.toml 里找不到 version")
    return m.group(1)


def bump(version: str, kind: str) -> str:
    """按语义版本号递增。"""
    parts = [int(x) for x in version.split(".")]
    while len(parts) < 3:
        parts.append(0)
    major, minor, patch = parts[:3]
    if kind == "major":
        major += 1
        minor = 0
        patch = 0
    elif kind == "minor":
        minor += 1
        patch = 0
    elif kind == "patch":
        patch += 1
    else:
        raise ValueError(f"未知递增类型: {kind}")
    return f"{major}.{minor}.{patch}"


def update_pyproject(version: str) -> bool:
    p = ROOT / "pyproject.toml"
    content = p.read_text(encoding="utf-8")
    new_content = re.sub(
        r'^version\s*=\s*"[^"]+"',
        f'version = "{version}"',
        content,
        count=1,
        flags=re.MULTILINE,
    )
    if new_content == content:
        return False
    p.write_text(new_content, encoding="utf-8")
    return True


def update_version_info(version: str) -> bool:
    """更新 version_info.txt 里的 4 处版本号。"""
    p = ROOT / "installer" / "version_info.txt"
    if not p.exists():
        return False
    content = p.read_text(encoding="utf-8")

    # (0, 1, 0, 0) 格式
    v_tuple = tuple(int(x) for x in version.split("."))
    while len(v_tuple) < 4:
        v_tuple = v_tuple + (0,)
    v_tuple_str = f"({v_tuple[0]}, {v_tuple[1]}, {v_tuple[2]}, {v_tuple[3]})"

    content = re.sub(
        r"filevers=\(\d+, \d+, \d+, \d+\)",
        f"filevers={v_tuple_str}",
        content,
    )
    content = re.sub(
        r"prodvers=\(\d+, \d+, \d+, \d+\)",
        f"prodvers={v_tuple_str}",
        content,
    )

    # '0.1.0.0' 字符串格式
    v_str = f"{version}.0"
    content = re.sub(
        r"StringStruct\(u'FileVersion', u'[^']+'\)",
        f"StringStruct(u'FileVersion', u'{v_str}')",
        content,
    )
    content = re.sub(
        r"StringStruct\(u'ProductVersion', u'[^']+'\)",
        f"StringStruct(u'ProductVersion', u'{v_str}')",
        content,
    )

    p.write_text(content, encoding="utf-8")
    return True


def update_iss(version: str) -> bool:
    """更新 zisuye.iss 里的默认版本号。"""
    p = ROOT / "installer" / "zisuye.iss"
    if not p.exists():
        return False
    content = p.read_text(encoding="utf-8")
    new_content = re.sub(
        r'#define AppVersion "[^"]+"',
        f'#define AppVersion "{version}"',
        content,
    )
    if new_content == content:
        return False
    p.write_text(new_content, encoding="utf-8")
    return True


def main():
    parser = argparse.ArgumentParser(description="版本号同步工具")
    parser.add_argument("version", nargs="?", help="目标版本号（如 0.2.0）")
    parser.add_argument("--bump", choices=["major", "minor", "patch"],
                        help="按类型递增版本号")
    parser.add_argument("--current", action="store_true",
                        help="显示当前版本号")
    args = parser.parse_args()

    cur = current_version()

    if args.current:
        print(cur)
        return

    if args.version:
        new_ver = args.version
    elif args.bump:
        new_ver = bump(cur, args.bump)
    else:
        print(f"当前版本: {cur}")
        parser.print_help()
        sys.exit(1)

    # 验证版本号格式
    if not re.match(r"^\d+\.\d+\.\d+$", new_ver):
        print(f"版本号格式错误: {new_ver}（应为 x.y.z）")
        sys.exit(1)

    print(f"版本号 {cur} → {new_ver}")

    updated = []
    if update_pyproject(new_ver):
        updated.append("pyproject.toml")
    if update_version_info(new_ver):
        updated.append("installer/version_info.txt")
    if update_iss(new_ver):
        updated.append("installer/zisuye.iss")

    for f in updated:
        print(f"  [OK] {f}")

    print(f"\n完成，共更新 {len(updated)} 个文件")


if __name__ == "__main__":
    main()
