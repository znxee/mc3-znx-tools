import os
from pathlib import Path
import struct
import sys
sys.path.insert(0, os.environ.get('MC3BOOT', 'mc3boot'))
import mc3_mkmod

path = Path(sys.argv[1])
data = path.read_bytes()
hdrs = mc3_mkmod._sections(data)
va, off, fsz, msz = mc3_mkmod._seg(data)
rows = mc3_mkmod._relocs(data, hdrs, va)
for i, (addr, typ) in enumerate(rows):
    if 0x780 <= addr <= 0x980:
        print(i, f"0x{addr:04X}", typ)

