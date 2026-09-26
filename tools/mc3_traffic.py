"""
mc3_traffic.py - replace meshes inside a city *_traffic.pck without breaking it.

A traffic container packs every traffic vehicle of a city into one file. Each
piece is the SAME structure as a standalone .mesh.pck -- tag, piece id at +0x06,
group count at +0x08, material table at +0x0C, block table at +0x10. The pieces
are reached from tables of consecutive pointers.

A PIECE HAS NO NAME OF ITS OWN. It is named by where it sits: the container's
vehicle directory says which vehicle owns it, and its piece id is a bone id in
that vehicle's skeleton -- 0 is the body at vroot, 4..7 are the wheels, and the
small one is the shadow. (The printable bytes at piece+0x18 are NOT a name;
they belong to the skeleton. See _adjacent_string.)

WHY EDITING IN PLACE IS RISKY, AND WHAT THIS DOES INSTEAD
The container stores strings inside what looks like padding: right before the
mesh at 0x24A70 sits the table of slot pointers, and the string "shadow" is
squeezed between the last pointer and the mesh. Anything that shifts bytes would
move those strings out from under the pointers that reference them.

So a replacement never moves a byte: the new mesh body is APPENDED at the end
(with its internal pointers rebased to where it landed) and only the slot pointer
is rewritten. The piece id is copied from the mesh being replaced, because that
is what ties the piece to its bone -- and so to its slot -- in the game.

Tags seen: 0x007A1E18 on atlanta/detroit/sd, 0x00BC8628 on tokyo. Both are the
same layout, so the scan validates the structure instead of trusting one tag.
"""
from __future__ import annotations

import importlib.util
import os
import struct

_HERE = os.path.dirname(os.path.abspath(__file__))


def _sibling(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    import sys as _s
    _s.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


embed = _sibling("mc3_pck_embed")

NAME_OFF = 0x18          # free gap in a traffic piece, NOT a name field
NAME_LEN = 8
PIECE_ID_OFF = 0x06

# The vehicle directory. Its three fields sit at file offset 0xB0, right in the
# header: a pointer to the list of vehicle objects, the count, and a pointer to
# the name table (104 bytes per entry, the name at +4).
DIR_OFF = 0xB0
DIR_NAME_STRIDE = 104
DIR_NAME_AT = 4
VEH_ROOT_AT = 0x2A4      # vehicle object -> root
ROOT_MESHGROUP_AT = 0x10  # root -> mesh group
MG_COUNT_AT = 2           # mesh group: u16 count
MG_ARRAY_AT = 8           # mesh group: -> array of slot pointers

# Each traffic vehicle carries its own skeleton, and the piece id IS the bone
# id -- which is where a piece's real name comes from. Layout of the header:
# u16 count at +0, tag 0x00010039 at +4, node array at +8; PS2 node stride.
ROOT_SKEL_AT = 0x68
SKEL_TAG = 0x00010039
SKEL_NODE_STRIDE = 68
SKEL_NAME_AT = 0x0C
SKEL_ID_AT = 0x38

# An object appended by a tool is not part of the file's original token-fixup
# list. It therefore has to carry the runtime vtable already translated. This
# address is the rmcModel mesh vtable in SLUS-21355 Remix; measured on 607
# retail meshes after loading. Readers still accept the original file tokens.
RUNTIME_MESH_VTABLE = 0x00626D98


class TrafficPck:
    """A city traffic container. Wraps mc3_pck_embed.Pck for the append/rebase
    machinery and adds the traffic-specific piece table."""

    def __init__(self, path):
        self.path = path
        self.pck = embed.Pck(path)
        self._pieces = None
        self._veh = None

    # ---------------------------------------------------------------- read
    @property
    def data(self):
        return self.pck.data

    def _looks_like_mesh(self, o):
        p = self.pck
        d = p.data
        if not p.inside(o + 0x20):
            return False
        tag = struct.unpack_from("<I", d, o)[0]
        token = (tag >> 16) in (0x007A, 0x00BC) and (tag & 0xFFFF) != 0
        if not token and tag != RUNTIME_MESH_VTABLE:
            return False
        num = struct.unpack_from("<I", d, o + 8)[0]
        if not (1 <= num <= 64):
            return False
        mat = struct.unpack_from("<I", d, o + 0x0C)[0]
        main = struct.unpack_from("<I", d, o + 0x10)[0]
        return p.inside(p.F(mat)) and p.inside(p.F(main))

    # ------------------------------------------------------------ directory
    def vehicles(self, refresh=False):
        """{piece_off: (vehicle_name, slot_off, index_within_vehicle, part_name)}.

        The container names its vehicles in a directory, and that is what the
        3D viewer shows -- "va_deliverytruck_a", not the part name. Chain:

            0xB0 -> object list, count, name table
            object[i] +0x2A4 -> root
            root     +0x10   -> mesh group (u16 count at +2, array at +8)
            array[k]         -> the piece, and the array slot IS the slot to
                                repoint

        Worth trusting: the slot the directory hands out is the same one the
        structural scan finds, on every piece the directory covers (103 of 103
        across the four cities).

        The part name comes from the vehicle's OWN skeleton at root+0x68: the
        piece id is a bone id, so id 0 is the body at vroot, 4..7 are whl0..3
        and the odd small piece is the shadow. Every vehicle in the four cities
        resolves this way, and the sizes agree -- the piece landing on "shadow"
        is the 256-byte one, the four landing on wheels are 512 bytes each.

        It does NOT cover everything: a city train is not in the vehicle list,
        so 12 pieces across detroit/sd/tokyo come back unnamed. Those are
        grouped by their own mesh group instead -- see pieces().
        """
        if getattr(self, "_veh", None) is not None and not refresh:
            return self._veh
        p = self.pck
        d = p.data
        out = {}

        def u32(o):
            return struct.unpack_from("<I", d, o)[0] if 0 <= o <= len(d) - 4 else 0

        def u16(o):
            return struct.unpack_from("<H", d, o)[0] if 0 <= o <= len(d) - 2 else 0

        def ok(v):
            return bool(v) and p.inside(p.F(v))

        try:
            p_list, n, p_names = u32(DIR_OFF), u16(DIR_OFF + 4), u32(DIR_OFF + 8)
            if not (ok(p_list) and ok(p_names) and 0 < n <= 256):
                self._veh = out
                return out
            for i in range(n):
                obj = u32(p.F(p_list) + 4 * i)
                if not ok(obj):
                    continue                      # no meshes: loads an external file
                root = u32(p.F(obj) + VEH_ROOT_AT)
                if not ok(root):
                    continue
                mg = u32(p.F(root) + ROOT_MESHGROUP_AT)
                if not ok(mg):
                    continue
                cnt, arr = u16(p.F(mg) + MG_COUNT_AT), u32(p.F(mg) + MG_ARRAY_AT)
                if not ok(arr) or cnt > 256:
                    continue
                base = p.F(p_names) + i * DIR_NAME_STRIDE + DIR_NAME_AT
                name = bytes(d[base:base + 60]).split(b"\0")[0].decode("latin1", "replace")
                bones = self._bones(p.F(root))
                for k in range(cnt):
                    slot = p.F(arr) + 4 * k
                    mp = u32(slot)
                    if ok(mp):
                        pid = u16(p.F(mp) + PIECE_ID_OFF)
                        out[p.F(mp)] = (name, slot, k, bones.get(pid, ""))
        except Exception:
            pass
        self._veh = out
        return out

    def roots(self):
        """[(vehicle_name, root_off)] straight from the directory at 0xB0.

        Same walk vehicles() does, but stopping at the root -- which is what
        both the mesh group and the skeleton hang off.
        """
        p = self.pck
        d = p.data
        out = []

        def u32(o):
            return struct.unpack_from("<I", d, o)[0] if 0 <= o <= len(d) - 4 else 0

        def u16(o):
            return struct.unpack_from("<H", d, o)[0] if 0 <= o <= len(d) - 2 else 0

        def ok(v):
            return bool(v) and p.inside(p.F(v))

        p_list, n, p_names = u32(DIR_OFF), u16(DIR_OFF + 4), u32(DIR_OFF + 8)
        if not (ok(p_list) and ok(p_names) and 0 < n <= 256):
            return out
        for i in range(n):
            obj = u32(p.F(p_list) + 4 * i)
            if not ok(obj):
                continue
            root = u32(p.F(obj) + VEH_ROOT_AT)
            if not ok(root):
                continue
            base = p.F(p_names) + i * DIR_NAME_STRIDE + DIR_NAME_AT
            name = bytes(d[base:base + 60]).split(b"\0")[0].decode("latin1", "replace")
            out.append((name, p.F(root)))
        return out

    def skeletons(self):
        """[(vehicle_name, [node, ...])] -- one skeleton per traffic vehicle.

        Each node is a dict with index, name, bone id, parent/child/next and the
        anchor. Unlike a vehicle container there is no single skeleton here: the
        directory hands out one root per vehicle and each carries its own, 19
        nodes on every car in every city.
        """
        p = self.pck
        d = p.data
        out = []

        def u32(o):
            return struct.unpack_from("<I", d, o)[0] if 0 <= o <= len(d) - 4 else 0

        def u16(o):
            return struct.unpack_from("<H", d, o)[0] if 0 <= o <= len(d) - 2 else 0

        def txt(o):
            o = p.F(o)
            if not p.inside(o):
                return ""
            e = d.find(b"\0", o)
            if not (o < e <= o + 64):
                return ""
            v = bytes(d[o:e]).decode("latin1", "replace")
            return v if all(32 <= ord(c) < 127 for c in v) else ""

        for name, root in self.roots():
            hdr = p.F(u32(root + ROOT_SKEL_AT))
            if not p.inside(hdr) or u32(hdr + 4) != SKEL_TAG:
                continue
            cnt, arr = u16(hdr), p.F(u32(hdr + 8))
            if not p.inside(arr) or not (0 < cnt <= 256):
                continue
            nodes = []
            for k in range(cnt):
                o = arr + SKEL_NODE_STRIDE * k
                nodes.append(dict(
                    index=k, off=o, name=txt(u32(o + SKEL_NAME_AT)),
                    bone=u16(o + SKEL_ID_AT),
                    anchor=struct.unpack_from("<fff", d, o),
                    next=u32(o + 0x14), child=u32(o + 0x18), parent=u32(o + 0x1C)))
            out.append((name, nodes))
        return out

    def _bones(self, root_off):
        """{bone_id: name} of the skeleton hanging off a vehicle root."""
        p = self.pck
        d = p.data
        out = {}

        def u32(o):
            return struct.unpack_from("<I", d, o)[0] if 0 <= o <= len(d) - 4 else 0

        def u16(o):
            return struct.unpack_from("<H", d, o)[0] if 0 <= o <= len(d) - 2 else 0

        hdr = p.F(u32(root_off + ROOT_SKEL_AT))
        if not p.inside(hdr) or u32(hdr + 4) != SKEL_TAG:
            return out
        cnt, arr = u16(hdr), p.F(u32(hdr + 8))
        if not p.inside(arr) or not (0 < cnt <= 256):
            return out
        for k in range(cnt):
            o = arr + SKEL_NODE_STRIDE * k
            so = p.F(u32(o + SKEL_NAME_AT))
            if not p.inside(so):
                continue
            end = d.find(b"\0", so)
            if not (so < end <= so + 64):
                continue
            s = bytes(d[so:end]).decode("latin1", "replace")
            if s and all(32 <= ord(c) < 127 for c in s):
                out.setdefault(u16(o + SKEL_ID_AT), s)
        return out

    def _orphan_group(self, slot):
        """Which mesh group owns a slot the directory never mentioned.

        Deterministic, unlike guessing from nearby strings: walk back along the
        slot array to whatever points at it -- that pointer lives at
        mesh_group+8. Pieces sharing a mesh group are one object.
        """
        g = self.pck.graph()
        for delta in range(0, 0x400, 4):
            src = g.sources_of(slot - delta)
            if src:
                return src[0] - MG_ARRAY_AT
        return None

    def pieces(self, refresh=False):
        """[(index, mesh_off, slot_off, piece_id, groups, name, size)].

        A piece counts only when exactly one pointer references it -- that
        pointer is the slot to rewrite. Anything referenced from several places
        is left alone, since repointing one of them would desync the others.

        Each piece also carries `vehicle` (from the directory) and `label`, the
        "vehicle / part" string worth showing to a human.
        """
        if self._pieces is not None and not refresh:
            return self._pieces
        p = self.pck
        d = p.data
        graph = p.graph()
        out = []
        for o in range(0x80, len(d) - 0x20, 4):
            if not self._looks_like_mesh(o):
                continue
            src = graph.sources_of(o)
            if len(src) != 1:
                continue
            fp = p.mesh_footprint(p.V(o))
            if not fp:
                continue
            out.append(dict(index=len(out), off=o, slot=src[0],
                            piece_id=struct.unpack_from("<H", d, o + PIECE_ID_OFF)[0],
                            groups=struct.unpack_from("<I", d, o + 8)[0],
                            neighbour=self._adjacent_string(o), size=fp[1] - fp[0]))

        veh = self.vehicles(refresh=refresh)
        unlisted = {}
        for pc in out:
            hit = veh.get(pc["off"])
            if hit:
                pc["vehicle"], pc["part_index"], bone = hit[0], hit[2], hit[3]
            else:
                mg = self._orphan_group(pc["slot"])
                if mg not in unlisted:
                    unlisted[mg] = "(not in the vehicle list #%d)" % (len(unlisted) + 1)
                pc["vehicle"], pc["part_index"], bone = unlisted[mg], None, ""
            # A piece has no name of its own. Its identity is the vehicle it
            # belongs to plus the bone its piece id points at.
            pc["part"] = bone or "id %d" % pc["piece_id"]
            pc["label"] = "%s / %s" % (pc["vehicle"], pc["part"])
        out.sort(key=lambda x: (x["vehicle"], x["off"]))
        for i, pc in enumerate(out):
            pc["index"] = i
        self._pieces = out
        return out

    def _adjacent_string(self, o):
        """The string sitting at piece+0x18 -- WHICH IS NOT THE PIECE'S NAME.

        This looked like an inline name and was reported as one for a while. It
        is not. A traffic piece uses +0x00..+0x14 and +0x20, leaving +0x18 free,
        and the exporter's allocator packs SKELETON NODE NAMES into that gap.
        Every one of them is pointed at from a node's name field at node+0x0C,
        with a 3-float anchor and a sane bone id at +0x38 -- the PS2 skel node
        layout exactly.

        Three things gave it away: the 3-group body of the atlanta delivery
        truck came out called "wheels" and the detroit one "hlight0"; the same
        bus reads "whl3" in atlanta but "hlight1" in detroit; and the bone id of
        the node owning the string never matches the piece id. It is adjacency,
        not association.

        Most pieces have plain 0xCD there, so this returns "" for them. Kept
        only as a diagnostic -- never use it to label a piece.
        """
        raw = bytes(self.pck.data[o + NAME_OFF:o + NAME_OFF + NAME_LEN])
        raw = raw.split(b"\0")[0].rstrip(b"\xCD")
        try:
            s = raw.decode("latin1")
        except Exception:
            return ""
        return s if s and all(32 <= ord(c) < 127 for c in s) else ""

    # -------------------------------------------------------------- extract
    def extract(self, index, out_path):
        """Write a piece out as a standalone .mesh.pck.

        The trick that keeps it valid: the new file declares the piece's own
        virtual address as its base, so every pointer inside the body still
        resolves without touching a single one of them.
        """
        pc = self.pieces()[index]
        p = self.pck
        fp = p.mesh_footprint(p.V(pc["off"]))
        if not fp:
            raise ValueError("could not measure this piece")
        lo, hi = fp
        head = bytearray(p.data[:0x80])
        struct.pack_into("<I", head, 0x00, p.V(lo))     # VA of what lands at 0x80
        struct.pack_into("<I", head, 0x04, 22)          # standalone vehicle mesh
        struct.pack_into("<I", head, 0x08, 1)
        struct.pack_into("<I", head, 0x0C, hi - lo)
        with open(out_path, "wb") as f:
            f.write(bytes(head))
            f.write(bytes(p.data[lo:hi]))
        return 0x80 + (hi - lo)

    # ------------------------------------------------------------- materials
    def materials(self, off):
        """The shader index of each group of the piece at file offset `off`."""
        p = self.pck
        num = struct.unpack_from("<I", p.data, off + 8)[0]
        mo = p.F(struct.unpack_from("<I", p.data, off + 0x0C)[0])
        if not (0 <= mo <= len(p.data) - 2 * num):
            return []
        return [struct.unpack_from("<H", p.data, mo + 2 * i)[0] for i in range(num)]

    def shader_count(self):
        """How many shaders this container has. A material index at or above
        this HANGS THE GAME, which is the whole reason replacements get their
        index forced."""
        try:
            shading = _sibling("mc3_shadinggroup")
            return len(shading.extract_shadinggroup(self.path)[1])
        except Exception:
            return 0

    def set_materials(self, off, value):
        """Write the shader index of every group of a piece.

        ``value`` may be one integer (force every group) or a sequence with one
        index per group.  The sequence form is what the selective shader
        transplant uses to preserve the donor vehicle's materials.

        Written only where it is safe; otherwise a fresh table is appended and
        the mesh repointed at it. Two things make that second path necessary:

        * a donor mesh carved out of another container keeps ITS pointers, which
          can land on the shared material area -- writing there would rewrite the
          shader index of pieces we never touched;
        * in a standalone .mesh.pck the material table sits at +0x18, which is
          exactly where a traffic piece keeps its inline name. Patching in place
          there would trade one for the other: copying the name over it made the
          shader index read back as 26743, which is simply the letters "wh" of
          "wheels".
        """
        p = self.pck
        num = struct.unpack_from("<I", p.data, off + 8)[0]
        if isinstance(value, int):
            values = [value] * num
        else:
            values = list(value)
            if len(values) != num:
                raise ValueError("material table has %d values for %d groups" %
                                 (len(values), num))
        mo = p.F(struct.unpack_from("<I", p.data, off + 0x0C)[0])
        appended_lo = getattr(self, "_append_lo", None)
        inside = (appended_lo is not None and appended_lo <= mo
                  and mo >= off + 0x20                  # clear of the header/name
                  and mo + 2 * num <= len(p.data))
        if not inside:
            while len(p.data) % 16:
                p.data += b"\xCD"
            mo = len(p.data)
            p.data += b"\x00" * (2 * num)
            while len(p.data) % 16:
                p.data += b"\xCD"
            struct.pack_into("<I", p.data, 0x0C, len(p.data) - 0x80)
            p.body = len(p.data) - 0x80
            struct.pack_into("<I", p.data, off + 0x0C, p.V(mo))
        for i, shader in enumerate(values):
            if not 0 <= shader <= 0xFFFF:
                raise ValueError("shader index %d does not fit u16" % shader)
            struct.pack_into("<H", p.data, mo + 2 * i, shader)
        return num

    # -------------------------------------------------------------- replace
    def replace(self, index, mesh_path, keep_id=True, material=1,
                translated_vtable=True):
        """Append `mesh_path` and repoint the slot. Nothing already in the file
        moves, so no existing pointer -- or string hidden in the padding -- can
        break.

        `material` forces every group of the new mesh onto one shader index
        (None leaves the donor's own). This is not cosmetic: a traffic container
        only carries 6 or 7 shaders, while a vehicle mesh routinely refers to
        index 14, and an index past the end HANGS THE GAME.

        There is no name to carry over: what sits at +0x18 belongs to the
        skeleton, not to the piece (see _adjacent_string). The gap is left as
        0xCD padding, which is what an untouched piece looks like there.

        Returns (new_va, pointers_rebased, appended_bytes).
        """
        pc = self.pieces()[index]
        p = self.pck
        old_id = struct.unpack_from("<H", p.data, pc["off"] + PIECE_ID_OFF)[0]

        self._append_lo = len(p.data)
        va, nfix, blen = p.append_mesh_body(mesh_path)
        new_off = p.F(va)
        if not self._looks_like_mesh(new_off):
            raise ValueError("the appended file does not look like a mesh "
                             "(is it a single .mesh.pck?)")
        # Materials first: a standalone .mesh.pck keeps its table at +0x18, so it
        # has to be relocated before that gap is cleared.
        source_materials = self.materials(new_off)
        if not source_materials:
            source_materials = [0] * struct.unpack_from("<I", p.data,
                                                         new_off + 8)[0]
        self.set_materials(new_off, material if material is not None
                           else source_materials)
        # Wipe what the donor left in the gap -- its old material table. Safe
        # because set_materials has already repointed +0x0C somewhere else, so
        # nothing references these 8 bytes any more.
        p.data[new_off + NAME_OFF:new_off + NAME_OFF + NAME_LEN] = b"\xCD" * NAME_LEN
        if keep_id:
            struct.pack_into("<H", p.data, new_off + PIECE_ID_OFF, old_id)
        if translated_vtable:
            struct.pack_into("<I", p.data, new_off, RUNTIME_MESH_VTABLE)

        struct.pack_into("<I", p.data, pc["slot"], va)
        self._pieces = None
        self._veh = None
        self._append_lo = None
        return va, nfix, blen

    def save(self, out_path=None):
        return self.pck.save(out_path or self.path)


def summary(path):
    t = TrafficPck(path)
    ps = t.pieces()
    return t, ps


def main():
    import sys
    if len(sys.argv) < 2:
        print(__doc__)
        return
    t, ps = summary(sys.argv[1])
    print("%s  VA=%#x  %d bytes  %d piece(s)"
          % (os.path.basename(sys.argv[1]), t.pck.va, len(t.pck.data), len(ps)))
    for pc in ps:
        print("  [%3d] id=%-4d %-10s %2d group(s) %7d B  mesh@%#08x slot@%#08x"
              % (pc["index"], pc["piece_id"], pc["label"], pc["groups"],
                 pc["size"], pc["off"], pc["slot"]))


if __name__ == "__main__":
    main()
