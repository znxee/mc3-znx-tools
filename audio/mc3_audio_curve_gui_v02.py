#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MC3 Vehicle Audio Curve GUI v0.20

PCK-first prototype.

Main changes from v0.1:
- loads .pck directly through File > Open PCK;
- no PatternData JSON is required from the user;
- parses the audio-root structure internally using the current audio pattern layout;
- shows Engine / Exhaust / Auxiliary audio blocks, RPM ranges and curves;
- groups auxiliary sounds under one tree submenu;
- adds safer auxiliary role/editability notes for future transfer work;
- can export the parsed data as JSON for debugging/research, but JSON is not an input requirement.

This version adds staged editing for known safe fields:
- AudioBlockFull bank_name, min_rpm_rec, max_rpm_rec;
- Raw auxiliary fixed string slots where mapped;
- curve-row copy/paste for active visible curves when row counts match;
- Save overwrites the currently open PCK; Save As writes a separate/current copy.
- Adds two donor import modes: Conservative and Experimental. Conservative mirrors the previous transfer tool; Experimental copies the maximum currently safe-to-address data without copying donor pointers.
- Adds curve point editing through the Curve Rows table and optional click-drag handles directly on the graph.
- Adds 10-step Undo/Redo history for in-memory edits/imports.
- Adds raw auxiliary float candidate editing table for experimental runtime tests.
- Changes donor import pairing to logical sample role first (ENGINE/TAIL), instead of blindly using slot number/root offset for main slots.
- Experimental import now copies High Pitch only when the receiving slot already has an active High Pitch curve.
- Paste active curve points now respects the same High Pitch target-active guard.
- Range Min/Mid/Max RPM fields are directly editable and displayed in Min/Mid/Max order.
- Save/Save As no longer show success popups.
- Graph editing now shows selected-point handles instead of drawing all control handles at once.
- Adds curve point insert/delete from the graph and moves selected handles with selected knots.
- Bank field edits now also update range sample-name prefixes, and Save commits pending top-field edits before writing.
- v0.20 updates audio_root names from .vehaudio research: Engine/Exhaust/TurboBlower/Common Levels, Impact/BigAir/Horn/Powerup/Fire/Wheel/Suspension/Gearshift/Backfire.
- v0.20 parses EngineRevSound at AudioBlockFull+0x68 and treats curve+0x14 as ActiveSeriesCount while preserving point_count aliases for GUI compatibility.
"""

from __future__ import annotations

import json
import math
import os
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except Exception as exc:
    raise SystemExit(f"tkinter is required to run this GUI: {exc}")


# -----------------------------------------------------------------------------
# Constants based on the current PatternEditor_Audio.hexpat / audio KB
# -----------------------------------------------------------------------------

AUDIO_ROOT_MAGICS = {
    0x007A1A28: "350Z / SRT4 style AudioRoot",
    0x007A2D30: "DeVille style AudioRoot",
}

AUDIO_BLOCK_MAGICS = {
    0x007A1DF0: "350Z / SRT4 style AudioBlockFull / TurboBlower",
    0x007A30F8: "DeVille style AudioBlockFull",
}

PROFILE_MAGICS = {
    0x007A1A80: "350Z / SRT4 style Common",
    0x007A2D88: "DeVille style Common",
}

CURVE_MAGIC = 0x0079E730
RANGE_ENTRY_SIZE = 0x2C
RANGE_ALLOCATED_COUNT = 8
CURVE_POINT_FLOATS = 9
CURVE_POINT_SIZE = CURVE_POINT_FLOATS * 4
CURVE_HEADER_SIZE = 0x20
# Curve objects are laid out as fixed-size 0x140-byte records in all tested car PCKs.
# The visible point_count may be lower than the allocated row capacity.
CURVE_STORAGE_SIZE = 0x140
CURVE_STORAGE_CAPACITY = (CURVE_STORAGE_SIZE - CURVE_HEADER_SIZE) // CURVE_POINT_SIZE  # 8 CurveSeries rows
AUDIO_BLOCK_FULL_PACKAGE_SIZE = 0x840
AUDIO_BLOCK_HEADER_SIZE = 0x0A0
RANGE_TABLE_REL_FALLBACK = 0x0A0
ENGINE_REV_SOUND_OFF = 0x68
ENGINE_REV_SOUND_SIZE = 0x20
FIXED_PACKAGE_CURVE_RELS = {
    "volume_curve": 0x200,
    "pitch_curve": 0x340,
    "high_pitch_curve": 0x480,
    "upshift_curve": 0x5C0,
    "downshift_curve": 0x700,
}

CURVE_FIELDS = [
    ("volume_curve", "Volume", "ptr_volume_curve", 0x88),
    ("pitch_curve", "Pitch", "ptr_pitch_curve", 0x8C),
    ("high_pitch_curve", "High Pitch", "ptr_high_pitch_curve", 0x90),
    ("upshift_curve", "Upshift", "ptr_upshift_curve", 0x94),
    ("downshift_curve", "Downshift", "ptr_downshift_curve", 0x98),
]

CURVE_COLORS = {
    "volume_curve": "#7EC8FF",
    "pitch_curve": "#9DFF8F",
    "high_pitch_curve": "#E6C85C",
    "upshift_curve": "#D596FF",
    "downshift_curve": "#FF9C7E",
}

# Minimum virtual canvas size. When the window is smaller than this, the graph
# keeps its readable dimensions and uses scrollbars instead of collapsing.
CURVE_CANVAS_MIN_WIDTH = 1040
CURVE_CANVAS_MIN_HEIGHT = 360

AUX_CATEGORY_INFO = {
    "Auxiliary / TurboBlower": {
        "role": "TurboBlower Level0..2. These are AudioBlockFull layers; Level0 is often intentionally empty, Level1/2 commonly contain Turbo_Culture1/2.",
        "current_editability": "Same technical structure as Engine/Exhaust AudioBlockFull: bank_name, EngineRevSound, ranges, RPMs, distances, warble/boost and curves.",
        "safe_transfer_now": "Treat as AudioBlockFull. Copy field-by-field only; never copy donor pointers.",
    },
    "Auxiliary / Impact": {
        "role": "Impact Level0. Collision/impact/scrape-style auxiliary block.",
        "current_editability": "Readable strings and float candidates only. Meaning of floats still needs runtime mapping.",
        "safe_transfer_now": "String/float experiments only; do not copy raw blocks.",
    },
    "Auxiliary / BigAir": {
        "role": "BigAir Level0. Likely air-state controller/modulator, not a conventional sample player.",
        "current_editability": "Raw floats only; no confirmed bank/sample strings in the 350Z-family block.",
        "safe_transfer_now": "Do not transfer blindly; this is a candidate for jump/air-volume tests.",
    },
    "Auxiliary / Wind": {
        "role": "Wind Level0. Wind-noise / speed-noise controller candidate.",
        "current_editability": "Readable strings and speed/level-like float candidates.",
        "safe_transfer_now": "Copy strings only unless isolating wind response.",
    },
    "Auxiliary / Horn": {
        "role": "Horn Level0. Separate root slot exposed by .vehaudio.",
        "current_editability": "Usually null/empty in tested car audio roots. Raw inspection only when present.",
        "safe_transfer_now": "Do not transfer automatically unless proven active.",
    },
    "Auxiliary / Powerup": {
        "role": "Powerup Level0. Previously unknown root+0x28; now mapped as Powerup.",
        "current_editability": "Raw strings/floats only. Semantic meaning still open.",
        "safe_transfer_now": "Do not transfer automatically unless testing specific powerup/audio behavior.",
    },
    "Auxiliary / Fire": {
        "role": "Fire Level0. Separate from Backfire; likely envelope/controller for fire-related effect.",
        "current_editability": "Often curve/controller-like data. Treat as experimental raw float/curve data.",
        "safe_transfer_now": "Do not conflate with Backfire; avoid blind copy.",
    },
    "Auxiliary / Wheel": {
        "role": "Wheel Level0..2. Tire/skid/chirp/burnout/surface-related audio block formerly called Wheel.",
        "current_editability": "Readable strings and float candidates; likely controls skid/chirp/burnout thresholds and levels.",
        "safe_transfer_now": "Copy strings only unless isolating wheel/skid behavior.",
    },
    "Auxiliary / Suspension": {
        "role": "Suspension Level0..2. Suspension/hydraulic/airbag/rise/drop event audio candidates.",
        "current_editability": "Readable strings and float candidates. One candidate for jump/landing old-car sound leakage.",
        "safe_transfer_now": "Copy strings only unless isolating suspension/jump behavior.",
    },
    "Auxiliary / Gearshift": {
        "role": "Gearshift Level0..2. Discrete gear-change samples, separate from UpShiftCurve/DownShiftCurve modulation inside AudioBlockFull.",
        "current_editability": "Readable gearshift strings and event-envelope-like floats.",
        "safe_transfer_now": "Copy compatible strings first; float copy remains experimental.",
    },
    "Auxiliary / Backfire": {
        "role": "Backfire Level0..2. Exhaust-pop/backfire sample bundle. Separate from Fire.",
        "current_editability": "Readable Backfires:BACKFIRE strings and chance/timing/level-like floats.",
        "safe_transfer_now": "Copy compatible strings first; float copy remains experimental.",
    },
    "Auxiliary / Unknown": {
        "role": "Unclassified root-relative audio/controller blocks or null placeholders.",
        "current_editability": "Inspection only.",
        "safe_transfer_now": "Do not transfer automatically.",
    },
}


def category_display_name(category: str) -> str:
    if category.startswith("Auxiliary / "):
        return category.split("/", 1)[1].strip()
    return category


def aux_category_info(category: str) -> Dict[str, str]:
    return AUX_CATEGORY_INFO.get(category, {
        "role": "No mapped role yet.",
        "current_editability": "Inspection only.",
        "safe_transfer_now": "Do not transfer automatically.",
    })

ROOT_POINTERS = [
    ("ptr_impact_18", "Impact Level0", 0x18, "raw", "Auxiliary / Impact"),
    ("ptr_bigair_1C", "BigAir Level0", 0x1C, "raw", "Auxiliary / BigAir"),
    ("ptr_wind_20", "Wind Level0", 0x20, "raw", "Auxiliary / Wind"),
    ("ptr_horn_24", "Horn Level0", 0x24, "raw", "Auxiliary / Horn"),
    ("ptr_powerup_28", "Powerup Level0", 0x28, "raw", "Auxiliary / Powerup"),
    ("ptr_fire_2C", "Fire Level0", 0x2C, "raw", "Auxiliary / Fire"),

    ("ptr_wheel_0_30", "Wheel Level0", 0x30, "raw", "Auxiliary / Wheel"),
    ("ptr_wheel_1_34", "Wheel Level1", 0x34, "raw", "Auxiliary / Wheel"),
    ("ptr_wheel_2_38", "Wheel Level2", 0x38, "raw", "Auxiliary / Wheel"),

    ("ptr_suspension_0_3C", "Suspension Level0", 0x3C, "raw", "Auxiliary / Suspension"),
    ("ptr_suspension_1_40", "Suspension Level1", 0x40, "raw", "Auxiliary / Suspension"),
    ("ptr_suspension_2_44", "Suspension Level2", 0x44, "raw", "Auxiliary / Suspension"),

    ("ptr_gearshift_0_48", "Gearshift Level0", 0x48, "raw_bundle", "Auxiliary / Gearshift"),
    ("ptr_gearshift_1_4C", "Gearshift Level1", 0x4C, "raw_bundle", "Auxiliary / Gearshift"),
    ("ptr_gearshift_2_50", "Gearshift Level2", 0x50, "raw_bundle", "Auxiliary / Gearshift"),

    ("ptr_backfire_0_54", "Backfire Level0", 0x54, "raw_bundle", "Auxiliary / Backfire"),
    ("ptr_backfire_1_58", "Backfire Level1", 0x58, "raw_bundle", "Auxiliary / Backfire"),
    ("ptr_backfire_2_5C", "Backfire Level2", 0x5C, "raw_bundle", "Auxiliary / Backfire"),

    ("ptr_turboblower_0_60", "TurboBlower Level0", 0x60, "block", "Auxiliary / TurboBlower"),
    ("ptr_turboblower_1_64", "TurboBlower Level1", 0x64, "block", "Auxiliary / TurboBlower"),
    ("ptr_turboblower_2_68", "TurboBlower Level2", 0x68, "block", "Auxiliary / TurboBlower"),

    ("ptr_engine_0_6C", "Engine Level0", 0x6C, "block", "Engine"),
    ("ptr_engine_1_70", "Engine Level1", 0x70, "block", "Engine"),
    ("ptr_engine_2_74", "Engine Level2", 0x74, "block", "Engine"),
    ("ptr_engine_3_78", "Engine Level3", 0x78, "block", "Engine"),
    ("ptr_engine_4_7C", "Engine Level4", 0x7C, "block", "Engine"),

    ("ptr_exhaust_0_80", "Exhaust Level0", 0x80, "block", "Exhaust"),
    ("ptr_exhaust_1_84", "Exhaust Level1", 0x84, "block", "Exhaust"),
    ("ptr_exhaust_2_88", "Exhaust Level2", 0x88, "block", "Exhaust"),
    ("ptr_exhaust_3_8C", "Exhaust Level3", 0x8C, "block", "Exhaust"),
    ("ptr_exhaust_4_90", "Exhaust Level4", 0x90, "block", "Exhaust"),

    ("ptr_common_0_94", "Common Level0", 0x94, "common", "Common"),
    ("ptr_common_1_98", "Common Level1", 0x98, "common", "Common"),
    ("ptr_common_2_9C", "Common Level2", 0x9C, "common", "Common"),
    ("ptr_common_3_A0", "Common Level3", 0xA0, "common", "Common"),
    ("ptr_common_4_A4", "Common Level4", 0xA4, "common", "Common"),
]



# -----------------------------------------------------------------------------
# Binary helpers
# -----------------------------------------------------------------------------

def u32_le(buf: bytes, off: int) -> int:
    if off < 0 or off + 4 > len(buf):
        raise ValueError(f"u32 out of range at 0x{off:X}")
    return struct.unpack_from("<I", buf, off)[0]


def f32_le(buf: bytes, off: int) -> float:
    if off < 0 or off + 4 > len(buf):
        raise ValueError(f"f32 out of range at 0x{off:X}")
    return struct.unpack_from("<f", buf, off)[0]


def safe_u32(buf: bytes, off: int, default: int = 0) -> int:
    try:
        return u32_le(buf, off)
    except Exception:
        return default


def safe_f32(buf: bytes, off: int, default: float = 0.0) -> float:
    try:
        value = f32_le(buf, off)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def va_to_off(va: int, base_va: int) -> int:
    return int(va) - int(base_va)


def off_to_va(off: int, base_va: int) -> int:
    return int(off) + int(base_va)


def is_nullish_u32(value: int) -> bool:
    return value in (0, 0xCDCDCDCD) or (value & 0xFFFF0000) == 0xCDCD0000


def read_fixed_string(buf: bytes, off: int, size: int = 0x20) -> str:
    if off < 0 or off >= len(buf):
        return ""
    out = bytearray()
    end = min(len(buf), off + size)
    for i in range(off, end):
        b = buf[i]
        if b in (0x00, 0xCD):
            break
        out.append(b)
    return out.decode("ascii", errors="replace")




def encode_fixed_string(value: str, size: int = 0x20) -> bytes:
    """Encode MC3 fixed ASCII fields: string, 00 terminator, CD padding."""
    text = (value or "").encode("ascii", errors="replace")[:max(0, size - 1)]
    return text + b"\x00" + (b"\xCD" * max(0, size - len(text) - 1))


def write_fixed_string(buf: bytearray, off: int, value: str, size: int = 0x20):
    if off < 0 or off + size > len(buf):
        raise ValueError(f"fixed string write out of range at 0x{off:X}")
    buf[off:off + size] = encode_fixed_string(value, size)


def write_f32(buf: bytearray, off: int, value: float):
    if off < 0 or off + 4 > len(buf):
        raise ValueError(f"f32 write out of range at 0x{off:X}")
    struct.pack_into("<f", buf, off, float(value))


def write_u32(buf: bytearray, off: int, value: int):
    if off < 0 or off + 4 > len(buf):
        raise ValueError(f"u32 write out of range at 0x{off:X}")
    struct.pack_into("<I", buf, off, int(value) & 0xFFFFFFFF)


def replace_sample_prefix(old_sample: str, donor_bank: str) -> str:
    """Copy only the bank prefix before ':' while preserving the receiver suffix.

    This mirrors the safer transfer behavior from the previous CLI tool:
      receiver E_Old:LOW_ENGINE + donor E_New -> E_New:LOW_ENGINE
    Unprefixed receiver samples are left untouched.
    """
    old_sample = old_sample or ""
    donor_bank = (donor_bank or "").strip()
    if not donor_bank or ":" not in old_sample:
        return old_sample
    _old_prefix, suffix = old_sample.split(":", 1)
    if not suffix:
        return old_sample
    return f"{donor_bank}:{suffix}"


def is_probable_fixed_audio_string(buf: bytes | bytearray, off: int, field_size: int = 0x20) -> bool:
    """Conservative detector for fixed 0x20-byte audio string fields in raw aux blocks."""
    if off < 0 or off + 1 >= len(buf):
        return False
    # Avoid detecting the middle of a larger ASCII string as a new field.
    if off > 0 and 0x20 <= buf[off - 1] <= 0x7E:
        return False
    text = read_fixed_string(buf, off, field_size)
    if len(text) < 4:
        return False
    end = off + len(text)
    if end >= len(buf) or buf[end] not in (0x00, 0xCD):
        return False
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_:-./")
    if any(ch not in allowed for ch in text):
        return False
    if text.lower().startswith("vp_"):
        return False
    if ":" in text:
        left, right = text.split(":", 1)
        return bool(left and right and all(ch.isalnum() or ch == "_" for ch in left))
    return text[0].isalpha() and all(ch.isalnum() or ch == "_" for ch in text)


AUX_MAGIC_SCAN_SIZES = {
    0x007ADED0: 0x0A0,
    0x007B08B8: 0x0A0,
    0x007A2440: 0x080,
    0x007B0868: 0x0C0,
    0x0079E730: 0x140,
    0x007B0940: 0x1F0,
    0x007A2408: 0x0B0,
    0x007A1E98: 0x420,
    0x007A2498: 0xA40,
}


def aux_scan_size_for_magic(magic: int, fallback: int = 0x300) -> int:
    return AUX_MAGIC_SCAN_SIZES.get(int(magic) & 0xFFFFFFFF, fallback)


def iter_fixed_audio_strings(buf: bytes | bytearray, start_off: Optional[int], max_size: int) -> List[Tuple[int, str]]:
    """Return (relative_offset, text) for aligned fixed audio strings in a raw aux block."""
    if start_off is None or start_off < 0 or start_off >= len(buf):
        return []
    end = min(len(buf), start_off + max_size)
    out: List[Tuple[int, str]] = []
    seen: set[int] = set()
    for off in range(start_off, max(start_off, end - 3), 4):
        if not is_probable_fixed_audio_string(buf, off, 0x20):
            continue
        rel = off - start_off
        if rel in seen:
            continue
        seen.add(rel)
        out.append((rel, read_fixed_string(buf, off, 0x20)))
    return out

def read_loose_ascii_strings(buf: bytes, off: int, size: int = 0x100) -> List[str]:
    """Extract readable null/CD-terminated ASCII names from a raw preview window."""
    if off < 0 or off >= len(buf):
        return []
    data = buf[off:min(len(buf), off + size)]
    strings: List[str] = []
    cur = bytearray()

    def flush():
        nonlocal cur
        if len(cur) >= 3:
            try:
                s = cur.decode("ascii", errors="replace")
            except Exception:
                s = ""
            if s and any(c.isalpha() for c in s) and s not in strings:
                strings.append(s)
        cur = bytearray()

    for b in data:
        if 32 <= b <= 126:
            cur.append(b)
            if len(cur) > 64:
                flush()
        else:
            flush()
    flush()
    return strings


def read_loose_ascii_string_entries(buf: bytes, off: int, size: int = 0x100) -> List[Dict[str, Any]]:
    """Extract readable ASCII runs with absolute file offsets for aux diagnostics."""
    if off < 0 or off >= len(buf):
        return []
    end = min(len(buf), off + size)
    entries: List[Dict[str, Any]] = []
    seen = set()
    cur = bytearray()
    cur_start: Optional[int] = None

    def flush():
        nonlocal cur, cur_start
        if cur_start is not None and len(cur) >= 3:
            try:
                s = cur.decode("ascii", errors="replace")
            except Exception:
                s = ""
            key = (cur_start, s)
            if s and any(c.isalpha() for c in s) and key not in seen:
                seen.add(key)
                entries.append({"file_off": cur_start, "rel_off": cur_start - off, "text": s})
        cur = bytearray()
        cur_start = None

    for i in range(off, end):
        b = buf[i]
        if 32 <= b <= 126:
            if cur_start is None:
                cur_start = i
            cur.append(b)
            if len(cur) > 96:
                flush()
        else:
            flush()
    flush()
    return entries


def raw_float_preview(buf: bytes, off: int, size: int = 0x100) -> List[Dict[str, Any]]:
    """Coarse float scan for research only. This does not name fields."""
    if off < 0 or off >= len(buf):
        return []
    end = min(len(buf), off + size)
    out: List[Dict[str, Any]] = []
    for pos in range(off + 4, end - 3, 4):
        raw = safe_u32(buf, pos)
        val = safe_f32(buf, pos)
        # Skip obvious padding, pointers and absurd values. Keep plausible scalar floats.
        if raw in (0, 0xCDCDCDCD):
            continue
        if raw >= 0x01000000 and raw <= 0x0FFFFFFF:
            continue
        if math.isfinite(val) and -100000.0 <= val <= 100000.0:
            out.append({"file_off": pos, "rel_off": pos - off, "float": val, "u32": raw})
        if len(out) >= 48:
            break
    return out


def format_hex(value: Optional[int], width: int = 8) -> str:
    if value is None:
        return "-"
    return f"0x{int(value) & ((1 << (width * 4)) - 1):0{width}X}"


def sample_suffix(sample_name: str) -> str:
    return sample_name.split(":", 1)[1] if ":" in sample_name else sample_name


def likely_sample_role(ranges: List["AudioRange"], active_count: int) -> str:
    active = ranges[:max(0, min(active_count, len(ranges)))]
    suffixes = [sample_suffix(r.sample_name).upper() for r in active]
    engine_count = sum(1 for s in suffixes if "_ENGINE" in s or s.endswith("ENGINE"))
    tail_count = sum(1 for s in suffixes if "_TAIL" in s or s.endswith("TAIL"))
    turbo_count = sum(1 for s in suffixes if "TURBO" in s or "BLOW" in s)
    # Some vanilla cars have an idle sample semantically crossed, e.g. IDLE_TAIL
    # inside an otherwise ENGINE layer. Use majority classification instead of
    # marking those as mixed, so import pairing does not fall back to slot number.
    if engine_count > tail_count:
        return "ENGINE samples"
    if tail_count > engine_count:
        return "TAIL samples"
    if engine_count and tail_count:
        return "MIXED ENGINE/TAIL"
    if turbo_count:
        return "TURBO/AUX samples"
    return "unknown samples"


MAIN_SLOT_RELS = set(range(0x6C, 0x94, 4))


def is_main_engine_tail_slot(block: Any) -> bool:
    return getattr(block, "root_rel", None) in MAIN_SLOT_RELS and getattr(block, "category", "") in ("Engine", "Exhaust", "Engine / Root A", "Exhaust / Root B")


def import_pair_key(block: Any) -> Tuple[str, Any]:
    """Stable donor/target pairing key for AudioBlockFull imports.

    Main engine/exhaust slots are paired by detected sample role first, so a
    target block that is semantically TAIL does not receive an ENGINE block just
    because both occupy the same numeric root slot. TurboBlower and unknown roles
    still fall back to root-relative slot because they are not part of the
    engine/tail layer swap problem.
    """
    if is_main_engine_tail_slot(block):
        role = getattr(block, "role", "unknown samples")
        if role == "ENGINE samples":
            return ("sample_role", "ENGINE")
        if role == "TAIL samples":
            return ("sample_role", "TAIL")
    return ("root_rel", getattr(block, "root_rel", None))


def pair_audio_blocks_for_import(donor_blocks: List[Any], target_blocks: List[Any]) -> Tuple[List[Tuple[Any, Any, str]], Dict[str, int], List[str]]:
    """Pair donor blocks to target blocks for transfer.

    Pairing is ordinal inside each logical key. For example, all donor blocks
    whose active ranges classify as ENGINE samples are sorted by root_rel and
    mapped to target ENGINE sample blocks in the same order. This preserves the
    performance-slot progression while avoiding Engine/Tail cross-writes when a
    car stores semantically swapped layers.
    """
    donor_groups: Dict[Tuple[str, Any], List[Any]] = {}
    for b in sorted(donor_blocks, key=lambda x: (getattr(x, "root_rel", 9999), getattr(x, "label", ""))):
        donor_groups.setdefault(import_pair_key(b), []).append(b)

    target_ordinals: Dict[Tuple[str, Any], int] = {}
    donor_by_rel = {getattr(b, "root_rel", None): b for b in donor_blocks}
    used_ids: set[int] = set()
    pairs: List[Tuple[Any, Any, str]] = []
    stats = {"role_matched": 0, "root_rel_matched": 0, "fallback_root_rel": 0, "unmatched": 0}
    warnings: List[str] = []

    for target in sorted(target_blocks, key=lambda x: (getattr(x, "root_rel", 9999), getattr(x, "label", ""))):
        key = import_pair_key(target)
        idx = target_ordinals.get(key, 0)
        target_ordinals[key] = idx + 1

        donor = None
        mode = "unmatched"
        candidates = donor_groups.get(key, [])
        if idx < len(candidates):
            donor = candidates[idx]
            mode = "role" if key[0] == "sample_role" else "root_rel"
        else:
            # Last-resort fallback: root-relative pairing, but only if that donor
            # was not already consumed by a role-based match.
            rel_donor = donor_by_rel.get(getattr(target, "root_rel", None))
            if rel_donor is not None and id(rel_donor) not in used_ids:
                donor = rel_donor
                mode = "fallback_root_rel"

        if donor is None:
            stats["unmatched"] += 1
            warnings.append(f"No donor AudioBlockFull match for {getattr(target, 'label', '<target>')} using key {key}.")
            continue

        used_ids.add(id(donor))
        if mode == "role":
            stats["role_matched"] += 1
        elif mode == "root_rel":
            stats["root_rel_matched"] += 1
        elif mode == "fallback_root_rel":
            stats["fallback_root_rel"] += 1
            warnings.append(f"{getattr(target, 'label', '<target>')}: role/key match missing; used root-relative fallback to {getattr(donor, 'label', '<donor>')}.")
        pairs.append((donor, target, mode))

    return pairs, stats, warnings


# -----------------------------------------------------------------------------
# Data model
# -----------------------------------------------------------------------------

@dataclass
class AudioRange:
    index: int
    file_off: int
    sample_name: str
    min_rpm: float
    max_rpm: float
    mid_rpm: float
    active: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "file_off": self.file_off,
            "sample_name": self.sample_name,
            "min_rpm": self.min_rpm,
            "max_rpm": self.max_rpm,
            "mid_rpm": self.mid_rpm,
            "active": self.active,
        }



def curve_allocated_row_capacity(buf_len: int, curve: "CurveData") -> int:
    """Return writable row capacity for an existing curve record.

    MC3 curve records observed so far use a fixed 0x140-byte storage block:
      +0x00..+0x1F header
      +0x20..+0x13F up to 8 rows of 9 floats

    point_count is just the number of active rows, not necessarily the amount of
    allocated storage. This matters when importing a donor with 3 rows into a
    receiver whose original curve only used 2 rows.
    """
    if curve.file_off is None or curve.points_file_off is None:
        return max(0, len(curve.rows))
    record_end = min(buf_len, curve.file_off + CURVE_STORAGE_SIZE)
    if curve.points_file_off < curve.file_off + CURVE_HEADER_SIZE:
        return max(0, len(curve.rows))
    if curve.points_file_off >= record_end:
        return max(0, len(curve.rows))
    return max(0, (record_end - curve.points_file_off) // CURVE_POINT_SIZE)


def eval_curve_row(row: List[float], x: float) -> float:
    if len(row) >= CURVE_POINT_FLOATS:
        return float(row[6]) * x * x + float(row[7]) * x + float(row[8])
    if len(row) >= 2:
        return float(row[1])
    return 0.0


def eval_piecewise_curve(rows: List[List[float]], x: float) -> float:
    usable = [r for r in rows if len(r) >= CURVE_POINT_FLOATS]
    if not usable:
        return 0.0
    # Prefer the segment whose start/end interval contains x.
    for r in usable:
        x0, x1 = float(r[0]), float(r[4])
        lo, hi = (x0, x1) if x0 <= x1 else (x1, x0)
        if lo - 1e-6 <= x <= hi + 1e-6:
            return eval_curve_row(r, x)
    # Clamp outside the domain to the nearest endpoint.
    first = usable[0]
    last = usable[-1]
    if x < min(float(first[0]), float(first[4])):
        return float(first[1])
    return float(last[5])


def fit_quadratic_coefficients(x0: float, y0: float, xm: float, ym: float, x1: float, y1: float) -> Tuple[float, float, float]:
    # Lagrange-form exact fit through 3 points. If the Xs collapse, use a linear fallback.
    if abs(x0 - xm) < 1e-7 or abs(xm - x1) < 1e-7 or abs(x0 - x1) < 1e-7:
        if abs(x1 - x0) < 1e-7:
            return 0.0, 0.0, float(y0)
        b = (y1 - y0) / (x1 - x0)
        c = y0 - b * x0
        return 0.0, b, c
    d0 = (x0 - xm) * (x0 - x1)
    dm = (xm - x0) * (xm - x1)
    d1 = (x1 - x0) * (x1 - xm)
    a = y0 / d0 + ym / dm + y1 / d1
    b = -y0 * (xm + x1) / d0 - ym * (x0 + x1) / dm - y1 * (x0 + xm) / d1
    c = y0 * xm * x1 / d0 + ym * x0 * x1 / dm + y1 * x0 * xm / d1
    return float(a), float(b), float(c)


def resample_curve_rows(source_rows: List[List[float]], target_count: int) -> List[List[float]]:
    """Compress/expand a curve into target_count rows without needing new storage.

    This preserves the donor's first X/Y and final X/Y exactly, then fits each
    output row as a quadratic segment sampled from the full donor curve.
    """
    usable = [r for r in source_rows if len(r) >= CURVE_POINT_FLOATS]
    target_count = int(target_count)
    if target_count <= 0 or not usable:
        return []
    if target_count == len(usable):
        return [list(map(float, r[:CURVE_POINT_FLOATS])) for r in usable]
    domain_start = float(usable[0][0])
    domain_end = float(usable[-1][4])
    if abs(domain_end - domain_start) < 1e-7:
        domain_end = domain_start + 1.0
    out: List[List[float]] = []
    for i in range(target_count):
        t0 = i / target_count
        t1 = (i + 1) / target_count
        x0 = domain_start + (domain_end - domain_start) * t0
        x1 = domain_start + (domain_end - domain_start) * t1
        xm = (x0 + x1) * 0.5
        y0 = eval_piecewise_curve(usable, x0)
        ym = eval_piecewise_curve(usable, xm)
        y1 = eval_piecewise_curve(usable, x1)
        a, b, c = fit_quadratic_coefficients(x0, y0, xm, ym, x1, y1)
        out.append([x0, y0, xm, ym, x1, y1, a, b, c])
    return out


def pack_curve_row(row: List[float]) -> bytes:
    vals = [float(v) for v in row[:CURVE_POINT_FLOATS]]
    if len(vals) != CURVE_POINT_FLOATS:
        raise ValueError("Curve row must contain exactly 9 floats.")
    return struct.pack("<" + "f" * CURVE_POINT_FLOATS, *vals)


@dataclass
class CurveData:
    key: str
    label: str
    ptr_key: str
    ptr_va: int = 0
    file_off: Optional[int] = None
    magic: int = 0
    min_x: float = 0.0
    min_y: float = 0.0
    max_x: float = 0.0
    max_y: float = 0.0
    point_count: int = 0  # Backward-compatible alias for ActiveSeriesCount.
    ptr_points: int = 0
    points_file_off: Optional[int] = None
    pad_1C: int = 0
    rows: List[List[float]] = field(default_factory=list)
    warning: str = ""

    @property
    def plot_points(self) -> List[Tuple[float, float]]:
        pts: List[Tuple[float, float]] = []
        for row in self.rows[:max(0, int(self.point_count))]:
            if len(row) >= 2:
                x, y = row[0], row[1]
                if math.isfinite(x) and math.isfinite(y):
                    pts.append((x, y))
        return pts

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "ptr_va": self.ptr_va,
            "file_off": self.file_off,
            "magic": self.magic,
            "min_x": self.min_x,
            "min_y": self.min_y,
            "max_x": self.max_x,
            "max_y": self.max_y,
            "active_series_count": self.point_count,
            "point_count": self.point_count,  # alias kept for older exported JSON/tools
            "ptr_points": self.ptr_points,
            "points_file_off": self.points_file_off,
            "pad_1C": self.pad_1C,
            "rows": self.rows,
            "warning": self.warning,
        }


@dataclass
class AudioBlock:
    doc: "PckAudioDocument"
    key: str
    label: str
    category: str
    root_rel: int
    ptr_name: str
    ptr_va: int
    file_off: Optional[int]
    kind: str = "block"
    magic: int = 0
    bank_name: str = ""
    min_rpm_rec: float = 0.0
    max_rpm_rec: float = 0.0
    near_dist: float = 0.0
    far_dist: float = 0.0
    active_range_count: int = 0
    ptr_range_table: int = 0
    range_table_file_off: Optional[int] = None
    warble_min_rpm: float = 0.0
    warble_max_rpm: float = 0.0
    warble_min_rpm_delta: float = 0.0
    warble_min_vol_delta: float = 0.0
    warble_max_rpm_delta: float = 0.0
    warble_max_vol_delta: float = 0.0
    warble_reverse_max_rpm_delta: float = 0.0
    warble_reverse_period_rate: float = 0.0
    boost_mix_percent: float = 0.0
    boost_mix_rate: float = 0.0
    high_pitch_curve_engage_gear: int = 0
    engine_rev_sound: str = ""
    ranges: List[AudioRange] = field(default_factory=list)
    curves: Dict[str, CurveData] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    @property
    def role(self) -> str:
        return likely_sample_role(self.ranges, self.active_range_count)

    @property
    def display_label(self) -> str:
        bank = self.bank_name or "<no bank>"
        role = self.role if self.ranges else "no ranges"
        return f"{self.label} | {bank} | {role}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "category": self.category,
            "root_rel": self.root_rel,
            "ptr_name": self.ptr_name,
            "ptr_va": self.ptr_va,
            "file_off": self.file_off,
            "kind": self.kind,
            "magic": self.magic,
            "bank_name": self.bank_name,
            "min_rpm_rec": self.min_rpm_rec,
            "max_rpm_rec": self.max_rpm_rec,
            "near_dist": self.near_dist,
            "far_dist": self.far_dist,
            "active_range_count": self.active_range_count,
            "ptr_range_table": self.ptr_range_table,
            "range_table_file_off": self.range_table_file_off,
            "warble_min_rpm": self.warble_min_rpm,
            "warble_max_rpm": self.warble_max_rpm,
            "warble_min_rpm_delta": self.warble_min_rpm_delta,
            "warble_min_vol_delta": self.warble_min_vol_delta,
            "warble_max_rpm_delta": self.warble_max_rpm_delta,
            "warble_max_vol_delta": self.warble_max_vol_delta,
            "warble_reverse_max_rpm_delta": self.warble_reverse_max_rpm_delta,
            "warble_reverse_period_rate": self.warble_reverse_period_rate,
            "boost_mix_percent": self.boost_mix_percent,
            "boost_mix_rate": self.boost_mix_rate,
            "high_pitch_curve_engage_gear": self.high_pitch_curve_engage_gear,
            "engine_rev_sound": self.engine_rev_sound,
            "ranges": [r.to_dict() for r in self.ranges],
            "curves": {k: v.to_dict() for k, v in self.curves.items()},
            "warnings": list(self.warnings),
        }


@dataclass
class RawAuxBlock:
    doc: "PckAudioDocument"
    key: str
    label: str
    category: str
    root_rel: int
    ptr_name: str
    ptr_va: int
    file_off: Optional[int]
    kind: str
    magic: int = 0
    possible_name_0: str = ""
    possible_name_1: str = ""
    found_strings: List[str] = field(default_factory=list)
    found_string_entries: List[Dict[str, Any]] = field(default_factory=list)
    float_preview: List[Dict[str, Any]] = field(default_factory=list)
    preview_hex: str = ""
    warnings: List[str] = field(default_factory=list)

    @property
    def display_label(self) -> str:
        names = [s for s in (self.possible_name_0, self.possible_name_1) if s]
        suffix = " | " + " / ".join(names[:2]) if names else ""
        return f"{self.label}{suffix}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "category": self.category,
            "root_rel": self.root_rel,
            "ptr_name": self.ptr_name,
            "ptr_va": self.ptr_va,
            "file_off": self.file_off,
            "kind": self.kind,
            "magic": self.magic,
            "possible_name_0": self.possible_name_0,
            "possible_name_1": self.possible_name_1,
            "found_strings": self.found_strings,
            "found_string_entries": self.found_string_entries,
            "float_preview": self.float_preview,
            "preview_hex": self.preview_hex,
            "warnings": self.warnings,
        }


@dataclass
class CommonBlock:
    doc: "PckAudioDocument"
    key: str
    label: str
    category: str
    root_rel: int
    ptr_name: str
    ptr_va: int
    file_off: Optional[int]
    magic: int = 0
    params: List[float] = field(default_factory=list)
    unk_5C: int = 0
    warnings: List[str] = field(default_factory=list)

    @property
    def display_label(self) -> str:
        return f"{self.label} | magic {format_hex(self.magic)}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "category": self.category,
            "root_rel": self.root_rel,
            "ptr_name": self.ptr_name,
            "ptr_va": self.ptr_va,
            "file_off": self.file_off,
            "magic": self.magic,
            "params": self.params,
            "unk_5C": self.unk_5C,
            "warnings": self.warnings,
        }


ParsedItem = AudioBlock | RawAuxBlock | CommonBlock


@dataclass
class PckAudioDocument:
    path: Path
    buf: bytearray
    ptr_va: int
    base_va: int
    file_version: int
    flags: int
    file_data_size: int
    expected_data_size: int
    root_va: int
    root_off: int
    root_magic: int
    root_name_ptr: int
    root_name: str
    root_pointers: Dict[str, int]
    blocks: List[AudioBlock] = field(default_factory=list)
    raw_aux_blocks: List[RawAuxBlock] = field(default_factory=list)
    commons: List[CommonBlock] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.path.name

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_pck": str(self.path),
            "pck_header_start": {
                "ptr_va": self.ptr_va,
                "file_version": self.file_version,
                "flags": self.flags,
                "file_data_size": self.file_data_size,
                "expected_data_size": self.expected_data_size,
                "base_va": self.base_va,
            },
            "audio_root": {
                "ptr_audio_root_A8": self.root_va,
                "file_off": self.root_off,
                "magic": self.root_magic,
                "magic_label": AUDIO_ROOT_MAGICS.get(self.root_magic, "unknown"),
                "ptr_name": self.root_name_ptr,
                "name": self.root_name,
                "pointers": self.root_pointers,
            },
            "engine_levels": {b.key: b.to_dict() for b in self.blocks if b.category in ("Engine", "Engine / Root A")},
            "exhaust_levels": {b.key: b.to_dict() for b in self.blocks if b.category in ("Exhaust", "Exhaust / Root B")},
            "turboblower_levels": {b.key: b.to_dict() for b in self.blocks if b.category in ("Auxiliary / TurboBlower", "Auxiliary / TurboBlower")},
            "raw_auxiliary": {b.key: b.to_dict() for b in self.raw_aux_blocks},
            "common_levels": {p.key: p.to_dict() for p in self.commons},
            "legacy_aliases": {
                "slot_engine_sounds": {b.key: b.to_dict() for b in self.blocks if b.category in ("Engine", "Engine / Root A")},
                "slot_exhaust_sounds": {b.key: b.to_dict() for b in self.blocks if b.category in ("Exhaust", "Exhaust / Root B")},
                "aux_audio_blocks": {b.key: b.to_dict() for b in self.blocks if b.category in ("Auxiliary / TurboBlower", "Auxiliary / TurboBlower")},
                "commons": {p.key: p.to_dict() for p in self.commons},
            },
            "warnings": self.warnings,
        }


# -----------------------------------------------------------------------------
# PCK Parser
# -----------------------------------------------------------------------------

class PckAudioParser:
    def __init__(self, path: Path, buf: Optional[bytes | bytearray] = None):
        self.path = Path(path)
        self.buf = bytearray(buf) if buf is not None else bytearray(self.path.read_bytes())
        if len(self.buf) < 0xC0:
            raise ValueError("File is too small to be a vehicle PCK with an audio root pointer.")
        self.ptr_va = u32_le(self.buf, 0x00)
        self.base_va = self.ptr_va - 0x80
        self.file_version = u32_le(self.buf, 0x04)
        self.flags = u32_le(self.buf, 0x08)
        self.file_data_size = u32_le(self.buf, 0x0C)
        self.expected_data_size = max(0, len(self.buf) - 0x80)

    def is_valid_file_range(self, off: Optional[int], size: int = 1) -> bool:
        return off is not None and off >= 0 and off + size <= len(self.buf)

    def is_valid_va(self, va: int, size: int = 1) -> bool:
        if is_nullish_u32(va):
            return False
        off = va_to_off(va, self.base_va)
        return self.is_valid_file_range(off, size)

    def va_to_off_optional(self, va: int, size: int = 1) -> Optional[int]:
        if self.is_valid_va(va, size):
            return va_to_off(va, self.base_va)
        return None

    def parse(self) -> PckAudioDocument:
        warnings: List[str] = []
        if self.file_data_size != self.expected_data_size:
            warnings.append(
                f"Header file_data_size={self.file_data_size} differs from actual size-0x80={self.expected_data_size}."
            )

        root_va = u32_le(self.buf, 0xA8)
        root_off = self.va_to_off_optional(root_va, 0xB8)
        if root_off is None:
            raise ValueError(f"Invalid ptr_audio_root_A8={format_hex(root_va)}. Cannot parse audio.")

        root_magic = u32_le(self.buf, root_off)
        if root_magic not in AUDIO_ROOT_MAGICS:
            warnings.append(f"Unknown audio root magic {format_hex(root_magic)} at file 0x{root_off:08X}.")

        root_name_ptr = u32_le(self.buf, root_off + 0x04)
        root_name_off = self.va_to_off_optional(root_name_ptr, 1)
        root_name = read_fixed_string(self.buf, root_name_off, 0x80) if root_name_off is not None else ""

        root_pointers: Dict[str, int] = {}
        for ptr_name, _label, root_rel, _kind, _category in ROOT_POINTERS:
            root_pointers[ptr_name] = safe_u32(self.buf, root_off + root_rel)

        doc = PckAudioDocument(
            path=self.path,
            buf=self.buf,
            ptr_va=self.ptr_va,
            base_va=self.base_va,
            file_version=self.file_version,
            flags=self.flags,
            file_data_size=self.file_data_size,
            expected_data_size=self.expected_data_size,
            root_va=root_va,
            root_off=root_off,
            root_magic=root_magic,
            root_name_ptr=root_name_ptr,
            root_name=root_name,
            root_pointers=root_pointers,
            warnings=warnings,
        )

        for ptr_name, label, root_rel, kind, category in ROOT_POINTERS:
            ptr_va = root_pointers.get(ptr_name, 0)
            file_off = self.va_to_off_optional(ptr_va, 4)
            key = ptr_name.replace("ptr_", "")
            if file_off is None:
                continue
            if kind == "block":
                block = self.parse_audio_block(doc, key, label, category, root_rel, ptr_name, ptr_va, file_off)
                doc.blocks.append(block)
            elif kind == "common":
                common = self.parse_common_block(doc, key, label, category, root_rel, ptr_name, ptr_va, file_off)
                doc.commons.append(common)
            else:
                raw = self.parse_raw_aux(doc, key, label, category, root_rel, ptr_name, ptr_va, file_off, kind)
                doc.raw_aux_blocks.append(raw)

        # Stable, user-facing order.
        cat_order = {
            "Engine": 0,
            "Exhaust": 1,
            "Auxiliary / TurboBlower": 2,
        }
        doc.blocks.sort(key=lambda b: (cat_order.get(b.category, 99), b.root_rel))
        doc.raw_aux_blocks.sort(key=lambda b: (b.category, b.root_rel))
        doc.commons.sort(key=lambda p: p.root_rel)
        return doc

    def parse_audio_block(
        self,
        doc: PckAudioDocument,
        key: str,
        label: str,
        category: str,
        root_rel: int,
        ptr_name: str,
        ptr_va: int,
        file_off: int,
    ) -> AudioBlock:
        warnings: List[str] = []
        if not self.is_valid_file_range(file_off, 0xA0):
            warnings.append("AudioBlockFull extends outside file range.")

        magic = safe_u32(self.buf, file_off)
        if magic not in AUDIO_BLOCK_MAGICS:
            warnings.append(f"Unexpected AudioBlockFull magic {format_hex(magic)}.")

        bank_name = read_fixed_string(self.buf, file_off + 0x04, 0x20)
        active_count = safe_u32(self.buf, file_off + 0x34)
        if active_count > RANGE_ALLOCATED_COUNT:
            warnings.append(f"active_range_count={active_count} is above allocated count {RANGE_ALLOCATED_COUNT}.")

        ptr_range_table = safe_u32(self.buf, file_off + 0x38)
        range_table_off = self.va_to_off_optional(ptr_range_table, RANGE_ENTRY_SIZE)
        expected_range_table_off = file_off + RANGE_TABLE_REL_FALLBACK
        if range_table_off is None and self.is_valid_file_range(expected_range_table_off, RANGE_ENTRY_SIZE):
            range_table_off = expected_range_table_off
            warnings.append("ptr_range_table invalid; used fixed AudioBlockFull+0xA0 range table fallback.")
        elif range_table_off is not None and range_table_off != expected_range_table_off and magic in AUDIO_BLOCK_MAGICS:
            warnings.append(
                f"ptr_range_table differs from fixed package expectation: {format_hex(range_table_off)} vs {format_hex(expected_range_table_off)}."
            )

        ranges: List[AudioRange] = []
        if range_table_off is None:
            warnings.append(f"Invalid ptr_range_table={format_hex(ptr_range_table)}.")
        else:
            max_entries = RANGE_ALLOCATED_COUNT
            for i in range(max_entries):
                roff = range_table_off + i * RANGE_ENTRY_SIZE
                if not self.is_valid_file_range(roff, RANGE_ENTRY_SIZE):
                    warnings.append(f"Range {i} extends outside file range.")
                    break
                sample = read_fixed_string(self.buf, roff, 0x20)
                ranges.append(
                    AudioRange(
                        index=i,
                        file_off=roff,
                        sample_name=sample,
                        min_rpm=safe_f32(self.buf, roff + 0x20),
                        max_rpm=safe_f32(self.buf, roff + 0x24),
                        mid_rpm=safe_f32(self.buf, roff + 0x28),
                        active=i < active_count,
                    )
                )

        block = AudioBlock(
            doc=doc,
            key=key,
            label=label,
            category=category,
            root_rel=root_rel,
            ptr_name=ptr_name,
            ptr_va=ptr_va,
            file_off=file_off,
            kind="block",
            magic=magic,
            bank_name=bank_name,
            min_rpm_rec=safe_f32(self.buf, file_off + 0x24),
            max_rpm_rec=safe_f32(self.buf, file_off + 0x28),
            near_dist=safe_f32(self.buf, file_off + 0x2C),
            far_dist=safe_f32(self.buf, file_off + 0x30),
            active_range_count=active_count,
            ptr_range_table=ptr_range_table,
            range_table_file_off=range_table_off,
            warble_min_rpm=safe_f32(self.buf, file_off + 0x3C),
            warble_max_rpm=safe_f32(self.buf, file_off + 0x40),
            warble_min_rpm_delta=safe_f32(self.buf, file_off + 0x44),
            warble_min_vol_delta=safe_f32(self.buf, file_off + 0x48),
            warble_max_rpm_delta=safe_f32(self.buf, file_off + 0x4C),
            warble_max_vol_delta=safe_f32(self.buf, file_off + 0x50),
            warble_reverse_max_rpm_delta=safe_f32(self.buf, file_off + 0x54),
            warble_reverse_period_rate=safe_f32(self.buf, file_off + 0x58),
            boost_mix_percent=safe_f32(self.buf, file_off + 0x5C),
            boost_mix_rate=safe_f32(self.buf, file_off + 0x60),
            high_pitch_curve_engage_gear=safe_u32(self.buf, file_off + 0x64),
            engine_rev_sound=read_fixed_string(self.buf, file_off + ENGINE_REV_SOUND_OFF, ENGINE_REV_SOUND_SIZE),
            ranges=ranges,
            warnings=warnings,
        )

        for curve_key, curve_label, ptr_key, ptr_rel in CURVE_FIELDS:
            ptr_curve = safe_u32(self.buf, file_off + ptr_rel)
            curve = self.parse_curve(curve_key, curve_label, ptr_key, ptr_curve)
            expected_curve_off = file_off + FIXED_PACKAGE_CURVE_RELS.get(curve_key, 0)
            if (curve.file_off is None or curve.magic != CURVE_MAGIC) and self.is_valid_file_range(expected_curve_off, CURVE_STORAGE_SIZE):
                fallback_va = self.base_va + expected_curve_off
                curve = self.parse_curve(curve_key, curve_label, ptr_key, fallback_va)
                curve.warning = (curve.warning + " " if curve.warning else "") + "used fixed AudioBlockFull curve-offset fallback."
            elif curve.file_off is not None and curve.file_off != expected_curve_off and self.is_valid_file_range(expected_curve_off, CURVE_STORAGE_SIZE):
                curve.warning = (curve.warning + " " if curve.warning else "") + f"curve pointer differs from fixed package expectation {format_hex(expected_curve_off)}."
            block.curves[curve_key] = curve

        return block

    def parse_curve(self, curve_key: str, curve_label: str, ptr_key: str, ptr_va: int) -> CurveData:
        curve = CurveData(key=curve_key, label=curve_label, ptr_key=ptr_key, ptr_va=ptr_va)
        curve_off = self.va_to_off_optional(ptr_va, 0x20)
        curve.file_off = curve_off
        if curve_off is None:
            curve.warning = f"Invalid curve pointer {format_hex(ptr_va)}."
            return curve

        curve.magic = safe_u32(self.buf, curve_off)
        curve.min_x = safe_f32(self.buf, curve_off + 0x04)
        curve.min_y = safe_f32(self.buf, curve_off + 0x08)
        curve.max_x = safe_f32(self.buf, curve_off + 0x0C)
        curve.max_y = safe_f32(self.buf, curve_off + 0x10)
        curve.point_count = safe_u32(self.buf, curve_off + 0x14)  # ActiveSeriesCount
        curve.ptr_points = safe_u32(self.buf, curve_off + 0x18)
        curve.pad_1C = safe_u32(self.buf, curve_off + 0x1C)

        if curve.magic != CURVE_MAGIC:
            curve.warning = f"Unexpected curve magic {format_hex(curve.magic)}."

        if curve.point_count > 64:
            curve.warning = (curve.warning + " " if curve.warning else "") + f"Suspicious ActiveSeriesCount={curve.point_count}."
            return curve

        points_off = self.va_to_off_optional(curve.ptr_points, max(0, curve.point_count) * CURVE_POINT_SIZE)
        if points_off is None:
            # Most tested files have ptr_points == curve + 0x20. Fallback keeps exploration usable.
            fallback = curve_off + 0x20
            if self.is_valid_file_range(fallback, max(0, curve.point_count) * CURVE_POINT_SIZE):
                points_off = fallback
                curve.warning = (curve.warning + " " if curve.warning else "") + "ptr_points invalid; used curve+0x20 fallback."
            else:
                curve.warning = (curve.warning + " " if curve.warning else "") + f"Invalid ptr_points={format_hex(curve.ptr_points)}."
                return curve
        curve.points_file_off = points_off

        rows: List[List[float]] = []
        for i in range(curve.point_count):
            row_off = points_off + i * CURVE_POINT_SIZE
            if not self.is_valid_file_range(row_off, CURVE_POINT_SIZE):
                curve.warning = (curve.warning + " " if curve.warning else "") + f"Point row {i} out of range."
                break
            row = [safe_f32(self.buf, row_off + j * 4) for j in range(CURVE_POINT_FLOATS)]
            rows.append(row)
        curve.rows = rows
        return curve

    def parse_raw_aux(
        self,
        doc: PckAudioDocument,
        key: str,
        label: str,
        category: str,
        root_rel: int,
        ptr_name: str,
        ptr_va: int,
        file_off: int,
        kind: str,
    ) -> RawAuxBlock:
        preview_size = 0x200 if kind == "raw_bundle" else 0x100
        warnings: List[str] = []
        if not self.is_valid_file_range(file_off, 4):
            warnings.append("Raw auxiliary pointer outside file range.")

        magic = safe_u32(self.buf, file_off)
        possible_name_0 = read_fixed_string(self.buf, file_off + 0x04, 0x20)
        possible_name_1 = read_fixed_string(self.buf, file_off + 0x24, 0x20)
        found_string_entries = read_loose_ascii_string_entries(self.buf, file_off + 0x04, preview_size)
        found_strings = []
        for entry in found_string_entries:
            text = str(entry.get("text", ""))
            if text and text not in found_strings:
                found_strings.append(text)
        float_preview = raw_float_preview(self.buf, file_off, min(preview_size, 0x120))

        raw = self.buf[file_off:min(len(self.buf), file_off + min(preview_size, 0x80))]
        preview_hex = " ".join(f"{b:02X}" for b in raw)
        return RawAuxBlock(
            doc=doc,
            key=key,
            label=label,
            category=category,
            root_rel=root_rel,
            ptr_name=ptr_name,
            ptr_va=ptr_va,
            file_off=file_off,
            kind=kind,
            magic=magic,
            possible_name_0=possible_name_0,
            possible_name_1=possible_name_1,
            found_strings=found_strings,
            found_string_entries=found_string_entries,
            float_preview=float_preview,
            preview_hex=preview_hex,
            warnings=warnings,
        )

    def parse_common_block(
        self,
        doc: PckAudioDocument,
        key: str,
        label: str,
        category: str,
        root_rel: int,
        ptr_name: str,
        ptr_va: int,
        file_off: int,
    ) -> CommonBlock:
        warnings: List[str] = []
        magic = safe_u32(self.buf, file_off)
        if magic not in PROFILE_MAGICS:
            warnings.append(f"Unexpected common magic {format_hex(magic)}.")
        params = []
        for i in range(22):
            params.append(safe_f32(self.buf, file_off + 0x04 + i * 4))
        unk_5C = safe_u32(self.buf, file_off + 0x5C)
        return CommonBlock(
            doc=doc,
            key=key,
            label=label,
            category=category,
            root_rel=root_rel,
            ptr_name=ptr_name,
            ptr_va=ptr_va,
            file_off=file_off,
            magic=magic,
            params=params,
            unk_5C=unk_5C,
            warnings=warnings,
        )


# -----------------------------------------------------------------------------
# Tk GUI
# -----------------------------------------------------------------------------

class AudioCurveGUI:
    BG = "#15161A"
    PANEL = "#1D1F26"
    PANEL2 = "#242731"
    FG = "#E6E8EF"
    MUTED = "#8D94A3"
    GRID = "#353945"
    ACCENT = "#E3C75F"
    WARN = "#FFB86C"
    ERR = "#FF6B6B"
    OK = "#7ED37E"

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("MC3 Audio Curve GUI v0.19")
        self.root.geometry("1480x900")
        self.root.minsize(1120, 700)

        self.doc: Optional[PckAudioDocument] = None
        self.item_map: Dict[str, Any] = {}
        self.selected: Optional[Any] = None
        self.current_path: Optional[Path] = None
        self.dirty = False
        self.selected_tree_id: Optional[str] = None

        self.history_limit = 10
        self.undo_stack: List[Dict[str, Any]] = []
        self.redo_stack: List[Dict[str, Any]] = []

        self.edit_field1 = tk.StringVar(value="")
        self.edit_field2 = tk.StringVar(value="")
        self.edit_field3 = tk.StringVar(value="")
        self.edit_field1_label = tk.StringVar(value="Banks File Name")
        self.edit_field2_label = tk.StringVar(value="Minimum RPM")
        self.edit_field3_label = tk.StringVar(value="Maximum RPM")
        self.editor_status = tk.StringVar(value="")

        self.curve_visible = {key: tk.BooleanVar(value=True) for key, _, _, _ in CURVE_FIELDS}
        self.curve_scale_mode = tk.StringVar(value="global")  # "global" or "fit"
        self.curve_draw_mode = tk.StringVar(value="poly")  # "poly" or "linear"
        self.show_point_labels = tk.BooleanVar(value=True)
        self.show_control_points = tk.BooleanVar(value=False)
        self.edit_curve_points = tk.BooleanVar(value=True)

        self._curve_hitboxes: List[Dict[str, Any]] = []
        self._curve_transform: Optional[Dict[str, float]] = None
        self._curve_drag: Optional[Dict[str, Any]] = None
        self._active_curve_handle: Optional[Dict[str, Any]] = None
        self._curve_table_editor: Optional[tk.Entry] = None

        self._setup_style()
        self._build_ui()

        # Keep the graph RPM hint labels responsive while the top Min/Max RPM
        # fields are being typed. The actual PCK bytes are still written only
        # by Return/FocusOut/apply, so this does not spam undo history.
        self.edit_field2.trace_add("write", self.on_top_rpm_preview_changed)
        self.edit_field3.trace_add("write", self.on_top_rpm_preview_changed)

        # Commit/clear active Entry focus when the user clicks outside editable fields.
        # This makes the top editors behave like typical DCC/property panels: clicking
        # in empty UI space applies the pending value and removes the caret/selection.
        self.root.bind_all("<ButtonPress-1>", self.on_global_left_click_for_focus_clear, add="+")

    def _widget_is_descendant_of(self, widget: tk.Widget, ancestor: tk.Widget) -> bool:
        """Return True when widget is ancestor itself or a child/grandchild."""
        try:
            current = widget
            while current is not None:
                if current == ancestor:
                    return True
                parent_name = current.winfo_parent()
                if not parent_name:
                    break
                current = current._nametowidget(parent_name)
        except Exception:
            return False
        return False

    def _is_editable_text_widget(self, widget: tk.Widget) -> bool:
        try:
            klass = widget.winfo_class()
        except Exception:
            return False
        return klass in {"Entry", "TEntry", "Text", "TText", "Spinbox", "TSpinbox", "Combobox", "TCombobox"}

    def _focus_clear_targets(self) -> List[tk.Widget]:
        targets: List[tk.Widget] = []
        for name in (
            "field1_entry", "field2_entry", "field3_entry",
            "_curve_table_editor", "_range_table_editor", "_aux_floats_tree_editor",
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                try:
                    if widget.winfo_exists():
                        targets.append(widget)
                except Exception:
                    pass
        return targets

    def on_global_left_click_for_focus_clear(self, event):
        """Commit pending edits and clear focus when clicking outside editable widgets."""
        try:
            clicked = event.widget
            focused = self.root.focus_get()
        except Exception:
            return

        if focused is None:
            return

        # Do not clear focus when the user clicks inside any editable text control.
        if self._is_editable_text_widget(clicked):
            return

        active_targets = self._focus_clear_targets()
        if not any(self._widget_is_descendant_of(focused, target) for target in active_targets):
            return

        # If the click is still inside the currently focused editor, keep editing.
        if any(self._widget_is_descendant_of(clicked, target) for target in active_targets):
            return

        # Top fields are normal property editors; apply silently before focus is removed.
        if focused in (getattr(self, "field1_entry", None), getattr(self, "field2_entry", None), getattr(self, "field3_entry", None)):
            self.apply_top_fields_from_vars(silent=True)

        # Moving focus to the root triggers FocusOut on temporary table editors, so their
        # existing commit logic remains the single source of truth.
        try:
            self.root.focus_set()
        except Exception:
            pass

    def _setup_style(self):
        self.root.configure(bg=self.BG)
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(".", background=self.BG, foreground=self.FG, fieldbackground=self.PANEL, bordercolor=self.GRID)
        style.configure("TFrame", background=self.BG)
        style.configure("Panel.TFrame", background=self.PANEL)
        style.configure("TLabel", background=self.BG, foreground=self.FG)
        style.configure("Muted.TLabel", background=self.BG, foreground=self.MUTED)
        style.configure("Accent.TLabel", background=self.BG, foreground=self.ACCENT)
        style.configure("Warn.TLabel", background=self.BG, foreground=self.WARN)
        style.configure("TButton", background=self.PANEL2, foreground=self.FG, padding=4)
        style.map("TButton", background=[("active", "#303443")])
        style.configure("Treeview", background=self.PANEL, foreground=self.FG, fieldbackground=self.PANEL, rowheight=24)
        style.configure("Treeview.Heading", background=self.PANEL2, foreground=self.FG)
        style.map("Treeview", background=[("selected", "#3A4052")], foreground=[("selected", "#FFFFFF")])
        style.configure("TCheckbutton", background=self.BG, foreground=self.FG)
        style.configure("TNotebook", background=self.BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=self.PANEL2, foreground=self.FG, padding=(8, 4))
        style.map("TNotebook.Tab", background=[("selected", self.PANEL)], foreground=[("selected", "#FFFFFF")])

    def _build_ui(self):
        self._build_menu()

        toolbar = ttk.Frame(self.root, style="Panel.TFrame")
        toolbar.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(toolbar, text="Open PCK", command=self.open_pck_dialog).pack(side=tk.LEFT, padx=4, pady=4)
        ttk.Button(toolbar, text="Reload", command=self.reload_current).pack(side=tk.LEFT, padx=4, pady=4)
        ttk.Button(toolbar, text="Save", command=self.save_current_pck).pack(side=tk.LEFT, padx=4, pady=4)
        ttk.Button(toolbar, text="Save As", command=self.save_pck_as).pack(side=tk.LEFT, padx=4, pady=4)
        self.undo_button = ttk.Button(toolbar, text="Undo", command=self.undo_last_action)
        self.undo_button.pack(side=tk.LEFT, padx=(12, 4), pady=4)
        self.redo_button = ttk.Button(toolbar, text="Redo", command=self.redo_last_action)
        self.redo_button.pack(side=tk.LEFT, padx=4, pady=4)
        ttk.Button(toolbar, text="Import Conservative", command=self.import_audio_from_pck_dialog).pack(side=tk.LEFT, padx=(12, 4), pady=4)
        ttk.Button(toolbar, text="Import Experimental", command=self.import_audio_experimental_from_pck_dialog).pack(side=tk.LEFT, padx=4, pady=4)
        ttk.Button(toolbar, text="Export Parsed JSON", command=self.export_parsed_json).pack(side=tk.LEFT, padx=4, pady=4)
        self.dirty_label = ttk.Label(toolbar, text="", style="Warn.TLabel")
        self.dirty_label.pack(side=tk.LEFT, padx=(18, 4))

        main = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        main.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        left = ttk.Frame(main, style="Panel.TFrame")
        right = ttk.Panedwindow(main, orient=tk.VERTICAL)
        main.add(left, weight=1)
        main.add(right, weight=4)

        ttk.Label(left, text="PCK Audio Structure", style="Accent.TLabel").pack(anchor="w", padx=8, pady=(8, 4))
        self.tree = ttk.Treeview(left, show="tree")
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=(0, 8))
        tree_scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.tree.yview)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y, pady=(0, 8))
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        top_right = ttk.Frame(right)
        bottom_right = ttk.Notebook(right)
        # Initial vertical split: keep the bottom tables at roughly 20% of the right pane.
        # The table pane is still manually adjustable through the splitter.
        right.add(top_right, weight=8)
        right.add(bottom_right, weight=2)

        self._build_edit_panel(top_right)
        self._build_curve_controls(top_right)

        curve_frame = ttk.Frame(top_right)
        curve_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(4, 4))
        curve_frame.columnconfigure(0, weight=1)
        curve_frame.rowconfigure(0, weight=1)

        self.curve_canvas = tk.Canvas(
            curve_frame,
            bg=self.PANEL,
            highlightthickness=0,
            xscrollincrement=16,
            yscrollincrement=16,
        )
        curve_scroll_x = ttk.Scrollbar(curve_frame, orient=tk.HORIZONTAL, command=self.curve_canvas.xview)
        curve_scroll_y = ttk.Scrollbar(curve_frame, orient=tk.VERTICAL, command=self.curve_canvas.yview)
        self.curve_canvas.configure(xscrollcommand=curve_scroll_x.set, yscrollcommand=curve_scroll_y.set)
        self.curve_canvas.grid(row=0, column=0, sticky="nsew")
        curve_scroll_y.grid(row=0, column=1, sticky="ns")
        curve_scroll_x.grid(row=1, column=0, sticky="ew")
        self.curve_canvas.bind("<Configure>", lambda _event: self.redraw_curve_canvas())
        self.curve_canvas.bind("<ButtonPress-1>", self.on_curve_canvas_press)
        self.curve_canvas.bind("<B1-Motion>", self.on_curve_canvas_drag)
        self.curve_canvas.bind("<ButtonRelease-1>", self.on_curve_canvas_release)
        self.curve_canvas.bind("<ButtonPress-2>", self.on_curve_canvas_middle_press)
        self.curve_canvas.bind("<ButtonPress-3>", self.on_curve_canvas_right_press)
        self.curve_canvas.bind("<Motion>", self.on_curve_canvas_motion)

        self.summary = tk.Text(
            top_right,
            height=6,
            bg=self.PANEL,
            fg=self.FG,
            insertbackground=self.FG,
            relief=tk.FLAT,
            wrap=tk.WORD,
        )
        self.summary.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(0, 8))
        self.summary.configure(state=tk.DISABLED)

        ranges_frame = ttk.Frame(bottom_right)
        curves_frame = ttk.Frame(bottom_right)
        aux_float_frame = ttk.Frame(bottom_right)
        raw_frame = ttk.Frame(bottom_right)
        bottom_right.add(ranges_frame, text="Ranges")
        bottom_right.add(curves_frame, text="Curve Rows")
        bottom_right.add(aux_float_frame, text="Aux Floats")
        bottom_right.add(raw_frame, text="Raw / Warnings")

        self.ranges_tree = ttk.Treeview(
            ranges_frame,
            columns=("idx", "active", "sample", "min", "mid", "max", "off"),
            show="headings",
            height=8,
        )
        for col, text, width, anchor in [
            ("idx", "#", 45, "e"),
            ("active", "Active", 65, "center"),
            ("sample", "Sample", 360, "w"),
            ("min", "Min RPM", 100, "e"),
            ("mid", "Mid RPM", 100, "e"),
            ("max", "Max RPM", 100, "e"),
            ("off", "File Off", 110, "e"),
        ]:
            self.ranges_tree.heading(col, text=text)
            self.ranges_tree.column(col, width=width, anchor=anchor)
        ranges_frame.columnconfigure(0, weight=1)
        ranges_frame.rowconfigure(0, weight=1)
        ranges_scroll_y = ttk.Scrollbar(ranges_frame, orient=tk.VERTICAL, command=self.ranges_tree.yview)
        ranges_scroll_x = ttk.Scrollbar(ranges_frame, orient=tk.HORIZONTAL, command=self.ranges_tree.xview)
        self.ranges_tree.configure(yscrollcommand=ranges_scroll_y.set, xscrollcommand=ranges_scroll_x.set)
        self.ranges_tree.bind("<Double-1>", self.on_ranges_double_click)
        self.ranges_tree.bind("<F2>", self.on_ranges_f2)
        self.ranges_tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=(8, 0))
        ranges_scroll_y.grid(row=0, column=1, sticky="ns", pady=(8, 0))
        ranges_scroll_x.grid(row=1, column=0, sticky="ew", padx=(8, 0), pady=(0, 8))

        self.curve_rows_tree = ttk.Treeview(
            curves_frame,
            columns=("curve", "idx", "x0", "y0", "cx", "cy", "x1", "y1", "a", "b", "c"),
            show="headings",
            height=8,
        )
        headings = [
            ("curve", "Curve", 120, "w"),
            ("idx", "#", 45, "e"),
            ("x0", "Start X", 90, "e"),
            ("y0", "Start Y", 90, "e"),
            ("cx", "Ctrl X", 90, "e"),
            ("cy", "Ctrl Y", 90, "e"),
            ("x1", "End X", 90, "e"),
            ("y1", "End Y", 90, "e"),
            ("a", "A", 90, "e"),
            ("b", "B", 90, "e"),
            ("c", "C", 90, "e"),
        ]
        for col, text, width, anchor in headings:
            self.curve_rows_tree.heading(col, text=text)
            self.curve_rows_tree.column(col, width=width, anchor=anchor)
        curves_frame.columnconfigure(0, weight=1)
        curves_frame.rowconfigure(0, weight=1)
        curve_scroll_y = ttk.Scrollbar(curves_frame, orient=tk.VERTICAL, command=self.curve_rows_tree.yview)
        curve_scroll_x = ttk.Scrollbar(curves_frame, orient=tk.HORIZONTAL, command=self.curve_rows_tree.xview)
        self.curve_rows_tree.configure(yscrollcommand=curve_scroll_y.set, xscrollcommand=curve_scroll_x.set)
        self.curve_rows_tree.bind("<Double-1>", self.on_curve_rows_double_click)
        self.curve_rows_tree.bind("<F2>", self.on_curve_rows_f2)
        self.curve_rows_tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=(8, 0))
        curve_scroll_y.grid(row=0, column=1, sticky="ns", pady=(8, 0))
        curve_scroll_x.grid(row=1, column=0, sticky="ew", padx=(8, 0), pady=(0, 8))

        self.aux_floats_tree = ttk.Treeview(
            aux_float_frame,
            columns=("rel", "off", "float", "u32", "note"),
            show="headings",
            height=8,
        )
        aux_float_headings = [
            ("rel", "Rel Off", 80, "e"),
            ("off", "File Off", 110, "e"),
            ("float", "Float", 140, "e"),
            ("u32", "Raw U32", 110, "e"),
            ("note", "Research note", 420, "w"),
        ]
        for col, text, width, anchor in aux_float_headings:
            self.aux_floats_tree.heading(col, text=text)
            self.aux_floats_tree.column(col, width=width, anchor=anchor)
        aux_float_frame.columnconfigure(0, weight=1)
        aux_float_frame.rowconfigure(0, weight=1)
        aux_float_scroll_y = ttk.Scrollbar(aux_float_frame, orient=tk.VERTICAL, command=self.aux_floats_tree.yview)
        aux_float_scroll_x = ttk.Scrollbar(aux_float_frame, orient=tk.HORIZONTAL, command=self.aux_floats_tree.xview)
        self.aux_floats_tree.configure(yscrollcommand=aux_float_scroll_y.set, xscrollcommand=aux_float_scroll_x.set)
        self.aux_floats_tree.bind("<Double-1>", self.on_aux_float_double_click)
        self.aux_floats_tree.bind("<F2>", self.on_aux_float_f2)
        self.aux_floats_tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=(8, 0))
        aux_float_scroll_y.grid(row=0, column=1, sticky="ns", pady=(8, 0))
        aux_float_scroll_x.grid(row=1, column=0, sticky="ew", padx=(8, 0), pady=(0, 8))

        self._aux_float_table_editor = None
        self._range_table_editor = None

        self.raw_text = tk.Text(
            raw_frame,
            bg=self.PANEL,
            fg=self.FG,
            insertbackground=self.FG,
            relief=tk.FLAT,
            wrap=tk.NONE,
        )
        raw_frame.columnconfigure(0, weight=1)
        raw_frame.rowconfigure(0, weight=1)
        raw_scroll_y = ttk.Scrollbar(raw_frame, orient=tk.VERTICAL, command=self.raw_text.yview)
        raw_scroll_x = ttk.Scrollbar(raw_frame, orient=tk.HORIZONTAL, command=self.raw_text.xview)
        self.raw_text.configure(yscrollcommand=raw_scroll_y.set, xscrollcommand=raw_scroll_x.set)
        self.raw_text.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=(8, 0))
        raw_scroll_y.grid(row=0, column=1, sticky="ns", pady=(8, 0))
        raw_scroll_x.grid(row=1, column=0, sticky="ew", padx=(8, 0), pady=(0, 8))

        self.set_summary("Open a vehicle .pck through File > Open PCK.")
        self.update_undo_redo_state()

    def _build_edit_panel(self, parent):
        """Top field editor matching the intended PCK-first workflow."""
        outer = ttk.Frame(parent, style="Panel.TFrame")
        outer.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(8, 4))

        fields = ttk.Frame(outer, style="Panel.TFrame")
        fields.pack(side=tk.LEFT, padx=(12, 16), pady=10)

        def make_field(col: int, label_var: tk.StringVar, value_var: tk.StringVar, width: int, paste_cmd, copy_cmd):
            ttk.Label(fields, textvariable=label_var, style="TLabel", font=("Consolas", 12, "bold")).grid(row=0, column=col * 3, columnspan=3, sticky="w", padx=(0, 12), pady=(0, 4))
            entry = ttk.Entry(fields, textvariable=value_var, width=width, font=("Consolas", 12))
            entry.grid(row=1, column=col * 3, sticky="ew", padx=(0, 8))
            entry.bind("<Return>", lambda _event: self.apply_top_fields_from_vars())
            entry.bind("<FocusOut>", lambda _event: self.apply_top_fields_from_vars(silent=True))
            ttk.Button(fields, text="copy", command=copy_cmd).grid(row=1, column=col * 3 + 1, padx=(0, 4))
            ttk.Button(fields, text="paste", command=paste_cmd).grid(row=1, column=col * 3 + 2, padx=(0, 16))
            return entry

        self.field1_entry = make_field(0, self.edit_field1_label, self.edit_field1, 22, lambda: self.paste_single_field(1), lambda: self.copy_single_field(1))
        self.field2_entry = make_field(1, self.edit_field2_label, self.edit_field2, 12, lambda: self.paste_single_field(2), lambda: self.copy_single_field(2))
        self.field3_entry = make_field(2, self.edit_field3_label, self.edit_field3, 12, lambda: self.paste_single_field(3), lambda: self.copy_single_field(3))

        actions = ttk.Frame(outer, style="Panel.TFrame")
        actions.pack(side=tk.LEFT, padx=(8, 8), pady=10)
        ttk.Button(actions, text="copy all fields", command=self.copy_all_fields).pack(anchor="w", pady=(2, 6))
        ttk.Button(actions, text="paste all fields", command=self.paste_all_fields).pack(anchor="w")
        ttk.Label(actions, textvariable=self.editor_status, style="Muted.TLabel").pack(anchor="w", pady=(8, 0))

    def _build_curve_controls(self, parent):
        controls = ttk.Frame(parent, style="Panel.TFrame")
        controls.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(0, 4))

        ttk.Label(controls, text="Curve visibility:", style="Muted.TLabel").pack(side=tk.LEFT, padx=(8, 4), pady=3)
        for key, label, _, _ in CURVE_FIELDS:
            cb = ttk.Checkbutton(controls, text=label, variable=self.curve_visible[key], command=self.redraw_and_refresh_curve_table)
            cb.pack(side=tk.LEFT, padx=3, pady=3)

        ttk.Label(controls, text="Scale:", style="Muted.TLabel").pack(side=tk.LEFT, padx=(16, 4), pady=3)
        ttk.Radiobutton(controls, text="Global 0-1", variable=self.curve_scale_mode, value="global", command=self.redraw_curve_canvas).pack(side=tk.LEFT, padx=2, pady=3)
        ttk.Radiobutton(controls, text="Fit to view", variable=self.curve_scale_mode, value="fit", command=self.redraw_curve_canvas).pack(side=tk.LEFT, padx=2, pady=3)

        ttk.Label(controls, text="Draw:", style="Muted.TLabel").pack(side=tk.LEFT, padx=(16, 4), pady=3)
        ttk.Radiobutton(controls, text="Polynomial", variable=self.curve_draw_mode, value="poly", command=self.redraw_curve_canvas).pack(side=tk.LEFT, padx=2, pady=3)
        ttk.Radiobutton(controls, text="Linear", variable=self.curve_draw_mode, value="linear", command=self.redraw_curve_canvas).pack(side=tk.LEFT, padx=2, pady=3)

        ttk.Checkbutton(controls, text="Point labels", variable=self.show_point_labels, command=self.redraw_curve_canvas).pack(side=tk.LEFT, padx=(12, 3), pady=3)
        ttk.Checkbutton(controls, text="Control pts", variable=self.show_control_points, command=self.redraw_curve_canvas).pack(side=tk.LEFT, padx=3, pady=3)
        ttk.Checkbutton(controls, text="Edit points", variable=self.edit_curve_points, command=self.redraw_curve_canvas).pack(side=tk.LEFT, padx=3, pady=3)

        ttk.Button(controls, text="copy active curve points", command=self.copy_active_curve_points).pack(side=tk.RIGHT, padx=(8, 8), pady=3)
        ttk.Button(controls, text="paste active curve points", command=self.paste_active_curve_points).pack(side=tk.RIGHT, padx=4, pady=3)

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Open PCK...", command=self.open_pck_dialog, accelerator="Ctrl+O")
        file_menu.add_command(label="Reload", command=self.reload_current, accelerator="F5")
        file_menu.add_command(label="Save", command=self.save_current_pck, accelerator="Ctrl+S")
        file_menu.add_command(label="Save As...", command=self.save_pck_as, accelerator="Ctrl+Shift+S")
        file_menu.add_separator()
        file_menu.add_command(label="Import Audio Conservative...", command=self.import_audio_from_pck_dialog)
        file_menu.add_command(label="Import Audio Experimental...", command=self.import_audio_experimental_from_pck_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="Export Parsed JSON...", command=self.export_parsed_json)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=False)
        edit_menu.add_command(label="Undo", command=self.undo_last_action, accelerator="Ctrl+Z")
        edit_menu.add_command(label="Redo", command=self.redo_last_action, accelerator="Ctrl+Y")
        menubar.add_cascade(label="Edit", menu=edit_menu)

        view_menu = tk.Menu(menubar, tearoff=False)
        for key, label, _, _ in CURVE_FIELDS:
            view_menu.add_checkbutton(label=label, variable=self.curve_visible[key], command=self.redraw_curve_canvas)
        view_menu.add_separator()
        scale_menu = tk.Menu(view_menu, tearoff=False)
        scale_menu.add_radiobutton(label="Global 0-1", variable=self.curve_scale_mode, value="global", command=self.redraw_curve_canvas)
        scale_menu.add_radiobutton(label="Fit to view", variable=self.curve_scale_mode, value="fit", command=self.redraw_curve_canvas)
        view_menu.add_cascade(label="Graph Scale", menu=scale_menu)

        draw_menu = tk.Menu(view_menu, tearoff=False)
        draw_menu.add_radiobutton(label="Polynomial", variable=self.curve_draw_mode, value="poly", command=self.redraw_curve_canvas)
        draw_menu.add_radiobutton(label="Linear", variable=self.curve_draw_mode, value="linear", command=self.redraw_curve_canvas)
        view_menu.add_cascade(label="Curve Draw Mode", menu=draw_menu)

        view_menu.add_checkbutton(label="Point labels", variable=self.show_point_labels, command=self.redraw_curve_canvas)
        view_menu.add_checkbutton(label="Control points", variable=self.show_control_points, command=self.redraw_curve_canvas)
        view_menu.add_checkbutton(label="Edit points", variable=self.edit_curve_points, command=self.redraw_curve_canvas)
        menubar.add_cascade(label="View", menu=view_menu)

        self.root.config(menu=menubar)
        self.root.bind("<Control-o>", lambda _event: self.open_pck_dialog())
        self.root.bind("<F5>", lambda _event: self.reload_current())
        self.root.bind("<Control-s>", lambda _event: self.save_current_pck())
        self.root.bind("<Control-S>", lambda _event: self.save_pck_as())
        self.root.bind_all("<Control-z>", lambda _event: self.undo_last_action())
        self.root.bind_all("<Control-Z>", lambda _event: self.undo_last_action())
        self.root.bind_all("<Control-y>", lambda _event: self.redo_last_action())
        self.root.bind_all("<Control-Y>", lambda _event: self.redo_last_action())

    def open_pck_dialog(self):
        path = filedialog.askopenfilename(
            title="Open MC3 vehicle PCK",
            filetypes=[("PCK files", "*.pck"), ("All files", "*.*")],
        )
        if path:
            self.load_pck(Path(path))

    def reload_current(self):
        if not self.current_path:
            return
        self.load_pck(self.current_path)

    def load_pck(self, path: Path):
        try:
            parser = PckAudioParser(path)
            doc = parser.parse()
        except Exception as exc:
            messagebox.showerror("PCK parse failed", str(exc))
            return

        self.doc = doc
        self.current_path = path
        self.dirty = False
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.selected = None
        self.root.title(f"MC3 Audio Curve GUI v0.19 - {path.name}")
        self.rebuild_tree()
        self.show_document_summary()
        self.clear_tables()
        self.redraw_curve_canvas()
        self.update_undo_redo_state()

    def export_parsed_json(self):
        if not self.doc:
            messagebox.showinfo("No PCK loaded", "Open a PCK first.")
            return
        default_name = self.doc.path.with_suffix(".audio_parsed.json").name
        path = filedialog.asksaveasfilename(
            title="Export parsed audio JSON",
            initialdir=str(self.doc.path.parent),
            initialfile=default_name,
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.doc.to_dict(), f, indent=2, ensure_ascii=False)
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        messagebox.showinfo("Export complete", f"Parsed audio JSON saved:\n{path}")

    def rebuild_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.item_map.clear()
        if not self.doc:
            return

        root_id = self.tree.insert("", "end", text=self.doc.label, open=True)
        self.item_map[root_id] = self.doc

        categories: Dict[str, List[Any]] = {}
        for block in self.doc.blocks:
            categories.setdefault(block.category, []).append(block)
        for raw in self.doc.raw_aux_blocks:
            categories.setdefault(raw.category, []).append(raw)
        if self.doc.commons:
            categories.setdefault("Common", []).extend(self.doc.commons)

        # Main continuous engine/exhaust layers stay top-level.
        for cat in ("Engine", "Exhaust"):
            if cat not in categories:
                continue
            cat_id = self.tree.insert(root_id, "end", text=cat, open=cat == "Engine")
            self.item_map[cat_id] = cat
            for item in categories[cat]:
                text = getattr(item, "display_label", getattr(item, "label", str(item)))
                child_id = self.tree.insert(cat_id, "end", text=text, open=False)
                self.item_map[child_id] = item

        # Collapse all auxiliary groups under one parent so the tree stays readable.
        aux_order = [
            "Auxiliary / TurboBlower",
            "Auxiliary / Impact",
            "Auxiliary / BigAir",
            "Auxiliary / Wind",
            "Auxiliary / Horn",
            "Auxiliary / Powerup",
            "Auxiliary / Fire",
            "Auxiliary / Wheel",
            "Auxiliary / Suspension",
            "Auxiliary / Gearshift",
            "Auxiliary / Backfire",
            "Auxiliary / Unknown",
        ]
        aux_cats = [cat for cat in aux_order if cat in categories]
        aux_cats.extend(sorted(cat for cat in categories if cat.startswith("Auxiliary / ") and cat not in aux_cats))
        if aux_cats:
            aux_id = self.tree.insert(root_id, "end", text="Auxiliary", open=False)
            self.item_map[aux_id] = "Auxiliary"
            for cat in aux_cats:
                cat_id = self.tree.insert(aux_id, "end", text=category_display_name(cat), open=False)
                self.item_map[cat_id] = cat
                for item in categories[cat]:
                    text = getattr(item, "display_label", getattr(item, "label", str(item)))
                    child_id = self.tree.insert(cat_id, "end", text=text, open=False)
                    self.item_map[child_id] = item

        if "Common" in categories:
            cat_id = self.tree.insert(root_id, "end", text="Common", open=False)
            self.item_map[cat_id] = "Common"
            for item in categories["Common"]:
                text = getattr(item, "display_label", getattr(item, "label", str(item)))
                child_id = self.tree.insert(cat_id, "end", text=text, open=False)
                self.item_map[child_id] = item

    def on_tree_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        self.selected_tree_id = sel[0]
        item = self.item_map.get(sel[0])
        self.selected = item
        self._active_curve_handle = None
        self.refresh_top_editor_fields()
        if isinstance(item, PckAudioDocument):
            self.show_document_summary()
        elif isinstance(item, AudioBlock):
            self.show_audio_block(item)
        elif isinstance(item, RawAuxBlock):
            self.show_raw_aux(item)
        elif isinstance(item, CommonBlock):
            self.show_common(item)
        elif isinstance(item, str):
            self.show_category(item)
        self.redraw_curve_canvas()

    def set_summary(self, text: str):
        self.summary.configure(state=tk.NORMAL)
        self.summary.delete("1.0", tk.END)
        self.summary.insert(tk.END, text)
        self.summary.configure(state=tk.DISABLED)

    def set_raw_text(self, text: str):
        self.raw_text.configure(state=tk.NORMAL)
        self.raw_text.delete("1.0", tk.END)
        self.raw_text.insert(tk.END, text)
        self.raw_text.configure(state=tk.DISABLED)

    def clear_tables(self):
        self.close_range_table_editor()
        self.close_curve_table_editor()
        self.close_aux_float_table_editor()
        for tree in (self.ranges_tree, self.curve_rows_tree, getattr(self, "aux_floats_tree", None)):
            if tree is not None:
                tree.delete(*tree.get_children())
        self.set_raw_text("")

    # ------------------------------------------------------------------
    # Editing / clipboard helpers
    # ------------------------------------------------------------------

    def set_status(self, text: str):
        self.editor_status.set(text)

    def update_dirty_state(self):
        title_suffix = " *modified" if self.dirty else ""
        if self.current_path:
            self.root.title(f"MC3 Audio Curve GUI v0.19 - {self.current_path.name}{title_suffix}")
        if hasattr(self, "dirty_label"):
            self.dirty_label.configure(text="modified" if self.dirty else "")

    def mark_dirty(self):
        self.dirty = True
        self.update_dirty_state()

    def current_selection_rel(self) -> Optional[int]:
        return getattr(self.selected, "root_rel", None) if self.selected is not None else None

    def _make_history_snapshot(self, label: str) -> Optional[Dict[str, Any]]:
        if not self.doc:
            return None
        return {
            "label": label,
            "buf": bytes(self.doc.buf),
            "dirty": bool(self.dirty),
            "selection_rel": self.current_selection_rel(),
        }

    def push_undo(self, label: str) -> Optional[Dict[str, Any]]:
        snapshot = self._make_history_snapshot(label)
        if snapshot is None:
            return None
        if self.undo_stack and self.undo_stack[-1].get("buf") == snapshot["buf"]:
            return None
        self.undo_stack.append(snapshot)
        if len(self.undo_stack) > self.history_limit:
            self.undo_stack = self.undo_stack[-self.history_limit:]
        self.redo_stack.clear()
        self.update_undo_redo_state()
        return snapshot

    def update_undo_redo_state(self):
        undo_state = "normal" if self.undo_stack else "disabled"
        redo_state = "normal" if self.redo_stack else "disabled"
        if hasattr(self, "undo_button"):
            self.undo_button.configure(state=undo_state)
        if hasattr(self, "redo_button"):
            self.redo_button.configure(state=redo_state)

    def _restore_history_snapshot(self, snapshot: Dict[str, Any], status_prefix: str):
        if not self.doc:
            return
        self.doc.buf = bytearray(snapshot["buf"])
        self.dirty = bool(snapshot.get("dirty", True))
        keep_rel = snapshot.get("selection_rel", None)
        self.reparse_current_buffer(keep_selection_rel=keep_rel)
        self.update_undo_redo_state()
        label = str(snapshot.get("label", "edit"))
        self.set_status(f"{status_prefix}: {label}")

    def undo_last_action(self):
        if not self.doc or not self.undo_stack:
            self.set_status("nothing to undo")
            return "break"
        current = self._make_history_snapshot("redo state")
        if current is not None:
            self.redo_stack.append(current)
            if len(self.redo_stack) > self.history_limit:
                self.redo_stack = self.redo_stack[-self.history_limit:]
        snapshot = self.undo_stack.pop()
        self._restore_history_snapshot(snapshot, "undo")
        return "break"

    def redo_last_action(self):
        if not self.doc or not self.redo_stack:
            self.set_status("nothing to redo")
            return "break"
        current = self._make_history_snapshot("undo state")
        if current is not None:
            self.undo_stack.append(current)
            if len(self.undo_stack) > self.history_limit:
                self.undo_stack = self.undo_stack[-self.history_limit:]
        snapshot = self.redo_stack.pop()
        self._restore_history_snapshot(snapshot, "redo")
        return "break"

    def clear_history(self):
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.update_undo_redo_state()

    def clipboard_set(self, text: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update_idletasks()

    def clipboard_get(self) -> str:
        try:
            return self.root.clipboard_get()
        except Exception:
            return ""

    def set_field_state(self, field: int, enabled: bool):
        entry = getattr(self, f"field{field}_entry", None)
        if entry is not None:
            entry.configure(state="normal" if enabled else "disabled")

    def on_top_rpm_preview_changed(self, *_args):
        """Preview-only graph refresh for top Min/Max RPM text edits.

        The graph uses these fields only for the RPM hint labels under the
        normalized X axis. This callback intentionally does not write to the
        PCK and does not create undo snapshots.
        """
        if isinstance(getattr(self, "selected", None), AudioBlock):
            canvas = getattr(self, "curve_canvas", None)
            if canvas is not None:
                try:
                    self.redraw_curve_canvas()
                except tk.TclError:
                    pass

    def _display_rpm_bounds_for_graph(self, block: AudioBlock) -> Tuple[float, float]:
        """Return Min/Max RPM used for the graph labels.

        If the selected AudioBlockFull is currently being edited in the top
        fields, prefer the not-yet-applied text values so the axis labels update
        immediately while typing. Invalid partial text falls back to committed
        PCK values.
        """
        min_rpm = float(block.min_rpm_rec)
        max_rpm = float(block.max_rpm_rec)
        if self.selected is block:
            try:
                min_candidate = float(self.edit_field2.get().strip())
                max_candidate = float(self.edit_field3.get().strip())
                if max_candidate > min_candidate:
                    return min_candidate, max_candidate
            except Exception:
                pass
        return min_rpm, max_rpm

    def refresh_top_editor_fields(self):
        item = self.selected
        self.editor_status.set("")
        for i in (1, 2, 3):
            self.set_field_state(i, True)

        if isinstance(item, AudioBlock):
            self.edit_field1_label.set("Banks File Name")
            self.edit_field2_label.set("Minimum RPM")
            self.edit_field3_label.set("Maximum RPM")
            self.edit_field1.set(item.bank_name)
            self.edit_field2.set(f"{item.min_rpm_rec:.6g}")
            self.edit_field3.set(f"{item.max_rpm_rec:.6g}")
            return

        if isinstance(item, RawAuxBlock):
            self.edit_field1_label.set("String 0")
            self.edit_field2_label.set("String 1")
            self.edit_field3_label.set("Not mapped")
            self.edit_field1.set(item.possible_name_0)
            self.edit_field2.set(item.possible_name_1)
            self.edit_field3.set("")
            self.set_field_state(3, False)
            return

        self.edit_field1_label.set("Banks File Name")
        self.edit_field2_label.set("Minimum RPM")
        self.edit_field3_label.set("Maximum RPM")
        self.edit_field1.set("")
        self.edit_field2.set("")
        self.edit_field3.set("")
        for i in (1, 2, 3):
            self.set_field_state(i, False)

    def copy_single_field(self, field: int):
        value = {1: self.edit_field1, 2: self.edit_field2, 3: self.edit_field3}[field].get()
        self.clipboard_set(value)
        self.set_status(f"copied field {field}")

    def paste_single_field(self, field: int):
        value = self.clipboard_get().strip()
        if field == 1:
            self.edit_field1.set(value)
        elif field == 2:
            self.edit_field2.set(value)
        else:
            self.edit_field3.set(value)
        self.apply_top_fields_from_vars()

    def current_field_payload(self) -> Dict[str, Any]:
        item = self.selected
        if isinstance(item, AudioBlock):
            return {
                "format": "MC3_AUDIO_FIELDS_V1",
                "kind": "AudioBlockFull",
                "bank_name": self.edit_field1.get(),
                "min_rpm_rec": self.edit_field2.get(),
                "max_rpm_rec": self.edit_field3.get(),
            }
        if isinstance(item, RawAuxBlock):
            return {
                "format": "MC3_AUDIO_FIELDS_V1",
                "kind": "RawAuxStrings",
                "string_0": self.edit_field1.get(),
                "string_1": self.edit_field2.get(),
            }
        return {"format": "MC3_AUDIO_FIELDS_V1", "kind": "none"}

    def copy_all_fields(self):
        self.clipboard_set(json.dumps(self.current_field_payload(), indent=2, ensure_ascii=False))
        self.set_status("copied all fields")

    def paste_all_fields(self):
        text = self.clipboard_get().strip()
        if not text:
            return
        try:
            payload = json.loads(text)
        except Exception:
            # Fallback: split tab/comma/newline values in the visible field order.
            parts = [p.strip() for p in text.replace("\t", "\n").replace(",", "\n").splitlines() if p.strip()]
            if len(parts) >= 1:
                self.edit_field1.set(parts[0])
            if len(parts) >= 2:
                self.edit_field2.set(parts[1])
            if len(parts) >= 3:
                self.edit_field3.set(parts[2])
            self.apply_top_fields_from_vars()
            return

        if isinstance(self.selected, AudioBlock):
            if "bank_name" in payload:
                self.edit_field1.set(str(payload.get("bank_name", "")))
            if "min_rpm_rec" in payload:
                self.edit_field2.set(str(payload.get("min_rpm_rec", "")))
            if "max_rpm_rec" in payload:
                self.edit_field3.set(str(payload.get("max_rpm_rec", "")))
            self.apply_top_fields_from_vars()
        elif isinstance(self.selected, RawAuxBlock):
            if "string_0" in payload:
                self.edit_field1.set(str(payload.get("string_0", "")))
            if "string_1" in payload:
                self.edit_field2.set(str(payload.get("string_1", "")))
            self.apply_top_fields_from_vars()

    def _sample_prefix_replacement_changes(self, block: AudioBlock, new_bank: str) -> List[Tuple[AudioRange, str, str]]:
        """Return range sample-name prefix changes implied by a bank_name edit.

        AudioBlockFull.bank_name and range sample_name prefixes are separate fields in
        the PCK. Editing only +0x04 changes the block bank label but often leaves the
        audible sample references pointing at the old bank, e.g. E_BMW_328i2:LOW_ENGINE.
        For practical GUI editing, a bank edit should keep the receiver suffixes
        (LOW_ENGINE, MID_TAIL, etc.) and update the prefix before ':'.
        """
        changes: List[Tuple[AudioRange, str, str]] = []
        bank = (new_bank or "").strip()
        if not bank:
            return changes
        for rr in block.ranges:
            old_sample = read_fixed_string(self.doc.buf, rr.file_off, 0x20) if self.doc else rr.sample_name
            if ":" not in old_sample:
                continue
            _old_prefix, suffix = old_sample.split(":", 1)
            if not suffix:
                continue
            new_sample = f"{bank}:{suffix}"
            if new_sample != old_sample:
                if len(new_sample.encode("ascii", errors="replace")) > 31:
                    raise ValueError(f"sample_name too long after bank prefix update: {new_sample!r}")
                changes.append((rr, old_sample, new_sample))
        return changes

    def apply_top_fields_from_vars(self, silent: bool = False):
        item = self.selected
        if not self.doc or item is None:
            return
        try:
            if isinstance(item, AudioBlock):
                if item.file_off is None:
                    return
                bank = self.edit_field1.get().strip()
                min_rpm = float(self.edit_field2.get().strip())
                max_rpm = float(self.edit_field3.get().strip())
                if len(bank.encode("ascii", errors="replace")) > 31:
                    raise ValueError("bank_name must fit in 31 ASCII chars + terminator")
                old_bank = read_fixed_string(self.doc.buf, item.file_off + 0x04, 0x20)
                old_min = safe_f32(self.doc.buf, item.file_off + 0x24)
                old_max = safe_f32(self.doc.buf, item.file_off + 0x28)
                sample_prefix_changes = self._sample_prefix_replacement_changes(item, bank) if old_bank != bank else []
                if old_bank == bank and old_min == min_rpm and old_max == max_rpm and not sample_prefix_changes:
                    return
                self.push_undo("Edit top fields")
                write_fixed_string(self.doc.buf, item.file_off + 0x04, bank, 0x20)
                write_f32(self.doc.buf, item.file_off + 0x24, min_rpm)
                write_f32(self.doc.buf, item.file_off + 0x28, max_rpm)
                for rr, _old_sample, new_sample in sample_prefix_changes:
                    write_fixed_string(self.doc.buf, rr.file_off, new_sample, 0x20)
                    rr.sample_name = new_sample
                item.bank_name = bank
                item.min_rpm_rec = min_rpm
                item.max_rpm_rec = max_rpm
                self.refresh_selected_tree_text()
                self.fill_ranges_table(item)
                self.fill_raw_for_block(item)
                self.mark_dirty()
                if not silent:
                    if sample_prefix_changes:
                        self.set_status(f"fields applied; {len(sample_prefix_changes)} sample prefix(es) updated")
                    else:
                        self.set_status("fields applied")
                self.redraw_curve_canvas()
                return

            if isinstance(item, RawAuxBlock):
                if item.file_off is None:
                    return
                s0 = self.edit_field1.get().strip()
                s1 = self.edit_field2.get().strip()
                if len(s0.encode("ascii", errors="replace")) > 31 or len(s1.encode("ascii", errors="replace")) > 31:
                    raise ValueError("raw aux strings must fit in 31 ASCII chars + terminator")
                old_s0 = read_fixed_string(self.doc.buf, item.file_off + 0x04, 0x20)
                old_s1 = read_fixed_string(self.doc.buf, item.file_off + 0x24, 0x20)
                if old_s0 == s0 and old_s1 == s1:
                    return
                self.push_undo("Edit auxiliary strings")
                write_fixed_string(self.doc.buf, item.file_off + 0x04, s0, 0x20)
                write_fixed_string(self.doc.buf, item.file_off + 0x24, s1, 0x20)
                item.possible_name_0 = s0
                item.possible_name_1 = s1
                item.found_string_entries = read_loose_ascii_string_entries(self.doc.buf, item.file_off + 0x04, 0x200 if item.kind == "raw_bundle" else 0x100)
                found = []
                for entry in item.found_string_entries:
                    text = str(entry.get("text", ""))
                    if text and text not in found:
                        found.append(text)
                item.found_strings = found
                self.refresh_selected_tree_text()
                self.mark_dirty()
                if not silent:
                    self.set_status("strings applied")
                self.show_raw_aux(item)
        except Exception as exc:
            if not silent:
                messagebox.showerror("Edit failed", str(exc))
            else:
                self.set_status(f"edit pending: {exc}")

    def refresh_selected_tree_text(self):
        if not self.selected_tree_id or not self.selected:
            return
        text = getattr(self.selected, "display_label", getattr(self.selected, "label", str(self.selected)))
        try:
            self.tree.item(self.selected_tree_id, text=text)
        except Exception:
            pass

    def visible_curve_keys(self) -> List[str]:
        return [key for key, _label, _ptr_key, _rel in CURVE_FIELDS if self.curve_visible[key].get()]

    def copy_active_curve_points(self):
        item = self.selected
        if not isinstance(item, AudioBlock):
            messagebox.showinfo("No curve block", "Select an AudioBlockFull item first.")
            return
        curves = {}
        for key in self.visible_curve_keys():
            curve = item.curves.get(key)
            if curve and curve.rows:
                curves[key] = {
                    "label": curve.label,
                    "point_count": curve.point_count,
                    "rows": curve.rows[:max(0, int(curve.point_count))],
                }
        payload = {
            "format": "MC3_AUDIO_CURVE_ROWS_V1",
            "source_pck": str(item.doc.path.name),
            "source_block": item.label,
            "curves": curves,
        }
        self.clipboard_set(json.dumps(payload, indent=2, ensure_ascii=False))
        self.set_status(f"copied {len(curves)} active curve(s)")

    def paste_active_curve_points(self):
        item = self.selected
        if not isinstance(item, AudioBlock):
            messagebox.showinfo("No curve block", "Select an AudioBlockFull item first.")
            return
        text = self.clipboard_get().strip()
        if not text:
            return
        try:
            payload = json.loads(text)
            curves_payload = payload.get("curves", {})
        except Exception as exc:
            messagebox.showerror("Curve paste failed", f"Clipboard does not contain MC3 curve JSON.\n{exc}")
            return

        self.push_undo("Paste active curve points")
        changed = 0
        skipped: List[str] = []
        for key in self.visible_curve_keys():
            if key not in curves_payload:
                continue
            target = item.curves.get(key)
            if key == "high_pitch_curve" and not (target and int(target.point_count) > 0):
                skipped.append("high_pitch_curve: target has no active High Pitch")
                continue
            if not target or target.points_file_off is None:
                skipped.append(f"{key}: target missing points")
                continue
            rows = curves_payload[key].get("rows", [])
            capacity = curve_allocated_row_capacity(len(item.doc.buf), target)
            if len(rows) > capacity:
                skipped.append(f"{key}: row count {len(rows)} exceeds target capacity {capacity}")
                continue
            try:
                normalized_rows: List[List[float]] = []
                for i, row in enumerate(rows):
                    if len(row) < CURVE_POINT_FLOATS:
                        raise ValueError(f"row {i} has {len(row)} floats")
                    values = [float(v) for v in row[:CURVE_POINT_FLOATS]]
                    normalized_rows.append(values)
                    row_off = target.points_file_off + i * CURVE_POINT_SIZE
                    for j, value in enumerate(values):
                        write_f32(item.doc.buf, row_off + j * 4, value)
                if target.file_off is not None:
                    write_u32(item.doc.buf, target.file_off + 0x14, len(normalized_rows))
                    target.point_count = len(normalized_rows)
                target.rows = normalized_rows
                changed += 1
            except Exception as exc:
                skipped.append(f"{key}: {exc}")

        if changed:
            self.mark_dirty()
            self.fill_curve_rows_table(item)
            self.redraw_curve_canvas()
        msg = f"pasted {changed} active curve(s)"
        if skipped:
            msg += " | skipped: " + "; ".join(skipped[:4])
        self.set_status(msg)

    def import_audio_from_pck_dialog(self):
        if not self.doc:
            messagebox.showinfo("No target PCK loaded", "Open the target PCK first, then import from a donor PCK.")
            return
        donor_path = filedialog.askopenfilename(
            title="Import audio from donor PCK",
            initialdir=str(self.doc.path.parent),
            filetypes=[("PCK files", "*.pck"), ("All files", "*.*")],
        )
        if not donor_path:
            return
        if Path(donor_path).resolve() == self.doc.path.resolve():
            messagebox.showinfo("Same file", "The donor PCK is the same as the currently open target PCK.")
            return
        if not messagebox.askyesno(
            "Import Audio From PCK",
            "This will patch the currently open PCK in memory using the donor PCK.\n\n"
            "Transferred fields:\n"
            "- AudioBlockFull bank_name\n"
            "- active range sample prefix before ':'\n"
            "- raw auxiliary fixed ASCII audio strings\n"
            "- conservative slot RPM ranges\n"
            "- upshift/downshift event curves only\n\n"
            "Main Engine/Tail blocks are paired by detected sample role first, not blindly by slot number.\n"
            "It will not copy continuous volume/pitch/high-pitch curves, distances, warble, boost, active range counts, pointers or whole raw blocks.\n\n"
            "Continue?",
        ):
            return
        try:
            donor_doc = PckAudioParser(Path(donor_path)).parse()
            self.push_undo(f"Import Conservative from {Path(donor_path).name}")
            report = self.import_audio_from_document(donor_doc)
        except Exception as exc:
            messagebox.showerror("Import failed", str(exc))
            return

        self.mark_dirty()
        self.reparse_current_buffer(keep_selection_rel=getattr(self.selected, "root_rel", None))
        summary = (
            f"Imported audio from {Path(donor_path).name}\n\n"
            f"AudioBlockFull fields changed: {report['block_changed']}\n"
            f"Sample prefixes changed: {report['sample_changed']}\n"
            f"Auxiliary strings changed: {report['aux_changed']}\n"
            f"RPM fields changed: {report['rpm_changed']}\n"
            f"Shift curve fields changed: {report.get('shift_curve_changed', 0)}\n"
            f"Role-paired Engine/Tail blocks: {report.get('pair_role_matched', 0)}\n"
            f"Root-rel paired aux/unknown blocks: {report.get('pair_root_rel_matched', 0)}\n"
            f"Skipped: {report['skipped']}\n\n"
            "Continuous volume/pitch/high-pitch curves, distances, warble, boost, active range counts, pointers and complete raw blocks were not copied."
        )
        if report["warnings"]:
            summary += "\n\nWarnings:\n- " + "\n- ".join(report["warnings"][:10])
        messagebox.showinfo("Import complete", summary)

    def import_audio_experimental_from_pck_dialog(self):
        if not self.doc:
            messagebox.showinfo("No target PCK loaded", "Open the target PCK first, then import from a donor PCK.")
            return
        donor_path = filedialog.askopenfilename(
            title="Import experimental audio from donor PCK",
            initialdir=str(self.doc.path.parent),
            filetypes=[("PCK files", "*.pck"), ("All files", "*.*")],
        )
        if not donor_path:
            return
        if Path(donor_path).resolve() == self.doc.path.resolve():
            messagebox.showinfo("Same file", "The donor PCK is the same as the currently open target PCK.")
            return
        if not messagebox.askyesno(
            "Import Audio Experimental",
            "This will patch the currently open PCK in memory using the donor PCK.\n\n"
            "Experimental mode tries to copy the maximum currently addressable audio data without copying donor pointers.\n\n"
            "Transferred when layouts match:\n"
            "- exact bank_name and exact range sample_name fields\n"
            "- min/max RPM, near/far distance, active range count, warble, boost and known block scalars\n"
            "- all existing allocated range RPM fields\n"
            "- curve rows and curve headers where the target has enough allocated rows\n"
            "- high-pitch target gate: copies High Pitch only when the receiving slot already has an active High Pitch curve\n"
            "- raw auxiliary strings and plausible float parameters at matching relative offsets\n"
            "- common block float parameters\n\n"
            "Still NOT copied: donor virtual pointers, root pointers, range table pointers, curve pointers, complete raw blocks, or file-size-changing data.\n\n"
            "This mode is intentionally risky for runtime behavior. Save As first if you want a separate test file.\n\n"
            "Continue?",
        ):
            return
        try:
            donor_doc = PckAudioParser(Path(donor_path)).parse()
            self.push_undo(f"Import Experimental from {Path(donor_path).name}")
            report = self.import_audio_from_document_experimental(donor_doc)
        except Exception as exc:
            messagebox.showerror("Experimental import failed", str(exc))
            return

        self.mark_dirty()
        self.reparse_current_buffer(keep_selection_rel=getattr(self.selected, "root_rel", None))
        summary = (
            f"Experimental import from {Path(donor_path).name}\n\n"
            f"Block scalar/string fields changed: {report['block_changed']}\n"
            f"Range fields changed: {report['range_changed']}\n"
            f"Curve header fields changed: {report['curve_header_changed']}\n"
            f"Curve row floats changed: {report['curve_row_changed']}\n"
            f"Raw auxiliary strings changed: {report['aux_string_changed']}\n"
            f"Raw auxiliary floats changed: {report['aux_float_changed']}\n"
            f"Common fields changed: {report['common_changed']}\n"
            f"High Pitch preserved because donor had none: {report.get('high_pitch_guarded', 0)}\n"
            f"High Pitch skipped because target had none: {report.get('high_pitch_skipped_target_empty', 0)}\n"
            f"Role-paired Engine/Tail blocks: {report.get('pair_role_matched', 0)}\n"
            f"Root-rel paired aux/unknown blocks: {report.get('pair_root_rel_matched', 0)}\n"
            f"Fallback root-rel pairs: {report.get('pair_fallback_root_rel', 0)}\n"
            f"Skipped: {report['skipped']}\n\n"
            "Donor virtual pointers and complete raw blocks were not copied. Save/Save As is still required to write the edited PCK."
        )
        if report["warnings"]:
            summary += "\n\nWarnings:\n- " + "\n- ".join(report["warnings"][:14])
            if len(report["warnings"]) > 14:
                summary += f"\n- ... {len(report['warnings']) - 14} more"
        messagebox.showinfo("Experimental import complete", summary)

    def import_audio_from_document_experimental(self, donor_doc: PckAudioDocument) -> Dict[str, Any]:
        """Max-addressable donor import for testing.

        This intentionally copies more than the conservative transfer, but still
        avoids anything that would invalidate target pointers. It copies values
        into the target's existing structures. Main engine/exhaust slots are
        matched by detected ENGINE/TAIL sample role first, then ordinal inside
        that role, instead of blindly matching only by slot number. No buffer
        growth, relocation, donor VA pointer copy, or raw-block
        memcpy is performed.
        """
        if not self.doc:
            raise RuntimeError("No target document loaded.")
        target_doc = self.doc
        report: Dict[str, Any] = {
            "block_changed": 0,
            "range_changed": 0,
            "curve_header_changed": 0,
            "curve_row_changed": 0,
            "aux_string_changed": 0,
            "aux_float_changed": 0,
            "common_changed": 0,
            "high_pitch_guarded": 0,
            "high_pitch_skipped_target_empty": 0,
            "curve_count_expanded": 0,
            "curve_resampled": 0,
            "pair_role_matched": 0,
            "pair_root_rel_matched": 0,
            "pair_fallback_root_rel": 0,
            "skipped": 0,
            "warnings": [],
        }

        def write_bytes_if_changed(dst_off: int, raw: bytes, counter: str) -> bool:
            if dst_off < 0 or dst_off + len(raw) > len(target_doc.buf):
                report["skipped"] += 1
                return False
            if bytes(target_doc.buf[dst_off:dst_off + len(raw)]) != raw:
                target_doc.buf[dst_off:dst_off + len(raw)] = raw
                report[counter] += 1
                return True
            return False

        def write_f32_if_changed(dst_off: int, value: float, counter: str) -> bool:
            if not math.isfinite(float(value)):
                report["skipped"] += 1
                return False
            old = safe_f32(target_doc.buf, dst_off)
            if old != float(value):
                write_f32(target_doc.buf, dst_off, float(value))
                report[counter] += 1
                return True
            return False

        def write_u32_if_changed(dst_off: int, value: int, counter: str) -> bool:
            old = safe_u32(target_doc.buf, dst_off)
            value = int(value) & 0xFFFFFFFF
            if old != value:
                write_u32(target_doc.buf, dst_off, value)
                report[counter] += 1
                return True
            return False

        block_pairs, pair_stats, pair_warnings = pair_audio_blocks_for_import(donor_doc.blocks, target_doc.blocks)
        report["pair_role_matched"] = pair_stats.get("role_matched", 0)
        report["pair_root_rel_matched"] = pair_stats.get("root_rel_matched", 0)
        report["pair_fallback_root_rel"] = pair_stats.get("fallback_root_rel", 0)
        report["skipped"] += pair_stats.get("unmatched", 0)
        report["warnings"].extend(pair_warnings)
        for donor, target, pair_mode in block_pairs:
            if target.file_off is None or donor.file_off is None:
                report["skipped"] += 1
                continue
            if target.magic != donor.magic:
                report["warnings"].append(
                    f"{target.label}: AudioBlockFull magic differs target={format_hex(target.magic)} donor={format_hex(donor.magic)}; skipped experimental block scalar/range/curve copy."
                )
                report["skipped"] += 1
                continue

            # Exact bank string, unlike conservative mode which only copies a usable donor bank when present.
            write_bytes_if_changed(
                target.file_off + 0x04,
                bytes(donor_doc.buf[donor.file_off + 0x04:donor.file_off + 0x24]),
                "block_changed",
            )

            donor_high = donor.curves.get("high_pitch_curve")
            target_high = target.curves.get("high_pitch_curve")
            donor_high_active = bool(donor_high is not None and int(donor_high.point_count) > 0)
            target_high_active = bool(target_high is not None and int(target_high.point_count) > 0)
            copy_high_pitch_fields = bool(donor_high_active and target_high_active)
            preserve_target_high_pitch = bool(target_high_active and not donor_high_active)
            skip_target_empty_high_pitch = bool(donor_high_active and not target_high_active)

            # Copy all known scalar fields except pointers. This includes RPM recommendation,
            # distances, active range count, warble, boost and the small unknown/padding tail.
            # Skipped pointer fields: +0x38 range table ptr and +0x88..+0x98 curve ptrs.
            # High Pitch is now target-gated: copy it only if the receiving block already has
            # an active High Pitch curve. If the target does not use High Pitch, preserve its
            # engage gear and do not activate/import donor High Pitch rows.
            if copy_high_pitch_fields:
                scalar_ranges = [
                    (0x24, 0x10),  # min/max RPM recommendation + near/far distance
                    (0x34, 0x04),  # active_range_count
                    (0x3C, 0x2C),  # warble + boost + high_pitch_curve_engage_gear
                    (ENGINE_REV_SOUND_OFF, ENGINE_REV_SOUND_SIZE),  # EngineRevSound[32]
                    (0x9C, 0x04),  # pad/sentinel after curve pointer table
                ]
            else:
                scalar_ranges = [
                    (0x24, 0x10),  # min/max RPM recommendation + near/far distance
                    (0x34, 0x04),  # active_range_count
                    (0x3C, 0x28),  # warble + boost, but not high_pitch_curve_engage_gear
                    (ENGINE_REV_SOUND_OFF, ENGINE_REV_SOUND_SIZE),  # EngineRevSound[32]
                    (0x9C, 0x04),  # pad/sentinel after curve pointer table
                ]
                if preserve_target_high_pitch:
                    report["high_pitch_guarded"] += 1
                    report["warnings"].append(
                        f"{target.label}: donor High Pitch curve is empty while target has {target_high.point_count} row(s); preserved target High Pitch curve and engage gear."
                    )
                elif skip_target_empty_high_pitch:
                    report["high_pitch_skipped_target_empty"] += 1
                    report["warnings"].append(
                        f"{target.label}: target has no active High Pitch curve; skipped donor High Pitch rows and engage gear."
                    )
            for rel, size in scalar_ranges:
                raw = bytes(donor_doc.buf[donor.file_off + rel:donor.file_off + rel + size])
                write_bytes_if_changed(target.file_off + rel, raw, "block_changed")

            # Exact range table content for all preallocated target ranges. This includes sample names
            # and min/max/mid RPM for inactive rows too. No range-table pointers are copied.
            common_ranges = min(len(target.ranges), len(donor.ranges))
            if common_ranges < len(donor.ranges):
                report["warnings"].append(
                    f"{target.label}: target has only {common_ranges}/{len(donor.ranges)} donor range row(s); partial range copy."
                )
            for i in range(common_ranges):
                tr = target.ranges[i]
                dr = donor.ranges[i]
                raw = bytes(donor_doc.buf[dr.file_off:dr.file_off + RANGE_ENTRY_SIZE])
                write_bytes_if_changed(tr.file_off, raw, "range_changed")

            # Curves: copy header values and rows, but never copy ptr_points. Point count is only
            # imported when the target has enough already allocated rows.
            for curve_key, _curve_label, _ptr_key, _ptr_rel in CURVE_FIELDS:
                tc = target.curves.get(curve_key)
                dc = donor.curves.get(curve_key)
                if not tc or not dc or tc.file_off is None or dc.file_off is None:
                    continue
                if curve_key == "high_pitch_curve" and not copy_high_pitch_fields:
                    continue
                if tc.magic != dc.magic:
                    report["warnings"].append(
                        f"{target.label}/{curve_key}: curve magic differs target={format_hex(tc.magic)} donor={format_hex(dc.magic)}; skipped."
                    )
                    report["skipped"] += 1
                    continue
                # Copy non-pointer curve header fields. +0x18 ptr_points is intentionally skipped.
                for rel, size in ((0x04, 0x10), (0x1C, 0x04)):
                    raw = bytes(donor_doc.buf[dc.file_off + rel:dc.file_off + rel + size])
                    write_bytes_if_changed(tc.file_off + rel, raw, "curve_header_changed")

                target_capacity = curve_allocated_row_capacity(len(target_doc.buf), tc)
                donor_count = max(0, int(dc.point_count))
                donor_rows = dc.rows[:min(donor_count, len(dc.rows))]
                if tc.points_file_off is None or dc.points_file_off is None:
                    if donor_count or target_capacity:
                        report["warnings"].append(f"{target.label}/{curve_key}: missing point table; rows skipped.")
                    continue
                if target_capacity <= 0:
                    if donor_count:
                        report["warnings"].append(f"{target.label}/{curve_key}: target has zero writable curve row capacity; rows skipped.")
                        report["skipped"] += 1
                    continue

                if donor_count <= target_capacity:
                    rows_to_write = [list(r[:CURVE_POINT_FLOATS]) for r in donor_rows]
                    new_count = donor_count
                    if donor_count > int(tc.point_count):
                        report["curve_count_expanded"] += 1
                        report["warnings"].append(
                            f"{target.label}/{curve_key}: target active point_count {tc.point_count} expanded to donor count {donor_count} inside existing capacity {target_capacity}."
                        )
                else:
                    new_count = target_capacity
                    rows_to_write = resample_curve_rows(donor_rows, new_count)
                    report["curve_resampled"] += 1
                    report["warnings"].append(
                        f"{target.label}/{curve_key}: donor point_count={donor_count} exceeds target capacity={target_capacity}; resampled donor curve to {new_count} row(s), preserving donor first/final endpoints."
                    )

                write_u32_if_changed(tc.file_off + 0x14, new_count, "curve_header_changed")
                for i, row in enumerate(rows_to_write):
                    dst = tc.points_file_off + i * CURVE_POINT_SIZE
                    raw = pack_curve_row(row)
                    write_bytes_if_changed(dst, raw, "curve_row_changed")

        # Raw auxiliary: copy strings plus plausible scalar floats at the same relative offsets,
        # only when magics match. This is more aggressive than conservative mode but still avoids
        # raw block memcpy and donor pointer copying.
        donor_raw_by_rel = {r.root_rel: r for r in donor_doc.raw_aux_blocks}
        for target_raw in target_doc.raw_aux_blocks:
            donor_raw = donor_raw_by_rel.get(target_raw.root_rel)
            if donor_raw is None or target_raw.file_off is None or donor_raw.file_off is None:
                continue
            if target_raw.magic != donor_raw.magic:
                report["warnings"].append(
                    f"{target_raw.label}: raw aux magic differs target={format_hex(target_raw.magic)} donor={format_hex(donor_raw.magic)}; skipped aux float copy."
                )
                # Still try string copy only if receiver fields look compatible.
            donor_strings = iter_fixed_audio_strings(
                donor_doc.buf,
                donor_raw.file_off,
                aux_scan_size_for_magic(donor_raw.magic, 0x300),
            )
            for rel_off, donor_text in donor_strings:
                dst_off = target_raw.file_off + rel_off
                if dst_off + 0x20 > len(target_doc.buf):
                    report["skipped"] += 1
                    continue
                if not is_probable_fixed_audio_string(target_doc.buf, dst_off, 0x20):
                    continue
                raw = encode_fixed_string(donor_text, 0x20)
                write_bytes_if_changed(dst_off, raw, "aux_string_changed")

            if target_raw.magic == donor_raw.magic:
                for entry in donor_raw.float_preview:
                    rel_off = int(entry.get("rel_off", -1))
                    value = float(entry.get("float", 0.0))
                    if rel_off < 4:
                        continue
                    dst_off = target_raw.file_off + rel_off
                    if dst_off + 4 > len(target_doc.buf):
                        report["skipped"] += 1
                        continue
                    # Avoid overwriting fixed string bytes detected in the receiver block.
                    in_string = False
                    for s_entry in target_raw.found_string_entries:
                        start = int(s_entry.get("rel_off", -999999))
                        text = str(s_entry.get("text", ""))
                        if start <= rel_off < start + min(0x20, len(text) + 1):
                            in_string = True
                            break
                    if in_string:
                        continue
                    write_f32_if_changed(dst_off, value, "aux_float_changed")

        # Commons: no pointers were observed in the parsed common payload used here; copy params.
        donor_commons_by_rel = {p.root_rel: p for p in donor_doc.commons}
        for target_prof in target_doc.commons:
            donor_prof = donor_commons_by_rel.get(target_prof.root_rel)
            if donor_prof is None or target_prof.file_off is None or donor_prof.file_off is None:
                continue
            if target_prof.magic != donor_prof.magic:
                report["warnings"].append(
                    f"{target_prof.label}: common magic differs target={format_hex(target_prof.magic)} donor={format_hex(donor_prof.magic)}; skipped."
                )
                report["skipped"] += 1
                continue
            # Copy 22 f32 params at +0x04..+0x5B and unk_5C.
            raw = bytes(donor_doc.buf[donor_prof.file_off + 0x04:donor_prof.file_off + 0x60])
            write_bytes_if_changed(target_prof.file_off + 0x04, raw, "common_changed")

        return report

    def import_audio_from_document(self, donor_doc: PckAudioDocument) -> Dict[str, Any]:
        """Apply the previous CLI transfer strategy to the current document buffer.

        Mirrors mode all-prefix-aux-rpm from mc3_transfer_vehicle_audio_v1.py:
        - copy bank_name for all matching AudioBlockFull root-relative entries;
        - copy only sample prefixes before ':' for active receiver ranges;
        - copy raw auxiliary fixed ASCII strings at matching relative offsets;
        - copy conservative RPM values only for engine/exhaust slot blocks.
        """
        if not self.doc:
            raise RuntimeError("No target document loaded.")
        target_doc = self.doc
        report: Dict[str, Any] = {
            "block_changed": 0,
            "sample_changed": 0,
            "aux_changed": 0,
            "rpm_changed": 0,
            "shift_curve_changed": 0,
            "pair_role_matched": 0,
            "pair_root_rel_matched": 0,
            "pair_fallback_root_rel": 0,
            "skipped": 0,
            "warnings": [],
        }

        def copy_safe_curve_rows(donor_curve: CurveData, target_curve: CurveData, curve_name: str, target_label: str) -> None:
            """Copy a curve without copying donor pointers or expanding the file.

            Used by conservative import only for event curves (upshift/downshift),
            because leaving those envelopes from the receiver can make gear changes
            sound mismatched even when bank/sample names were transferred.
            """
            if target_curve.file_off is None or donor_curve.file_off is None:
                report["skipped"] += 1
                return
            if target_curve.magic != donor_curve.magic:
                report["skipped"] += 1
                report["warnings"].append(
                    f"{target_label}/{curve_name}: curve magic differs; shift curve skipped."
                )
                return
            if target_curve.points_file_off is None or donor_curve.points_file_off is None:
                if donor_curve.point_count or target_curve.point_count:
                    report["warnings"].append(
                        f"{target_label}/{curve_name}: missing point table; shift curve rows skipped."
                    )
                report["skipped"] += 1
                return

            # Copy non-pointer curve header fields. Never copy ptr_points at +0x18.
            for rel, size in ((0x04, 0x10), (0x1C, 0x04)):
                src = donor_curve.file_off + rel
                dst = target_curve.file_off + rel
                raw = bytes(donor_doc.buf[src:src + size])
                if bytes(target_doc.buf[dst:dst + size]) != raw:
                    target_doc.buf[dst:dst + size] = raw
                    report["shift_curve_changed"] += 1

            donor_count = max(0, int(donor_curve.point_count))
            target_capacity = curve_allocated_row_capacity(len(target_doc.buf), target_curve)
            donor_rows = donor_curve.rows[:min(donor_count, len(donor_curve.rows))]
            if target_capacity <= 0:
                if donor_count:
                    report["skipped"] += 1
                    report["warnings"].append(
                        f"{target_label}/{curve_name}: target has zero writable curve row capacity; shift rows skipped."
                    )
                return
            if donor_count <= target_capacity:
                rows_to_write = [list(r[:CURVE_POINT_FLOATS]) for r in donor_rows]
                new_count = donor_count
            else:
                rows_to_write = resample_curve_rows(donor_rows, target_capacity)
                new_count = target_capacity
                report["skipped"] += 1
                report["warnings"].append(
                    f"{target_label}/{curve_name}: donor point_count={donor_count} exceeds target capacity={target_capacity}; resampled shift curve to {new_count} row(s)."
                )
            if safe_u32(target_doc.buf, target_curve.file_off + 0x14) != new_count:
                write_u32(target_doc.buf, target_curve.file_off + 0x14, new_count)
                report["shift_curve_changed"] += 1
            for i, row in enumerate(rows_to_write):
                dst = target_curve.points_file_off + i * CURVE_POINT_SIZE
                raw = pack_curve_row(row)
                if bytes(target_doc.buf[dst:dst + CURVE_POINT_SIZE]) != raw:
                    target_doc.buf[dst:dst + CURVE_POINT_SIZE] = raw
                    report["shift_curve_changed"] += 1

        slot_rels = MAIN_SLOT_RELS
        block_pairs, pair_stats, pair_warnings = pair_audio_blocks_for_import(donor_doc.blocks, target_doc.blocks)
        report["pair_role_matched"] = pair_stats.get("role_matched", 0)
        report["pair_root_rel_matched"] = pair_stats.get("root_rel_matched", 0)
        report["pair_fallback_root_rel"] = pair_stats.get("fallback_root_rel", 0)
        report["skipped"] += pair_stats.get("unmatched", 0)
        report["warnings"].extend(pair_warnings)
        for donor, target, pair_mode in block_pairs:
            if target.file_off is None or donor.file_off is None:
                report["skipped"] += 1
                continue

            donor_bank = (donor.bank_name or "").strip()
            if donor_bank:
                old_bank = read_fixed_string(target_doc.buf, target.file_off + 0x04, 0x20)
                if old_bank != donor_bank:
                    write_fixed_string(target_doc.buf, target.file_off + 0x04, donor_bank, 0x20)
                    report["block_changed"] += 1

            # Previous CLI behavior only affected parsed/active ranges.
            common_active_ranges = min(
                max(0, int(target.active_range_count)),
                max(0, int(donor.active_range_count)),
                len(target.ranges),
                len(donor.ranges),
            )
            for i in range(common_active_ranges):
                tr = target.ranges[i]
                old_sample = read_fixed_string(target_doc.buf, tr.file_off, 0x20)
                new_sample = replace_sample_prefix(old_sample, donor_bank)
                if new_sample != old_sample:
                    write_fixed_string(target_doc.buf, tr.file_off, new_sample, 0x20)
                    report["sample_changed"] += 1

            # Conservative RPM transfer: slot engine/exhaust blocks only.
            if target.root_rel in slot_rels and donor.root_rel in slot_rels:
                rpm_pairs = [
                    (target.file_off + 0x24, donor.file_off + 0x24, "min_rpm_rec"),
                    (target.file_off + 0x28, donor.file_off + 0x28, "max_rpm_rec"),
                ]
                for dst_off, src_off, _name in rpm_pairs:
                    val = safe_f32(donor_doc.buf, src_off)
                    if val > 0.0 and safe_f32(target_doc.buf, dst_off) != val:
                        write_f32(target_doc.buf, dst_off, val)
                        report["rpm_changed"] += 1
                for i in range(common_active_ranges):
                    dr = donor.ranges[i]
                    tr = target.ranges[i]
                    for rel in (0x20, 0x24, 0x28):
                        val = safe_f32(donor_doc.buf, dr.file_off + rel)
                        if val > 0.0 and safe_f32(target_doc.buf, tr.file_off + rel) != val:
                            write_f32(target_doc.buf, tr.file_off + rel, val)
                            report["rpm_changed"] += 1

            # Conservative+ addition: transfer event envelopes for gear changes.
            # These are short event curves, not continuous volume/pitch curves.
            for curve_key in ("upshift_curve", "downshift_curve"):
                tc = target.curves.get(curve_key)
                dc = donor.curves.get(curve_key)
                if tc and dc:
                    copy_safe_curve_rows(dc, tc, curve_key, target.label)

        donor_raw_by_rel = {r.root_rel: r for r in donor_doc.raw_aux_blocks}
        for target_raw in target_doc.raw_aux_blocks:
            donor_raw = donor_raw_by_rel.get(target_raw.root_rel)
            if donor_raw is None or target_raw.file_off is None or donor_raw.file_off is None:
                continue
            donor_strings = iter_fixed_audio_strings(
                donor_doc.buf,
                donor_raw.file_off,
                aux_scan_size_for_magic(donor_raw.magic, 0x300),
            )
            for rel_off, donor_text in donor_strings:
                dst_off = target_raw.file_off + rel_off
                if dst_off + 0x20 > len(target_doc.buf):
                    report["skipped"] += 1
                    continue
                if not is_probable_fixed_audio_string(target_doc.buf, dst_off, 0x20):
                    continue
                old_text = read_fixed_string(target_doc.buf, dst_off, 0x20)
                if old_text != donor_text:
                    write_fixed_string(target_doc.buf, dst_off, donor_text, 0x20)
                    report["aux_changed"] += 1

        return report

    def reparse_current_buffer(self, keep_selection_rel: Optional[int] = None):
        if not self.doc:
            return
        path = self.doc.path
        buf = bytes(self.doc.buf)
        try:
            new_doc = PckAudioParser(path, buf=buf).parse()
        except Exception as exc:
            messagebox.showerror("Reparse failed", str(exc))
            return
        self.doc = new_doc
        self.rebuild_tree()
        selected_id = None
        if keep_selection_rel is not None:
            for tree_id, item in self.item_map.items():
                if getattr(item, "root_rel", None) == keep_selection_rel:
                    selected_id = tree_id
                    break
        if selected_id:
            self.tree.selection_set(selected_id)
            self.tree.see(selected_id)
            self.on_tree_select()
        else:
            self.show_document_summary()
            self.clear_tables()
            self.redraw_curve_canvas()
        self.update_dirty_state()

    def redraw_and_refresh_curve_table(self):
        if isinstance(self.selected, AudioBlock):
            self.fill_curve_rows_table(self.selected)
        self.redraw_curve_canvas()

    def _write_doc_to_path(self, path: Path):
        if not self.doc:
            raise RuntimeError("No PCK loaded.")
        tmp = path.with_name(path.name + ".tmp_audio_gui")
        tmp.write_bytes(bytes(self.doc.buf))
        os.replace(tmp, path)

    def save_current_pck(self):
        if not self.doc:
            messagebox.showinfo("No PCK loaded", "Open a PCK first.")
            return
        self.apply_top_fields_from_vars(silent=True)
        path = self.doc.path
        try:
            self._write_doc_to_path(path)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        self.dirty = False
        self.clear_history()
        self.update_dirty_state()
        self.set_status(f"saved: {path}")

    def save_pck_as(self):
        if not self.doc:
            messagebox.showinfo("No PCK loaded", "Open a PCK first.")
            return
        self.apply_top_fields_from_vars(silent=True)
        default_name = self.doc.path.with_name(self.doc.path.stem + "_audio_edit.pck").name
        path_str = filedialog.asksaveasfilename(
            title="Save As",
            initialdir=str(self.doc.path.parent),
            initialfile=default_name,
            defaultextension=".pck",
            filetypes=[("PCK files", "*.pck"), ("All files", "*.*")],
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            self._write_doc_to_path(path)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        self.doc.path = path
        self.current_path = path
        self.dirty = False
        self.clear_history()
        self.update_dirty_state()
        self.rebuild_tree()
        self.set_status(f"saved as: {path}")

    def show_document_summary(self):
        if not self.doc:
            return
        doc = self.doc
        lines = [
            f"File: {doc.path}",
            f"PTR_VA: {format_hex(doc.ptr_va)} | BaseVA: {format_hex(doc.base_va)} | Version: {format_hex(doc.file_version)} | Flags: {doc.flags}",
            f"Header Data Size: {doc.file_data_size} | Actual Size-0x80: {doc.expected_data_size}",
            f"Audio Root: VA {format_hex(doc.root_va)} -> file 0x{doc.root_off:08X} | Magic {format_hex(doc.root_magic)} ({AUDIO_ROOT_MAGICS.get(doc.root_magic, 'unknown')})",
            f"Audio Root Name: {doc.root_name or '<empty>'}",
            f"Parsed AudioBlockFull blocks: {len(doc.blocks)} | Raw aux previews: {len(doc.raw_aux_blocks)} | Common blocks: {len(doc.commons)}",
        ]
        if doc.warnings:
            lines.append("")
            lines.append("Warnings:")
            lines.extend(f"- {w}" for w in doc.warnings)
        self.set_summary("\n".join(lines))
        self.clear_tables()

    def show_category(self, category: str):
        if not self.doc:
            return
        items = []
        if category == "Auxiliary":
            items.extend([b for b in self.doc.blocks if b.category.startswith("Auxiliary / ")])
            items.extend([b for b in self.doc.raw_aux_blocks if b.category.startswith("Auxiliary / ")])
            lines = ["Category: Auxiliary", f"Items: {len(items)}", "", "Subgroups:"]
            counts: Dict[str, int] = {}
            for item in items:
                counts[getattr(item, "category", "Auxiliary / Unknown")] = counts.get(getattr(item, "category", "Auxiliary / Unknown"), 0) + 1
            for cat in sorted(counts, key=lambda c: (0 if c == "Auxiliary / TurboBlower" else 1, category_display_name(c))):
                info = aux_category_info(cat)
                lines.append(f"- {category_display_name(cat)}: {counts[cat]} item(s) — {info['role']}")
            lines.extend([
                "",
                "Current transfer policy:",
                "- Safe baseline: copy fixed ASCII bank/sample strings only.",
                "- TurboBlower AudioBlockFull auxiliary blocks can be edited like engine/exhaust, but curves/warble/boost should not be copied blindly.",
                "- Raw auxiliary bundles still need runtime isolation before exposing float sliders as named controls.",
            ])
            self.set_summary("\n".join(lines))
            self.clear_tables()
            return

        items.extend([b for b in self.doc.blocks if b.category == category])
        items.extend([b for b in self.doc.raw_aux_blocks if b.category == category])
        if category == "Common":
            items.extend(self.doc.commons)
        title = category_display_name(category) if category.startswith("Auxiliary / ") else category
        lines = [f"Category: {title}", f"Internal category: {category}", f"Items: {len(items)}", ""]
        if category.startswith("Auxiliary / "):
            info = aux_category_info(category)
            lines.extend([
                f"Likely role: {info['role']}",
                f"Editable candidates: {info['current_editability']}",
                f"Recommended transfer for now: {info['safe_transfer_now']}",
                "",
            ])
        for item in items:
            ptr_va = getattr(item, "ptr_va", 0)
            file_off = getattr(item, "file_off", None)
            lines.append(f"{getattr(item, 'label', 'item')} | VA {format_hex(ptr_va)} | file {format_hex(file_off)}")
        self.set_summary("\n".join(lines))
        self.clear_tables()

    def show_audio_block(self, block: AudioBlock):
        lines = [
            f"{block.label} ({category_display_name(block.category) if block.category.startswith('Auxiliary / ') else block.category})",
            f"Internal category: {block.category}",
            f"Root pointer: {block.ptr_name} @ root+0x{block.root_rel:02X} = {format_hex(block.ptr_va)} -> file {format_hex(block.file_off)}",
            f"Magic: {format_hex(block.magic)} ({AUDIO_BLOCK_MAGICS.get(block.magic, 'unknown')})",
            f"Bank: {block.bank_name or '<empty>'}",
            f"Sample role by active ranges: {block.role}",
            "",
            f"min_rpm_rec: {block.min_rpm_rec:.6g} | max_rpm_rec: {block.max_rpm_rec:.6g}",
            f"near_dist: {block.near_dist:.6g} | far_dist: {block.far_dist:.6g}",
            f"active_range_count: {block.active_range_count} | ptr_range_table: {format_hex(block.ptr_range_table)} -> file {format_hex(block.range_table_file_off)}",
            "",
            f"warble_min_rpm: {block.warble_min_rpm:.6g} | warble_max_rpm: {block.warble_max_rpm:.6g}",
            f"warble_min_rpm_delta: {block.warble_min_rpm_delta:.6g} | warble_min_vol_delta: {block.warble_min_vol_delta:.6g}",
            f"warble_max_rpm_delta: {block.warble_max_rpm_delta:.6g} | warble_max_vol_delta: {block.warble_max_vol_delta:.6g}",
            f"warble_reverse_max_rpm_delta: {block.warble_reverse_max_rpm_delta:.6g} | warble_reverse_period_rate: {block.warble_reverse_period_rate:.6g}",
            f"boost_mix_percent: {block.boost_mix_percent:.6g} | boost_mix_rate: {block.boost_mix_rate:.6g}",
            f"high_pitch_curve_engage_gear: {block.high_pitch_curve_engage_gear}",
            f"EngineRevSound: {block.engine_rev_sound or '<empty>'}",
        ]
        if block.category.startswith("Auxiliary / "):
            info = aux_category_info(block.category)
            lines.extend([
                "",
                f"Likely role: {info['role']}",
                f"Editable candidates: {info['current_editability']}",
                f"Recommended transfer for now: {info['safe_transfer_now']}",
            ])
        if block.warnings:
            lines.append("")
            lines.append("Warnings:")
            lines.extend(f"- {w}" for w in block.warnings)
        for curve in block.curves.values():
            if curve.warning:
                lines.append(f"- {curve.label}: {curve.warning}")
        self.set_summary("\n".join(lines))
        if hasattr(self, "aux_floats_tree"):
            self.aux_floats_tree.delete(*self.aux_floats_tree.get_children())
        self.fill_ranges_table(block)
        self.fill_curve_rows_table(block)
        self.fill_raw_for_block(block)

    def show_raw_aux(self, raw: RawAuxBlock):
        info = aux_category_info(raw.category)
        lines = [
            f"{raw.label} ({category_display_name(raw.category)})",
            f"Internal category: {raw.category}",
            f"Root pointer: {raw.ptr_name} @ root+0x{raw.root_rel:02X} = {format_hex(raw.ptr_va)} -> file {format_hex(raw.file_off)}",
            f"Magic: {format_hex(raw.magic)}",
            "",
            f"Likely role: {info['role']}",
            f"Editable candidates: {info['current_editability']}",
            f"Recommended transfer for now: {info['safe_transfer_now']}",
            "",
            f"possible_name_0: {raw.possible_name_0 or '<empty>'}",
            f"possible_name_1: {raw.possible_name_1 or '<empty>'}",
            "",
            "Readable strings found in preview:",
        ]
        if raw.found_string_entries:
            for entry in raw.found_string_entries[:60]:
                lines.append(
                    f"- file 0x{int(entry.get('file_off', 0)):08X} rel +0x{int(entry.get('rel_off', 0)):02X}: {entry.get('text', '')}"
                )
        else:
            lines.append("- <none>")
        if raw.warnings:
            lines.append("")
            lines.append("Warnings:")
            lines.extend(f"- {w}" for w in raw.warnings)
        self.set_summary("\n".join(lines))
        self.clear_tables()
        self.fill_aux_floats_table(raw)

        raw_lines = ["Raw preview hex:", raw.preview_hex, "", "Float scan preview (research only; fields are not named):"]
        if raw.float_preview:
            for entry in raw.float_preview[:48]:
                raw_lines.append(
                    f"file 0x{int(entry.get('file_off', 0)):08X} rel +0x{int(entry.get('rel_off', 0)):02X}: "
                    f"f32={float(entry.get('float', 0.0)):.9g} u32=0x{int(entry.get('u32', 0)) & 0xFFFFFFFF:08X}"
                )
        else:
            raw_lines.append("<none>")
        self.set_raw_text("\n".join(raw_lines))

    def aux_float_note(self, raw: RawAuxBlock, rel_off: int, value: float, raw_u32: int) -> str:
        # Do not label these as confirmed Volume/Pitch/etc. yet. The category
        # hint just helps runtime A/B testing.
        cat = raw.category.lower()
        hints = []
        if 0.0 <= value <= 2.5:
            hints.append("gain/pitch/probability candidate")
        elif 2.5 < value <= 250.0:
            hints.append("timing/distance/threshold candidate")
        elif 250.0 < value <= 30000.0:
            hints.append("speed/RPM/threshold candidate")
        if "wind" in cat:
            hints.append("wind block")
        elif "gearshift" in cat:
            hints.append("gearshift block")
        elif "backfire" in cat:
            hints.append("backfire block")
        elif "tire" in cat:
            hints.append("tire/skid block")
        elif "suspension" in cat:
            hints.append("suspension block")
        elif "explosion" in cat:
            hints.append("impact/scrape block")
        return "; ".join(hints) if hints else "unknown scalar candidate"

    def fill_aux_floats_table(self, raw: RawAuxBlock):
        if not hasattr(self, "aux_floats_tree"):
            return
        self.aux_floats_tree.delete(*self.aux_floats_tree.get_children())
        for entry in raw.float_preview:
            file_off = int(entry.get("file_off", 0))
            rel_off = int(entry.get("rel_off", 0))
            value = float(entry.get("float", 0.0))
            raw_u32 = int(entry.get("u32", 0)) & 0xFFFFFFFF
            iid = f"aux_float|{raw.key}|{file_off}"
            self.aux_floats_tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    f"+0x{rel_off:03X}",
                    f"0x{file_off:08X}",
                    f"{value:.9g}",
                    f"0x{raw_u32:08X}",
                    self.aux_float_note(raw, rel_off, value, raw_u32),
                ),
            )

    def close_aux_float_table_editor(self):
        editor = getattr(self, "_aux_float_table_editor", None)
        if editor is not None:
            try:
                editor.destroy()
            except Exception:
                pass
        self._aux_float_table_editor = None
        self._range_table_editor = None

    def on_aux_float_f2(self, _event=None):
        if not hasattr(self, "aux_floats_tree"):
            return
        focus = self.aux_floats_tree.focus()
        if focus:
            self.open_aux_float_table_editor(focus, "#3")

    def on_aux_float_double_click(self, event):
        region = self.aux_floats_tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        item_id = self.aux_floats_tree.identify_row(event.y)
        col_id = self.aux_floats_tree.identify_column(event.x)
        self.open_aux_float_table_editor(item_id, col_id)

    def open_aux_float_table_editor(self, item_id: str, col_id: str):
        if not isinstance(self.selected, RawAuxBlock):
            return
        # Only the f32 value column is editable. Offsets/raw U32 remain diagnostic.
        if col_id != "#3":
            return
        bbox = self.aux_floats_tree.bbox(item_id, col_id)
        if not bbox:
            return
        x, y, w, h = bbox
        values = list(self.aux_floats_tree.item(item_id, "values"))
        current = values[2] if len(values) >= 3 else ""
        self.close_aux_float_table_editor()
        editor = tk.Entry(
            self.aux_floats_tree,
            bg="#111318",
            fg=self.FG,
            insertbackground=self.FG,
            relief=tk.SOLID,
            bd=1,
            font=("Consolas", 10),
        )
        editor.insert(0, str(current))
        editor.select_range(0, tk.END)
        editor.focus_set()
        editor.place(x=x, y=y, width=w, height=h)
        self._aux_float_table_editor = editor

        def commit(_event=None):
            value = editor.get().strip()
            self.close_aux_float_table_editor()
            self.apply_aux_float_cell(item_id, value)
            return "break"

        def cancel(_event=None):
            self.close_aux_float_table_editor()
            return "break"

        editor.bind("<Return>", commit)
        editor.bind("<FocusOut>", commit)
        editor.bind("<Escape>", cancel)

    def apply_aux_float_cell(self, item_id: str, value_text: str):
        if not self.doc or not isinstance(self.selected, RawAuxBlock):
            return
        parts = item_id.split("|")
        if len(parts) != 3 or parts[0] != "aux_float":
            return
        try:
            file_off = int(parts[2])
            value = float(value_text)
        except Exception as exc:
            messagebox.showerror("Aux float edit failed", f"Invalid numeric value: {value_text!r}\n{exc}")
            return
        if not math.isfinite(value):
            messagebox.showerror("Aux float edit failed", "Value must be finite.")
            return
        if file_off < 0 or file_off + 4 > len(self.doc.buf):
            messagebox.showerror("Aux float edit failed", f"Offset outside file: 0x{file_off:08X}")
            return
        old_value = safe_f32(self.doc.buf, file_off)
        if old_value == value:
            return
        self.push_undo("Edit auxiliary float")
        write_f32(self.doc.buf, file_off, value)
        self.mark_dirty()
        # Refresh the raw preview from the modified bytes without reparsing the full tree.
        raw = self.selected
        preview_size = 0x200 if raw.kind == "raw_bundle" else 0x100
        if raw.file_off is not None:
            raw.float_preview = raw_float_preview(self.doc.buf, raw.file_off, min(preview_size, 0x120))
            raw.found_string_entries = read_loose_ascii_string_entries(self.doc.buf, raw.file_off + 0x04, preview_size)
            found = []
            for entry in raw.found_string_entries:
                text = str(entry.get("text", ""))
                if text and text not in found:
                    found.append(text)
            raw.found_strings = found
        self.show_raw_aux(raw)
        self.set_status(f"edited aux float at 0x{file_off:08X}: {old_value:.6g} -> {value:.6g}")

    def show_common(self, common: CommonBlock):
        lines = [
            f"{common.label} ({common.category})",
            f"Root pointer: {common.ptr_name} @ root+0x{common.root_rel:02X} = {format_hex(common.ptr_va)} -> file {format_hex(common.file_off)}",
            f"Magic: {format_hex(common.magic)} ({PROFILE_MAGICS.get(common.magic, 'unknown')})",
            f"unk_5C: {format_hex(common.unk_5C)}",
            "",
            "params[22]:",
        ]
        for i, value in enumerate(common.params):
            lines.append(f"  [{i:02d}] {value:.9g}")
        if common.warnings:
            lines.append("")
            lines.append("Warnings:")
            lines.extend(f"- {w}" for w in common.warnings)
        self.set_summary("\n".join(lines))
        self.clear_tables()

    def fill_ranges_table(self, block: AudioBlock):
        self.ranges_tree.delete(*self.ranges_tree.get_children())
        for r in block.ranges:
            self.ranges_tree.insert(
                "",
                "end",
                iid=f"range_row|{r.index}",
                values=(
                    r.index,
                    "yes" if r.active else "no",
                    r.sample_name,
                    f"{r.min_rpm:.6g}",
                    f"{r.mid_rpm:.6g}",
                    f"{r.max_rpm:.6g}",
                    f"0x{r.file_off:08X}",
                ),
            )

    def fill_curve_rows_table(self, block: AudioBlock):
        self.curve_rows_tree.delete(*self.curve_rows_tree.get_children())
        for key, label, _, _ in CURVE_FIELDS:
            if not self.curve_visible[key].get():
                continue
            curve = block.curves.get(key)
            if not curve:
                continue
            for i, row in enumerate(curve.rows):
                values = [label, i]
                values.extend(f"{v:.6g}" for v in row[:CURVE_POINT_FLOATS])
                iid = f"curve_row|{key}|{i}"
                self.curve_rows_tree.insert("", "end", iid=iid, values=values)

    # ------------------------------------------------------------------
    # Range table editing
    # ------------------------------------------------------------------

    def close_range_table_editor(self):
        editor = getattr(self, "_range_table_editor", None)
        if editor is not None:
            try:
                editor.destroy()
            except Exception:
                pass
        self._range_table_editor = None

    def on_ranges_f2(self, _event=None):
        focus = self.ranges_tree.focus()
        if not focus:
            return
        # Default to Min RPM when F2 is pressed without a mouse column.
        self.open_range_table_editor(focus, "#4")

    def on_ranges_double_click(self, event):
        region = self.ranges_tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        item_id = self.ranges_tree.identify_row(event.y)
        col_id = self.ranges_tree.identify_column(event.x)
        self.open_range_table_editor(item_id, col_id)

    def open_range_table_editor(self, item_id: str, col_id: str):
        if not item_id or not col_id:
            return
        # Editable numeric fields only: Min/Mid/Max RPM.
        if col_id not in ("#4", "#5", "#6"):
            return
        bbox = self.ranges_tree.bbox(item_id, col_id)
        if not bbox:
            return
        x, y, w, h = bbox
        values = list(self.ranges_tree.item(item_id, "values"))
        col_index = int(col_id[1:]) - 1
        current = values[col_index] if 0 <= col_index < len(values) else ""
        self.close_range_table_editor()
        editor = tk.Entry(
            self.ranges_tree,
            bg="#111318",
            fg=self.FG,
            insertbackground=self.FG,
            relief=tk.SOLID,
            bd=1,
            font=("Consolas", 10),
        )
        editor.insert(0, str(current))
        editor.select_range(0, tk.END)
        editor.focus_set()
        editor.place(x=x, y=y, width=w, height=h)
        self._range_table_editor = editor

        def commit(_event=None):
            value = editor.get().strip()
            self.close_range_table_editor()
            self.apply_range_table_cell(item_id, col_id, value)
            return "break"

        def cancel(_event=None):
            self.close_range_table_editor()
            return "break"

        editor.bind("<Return>", commit)
        editor.bind("<FocusOut>", commit)
        editor.bind("<Escape>", cancel)

    def apply_range_table_cell(self, item_id: str, col_id: str, value_text: str):
        if not isinstance(self.selected, AudioBlock):
            return
        parts = item_id.split("|")
        if len(parts) != 2 or parts[0] != "range_row":
            return
        try:
            range_index = int(parts[1])
            value = float(value_text)
        except Exception as exc:
            messagebox.showerror("Range edit failed", f"Invalid numeric value: {value_text!r}\n{exc}")
            return
        if not math.isfinite(value):
            messagebox.showerror("Range edit failed", "Value must be finite.")
            return
        if range_index < 0 or range_index >= len(self.selected.ranges):
            return
        col_to_attr = {
            "#4": ("min_rpm", 0x20, "Min RPM"),
            "#5": ("mid_rpm", 0x28, "Mid RPM"),
            "#6": ("max_rpm", 0x24, "Max RPM"),
        }
        if col_id not in col_to_attr:
            return
        attr, rel_off, label = col_to_attr[col_id]
        rng = self.selected.ranges[range_index]
        old = float(getattr(rng, attr))
        if old == value:
            return
        self.push_undo(f"Edit range {range_index} {label}")
        try:
            write_f32(self.selected.doc.buf, rng.file_off + rel_off, value)
            setattr(rng, attr, value)
        except Exception as exc:
            messagebox.showerror("Range edit failed", str(exc))
            return
        self.mark_dirty()
        self.fill_ranges_table(self.selected)
        self.redraw_curve_canvas()
        self.set_status(f"edited range[{range_index}] {label}: {old:.6g} -> {value:.6g}")

    # ------------------------------------------------------------------
    # Curve row editing / graph point dragging
    # ------------------------------------------------------------------

    def close_curve_table_editor(self):
        editor = getattr(self, "_curve_table_editor", None)
        if editor is not None:
            try:
                editor.destroy()
            except Exception:
                pass
        self._curve_table_editor = None

    def on_curve_rows_f2(self, _event=None):
        focus = self.curve_rows_tree.focus()
        if not focus:
            return
        # Default to Start X when F2 is pressed without a mouse column.
        self.open_curve_table_editor(focus, "#3")

    def on_curve_rows_double_click(self, event):
        region = self.curve_rows_tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        item_id = self.curve_rows_tree.identify_row(event.y)
        col_id = self.curve_rows_tree.identify_column(event.x)
        self.open_curve_table_editor(item_id, col_id)

    def open_curve_table_editor(self, item_id: str, col_id: str):
        if not item_id or not col_id:
            return
        # #1 Curve and #2 row index are labels, not editable values.
        if col_id in ("#1", "#2"):
            return
        col_to_field = {
            "#3": 0, "#4": 1, "#5": 2, "#6": 3, "#7": 4, "#8": 5,
            "#9": 6, "#10": 7, "#11": 8,
        }
        if col_id not in col_to_field:
            return
        bbox = self.curve_rows_tree.bbox(item_id, col_id)
        if not bbox:
            return
        x, y, w, h = bbox
        values = list(self.curve_rows_tree.item(item_id, "values"))
        col_index = int(col_id[1:]) - 1
        current = values[col_index] if 0 <= col_index < len(values) else ""
        self.close_curve_table_editor()
        editor = tk.Entry(
            self.curve_rows_tree,
            bg="#111318",
            fg=self.FG,
            insertbackground=self.FG,
            relief=tk.SOLID,
            bd=1,
            font=("Consolas", 10),
        )
        editor.insert(0, str(current))
        editor.select_range(0, tk.END)
        editor.focus_set()
        editor.place(x=x, y=y, width=w, height=h)
        self._curve_table_editor = editor

        def commit(_event=None):
            value = editor.get().strip()
            self.close_curve_table_editor()
            self.apply_curve_table_cell(item_id, col_id, value)
            return "break"

        def cancel(_event=None):
            self.close_curve_table_editor()
            return "break"

        editor.bind("<Return>", commit)
        editor.bind("<FocusOut>", commit)
        editor.bind("<Escape>", cancel)

    def apply_curve_table_cell(self, item_id: str, col_id: str, value_text: str):
        if not isinstance(self.selected, AudioBlock):
            return
        parts = item_id.split("|")
        if len(parts) != 3 or parts[0] != "curve_row":
            return
        curve_key = parts[1]
        try:
            row_index = int(parts[2])
            value = float(value_text)
        except Exception as exc:
            messagebox.showerror("Curve edit failed", f"Invalid numeric value: {value_text!r}\n{exc}")
            return
        col_to_field = {
            "#3": 0, "#4": 1, "#5": 2, "#6": 3, "#7": 4, "#8": 5,
            "#9": 6, "#10": 7, "#11": 8,
        }
        if col_id not in col_to_field:
            return
        field_index = col_to_field[col_id]
        curve = self.selected.curves.get(curve_key) if isinstance(self.selected, AudioBlock) else None
        if curve and 0 <= row_index < len(curve.rows) and field_index < len(curve.rows[row_index]):
            if curve.rows[row_index][field_index] == value:
                return
        self.push_undo(f"Edit {curve_key} row {row_index}")
        try:
            self.set_curve_row_field(self.selected, curve_key, row_index, field_index, value)
        except Exception as exc:
            messagebox.showerror("Curve edit failed", str(exc))
            return
        self.mark_dirty()
        self.fill_curve_rows_table(self.selected)
        self.redraw_curve_canvas()
        self.set_status(f"edited {curve_key} row {row_index}")

    def set_curve_row_field(self, block: AudioBlock, curve_key: str, row_index: int, field_index: int, value: float):
        curve = block.curves.get(curve_key)
        if not curve:
            raise ValueError(f"Curve not found: {curve_key}")
        if row_index < 0 or row_index >= len(curve.rows):
            raise ValueError(f"Row index out of range: {row_index}")
        if field_index < 0 or field_index >= CURVE_POINT_FLOATS:
            raise ValueError(f"Field index out of range: {field_index}")
        if not math.isfinite(value):
            raise ValueError("Value must be finite.")

        # Start/end point edits also update the neighboring segment endpoint so the curve remains continuous.
        if field_index in (0, 1):
            curve.rows[row_index][field_index] = value
            self.refit_curve_row(curve, row_index)
            self.write_curve_row(curve, row_index)
            if row_index > 0:
                prev_field = 4 if field_index == 0 else 5
                curve.rows[row_index - 1][prev_field] = value
                self.refit_curve_row(curve, row_index - 1)
                self.write_curve_row(curve, row_index - 1)
        elif field_index in (4, 5):
            curve.rows[row_index][field_index] = value
            self.refit_curve_row(curve, row_index)
            self.write_curve_row(curve, row_index)
            if row_index + 1 < len(curve.rows):
                next_field = 0 if field_index == 4 else 1
                curve.rows[row_index + 1][next_field] = value
                self.refit_curve_row(curve, row_index + 1)
                self.write_curve_row(curve, row_index + 1)
        elif field_index in (2, 3):
            curve.rows[row_index][field_index] = value
            self.refit_curve_row(curve, row_index)
            self.write_curve_row(curve, row_index)
        else:
            # Advanced/raw coefficient edit. This intentionally does not refit from points.
            curve.rows[row_index][field_index] = value
            self.write_curve_row(curve, row_index)

    def refit_curve_row(self, curve: CurveData, row_index: int):
        if row_index < 0 or row_index >= len(curve.rows):
            return
        row = curve.rows[row_index]
        if len(row) < CURVE_POINT_FLOATS:
            return
        a, b, c = fit_quadratic_coefficients(row[0], row[1], row[2], row[3], row[4], row[5])
        row[6], row[7], row[8] = a, b, c

    def write_curve_row(self, curve: CurveData, row_index: int):
        if not self.doc:
            raise RuntimeError("No PCK loaded.")
        if curve.points_file_off is None:
            raise ValueError(f"Curve {curve.key} has no writable points offset.")
        capacity = curve_allocated_row_capacity(len(self.doc.buf), curve)
        if row_index < 0 or row_index >= capacity:
            raise ValueError(f"Curve row {row_index} is outside allocated capacity {capacity}.")
        if row_index >= len(curve.rows):
            raise ValueError(f"Curve row {row_index} is not active/loaded.")
        dst = curve.points_file_off + row_index * CURVE_POINT_SIZE
        self.doc.buf[dst:dst + CURVE_POINT_SIZE] = pack_curve_row(curve.rows[row_index])

    def write_curve_point_count(self, curve: CurveData, count: int):
        if not self.doc:
            raise RuntimeError("No PCK loaded.")
        if curve.file_off is None:
            raise ValueError(f"Curve {curve.key} has no writable header offset.")
        write_u32(self.doc.buf, curve.file_off + 0x14, int(count))
        curve.point_count = int(count)

    def write_active_curve_rows(self, curve: CurveData):
        active_count = min(max(0, int(curve.point_count)), len(curve.rows))
        for i in range(active_count):
            self.write_curve_row(curve, i)

    def get_curve_knot_xy(self, curve: CurveData, point_index: int) -> Optional[Tuple[float, float]]:
        active_count = min(max(0, int(curve.point_count)), len(curve.rows))
        if active_count <= 0:
            return None
        if point_index <= 0:
            return float(curve.rows[0][0]), float(curve.rows[0][1])
        prev_idx = point_index - 1
        if 0 <= prev_idx < active_count:
            return float(curve.rows[prev_idx][4]), float(curve.rows[prev_idx][5])
        return None

    def build_curve_row_from_points(self, x0: float, y0: float, ctrl_x: float, ctrl_y: float, x1: float, y1: float) -> List[float]:
        a, b, c = fit_quadratic_coefficients(x0, y0, ctrl_x, ctrl_y, x1, y1)
        return [float(x0), float(y0), float(ctrl_x), float(ctrl_y), float(x1), float(y1), float(a), float(b), float(c)]

    def update_curve_knot_from_graph(self, block: AudioBlock, curve_key: str, point_index: int, x: float, y: float, move_adjacent_controls: bool = True):
        curve = block.curves.get(curve_key)
        if not curve:
            return
        active_count = min(max(0, int(curve.point_count)), len(curve.rows))
        if active_count <= 0:
            return

        old_xy = self.get_curve_knot_xy(curve, point_index)
        dx = dy = 0.0
        if old_xy is not None:
            dx = float(x) - old_xy[0]
            dy = float(y) - old_xy[1]

        # When a knot is moved, keep its visible tangent/control handles attached
        # to that knot by translating the adjacent row controls by the same delta.
        # This is closer to Blender/AE/Unreal graph-editor behavior and avoids
        # leaving handles behind after moving a selected point.
        moved_control_rows: set[int] = set()
        if move_adjacent_controls and (abs(dx) > 1e-12 or abs(dy) > 1e-12):
            for ctrl_row in (point_index - 1, point_index):
                if 0 <= ctrl_row < active_count and self.curve_row_valid(curve.rows[ctrl_row]):
                    curve.rows[ctrl_row][2] = float(curve.rows[ctrl_row][2]) + dx
                    curve.rows[ctrl_row][3] = float(curve.rows[ctrl_row][3]) + dy
                    moved_control_rows.add(ctrl_row)

        touched_rows: set[int] = set(moved_control_rows)
        if point_index <= 0:
            curve.rows[0][0] = x
            curve.rows[0][1] = y
            touched_rows.add(0)
        else:
            prev_idx = point_index - 1
            if 0 <= prev_idx < active_count:
                curve.rows[prev_idx][4] = x
                curve.rows[prev_idx][5] = y
                touched_rows.add(prev_idx)
            if point_index < active_count:
                curve.rows[point_index][0] = x
                curve.rows[point_index][1] = y
                touched_rows.add(point_index)

        for row_idx in sorted(touched_rows):
            self.refit_curve_row(curve, row_idx)
            self.write_curve_row(curve, row_idx)

    def update_curve_control_from_graph(self, block: AudioBlock, curve_key: str, row_index: int, x: float, y: float):
        curve = block.curves.get(curve_key)
        if not curve:
            return
        if row_index < 0 or row_index >= min(int(curve.point_count), len(curve.rows)):
            return
        curve.rows[row_index][2] = x
        curve.rows[row_index][3] = y
        self.refit_curve_row(curve, row_index)
        self.write_curve_row(curve, row_index)

    def curve_to_canvas_xy(self, x: float, y: float) -> Optional[Tuple[float, float]]:
        tr = self._curve_transform
        if not tr:
            return None
        xmin, xmax = tr["xmin"], tr["xmax"]
        ymin, ymax = tr["ymin"], tr["ymax"]
        if math.isclose(xmin, xmax) or math.isclose(ymin, ymax):
            return None
        px = tr["margin_l"] + (float(x) - xmin) / (xmax - xmin) * tr["plot_w"]
        py = tr["margin_t"] + (1.0 - (float(y) - ymin) / (ymax - ymin)) * tr["plot_h"]
        return float(px), float(py)

    @staticmethod
    def _point_to_segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> Tuple[float, float]:
        vx = bx - ax
        vy = by - ay
        wx = px - ax
        wy = py - ay
        denom = vx * vx + vy * vy
        if denom <= 1e-12:
            return math.hypot(px - ax, py - ay), 0.0
        t = max(0.0, min(1.0, (wx * vx + wy * vy) / denom))
        qx = ax + t * vx
        qy = ay + t * vy
        return math.hypot(px - qx, py - qy), t

    def find_curve_segment_near(self, canvas_x: float, canvas_y: float, threshold: float = 14.0) -> Optional[Dict[str, Any]]:
        selected = self.selected
        if not isinstance(selected, AudioBlock):
            return None
        best: Optional[Dict[str, Any]] = None
        best_dist = float(threshold)
        draw_mode = self.curve_draw_mode.get()
        for key in self.visible_curve_keys():
            curve = selected.curves.get(key)
            if not curve:
                continue
            active_count = min(max(0, int(curve.point_count)), len(curve.rows))
            for row_index, row in enumerate(curve.rows[:active_count]):
                if not self.curve_row_valid(row):
                    continue
                samples = self.sample_curve_row(row, steps=48, mode=draw_mode)
                if len(samples) < 2:
                    continue
                sample_pixels: List[Tuple[float, float, float, float]] = []
                for x, y in samples:
                    pixel = self.curve_to_canvas_xy(x, y)
                    if pixel is not None:
                        sample_pixels.append((x, y, pixel[0], pixel[1]))
                for a, b in zip(sample_pixels, sample_pixels[1:]):
                    dist, t = self._point_to_segment_distance(canvas_x, canvas_y, a[2], a[3], b[2], b[3])
                    if dist < best_dist:
                        curve_x = a[0] + (b[0] - a[0]) * t
                        # Keep the inserted point exactly on the source curve, not at the mouse Y.
                        curve_y = eval_curve_row(row, curve_x) if draw_mode != "linear" else a[1] + (b[1] - a[1]) * t
                        best_dist = dist
                        best = {
                            "curve_key": key,
                            "row_index": int(row_index),
                            "x": float(curve_x),
                            "y": float(curve_y),
                            "distance": float(dist),
                        }
        return best

    def insert_curve_point(self, block: AudioBlock, curve_key: str, row_index: int, x: float, y: float):
        curve = block.curves.get(curve_key)
        if not curve:
            raise ValueError(f"Curve not found: {curve_key}")
        if curve.points_file_off is None:
            raise ValueError(f"Curve {curve_key} has no writable points.")
        active_count = min(max(0, int(curve.point_count)), len(curve.rows))
        capacity = curve_allocated_row_capacity(len(block.doc.buf), curve)
        if active_count >= capacity:
            raise ValueError(f"Curve capacity reached ({capacity} rows).")
        if row_index < 0 or row_index >= active_count:
            raise ValueError(f"Segment index out of range: {row_index}")
        row = curve.rows[row_index]
        if not self.curve_row_valid(row):
            raise ValueError("Cannot split invalid curve row.")

        x0, y0, _cx, _cy, x1, y1 = row[:6]
        lo, hi = (x0, x1) if x0 <= x1 else (x1, x0)
        new_x = max(lo, min(float(x), hi))
        # Avoid degenerate split at segment endpoints.
        if abs(new_x - x0) < 1e-6 or abs(new_x - x1) < 1e-6:
            raise ValueError("Click farther from the segment endpoint to insert a point.")
        new_y = float(y)

        left_mid_x = (x0 + new_x) * 0.5
        right_mid_x = (new_x + x1) * 0.5
        left_mid_y = eval_curve_row(row, left_mid_x)
        right_mid_y = eval_curve_row(row, right_mid_x)
        left_row = self.build_curve_row_from_points(x0, y0, left_mid_x, left_mid_y, new_x, new_y)
        right_row = self.build_curve_row_from_points(new_x, new_y, right_mid_x, right_mid_y, x1, y1)

        curve.rows[row_index] = left_row
        curve.rows.insert(row_index + 1, right_row)
        self.write_curve_point_count(curve, active_count + 1)
        self.write_active_curve_rows(curve)
        self._active_curve_handle = {"curve_key": curve_key, "point_index": row_index + 1}

    def delete_curve_point(self, block: AudioBlock, curve_key: str, point_index: int):
        curve = block.curves.get(curve_key)
        if not curve:
            raise ValueError(f"Curve not found: {curve_key}")
        if curve.points_file_off is None:
            raise ValueError(f"Curve {curve_key} has no writable points.")
        active_count = min(max(0, int(curve.point_count)), len(curve.rows))
        if active_count <= 1:
            raise ValueError("Cannot delete the last curve segment.")
        if point_index < 0 or point_index > active_count:
            raise ValueError(f"Point index out of range: {point_index}")

        if point_index <= 0:
            # Delete first knot by dropping the first segment. The old point 1 becomes the new first knot.
            del curve.rows[0]
            new_active = active_count - 1
            new_active_point = 0
        elif point_index >= active_count:
            # Delete final knot by dropping the final segment.
            del curve.rows[active_count - 1]
            new_active = active_count - 1
            new_active_point = max(0, new_active)
        else:
            # Internal knot: merge the two neighboring segments into one.
            left = curve.rows[point_index - 1]
            right = curve.rows[point_index]
            if not self.curve_row_valid(left) or not self.curve_row_valid(right):
                raise ValueError("Cannot merge invalid curve rows.")
            x0, y0 = left[0], left[1]
            x1, y1 = right[4], right[5]
            ctrl_x = (x0 + x1) * 0.5
            ctrl_y = eval_piecewise_curve([left, right], ctrl_x)
            merged = self.build_curve_row_from_points(x0, y0, ctrl_x, ctrl_y, x1, y1)
            curve.rows[point_index - 1] = merged
            del curve.rows[point_index]
            new_active = active_count - 1
            new_active_point = point_index

        self.write_curve_point_count(curve, new_active)
        self.write_active_curve_rows(curve)
        self._active_curve_handle = {"curve_key": curve_key, "point_index": min(new_active_point, new_active)}

    def canvas_to_curve_xy(self, canvas_x: float, canvas_y: float) -> Tuple[float, float]:
        tr = self._curve_transform
        if not tr:
            return 0.0, 0.0
        xmin, xmax = tr["xmin"], tr["xmax"]
        ymin, ymax = tr["ymin"], tr["ymax"]
        x = xmin + (canvas_x - tr["margin_l"]) / max(1.0, tr["plot_w"]) * (xmax - xmin)
        y = ymin + (1.0 - (canvas_y - tr["margin_t"]) / max(1.0, tr["plot_h"])) * (ymax - ymin)
        # Keep edits inside the currently visible graph domain. This prevents accidental huge values when dragging outside the plot.
        x = max(min(x, xmax), xmin)
        y = max(min(y, ymax), ymin)
        return float(x), float(y)

    def find_curve_hitbox(self, canvas_x: float, canvas_y: float) -> Optional[Dict[str, Any]]:
        best = None
        best_d2 = 999999.0
        for hb in self._curve_hitboxes:
            dx = canvas_x - float(hb.get("px", 0.0))
            dy = canvas_y - float(hb.get("py", 0.0))
            d2 = dx * dx + dy * dy
            radius = float(hb.get("radius", 9.0))
            if d2 <= radius * radius and d2 < best_d2:
                best = hb
                best_d2 = d2
        return best

    def on_curve_canvas_press(self, event):
        if not self.edit_curve_points.get() or not isinstance(self.selected, AudioBlock):
            return
        canvas = self.curve_canvas
        cx = canvas.canvasx(event.x)
        cy = canvas.canvasy(event.y)
        hit = self.find_curve_hitbox(cx, cy)
        if not hit:
            return
        if hit.get("kind") == "knot":
            self._active_curve_handle = {
                "curve_key": hit.get("curve_key"),
                "point_index": int(hit.get("point_index", 0)),
            }
        elif hit.get("kind") == "control" and not self._active_curve_handle:
            # If a handle is clicked directly without an active point, bind selection
            # to the nearest right-side knot of that segment.
            self._active_curve_handle = {
                "curve_key": hit.get("curve_key"),
                "point_index": int(hit.get("row_index", 0)) + 1,
            }
        self._curve_drag = dict(hit)
        self._curve_drag["start_canvas"] = (cx, cy)
        canvas.configure(cursor="fleur")
        self.redraw_curve_canvas()
        return "break"

    def on_curve_canvas_middle_press(self, event):
        if not self.edit_curve_points.get() or not isinstance(self.selected, AudioBlock):
            return
        canvas = self.curve_canvas
        cx = canvas.canvasx(event.x)
        cy = canvas.canvasy(event.y)
        segment = self.find_curve_segment_near(cx, cy)
        if not segment:
            self.set_status("middle click must be near a visible curve segment")
            return "break"
        curve_key = str(segment["curve_key"])
        row_index = int(segment["row_index"])
        try:
            self.push_undo(f"Insert {curve_key} point")
            self.insert_curve_point(self.selected, curve_key, row_index, float(segment["x"]), float(segment["y"]))
        except Exception as exc:
            # The undo snapshot is harmless if nothing changed; remove it to avoid a dead undo step.
            if self.undo_stack and self.undo_stack[-1].get("label") == f"Insert {curve_key} point":
                self.undo_stack.pop()
                self.update_undo_redo_buttons()
            self.set_status(f"insert point failed: {exc}")
            return "break"
        self.mark_dirty()
        self.fill_curve_rows_table(self.selected)
        self.redraw_curve_canvas()
        self.set_status(f"inserted {curve_key} point after segment {row_index}")
        return "break"

    def on_curve_canvas_right_press(self, event):
        if not self.edit_curve_points.get() or not isinstance(self.selected, AudioBlock):
            return
        canvas = self.curve_canvas
        cx = canvas.canvasx(event.x)
        cy = canvas.canvasy(event.y)
        hit = self.find_curve_hitbox(cx, cy)
        if hit and hit.get("kind") == "knot":
            self._active_curve_handle = {
                "curve_key": hit.get("curve_key"),
                "point_index": int(hit.get("point_index", 0)),
            }
        active = getattr(self, "_active_curve_handle", None)
        if not active:
            self.set_status("right click a point, or select a point first, to delete it")
            return "break"
        curve_key = str(active.get("curve_key"))
        point_index = int(active.get("point_index", -1))
        try:
            self.push_undo(f"Delete {curve_key} point")
            self.delete_curve_point(self.selected, curve_key, point_index)
        except Exception as exc:
            if self.undo_stack and self.undo_stack[-1].get("label") == f"Delete {curve_key} point":
                self.undo_stack.pop()
                self.update_undo_redo_buttons()
            self.set_status(f"delete point failed: {exc}")
            return "break"
        self.mark_dirty()
        self.fill_curve_rows_table(self.selected)
        self.redraw_curve_canvas()
        self.set_status(f"deleted {curve_key} point {point_index}")
        return "break"

    def on_curve_canvas_drag(self, event):
        if not self._curve_drag or not isinstance(self.selected, AudioBlock):
            return
        canvas = self.curve_canvas
        cx = canvas.canvasx(event.x)
        cy = canvas.canvasy(event.y)
        x, y = self.canvas_to_curve_xy(cx, cy)
        drag = self._curve_drag
        if not drag.get("undo_pushed"):
            self.push_undo(f"Drag {drag.get('curve_key')} point")
            drag["undo_pushed"] = True
        try:
            if drag.get("kind") == "control":
                self.update_curve_control_from_graph(self.selected, str(drag["curve_key"]), int(drag["row_index"]), x, y)
            else:
                self.update_curve_knot_from_graph(self.selected, str(drag["curve_key"]), int(drag["point_index"]), x, y)
        except Exception as exc:
            self.set_status(f"drag edit failed: {exc}")
            return "break"
        self.mark_dirty()
        self.fill_curve_rows_table(self.selected)
        self.redraw_curve_canvas()
        self.set_status(f"drag {drag.get('curve_key')} x={x:.6g} y={y:.6g}")
        return "break"

    def on_curve_canvas_release(self, _event):
        if self._curve_drag is not None:
            self._curve_drag = None
            self.curve_canvas.configure(cursor="")
            if isinstance(self.selected, AudioBlock):
                self.fill_curve_rows_table(self.selected)
                self.redraw_curve_canvas()
        return "break"

    def on_curve_canvas_motion(self, event):
        if not self.edit_curve_points.get() or self._curve_drag is not None:
            return
        cx = self.curve_canvas.canvasx(event.x)
        cy = self.curve_canvas.canvasy(event.y)
        hit = self.find_curve_hitbox(cx, cy)
        self.curve_canvas.configure(cursor="hand2" if hit else "")

    def fill_raw_for_block(self, block: AudioBlock):
        lines = []
        lines.append("Curve headers:")
        for key, label, ptr_key, _ in CURVE_FIELDS:
            c = block.curves.get(key)
            if not c:
                continue
            lines.append(
                f"{label}: {ptr_key}={format_hex(c.ptr_va)} file={format_hex(c.file_off)} "
                f"magic={format_hex(c.magic)} point_count={c.point_count} "
                f"ptr_points={format_hex(c.ptr_points)} points_file={format_hex(c.points_file_off)}"
            )
            if c.warning:
                lines.append(f"  WARN: {c.warning}")
        self.set_raw_text("\n".join(lines))

    @staticmethod
    def curve_row_valid(row: List[float]) -> bool:
        return len(row) >= 9 and all(math.isfinite(v) for v in row[:9])

    @staticmethod
    def sample_curve_row(row: List[float], steps: int = 32, mode: str = "poly") -> List[Tuple[float, float]]:
        """Sample one MC3 audio curve span.

        row[0:6] = start/control/end points: (x0,y0), (cx,cy), (x1,y1)
        row[6:9] = quadratic coefficients A/B/C. Polynomial mode evaluates y=A*x*x+B*x+C.
        Linear mode ignores the coefficients and draws a straight segment from start to end.
        """
        if not AudioCurveGUI.curve_row_valid(row):
            return []
        x0, y0, _cx, _cy, x1, y1, a, b, c = row[:9]
        if math.isclose(x0, x1):
            y = (a * x0 * x0 + b * x0 + c) if mode != "linear" else y0
            return [(x0, y)]
        n = max(2, int(steps))
        pts: List[Tuple[float, float]] = []
        for i in range(n + 1):
            t = i / n
            x = x0 + (x1 - x0) * t
            if mode == "linear":
                y = y0 + (y1 - y0) * t
            else:
                y = a * x * x + b * x + c
            if math.isfinite(x) and math.isfinite(y):
                pts.append((x, y))
        return pts

    @staticmethod
    def sampled_curve_points(curve: CurveData, mode: str = "poly") -> List[Tuple[float, float]]:
        pts: List[Tuple[float, float]] = []
        for row in curve.rows[:max(0, int(curve.point_count))]:
            seg = AudioCurveGUI.sample_curve_row(row, mode=mode)
            if not seg:
                continue
            if pts and seg and math.isclose(pts[-1][0], seg[0][0]) and math.isclose(pts[-1][1], seg[0][1]):
                pts.extend(seg[1:])
            else:
                pts.extend(seg)
        return pts

    @staticmethod
    def curve_knot_points(curve: CurveData) -> List[Tuple[int, float, float]]:
        pts: List[Tuple[int, float, float]] = []
        for i, row in enumerate(curve.rows[:max(0, int(curve.point_count))]):
            if not AudioCurveGUI.curve_row_valid(row):
                continue
            x0, y0, _cx, _cy, x1, y1 = row[:6]
            if i == 0:
                pts.append((0, x0, y0))
            pts.append((i + 1, x1, y1))
        return pts

    @staticmethod
    def curve_control_points(curve: CurveData) -> List[Tuple[int, float, float]]:
        pts: List[Tuple[int, float, float]] = []
        for i, row in enumerate(curve.rows[:max(0, int(curve.point_count))]):
            if not AudioCurveGUI.curve_row_valid(row):
                continue
            pts.append((i, row[2], row[3]))
        return pts

    def active_handle_for_curve(self, curve: CurveData) -> Optional[Dict[str, Any]]:
        active = getattr(self, "_active_curve_handle", None)
        if not active or active.get("curve_key") != curve.key:
            return None
        try:
            point_index = int(active.get("point_index", -1))
        except Exception:
            return None
        if point_index < 0 or point_index > max(0, int(curve.point_count)):
            return None
        return active

    def active_control_rows_for_curve(self, curve: CurveData) -> List[int]:
        active = self.active_handle_for_curve(curve)
        if not active:
            return []
        point_index = int(active["point_index"])
        active_count = min(max(0, int(curve.point_count)), len(curve.rows))
        rows: List[int] = []
        # Left segment handle for the selected knot.
        if point_index > 0 and point_index - 1 < active_count:
            rows.append(point_index - 1)
        # Right segment handle for the selected knot.
        if point_index < active_count:
            rows.append(point_index)
        # Keep deterministic order and remove duplicates.
        return sorted(set(rows))

    def normalized_rpm_label(self, block: AudioBlock, x: float) -> str:
        min_rpm, max_rpm = self._display_rpm_bounds_for_graph(block)
        if max_rpm > min_rpm:
            rpm = min_rpm + x * (max_rpm - min_rpm)
            return f"{rpm:.0f} rpm"
        return ""

    def redraw_curve_canvas(self):
        canvas = getattr(self, "curve_canvas", None)
        if canvas is None:
            return
        canvas.delete("all")
        self._curve_hitboxes = []
        self._curve_transform = None
        view_w = max(10, canvas.winfo_width())
        view_h = max(10, canvas.winfo_height())
        w = max(view_w, CURVE_CANVAS_MIN_WIDTH)
        h = max(view_h, CURVE_CANVAS_MIN_HEIGHT)
        canvas.configure(scrollregion=(0, 0, w, h))
        canvas.create_rectangle(0, 0, w, h, fill=self.PANEL, outline="")

        selected = self.selected
        if not isinstance(selected, AudioBlock):
            self.draw_canvas_message("Select an AudioBlockFull item to view curves.")
            return

        draw_mode = self.curve_draw_mode.get()
        visible_curves: List[CurveData] = []
        sampled_by_key: Dict[str, List[Tuple[float, float]]] = {}
        for key, _label, _ptr_key, _rel in CURVE_FIELDS:
            if self.curve_visible[key].get():
                c = selected.curves.get(key)
                if c:
                    sampled = self.sampled_curve_points(c, mode=draw_mode)
                    if sampled:
                        visible_curves.append(c)
                        sampled_by_key[c.key] = sampled

        if not visible_curves:
            self.draw_canvas_message("No visible curve points for this block.")
            return

        margin_l, margin_r, margin_t, margin_b = 78, 26, 28, 62
        plot_w = max(1, w - margin_l - margin_r)
        plot_h = max(1, h - margin_t - margin_b)

        all_points: List[Tuple[float, float]] = []
        for c in visible_curves:
            all_points.extend(sampled_by_key.get(c.key, []))
            all_points.extend((x, y) for _idx, x, y in self.curve_knot_points(c))
            if self.show_control_points.get():
                all_points.extend((x, y) for _idx, x, y in self.curve_control_points(c))

        all_x = [x for x, _y in all_points]
        all_y = [y for _x, y in all_points]
        if not all_x or not all_y:
            self.draw_canvas_message("No plottable curve data.")
            return

        if self.curve_scale_mode.get() == "fit":
            xmin, xmax = min(all_x), max(all_x)
            ymin, ymax = min(all_y), max(all_y)
            if math.isclose(xmin, xmax):
                xmin -= 0.5
                xmax += 0.5
            else:
                pad_x = (xmax - xmin) * 0.06
                xmin -= pad_x
                xmax += pad_x
            if math.isclose(ymin, ymax):
                ymin -= 0.5
                ymax += 0.5
            else:
                pad_y = (ymax - ymin) * 0.12
                ymin -= pad_y
                ymax += pad_y
            scale_label = "Fit to view"
        else:
            # Absolute normalized comparison mode. This is intentionally fixed so two cars can be
            # compared visually without the y-axis changing per block.
            xmin, xmax = 0.0, 1.0
            ymin, ymax = 0.0, 1.0
            # If the user isolates only shift curves, keep them in the same normalized frame anyway.
            scale_label = "Global 0-1"

        draw_label = "Polynomial" if draw_mode != "linear" else "Linear"

        if math.isclose(xmin, xmax):
            xmax = xmin + 1.0
        if math.isclose(ymin, ymax):
            ymax = ymin + 1.0

        def sx(x: float) -> float:
            return margin_l + (x - xmin) / (xmax - xmin) * plot_w

        def sy(y: float) -> float:
            return margin_t + (1.0 - (y - ymin) / (ymax - ymin)) * plot_h

        self._curve_transform = {
            "xmin": float(xmin), "xmax": float(xmax), "ymin": float(ymin), "ymax": float(ymax),
            "margin_l": float(margin_l), "margin_t": float(margin_t),
            "plot_w": float(plot_w), "plot_h": float(plot_h), "canvas_w": float(w), "canvas_h": float(h),
        }

        # Grid and axis labels.
        for i in range(6):
            gx = margin_l + i / 5 * plot_w
            canvas.create_line(gx, margin_t, gx, margin_t + plot_h, fill=self.GRID)
            val = xmin + i / 5 * (xmax - xmin)
            canvas.create_text(gx, margin_t + plot_h + 18, text=f"{val:.3g}", fill=self.MUTED, font=("Consolas", 9))
            rpm_hint = self.normalized_rpm_label(selected, val)
            if self.curve_scale_mode.get() == "global" and rpm_hint:
                canvas.create_text(gx, margin_t + plot_h + 34, text=rpm_hint, fill="#677085", font=("Consolas", 8))
        for i in range(5):
            gy = margin_t + i / 4 * plot_h
            canvas.create_line(margin_l, gy, margin_l + plot_w, gy, fill=self.GRID)
            val = ymax - i / 4 * (ymax - ymin)
            canvas.create_text(margin_l - 8, gy, text=f"{val:.3g}", anchor="e", fill=self.MUTED, font=("Consolas", 9))

        canvas.create_rectangle(margin_l, margin_t, margin_l + plot_w, margin_t + plot_h, outline="#565B6B")
        canvas.create_text(margin_l, 10, anchor="w", text=f"{selected.label} | {selected.bank_name}", fill=self.FG, font=("Segoe UI", 10, "bold"))
        canvas.create_text(
            margin_l + plot_w,
            10,
            anchor="e",
            text=f"{scale_label} | {draw_label} draw | row = start/control/end + y=A*x²+B*x+C",
            fill=self.MUTED,
            font=("Segoe UI", 9),
        )
        canvas.create_text(margin_l + plot_w / 2, h - 12, text="X: normalized controller / RPM-like axis for continuous curves; shift curves use event-time-like X", fill=self.MUTED, font=("Segoe UI", 8))
        canvas.create_text(14, margin_t + plot_h / 2, text="Y: normalized curve output", fill=self.MUTED, font=("Segoe UI", 8), angle=90)

        legend_x = margin_l + 8
        legend_y = margin_t + 8
        for c in visible_curves:
            color = CURVE_COLORS.get(c.key, "#FFFFFF")
            sampled = sampled_by_key.get(c.key, [])
            pts = [(sx(x), sy(y)) for x, y in sampled]
            if len(pts) >= 2:
                for a, b in zip(pts, pts[1:]):
                    # Keep out-of-global-range points clipped visually by just drawing the segment.
                    canvas.create_line(a[0], a[1], b[0], b[1], fill=color, width=2)

            knot_points = self.curve_knot_points(c)
            active_handle = self.active_handle_for_curve(c)
            active_point_index = int(active_handle["point_index"]) if active_handle else None
            knot_pixel_by_index: Dict[int, Tuple[float, float]] = {}

            for idx, x, y in knot_points:
                px, py = sx(x), sy(y)
                knot_pixel_by_index[int(idx)] = (px, py)
                is_active = active_point_index == int(idx)
                fill = "#FFFFFF" if is_active else color
                outline = color if is_active else ""
                radius = 6 if is_active else 4
                canvas.create_oval(px - radius, py - radius, px + radius, py + radius, fill=fill, outline=outline, width=2 if is_active else 1)
                if self.edit_curve_points.get():
                    self._curve_hitboxes.append({
                        "kind": "knot", "curve_key": c.key, "point_index": int(idx),
                        "px": float(px), "py": float(py), "radius": 11.0,
                    })
                if self.show_point_labels.get():
                    label_fill = "#FFFFFF" if is_active else color
                    canvas.create_text(px + 8, py - 8, text=str(idx), anchor="w", fill=label_fill, font=("Consolas", 8, "bold" if is_active else "normal"))

            # Graph-editor style handles: show only the handles attached to the active knot.
            show_handles = bool(active_handle and (self.show_control_points.get() or self.edit_curve_points.get()))
            if show_handles and active_point_index in knot_pixel_by_index:
                active_px, active_py = knot_pixel_by_index[int(active_point_index)]
                for row_idx in self.active_control_rows_for_curve(c):
                    if row_idx < 0 or row_idx >= len(c.rows) or not self.curve_row_valid(c.rows[row_idx]):
                        continue
                    x, y = c.rows[row_idx][2], c.rows[row_idx][3]
                    px, py = sx(x), sy(y)
                    canvas.create_line(active_px, active_py, px, py, fill=color, dash=(4, 3), width=1)
                    canvas.create_rectangle(px - 5, py - 5, px + 5, py + 5, fill=self.PANEL, outline="#FFFFFF", width=2)
                    if self.edit_curve_points.get():
                        self._curve_hitboxes.append({
                            "kind": "control", "curve_key": c.key, "row_index": int(row_idx),
                            "px": float(px), "py": float(py), "radius": 11.0,
                        })
                    if self.show_point_labels.get():
                        canvas.create_text(px + 8, py + 8, text=f"c{row_idx}", anchor="w", fill="#FFFFFF", font=("Consolas", 8))

            canvas.create_rectangle(legend_x, legend_y - 5, legend_x + 12, legend_y + 7, fill=color, outline="")
            canvas.create_text(legend_x + 18, legend_y + 1, anchor="w", text=f"{c.label} ({c.point_count})", fill=self.FG, font=("Segoe UI", 9))
            legend_y += 18

    def draw_canvas_message(self, message: str):
        w = max(10, self.curve_canvas.winfo_width())
        h = max(10, self.curve_canvas.winfo_height())
        self.curve_canvas.create_text(w / 2, h / 2, text=message, fill=self.MUTED, font=("Segoe UI", 11))


def main(argv: List[str]) -> int:
    # Tiny CLI parse mode for quick validation without opening a GUI:
    #   python mc3_audio_curve_gui_v16.py --dump vp_ram_04.pck
    if len(argv) >= 3 and argv[1] == "--dump":
        path = Path(argv[2])
        doc = PckAudioParser(path).parse()
        print(json.dumps(doc.to_dict(), indent=2, ensure_ascii=False))
        return 0

    root = tk.Tk()
    app = AudioCurveGUI(root)
    if len(argv) >= 2 and Path(argv[1]).is_file():
        app.load_pck(Path(argv[1]))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
