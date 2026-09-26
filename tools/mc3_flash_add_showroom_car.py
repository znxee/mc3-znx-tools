# -*- coding: utf-8 -*-
"""Add a 24th car to toplevel.pck's Showroom catalog: vp_dlslot_23, appended
to ShowRoomNames and its eight parallel arrays, copying vp_dlslot_00's own
values (Price $0.00, Status '' , Class 1, Perf 'z', the three Min stats '0',
PrizeStatus '0') - the exact template every existing dlslot car already uses.

This is the SAME growth technique proven earlier (assemble_list, relocate to
end of file, fix the one pointer + the payload-size field), applied to the
one giant root script instead of a small standalone one. Branch targets
inside that 442-action script are safe: disasm() resolves them to ACTION
INDICES, not byte offsets, and assemble_list() recomputes displacements from
those indices - so growing one Push action in place does not disturb anything
that branches around it.
"""
import shutil
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mc3allin'))
import mc3pck as M

SRC = os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/flash/toplevel.pck')
BACKUP = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools', 'output', 'flash/toplevel.pck.orig_backup')
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools', 'output', 'flash/toplevel_dlslot23.pck')
ROOT_CODE = 0xE2

NEW_NAME = 'vp_dlslot_23'
ARRAYS = {
    'ShowRoomNames': NEW_NAME,
    'ShowRoomPrice': '$0.00',
    'ShowRoomStatus': '',
    'ShowroomClass': 1,
    'ShowroomPerf': 'z',
    'AccelMin': '0',
    'TopSpeedMin': '0',
    'HandlingMin': '0',
    'PrizeStatus': '0',
}

shutil.copy2(SRC, BACKUP)
p = M.Pck(SRC)
assert p.valid()

acts = p.disasm(ROOT_CODE)
pool = p.constant_pool(acts)
print('root script: %d actions, %d pool strings' % (len(acts), len(pool)))


def encode_value(v):
    if isinstance(v, str):
        return ('string', v)
    return ('int', v)


touched = 0
for i, a in enumerate(acts):
    if a.name != 'Push' or not a.values or a.name == 'End':
        continue
    t0, v0 = a.values[0]
    if t0 not in ('const8', 'const16'):
        continue
    prop = pool[v0]
    if prop not in ARRAYS:
        continue
    existing = [pool[v] if t in ('const8', 'const16') else v for t, v in a.values[1:-2]]
    if ARRAYS[prop] in existing:
        raise SystemExit(
            '%s already has %r - this script already ran against this file; '
            're-run it against a pristine toplevel.pck (see .orig_backup / '
            '.orig), not against its own output' % (prop, ARRAYS[prop]))
    cnt_t, cnt_v = a.values[-2]
    cls_t, cls_v = a.values[-1]
    assert cnt_t == 'int' and cls_t == 'const8' and pool[cls_v] == 'Array'
    new_val = encode_value(ARRAYS[prop])
    a.values = a.values[:-2] + [new_val, ('int', cnt_v + 1), (cls_t, cls_v)]
    touched += 1
    print('  grew %-16s %d -> %d elements (action #%d)' % (prop, cnt_v, cnt_v + 1, i))

assert touched == len(ARRAYS), 'expected %d arrays, touched %d' % (len(ARRAYS), touched)

blob = p.assemble_list(acts, budget=None, pool=None)
print('root script re-encoded: %d bytes (was %d)'
      % (len(blob), sum(a.size for a in p.disasm(ROOT_CODE))))

# -- splice the grown script into a fresh copy of the file -------------------
refs = p.actionlist_refs()
fields = refs[ROOT_CODE]
assert len(fields) == 1, 'root script has more than one reference, unexpected'
field = fields[0]

data = bytearray(p.data)
new_off = len(data)
if new_off % 4:
    new_off += 4 - (new_off % 4)
data.extend(b'\0' * (new_off - len(data)))
refcount_off = new_off
data.extend(struct.pack('<H', 1))
data.extend(blob)

new_ptr_value = p.pckoffset + (refcount_off - M.HEADER_SIZE)
struct.pack_into('<I', data, field, new_ptr_value)
struct.pack_into('<I', data, 0x0C, len(data) - M.HEADER_SIZE)

with open(OUT, 'wb') as f:
    f.write(data)
print('wrote', OUT, len(data), 'bytes (was', len(p.data), ')')

# -- verify: reparse, check every array's new length/value, and confirm every
# OTHER action list in the file is untouched -------------------------------
chk = M.Pck(OUT)
print('valid() =', chk.valid())
new_code = chk.ptr(field) + M.ACTIONLIST_PREFIX
new_acts = chk.disasm(new_code)
new_pool = chk.constant_pool(new_acts)
print('re-parsed root script: %d actions (was %d)' % (len(new_acts), len(acts)))


def array_values(acts_, pool_, action_idx):
    push = acts_[action_idx - 1]
    out = []
    for t, v in push.values[1:-2]:
        out.append(pool_[v] if t in ('const8', 'const16') else v)
    return out


checked = 0
for i, a in enumerate(new_acts):
    if a.name != 'Push' or not a.values:
        continue
    t0, v0 = a.values[0]
    if t0 not in ('const8', 'const16'):
        continue
    prop = new_pool[v0]
    if prop not in ARRAYS:
        continue
    vals = [new_pool[v] if t in ('const8', 'const16') else v for t, v in a.values[1:-2]]
    expected = ARRAYS[prop]
    ok = vals[-1] == expected and len(vals) == 118
    print('  %-16s len=%d last=%r  %s' % (prop, len(vals), vals[-1], 'OK' if ok else 'MISMATCH'))
    assert ok
    checked += 1
assert checked == len(ARRAYS)

# every OTHER action list in the file (unrelated to this one root script)
# must be byte-identical - this growth must not have disturbed anything else
orig_lists = {c: bytes(p.data[c:c + 200]) for _o, _k, c in p.action_lists() if c != ROOT_CODE}
new_lists = {c: bytes(chk.data[c:c + 200]) for _o, _k, c in chk.action_lists() if c != new_code}
assert len(orig_lists) == len(new_lists) == 388, (len(orig_lists), len(new_lists))
assert orig_lists == new_lists, 'some other action list changed bytes'
print('other action lists in the file: %d, byte-identical to before' % len(orig_lists))

print('OK - vp_dlslot_23 added to all 9 Showroom arrays, nothing else changed')
