#!/usr/bin/env python3
"""Recenter MC3 city component VIF positions without touching instances.

The source directory is expected to contain one ``*.mesh.pck`` per model.  The
component set and its exact centre are taken through mc3_city_build.py itself,
so model naming and XMOD parsing stay identical to the city builder.

Only the position stream is changed.  Absolute 8/16/32-bit streams have the
constant subtracted from every row; difference-mode streams keep every delta
and subtract it only from STROW.  Unknown, masked, fill-cycle, wrapped, or
out-of-range forms fail closed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import struct
import sys


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def s8(v: int) -> int:
    return v - 0x100 if v & 0x80 else v


def s16(v: int) -> int:
    return v - 0x10000 if v & 0x8000 else v


def s32(v: int) -> int:
    return v - 0x100000000 if v & 0x80000000 else v


def unpack_size(cmd: int, num: int) -> int:
    n = num or 256
    cols = ((cmd >> 2) & 3) + 1
    vl = cmd & 3
    size = n * 2 if vl == 3 else n * cols * (4 >> vl)
    return (size + 3) & ~3


def commands(data: bytes | bytearray):
    """Yield bounded VIF commands with state immediately before the command."""
    off = 0
    mode = 0
    cl = wl = 1
    row_off = None
    while off < len(data):
        if off + 4 > len(data):
            raise ValueError(f"partial command at {off:#x}")
        cmd_off = off
        word = struct.unpack_from("<I", data, off)[0]
        off += 4
        cmd = (word >> 24) & 0x7F
        num = (word >> 16) & 0xFF
        imm = word & 0xFFFF
        size = 0
        mode_before = mode
        row_before = row_off
        if cmd in (0, 2, 3, 4, 6, 7, 0x10, 0x11, 0x13,
                   0x14, 0x15, 0x17):
            pass
        elif cmd == 1:
            cl = (imm & 0xFF) or 256
            wl = ((imm >> 8) & 0xFF) or 256
            if wl > cl:
                raise ValueError(f"STCYCL fill unsupported at {cmd_off:#x}")
        elif cmd == 5:
            mode = imm & 3
        elif cmd == 0x20:
            size = 4
        elif cmd in (0x30, 0x31):
            size = 16
            if cmd == 0x30:
                row_off = off
        elif cmd == 0x4A:
            size = (num or 256) * 8
        elif cmd in (0x50, 0x51):
            size = (imm or 65536) * 16
        elif 0x60 <= cmd <= 0x7F:
            size = unpack_size(cmd, num)
        else:
            raise ValueError(f"unknown VIF command {cmd:#x} at {cmd_off:#x}")
        if off + size > len(data):
            raise ValueError(f"payload beyond packet at {cmd_off:#x}")
        yield {
            "off": cmd_off,
            "payload": off,
            "word": word,
            "cmd": cmd,
            "num": num,
            "imm": imm,
            "size": size,
            "mode": mode_before,
            "row_off": row_before,
            "cl": cl,
            "wl": wl,
        }
        off += size


def header_info(c, data: bytes | bytearray):
    if not (c["cmd"] == 0x60 and (c["num"] or 256) == 2
            and (c["imm"] & 0x4000)):
        return None
    scale = struct.unpack_from("<f", data, c["payload"])[0]
    count = struct.unpack_from("<I", data, c["payload"] + 4)[0]
    if not math.isfinite(scale) or not (0.0 < scale <= 1.0):
        return None
    if not (0 < count <= 255):
        return None
    return c["imm"] & 0x3FF, scale, count


def is_position(c, base: int, count: int) -> bool:
    if not (0x60 <= c["cmd"] <= 0x7F):
        return False
    base_cmd = c["cmd"] & ~0x10
    cols = ((base_cmd >> 2) & 3) + 1
    if cols != 3:
        return False
    start = (base + 0x62) & 0x3FF
    addr = c["imm"] & 0x3FF
    n = c["num"] or 256
    return start <= addr < start + count and addr + n <= start + count


def decode_positions(data: bytes | bytearray):
    out = []
    row = [0, 0, 0, 0]
    base = scale = count = None
    chain_rows = 0
    chains = 0
    for c in commands(data):
        header = header_info(c, data)
        if header is not None:
            if count is not None and chain_rows != count:
                raise ValueError(f"position count {chain_rows} != header {count}")
            base, scale, count = header
            chain_rows = 0
            chains += 1
            continue
        if c["cmd"] == 0x30:
            row = [s32(struct.unpack_from("<I", data, c["payload"] + 4 * k)[0])
                   for k in range(4)]
            continue
        if base is None:
            continue
        if not is_position(c, base, count):
            continue
        if c["cmd"] & 0x10:
            raise ValueError(f"masked position UNPACK unsupported at {c['off']:#x}")
        cmd = c["cmd"]
        n = c["num"] or 256
        p = c["payload"]
        vals = []
        if cmd == 0x68:
            for r in range(n):
                vals.append(tuple(s32(struct.unpack_from("<I", data, p + 12*r + 4*k)[0])
                                  for k in range(3)))
        elif cmd == 0x69:
            for r in range(n):
                vals.append(tuple(struct.unpack_from("<hhh", data, p + 6*r)))
        elif cmd == 0x6A:
            for r in range(n):
                raw = tuple(s8(data[p + 3*r + k]) for k in range(3))
                if c["mode"] == 2:
                    prev = row if r == 0 else vals[-1]
                    vals.append(tuple(prev[k] + raw[k] for k in range(3)))
                elif c["mode"] == 0:
                    vals.append(raw)
                else:
                    raise ValueError(f"unsupported STMOD {c['mode']} for positions")
        else:
            raise ValueError(f"unsupported position UNPACK {cmd:#x}")
        out.extend((scale, x) for x in vals)
        chain_rows += len(vals)
        if vals and c["mode"] == 2:
            row[:3] = vals[-1]
    if count is not None and chain_rows != count:
        raise ValueError(f"position count {chain_rows} != header {count}")
    if not chains:
        raise ValueError("short packet header not found")
    return out, chains


def recenter_packet(data: bytes, centre):
    result = bytearray(data)
    changed = set()
    forms = {}
    base = scale = count = shift = None
    rows_done = 0
    chains = 0
    scales = set()
    shifts = set()
    for c in list(commands(result)):
        header = header_info(c, result)
        if header is not None:
            if count is not None and rows_done != count:
                raise ValueError(f"patched position count {rows_done} != header {count}")
            base, scale, count = header
            shift = tuple(int(round(float(v) / scale)) for v in centre)
            rows_done = 0
            chains += 1
            scales.add(scale)
            shifts.add(shift)
            continue
        if base is None:
            continue
        if not is_position(c, base, count):
            continue
        if c["cmd"] & 0x10:
            raise ValueError(f"masked position UNPACK unsupported at {c['off']:#x}")
        cmd = c["cmd"]
        n = c["num"] or 256
        p = c["payload"]
        forms[f"{cmd:02X}_m{c['mode']}"] = forms.get(f"{cmd:02X}_m{c['mode']}", 0) + 1
        if cmd == 0x68 and c["mode"] == 0:
            for r in range(n):
                for k in range(3):
                    q = s32(struct.unpack_from("<I", result, p + 12*r + 4*k)[0])
                    struct.pack_into("<i", result, p + 12*r + 4*k, q - shift[k])
                    changed.update(range(p + 12*r + 4*k, p + 12*r + 4*k + 4))
        elif cmd == 0x69 and c["mode"] == 0:
            for r in range(n):
                vals = list(struct.unpack_from("<hhh", result, p + 6*r))
                vals = [vals[k] - shift[k] for k in range(3)]
                if any(v < -32768 or v > 32767 for v in vals):
                    raise ValueError(f"recentered s16 overflow: {vals}")
                struct.pack_into("<hhh", result, p + 6*r, *vals)
                changed.update(range(p + 6*r, p + 6*r + 6))
        elif cmd == 0x6A and c["mode"] == 0:
            for r in range(n):
                vals = [s8(result[p + 3*r + k]) - shift[k] for k in range(3)]
                if any(v < -128 or v > 127 for v in vals):
                    raise ValueError(f"recentered s8 overflow: {vals}")
                for k, v in enumerate(vals):
                    result[p + 3*r + k] = v & 0xFF
                    changed.add(p + 3*r + k)
        elif cmd == 0x6A and c["mode"] == 2:
            ro = c["row_off"]
            if ro is None:
                raise ValueError("difference position stream without STROW")
            if any(ro + j in changed for j in range(12)):
                raise ValueError("STROW reused by multiple translated unpacks")
            # ROW xyz must not feed another attribute before being replaced.
            for following in commands(data):
                if following['off'] <= c['off']:
                    continue
                if following['cmd'] == 0x30:
                    break
                if 0x60 <= following['cmd'] <= 0x7F:
                    if following['mode'] != 0 or following['cmd'] & 0x10:
                        raise ValueError('translated STROW may feed another attribute')
            for k in range(3):
                q = s32(struct.unpack_from("<I", result, ro + 4*k)[0])
                struct.pack_into("<i", result, ro + 4*k, q - shift[k])
                changed.update(range(ro + 4*k, ro + 4*k + 4))
        else:
            raise ValueError(f"unsupported position form cmd={cmd:#x} mode={c['mode']}")
        rows_done += n
    if count is not None and rows_done != count:
        raise ValueError(f"patched position count {rows_done} != header {count}")

    before, before_chains = decode_positions(data)
    after, after_chains = decode_positions(result)
    if before_chains != after_chains or len(before) != len(after):
        raise ValueError("position decode shape changed")
    for (bs, bp), (ats, ap) in zip(before, after):
        expected_shift = tuple(int(round(float(v) / bs)) for v in centre)
        expected = tuple(bp[k] - expected_shift[k] for k in range(3))
        if bs != ats or ap != expected:
            raise ValueError("position translation did not round-trip in quantized space")
    return bytes(result), shifts, scales, changed, forms, len(before), chains


def pck_packets(data: bytes | bytearray):
    if len(data) < 0xA0:
        raise ValueError("mesh.pck too short")
    base = struct.unpack_from("<I", data, 0)[0]
    body = struct.unpack_from("<I", data, 12)[0]
    if body != len(data) - 0x80:
        raise ValueError(f"body size {body} != file payload {len(data)-0x80}")
    to_off = lambda va: 0x80 + va - base
    groups = struct.unpack_from("<H", data, 0x88)[0]
    geo = to_off(struct.unpack_from("<I", data, 0x90)[0])
    if not (0x80 <= geo <= len(data) - 8 * groups):
        raise ValueError("geometry table outside file")
    for g in range(groups):
        list_va, count = struct.unpack_from("<IH", data, geo + 8*g)
        table = to_off(list_va)
        if not (0x80 <= table <= len(data) - 8 * count):
            raise ValueError(f"packet table outside file in group {g}")
        for k in range(count):
            va, qw, nv = struct.unpack_from("<IHH", data, table + 8*k)
            off = to_off(va)
            size = 16 * qw
            if not (0x80 <= off <= len(data) - size):
                raise ValueError(f"packet outside file in group {g}/{k}")
            if nv and qw:
                yield g, k, off, size, nv


def recenter_pck(blob: bytes, centre):
    out = bytearray(blob)
    packet_count = vertex_count = 0
    changed_file = set()
    forms = {}
    scales = set()
    shifts = set()
    for g, k, off, size, nv in list(pck_packets(blob)):
        payload = bytes(blob[off:off+size])
        patched, packet_shifts, packet_scales, changed, pf, rows, chains = recenter_packet(
            payload, centre)
        if rows != nv:
            raise ValueError(f"group {g} packet {k}: VIF rows {rows} != descriptor {nv}")
        out[off:off+size] = patched
        changed_file.update(off + x for x in changed)
        packet_count += 1
        vertex_count += rows
        shifts.update(packet_shifts)
        scales.update(packet_scales)
        for name, n in pf.items():
            forms[name] = forms.get(name, 0) + n
    if not packet_count:
        raise ValueError("no packets")
    assert all(a == b or i in changed_file for i, (a,b) in enumerate(zip(blob,out)))
    return bytes(out), {
        "packets": packet_count,
        "vertices": vertex_count,
        "changed_bytes": len(changed_file),
        "forms": forms,
        "scales": sorted(scales),
        "quantized_shifts": [list(x) for x in sorted(shifts)],
    }


def load_builder(path: Path):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("mc3_city_build_recenter_source", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def component_centres(builder, place: Path, models: Path):
    lines, _extents = builder.read_place(str(place))
    result = {}
    for line in lines:
        if line["kind"] != "comp":
            continue
        model = builder.mesh_path(str(models), line)
        if not model:
            raise ValueError(f"component model missing for {line}")
        blocks, centre = builder.read_world_component(model)
        if not blocks:
            raise ValueError(f"empty component model {model}")
        key = Path(model).stem.lower()
        if key in result and result[key] != tuple(centre):
            raise ValueError(f"component {key} has conflicting centres")
        result[key] = tuple(float(x) for x in centre)
    return result


def run(args):
    builder = load_builder(args.builder.resolve())
    centres = component_centres(builder, args.place.resolve(), args.models.resolve())
    source_files = sorted(args.source.resolve().glob("*.mesh.pck"))
    source_map = {p.name[:-len(".mesh.pck")].lower(): p for p in source_files}
    missing = sorted(set(centres) - set(source_map))
    if missing:
        raise ValueError(f"{len(missing)} component mesh.pck missing; first: {missing[:5]}")

    report = {
        "source": str(args.source.resolve()),
        "output": str(args.output.resolve()) if args.output else None,
        "place": str(args.place.resolve()),
        "models": str(args.models.resolve()),
        "builder": str(args.builder.resolve()),
        "source_files": len(source_files),
        "components": len(centres),
        "component_results": [],
    }
    totals = {"packets": 0, "vertices": 0, "changed_bytes": 0}
    form_totals = {}

    if args.output:
        args.output.mkdir(parents=True, exist_ok=False)

    for src in source_files:
        key = src.name[:-len(".mesh.pck")].lower()
        raw = src.read_bytes()
        if key in centres:
            try:
                patched, info = recenter_pck(raw, centres[key])
            except Exception as e:
                raise ValueError(f"{key} centre={centres[key]}: {e}") from e
            info.update({
                "name": key,
                "centre": list(centres[key]),
                "source_sha256": hashlib.sha256(raw).hexdigest(),
                "output_sha256": hashlib.sha256(patched).hexdigest(),
            })
            report["component_results"].append(info)
            for name in totals:
                totals[name] += info[name]
            for name, n in info["forms"].items():
                form_totals[name] = form_totals.get(name, 0) + n
            if args.output:
                (args.output / src.name).write_bytes(patched)
        elif args.output:
            shutil.copy2(src, args.output / src.name)

    report["totals"] = totals
    report["forms"] = form_totals
    report["unchanged_instance_files"] = len(source_files) - len(centres)
    report["all_component_files_changed"] = all(
        x["source_sha256"] != x["output_sha256"] for x in report["component_results"])
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({
        "source_files": len(source_files),
        "components": len(centres),
        "unchanged_instances": report["unchanged_instance_files"],
        "totals": totals,
        "forms": form_totals,
        "output": report["output"],
        "report": str(args.report.resolve()) if args.report else None,
    }, indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--builder", type=Path, required=True)
    ap.add_argument("--place", type=Path, required=True)
    ap.add_argument("--models", type=Path, required=True)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--output", type=Path,
                    help="omit for a read-only compatibility audit")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args(argv)
    run(args)


if __name__ == "__main__":
    main()
