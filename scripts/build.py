#!/usr/bin/env python
"""本地打包工具：一键完成前端构建 + PyInstaller 打包。

用法：
    python scripts/build.py              # 默认打包
    python scripts/build.py --version    # 先更新版本号再打包
    python scripts/build.py --skip-test  # 跳过测试
    python scripts/build.py --clean      # 清理旧的构建产物
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"
DIST_DIR = ROOT / "dist"


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> int:
    """运行命令并返回退出码。"""
    print(f"\n>>> {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env)
    return result.returncode


def clean():
    """清理旧构建产物。"""
    for d in ["build", "dist"]:
        p = ROOT / d
        if p.exists():
            print(f"清理 {p}")
            shutil.rmtree(p)
    # PyInstaller 的 .spec 同名目录
    spec_dir = ROOT / "紫苏叶"
    if spec_dir.exists() and spec_dir.is_dir():
        shutil.rmtree(spec_dir)


def build_frontend() -> bool:
    """构建前端。"""
    if not FRONTEND_DIR.exists():
        print("找不到 frontend 目录，跳过前端构建")
        return True

    # 安装依赖（如果没有 node_modules）
    if not (FRONTEND_DIR / "node_modules").exists():
        print("安装前端依赖...")
        rc = run(["npm", "ci", "--no-audit", "--no-fund"], cwd=FRONTEND_DIR)
        if rc != 0:
            print("前端依赖安装失败")
            return False

    # 构建
    print("构建前端...")
    rc = run(["npm", "run", "build"], cwd=FRONTEND_DIR)
    if rc != 0:
        print("前端构建失败")
        return False

    # 检查产物
    dist = FRONTEND_DIR / "dist"
    if not dist.exists() or not (dist / "index.html").exists():
        print("前端构建产物不存在")
        return False

    print(f"前端构建完成: {dist}")
    return True


def run_tests() -> bool:
    """运行后端测试。"""
    print("运行后端测试...")
    env = os.environ.copy()
    env["ZISUYE_MODE"] = "test"
    rc = run([sys.executable, "-m", "pytest", "backend/tests/", "-v", "--tb=short"],
             cwd=ROOT, env=env)
    if rc != 0:
        print("测试失败")
        return False
    return True


def build_pyinstaller() -> bool:
    """PyInstaller 打包。"""
    spec_file = ROOT / "zisuye.spec"
    if not spec_file.exists():
        print(f"找不到 {spec_file}")
        return False

    # 检查 pyinstaller 是否安装
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("安装 PyInstaller...")
        rc = run([sys.executable, "-m", "pip", "install", "pyinstaller"])
        if rc != 0:
            print("PyInstaller 安装失败")
            return False

    print("PyInstaller 打包...")
    rc = run([sys.executable, "-m", "PyInstaller", str(spec_file),
              "--noconfirm", "--clean"], cwd=ROOT)
    if rc != 0:
        print("PyInstaller 打包失败")
        return False

    # 检查产物
    exe = DIST_DIR / "紫苏叶" / "紫苏叶.exe"
    if not exe.exists():
        print(f"打包产物不存在: {exe}")
        return False

    print(f"打包完成: {exe}")
    return True


def make_zip() -> Path | None:
    """打 zip 便携版。"""
    src = DIST_DIR / "紫苏叶"
    if not src.exists():
        return None

    # 读取版本号
    from bump_version import current_version  # 复用版本号读取
    version = current_version()

    zip_name = f"紫苏叶策略_{version}_windows_portable"
    zip_path = DIST_DIR / zip_name

    print(f"打 zip: {zip_path}.zip")
    shutil.make_archive(str(zip_path), "zip", root_dir=str(DIST_DIR), base_dir="紫苏叶")

    final = zip_path.with_suffix(".zip")
    size_mb = final.stat().st_size / (1024 * 1024)
    print(f"完成: {final} ({size_mb:.1f} MB)")
    return final


def main():
    parser = argparse.ArgumentParser(description="紫苏叶本地打包工具")
    parser.add_argument("--clean", action="store_true", help="先清理旧产物")
    parser.add_argument("--skip-test", action="store_true", help="跳过测试")
    parser.add_argument("--skip-frontend", action="store_true", help="跳过前端构建")
    parser.add_argument("--version", help="先设置版本号再打包")
    parser.add_argument("--zip", action="store_true", help="打包后生成 zip 便携版")
    args = parser.parse_args()

    # 清理
    if args.clean:
        clean()

    # 设置版本号
    if args.version:
        from bump_version import main as bump_main
        sys.argv = ["bump_version.py", args.version]
        bump_main()

    # 前端构建
    if not args.skip_frontend:
        if not build_frontend():
            sys.exit(1)

    # 测试
    if not args.skip_test:
        if not run_tests():
            sys.exit(1)

    # PyInstaller 打包
    if not build_pyinstaller():
        sys.exit(1)

    # zip
    if args.zip:
        make_zip()

    print("\n打包完成！")


if __name__ == "__main__":
    main()
