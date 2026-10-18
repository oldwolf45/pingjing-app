"""只起后端 HTTP 服务不开窗口，供浏览器/无头工具验证前端产物。

用法：python tools/_serve_only.py [port]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

from app import main as appmain  # noqa: E402

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18000
    appmain.start_server(port)
    print(f"listening on http://127.0.0.1:{port}/", flush=True)
    while True:
        time.sleep(3600)
