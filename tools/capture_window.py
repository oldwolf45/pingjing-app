"""按窗口标题截图。用来确认 WebView2 窗口里到底画出了什么。

用法：python tools/capture_window.py <标题片段> <输出png> [超时秒]
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

SRCCOPY = 0x00CC0020
PW_RENDERFULLCONTENT = 0x00000002


def find_window(needle: str) -> int | None:
    found: list[int] = []

    def cb(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if needle in buf.value and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return found[0] if found else None


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


def capture(hwnd: int, out: Path, method: str = "screen") -> tuple[int, int]:
    rect = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top

    # screen 模式从桌面 DC 直接抠窗口区域。Chromium 走 DirectComposition，
    # PrintWindow 通常只能拿到一块白板，必须抓屏幕才算数。
    src = user32.GetDC(0) if method == "screen" else user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(src)
    bmp = gdi32.CreateCompatibleBitmap(src, w, h)
    gdi32.SelectObject(mem, bmp)

    if method == "screen":
        gdi32.BitBlt(mem, 0, 0, w, h, src, rect.left, rect.top, SRCCOPY)
    else:
        ok = user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)
        if not ok:
            gdi32.BitBlt(mem, 0, 0, w, h, src, 0, 0, SRCCOPY)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0

    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB").save(out)

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(0 if method == "screen" else hwnd, src)
    return w, h


def main() -> int:
    needle = sys.argv[1] if len(sys.argv) > 1 else "紫苏叶"
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "window.png")
    timeout = float(sys.argv[3]) if len(sys.argv) > 3 else 20

    hwnd = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = find_window(needle)
        if hwnd:
            break
        time.sleep(0.5)
    if not hwnd:
        print(f"没找到标题含“{needle}”的可见窗口")
        return 1

    # WebView2 首帧晚于窗口创建，多等一会儿再截；先抢到前台再抓屏幕
    user32.SetForegroundWindow(hwnd)
    time.sleep(4)
    w, h = capture(hwnd, out, "screen")
    print(f"captured {w}x{h} -> {out}")
    ph = out.with_name(out.stem + "_pw.png")
    capture(hwnd, ph, "printwindow")
    print(f"captured(printwindow) -> {ph}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
