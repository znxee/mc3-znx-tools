"""Independent relocation and on-disk baseline checks for frontend_mod_status."""
import os
import importlib.util
from pathlib import Path
import struct
import zipfile
here=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('packer',here.parents[1]/'mc3_mkmod.py')
packer=importlib.util.module_from_spec(spec); spec.loader.exec_module(packer)
mod=(here.parents[2]/'output/mods/frontend_mod_status.mod').read_bytes()
elf=(here/'frontend_mod_status.verify.elf').read_bytes()
base,offset,filesz,memsz=packer._seg(elf)
linked_base=next(s['addr'] for s in packer._sections(elf) if s['nome']=='.text')
relocated,_=packer.relocar(mod,linked_base)
reference=(elf[offset:offset+filesz]+bytes(memsz-filesz))[linked_base-base:]
assert relocated==reference,'Relocation differs from independent PS2 linker'
assert mod==Path(os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'frontend_mod_status.mod')).read_bytes()
print('PASS: relocation matches linker at %08X; HostFS identical'%linked_base)
baseline=here.parents[2]/'output/freecam_test/PCSX2/sstates/SLUS-21355 (B3FD5361).08.p2s'
if baseline.exists():
    import sys
    sys.path.insert(0,str(here.parents[1]))
    from mc3_inject import ee_from_savestate
    mem=ee_from_savestate(str(baseline))
    u=lambda a:struct.unpack_from('<I',mem,a)[0]
    name=u(0x616028)
    print('Baseline default font:',hex(name),mem[name:name+80].split(b'\0')[0])
    active=u(0x70E500)
    print('Active allocator:',hex(active),'base/top/cursor:',[hex(u(active+i)) for i in (4,8,12)])
    print('Memtop depth:',u(0x715864))
    assert (u(0x1A2E2C),u(0x1A2E30)) == (0x0C080FF2,0x8E445708)
    print('PASS: frontend compositor hook signature in baseline')
