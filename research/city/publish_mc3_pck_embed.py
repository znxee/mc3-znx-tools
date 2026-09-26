"""
mc3_pck_embed.py - embeds external meshes inside the main vp_*.pck.

Per LOD, the container has three parallel tables (n items each), reached through
pointers in the table header (@+0x08, @+0x0C, @+0x10 -- NOT at a fixed offset):
    mesh table   -> direct pointer to the mesh data
    name table   -> pointer to the "<...>.mesh" name
    ID table     -> u32
When the mesh table pointer is ZERO and the name pointer is populated, the game
loads "<name>.mesh.pck" from the active vehicle archive/folder.  The ID is the
skeleton-node index used after loading; ID 0 is valid and means ``vroot``.
Embedding = writing the pointer into that slot.

FINDING POINTERS: an aligned u32 whose value falls in [virtual_address,
+body_size]. But vertex/normal data forms such values all the time, so the scan
skips packet data regions and targets not aligned to 4 (see data_regions).

WHY APPEND AT THE END (instead of inserting in the middle):
the file uses 0xCD as padding, but sometimes reuses that space to store
strings/pieces of other structures. Inserting bytes in the middle would shift
everything and require fixing every pointer -- and anything "hidden" in the
padding would break. Appending at the end moves NOTHING: no existing pointer
changes, and only the chosen slot starts pointing at the new block. The external
mesh body is rebased to the virtual address where it landed (16-byte aligned,
because MC3 never points at +8/+12).

REMOVING (shrinking the file) does the reverse: it erases the mesh footprint
(header + tables + blocks, always in multiples of 16 so alignment is preserved),
RELOCATES every pointer through a shift map and zeroes the slot, so the game goes
back to loading the external ".mesh.pck". Before erasing, it checks whether any
pointer from OUTSIDE points inside the footprint -- if so, the mesh is SKIPPED
(this is the case of other structures' pieces stored in the padding).
Careful: every named external member must exist. ``rmcLodPaged::Touch`` returns
false when even one requested model fails, which can keep the whole LOD from
becoming resident.  This used to be mistaken for an ID-0 loading gate.

Usage:
    python mc3_pck_embed.py container.pck --list
    python mc3_pck_embed.py container.pck --bone-tags
    python mc3_pck_embed.py container.pck part.mesh.pck --name vroot_..._geo.mesh
    python mc3_pck_embed.py container.pck part.mesh.pck --index 7 --lod HLOD
    python mc3_pck_embed.py container.pck --auto-dir            (embeds every
        zeroed slot whose "<name>.pck" exists in the container folder)
    python mc3_pck_embed.py container.pck --remove --index 0    (removes 1)
    python mc3_pck_embed.py container.pck --remove --name NAME
    python mc3_pck_embed.py container.pck --remove-all --dry-run
Options: --lod HLOD|MLOD|LLOD|all (default all)  --out output.pck  --dry-run
"""
import bisect
import importlib.util
import os
import re
import struct
import sys

# reverse pointer lookup helper (same directory)
_here = os.path.dirname(os.path.abspath(__file__))
_pspec = importlib.util.spec_from_file_location(
    "mc3_pointers", os.path.join(_here, "mc3_pointers.py"))
mc3_pointers = importlib.util.module_from_spec(_pspec)
_pspec.loader.exec_module(mc3_pointers)
PointerGraph = mc3_pointers.PointerGraph

ROOT_PTR_VA = 0x06800128          # pointer to the root (tag 20947A00)
TAG_ROOT = bytes.fromhex("20947a00")
TAG_TABLE = bytes.fromhex("f00e7a00")
TAG_SKEL = bytes.fromhex("ee020100")
# High half of a mesh tag: 7A00 on PS2 vehicles and on most traffic containers,
# BC00 on tokyo_traffic. Same layout either way.
MESH_TAG_HI = (b"\x7a\x00", b"\xbc\x00")
MESH_RUNTIME_VTABLES = (0x00626D98,)
LOD_NAMES = ("HLOD", "MLOD", "LLOD")
MAGIC_MC3_PSP = bytes.fromhex("cc991609")
MAGIC_MCLA_PSP = bytes.fromhex("38545a00")


class Pck:
    def __init__(self, path):
        self.path = path
        self.data = bytearray(open(path, "rb").read())
        self.va = struct.unpack_from("<I", self.data, 0)[0]
        self.base = self.va - 0x80          # file = va - base
        self.body = struct.unpack_from("<I", self.data, 0x0C)[0]
        self._data_regions = None
        self._lods = None
        self._skel = None
        self._bone_tags = None

    # --- helpers -----------------------------------------------------------
    def F(self, va):
        return va - self.base

    def V(self, off):
        return off + self.base

    def U(self, off):
        return struct.unpack_from("<I", self.data, off)[0]

    def U16(self, off):
        return struct.unpack_from("<H", self.data, off)[0]

    def inside(self, off):
        return 0 <= off <= len(self.data) - 4

    def cstr(self, off):
        e = off
        while e < len(self.data) and self.data[e] not in (0, 0xCD):
            e += 1
        return self.data[off:e].decode("latin1", "replace")

    # --- structure ---------------------------------------------------------
    def lods(self):
        """{'HLOD': table_va, ...}. Combines two methods:

        1) the PS2 root path (ptr at VA 0x06800128 -> tag 20947A00 ->
           HLOD/MLOD/LLOD at +0x10/+0x14/+0x18);
        2) a structural scan, which finds the tables in any format -- this is
           what makes psppck (MC3 and MC:LA) work, since there the path is
           different (level1 -> level2 +0x44/+0x48) and the table tag differs
           too. It also picks up tables the root path does not reach (e.g. PS2
           LLOD, whose tables sit far from the header).
        """
        if self._lods is not None:
            return self._lods
        out = {}
        try:
            root = self.U(self.F(ROOT_PTR_VA))
            r = self.F(root)
            if self.inside(r) and self.data[r:r+4] == TAG_ROOT:
                for i, nm in enumerate(LOD_NAMES):
                    va = self.U(r + 16 + 4*i)
                    if va and self.inside(self.F(va)):
                        out[nm] = va
        except Exception:
            pass
        for va in self.scan_tables():
            if va not in out.values():
                out["LOD%d" % len(out)] = va
        self._lods = out
        return out

    def table_count_off(self, lod_va):
        """Offset of the u16 holding the item count: +0x02 (PS2 and MC3 PSP)
        or +0x06 (MC:LA PSP)."""
        h = self.F(lod_va)
        pm, pn = self.F(self.U(h + 8)), self.F(self.U(h + 0xC))
        for off in (2, 6):
            n = self.U16(h + off)
            if 1 <= n <= 4096 and 4*n <= pn - pm < 4*n + 16:
                return off
        return 2

    def scan_tables(self):
        """Find LOD tables structurally: consecutive mesh/name/ID pointers
        (with alignment padding) and the count matching at +0x02 or +0x06."""
        found = []
        N = len(self.data)
        for o in range(0x80, N - 0x14, 4):
            pm, pn, pi = self.U(o+8), self.U(o+0xC), self.U(o+0x10)
            if not (pm and pn and pi):
                continue
            fm, fn, fi = self.F(pm), self.F(pn), self.F(pi)
            if not (0 <= fm < N and 0 <= fn < N and 0 <= fi < N and fm < fn < fi):
                continue
            for off in (2, 6):
                n = self.U16(o + off)
                if 1 <= n <= 4096 and 4*n <= fn-fm < 4*n+16 and 4*n <= fi-fn < 4*n+16:
                    found.append(o + self.base)
                    break
        return found

    def slots(self, lod_va):
        """[(index, mesh_ptr, name, id, slot_off)] of one LOD.

        Table header: u16 unk | u16 n | tag F00E7A00 | mesh table ptr | name
        table ptr | ID table ptr. The tables are pointed at explicitly (in HLOD
        they happen to sit at +0x20, but in LLOD they are far away). IDs are u32.
        """
        h = self.F(lod_va)
        n = self.U16(h + self.table_count_off(lod_va))
        t_mesh = self.F(self.U(h + 0x08))
        t_names = self.F(self.U(h + 0x0C))
        t_ids = self.F(self.U(h + 0x10))
        out = []
        for i in range(n):
            if not self.inside(t_mesh + 4*i):
                break
            mp = self.U(t_mesh + 4*i)
            npr = self.U(t_names + 4*i) if self.inside(t_names + 4*i) else 0
            nm = self.cstr(self.F(npr)) if npr and self.inside(self.F(npr)) else ""
            idv = self.U(t_ids + 4*i) if self.inside(t_ids + 4*i) else 0
            out.append((i, mp, nm, idv, t_mesh + 4*i))
        return out

    # --- embed -------------------------------------------------------------
    def append_mesh_body(self, mesh_path):
        """Append the BODY of a .mesh.pck at the end, rebasing its pointers.
        Returns the virtual address where the body landed."""
        md = open(mesh_path, "rb").read()
        src_va = struct.unpack_from("<I", md, 0)[0]
        src_body_len = struct.unpack_from("<I", md, 0x0C)[0]
        body = bytearray(md[0x80:0x80 + src_body_len] if src_body_len else md[0x80:])

        # align the destination to 16 (MC3 never points at +4/+8/+12)
        while len(self.data) % 16:
            self.data += b"\xCD"
        dst_off = len(self.data)
        dst_va = self.V(dst_off)

        # rebase: every aligned u32 landing in the source mesh virtual range
        lo, hi = src_va, src_va + max(src_body_len, len(body))
        delta = dst_va - src_va
        n_fixed = 0
        for o in range(0, len(body) - 3, 4):
            v = struct.unpack_from("<I", body, o)[0]
            if lo <= v <= hi:
                struct.pack_into("<I", body, o, (v + delta) & 0xFFFFFFFF)
                n_fixed += 1

        self.data += body
        while len(self.data) % 16:
            self.data += b"\xCD"
        struct.pack_into("<I", self.data, 0x0C, len(self.data) - 0x80)
        return dst_va, n_fixed, len(body)

    def set_slot(self, slot_off, va):
        struct.pack_into("<I", self.data, slot_off, va)

    def id_offset(self, lod_va, index):
        """Offset of a slot ID (u32) inside the ID table."""
        return self.F(self.U(self.F(lod_va) + 0x10)) + 4*index

    def set_id(self, lod_va, index, value):
        struct.pack_into("<I", self.data, self.id_offset(lod_va, index), value & 0xFFFFFFFF)

    # Pointer slots in the car PCK header -> vehicle structures.
    # Every structure starts with the class VTABLE POINTER (a leftover of the
    # serialization: the object was saved with memcpy, so the ptr went along).
    # The game REWRITES that value on load -- you can zero it and it comes back,
    # and the address changes on every run. The real type comes from the slot
    # POSITION, not from the value. Structures of the same type show the same
    # pointer.
    VEH_SLOTS = (
        (0x98, "VehInput"), (0xA0, "VehDamage"), (0xA4, "VehModel"),
        (0xA8, "Audio root"), (0xB4, "VehSim Base"), (0xB8, "VehSim Mods"),
        (0xBC, "VehGyro Base"), (0xC0, "VehGyro Mods"), (0xC4, "VehZone"),
        (0xC8, "VehMods"),
    )

    def veh_structs(self):
        """[(name, va, type_id, string)] of the vehicle structures."""
        out = []
        for off, nm in self.VEH_SLOTS:
            try:
                p = self.U(off)
            except Exception:
                continue
            o = self.F(p)
            if not self.inside(o):
                continue
            tid = self.U(o)
            sp = self.U(o + 4)
            s = self.cstr(self.F(sp)) if self.inside(self.F(sp)) else ""
            if not s.isprintable():
                s = ""
            out.append((nm, p, tid, s))
        return out

    SKEL_ITEM = 0x44        # PS2 node size; PSP uses 0x60 (see SKEL_LAYOUT)

    # Both consoles store the same bone node, but PSP pads the anchor to 16
    # bytes, which pushes every field down and grows the node from 0x44 to 0x60.
    # Field offsets per node size:
    #                    name  next  child parent anchor2  id(u16)
    SKEL_LAYOUT = {
        0x44: dict(name=0x0C, next=0x14, child=0x18, parent=0x1C, anchor2=0x20, id=0x38),
        0x60: dict(name=0x10, next=0x18, child=0x1C, parent=0x20, anchor2=0x30, id=0x50),
    }

    def _skel_node_ok(self, off, stride):
        """A node is plausible when its name pointer lands on a printable name."""
        L = self.SKEL_LAYOUT[stride]
        if not self.inside(off + stride - 4):
            return False
        p = self.U(off + L["name"])
        if not (self.va <= p <= self.va + len(self.data)):
            return False
        s = self.cstr(self.F(p))
        return bool(s) and 2 < len(s) < 48 and all(
            c.isalnum() or c in "_-." for c in s)

    def _skel_score(self, n, lp, stride):
        """How much an array really looks like a bone list: the share of nodes
        with a readable name, plus a bonus when the first one is the usual root.

        Scoring instead of taking the first hit matters: on MC:LA a block of
        shader parameters ("tintA", "WinFresnelMin") passes a 2-node check and
        was being picked over the real skeleton.
        """
        step = max(1, n // 24)
        checked = good = 0
        for i in range(0, n, step):
            checked += 1
            if self._skel_node_ok(lp + i*stride, stride):
                good += 1
        if not checked:
            return 0.0
        score = good / float(checked)
        first = self.U(lp + self.SKEL_LAYOUT[stride]["name"])
        if first and self.inside(self.F(first)):
            if self.cstr(self.F(first)).lower() == "vroot":
                score += 1.0          # every vehicle skeleton starts at vroot
        return score

    def skel_table(self):
        """(count, list_file_offset, node_stride) of the .skel, or None.

        Header layout is the same everywhere -- u16 count | u16 unk | u32 tag |
        list ptr -- but it is reached differently and the tag is not constant:
        PS2 points at it from file 0x1AC with tag EE020100, MC3 PSP points from
        0x1C4 with the same tag, and MC:LA uses tag 00010000. So rather than
        trusting one offset or one tag, this validates the structure and keeps
        the best-scoring candidate.
        """
        if self._skel is not None:
            return self._skel or None
        best, best_score = None, 0.0
        seen = set()

        def consider(t):
            nonlocal best, best_score
            try:
                n = self.U16(t)
                if not (1 <= n <= 4096):
                    return
                lp = self.F(self.U(t + 8))
                if not self.inside(lp) or lp in seen:
                    return
                for stride in (0x60, 0x44):
                    if lp + n*stride > len(self.data):
                        continue
                    if not self._skel_node_ok(lp, stride):
                        continue
                    s = self._skel_score(n, lp, stride)
                    if s > best_score:
                        best, best_score = (n, lp, stride), s
                    seen.add(lp)
                    break
            except Exception:
                pass

        for root in (0x1AC, 0x1C4):      # known direct pointers, cheap and exact
            try:
                consider(self.F(self.U(root)))
            except Exception:
                pass
        if best_score < 1.5:
            # structural scan: needed on MC:LA and on any build whose root
            # pointer moved
            for o in range(0, len(self.data) - 16, 4):
                if 1 <= self.U16(o) <= 4096:
                    consider(o)
        self._skel = best or False
        return best

    def skel_nodes(self):
        """[(idx, name, id, parent, child, next, (ax,ay,az), file_off)] of the
        .skel.

        PS2 node (0x44): +0x00 anchor1 (3 floats), +0x0C name ptr, +0x14 next,
        +0x18 child, +0x1C parent, +0x20 anchor2, +0x38 u16 ID.
        PSP node (0x60): the anchor is padded to 16 bytes, so name is at +0x10,
        next +0x18, child +0x1C, parent +0x20, anchor2 +0x30, ID +0x50.
        The ID is the same value used in the mesh ID table -- that is how a part
        gets attached to a bone.
        """
        t = self.skel_table()
        if not t:
            return []
        n, lp, stride = t
        L = self.SKEL_LAYOUT[stride]

        def idx_of(va):
            if not va:
                return None
            off = self.F(va) - lp
            if 0 <= off < n*stride and off % stride == 0:
                return off // stride
            return None

        out = []
        for i in range(n):
            o = lp + i*stride
            npr = self.U(o + L["name"])
            nm = self.cstr(self.F(npr)) if npr and self.inside(self.F(npr)) else ""
            out.append((i, nm, self.U16(o + L["id"]), idx_of(self.U(o + L["parent"])),
                        idx_of(self.U(o + L["child"])), idx_of(self.U(o + L["next"])),
                        struct.unpack_from("<fff", self.data, o), o))
        return out

    def bone_tags(self):
        """Return the PS2 drwBonyData tag map.

        Each result is ``(name, skeleton_index, stored_index_plus_one,
        bucket_index, entry_file_offset)``.  The data word deliberately stores
        index+1 because zero is HashTable's miss result.  ``following`` links
        hash collisions, not parent/child bones.

        This table belongs to the PS2 main ``vp_*.pck`` graph. PSP packages do
        not use the same owner path; their portable bone information remains
        available through :meth:`skel_nodes` and can be remapped to these tags
        by name during conversion.
        """
        if self._bone_tags is not None:
            return self._bone_tags
        self._bone_tags = []
        try:
            if len(self.data) < 0x90 or self.U(4) != 0x0038E030:
                return self._bone_tags
            model = self.F(self.U(0x88))
            if not self.inside(model + 0x3864):
                return self._bone_tags
            shader_model = self.F(self.U(model + 0xD8))
            if not self.inside(shader_model + 0x80):
                return self._bone_tags
            bony = self.F(self.U(shader_model + 0x80))
            if not self.inside(bony):
                return self._bone_tags
            table = self.F(self.U(bony))
            if not self.inside(table + 0x14):
                return self._bone_tags
            bucket_count = self.U(table + 0x08)
            entry_count = self.U(table + 0x0C)
            buckets = self.F(self.U(table + 0x10))
            if not (1 <= bucket_count <= 4096
                    and entry_count <= 65536
                    and self.inside(buckets + 4 * bucket_count - 4)):
                return self._bone_tags

            out = []
            seen = set()
            for bucket_index in range(bucket_count):
                entry_va = self.U(buckets + 4 * bucket_index)
                while entry_va:
                    entry = self.F(entry_va)
                    if entry in seen or not self.inside(entry + 8):
                        return []
                    seen.add(entry)
                    name_va = self.U(entry)
                    name_off = self.F(name_va)
                    if not self.inside(name_off):
                        return []
                    stored = self.U(entry + 4)
                    name = self.cstr(name_off)
                    out.append((name, stored - 1 if stored else None, stored,
                                bucket_index, entry))
                    entry_va = self.U(entry + 8)
            if len(out) != entry_count:
                return []
            self._bone_tags = out
        except (IndexError, struct.error):
            self._bone_tags = []
        return self._bone_tags

    def bone_tag_map(self):
        """``{tag_name: skeleton_index}`` for a PS2 main vehicle package."""
        return {name: index for name, index, _stored, _bucket, _off
                in self.bone_tags() if index is not None}

    def set_skel_id(self, node_index, value):
        t = self.skel_table()
        if not t:
            raise ValueError("this file has no .skel")
        n, lp, stride = t
        struct.pack_into("<H", self.data,
                         lp + node_index*stride + self.SKEL_LAYOUT[stride]["id"],
                         value & 0xFFFF)

    def set_skel_anchor(self, node_index, xyz, second=False):
        t = self.skel_table()
        if not t:
            raise ValueError("this file has no .skel")
        n, lp, stride = t
        L = self.SKEL_LAYOUT[stride]
        off = lp + node_index*stride + (L["anchor2"] if second else 0x00)
        struct.pack_into("<fff", self.data, off, *[float(v) for v in xyz])

    def skel_info(self, lod_va):
        """(items, skel_va) -- the .skel is referenced from the LOD table
        header itself: +0x14 u16 items, +0x16 u16 unk, +0x18 tag EE020100,
        +0x1C pointer. The mesh table IDs index into this list."""
        h = self.F(lod_va)
        if not self.inside(h + 0x1C) or self.data[h+0x18:h+0x1C] != TAG_SKEL:
            return None
        return (self.U16(h + 0x14), self.U(h + 0x1C))

    # --- remove (shrink) --------------------------------------------------
    def data_regions(self):
        """Ranges [start,end) that are packet DATA (vertex/UV/color/normal).

        No real pointer lives in there, but the geometry bytes form u32 that fall
        inside the VA window all the time (e.g. in vp_350z_04_g.pck offset
        0x80794 holds E8 16 87 06 = 0x068716E8, inside the range and even aligned
        to 4, yet it is a vertex coordinate). Without excluding these regions the
        scan produces false positives -- which blocks removals and, worse, would
        make relocation rewrite geometry.
        """
        if self._data_regions is not None:
            return self._data_regions
        regions = []
        for lod_va in self.lods().values():
            for (_i, mp, _nm, _id, _off) in self.slots(lod_va):
                if not mp:
                    continue
                o = self.F(mp)
                if self.is_psp_mesh(o):
                    # PSP mesh: the data is the 14 bytes/vertex stream, plus
                    # the material and sub-offset tables, which are u16 pairs --
                    # two consecutive u16 form a u32 that falls inside the VA
                    # window and would become a false "pointer".
                    md = self.F(self.U(o + 0x10))
                    nv = self.U16(o + 0x24)
                    if self.inside(md) and nv:
                        regions.append((md, min(len(self.data), md + nv*14)))
                    ml = self.F(self.U(o + 0x04))
                    mt = self.F(self.U(o + 0x08))
                    npsp = self.data[o + 0x1B]
                    if self.inside(mt) and npsp:
                        regions.append((mt, min(len(self.data), mt + 2*npsp)))
                    for g in range(npsp):
                        e = ml + g*0xC
                        if not self.inside(e):
                            break
                        sp = self.F(self.U(e))
                        cnt = self.U16(e + 4)
                        if self.inside(sp) and 1 <= cnt <= 4096:
                            regions.append((sp, min(len(self.data), sp + 4*cnt)))
                    continue
                if not self.inside(o) or self.data[o+2:o+4] not in MESH_TAG_HI:
                    continue
                num = self.U(o + 8)
                main = self.F(self.U(o + 0x10))
                if not (1 <= num <= 4096) or not self.inside(main):
                    continue
                for gi in range(num):
                    mpp = self.F(self.U(main + 8*gi))
                    cnt = self.U16(main + 8*gi + 4)
                    if not self.inside(mpp) or not (1 <= cnt <= 4096):
                        continue
                    for k in range(cnt):
                        e = mpp + 8*k
                        bp = self.F(self.U(e))
                        qw = self.U16(e + 4)
                        if self.inside(bp) and qw:
                            regions.append((bp, min(len(self.data), bp + qw*16)))
        regions.sort()
        merged = []
        for a, b in regions:
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        self._data_regions = [(a, b) for a, b in merged]
        return self._data_regions

    def in_data(self, off):
        regions = self.data_regions()
        i = bisect.bisect_right(regions, (off, float("inf"))) - 1
        return i >= 0 and regions[i][0] <= off < regions[i][1]

    def graph(self):
        """PointerGraph over the current state of the file. Not cached: the
        embed mutates self.data (append/remove), so a stale index would be
        dangerous."""
        return PointerGraph(self.data, self.base,
                            valid_lo=self.va, valid_hi=self.va + self.body,
                            align=4, skip=self.in_data)

    def all_pointers(self):
        """[(offset, va)] of every aligned u32 landing in the file virtual
        range (the VA-range technique), discarding:
          - offsets inside packet data regions (see data_regions)
          - targets not aligned to 4
        """
        return self.graph().all_pointers()

    def is_psp_mesh(self, off):
        return self.inside(off) and self.data[off:off+4] in (MAGIC_MC3_PSP, MAGIC_MCLA_PSP)

    def psp_mesh_footprint(self, mesh_va):
        """Footprint of an embedded PSP mesh. Layout (relative to the magic):
        +0x04 mesh_offset_list | +0x08 material | +0x0C damage | +0x10 mesh_data
        | +0x19 id | +0x1B num | +0x24 u16 total vertices (stride 14)."""
        o = self.F(mesh_va)
        if not self.is_psp_mesh(o):
            return None
        ml = self.F(self.U(o + 0x04))
        mt = self.F(self.U(o + 0x08))
        md = self.F(self.U(o + 0x10))
        num = self.data[o + 0x1B]
        nverts = self.U16(o + 0x24)
        if not (self.inside(ml) and self.inside(mt) and self.inside(md)):
            return None
        end = max(o + 0x30, ml + num*0xC, mt + num*2, md + nverts*14)
        # include the sub-offset table pointed at by each mesh list entry
        for g in range(num):
            e = ml + g*0xC
            if not self.inside(e):
                break
            p = self.F(self.U(e))
            cnt = self.U16(e + 4)
            if self.inside(p) and 1 <= cnt <= 4096:
                end = max(end, p + 4*cnt)
        end = min(len(self.data), end + (-end) % 16)
        return (o, end)

    def mesh_footprint(self, mesh_va):
        """File extent [start, end) taken by an embedded mesh: header + pointer
        tables + block regions. mat_ptr may point at the container shared material
        area, so it is NOT part of the footprint."""
        o = self.F(mesh_va)
        if self.is_psp_mesh(o):
            return self.psp_mesh_footprint(mesh_va)
        # tag high half: 7A00 on PS2 vehicles and most traffic containers,
        # BC00 on tokyo_traffic -- same layout either way
        if not self.inside(o):
            return None
        tag = self.U(o)
        if (self.data[o+2:o+4] not in MESH_TAG_HI
                and tag not in MESH_RUNTIME_VTABLES):
            return None
        end = o + 0x20
        num = self.U(o + 8)
        main = self.F(self.U(o + 0x10))
        if not (1 <= num <= 4096) or not self.inside(main):
            return None
        end = max(end, main + 8*num)
        for gi in range(num):
            mp = self.F(self.U(main + 8*gi))
            cnt = self.U16(main + 8*gi + 4)
            if not self.inside(mp) or not (1 <= cnt <= 4096):
                continue
            end = max(end, mp + 8*cnt)
            for k in range(cnt):
                e = mp + 8*k
                bp = self.F(self.U(e))
                qw = self.U16(e + 4)
                if self.inside(bp) and qw:
                    end = max(end, bp + qw*16)
        end = min(len(self.data), end + (-end) % 16)
        return (o, end)

    def external_refs(self, lo, hi, ignore_offsets=()):
        """Pointers from OUTSIDE [lo,hi) that point INSIDE it. If any exists
        (besides the slots we are going to zero), removing is not safe: the
        container padding sometimes stores strings/pieces of other structures."""
        g = self.graph()
        bad = []
        for t in range(lo, hi, g.align):
            for o in g.sources_of(t):
                if not (lo <= o < hi) and o not in ignore_offsets:
                    bad.append((o, t))
        return bad

    def relocate_blockers(self, lo, hi, ignore_offsets=()):
        """Clear what blocks removing [lo,hi): strings stored in the padding
        inside the footprint are COPIED to the end of the file and the pointers
        referencing them are repointed. After that the range is free. Returns
        (n_strings_moved, n_pointers_repointed) or None if some blocker is not a
        string (in which case removing stays unsafe)."""
        refs = self.external_refs(lo, hi, ignore_offsets)
        if not refs:
            return (0, 0)
        by_target = {}
        for (po, t) in refs:
            by_target.setdefault(t, []).append(po)
        # only relocate if ALL blockers are ascii strings
        for t in by_target:
            s = self.cstr(t)
            if not s or not all(32 <= ord(ch) < 127 for ch in s):
                return None
        moved = 0
        repointed = 0
        for t, ptr_offs in sorted(by_target.items()):
            s = self.cstr(t)
            blob = self.data[t:t + len(s)] + b"\x00"
            while len(self.data) % 4:
                self.data += b"\xCD"
            new_off = len(self.data)
            self.data += blob
            for po in ptr_offs:
                struct.pack_into("<I", self.data, po, self.V(new_off))
                repointed += 1
            moved += 1
        while len(self.data) % 16:
            self.data += b"\xCD"
        struct.pack_into("<I", self.data, 0x0C, len(self.data) - 0x80)
        self.body = len(self.data) - 0x80
        return (moved, repointed)

    def remove_ranges(self, ranges, zero_slots=()):
        """Remove [start,end) (multiples of 16, to preserve alignment),
        relocate every pointer and zero the given slots."""
        ranges = sorted(ranges)
        keep = []
        prev = 0
        for (a, b) in ranges:
            keep.append((prev, a))
            prev = b
        keep.append((prev, len(self.data)))

        # map old_offset -> accumulated shift
        def shift_of(off):
            s = 0
            for (a, b) in ranges:
                if off >= b:
                    s += b - a
                elif off >= a:
                    return None          # falls inside what was removed
            return s

        new = bytearray()
        for (a, b) in keep:
            new += self.data[a:b]

        # rewrite the pointers in the new buffer
        n_fixed = 0
        for (o, v) in self.all_pointers():
            so = shift_of(o)
            if so is None:
                continue                 # the pointer itself was removed
            t = self.F(v)
            st = shift_of(t)
            if st is None:
                continue                 # target removed: the slot gets zeroed
            if st:
                struct.pack_into("<I", new, o - so, v - st)
                n_fixed += 1

        for slot in zero_slots:
            so = shift_of(slot)
            if so is not None:
                struct.pack_into("<I", new, slot - so, 0)

        removed = sum(b - a for (a, b) in ranges)
        self.data = new
        self.body -= removed
        struct.pack_into("<I", self.data, 0x0C, len(self.data) - 0x80)
        return removed, n_fixed

    def save(self, out_path):
        open(out_path, "wb").write(bytes(self.data))
        return len(self.data)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    files = [a for a in args if not a.startswith("-")]
    container = files[0]
    mesh = files[1] if len(files) > 1 else None
    want_lods = LOD_NAMES
    for a in args:
        if a.startswith("--lod"):
            v = a.split("=", 1)[1].upper() if "=" in a else "ALL"
            if v != "ALL":
                want_lods = (v,)
    name = None
    index = None
    for i, a in enumerate(args):
        if a.startswith("--name"):
            name = a.split("=", 1)[1] if "=" in a else (args[i+1] if i+1 < len(args) else None)
        if a.startswith("--index"):
            index = int(a.split("=", 1)[1], 0) if "=" in a else int(args[i+1], 0)
    out = None
    for a in args:
        if a.startswith("--out"):
            out = a.split("=", 1)[1]
    dry = "--dry-run" in args
    do_list = "--list" in args
    do_bone_tags = "--bone-tags" in args
    auto = "--auto-dir" in args
    remove_all = "--remove-all" in args
    remove_sel = remove_all or ("--remove" in args) or any(a.startswith("--remove") for a in args)

    p = Pck(container)
    lods = p.lods()
    print("%s  VA=%#x  size=%d" % (os.path.basename(container), p.va, len(p.data)))
    if not lods:
        print("could not find the root/tables (is the file a main vp_*.pck?)")
        return
    print("LODs: " + ", ".join("%s@%#x" % (k, v) for k, v in lods.items()))
    # without an explicit --lod, operate on every table found (PSP containers
    # do not use the HLOD/MLOD/LLOD names)
    if want_lods == LOD_NAMES:
        want_lods = tuple(lods.keys())

    if do_bone_tags:
        tags = p.bone_tags()
        if not tags:
            print("no PS2 drwBonyData bone-tag table (use skel_nodes for PSP)")
            return
        print("\nBone tags: %d" % len(tags))
        for tag, index, stored, bucket, off in sorted(tags):
            shown = "not found" if index is None else str(index)
            print("  %-38s index=%-4s stored=%-4d bucket=%-3d file=%#x" %
                  (tag, shown, stored, bucket, off))
        return

    if do_list:
        for lod in want_lods:
            if lod not in lods:
                continue
            sl = p.slots(lods[lod])
            miss = [s for s in sl if s[1] == 0]
            print("\n%s: %d slots, %d with ZERO pointer (load external .pck)" % (lod, len(sl), len(miss)))
            for (i, mp, nm, idv, off) in sl:
                if mp == 0:
                    print("   [%3d] id=%-4d %s" % (i, idv, nm))
        return

    # ---- remove embedded meshes (shrink the file) ----
    if remove_sel:
        found = []          # (lod, i, off, name, mesh_va, id)
        for lod in want_lods:
            if lod not in lods:
                continue
            for (i, mp, nm, idv, off) in p.slots(lods[lod]):
                if mp == 0:
                    continue
                if (name is not None and nm == name) or (index is not None and i == index) or remove_all:
                    found.append((lod, i, off, nm, mp, idv))
        if not found:
            print("no matching embedded mesh to remove.")
            return
        # group by mesh_va: the same body may be used by several slots
        by_va = {}
        for t in found:
            by_va.setdefault(t[4], []).append(t)
        ranges, zeros, total = [], [], 0
        for mesh_va, group in sorted(by_va.items()):
            fp = p.mesh_footprint(mesh_va)
            if not fp:
                print("  [skip] VA %#x: does not look like a valid mesh" % mesh_va)
                continue
            lo, hi = fp
            slots_here = [t[2] for t in group]
            # every slot pointing at this mesh (even outside the selection)
            for lod in lods:
                for (i, mp, nm, idv, off) in p.slots(lods[lod]):
                    if mp == mesh_va and off not in slots_here:
                        slots_here.append(off)
            refs = p.external_refs(lo, hi, ignore_offsets=tuple(slots_here))
            root_ids = [t for t in group if t[5] == 0]
            if refs:
                # try to unblock by moving the strings stored in the padding
                r = None if dry else p.relocate_blockers(lo, hi, tuple(slots_here))
                if r is None:
                    print("  [skip] %s: %d external reference(s) point inside "
                          "(%s...) and are not strings -- removing would break them" %
                          (group[0][3][:40], len(refs), ", ".join(f"{o:#x}" for o, _ in refs[:3])))
                    continue
                print("    (relocated %d string(s) to the end, %d pointer(s) repointed)" % r)
            print("  remove %s: %#x..%#x (%d bytes), %d slot(s)%s" %
                  (group[0][3][:44], lo, hi, hi-lo, len(slots_here),
                   "  ID=0 attaches the loaded mesh to vroot" if root_ids else ""))
            ranges.append((lo, hi))
            zeros += slots_here
            total += hi - lo
        if not ranges:
            print("nothing removed.")
            return
        if dry:
            print("\n[dry] would remove %d bytes (%.1f%%)" % (total, 100.0*total/len(p.data)))
            return
        removed, nfix = p.remove_ranges(ranges, zeros)
        out2 = out or (os.path.splitext(container)[0] + "_slim.pck")
        size = p.save(out2)
        print("\nremoved %d bytes, %d pointers relocated" % (removed, nfix))
        print("saved: %s (%d bytes)" % (out2, size))
        return

    targets = []      # (lod, slot_index, slot_off, name, mesh_file)
    if auto:
        folder = os.path.dirname(os.path.abspath(container))
        for lod in want_lods:
            if lod not in lods:
                continue
            for (i, mp, nm, idv, off) in p.slots(lods[lod]):
                if mp == 0 and nm:
                    # PS2 uses "<name>.pck", PSP uses "<name>.psppck"
                    ext = ".psppck" if container.lower().endswith(".psppck") else ".pck"
                    for cand in (os.path.join(folder, nm + ext),
                                 os.path.join(folder, nm + ".pck"),
                                 os.path.join(folder, nm + ".psppck")):
                        if os.path.isfile(cand):
                            targets.append((lod, i, off, nm, cand))
                            break
    else:
        if not mesh:
            print("give the .mesh.pck to embed (or use --list / --auto-dir)")
            return
        for lod in want_lods:
            if lod not in lods:
                continue
            for (i, mp, nm, idv, off) in p.slots(lods[lod]):
                if (name is not None and nm == name) or (index is not None and i == index):
                    targets.append((lod, i, off, nm, mesh))

    if not targets:
        print("no matching slot found.")
        return

    # one append per distinct file; the same body serves several slots
    placed = {}
    for (lod, i, off, nm, mf) in targets:
        if dry:
            print("[dry] %s[%d] <- %s" % (lod, i, os.path.basename(mf)))
            continue
        if mf not in placed:
            va, nfix, blen = p.append_mesh_body(mf)
            placed[mf] = va
            print("appended %s: %d bytes at VA %#x (%d pointers rebased)" %
                  (os.path.basename(mf), blen, va, nfix))
        p.set_slot(off, placed[mf])
        print("  %s[%d] %s -> %#x" % (lod, i, nm[:52], placed[mf]))

    if dry:
        return
    out = out or (os.path.splitext(container)[0] + "_embedded.pck")
    size = p.save(out)
    print("\nsaved: %s (%d bytes)" % (out, size))


if __name__ == "__main__":
    main()
