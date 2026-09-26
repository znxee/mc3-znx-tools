#!/usr/bin/env python3
"""
Parser for Midnight Club 3 `.pck` files (baked Macromedia Flash / SWF resources).

Structure recovered from the engine's own loader code (see mc3elf.py):

  file header (128 bytes, not part of the addressable payload)
      +0x00  u32  pckoffset   virtual base the baked pointers assume
      +0x04  u32  version     5 = alpha build, 6 = retail; hard-checked on load
      +0x08  u32  flag        must be 1
      +0x0C  u32  size        payload size == filesize - 128

  payload starts at file offset 0x80 and IS the swfFILE object
  (datRscBuilder::LoadBuild reads it straight into an aligned allocation,
   then swfFILE::swfFILE walks the graph relocating pointers by a single delta)

      swfFILE   +0x10 u32  object table pointer
                +0x2A u16  object count   (iterated from index 1)
      swfOBJECT +0x00 u8   type 1..8 (SHAPE SPRITE BUTTON BITMAP FONT TEXT EDITTEXT SOUND)
                +0x04 u32  vtable
      swfSPRITE +0x08 u16  frame count
                +0x0C u32  frame array pointer (8 bytes per frame)
      swfFRAME  +0x04 u32  first swfCMD
      swfCMD    +0x00 u8   type 0..4 (PlaceObject2 PlaceObject2ClipEvent
                              RemoveObject2 DoAction DoInitAction)
                +0x04 u32  next swfCMD
                +0x08 u32  vtable
                +0x0C u32  action bytecode (types 3 and 4 only)

  Bytecode also hangs off two other places, which is where the bulk of it lives:

      swfCMD_PlaceObject2ClipEvent (cmd type 1)
                +0x1C s32  clip event count
                +0x20 u32  CLIPEVENT array (0x0C bytes each), each +0x04 -> bytecode
      swfBUTTON +0x0A u8   cond-action count
                +0x10 u32  BUTTONCONDACTION array (8 bytes each), each +0x04 -> bytecode

Pointer conversion: file_offset = P - pckoffset + 0x80

Usage:
    python mc3pck.py <file.pck>                 summary
    python mc3pck.py <file.pck> --actions       list action lists
    python mc3pck.py <file.pck> --dis <offset>  disassemble one action list
    python mc3pck.py <file.pck> --grep <text>   disassemble lists mentioning text
"""

import os
import re
import shutil
import struct
import sys

HEADER_SIZE = 0x80

BACKUP_SUFFIX = ".bak"


def make_backup(path, suffix=BACKUP_SUFFIX):
    """Copy `path` alongside itself before it is overwritten.

    The backup is always refreshed, so it holds the file exactly as it stood
    immediately before the current save. Note that this means saving twice
    leaves the .bak holding the *first* edit rather than the pristine original
    -- keep a separate untouched copy if that matters.

    Returns the backup path, or None if there was nothing to back up.
    """
    if not os.path.exists(path):
        return None
    bak = path + suffix
    shutil.copy2(path, bak)
    return bak

# A bytecode pointer aims at a swfACTIONLIST: u16 refcount, then the AVM1 stream.
# (swfACTIONLIST::Release exists in the symbol table, hence the refcount.)
ACTIONLIST_PREFIX = 2

OBJECT_TYPES = {1: "SHAPE", 2: "SPRITE", 3: "BUTTON", 4: "BITMAP",
                5: "FONT", 6: "TEXT", 7: "EDITTEXT", 8: "SOUND"}
CMD_TYPES = {0: "PlaceObject2", 1: "PlaceObject2ClipEvent", 2: "RemoveObject2",
             3: "DoAction", 4: "DoInitAction"}
CMD_WITH_ACTIONS = (3, 4)

# AVM1 opcodes, cross-checked against this build's dispatch table at 0x005F50C0.
# Names marked (*) share a handler with another opcode in this build.
ACTIONS = {
    0x04: "NextFrame", 0x05: "PrevFrame", 0x06: "Play", 0x07: "Stop",
    0x0A: "Add", 0x0B: "Subtract", 0x0C: "Multiply", 0x0D: "Divide",
    0x0E: "Equals", 0x0F: "Less", 0x10: "And", 0x11: "Or", 0x12: "Not",
    0x13: "StringEquals", 0x14: "StringLength", 0x15: "StringExtract",
    0x17: "Pop", 0x18: "ToInteger", 0x1C: "GetVariable", 0x1D: "SetVariable",
    0x21: "StringAdd", 0x26: "Trace", 0x29: "StringLess",
    0x30: "RandomNumber", 0x31: "MBStringLength", 0x32: "CharToAscii",
    0x33: "AsciiToChar", 0x34: "GetTime", 0x35: "MBStringExtract",
    0x36: "MBCharToAscii", 0x37: "MBAsciiToChar",
    0x3C: "DefineLocal", 0x3F: "Modulo", 0x40: "NewObject",
    0x41: "DefineLocal2", 0x44: "TypeOf", 0x47: "Add2", 0x48: "Less2",
    0x49: "Equals2", 0x4C: "PushDuplicate", 0x4D: "StackSwap",
    0x4E: "GetMember", 0x4F: "SetMember", 0x50: "Increment",
    0x51: "Decrement", 0x52: "CallMethod",
    0x60: "BitAnd", 0x61: "BitOr", 0x62: "BitXor", 0x63: "BitLShift",
    0x64: "BitRShift", 0x65: "BitURShift", 0x67: "Greater",
    0x81: "GotoFrame", 0x88: "ConstantPool", 0x96: "Push",
    0x99: "Jump", 0x9D: "If",
}

PUSH_TYPES = {0: "string", 1: "float", 2: "null", 3: "undefined", 4: "register",
              5: "boolean", 6: "double", 7: "int", 8: "const8", 9: "const16"}


# Opcodes outside the dispatch table's 0x04..0x9D range fall straight through to
# the interpreter's loop continuation with the operand width forced to zero
# (`move $s7, $zero` at 0x00208B9C), which makes them true one-byte no-ops. The
# original compiler emits 0x01 as filler itself.
NOP = 0x01

# Only Jump and If carry a target, encoded as a signed 16-bit displacement from
# the byte after the action header.
BRANCH_OPS = (0x99, 0x9D)

# Inverse of ACTIONS, for the script editor's parser -- plus the two codes
# disasm()/assemble_list() name specially rather than through the table
# (0 has no dedicated opcode entry since it is the literal end-of-stream
# marker, not a real instruction; NOP is a filler byte the compiler itself
# emits, never a named action in ACTIONS).
_NAME_TO_CODE = {name: code for code, name in ACTIONS.items()}
_NAME_TO_CODE["End"] = 0
_NAME_TO_CODE["Nop"] = NOP
_UNKNOWN_NAME_RE = re.compile(r'^Unknown_([0-9A-Fa-f]{2})$')


class BudgetError(Exception):
    """Raised when a re-assembled action list will not fit its byte budget."""

    def __init__(self, needed, available):
        self.needed, self.available = needed, available
        super().__init__(
            f"re-assembled list needs {needed} bytes but only {available} are "
            f"available ({needed - available:+d}); growing a list is the "
            f"insertion problem and is not supported")


class ScriptParseError(ValueError):
    """Every problem found in edited script text, not just the first --
    parse_script() collects them all so one dialog can list everything
    wrong instead of a fix-one-run-again loop."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("\n".join(self.errors))


class Action:
    __slots__ = ("offset", "code", "name", "body", "values", "target",
                 "target_index")

    def __init__(self, offset, code, name, body):
        self.offset, self.code, self.name, self.body = offset, code, name, body
        self.values = None        # Push only: list of (type_name, value)
        self.target = None        # Jump/If only: absolute file offset
        self.target_index = None  # Jump/If only: index of the target action

    @property
    def size(self):
        return (3 + len(self.body)) if self.code >= 0x80 else 1

    def clone(self):
        a = Action(self.offset, self.code, self.name, self.body)
        a.values = list(self.values) if self.values is not None else None
        a.target, a.target_index = self.target, self.target_index
        return a


class Pck:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            self.data = bytearray(f.read())
        self.pckoffset = self.u32(0)
        self.version = self.u32(4)
        self.flag = self.u32(8)
        self.size = self.u32(0xC)

    # -- primitives --------------------------------------------------------

    def u8(self, o):
        return self.data[o]

    def u16(self, o):
        return struct.unpack_from("<H", self.data, o)[0]

    def u32(self, o):
        return struct.unpack_from("<I", self.data, o)[0]

    def ptr(self, o):
        """Read a baked pointer and convert it to a file offset (0 -> None)."""
        p = self.u32(o)
        if p == 0:
            return None
        fo = p - self.pckoffset + HEADER_SIZE
        return fo if 0 <= fo < len(self.data) else None

    def cstr(self, o):
        e = self.data.index(b"\0", o)
        return self.data[o:e].decode("latin-1")

    def valid(self):
        return (self.flag == 1 and self.size == len(self.data) - HEADER_SIZE
                and self.version in (5, 6))

    # -- object graph ------------------------------------------------------

    def objects(self):
        """Yield (index, file_offset, type_name) for each root object."""
        table = self.ptr(HEADER_SIZE + 0x10)
        count = self.u16(HEADER_SIZE + 0x2A)
        if table is None:
            return
        for i in range(1, count):
            o = self.ptr(table + i * 4)
            if o is None:
                continue
            yield i, o, OBJECT_TYPES.get(self.u8(o), f"BAD({self.u8(o)})")

    def sprites(self):
        """Root swfFILE first (it derives from swfSPRITE), then table sprites."""
        yield HEADER_SIZE
        for _i, obj, kind in self.objects():
            if kind == "SPRITE":
                yield obj

    def commands(self):
        """Yield (sprite_offset, frame_index, cmd_offset, cmd_type_name)."""
        for obj in self.sprites():
            frames = self.ptr(obj + 0xC)
            if frames is None:
                continue
            for fi in range(self.u16(obj + 8)):
                cmd = self.ptr(frames + fi * 8 + 4)
                seen = set()
                while cmd is not None and cmd not in seen:
                    seen.add(cmd)
                    yield obj, fi, cmd, CMD_TYPES.get(self.u8(cmd), f"BAD({self.u8(cmd)})")
                    cmd = self.ptr(cmd + 4)

    def actionlist_refs(self):
        """Map each action list to the pointer fields that reach it.

        Returns {stream_offset: [pointer_field_offset, ...]}. An action list is
        a self-contained blob: the only things pointing at it are these fields,
        and every branch inside it is PC-relative. That makes it safe to move
        the whole blob elsewhere in the file and rewrite these pointers --
        which is how a list can be grown without shifting anything else.
        """
        refs = {}

        def add(field):
            target = self.ptr(field)
            if target is not None:
                refs.setdefault(target + ACTIONLIST_PREFIX, []).append(field)

        for _obj, _fi, cmd, _kind in self.commands():
            t = self.u8(cmd)
            if t in CMD_WITH_ACTIONS:
                add(cmd + 0xC)
            elif t == 1:  # PlaceObject2ClipEvent
                events = self.ptr(cmd + 0x20)
                n = struct.unpack_from("<i", self.data, cmd + 0x1C)[0]
                if events is not None and 0 < n < 4096:
                    for k in range(n):
                        add(events + k * 0xC + 4)

        for _i, obj, kind in self.objects():
            if kind != "BUTTON":
                continue
            conds = self.ptr(obj + 0x10)
            if conds is not None:
                for k in range(self.u8(obj + 0xA)):
                    add(conds + k * 8 + 4)
        return refs

    def action_lists(self):
        """Yield (owner_offset, source_label, bytecode_offset), de-duplicated.

        Covers all three places the engine relocates a bytecode pointer:
        DoAction/DoInitAction commands, PlaceObject2 clip events, and
        button condition-actions.
        """
        seen = set()

        def emit(owner, label, code):
            if code is not None and code not in seen:
                seen.add(code)
                return [(owner, label, code + ACTIONLIST_PREFIX)]
            return []

        for _obj, _fi, cmd, kind in self.commands():
            t = self.u8(cmd)
            if t in CMD_WITH_ACTIONS:
                yield from emit(cmd, kind, self.ptr(cmd + 0xC))
            elif t == 1:  # PlaceObject2ClipEvent
                events = self.ptr(cmd + 0x20)
                n = struct.unpack_from("<i", self.data, cmd + 0x1C)[0]
                if events is None or not 0 < n < 4096:
                    continue
                for k in range(n):
                    yield from emit(cmd, f"ClipEvent[{k}]", self.ptr(events + k * 0xC + 4))

        for _i, obj, kind in self.objects():
            if kind != "BUTTON":
                continue
            conds = self.ptr(obj + 0x10)
            if conds is None:
                continue
            for k in range(self.u8(obj + 0xA)):
                yield from emit(obj, f"ButtonAction[{k}]", self.ptr(conds + k * 8 + 4))

    # -- AVM1 --------------------------------------------------------------

    def disasm(self, offset, limit=100000):
        """Decode one AVM1 action list starting at a file offset."""
        out = []
        o = offset
        end = min(len(self.data), offset + limit)
        while o < end:
            code = self.data[o]
            if code == 0:  # end-of-actions marker
                out.append(Action(o, 0, "End", b""))
                break
            name = ACTIONS.get(code, f"Unknown_{code:02X}")
            if code >= 0x80:
                ln = self.u16(o + 1)
                body = bytes(self.data[o + 3:o + 3 + ln])
                a = Action(o, code, name, body)
                if code == 0x96:
                    a.values = self._decode_push(body)
                elif code in BRANCH_OPS:
                    # The handler does `s4 += s7 + rel` with s4 already past the
                    # 3-byte header and s7 holding the body length, so the
                    # displacement is measured from the END of the action.
                    rel = struct.unpack("<h", body[:2])[0]
                    a.target = o + a.size + rel
            else:
                a = Action(o, code, name, b"")
            out.append(a)
            o += a.size

        # Resolve branch targets to action indices so the list can be laid out
        # again at different offsets. A target landing one past the last action
        # (i.e. off the end of the list) is represented as len(out).
        index_of = {a.offset: i for i, a in enumerate(out)}
        if out:
            index_of.setdefault(out[-1].offset + out[-1].size, len(out))
        for a in out:
            if a.target is not None:
                a.target_index = index_of.get(a.target)
        return out

    def _decode_push(self, body):
        vals, i = [], 0
        while i < len(body):
            t = body[i]
            i += 1
            try:
                if t == 0:
                    e = body.index(b"\0", i)
                    vals.append(("string", body[i:e].decode("latin-1")))
                    i = e + 1
                elif t == 1:
                    vals.append(("float", struct.unpack_from("<f", body, i)[0])); i += 4
                elif t in (2, 3):
                    vals.append((PUSH_TYPES[t], None))
                elif t in (4, 5, 8):
                    vals.append((PUSH_TYPES[t], body[i])); i += 1
                elif t == 6:
                    vals.append(("double", struct.unpack_from("<d", body, i)[0])); i += 8
                elif t == 7:
                    vals.append(("int", struct.unpack_from("<i", body, i)[0])); i += 4
                elif t == 9:
                    vals.append(("const16", struct.unpack_from("<H", body, i)[0])); i += 2
                else:
                    vals.append((f"badtype{t}", body[i:].hex())); break
            except (struct.error, ValueError):
                vals.append(("truncated", body[i:].hex())); break
        return vals

    @staticmethod
    def encode_push(values, wide=()):
        """Inverse of _decode_push.

        `wide` is a set of value indices to force to the 3-byte const16 form
        even when const8 would reach; assemble_list uses that to spend a spare
        byte. Pool indices of 256 or more are always const16 regardless.
        """
        out = bytearray()
        for i, (t, v) in enumerate(values):
            if t in ("const8", "const16"):
                if v >= 256 or t == "const16" or i in wide:
                    out.append(9)
                    out.extend(struct.pack("<H", v))
                else:
                    out.extend((8, v))
            elif t == "string":
                out.append(0)
                out.extend(v.encode("latin-1") + b"\0")
            elif t == "float":
                out.append(1)
                out.extend(struct.pack("<f", v))
            elif t in ("null", "undefined"):
                out.append(2 if t == "null" else 3)
            elif t in ("register", "boolean"):
                out.append(4 if t == "register" else 5)
                out.append(int(v) & 0xFF)
            elif t == "double":
                out.append(6)
                out.extend(struct.pack("<d", v))
            elif t == "int":
                out.append(7)
                out.extend(struct.pack("<i", v))
            else:
                raise ValueError(f"cannot encode push value of type {t!r}")
        return bytes(out)

    @staticmethod
    def encode_pool(strings):
        """Body of a 0x88 ConstantPool action: u16 count, then C strings."""
        out = bytearray(struct.pack("<H", len(strings)))
        for s in strings:
            out.extend(s.encode("latin-1") + b"\0")
        return bytes(out)

    def natural_size(self, actions, pool=None):
        """Bytes this list needs with no padding -- the figure a budget bar wants."""
        total = 0
        for a in actions:
            if a.code == 0x88 and pool is not None:
                total += 3 + len(self.encode_pool(pool))
            elif a.code == 0x96 and a.values is not None:
                total += 3 + len(self.encode_push(a.values))
            else:
                total += a.size
        return total

    def assemble_list(self, actions, budget=None, pool=None):
        """Re-emit a full action list into exactly `budget` bytes.

        `budget=None` emits the list at its natural size with no padding, for
        callers that will relocate it rather than fit it in place.

        Branch displacements are recomputed from each branch's target_index, so
        actions may change size freely. Any shortfall is padded with one-byte
        no-ops placed immediately before the End marker; a single spare byte
        that padding cannot express is spent by widening a const8 to const16.

        Raises BudgetError if the result cannot be made to fit.
        """
        acts = [a.clone() for a in actions]

        if pool is not None:
            for a in acts:
                if a.code == 0x88:
                    a.body = self.encode_pool(pool)
                    break
            else:
                raise ValueError("list has no ConstantPool action to replace")

        for a in acts:
            if a.code == 0x96 and a.values is not None:
                a.body = self.encode_push(a.values)
            if a.code in BRANCH_OPS and a.target_index is None:
                raise ValueError(
                    f"branch at 0x{a.offset:06X} has an unresolved target; "
                    f"this list cannot be re-laid out")

        def layout(pad, wide_at=None):
            """Place actions, returning (bytes, total). `pad` no-ops go before End."""
            work = [a.clone() for a in acts]
            if wide_at is not None:
                ai, vi = wide_at
                work[ai].body = self.encode_push(work[ai].values, wide={vi})

            # seq pairs each emitted action with the original index it came from
            # (None for inserted padding), so branch targets remap exactly.
            tail = 1 if work and work[-1].code == 0 else 0
            head = len(work) - tail
            seq = [(i, work[i]) for i in range(head)]
            seq += [(None, Action(0, NOP, "Nop", b"")) for _ in range(pad)]
            seq += [(i, work[i]) for i in range(head, len(work))]

            offs, o = [], 0
            for _i, a in seq:
                offs.append(o)
                o += a.size
            offs.append(o)  # one-past-the-end, for branches off the end of the list
            pos = {i: k for k, (i, _a) in enumerate(seq) if i is not None}

            out = bytearray()
            for k, (_i, a) in enumerate(seq):
                if a.code in BRANCH_OPS:
                    dest = offs[pos.get(a.target_index, len(seq))]
                    rel = dest - (offs[k] + a.size)
                    if not -0x8000 <= rel < 0x8000:
                        raise BudgetError(o, budget)
                    a.body = struct.pack("<h", rel) + a.body[2:]
                out.append(a.code)
                if a.code >= 0x80:
                    out.extend(struct.pack("<H", len(a.body)))
                    out.extend(a.body)
            return bytes(out), len(out)

        blob, natural = layout(0)
        if budget is None:
            return blob
        if natural > budget:
            raise BudgetError(natural, budget)

        pad = budget - natural
        blob, total = layout(pad)
        if total == budget:
            return blob

        # Padding is one byte per no-op, so this should not happen; kept as a
        # belt-and-braces path that widens a const8 to spend an odd byte.
        for ai, a in enumerate(acts):
            if a.code == 0x96 and a.values:
                for vi, (t, v) in enumerate(a.values):
                    if t == "const8" and v < 256:
                        blob, total = layout(max(0, pad - 1), wide_at=(ai, vi))
                        if total == budget:
                            return blob
        raise BudgetError(total, budget)

    def assemble(self, actions):
        """Re-encode a decoded action list; inverse of disasm()."""
        out = bytearray()
        for a in actions:
            if a.code == 0:
                out.append(0)
            elif a.code >= 0x80:
                out.append(a.code)
                out += struct.pack("<H", len(a.body))
                out += a.body
            else:
                out.append(a.code)
        return bytes(out)

    def format(self, actions, pool=None):
        lines = []
        for a in actions:
            if a.values is not None:
                parts = []
                for t, v in a.values:
                    if t in ("const8", "const16") and pool and v < len(pool):
                        parts.append(f'const[{v}]="{pool[v]}"')
                    elif t == "string":
                        parts.append(f'"{v}"')
                    elif v is None:
                        parts.append(t)
                    else:
                        parts.append(f"{t}({v})")
                arg = ", ".join(parts)
            elif a.target is not None:
                arg = f"-> 0x{a.target:X}"
            elif a.body:
                arg = a.body.hex(" ")
            else:
                arg = ""
            lines.append(f"  0x{a.offset:06X}  {a.name:<16} {arg}")
        return "\n".join(lines)

    def global_arrays(self):
        """Yield every `_global.<name> = new Array(...)` construction as
        (name, [elements], push_offset, script_offset).

        Shape in the bytecode is a single Push carrying
        [propName, *elements, count, "Array"] followed by NewObject (0x40).
        The count is normally an int; grow_category.py re-encodes it as a
        string (asInt routes strings through atoi), so both are accepted.
        """
        for _owner, _label, code in self.action_lists():
            acts = self.disasm(code)
            pool = self.constant_pool(acts)
            if not pool:
                continue
            for i, a in enumerate(acts):
                if a.code != 0x96 or not a.values or len(a.values) < 4:
                    continue
                if i + 1 >= len(acts) or acts[i + 1].code != 0x40:
                    continue
                t0, v0 = a.values[0]
                tc, vc = a.values[-2]
                tn, vn = a.values[-1]
                if not (t0.startswith("const") and tn.startswith("const")):
                    continue
                if v0 >= len(pool) or vn >= len(pool) or pool[vn] != "Array":
                    continue
                try:
                    declared = int(vc)
                except (TypeError, ValueError):
                    continue
                if declared != len(a.values) - 3:
                    continue
                els = [pool[v] if (t.startswith("const") and v < len(pool)) else v
                       for t, v in a.values[1:-2]]
                yield pool[v0], els, a.offset, code

    def constant_pool(self, actions):
        """Extract the pool from a leading ConstantPool action, if present."""
        for a in actions:
            if a.code == 0x88:
                count = struct.unpack_from("<H", a.body, 0)[0]
                strs = a.body[2:].split(b"\0")
                return [s.decode("latin-1") for s in strs[:count]]
        return None


# ---------------------------------------------------------------------------
# The Scripts tab's editable text format: format_script()/parse_script() are
# an inverse pair, unlike Pck.format() (read-only, addresses baked in).
#
# format() labels each action by its file offset, which is fine for display
# but goes stale the instant a line is added or removed -- every action
# after the edit point shifts. format_script() instead labels each line
# L<n> by its position in the list being shown; a trailing, nameless L<n>
# line (n = len(actions)) stands for "one action past the end of this
# list", the same target disasm() gives a branch that jumps straight out.
# parse_script() resolves "-> L<n>" purely by matching labels, so the
# labels' numeric value never has to track position -- only uniqueness and
# "does some line claim it" matter, which is what lets lines move, get
# inserted, or get deleted at all.

def _escape_str(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _unescape_str(s):
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            out.append(s[i + 1])
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def format_script(actions, pool=None):
    """The Scripts tab's editable text form. See module note above."""
    lines = [f"L{i:03d}: {_format_op(a, pool)}" for i, a in enumerate(actions)]
    lines.append(f"L{len(actions):03d}:")
    return "\n".join(lines)


def _format_op(a, pool):
    if a.code == 0x88:
        return ("ConstantPool     ; the pool itself -- edit its strings on "
                "the Strings tab, not here")
    if a.values is not None:
        parts = []
        for t, v in a.values:
            if t in ("const8", "const16") and pool is not None and 0 <= v < len(pool):
                parts.append(f'const[{v}]="{_escape_str(pool[v])}"')
            elif t == "string":
                parts.append(f'"{_escape_str(v)}"')
            elif v is None:
                parts.append(t)
            else:
                parts.append(f"{t}({v})")
        return f"{a.name:<16} " + ", ".join(parts)
    if a.code in BRANCH_OPS:
        target = f"L{a.target_index:03d}" if a.target_index is not None else "???"
        return f"{a.name:<16} -> {target}"
    if a.body:
        return f"{a.name:<16} " + a.body.hex(" ")
    return a.name


def _strip_comment(line):
    """Drop a trailing `; comment`, but not a `;` sitting inside a string."""
    in_str, esc = False, False
    for i, ch in enumerate(line):
        if esc:
            esc = False
        elif ch == "\\" and in_str:
            esc = True
        elif ch == '"':
            in_str = not in_str
        elif ch == ";" and not in_str:
            return line[:i]
    return line


def _split_args(text):
    """Comma-split one line's operand text, respecting quotes and parens so
    a comma inside a string literal or a new_const("...") call is not
    mistaken for a separator between values."""
    parts, depth, in_str, esc, cur = [], 0, False, False, []
    for ch in text:
        if esc:
            cur.append(ch)
            esc = False
            continue
        if ch == "\\" and in_str:
            cur.append(ch)
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            cur.append(ch)
            continue
        if not in_str:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append("".join(cur).strip())
                cur = []
                continue
        cur.append(ch)
    tail = "".join(cur).strip()
    if tail or parts:
        parts.append(tail)
    return parts


_LABEL_RE = re.compile(r'^L(\d+):\s*(.*)$')
_CONST_RE = re.compile(r'^const\[(\d+)\]="((?:[^"\\]|\\.)*)"$')
_NEWCONST_RE = re.compile(r'^new_const\("((?:[^"\\]|\\.)*)"\)$')
_STR_RE = re.compile(r'^"((?:[^"\\]|\\.)*)"$')
_TYPED_RE = re.compile(r'^(int|float|double|register|boolean)\(([^)]*)\)$')
_BRANCH_RE = re.compile(r'^->\s*L(\d+)$')
_HEX_RE = re.compile(r'^([0-9A-Fa-f]{2}(?:\s+[0-9A-Fa-f]{2})*)?$')


def parse_script(text, pool, orig_pool_body):
    """Inverse of format_script(). Returns (actions, new_pool_strings).

    `pool` is this script's current constant pool (pre-edit), used to
    resolve const[i] references and to detect an already-pooled string
    passed to new_const(...). `orig_pool_body` is the raw body bytes of
    this script's own ConstantPool action (None if it has none) -- a
    ConstantPool line is always reproduced from this verbatim, never
    reconstructed from its text, since this editor doesn't expose pool
    bytes for hand-editing (that's the Strings tab's job).

    Raises ScriptParseError with every problem found, not just the first.
    On success, the second return value is the new strings introduced via
    new_const(...), in the order they must be appended to the pool --
    append-only, so no const[i] reference already in this script shifts.
    """
    errors = []
    real, end_entries, seen_labels = [], [], {}

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw).strip()
        if not line:
            continue
        m = _LABEL_RE.match(line)
        if not m:
            errors.append(f'line {lineno}: expected "L<number>: ...", got {raw.strip()!r}')
            continue
        label, rest = int(m.group(1)), m.group(2).strip()
        if label in seen_labels:
            errors.append(f"line {lineno}: label L{label:03d} is already used "
                          f"on line {seen_labels[label]}")
        else:
            seen_labels[label] = lineno
        if rest:
            parts = rest.split(None, 1)
            real.append((label, parts[0], parts[1] if len(parts) > 1 else "", lineno))
        else:
            end_entries.append((label, lineno))

    if not end_entries:
        errors.append('no end-of-list marker line (a bare "L<number>:") found -- '
                      "every script needs exactly one, normally right after the last action")
    elif len(end_entries) > 1:
        at = ", ".join(str(ln) for _l, ln in end_entries)
        errors.append(f"more than one end-of-list marker line found (lines {at}); "
                      f"there must be exactly one")

    if errors:
        raise ScriptParseError(errors)

    end_label = end_entries[0][0]
    label_to_index = {label: i for i, (label, _n, _a, _ln) in enumerate(real)}
    label_to_index[end_label] = len(real)

    new_pool, new_pool_index = [], {}

    def resolve_new_const(literal):
        if literal in pool:
            return pool.index(literal)
        if literal in new_pool_index:
            return new_pool_index[literal]
        idx = len(pool) + len(new_pool)
        new_pool.append(literal)
        new_pool_index[literal] = idx
        return idx

    actions = []
    pool_line_seen = False
    for label, name, argtext, lineno in real:
        code = _NAME_TO_CODE.get(name)
        if code is None:
            um = _UNKNOWN_NAME_RE.match(name)
            code = int(um.group(1), 16) if um else None
        if code is None:
            errors.append(f'line {lineno}: unknown action "{name}"')
            continue

        a = Action(0, code, name, b"")

        if code == 0x88:
            pool_line_seen = True
            if argtext:
                errors.append(f"line {lineno}: ConstantPool's contents can't be "
                              f"hand-edited here -- use the Strings tab, or leave "
                              f"this line's text alone")
            if orig_pool_body is not None:
                a.body = orig_pool_body
            else:
                errors.append(f"line {lineno}: this script has no existing "
                              f"ConstantPool action to preserve -- adding a "
                              f"brand-new one isn't supported here")
            actions.append(a)
            continue

        if code in BRANCH_OPS:
            bm = _BRANCH_RE.match(argtext)
            if not bm:
                errors.append(f'line {lineno}: {name} needs a target, like '
                              f'"-> L003" (got {argtext!r})')
            else:
                target_label = int(bm.group(1))
                if target_label not in label_to_index:
                    errors.append(f"line {lineno}: branch target L{target_label:03d} "
                                  f"is not defined anywhere in this script")
                else:
                    a.target_index = label_to_index[target_label]
            actions.append(a)
            continue

        if code == 0x96:
            values = []
            for tok in _split_args(argtext):
                if not tok:
                    continue
                cm, nm = _CONST_RE.match(tok), _NEWCONST_RE.match(tok)
                sm, tm = _STR_RE.match(tok), _TYPED_RE.match(tok)
                if cm:
                    idx, shown = int(cm.group(1)), _unescape_str(cm.group(2))
                    if not 0 <= idx < len(pool):
                        errors.append(f"line {lineno}: const[{idx}] is out of range "
                                      f"(this script's pool has {len(pool)} entries)")
                    elif pool[idx] != shown:
                        errors.append(f"line {lineno}: const[{idx}] is shown as "
                                      f"{shown!r} but the pool actually holds "
                                      f"{pool[idx]!r} -- that text is a label, not "
                                      f'something editable here; use new_const("...") '
                                      f"to push a different string, or rename the "
                                      f"pool entry on the Strings tab")
                    else:
                        values.append(("const16" if idx >= 256 else "const8", idx))
                elif nm:
                    idx = resolve_new_const(_unescape_str(nm.group(1)))
                    values.append(("const16" if idx >= 256 else "const8", idx))
                elif sm:
                    values.append(("string", _unescape_str(sm.group(1))))
                elif tok == "null":
                    values.append(("null", None))
                elif tok == "undefined":
                    values.append(("undefined", None))
                elif tm:
                    kind, raw_v = tm.group(1), tm.group(2)
                    try:
                        if kind == "int":
                            values.append(("int", int(raw_v)))
                        elif kind in ("float", "double"):
                            values.append((kind, float(raw_v)))
                        elif kind in ("register", "boolean"):
                            v = int(raw_v)
                            if not 0 <= v <= 255:
                                raise ValueError
                            values.append((kind, v))
                    except ValueError:
                        errors.append(f"line {lineno}: bad {kind}(...) value {raw_v!r}")
                else:
                    errors.append(f"line {lineno}: can't parse push value {tok!r}")
            a.values = values
            actions.append(a)
            continue

        if code < 0x80:
            if argtext:
                errors.append(f"line {lineno}: {name} takes no operand (got {argtext!r})")
            actions.append(a)
            continue

        hm = _HEX_RE.match(argtext)
        if not hm:
            errors.append(f"line {lineno}: {name}'s body should be hex byte pairs "
                          f'like "01 02 03" (got {argtext!r})')
        else:
            a.body = bytes.fromhex(argtext) if argtext else b""
        actions.append(a)

    pool_count = sum(1 for a in actions if a.code == 0x88)
    if pool_count > 1:
        errors.append(f"{pool_count} ConstantPool actions found -- there should be at most one")

    if not pool_line_seen:
        uses_const = any(v[0] in ("const8", "const16")
                          for a in actions if a.values is not None
                          for v in a.values)
        if uses_const:
            errors.append("this script's ConstantPool action was removed, but a "
                          "Push still references const[...] -- the game would read "
                          "garbage or crash; keep the ConstantPool line if any Push needs it")

    if not actions or actions[-1].code != 0:
        errors.append('the last real action (just before the end-of-list marker) '
                      'must be "End" -- every action list needs exactly one, as its final action')
    end_count = sum(1 for a in actions if a.code == 0)
    if end_count > 1:
        errors.append(f'{end_count} "End" actions found -- there must be exactly '
                      f"one, as the last action")

    if errors:
        raise ScriptParseError(errors)

    return actions, new_pool


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    p = Pck(argv[0])
    print(f"{p.path}")
    print(f"  pckoffset 0x{p.pckoffset:08X}  version {p.version}  "
          f"payload {p.size}  valid={p.valid()}")

    if "--actions" in argv or "--grep" in argv or "--dis" in argv:
        pass
    else:
        from collections import Counter
        c = Counter(k for _i, _o, k in p.objects())
        print(f"  objects: {sum(c.values())}  {dict(c)}")
        lists = list(p.action_lists())
        print(f"  action lists: {len(lists)}")
        return 0

    if "--dis" in argv:
        off = int(argv[argv.index("--dis") + 1], 0)
        acts = p.disasm(off)
        print(p.format(acts, p.constant_pool(acts)))
        return 0

    if "--grep" in argv:
        needle = argv[argv.index("--grep") + 1]
        for cmd, kind, code in p.action_lists():
            acts = p.disasm(code)
            pool = p.constant_pool(acts)
            text = p.format(acts, pool)
            if needle in text:
                print(f"\n=== {kind} cmd@0x{cmd:X} actions@0x{code:X} "
                      f"({len(acts)} actions) ===")
                print(text)
        return 0

    for cmd, kind, code in p.action_lists():
        acts = p.disasm(code)
        n = sum(a.size for a in acts)
        print(f"  {kind:<22} cmd@0x{cmd:06X}  actions@0x{code:06X}  "
              f"{len(acts):4d} actions  {n} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
