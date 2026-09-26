import os
from pathlib import Path

src = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'research', 'city', 'mc3boot.disable_wshd.ini'))
dst = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'research', 'city', 'mc3boot.wshd_off.ini'))
data = src.read_bytes()
old = b"city_world_shader_trace_v2.mod = defer_once"
new = b"city_world_shader_trace_v2.mod = 0"
if data.count(old) != 1:
    raise SystemExit(f"expected exactly one WSHD line, found {data.count(old)}")
dst.write_bytes(data.replace(old, new))
