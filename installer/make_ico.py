"""把 installer/icon_png 下的圆角 PNG 组装成多尺寸 zisuye.ico（PNG 条目，Vista+ 支持）。"""

import struct
from pathlib import Path

HERE = Path(__file__).parent
SIZES = [256, 128, 64, 48, 32, 24, 16]

blobs = []
for s in SIZES:
    data = (HERE / "icon_png" / f"zisuye_{s}.png").read_bytes()
    blobs.append((s, data))

out = struct.pack("<HHH", 0, 1, len(blobs))
offset = 6 + 16 * len(blobs)
entries = b""
for s, data in blobs:
    entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
    offset += len(data)

ico = out + entries + b"".join(data for _, data in blobs)
target = HERE / "zisuye.ico"
target.write_bytes(ico)
print(f"OK {target} {len(ico)} bytes, {len(blobs)} sizes")
