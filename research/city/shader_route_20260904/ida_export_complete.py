"""Extend the bounded export, leaving the shared IDA database untouched."""
import os
from pathlib import Path
p = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'research', 'city', 'shader_route_20260904/ida_export_route.py'))
source = p.read_text()
source = source.replace('(0x2AE800, 0x2BB030)', '(0x2ACD00, 0x2BB030)')
source = source.replace('(0x595DE8, 0x598300)', '(0x595A00, 0x598300), (0x431000, 0x432600)')
source = source.replace('0x3993A8,0x398AD0,0x398B68,0x1A9080]',
    '0x3993A8,0x398AD0,0x398B18,0x398B68,0x1A9080,0x259160,0x394290,0x394460,0x394558,0x3B5138,0x5BDA50,0x5BDB50]')
source = source.replace('(0x635AB8,0x635D80)', '(0x635A00,0x635D80)')
source = source.replace('(0x61C200,0x61C400)', '(0x61C200,0x61C400), (0x61B000,0x61C200)')
exec(compile(source, str(p), 'exec'))
