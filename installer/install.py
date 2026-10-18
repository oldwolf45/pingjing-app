"""紫苏叶策略 · 安装/卸载命令行。

真正的实现在 backend/app/services/installer.py —— 因为卸载入口必须是**装进去的
紫苏叶.exe 自己**（目标机器上没有 Python）。这个脚本只是构建机上用的外壳：

    python installer/install.py                     # 装到 %LOCALAPPDATA%\\Programs\\ZiSuYe
    python installer/install.py --uninstall         # 卸载（保留数据）
    python installer/install.py --root D:\\tmp\\x --reg-key 紫苏叶_演练

正式发版走 Inno Setup：ISCC installer\\zisuye.iss
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import installer  # noqa: E402


if __name__ == "__main__":
    sys.exit(installer.main())
