# -*- coding: utf-8 -*-
"""Insert a genuinely NEW object (not just grow a script) into a .pck: clone
an existing BUTTON, register it as object #(count), and splice a new
PlaceObject2 command into an existing sprite's frame so the game actually
places it.

Cracked from the ELF, not guessed from bytes (swfCMD_PlaceObject2::Update,
0x00212D70, in slus_213.55.ELF):

    +0x00 u8   type = 0 (PlaceObject2)
    +0x01 u8   flags: bit0 = has scale/rotate matrix override (read via +0x10)
                       bit1 = has a second matrix component (rotate/skew)
               0 for both -> defaults to identity, and the 0x18.. payload
               bytes are then never read at all.
    +0x04 u32  next command in this frame's list (0 = end)
    +0x08 u32  class constant, identical across every sample in two files
    +0x0C u16  minor field, 1 in every sample seen
    +0x0E u16  CHARACTER = 1-based index into swfFILE's own object table.
               0xFFFF = "no character" (branch confirmed in Update).
    +0x10 u32  self-referencing pointer to +0x18 of THIS SAME command (an
               embedded-matrix accessor, same pattern SHAPE's own +0x10 uses)
    +0x14 u32  pointer to an external 3x4 translation matrix, 0 = origin/
               inherit (also null-checked in Update; every sample was 0)
    +0x18..    matrix payload -- DEAD BYTES when the +0x01 flags are 0,
               confirmed by Update never reading them in that case.

Because every field except +0x0E is either a constant or provably unread
in the "flags=0, no translation override" case, cloning a whole working
PlaceObject2 command and only touching +0x0E is safe -- we do not need to
know exactly where the struct "ends".
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mc3allin'))
import mc3pck as M

# SRC needs a clean, unmodified controller.pck - copy one out of
# $MC3_HOSTFS/ASSETS/flash/controller.pck before running if this path
# is missing (the backup here is a leftover from the session that proved
# this technique, not something the toolkit maintains on its own).
SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools', 'output', 'flash/controller.pck.orig_backup')
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools', 'output', 'flash/controller_new_button.pck')

CLONE_BUTTON_IDX = 18          # object index to duplicate
TEMPLATE_CMD = 0x1FF8          # a real, working PlaceObject2 to copy
TARGET_FRAME = 0xC0            # root's own frame struct (frame 0), +4 = first cmd
CMD_SPAN = 0x28                # bytes of the command struct we clone

p = M.Pck(SRC)
assert p.valid()

objs_by_idx = {i: (o, k) for i, o, k in p.objects()}
btn_off, btn_kind = objs_by_idx[CLONE_BUTTON_IDX]
assert btn_kind == 'BUTTON'
next_off = min(o for i, (o, k) in objs_by_idx.items() if o > btn_off)
btn_bytes = bytearray(p.data[btn_off:next_off])
print('cloning button #%d @0x%X, %d bytes (up to next object @0x%X)'
      % (CLONE_BUTTON_IDX, btn_off, len(btn_bytes), next_off))

# SHAPE and the PlaceObject2 command both turned out to carry a self-
# referencing pointer (an "embedded sub-struct" accessor pointing back into
# the SAME object, at a fixed offset). The crash from the first attempt
# (TLB miss inside swfBUTTON::Draw right after installing an unpatched clone)
# is consistent with BUTTON having the same pattern: a raw byte-for-byte copy
# keeps that pointer aimed at the ORIGINAL object's address, not the clone's.
# Fix generically: any u32 in the clone that resolves (via ptr()) to somewhere
# INSIDE the original object's own byte span gets shifted by the same
# displacement the object itself is moving by - self-references stay
# self-references, wherever the clone ends up.
def va_to_off(va):
    """Same math as Pck.ptr(), but applied to a VALUE already in hand
    instead of re-reading it from a file offset."""
    off = va - p.pckoffset + M.HEADER_SIZE
    return off if 0 <= off < len(p.data) else None


def fix_self_refs(buf, old_base, old_end, displacement):
    n = 0
    for k in range(0, len(buf) - 3, 4):
        v = struct.unpack_from('<I', buf, k)[0]
        if v == 0:
            continue
        target = va_to_off(v)
        if target is not None and old_base <= target < old_end:
            struct.pack_into('<I', buf, k, v + displacement)
            n += 1
    return n

old_count = p.u16(M.HEADER_SIZE + 0x2A)
old_table_off = p.ptr(M.HEADER_SIZE + 0x10)
new_index = old_count           # objects() iterates range(1, count); count IS
                                 # the first free slot number
print('old object count field =', old_count, '-> new object gets index', new_index)

data = bytearray(p.data)


def append_aligned(buf, chunk):
    if len(buf) % 4:
        buf.extend(b'\0' * (4 - len(buf) % 4))
    start = len(buf)
    buf.extend(chunk)
    return start


# 1) the cloned button object - land it, then fix any pointer that pointed
# back into the ORIGINAL object so it points into the CLONE instead
provisional = len(data) + (4 - len(data) % 4 if len(data) % 4 else 0)
displacement = provisional - btn_off
n_fixed = fix_self_refs(btn_bytes, btn_off, next_off, displacement)
print('relocated %d internal self-reference(s) inside the clone by %+d bytes'
      % (n_fixed, displacement))
new_btn_off = append_aligned(data, btn_bytes)
assert new_btn_off == provisional, 'alignment math drifted'

# 2) the object table, grown by one slot, old entries preserved verbatim
old_table = data[old_table_off:old_table_off + old_count * 4]
new_table_off = append_aligned(data, old_table + b'\0\0\0\0')
new_table_ptr_field = p.pckoffset + (new_table_off - M.HEADER_SIZE)
new_btn_ptr = p.pckoffset + (new_btn_off - M.HEADER_SIZE)
struct.pack_into('<I', data, new_table_off + new_index * 4, new_btn_ptr)

# 3) the new PlaceObject2 command: clone the template, then patch three fields
tmpl = bytes(data[TEMPLATE_CMD:TEMPLATE_CMD + CMD_SPAN])
new_cmd_off = append_aligned(data, tmpl)
old_first_cmd = p.ptr(TARGET_FRAME + 4)
new_next_field = p.u32(TARGET_FRAME + 4)          # raw VA, reuse verbatim
struct.pack_into('<H', data, new_cmd_off + 0x0E, new_index)      # character
struct.pack_into('<I', data, new_cmd_off + 0x04, new_next_field)  # -> old 1st
new_self_ptr = p.pckoffset + ((new_cmd_off + 0x18) - M.HEADER_SIZE)
struct.pack_into('<I', data, new_cmd_off + 0x10, new_self_ptr)

# 4) splice: the frame now points at OUR command first
new_cmd_va = p.pckoffset + (new_cmd_off - M.HEADER_SIZE)
struct.pack_into('<I', data, TARGET_FRAME + 4, new_cmd_va)

# 5) header updates: object count, object-table pointer, payload size
struct.pack_into('<H', data, M.HEADER_SIZE + 0x2A, old_count + 1)
struct.pack_into('<I', data, M.HEADER_SIZE + 0x10, new_table_ptr_field)
struct.pack_into('<I', data, 0x0C, len(data) - M.HEADER_SIZE)

with open(OUT, 'wb') as f:
    f.write(data)
print('wrote', OUT, len(data), 'bytes (was', len(p.data), ')')

# -- verify with a completely fresh parse ------------------------------------
chk = M.Pck(OUT)
print('valid() =', chk.valid())
objs2 = list(chk.objects())
print('object count now:', len(objs2), '(was', len(list(p.objects())), ')')
last_idx, last_off, last_kind = objs2[-1]
print('new object: idx=%d off=0x%X kind=%s (expected idx %d, kind BUTTON)'
      % (last_idx, last_off, last_kind, new_index))
assert last_idx == new_index and last_kind == 'BUTTON'
assert bytes(chk.data[last_off:last_off + len(btn_bytes)]) == btn_bytes, \
    'cloned button bytes do not match the original'

# walk the frame and confirm OUR command is first, and the chain still reaches
# every command the frame had before
cmd = chk.ptr(TARGET_FRAME + 4)
chain = []
seen = set()
while cmd and cmd not in seen and len(chain) < 20:
    seen.add(cmd)
    chain.append((cmd, chk.u8(cmd)))
    cmd = chk.ptr(cmd + 4)
print('frame 0 command chain now:', [(hex(c), t) for c, t in chain])
assert chain[0][0] == new_cmd_off and chain[0][1] == 0
assert chain[1][0] == old_first_cmd
print('OK: new PlaceObject2 is first in the chain, old chain follows intact')

char_idx = chk.u16(new_cmd_off + 0x0E)
print('new command character field = %d (expected %d)' % (char_idx, new_index))
assert char_idx == new_index
