"""Inspect and compare MC3 PS2 city shader slots.

The city shading table is not homogeneous.  A material index can point at a
basic shader with an inline 0xA0 texture-info object, an instance/reference, or
one of the owner-placed resource classes.  Therefore copying 0xAC bytes from
every slot is unsafe: for some classes that range already belongs to following
objects.

Usage:

    python mc3_shader_diff.py CITY.pck --a 63 --b 480
    python mc3_shader_diff.py CITY.pck --a 63 --group-a 0 --b 480 --group-b 0

The report auto-detects the city's resource-token profile, normalizes pointers,
follows texture nodes, and decodes the TEX0 templates stored in texture-info
objects.  Tokyo uses the same logical classes as Atlanta with every token
shifted by +0x488 and commonly stores the texture-info object out of line.
"""

from __future__ import annotations

import argparse
import difflib
import os
import struct
from dataclasses import dataclass


VT_BASIC = 0x007A1EF8
VT_NAMED = 0x007A1F58
VT_INSTANCE = 0x007A1FC8
VT_TEXTURE_NODE = 0x007A20E0
VT_TEXTURE_INFO = 0x007A2320
VT_OWNER_16 = 0x007B2318
VT_OWNER_17 = 0x007B22B8

BASE_VT_NAMES = {
    VT_BASIC: "rmcShaderBasic/file-token",
    VT_NAMED: "named shader/file-token",
    VT_INSTANCE: "rmcShaderInstance/file-token",
    VT_TEXTURE_NODE: "texture node/file-token",
    VT_TEXTURE_INFO: "texture info/file-token",
    VT_OWNER_16: "owner-placed type 16",
    VT_OWNER_17: "owner-placed type 17",
}

# Retail Tokyo moves the whole city resource-token table by this amount.  Keep
# the constants above as logical/Atlanta-family IDs and translate at the PCK
# boundary; consumers can then compare classes rather than literal addresses.
TOKEN_PROFILE_SHIFTS = (0, 0x488)

PSM_NAMES = {
    0x00: "PSMCT32",
    0x01: "PSMCT24",
    0x02: "PSMCT16",
    0x0A: "PSMCT16S",
    0x13: "PSMT8",
    0x14: "PSMT4",
    0x1B: "PSMT8H",
    0x24: "PSMT4HL",
    0x2C: "PSMT4HH",
}


class Pck:
    def __init__(self, path: str):
        self.path = path
        with open(path, "rb") as fh:
            self.data = fh.read()
        if len(self.data) < 0x90:
            raise ValueError("file too small for a city PCK")
        self.va = self.u32(0)
        self.base = self.va - 0x80
        self.token_shift = detect_token_shift(self)

    def u8(self, off: int) -> int:
        return self.data[off] if 0 <= off < len(self.data) else 0

    def u16(self, off: int) -> int:
        return struct.unpack_from("<H", self.data, off)[0]

    def u32(self, off: int) -> int:
        return struct.unpack_from("<I", self.data, off)[0]

    def inside(self, off: int, size: int = 4) -> bool:
        return 0x80 <= off and off + size <= len(self.data)

    def file(self, va: int) -> int:
        return va - self.base

    def virtual(self, off: int) -> int:
        return off + self.base

    def is_pointer(self, value: int, size: int = 4) -> bool:
        return bool(value) and self.inside(self.file(value), size)

    def token(self, logical: int) -> int:
        return logical + self.token_shift

    def token_name(self, value: int) -> str | None:
        return BASE_VT_NAMES.get(value - self.token_shift)

    def token_label(self, value: int) -> str:
        name = self.token_name(value)
        return name if name is not None else "vt=%08X" % value


@dataclass(frozen=True)
class Slot:
    group: int
    material: int
    va: int
    off: int
    vtable: int
    tag: int


def descriptors(pck: Pck):
    count = pck.u32(0x80 + 0x08)
    ptr = pck.u32(0x80 + 0x0C)
    off = pck.file(ptr)
    if not (0 < count <= 128 and pck.inside(off, count * 16)):
        raise ValueError("invalid shader descriptors")
    out = []
    for group in range(count):
        desc = off + 16 * group
        array = pck.file(pck.u32(desc + 4))
        slots = pck.u16(desc + 8)
        if not pck.inside(array, slots * 4):
            raise ValueError("group %d array points outside the file" % group)
        out.append((desc, array, slots))
    return out


def slot_at(pck: Pck, group: int, material: int) -> Slot | None:
    ds = descriptors(pck)
    if not 0 <= group < len(ds):
        raise ValueError("group %d outside 0..%d" % (group, len(ds) - 1))
    _desc, array, count = ds[group]
    if not 0 <= material < count:
        raise ValueError("material %d outside 0..%d" % (material, count - 1))
    va = pck.u32(array + 4 * material)
    if not va:
        return None
    off = pck.file(va)
    if not pck.inside(off, 12):
        raise ValueError("slot g%d:m%d points outside the file: %08X" %
                         (group, material, va))
    return Slot(group, material, va, off, pck.u32(off), pck.u32(off + 4) & 0x7F)


def all_slots(pck: Pck):
    out = []
    for group, (_desc, array, count) in enumerate(descriptors(pck)):
        for material in range(count):
            va = pck.u32(array + 4 * material)
            if not va:
                continue
            off = pck.file(va)
            if pck.inside(off, 12):
                out.append(Slot(group, material, va, off,
                                pck.u32(off), pck.u32(off + 4) & 0x7F))
    return out


def detect_token_shift(pck: Pck) -> int:
    """Choose the known token table that best explains shader-slot vtables."""
    # Only class tokens expected at the start of a shader object are scored;
    # texture node/info tokens are pointed resources, not slot vtables.
    class_tokens = (VT_BASIC, VT_NAMED, VT_INSTANCE, VT_OWNER_16, VT_OWNER_17)
    vtables = [slot.vtable for slot in all_slots(pck)]
    scores = {
        shift: sum(value in {token + shift for token in class_tokens}
                   for value in vtables)
        for shift in TOKEN_PROFILE_SHIFTS
    }
    shift = max(scores, key=scores.get)
    if scores[shift] == 0:
        return 0
    return shift


def class_block_size(pck: Pck, slot: Slot) -> int:
    """Bytes owned inline by the slot, excluding separately pointed resources."""
    sub = pck.u32(slot.off + 8)
    delta = pck.file(sub) - slot.off if pck.is_pointer(sub) else None
    if slot.vtable == pck.token(VT_BASIC):
        # Common retail form: 0x10-byte header + inline 0xA0 texture-info.
        # Tokyo's out-of-line form is a packed 12-byte {vtable, tag, pointer}
        # header; treating it as 0x10 would consume the next shader's vtable.
        return 0xB0 if delta == 0x10 else 0x0C
    if slot.vtable == pck.token(VT_INSTANCE):
        return 0x20 if delta == 0x10 else 0x30 if delta == 0x20 else 0x10
    if slot.vtable == pck.token(VT_OWNER_17):
        # Header plus two inline 0x10-byte texture nodes.
        return 0x40
    if slot.vtable == pck.token(VT_OWNER_16):
        return 0x40
    if slot.vtable == pck.token(VT_NAMED):
        return 0x30
    return 0x10


def decode_tex0(lo: int, hi: int):
    value = lo | (hi << 32)
    fields = (
        ("TBP0", 14), ("TBW", 6), ("PSM", 6), ("TW", 4), ("TH", 4),
        ("TCC", 1), ("TFX", 2), ("CBP", 14), ("CPSM", 4), ("CSM", 1),
        ("CSA", 5), ("CLD", 3),
    )
    shift = 0
    out = {}
    for name, width in fields:
        out[name] = (value >> shift) & ((1 << width) - 1)
        shift += width
    out["PSM_NAME"] = PSM_NAMES.get(out["PSM"], "PSM_%d" % out["PSM"])
    out["NOMINAL"] = (1 << out["TW"], 1 << out["TH"])
    return out


def texture_info(pck: Pck, info_off: int):
    if (not pck.inside(info_off, 0xA0) or
            pck.u32(info_off) != pck.token(VT_TEXTURE_INFO)):
        return None
    records = []
    # The first u16 is an owner/dictionary selector.  Each 16-byte record then
    # carries an index, two transfer dimensions, and a TEX0 template.  A valid
    # record ends with 0x20000005; zero/padding ends the chain.
    selector = pck.u16(info_off + 6)
    pos = info_off + 8
    while pos + 16 <= info_off + 0x48:
        index = pck.u32(pos)
        width = pck.u16(pos + 4)
        height = pck.u16(pos + 6)
        lo = pck.u32(pos + 8)
        hi = pck.u32(pos + 12)
        if not index or hi != 0x20000005:
            break
        records.append({
            "index": index,
            "transfer": (width, height),
            "tex0_raw": lo | (hi << 32),
            "tex0": decode_tex0(lo, hi),
        })
        pos += 16
    desc_va = pck.u32(info_off + 0x48)
    return {
        "off": info_off,
        "va": pck.virtual(info_off),
        "subtype": pck.u8(info_off + 4),
        "selector": selector,
        "records": records,
        "embedded_descriptor": desc_va if pck.is_pointer(desc_va) else 0,
    }


def linked_infos(pck: Pck, slot: Slot):
    size = class_block_size(pck, slot)
    found = []
    seen = set()
    for rel in range(0, size - 3, 4):
        value = pck.u32(slot.off + rel)
        if not pck.is_pointer(value, 4):
            continue
        target = pck.file(value)
        vt = pck.u32(target)
        if vt == pck.token(VT_TEXTURE_INFO):
            info = texture_info(pck, target)
            if info and target not in seen:
                found.append((rel, info))
                seen.add(target)
        elif vt == pck.token(VT_TEXTURE_NODE) and pck.inside(target, 0x10):
            info_va = pck.u32(target + 0x0C)
            if pck.is_pointer(info_va, 0xA0):
                info_off = pck.file(info_va)
                info = texture_info(pck, info_off)
                if info and info_off not in seen:
                    found.append((rel, info))
                    seen.add(info_off)
    return found


def normalized_region(pck: Pck, off: int, size: int):
    out = []
    for rel in range(0, size, 4):
        value = pck.u32(off + rel)
        if pck.is_pointer(value):
            target = pck.file(value)
            if off <= target < off + size:
                text = "PTR(self+0x%X)" % (target - off)
            else:
                vt = pck.u32(target)
                text = "PTR(%s)" % pck.token_label(vt)
        elif pck.token_name(value) is not None:
            text = pck.token_name(value)
        else:
            text = "%08X" % value
        out.append("+%02X  %s" % (rel, text))
    return out


def normalized_words(pck: Pck, slot: Slot):
    return normalized_region(pck, slot.off, class_block_size(pck, slot))


def print_info(prefix: str, info):
    print("%sinfo @%08X subtype=%d selector=%d records=%d embedded_desc=%s" %
          (prefix, info["va"], info["subtype"], info["selector"],
           len(info["records"]),
           ("%08X" % info["embedded_descriptor"])
           if info["embedded_descriptor"] else "none"))
    for num, rec in enumerate(info["records"]):
        tex = rec["tex0"]
        print("%s  rec%d index=%d transfer=%dx%d TEX0=%016X "
              "%s nominal=%dx%d TBW=%d TCC=%d TFX=%d" %
              (prefix, num, rec["index"], rec["transfer"][0],
               rec["transfer"][1], rec["tex0_raw"], tex["PSM_NAME"],
               tex["NOMINAL"][0], tex["NOMINAL"][1], tex["TBW"],
               tex["TCC"], tex["TFX"]))


def print_slot(pck: Pck, label: str, slot: Slot):
    sub = pck.u32(slot.off + 8)
    delta = pck.file(sub) - slot.off if pck.is_pointer(sub) else None
    print("%s: group=%d material=%d VA=%08X class=%s tag=%d inline=%#x sub=%s" %
          (label, slot.group, slot.material, slot.va,
           pck.token_name(slot.vtable) or "vtable %08X" % slot.vtable, slot.tag,
           class_block_size(pck, slot),
           ("self+0x%X" % delta) if delta is not None else "%08X" % sub))
    infos = linked_infos(pck, slot)
    if not infos:
        print("  no linked %08X texture-info found" % pck.token(VT_TEXTURE_INFO))
    for rel, info in infos:
        print_info("  via +0x%02X: " % rel, info)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pck")
    ap.add_argument("--a", type=int, required=True, help="first material index")
    ap.add_argument("--b", type=int, required=True, help="second material index")
    ap.add_argument("--group-a", type=int, default=0)
    ap.add_argument("--group-b", type=int, default=0)
    args = ap.parse_args()

    pck = Pck(args.pck)
    a = slot_at(pck, args.group_a, args.a)
    b = slot_at(pck, args.group_b, args.b)
    if a is None or b is None:
        missing = []
        if a is None:
            missing.append("A g%d:m%d" % (args.group_a, args.a))
        if b is None:
            missing.append("B g%d:m%d" % (args.group_b, args.b))
        raise SystemExit("empty slot: " + ", ".join(missing))

    print(os.path.basename(args.pck))
    profile = ("Atlanta-family" if pck.token_shift == 0 else
               "Tokyo (+0x%X)" % pck.token_shift)
    print("token-profile=%s" % profile)
    print("groups=%d, slots/group=%d, filled=%d" %
          (len(descriptors(pck)), descriptors(pck)[0][2], len(all_slots(pck))))
    print()
    print_slot(pck, "A", a)
    print_slot(pck, "B", b)

    print("\nnormalized inline diff (pointers compare by target class):")
    left = normalized_words(pck, a)
    right = normalized_words(pck, b)
    for line in difflib.unified_diff(left, right,
                                     fromfile="A g%d:m%d" % (a.group, a.material),
                                     tofile="B g%d:m%d" % (b.group, b.material),
                                     lineterm=""):
        print(line)

    # Atlanta usually places the 0xA0 info object inside the basic shader; Tokyo
    # commonly serializes it elsewhere and leaves only a 12-byte header.  Diff
    # external infos explicitly so pointer normalization does not hide content.
    a_infos = linked_infos(pck, a)
    b_infos = linked_infos(pck, b)
    for index, ((a_rel, a_info), (b_rel, b_info)) in enumerate(zip(a_infos, b_infos)):
        a_external = not (a.off <= a_info["off"] < a.off + class_block_size(pck, a))
        b_external = not (b.off <= b_info["off"] < b.off + class_block_size(pck, b))
        if not (a_external or b_external):
            continue
        print("\nnormalized external texture-info diff #%d:" % index)
        info_left = normalized_region(pck, a_info["off"], 0xA0)
        info_right = normalized_region(pck, b_info["off"], 0xA0)
        for line in difflib.unified_diff(
                info_left, info_right,
                fromfile="A via +0x%02X @%08X" % (a_rel, a_info["va"]),
                tofile="B via +0x%02X @%08X" % (b_rel, b_info["va"]),
                lineterm=""):
            print(line)


if __name__ == "__main__":
    main()
