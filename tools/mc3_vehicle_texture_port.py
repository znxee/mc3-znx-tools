"""Port an MC:LA PSP vehicle texture into an MC3 PS2 vehicle PCK.

The PSP vehicle container stores each indexed image as a compact object:

    +0x00  object header / serialized class residue
    +0x0C  object bytes (0x430 + width*height for the 8-bit square form)
    +0x20  256 RGBA palette entries (straight 0..255 alpha)
    +0x420 16-byte image header/tail
    +0x430 PSP-swizzled 8-bit indices

The PS2 target uses a different swizzle and 0..128 alpha.  This tool converts
those two details but keeps the native PSP dimensions.  Replacing a 256x256
PS2 atlas by the PSP 128x128 atlas does not relocate anything: the descriptor
and its owning rmcTexturePS2 header are changed to 128x128, the smaller payload
is written at the same address, and the unused tail is filled with 0xCD.  All
texture/shader pointers therefore stay valid.

Usage:
    python mc3_vehicle_texture_port.py list CAR.psppck
    python mc3_vehicle_texture_port.py extract CAR.psppck OUT_DIR
    python mc3_vehicle_texture_port.py inject CAR.psppck TARGET.pck OUT.pck
"""

from __future__ import annotations

import os
import argparse
import importlib.util
import math
import pathlib
import re
import struct
import sys
from dataclasses import dataclass


def _load_sibling(name: str):
    here = pathlib.Path(__file__).resolve().parent
    candidates = (
        here / f"{name}.py",
        pathlib.Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools')) / f"{name}.py",
    )
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        raise ImportError(f"cannot find {name}.py beside this tool or in the shared toolkit")
    spec = importlib.util.spec_from_file_location(f"_{name}_vehicle_port", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tex = _load_sibling("mc3_tex")


@dataclass(frozen=True)
class PspVehicleTexture:
    name: str
    descriptor: int
    width: int
    height: int
    palette: bytes
    indexes: bytes
    total_bytes: int

    def ps2_palette(self) -> bytearray:
        return tex.alpha_to_ps2(bytearray(self.palette))

    def image(self):
        return tex.Image(
            self.width,
            self.height,
            bytearray(self.indexes),
            self.ps2_palette(),
            name=self.name,
            addr=self.descriptor,
        )


_RESOURCE_RE = re.compile(rb"[A-Za-z_][A-Za-z0-9_.-]{2,63}\0")
_PSP_FIXED_BYTES = 0x430  # 0x20 header + 0x400 CLUT + 0x10 tail


def psp_unswizzle8(src: bytes, width: int, height: int) -> bytearray:
    """PSP row-major 16x8 byte tiles -> linear 8-bit indices."""
    if width % 16 or height % 8 or len(src) != width * height:
        raise ValueError("PSP 8-bit texture must use complete 16x8 tiles")
    dst = bytearray(width * height)
    blocks_x = width // 16
    for y in range(height):
        for x in range(width):
            block = (y // 8) * blocks_x + (x // 16)
            so = block * 128 + (y & 7) * 16 + (x & 15)
            dst[y * width + x] = src[so]
    return dst


def _name_before(data: bytes, descriptor: int) -> str:
    hits = list(_RESOURCE_RE.finditer(data[max(0, descriptor - 0x80):descriptor]))
    return hits[-1].group()[:-1].decode("ascii") if hits else f"texture_{descriptor:08x}"


def psp_vehicle_textures(source) -> list[PspVehicleTexture]:
    """Find the compact square 8-bit textures in an MC:LA PSP vehicle PCK."""
    data = source if isinstance(source, (bytes, bytearray)) else pathlib.Path(source).read_bytes()
    out = []
    for off in range(0x80, len(data) - _PSP_FIXED_BYTES, 4):
        if data[off:off + 4] != b"\0\0\0\0":
            continue
        if data[off + 0x10:off + 0x14] != b"\xCD" * 4:
            continue
        if data[off + 0x14:off + 0x18] != b"\0\0\xFF\xFF":
            continue
        if data[off + 0x18:off + 0x1C] != b"\0" * 4:
            continue
        if data[off + 0x1C:off + 0x20] != b"\xCD" * 4:
            continue
        total = struct.unpack_from("<I", data, off + 0x0C)[0]
        area = total - _PSP_FIXED_BYTES
        side = math.isqrt(area) if area > 0 else 0
        if side * side != area or side not in (16, 32, 64, 128, 256, 512):
            continue
        pixel_start = off + _PSP_FIXED_BYTES
        end = pixel_start + area
        if end > len(data):
            continue
        palette = bytes(data[off + 0x20:off + 0x420])
        # A real 256-entry palette has varied alpha and more than padding/one colour.
        if len(set(palette)) < 16 or len(set(palette[3::4])) < 2:
            continue
        raw = bytes(data[pixel_start:end])
        out.append(PspVehicleTexture(
            _name_before(data, off), off, side, side, palette,
            bytes(psp_unswizzle8(raw, side, side)), total,
        ))
    return out


def _ps2_candidates(data: bytes, base_va: int):
    out = []
    for name, pixels, dims, descriptor in tex.textures(data, base_va):
        if not dims or dims == (8, 8):
            continue
        out.append({
            "name": name or f"texture_{descriptor:08x}",
            "pixels": pixels,
            "width": dims[0],
            "height": dims[1],
            "descriptor": descriptor,
        })
    return out


def _select(rows, wanted: str | None, label: str):
    if wanted:
        hits = [r for r in rows if r.name.lower() == wanted.lower()] if rows and hasattr(rows[0], "name") else [
            r for r in rows if r["name"].lower() == wanted.lower()
        ]
        if len(hits) != 1:
            raise ValueError(f"{label} {wanted!r}: expected one match, got {len(hits)}")
        return hits[0]
    if not rows:
        raise ValueError(f"no {label} texture found")
    if hasattr(rows[0], "width"):
        return max(rows, key=lambda r: r.width * r.height)
    return max(rows, key=lambda r: r["width"] * r["height"])


def _virtual_to_offset(data: bytes, base_va: int, va: int) -> int | None:
    if not va:
        return None
    off = va - base_va + 0x80
    if not (0x80 <= off <= len(data) - 4):
        return None
    return off


def _texture_owner(data: bytes, base_va: int, mip_buffer: int):
    """Return ``(rmcTexturePS2 offset, mip level)`` for one MipBuffer.

    MipBuffer+0x08 points back to the owning MipData entry.  A TexturePS2 owns
    four 12-byte MipData records at +0x48, so trying the four possible levels
    recovers the texture without scanning for a nearby descriptor or name.
    """
    if not (0x80 <= mip_buffer <= len(data) - 0x10):
        return None
    owner_va = struct.unpack_from("<I", data, mip_buffer + 0x08)[0]
    owner = _virtual_to_offset(data, base_va, owner_va)
    if owner is None:
        return None
    expected_buffer_va = base_va + mip_buffer - 0x80
    for level in range(4):
        texture = owner - (0x48 + 12 * level)
        if not (0x80 <= texture <= len(data) - 0xA0):
            continue
        if data[texture + 0x04] != 0:       # type 0 = rmcTexturePS2
            continue
        if struct.unpack_from("<I", data, owner)[0] != expected_buffer_va:
            continue
        return texture, level
    return None


def _size_class_for_blocks(blocks: int) -> int:
    """Encode the measured rmcTexturePS2 size class for a power-of-two block count."""
    if blocks <= 0 or blocks & (blocks - 1):
        raise ValueError(f"texture uses unsupported non-power-of-two block count {blocks}")
    exponent = blocks.bit_length() - 1
    return 99 + 33 * (exponent // 2) + (exponent & 1)


def _patch_texture_header(data: bytearray, base_va: int, mip_buffer: int,
                          width: int, height: int):
    """Make the serialized rmcTexturePS2 agree with a replaced PSMT8 payload."""
    owner = _texture_owner(data, base_va, mip_buffer)
    if owner is None:
        raise ValueError("target texture MipBuffer has no valid rmcTexturePS2 owner")
    texture, level = owner
    if level != 0:
        raise ValueError("native atlas replacement must target mip level zero")
    if data[texture + 0x86] != 1:
        raise ValueError("native PSP atlas replacement currently requires a one-mip target")
    if (width <= 0 or height <= 0 or width & (width - 1) or
            height & (height - 1)):
        raise ValueError("PS2 TEX0 dimensions must be powers of two")

    blocks = width * height // 256
    if blocks * 256 != width * height:
        raise ValueError("PSMT8 texture size must be an exact number of GS blocks")
    size_class = _size_class_for_blocks(blocks)
    clut_blocks = 4                       # 256 RGBA entries = 0x400 bytes

    mip = texture + 0x08
    old_tex0 = struct.unpack_from("<Q", data, mip + 0x08)[0]
    psm = (old_tex0 >> 20) & 0x1F
    if psm != 0x13:
        raise ValueError(f"target TexturePS2 is PSM {psm:#x}, expected PSMT8")
    tw = width.bit_length() - 1
    th = height.bit_length() - 1
    tbw = max(1, (width + 63) // 64)
    tex0_mask = (0x3F << 14) | (0x0F << 26) | (0x0F << 30)
    new_tex0 = ((old_tex0 & ~tex0_mask) | (tbw << 14) |
                (tw << 26) | (th << 30))

    struct.pack_into("<HHHHQ", data, mip, size_class, 0, blocks,
                     blocks + clut_blocks, new_tex0)
    # This serialized field follows area/16 for the one-mip indexed vehicle
    # atlases (256x256 -> 4096; retail 128x128 -> 1024).
    struct.pack_into("<H", data, texture + 0x80, width * height // 16)

    check_tex0 = struct.unpack_from("<Q", data, texture + 0x10)[0]
    check_w = 1 << ((check_tex0 >> 26) & 0x0F)
    check_h = 1 << ((check_tex0 >> 30) & 0x0F)
    if (check_w, check_h) != (width, height):
        raise AssertionError("rmcTexturePS2 TEX0 dimensions failed to round-trip")
    return {
        "texture_header": texture,
        "mip_level": level,
        "old_tex0": old_tex0,
        "new_tex0": new_tex0,
        "size_class": size_class,
        "blocks": blocks,
        "blocks_with_clut": blocks + clut_blocks,
        "tbw": tbw,
    }


def port_native_texture(target, source, *, source_name: str | None = None,
                        target_name: str | None = None):
    """Replace the largest named PS2 vehicle atlas with the native PSP atlas.

    ``target`` may be a Pck-like object with ``data`` and ``va`` or a mutable
    bytearray.  No bytes are inserted or removed.  Returns a report dict.
    """
    src_rows = psp_vehicle_textures(source)
    src = _select(src_rows, source_name, "PSP")
    data = target.data if hasattr(target, "data") else target
    if not isinstance(data, bytearray):
        raise TypeError("target data must be mutable")
    base_va = target.va if hasattr(target, "va") else struct.unpack_from("<I", data, 0)[0]
    dst_rows = _ps2_candidates(data, base_va)
    dst = _select(dst_rows, target_name, "PS2")

    old_fmt = bytes(data[dst["descriptor"] + 0x0C:dst["descriptor"] + 0x10])
    old_span = tex.descriptor_bytes(old_fmt)
    new_span = src.width * src.height + 0x400
    if old_span is None or new_span > old_span:
        raise ValueError(f"native {src.width}x{src.height} payload does not fit target")

    image = src.image()
    end = tex.write_block(data, dst["pixels"], [image], image.palette,
                          colors=256, swizzled=True)
    if end - dst["pixels"] != new_span:
        raise AssertionError("PS2 writer produced an unexpected texture span")
    data[end:dst["pixels"] + old_span] = b"\xCD" * (old_span - new_span)
    struct.pack_into("<I", data, dst["descriptor"] + 0x0C, new_span + 0x100)
    header = _patch_texture_header(data, base_va, dst["descriptor"],
                                   src.width, src.height)

    decoded = tex.decode_texture(data, dst["pixels"], src.width, src.height)
    if bytes(decoded.indexes) != src.indexes or bytes(decoded.palette) != bytes(image.palette):
        raise AssertionError("native texture failed the PS2 swizzle round trip")
    return {
        "source_name": src.name,
        "source_descriptor": src.descriptor,
        "target_name": dst["name"],
        "target_descriptor": dst["descriptor"],
        "old_dimensions": (dst["width"], dst["height"]),
        "new_dimensions": (src.width, src.height),
        "old_payload": old_span,
        "new_payload": new_span,
        "padding_reclaimed": old_span - new_span,
        **header,
    }


def _main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("psppck", type=pathlib.Path)
    ex = sub.add_parser("extract")
    ex.add_argument("psppck", type=pathlib.Path)
    ex.add_argument("outdir", type=pathlib.Path)
    inj = sub.add_parser("inject")
    inj.add_argument("psppck", type=pathlib.Path)
    inj.add_argument("target", type=pathlib.Path)
    inj.add_argument("out", type=pathlib.Path)
    inj.add_argument("--source-name")
    inj.add_argument("--target-name")
    ns = ap.parse_args(argv)

    rows = psp_vehicle_textures(ns.psppck)
    if ns.cmd == "list":
        for row in rows:
            print(f"{row.name:36} {row.width}x{row.height} desc=0x{row.descriptor:X} bytes=0x{row.total_bytes:X}")
        return
    if ns.cmd == "extract":
        ns.outdir.mkdir(parents=True, exist_ok=True)
        for row in rows:
            path = ns.outdir / f"{row.name}_{row.width}x{row.height}.png"
            row.image().save_png(str(path))
            print(path)
        return

    from mc3_pck_embed import Pck
    target = Pck(ns.target)
    report = port_native_texture(target, ns.psppck,
                                 source_name=ns.source_name,
                                 target_name=ns.target_name)
    target.save(ns.out)
    print(report)
    print(ns.out)


if __name__ == "__main__":
    _main()
