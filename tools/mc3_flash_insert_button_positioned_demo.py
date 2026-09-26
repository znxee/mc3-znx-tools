# -*- coding: utf-8 -*-
"""Same object-insertion technique as insert_button.py, but the new BUTTON
gets its OWN screen position instead of landing wherever the cloned template
happened to sit.

Two ways to move an object were on the table:
  (a) fill in PlaceObject2's own translation pointer (+0x14) with a real
      2-float (tx,ty) block
  (b) attach a ClipEvent to the placement and move it with AVM1, the exact
      idiom the retail movies already use (`this._x = ...`)

(a) turned out to be a dead end worth recording: across bg_arcade.pck's 823
PlaceObject2 commands, every one with a real (non-0xFFFF) character index had
tx=ty=0.0 - every moving/positioned thing in that file (bird, pipes) is
positioned by SCRIPT instead, every frame. The engine supports a baked
translation, but nothing actually baked one.

So this uses (b): a `PlaceObject2ClipEvent` (command type 1, not type 0)
placing the new button, carrying ONE clip event (flag 2, the same value the
retail per-frame update event already uses in this exact file) whose bytecode
sets `this._x` / `this._y` once the instance exists. Layout for type 1, from
the same disassembly of swfCMD_PlaceObject2::Update plus swfCMD_
PlaceObject2ClipEvent::Update (which calls into it first) and a real example
(cmd@0x26B0 in controller.pck):

    +0x00 u8   type = 1
    +0x01 u8   matrix-override flags (kept 0 here - same reasoning as before)
    +0x04 u32  next command
    +0x08 u32  class constant (0x00211D10 - different from type 0's 0x211D30)
    +0x0E u16  character = object table index (same field as type 0)
    +0x10 u32  self-ref pointer (target offset shifts because of the fields
               below - the generic relocator does not care where it points,
               only that it stays self-referencing)
    +0x1C u32  clip event count
    +0x20 u32  pointer to the event array, 0x0C bytes per entry:
                 +0x00 u32 event flag(s)
                 +0x04 u32 pointer to the AVM1 bytecode (u16 refcount prefix
                       first, same convention as every other action list)
    +0x24 u32  ANOTHER self-ref pointer (probably an optional ColorTransform)
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mc3allin'))
import mc3pck as M

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools', 'output', 'flash/controller.pck.orig_backup')
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools', 'output', 'flash/controller_button_positioned.pck')

CLONE_BUTTON_IDX = 18
TEMPLATE_CMD = 0x26B0      # a real PlaceObject2ClipEvent, root frame's own
CMD_SPAN = 0x38      # must cover both self-refs (+0x10 -> +0x28, +0x24 -> +0x30)
TARGET_FRAME = 0xC0
NEW_TX, NEW_TY = 250.0, 150.0   # deliberately far from the original's spot

p = M.Pck(SRC)
assert p.valid()


def va_to_off(va):
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


def append_aligned(buf, chunk):
    if len(buf) % 4:
        buf.extend(b'\0' * (4 - len(buf) % 4))
    start = len(buf)
    buf.extend(chunk)
    return start


objs_by_idx = {i: (o, k) for i, o, k in p.objects()}
btn_off, btn_kind = objs_by_idx[CLONE_BUTTON_IDX]
assert btn_kind == 'BUTTON'
next_off = min(o for i, (o, k) in objs_by_idx.items() if o > btn_off)
btn_bytes = bytearray(p.data[btn_off:next_off])

old_count = p.u16(M.HEADER_SIZE + 0x2A)
old_table_off = p.ptr(M.HEADER_SIZE + 0x10)
new_index = old_count

data = bytearray(p.data)

# 1) cloned button, self-references fixed for its new home
provisional = len(data) + (4 - len(data) % 4 if len(data) % 4 else 0)
displacement = provisional - btn_off
n_fixed = fix_self_refs(btn_bytes, btn_off, next_off, displacement)
print('button clone: relocated %d self-reference(s)' % n_fixed)
new_btn_off = append_aligned(data, btn_bytes)
assert new_btn_off == provisional

# 2) object table, grown by one slot
old_table = data[old_table_off:old_table_off + old_count * 4]
new_table_off = append_aligned(data, old_table + b'\0\0\0\0')
new_table_ptr_field = p.pckoffset + (new_table_off - M.HEADER_SIZE)
new_btn_ptr = p.pckoffset + (new_btn_off - M.HEADER_SIZE)
struct.pack_into('<I', data, new_table_off + new_index * 4, new_btn_ptr)

# 3) the AVM1 that will run once the placed instance exists. Position ALONE
# on button #18 turned out visually inconclusive (its up-state graphic may be
# small/transparent - it's a hit region, not necessarily big art) - blow it
# up 5x at the same time so *something* is unmistakable on screen regardless
# of whatever button18 actually looks like.
def set_prop(name, value):
    return [
        M.Action(0, 0x96, 'Push', M.Pck.encode_push([('string', 'this')])),
        M.Action(0, 0x1C, 'GetVariable', b''),
        M.Action(0, 0x96, 'Push', M.Pck.encode_push([('string', name), ('double', value)])),
        M.Action(0, 0x4F, 'SetMember', b''),
    ]


actions = (set_prop('_x', NEW_TX) + set_prop('_y', NEW_TY)
           + set_prop('_xscale', 500.0) + set_prop('_yscale', 500.0)
           + [M.Action(0, 0, 'End', b'')])
blob = p.assemble_list(actions, budget=None, pool=None)
code_off = len(data)
if code_off % 4:
    code_off += 4 - (code_off % 4)
data.extend(b'\0' * (code_off - len(data)))
refcount_off = code_off
data.extend(struct.pack('<H', 1))
data.extend(blob)
code_va = p.pckoffset + (refcount_off - M.HEADER_SIZE)

# 4) the clip-event array: one entry, flag=2 (the value this same file's own
# per-frame update event already uses), pointing at the code above
event_arr_off = append_aligned(data, struct.pack('<III', 2, code_va, 0))
event_arr_va = p.pckoffset + (event_arr_off - M.HEADER_SIZE)

# 5) the PlaceObject2ClipEvent command itself: clone the header, force flags
# to 0 (skip the matrix path entirely - position comes from the script
# above), fix its own self-references, then set the fields that matter
tmpl = bytearray(data[TEMPLATE_CMD:TEMPLATE_CMD + CMD_SPAN])
tmpl[0x01] = 0
provisional_cmd = len(data) + (4 - len(data) % 4 if len(data) % 4 else 0)
cmd_displacement = provisional_cmd - TEMPLATE_CMD
n_fixed_cmd = fix_self_refs(tmpl, TEMPLATE_CMD, TEMPLATE_CMD + CMD_SPAN, cmd_displacement)
new_cmd_off = append_aligned(data, tmpl)
assert new_cmd_off == provisional_cmd
print('command clone: relocated %d self-reference(s)' % n_fixed_cmd)

old_first_cmd_va = p.u32(TARGET_FRAME + 4)
struct.pack_into('<H', data, new_cmd_off + 0x0E, new_index)
struct.pack_into('<I', data, new_cmd_off + 0x04, old_first_cmd_va)
struct.pack_into('<I', data, new_cmd_off + 0x1C, 1)
struct.pack_into('<I', data, new_cmd_off + 0x20, event_arr_va)

# 6) splice into the frame, ahead of everything that was there
new_cmd_va = p.pckoffset + (new_cmd_off - M.HEADER_SIZE)
struct.pack_into('<I', data, TARGET_FRAME + 4, new_cmd_va)

# 7) header bookkeeping
struct.pack_into('<H', data, M.HEADER_SIZE + 0x2A, old_count + 1)
struct.pack_into('<I', data, M.HEADER_SIZE + 0x10, new_table_ptr_field)
struct.pack_into('<I', data, 0x0C, len(data) - M.HEADER_SIZE)

with open(OUT, 'wb') as f:
    f.write(data)
print('wrote', OUT, len(data), 'bytes (was', len(p.data), ')')

# -- verify -------------------------------------------------------------
chk = M.Pck(OUT)
print('valid() =', chk.valid())
objs2 = list(chk.objects())
print('object count now:', len(objs2))
assert objs2[-1][0] == new_index and objs2[-1][2] == 'BUTTON'

cmd = chk.ptr(TARGET_FRAME + 4)
print('frame 0 first cmd @0x%X type=%d char_idx=%d flags=%d'
      % (cmd, chk.u8(cmd), chk.u16(cmd + 0xE), chk.u8(cmd + 1)))
assert cmd == new_cmd_off
assert chk.u16(cmd + 0xE) == new_index
assert chk.u8(cmd + 1) == 0

n = chk.u32(cmd + 0x1C)
arr = chk.ptr(cmd + 0x20)
print('clip events:', n, 'array @', hex(arr))
flag, codeva, _res = struct.unpack_from('<III', chk.data, arr)
code_off_chk = chk.ptr(arr + 4)
print('event flag=%d  code @0x%X' % (flag, code_off_chk))
acts_chk = chk.disasm(code_off_chk + M.ACTIONLIST_PREFIX)
print(chk.format(acts_chk, chk.constant_pool(acts_chk)))
