"""Transplant selected PS2 vehicle shaders between vehicle containers.

Traffic vehicles do not share one global shader group: each vehicle root owns
an ``rmcShaderGroup`` at ``root+0x08``.  This makes a contained transplant
possible.  A new group is appended for the chosen vehicle, its wheel/shadow
shaders keep pointing at the target resources, and only the donor shaders used
by the replacement body are cloned with their reachable dependencies.

No existing byte moves.  The donor's local-parameter list is cloned in its
original order, so compiled local indices used by carpaint/windows/lights stay
valid.  Vtable/resource tokens are deliberately treated as opaque data; Tokyo
therefore works through the same structural path.

``clone_shader_into_slot`` is the contained player-car variant: it replaces
one existing slot while preserving the target shader group and its local list.
"""
from __future__ import annotations

import argparse
import bisect
import importlib.util
import os
import re
import struct
import sys
import types
from dataclasses import dataclass


SHADER_NAME_RE = re.compile(rb"[A-Za-z_][A-Za-z0-9_.]{2,}\.shadert\x00")


@dataclass(frozen=True)
class ShaderEntry:
    index: int
    pointer_off: int
    resource_va: int
    resource_off: int
    name: str


@dataclass(frozen=True)
class ShaderGroup:
    group_off: int
    array_off: int
    size: int
    capacity: int
    entries: tuple[ShaderEntry | None, ...]
    local_head_off: int | None
    owner_fields: tuple[int, ...]


@dataclass(frozen=True)
class CloneResult:
    offset_map: dict[int, int]
    segment_count: int
    source_bytes: int
    appended_bytes: int
    pointers_rebased: int
    texture_count: int
    texture_bytes: int
    external_texture_fields: tuple[int, ...]
    external_texture_names: tuple[str, ...]
    fallback_texture_off: int | None


@dataclass(frozen=True)
class TransplantResult:
    shader_map: dict[int, int]
    shader_names: dict[int, str]
    preserved_indices: tuple[int, ...]
    old_group_off: int
    new_group_off: int
    new_array_off: int
    shader_count: int
    clone: CloneResult


@dataclass(frozen=True)
class SlotCloneResult:
    donor_index: int
    target_index: int
    shader_name: str
    target_group_off: int
    target_resource_off: int
    clone: CloneResult


class Pck:
    def __init__(self, source):
        if isinstance(source, (str, os.PathLike)):
            self.path = os.fspath(source)
            self.data = bytearray(open(self.path, "rb").read())
            self.owner = None
        else:
            self.owner = source
            self.path = getattr(source, "path", "<memory>")
            self.data = source.data
        if len(self.data) < 0x80:
            raise ValueError("file is too small to be a PCK")
        self.va = self.u32(0)
        self.base = self.va - 0x80
        self.body = self.u32(0x0C)

    def u16(self, off: int) -> int:
        return struct.unpack_from("<H", self.data, off)[0]

    def u32(self, off: int) -> int:
        return struct.unpack_from("<I", self.data, off)[0]

    def inside(self, off: int, size: int = 4) -> bool:
        return 0 <= off and off + size <= len(self.data)

    def file(self, va: int) -> int:
        return va - self.base

    def virtual(self, off: int) -> int:
        return off + self.base

    def pointer(self, off: int) -> int | None:
        if not self.inside(off):
            return None
        va = self.u32(off)
        target = self.file(va)
        return target if self.inside(target) and target % 4 == 0 else None

    def cstr(self, off: int, limit: int = 256) -> str | None:
        if not self.inside(off, 1):
            return None
        end = self.data.find(0, off, min(len(self.data), off + limit))
        if end < 0:
            return None
        raw = bytes(self.data[off:end])
        try:
            value = raw.decode("ascii")
        except UnicodeDecodeError:
            return None
        return value if value and all(32 <= ord(ch) < 127 for ch in value) else None

    def sources_of(self, target_off: int) -> list[int]:
        target_va = self.virtual(target_off)
        needle = struct.pack("<I", target_va)
        out = []
        pos = self.data.find(needle)
        while pos >= 0:
            if pos % 4 == 0:
                out.append(pos)
            pos = self.data.find(needle, pos + 1)
        return out

    def finish_append(self):
        while len(self.data) % 16:
            self.data += b"\xCD"
        self.body = len(self.data) - 0x80
        struct.pack_into("<I", self.data, 0x0C, self.body)
        if self.owner is not None:
            self.owner.body = self.body
            # Packet-region caches describe the immutable retail portion.  New
            # shader resources contain no VIF packets, but invalidating is safer
            # for callers that subsequently append a mesh.
            if hasattr(self.owner, "_data_regions"):
                self.owner._data_regions = None


def _name_offsets(pck: Pck) -> dict[int, str]:
    return {
        match.start(): match.group()[:-1].decode("ascii")
        for match in SHADER_NAME_RE.finditer(pck.data)
    }


def _shader_name(pck: Pck, resource_off: int,
                 name_offsets: dict[int, str]) -> str | None:
    """Return the .shadert name referenced in a resource's first 0x140 bytes."""
    for rel in range(0, 0x140, 4):
        field = resource_off + rel
        target = pck.pointer(field)
        if target is not None and target in name_offsets:
            return name_offsets[target]
    return None


def read_shader_group(pck: Pck, group_off: int,
                      minimum_count: int = 0) -> ShaderGroup:
    """Read the 16-byte rmcShaderGroup plus its ptr-vector.

    ``minimum_count`` covers the retail SD/Tokyo off-by-one layout: their
    serialized vector size is five while a mesh legitimately uses material 5,
    stored immediately after those five entries.  A rewritten group never
    relies on that quirk; its size and capacity equal the actual new array.
    """
    if not pck.inside(group_off, 0x10):
        raise ValueError("shader-group object points outside the PCK")
    array_off = pck.pointer(group_off + 4)
    size = pck.u16(group_off + 8)
    capacity = pck.u16(group_off + 0x0A)
    count = max(size, minimum_count)
    if array_off is None or not (0 < count <= 4096):
        raise ValueError("invalid shader-group vector")
    if not pck.inside(array_off, 4 * count):
        raise ValueError("shader-group vector leaves the PCK")

    names = _name_offsets(pck)
    entries: list[ShaderEntry | None] = []
    for index in range(count):
        pointer_off = array_off + 4 * index
        resource_off = pck.pointer(pointer_off)
        if resource_off is None:
            entries.append(None)
            continue
        name = _shader_name(pck, resource_off, names)
        if name is None:
            entries.append(None)
            continue
        entries.append(ShaderEntry(index, pointer_off, pck.u32(pointer_off),
                                   resource_off, name))
    local_head = pck.pointer(group_off + 0x0C)
    return ShaderGroup(group_off, array_off, size, capacity, tuple(entries),
                       local_head, (group_off + 4,))


def local_parameters(pck: Pck, group: ShaderGroup):
    """Return the serialized local list as ``[(name, float), ...]``."""
    out = []
    current = group.local_head_off
    seen = set()
    while current is not None:
        if current in seen or not pck.inside(current, 12):
            raise ValueError("shader local list is cyclic or leaves the PCK")
        seen.add(current)
        name_off = pck.pointer(current + 4)
        name = pck.cstr(name_off) if name_off is not None else None
        if name is None:
            raise ValueError("shader local at %#x has no valid name" % current)
        value = struct.unpack_from("<f", pck.data, current + 8)[0]
        out.append((name, value))
        next_va = pck.u32(current)
        if not next_va:
            current = None
        else:
            current = pck.file(next_va)
            if not pck.inside(current, 12):
                raise ValueError("shader local next pointer leaves the PCK")
    return out


def find_shader_group(pck: Pck) -> ShaderGroup:
    names = _name_offsets(pck)
    if not names:
        raise ValueError("no .shadert names found")

    cache: dict[int, str | None] = {}

    def entry_at(pointer_off: int):
        resource = pck.pointer(pointer_off)
        if resource is None:
            return None
        if resource not in cache:
            cache[resource] = _shader_name(pck, resource, names)
        return (resource, cache[resource]) if cache[resource] else None

    # Prefer the actual [token, slots*, size/capacity, local_head*] object.
    # A vehicle can serialize local_head immediately before the slots array;
    # because that node also points at a .shadert-owned resource, the old
    # longest-run fallback started one word too early and then could not find
    # the structural owner of the resulting run.  Anonymous final slots are
    # valid, so score the whole declared vector by its named majority.
    structural = []
    for group_off in range(0x80, len(pck.data) - 0x10, 4):
        array_off = pck.pointer(group_off + 4)
        size = pck.u16(group_off + 8)
        capacity = pck.u16(group_off + 0x0A)
        if (array_off is None or not (1 <= size <= capacity <= 4096) or
                not pck.inside(array_off, 4 * size)):
            continue
        named = sum(entry_at(array_off + 4 * index) is not None
                    for index in range(size))
        if named >= 2 and named * 2 >= size:
            structural.append((named, size, group_off))
    if structural:
        _named, _size, group_off = max(structural)
        return read_shader_group(pck, group_off)

    # Legacy fallback for containers without the normal group header.
    best_off = 0
    best: list[tuple[int, str]] = []
    off = 0x80
    while off <= len(pck.data) - 4:
        first = entry_at(off)
        if first is None:
            off += 4
            continue
        run = []
        pos = off
        while pos <= len(pck.data) - 4:
            item = entry_at(pos)
            if item is None:
                break
            run.append(item)
            pos += 4
        if len(run) > len(best):
            best_off, best = off, run
        off = max(pos, off + 4)
    if not best:
        raise ValueError("no shading-group pointer array found")

    owners = [src for src in pck.sources_of(best_off)
              if not (best_off <= src < best_off + 4 * len(best))]
    candidates = []
    for owner in owners:
        group_off = owner - 4
        if not pck.inside(group_off, 0x10):
            continue
        size, capacity = pck.u16(group_off + 8), pck.u16(group_off + 0x0A)
        if 0 < size <= len(best) and size <= capacity <= 4096:
            candidates.append((size, group_off))
    if not candidates:
        raise ValueError("shading-group array has no structural owner")
    # The longest named run can spill into adjacent pointer fields on SD/Tokyo.
    # Prefer the owner whose declared vector consumes the largest valid prefix.
    _size, group_off = max(candidates)
    return read_shader_group(pck, group_off)


def _load_embed_module():
    try:
        import mc3_pck_embed as module
        return module
    except ImportError:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "mc3_pck_embed.py")
        if not os.path.isfile(path):
            raise
        spec = importlib.util.spec_from_file_location("mc3_pck_embed", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("mc3_pck_embed", module)
        spec.loader.exec_module(module)
        return module


def _printable_end(data: bytearray, off: int, limit: int = 256) -> int | None:
    end = data.find(0, off, min(len(data), off + limit))
    if end <= off:
        return None
    raw = data[off:end]
    return end + 1 if all(32 <= byte < 127 for byte in raw) else None


@dataclass(frozen=True)
class _TextureRegion:
    descriptor_off: int
    pixels_off: int
    end: int


_DESC_ANCHOR = bytes.fromhex("cdcdcdcd0e00ffff00000000cdcdcdcd")


def _texture_regions(pck: Pck) -> tuple[_TextureRegion, ...]:
    """Find inline PS2 texture descriptor+pixel regions structurally.

    The descriptor's +0x0C field is ``data_bytes + 0x100``.  Its pixels (and
    palette) start at +0x90.  The anchor and size rule are shared by all retail
    vehicle variants and do not depend on vtable values.
    """
    out = []
    pos = pck.data.find(_DESC_ANCHOR)
    while pos >= 0:
        descriptor = pos - 0x10
        if pck.inside(descriptor, 0x90):
            stored_size = pck.u32(descriptor + 0x0C)
            data_bytes = stored_size - 0x100
            pixels = descriptor + 0x90
            end = pixels + data_bytes
            if 0 < data_bytes <= 0x400000 and end <= len(pck.data):
                out.append(_TextureRegion(descriptor, pixels, end))
        pos = pck.data.find(_DESC_ANCHOR, pos + 4)
    return tuple(out)


def _inline_texture_infos(pck: Pck):
    """Return ``{info_offset: region}`` for materialized inline textures.

    A descriptor points back to ``info+0x48``.  Checking both directions keeps
    an address-looking GS word from being mistaken for a texture owner.
    """
    out = {}
    for region in _texture_regions(pck):
        owner_field = pck.pointer(region.descriptor_off + 8)
        if owner_field is None or owner_field < 0x48:
            continue
        info = owner_field - 0x48
        if (pck.inside(info, 0x4C) and
                pck.pointer(info + 0x48) == region.descriptor_off):
            out[info] = region
    return out


def _texture_reference_tokens(pck: Pck, infos):
    """Learn the texture-reference class token from internal references.

    Serialized vtables differ between retail profiles, so the token is learned
    from shape: a type-2 reference has a valid name at +8 and an inline texture
    info at +0x0C.  External references of the same class can then be found
    without baking in Atlanta's 0x007A1260 value.
    """
    tokens = set()
    for off in range(0x80, len(pck.data) - 0x10, 4):
        if pck.data[off + 4] != 2:
            continue
        info = pck.pointer(off + 0x0C)
        name_off = pck.pointer(off + 8)
        if (info in infos and name_off is not None and
                pck.cstr(name_off) is not None):
            tokens.add(pck.u32(off))
    return tokens


def _named_inline_texture_infos(pck: Pck):
    infos = _inline_texture_infos(pck)
    tokens = _texture_reference_tokens(pck, infos)
    named = {}
    for off in range(0x80, len(pck.data) - 0x10, 4):
        if pck.u32(off) not in tokens:
            continue
        info = pck.pointer(off + 0x0C)
        name_off = pck.pointer(off + 8)
        name = pck.cstr(name_off) if name_off is not None else None
        if info in infos and name is not None:
            named.setdefault(name, info)
    return named


def _safe_texture_fallback(pck: Pck):
    """A materialized neutral texture already owned by the target container.

    Atlanta names its flat 8x8 white texture ``__envmap__``.  Tokyo carries
    the same 4bpp placeholder without a texture-reference name, so its
    descriptor shape is the portable fallback.
    """
    named = _named_inline_texture_infos(pck)
    if "__envmap__" in named:
        return named["__envmap__"]
    candidates = []
    for info, region in _inline_texture_infos(pck).items():
        fmt = bytes(pck.data[region.descriptor_off + 0x0C:
                             region.descriptor_off + 0x10])
        if fmt == b"\x60\x01\x00\x00" and region.end - region.pixels_off == 96:
            candidates.append(info)
    return candidates[0] if candidates else None


def _external_texture_nodes(pck: Pck, blocks, tokens):
    """External rmcTextureReference nodes contained in cloned ranges.

    Their +0x0C values are addresses from the donor's runtime dictionary, not
    PCK-internal pointers.  Leaving one intact is unsafe: if lookup by name has
    not happened yet, Place adds the traffic image delta to that foreign value.
    """
    out = []
    for off in range(0x80, len(pck.data) - 0x10, 4):
        if (pck.u32(off) not in tokens or not _contains(blocks, off) or
                not _contains(blocks, off + 0x0F)):
            continue
        name_off = pck.pointer(off + 8)
        name = pck.cstr(name_off) if name_off is not None else None
        raw = pck.u32(off + 0x0C)
        if name is not None and raw and pck.pointer(off + 0x0C) is None:
            out.append((off, name, raw))
    return out


def _shader_resource_ranges(pck: Pck, group: ShaderGroup, graph):
    """Return one ownership interval per top-level shader resource.

    Vehicle serializers place each shader and its private dependent objects
    before the next shader pointer.  The last interval ends at the first later
    object referenced from before the shader arena (normally the next LOD
    table).  This distinction matters: using every *interior* pointer target as
    a boundary truncates texture-info objects at their first field.
    """
    starts = sorted({entry.resource_off for entry in group.entries if entry})
    if not starts:
        raise ValueError("donor shader group contains no recognized resources")
    first, last = starts[0], starts[-1]
    terminal = None
    for candidate in graph.targets():
        if candidate <= last:
            continue
        if any(source < first for source in graph.sources_of(candidate)):
            terminal = candidate
            break
    if terminal is None or terminal <= last or terminal - last > 0x400000:
        raise ValueError("cannot determine the end of the donor shader arena")
    ends = starts[1:] + [terminal]
    return dict(zip(starts, ends))


def _merged_ranges(ranges):
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _contains(ranges, off: int) -> bool:
    index = bisect.bisect_right(ranges, [off, 1 << 62]) - 1
    return index >= 0 and ranges[index][0] <= off < ranges[index][1]


def _clone_reachable(target: Pck, donor: Pck, donor_group: ShaderGroup,
                     donor_indices: tuple[int, ...]) -> CloneResult:
    """Clone complete selected shaders and their reachable dependencies.

    Top-level shader ownership intervals are copied whole.  Dependencies that
    live elsewhere (shared nodes, strings, dictionary-reference records and the
    local-parameter list) are followed through the pointer graph.  Inline PS2
    textures receive special treatment because the descriptor points back to
    its owner while its pixel payload is contiguous rather than pointer-linked.
    Pixel bytes are opaque and are never scanned as possible pointers.
    """
    embed = _load_embed_module()
    source = embed.Pck(donor.path)
    if bytes(source.data) != bytes(donor.data):
        raise ValueError("donor changed while building its pointer graph")
    graph = source.graph()
    pointers = graph.all_pointers()
    pointer_offsets = [off for off, _va in pointers]
    targets = graph.targets()
    shader_ranges = _shader_resource_ranges(donor, donor_group, graph)
    texture_regions = _texture_regions(donor)
    texture_by_descriptor = {item.descriptor_off: item
                             for item in texture_regions}
    texture_infos = _inline_texture_infos(donor)
    texture_reference_tokens = _texture_reference_tokens(donor, texture_infos)

    selected_roots = [donor_group.entries[index].resource_off
                      for index in donor_indices]
    requested_ranges = [(root, shader_ranges[root]) for root in selected_roots]
    mapped_points = set(selected_roots)

    # Local-list nodes are fixed 12-byte records.  Treating the entire gap up
    # to the next shader as theirs would pull unrelated donor shaders merely
    # because the serializer interleaves these small records.
    local_nodes = set()
    current = donor_group.local_head_off
    while current is not None:
        if current in local_nodes or not donor.inside(current, 12):
            raise ValueError("shader local list is cyclic or leaves the PCK")
        local_nodes.add(current)
        requested_ranges.append((current, current + 12))
        mapped_points.add(current)
        name_off = donor.pointer(current + 4)
        name_end = (_printable_end(donor.data, name_off)
                    if name_off is not None else None)
        if name_off is None or name_end is None:
            raise ValueError("shader local at %#x has no valid name" % current)
        requested_ranges.append((name_off, name_end))
        mapped_points.add(name_off)
        next_va = donor.u32(current)
        current = donor.file(next_va) if next_va else None

    opaque = [(item.pixels_off, item.end) for item in texture_regions]
    opaque_merged = _merged_ranges(opaque)

    def dependency_range(start: int):
        if start in local_nodes:
            return start, start + 12
        string_end = _printable_end(donor.data, start)
        if string_end is not None:
            return start, string_end
        region = texture_by_descriptor.get(start)
        if region is not None:
            return region.descriptor_off, region.end

        # Texture node: +8 names it and +0x0C is either an inline-info pointer
        # or a serialized dictionary reference.  The node itself is 16 bytes.
        if donor.inside(start, 0x10):
            name_off = donor.pointer(start + 8)
            if name_off is not None and donor.cstr(name_off) is not None:
                return start, start + 0x10

        # Texture info: +0x48 points at a descriptor whose +8 back-pointer
        # targets that exact owner field.  The info object is 0xA0 bytes in all
        # measured retail vehicle profiles; no token value is assumed.
        if donor.inside(start, 0xA0):
            descriptor = donor.pointer(start + 0x48)
            region = texture_by_descriptor.get(descriptor)
            if (region is not None and donor.pointer(descriptor + 8) ==
                    start + 0x48):
                return start, start + 0xA0

        # Other shared resources in vehicle PCKs (registration records,
        # pointer arrays and dictionary references) are bounded by the next
        # independently pointed address.
        index = bisect.bisect_right(targets, start)
        end = targets[index] if index < len(targets) else None
        if end is None or end <= start or end - start > 0x1000:
            raise ValueError("cannot size dependency at %#x" % start)
        return start, end

    # Grow the range set until every real pointer field in it has a mapped
    # target.  Texture pixels are intentionally excluded from this scan.
    while True:
        blocks = _merged_ranges(requested_ranges)
        changed = False

        # If a copied shader interval intersects a descriptor, include its
        # complete pixel payload even when the next shader starts unusually
        # close to the descriptor.
        for item in texture_regions:
            if (_contains(blocks, item.descriptor_off) and
                    not _contains(blocks, item.end - 1)):
                requested_ranges.append((item.descriptor_off, item.end))
                changed = True

        if changed:
            continue
        for field, target_va in pointers:
            if not _contains(blocks, field) or _contains(opaque_merged, field):
                continue
            dependency = source.F(target_va)
            mapped_points.add(dependency)
            if _contains(blocks, dependency):
                continue
            requested_ranges.append(dependency_range(dependency))
            changed = True
        if not changed:
            break

    blocks = _merged_ranges(requested_ranges)

    external_texture_nodes = _external_texture_nodes(
        donor, blocks, texture_reference_tokens)
    fallback_texture_off = None
    if external_texture_nodes:
        # Retail traffic containers carry a materialized 8x8 white texture
        # (named __envmap__ in Atlanta and anonymous in Tokyo).  It is already
        # part of the target's PageIn owner graph, so it is a safe fallback
        # when a donor-specific name has not yet been registered.  Name lookup
        # still wins whenever the real texture is available; only the
        # otherwise-invalid serialized pointer changes.
        fallback_texture_off = _safe_texture_fallback(target)
        if fallback_texture_off is None:
            names = ", ".join(sorted({name for _off, name, _raw
                                      in external_texture_nodes}))
            raise ValueError(
                "donor has unresolved external texture references (%s), but "
                "target has no materialized neutral texture fallback" % names)

    append_start = len(target.data)
    block_map: list[tuple[int, int, int]] = []
    for source_start, source_end in blocks:
        # Preserve modulo-16 placement; several PS2 resources use qword loads.
        while len(target.data) % 16 != source_start % 16:
            target.data += b"\xCD"
        dest_start = len(target.data)
        target.data += donor.data[source_start:source_end]
        block_map.append((source_start, source_end, dest_start))

    def mapped_offset(source_off: int) -> int:
        index = bisect.bisect_right(block_map,
                                    (source_off, 1 << 62, 1 << 62)) - 1
        if index >= 0:
            block_start, block_end, dest_start = block_map[index]
            if source_off < block_end:
                return dest_start + source_off - block_start
        raise ValueError("reached offset %#x was not packed" % source_off)

    offset_map = {off: mapped_offset(off) for off in mapped_points}
    rewritten = set()
    for start, end, _dest in block_map:
        lo = bisect.bisect_left(pointer_offsets, start)
        hi = bisect.bisect_left(pointer_offsets, end)
        for field, target_va in pointers[lo:hi]:
            if field in rewritten or _contains(opaque_merged, field):
                continue
            dependency = source.F(target_va)
            if not _contains(blocks, dependency):
                raise ValueError("dependency closure missed %#x" % dependency)
            dest_field = mapped_offset(field)
            struct.pack_into("<I", target.data, dest_field,
                             target.virtual(mapped_offset(dependency)))
            rewritten.add(field)

    external_texture_fields = []
    if external_texture_nodes:
        fallback_va = target.virtual(fallback_texture_off)
        for source_node, _name, _raw in external_texture_nodes:
            dest_field = mapped_offset(source_node) + 0x0C
            struct.pack_into("<I", target.data, dest_field, fallback_va)
            external_texture_fields.append(dest_field)

    # Round-trip the opaque payloads byte-for-byte.  This specifically guards
    # against treating a palette/pixel word that resembles 0x0680.... as a
    # relocatable pointer.
    included_textures = []
    for item in texture_regions:
        if (_contains(blocks, item.descriptor_off) and
                _contains(blocks, item.end - 1)):
            included_textures.append(item)
            src = donor.data[item.pixels_off:item.end]
            dst = mapped_offset(item.pixels_off)
            if target.data[dst:dst + len(src)] != src:
                raise ValueError("texture payload changed while cloning")

    return CloneResult(
        offset_map, len(blocks), sum(end - start for start, end in blocks),
        len(target.data) - append_start, len(rewritten),
        len(included_textures),
        sum(item.end - item.pixels_off for item in included_textures),
        tuple(external_texture_fields),
        tuple(sorted({name for _off, name, _raw in external_texture_nodes})),
        fallback_texture_off)


def clone_shader_into_slot(target_source, donor_source, donor_index: int,
                           target_index: int) -> SlotCloneResult:
    """Clone one donor shader into an existing target group slot.

    Unlike :func:`transplant`, this keeps the target group's local-parameter
    list and every other slot byte-for-byte.  It is intended for player-car
    variants such as a dedicated headlight-lens ``colored_glass`` shader,
    where replacing the whole local list would invalidate the target model's
    serialized local-index table.  A shader that owns an inline materialized
    texture is rejected: copying its bytes is not enough, because the target
    package's texture-owner registry must also Place that object at runtime.
    """
    target = target_source if isinstance(target_source, Pck) else Pck(target_source)
    donor = donor_source if isinstance(donor_source, Pck) else Pck(donor_source)
    target_group = find_shader_group(target)
    donor_group = find_shader_group(donor)
    donor_index = int(donor_index)
    target_index = int(target_index)
    if not 0 <= donor_index < donor_group.size:
        raise ValueError("donor shader %d outside 0..%d" %
                         (donor_index, donor_group.size - 1))
    donor_entry = donor_group.entries[donor_index]
    if donor_entry is None:
        raise ValueError("donor shader %d is null or unrecognized" % donor_index)
    if not 0 <= target_index < target_group.size:
        raise ValueError("target shader slot %d outside 0..%d" %
                         (target_index, target_group.size - 1))

    target_locals = local_parameters(target, target_group)

    # Preflight on a private image because _clone_reachable appends while it
    # discovers the complete graph.  The first player-car test proved that an
    # unregistered inline TexturePS2 keeps its serialized 0x007A.... token in
    # RAM; a later virtual call then jumps into CD padding.  External named
    # references are fine, but materialized textures need owner registration
    # that this contained slot helper does not implement yet.
    probe_owner = types.SimpleNamespace(
        data=bytearray(target.data), path=target.path, body=target.body)
    probe = Pck(probe_owner)
    probe_clone = _clone_reachable(
        probe, donor, donor_group, (donor_index,))
    if probe_clone.texture_count:
        raise ValueError(
            "selected shader owns %d inline texture(s); slot-only cloning "
            "cannot register them with the target texture owner" %
            probe_clone.texture_count)

    clone = _clone_reachable(target, donor, donor_group, (donor_index,))
    new_resource = clone.offset_map[donor_entry.resource_off]
    struct.pack_into("<I", target.data,
                     target_group.array_off + 4 * target_index,
                     target.virtual(new_resource))
    target.finish_append()

    check = read_shader_group(target, target_group.group_off)
    entry = check.entries[target_index]
    if entry is None or entry.name != donor_entry.name:
        raise ValueError("cloned shader slot failed structural name check")
    if local_parameters(target, check) != target_locals:
        raise ValueError("cloning one shader changed the target local list")
    if clone.external_texture_fields:
        fallback_va = target.virtual(clone.fallback_texture_off)
        for field in clone.external_texture_fields:
            if target.u32(field) != fallback_va:
                raise ValueError(
                    "external texture fallback at %#x did not round-trip" % field)
    return SlotCloneResult(donor_index, target_index, donor_entry.name,
                           target_group.group_off, new_resource, clone)


def transplant(target_source, donor_source, group_pointer_field: int,
               donor_indices, preserve_indices=()) -> TransplantResult:
    """Append a per-vehicle group and return ``{donor_index: new_index}``.

    ``group_pointer_field`` is normally ``traffic_vehicle_root + 0x08``.
    Target entries listed in ``preserve_indices`` remain at their old indices;
    this keeps the generic traffic wheels and shadow intact.  Donor shaders are
    packed consecutively after the highest preserved index.
    """
    target = target_source if isinstance(target_source, Pck) else Pck(target_source)
    donor = donor_source if isinstance(donor_source, Pck) else Pck(donor_source)
    old_group_off = target.pointer(group_pointer_field)
    if old_group_off is None:
        raise ValueError("vehicle root has no shader group at +0x08")

    preserve = tuple(sorted(set(int(index) for index in preserve_indices)))
    minimum = max(preserve, default=-1) + 1
    old_group = read_shader_group(target, old_group_off, minimum)
    for index in preserve:
        if index >= len(old_group.entries) or old_group.entries[index] is None:
            raise ValueError("target shader %d cannot be preserved" % index)

    donor_group = find_shader_group(donor)
    selected = tuple(sorted(set(int(index) for index in donor_indices)))
    if not selected:
        raise ValueError("no donor shader indices requested")
    for index in selected:
        if index < 0 or index >= donor_group.size:
            raise ValueError("donor shader %d outside 0..%d" %
                             (index, donor_group.size - 1))
        if index >= len(donor_group.entries) or donor_group.entries[index] is None:
            raise ValueError("donor shader %d is null or unrecognized" % index)

    first_new = max(preserve, default=-1) + 1
    shader_map = {old: first_new + pos for pos, old in enumerate(selected)}
    clone = _clone_reachable(target, donor, donor_group, selected)

    while len(target.data) % 16:
        target.data += b"\xCD"
    new_group_off = len(target.data)
    shader_count = first_new + len(selected)
    new_array_off = new_group_off + 0x20
    local_va = (target.virtual(clone.offset_map[donor_group.local_head_off])
                if donor_group.local_head_off is not None else 0)
    group = bytearray(0x20 + 4 * shader_count)
    group[:] = b"\xCD" * len(group)
    struct.pack_into("<I", group, 0x00, target.u32(old_group_off))
    struct.pack_into("<I", group, 0x04, target.virtual(new_array_off))
    struct.pack_into("<HH", group, 0x08, shader_count, shader_count)
    struct.pack_into("<I", group, 0x0C, local_va)
    # datOwner's allocation header lives 0x10 bytes before its returned array.
    struct.pack_into("<I", group, 0x10, shader_count)

    for index in preserve:
        entry = old_group.entries[index]
        struct.pack_into("<I", group, 0x20 + 4 * index, entry.resource_va)
    shader_names = {}
    for old_index, new_index in shader_map.items():
        entry = donor_group.entries[old_index]
        new_resource = clone.offset_map[entry.resource_off]
        struct.pack_into("<I", group, 0x20 + 4 * new_index,
                         target.virtual(new_resource))
        shader_names[old_index] = entry.name

    target.data += group
    struct.pack_into("<I", target.data, group_pointer_field,
                     target.virtual(new_group_off))
    target.finish_append()

    check = read_shader_group(target, new_group_off, shader_count)
    if check.size != shader_count or check.capacity != shader_count:
        raise ValueError("new shader vector count did not round-trip")
    for old_index, new_index in shader_map.items():
        entry = check.entries[new_index]
        if entry is None or entry.name != shader_names[old_index]:
            raise ValueError("new shader %d failed structural name check" % new_index)
    if local_parameters(target, check) != local_parameters(donor, donor_group):
        raise ValueError("donor local-parameter list did not round-trip")
    if clone.external_texture_fields:
        fallback_va = target.virtual(clone.fallback_texture_off)
        for field in clone.external_texture_fields:
            if target.u32(field) != fallback_va:
                raise ValueError(
                    "external texture fallback at %#x did not round-trip" %
                    field)
    return TransplantResult(shader_map, shader_names, preserve, old_group_off,
                            new_group_off, new_array_off, shader_count, clone)


def _words(pck: Pck, center: int, before: int = 0x20, after: int = 0x30):
    lo = max(0, center - before) & ~3
    hi = min(len(pck.data), center + after) & ~3
    return [(off, pck.u32(off)) for off in range(lo, hi, 4)]


def inspect(path: str):
    pck = Pck(path)
    group = find_shader_group(pck)
    print("%s: VA=%#x size=%d" % (os.path.basename(path), pck.va, len(pck.data)))
    print("shader group @%#x; array @%#x VA=%#x size=%d capacity=%d" %
          (group.group_off, group.array_off, pck.virtual(group.array_off),
           group.size, group.capacity))
    print("local list: %s" %
          ("%#x" % group.local_head_off
           if group.local_head_off is not None else "none"))
    print("owner field(s): %s" %
          (", ".join("%#x" % value for value in group.owner_fields) or "none"))
    for owner in group.owner_fields:
        print("owner context @%#x:" % owner)
        for off, value in _words(pck, owner):
            marker = " -> array" if off == owner else ""
            print("  +%#06x  %08X%s" % (off, value, marker))
    for entry in group.entries:
        if entry is None:
            print("  --  null/unrecognized")
        else:
            print("  %2d  ptr@%#x -> %#x  %s" %
                  (entry.index, entry.pointer_off, entry.resource_off, entry.name))
    return group


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pck")
    args = parser.parse_args(argv)
    inspect(args.pck)


if __name__ == "__main__":
    main()
