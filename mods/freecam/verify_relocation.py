"""Compare packed relocation with an independent PS2 linker at another base."""
import os
import importlib.util
from pathlib import Path
import struct

here=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('packer',here.parents[1]/'mc3_mkmod.py')
packer=importlib.util.module_from_spec(spec); spec.loader.exec_module(packer)
mod=(here.parents[2]/'output'/'mods'/'freecam.mod').read_bytes()
elf=(here/'freecam.verify.elf').read_bytes()
base,offset,filesz,memsz=packer._seg(elf)
text_section=next(s for s in packer._sections(elf) if s['nome']=='.text')
linked_base=text_section['addr']
relocated=packer.relocar(mod,linked_base)
# relocar returns the relocated code followed by entry as defined by packer.
if isinstance(relocated,tuple): relocated=next(x for x in relocated if isinstance(x,(bytes,bytearray)))
reference=(elf[offset:offset+filesz]+bytes(memsz-filesz))[linked_base-base:]
assert relocated[:len(reference)]==reference, 'Relocation differs from PS2 linker'
assert len(relocated)==len(reference), (len(relocated),len(reference))
assert struct.unpack_from('<I',mod,28)[0]==0, 'Must remain a per-frame module'
assert (here.parents[2]/'output'/'mods'/'freecam.mod').read_bytes()==Path(os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'freecam.mod')).read_bytes()
print('PASS: all packed relocations match independent linker at %08X; HostFS copy identical'%linked_base)
