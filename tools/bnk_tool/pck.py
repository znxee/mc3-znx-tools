"""MC3 vehicle .pck audio-section parser + in-place editor.

Parser adapted from mc3_audio_curve_gui_v01.py (same offsets/semantics, GUI-free).
Editing is strictly in-place: every editable structure has a fixed allocation in the
file (8 range slots of 0x2C, 8 curve rows of 0x24 per curve, fixed-size headers), so
values can be rewritten without moving any pointer.

Curve math (validated against vp_350z_04.pck): each curve row stores 3 points
(x0,y0)(cx,cy)(x1,y1) plus quadratic coefficients A,B,C with y = A*x^2 + B*x + C; the
parabola passes exactly through all 3 points, so A/B/C are recomputed from the points
on every edit (same relationship the original build pipeline used - the leftover
.audengine text files only store the 3 points per series).
"""
from __future__ import annotations

import math
import os
import shutil
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
CURVE_STORAGE_SIZE = 0x140     # fixed 0x140-byte curve records in all tested car PCKs
CURVE_ROW_SLOTS = (CURVE_STORAGE_SIZE - CURVE_HEADER_SIZE) // CURVE_POINT_SIZE  # 8 rows
UNUSED_ROW = (-1.0, -1.0, -1.0, -1.0, -1.0, -1.0, 0.0, 0.0, 0.0)

# v0.20 research: EngineRevSound fixed string inside AudioBlockFull
ENGINE_REV_SOUND_OFF = 0x68
ENGINE_REV_SOUND_SIZE = 0x20

MAIN_SLOT_RELS = set(range(0x6C, 0x94, 4))  # engine+exhaust performance slots

CURVE_FIELDS = [
    ("volume_curve", "Volume", "ptr_volume_curve", 0x88),
    ("pitch_curve", "Pitch", "ptr_pitch_curve", 0x8C),
    ("high_pitch_curve", "High Pitch", "ptr_high_pitch_curve", 0x90),
    ("upshift_curve", "Upshift", "ptr_upshift_curve", 0x94),
    ("downshift_curve", "Downshift", "ptr_downshift_curve", 0x98),
]

# editable AudioBlockFull scalars: name -> (offset, type)
BLOCK_SCALAR_FIELDS = {
    "min_rpm_rec": (0x24, "f32"),
    "max_rpm_rec": (0x28, "f32"),
    "near_dist": (0x2C, "f32"),
    "far_dist": (0x30, "f32"),
    "active_range_count": (0x34, "u32"),
    "warble_min_rpm": (0x3C, "f32"),
    "warble_max_rpm": (0x40, "f32"),
    "warble_min_rpm_delta": (0x44, "f32"),
    "warble_min_vol_delta": (0x48, "f32"),
    "warble_max_rpm_delta": (0x4C, "f32"),
    "warble_max_vol_delta": (0x50, "f32"),
    "warble_reverse_max_rpm_delta": (0x54, "f32"),
    "warble_reverse_period_rate": (0x58, "f32"),
    "boost_mix_percent": (0x5C, "f32"),
    "boost_mix_rate": (0x60, "f32"),
    "high_pitch_curve_engage_gear": (0x64, "u32"),
}

# v0.20: root pointer names updated from .vehaudio research
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
    ("ptr_common_0_94", "Common Level0", 0x94, "profile", "Common"),
    ("ptr_common_1_98", "Common Level1", 0x98, "profile", "Common"),
    ("ptr_common_2_9C", "Common Level2", 0x9C, "profile", "Common"),
    ("ptr_common_3_A0", "Common Level3", 0xA0, "profile", "Common"),
    ("ptr_common_4_A4", "Common Level4", 0xA4, "profile", "Common"),
]

# v0.20: role/editability notes per auxiliary category (from .vehaudio research)
AUX_CATEGORY_INFO = {
    "Auxiliary / TurboBlower": {
        "role": "TurboBlower Level0..2: AudioBlockFull layers; Level0 is usually empty on purpose, "
                "Level1/2 usually have Turbo_Culture1/2.",
        "edit": "Same structure as Engine/Exhaust: bank_name, EngineRevSound, ranges, RPMs, "
                "distances, warble/boost and curves.",
    },
    "Auxiliary / Impact": {
        "role": "Impact Level0: auxiliary collision/impact/scrape block.",
        "edit": "Readable strings and candidate floats only; the meaning of the floats is still being researched.",
    },
    "Auxiliary / BigAir": {
        "role": "BigAir Level0: probably an airborne-state controller/modulator, not a sample player.",
        "edit": "Raw floats only; no confirmed bank strings in the 350Z style.",
    },
    "Auxiliary / Wind": {
        "role": "Wind Level0: candidate wind/speed noise controller.",
        "edit": "Readable strings and speed/level-like floats.",
    },
    "Auxiliary / Horn": {
        "role": "Horn Level0: separate slot exposed by the .vehaudio.",
        "edit": "Usually null/empty on the cars tested; inspection only when present.",
    },
    "Auxiliary / Powerup": {
        "role": "Powerup Level0: formerly the unknown root+0x28, now mapped as Powerup.",
        "edit": "Raw strings/floats only; semantics still open.",
    },
    "Auxiliary / Fire": {
        "role": "Fire Level0: separate from Backfire; probably a fire-effect envelope/controller.",
        "edit": "Curve/controller-like data; treat as experimental.",
    },
    "Auxiliary / Wheel": {
        "role": "Wheel Level0..2: tyre/skid/chirp/burnout/surface audio.",
        "edit": "Readable strings and candidate floats (probably skid thresholds/levels).",
    },
    "Auxiliary / Suspension": {
        "role": "Suspension Level0..2: candidate suspension/hydraulics/airbag events.",
        "edit": "Readable strings and candidate floats.",
    },
    "Auxiliary / Gearshift": {
        "role": "Gearshift Level0..2: discrete gear-shift samples (separate from the "
                "Upshift/Downshift curves inside AudioBlockFull).",
        "edit": "Readable gearshift strings and event-envelope-like floats.",
    },
    "Auxiliary / Backfire": {
        "role": "Backfire Level0..2: bundle of exhaust/backfire samples. Separate from Fire.",
        "edit": "Readable Backfires:BACKFIRE strings and chance/timing/level-like floats.",
    },
    "Auxiliary / Unknown": {
        "role": "Unclassified blocks or null placeholders.",
        "edit": "Inspection only.",
    },
}


def aux_category_info(category: str) -> Dict[str, str]:
    return AUX_CATEGORY_INFO.get(category, {"role": "No mapped role.", "edit": "Inspection only."})


class PckError(Exception):
    pass


# ---------------------------------------------------------------------------
# binary helpers
# ---------------------------------------------------------------------------

def u32_le(buf, off):
    return struct.unpack_from("<I", buf, off)[0]


def f32_le(buf, off):
    return struct.unpack_from("<f", buf, off)[0]


def safe_u32(buf, off, default=0):
    try:
        return u32_le(buf, off)
    except struct.error:
        return default


def safe_f32(buf, off, default=0.0):
    try:
        v = f32_le(buf, off)
        return v if math.isfinite(v) else default
    except struct.error:
        return default


def is_nullish_u32(value):
    return value in (0, 0xCDCDCDCD) or (value & 0xFFFF0000) == 0xCDCD0000


def read_fixed_string(buf, off, size=0x20):
    if off is None or off < 0 or off >= len(buf):
        return ""
    out = bytearray()
    for i in range(off, min(len(buf), off + size)):
        b = buf[i]
        if b in (0x00, 0xCD):
            break
        out.append(b)
    return out.decode("ascii", errors="replace")


def encode_fixed_string(value: str, size: int = 0x20) -> bytes:
    raw = (value or "").encode("ascii", errors="replace")[: size - 1]
    return raw.ljust(size, b"\x00")


def sample_suffix(sample_name: str) -> str:
    return sample_name.split(":", 1)[1] if ":" in sample_name else sample_name


def replace_sample_prefix(old_sample: str, new_bank: str) -> str:
    """Keeps the receiver suffix (LOW_ENGINE etc.) and swaps only the bank prefix.
    Unprefixed samples are left untouched."""
    old_sample = old_sample or ""
    new_bank = (new_bank or "").strip()
    if not new_bank or ":" not in old_sample:
        return old_sample
    _old, suffix = old_sample.split(":", 1)
    if not suffix:
        return old_sample
    return f"{new_bank}:{suffix}"


def likely_sample_role(ranges, active_count: int) -> str:
    """Majority classification (v0.20): some vanilla cars have one semantically crossed
    sample (e.g. IDLE_TAIL inside an ENGINE layer); majority wins so donor-import
    pairing doesn't fall back to slot numbers on those."""
    active = ranges[:max(0, min(active_count, len(ranges)))]
    suffixes = [sample_suffix(r.sample_name).upper() for r in active]
    engine_count = sum(1 for s in suffixes if "_ENGINE" in s or s.endswith("ENGINE"))
    tail_count = sum(1 for s in suffixes if "_TAIL" in s or s.endswith("TAIL"))
    turbo_count = sum(1 for s in suffixes if "TURBO" in s or "BLOW" in s)
    if engine_count > tail_count:
        return "ENGINE samples"
    if tail_count > engine_count:
        return "TAIL samples"
    if engine_count and tail_count:
        return "MIXED ENGINE/TAIL"
    if turbo_count:
        return "TURBO/AUX samples"
    return "unknown samples"


def is_probable_fixed_audio_string(buf, off: int, field_size: int = 0x20) -> bool:
    """Conservative detector for fixed 0x20-byte audio string fields in raw aux blocks."""
    if off < 0 or off + 1 >= len(buf):
        return False
    if off > 0 and 0x20 <= buf[off - 1] <= 0x7E:
        return False  # middle of a larger ASCII run
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
    0x007ADED0: 0x0A0, 0x007B08B8: 0x0A0, 0x007A2440: 0x080, 0x007B0868: 0x0C0,
    0x0079E730: 0x140, 0x007B0940: 0x1F0, 0x007A2408: 0x0B0, 0x007A1E98: 0x420,
    0x007A2498: 0xA40,
}


def aux_scan_size_for_magic(magic: int, fallback: int = 0x300) -> int:
    return AUX_MAGIC_SCAN_SIZES.get(int(magic) & 0xFFFFFFFF, fallback)


def iter_fixed_audio_strings(buf, start_off, max_size: int):
    """(relative_offset, text) for aligned fixed audio strings in a raw aux block."""
    if start_off is None or start_off < 0 or start_off >= len(buf):
        return []
    end = min(len(buf), start_off + max_size)
    out = []
    seen = set()
    for off in range(start_off, max(start_off, end - 3), 4):
        if not is_probable_fixed_audio_string(buf, off, 0x20):
            continue
        rel = off - start_off
        if rel not in seen:
            seen.add(rel)
            out.append((rel, read_fixed_string(buf, off, 0x20)))
    return out


def read_loose_ascii_string_entries(buf, off, size=0x100):
    """Readable ASCII runs with offsets, for aux diagnostics/editing."""
    if off is None or off < 0 or off >= len(buf):
        return []
    end = min(len(buf), off + size)
    entries = []
    seen = set()
    cur = bytearray()
    cur_start = None

    def flush():
        nonlocal cur, cur_start
        if cur_start is not None and len(cur) >= 3:
            s = cur.decode("ascii", errors="replace")
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


def raw_float_preview(buf, off, size=0x100):
    """Coarse float-candidate scan of a raw aux block, for research/edit tables."""
    if off is None or off < 0 or off >= len(buf):
        return []
    end = min(len(buf), off + size)
    out = []
    for pos in range(off + 4, end - 3, 4):
        raw = safe_u32(buf, pos)
        val = safe_f32(buf, pos)
        if raw in (0, 0xCDCDCDCD):
            continue
        if 0x01000000 <= raw <= 0x0FFFFFFF:
            continue  # looks like a pointer/magic
        if math.isfinite(val) and -100000.0 <= val <= 100000.0:
            out.append({"file_off": pos, "rel_off": pos - off, "float": val, "u32": raw})
        if len(out) >= 48:
            break
    return out


def fit_quadratic(p0: Tuple[float, float], pc: Tuple[float, float], p1: Tuple[float, float]):
    """Parabola y=A x^2+B x+C through 3 points; degenerates to line/constant gracefully."""
    x0, y0 = p0
    xc, yc = pc
    x1, y1 = p1
    try:
        if math.isclose(x0, x1):
            return 0.0, 0.0, y0
        if math.isclose(x0, xc) or math.isclose(xc, x1):
            b = (y1 - y0) / (x1 - x0)
            return 0.0, b, y0 - b * x0
        a = ((y0 - yc) / (x0 - xc) - (yc - y1) / (xc - x1)) / (x0 - x1)
        b = (y0 - yc) / (x0 - xc) - a * (x0 + xc)
        c = y0 - a * x0 * x0 - b * x0
        return a, b, c
    except ZeroDivisionError:
        return 0.0, 0.0, y0


# ---------------------------------------------------------------------------
# data model
# ---------------------------------------------------------------------------

@dataclass
class AudioRange:
    index: int
    file_off: int
    sample_name: str
    min_rpm: float
    max_rpm: float
    mid_rpm: float
    active: bool


@dataclass
class CurveData:
    key: str
    label: str
    ptr_va: int = 0
    file_off: Optional[int] = None
    magic: int = 0
    min_x: float = 0.0        # curve header +0x04..+0x10 (v0.20 naming)
    min_y: float = 0.0
    max_x: float = 0.0
    max_y: float = 0.0
    point_count: int = 0      # +0x14: ActiveSeriesCount (alias kept for compatibility)
    ptr_points: int = 0
    points_file_off: Optional[int] = None
    rows: List[List[float]] = field(default_factory=list)
    warning: str = ""

    @property
    def active_series_count(self) -> int:
        return self.point_count

    @property
    def segments(self) -> List[Tuple[float, ...]]:
        """The editable 3-point representation of each active row."""
        return [tuple(r[:6]) for r in self.rows[: max(0, self.point_count)]]


@dataclass
class AudioBlock:
    key: str
    label: str
    category: str
    root_rel: int
    ptr_va: int
    file_off: Optional[int]
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


@dataclass
class ProfileBlock:
    key: str
    label: str
    root_rel: int
    ptr_va: int
    file_off: Optional[int]
    magic: int = 0
    params: List[float] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class RawAuxBlock:
    key: str
    label: str
    category: str
    root_rel: int
    ptr_va: int
    file_off: Optional[int]
    kind: str = "raw"
    magic: int = 0
    found_strings: List[str] = field(default_factory=list)
    found_string_entries: List[dict] = field(default_factory=list)   # {file_off, rel_off, text}
    float_preview: List[dict] = field(default_factory=list)           # {file_off, rel_off, float, u32}


@dataclass
class PckDocument:
    path: Path
    buf: bytes
    base_va: int
    root_off: int
    root_magic: int
    root_name: str
    blocks: List[AudioBlock] = field(default_factory=list)
    profiles: List[ProfileBlock] = field(default_factory=list)
    raw_aux_blocks: List[RawAuxBlock] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def block_by_key(self, key: str) -> Optional[AudioBlock]:
        for b in self.blocks:
            if b.key == key:
                return b
        return None

    def bank_names(self) -> List[str]:
        seen = []
        for b in self.blocks:
            if b.bank_name and b.bank_name not in seen:
                seen.append(b.bank_name)
        return seen


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def parse_pck(path) -> PckDocument:
    path = Path(path)
    buf = path.read_bytes()
    if len(buf) < 0xC0:
        raise PckError("File too small to be a vehicle PCK with an audio root.")

    ptr_va = u32_le(buf, 0x00)
    base_va = ptr_va - 0x80

    def va_to_off(va, size=1):
        if is_nullish_u32(va):
            return None
        off = va - base_va
        if off < 0 or off + size > len(buf):
            return None
        return off

    warnings: List[str] = []
    root_va = u32_le(buf, 0xA8)
    root_off = va_to_off(root_va, 0xB8)
    if root_off is None:
        raise PckError(f"invalid ptr_audio_root (0xA8): 0x{root_va:08X} - this PCK has no readable audio section.")

    root_magic = u32_le(buf, root_off)
    if root_magic not in AUDIO_ROOT_MAGICS:
        warnings.append(f"Unknown audio root magic: 0x{root_magic:08X}")

    name_off = va_to_off(u32_le(buf, root_off + 0x04), 1)
    root_name = read_fixed_string(buf, name_off, 0x80) if name_off is not None else ""

    doc = PckDocument(path=path, buf=buf, base_va=base_va, root_off=root_off,
                      root_magic=root_magic, root_name=root_name, warnings=warnings)

    for ptr_name, label, root_rel, kind, category in ROOT_POINTERS:
        ptr_va2 = safe_u32(buf, root_off + root_rel)
        file_off = va_to_off(ptr_va2, 4)
        if file_off is None:
            continue
        key = ptr_name.replace("ptr_", "")
        if kind == "block":
            doc.blocks.append(_parse_block(buf, va_to_off, key, label, category, root_rel, ptr_va2, file_off))
        elif kind == "profile":
            magic = safe_u32(buf, file_off)
            profile = ProfileBlock(key=key, label=label, root_rel=root_rel, ptr_va=ptr_va2, file_off=file_off,
                                   magic=magic,
                                   params=[safe_f32(buf, file_off + 0x04 + i * 4) for i in range(22)])
            if magic not in PROFILE_MAGICS:
                profile.warnings.append(f"Unknown profile magic: 0x{magic:08X}")
            doc.profiles.append(profile)
        else:
            preview_size = 0x200 if kind == "raw_bundle" else 0x100
            entries = read_loose_ascii_string_entries(buf, file_off + 0x04, preview_size)
            for e in entries:
                e["rel_off"] = e["file_off"] - file_off  # normalize: relative to block start
            doc.raw_aux_blocks.append(RawAuxBlock(
                key=key, label=label, category=category, root_rel=root_rel, ptr_va=ptr_va2,
                file_off=file_off, kind=kind, magic=safe_u32(buf, file_off),
                found_strings=[e["text"] for e in entries],
                found_string_entries=entries,
                float_preview=raw_float_preview(buf, file_off, min(preview_size, 0x120))))

    cat_order = {"Engine": 0, "Exhaust": 1, "Auxiliary / TurboBlower": 2}
    doc.blocks.sort(key=lambda b: (cat_order.get(b.category, 99), b.root_rel))
    doc.profiles.sort(key=lambda p: p.root_rel)
    return doc


def _parse_block(buf, va_to_off, key, label, category, root_rel, ptr_va, file_off) -> AudioBlock:
    warnings: List[str] = []
    magic = safe_u32(buf, file_off)
    if magic not in AUDIO_BLOCK_MAGICS:
        warnings.append(f"Unexpected AudioBlockFull magic: 0x{magic:08X}")

    active_count = safe_u32(buf, file_off + 0x34)
    ptr_range_table = safe_u32(buf, file_off + 0x38)
    range_table_off = va_to_off(ptr_range_table, RANGE_ENTRY_SIZE)

    ranges: List[AudioRange] = []
    if range_table_off is not None:
        for i in range(RANGE_ALLOCATED_COUNT):
            roff = range_table_off + i * RANGE_ENTRY_SIZE
            if roff + RANGE_ENTRY_SIZE > len(buf):
                break
            ranges.append(AudioRange(
                index=i, file_off=roff,
                sample_name=read_fixed_string(buf, roff, 0x20),
                min_rpm=safe_f32(buf, roff + 0x20),
                max_rpm=safe_f32(buf, roff + 0x24),
                mid_rpm=safe_f32(buf, roff + 0x28),
                active=i < active_count))

    block = AudioBlock(
        key=key, label=label, category=category, root_rel=root_rel, ptr_va=ptr_va, file_off=file_off,
        magic=magic, bank_name=read_fixed_string(buf, file_off + 0x04, 0x20),
        active_range_count=active_count, ptr_range_table=ptr_range_table,
        range_table_file_off=range_table_off, ranges=ranges, warnings=warnings,
        engine_rev_sound=read_fixed_string(buf, file_off + ENGINE_REV_SOUND_OFF, ENGINE_REV_SOUND_SIZE))

    for name, (rel, kind) in BLOCK_SCALAR_FIELDS.items():
        if name == "active_range_count":
            continue
        if kind == "f32":
            setattr(block, name, safe_f32(buf, file_off + rel))
        else:
            setattr(block, name, safe_u32(buf, file_off + rel))

    for curve_key, curve_label, _pk, ptr_rel in CURVE_FIELDS:
        ptr_curve = safe_u32(buf, file_off + ptr_rel)
        curve = CurveData(key=curve_key, label=curve_label, ptr_va=ptr_curve)
        curve_off = va_to_off(ptr_curve, 0x20)
        curve.file_off = curve_off
        if curve_off is not None:
            curve.magic = safe_u32(buf, curve_off)
            curve.min_x = safe_f32(buf, curve_off + 0x04)
            curve.min_y = safe_f32(buf, curve_off + 0x08)
            curve.max_x = safe_f32(buf, curve_off + 0x0C)
            curve.max_y = safe_f32(buf, curve_off + 0x10)
            curve.point_count = safe_u32(buf, curve_off + 0x14)
            curve.ptr_points = safe_u32(buf, curve_off + 0x18)
            if curve.magic != CURVE_MAGIC:
                curve.warning = f"Unexpected curve magic: 0x{curve.magic:08X}"
            if curve.point_count > CURVE_ROW_SLOTS:
                curve.warning = (curve.warning + " " if curve.warning else "") + \
                    f"point_count={curve.point_count} above the {CURVE_ROW_SLOTS} slots"
            else:
                points_off = va_to_off(curve.ptr_points, curve.point_count * CURVE_POINT_SIZE)
                if points_off is None and curve_off + 0x20 + curve.point_count * CURVE_POINT_SIZE <= len(buf):
                    points_off = curve_off + 0x20
                curve.points_file_off = points_off
                if points_off is not None:
                    for i in range(CURVE_ROW_SLOTS):
                        row_off = points_off + i * CURVE_POINT_SIZE
                        if row_off + CURVE_POINT_SIZE > len(buf):
                            break
                        curve.rows.append([safe_f32(buf, row_off + j * 4) for j in range(CURVE_POINT_FLOATS)])
        block.curves[curve_key] = curve

    return block


# ---------------------------------------------------------------------------
# curve row helpers (shared by editor + donor import)
# ---------------------------------------------------------------------------

def pack_curve_row(row) -> bytes:
    vals = [float(v) for v in row[:CURVE_POINT_FLOATS]]
    if len(vals) != CURVE_POINT_FLOATS:
        raise PckError("a curve row needs exactly 9 floats")
    return struct.pack("<" + "f" * CURVE_POINT_FLOATS, *vals)


def eval_curve_rows(rows, x: float) -> float:
    """Evaluates a piecewise curve (list of 9-float rows) at x using stored A/B/C,
    clamping to the nearest endpoint outside the domain."""
    usable = [r for r in rows if len(r) >= CURVE_POINT_FLOATS]
    if not usable:
        return 0.0
    for r in usable:
        x0, _y0, _cx, _cy, x1 = r[0], r[1], r[2], r[3], r[4]
        lo, hi = (x0, x1) if x0 <= x1 else (x1, x0)
        if lo <= x <= hi:
            a, b, c = r[6], r[7], r[8]
            return a * x * x + b * x + c
    first, last = usable[0], usable[-1]
    if x < min(first[0], first[4]):
        return first[1]
    return last[5]


def resample_curve_rows(source_rows, target_count: int):
    """Compress/expand a curve into target_count rows (used when a donor curve has
    more active rows than the receiver has allocated). Preserves the donor's first
    and final endpoints exactly."""
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
    out = []
    for i in range(target_count):
        t0 = i / target_count
        t1 = (i + 1) / target_count
        x0 = domain_start + (domain_end - domain_start) * t0
        x1 = domain_start + (domain_end - domain_start) * t1
        xm = (x0 + x1) * 0.5
        y0 = eval_curve_rows(usable, x0)
        ym = eval_curve_rows(usable, xm)
        y1 = eval_curve_rows(usable, x1)
        a, b, c = fit_quadratic((x0, y0), (xm, ym), (x1, y1))
        out.append([x0, y0, xm, ym, x1, y1, a, b, c])
    return out


def curve_allocated_row_capacity(buf_len: int, curve: "CurveData") -> int:
    """Writable row capacity of an existing curve record (fixed 0x140 storage)."""
    if curve.points_file_off is None:
        return 0
    cap = CURVE_ROW_SLOTS
    max_bytes = buf_len - curve.points_file_off
    return max(0, min(cap, max_bytes // CURVE_POINT_SIZE))


# ---------------------------------------------------------------------------
# donor/target pairing for audio imports (v0.20 logic: role first for main slots)
# ---------------------------------------------------------------------------

def is_main_engine_tail_slot(block) -> bool:
    return getattr(block, "root_rel", None) in MAIN_SLOT_RELS and \
        getattr(block, "category", "") in ("Engine", "Exhaust", "Engine / Root A", "Exhaust / Root B")


def import_pair_key(block):
    if is_main_engine_tail_slot(block):
        role = getattr(block, "role", "unknown samples")
        if role == "ENGINE samples":
            return ("sample_role", "ENGINE")
        if role == "TAIL samples":
            return ("sample_role", "TAIL")
    return ("root_rel", getattr(block, "root_rel", None))


def pair_audio_blocks_for_import(donor_blocks, target_blocks):
    """Pairs donor->target blocks by logical sample role (ENGINE/TAIL) first, then by
    root-relative slot, so cars with semantically swapped layers don't get engine
    audio written into tail slots. Returns (pairs, stats, warnings)."""
    donor_groups = {}
    for b in sorted(donor_blocks, key=lambda x: (getattr(x, "root_rel", 9999), getattr(x, "label", ""))):
        donor_groups.setdefault(import_pair_key(b), []).append(b)

    target_ordinals = {}
    donor_by_rel = {getattr(b, "root_rel", None): b for b in donor_blocks}
    used_ids = set()
    pairs = []
    stats = {"role_matched": 0, "root_rel_matched": 0, "fallback_root_rel": 0, "unmatched": 0}
    warnings = []

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
            rel_donor = donor_by_rel.get(getattr(target, "root_rel", None))
            if rel_donor is not None and id(rel_donor) not in used_ids:
                donor = rel_donor
                mode = "fallback_root_rel"

        if donor is None:
            stats["unmatched"] += 1
            warnings.append(f"No donor for {getattr(target, 'label', '<target>')} (key {key}).")
            continue
        used_ids.add(id(donor))
        stats["role_matched" if mode == "role" else
              "root_rel_matched" if mode == "root_rel" else "fallback_root_rel"] += 1
        if mode == "fallback_root_rel":
            warnings.append(f"{getattr(target, 'label', '<target>')}: no pair by role; used the per-slot fallback "
                            f"({getattr(donor, 'label', '<donor>')}).")
        pairs.append((donor, target, mode))

    return pairs, stats, warnings


# ---------------------------------------------------------------------------
# editor
# ---------------------------------------------------------------------------

class PckEditor:
    """In-place edits over a parsed PCK. All writes are bounded to the fixed-size
    structures the parser located; nothing is moved or resized."""

    def __init__(self, doc: PckDocument):
        self.doc = doc
        self.buf = bytearray(doc.buf)
        self.dirty = False

    # -- primitives --
    def _put_f32(self, off, value):
        struct.pack_into("<f", self.buf, off, float(value))
        self.dirty = True

    def _put_u32(self, off, value):
        struct.pack_into("<I", self.buf, off, int(value) & 0xFFFFFFFF)
        self.dirty = True

    def _put_fixed_string(self, off, text, size):
        raw = text.encode("ascii", errors="replace")[: size - 1]
        self.buf[off:off + size] = raw.ljust(size, b"\x00")
        self.dirty = True

    # -- block fields --
    def set_block_scalar(self, block: AudioBlock, name: str, value):
        if name not in BLOCK_SCALAR_FIELDS:
            raise PckError(f"unknown field: {name}")
        rel, kind = BLOCK_SCALAR_FIELDS[name]
        if block.file_off is None:
            raise PckError("block without a valid offset")
        if kind == "f32":
            self._put_f32(block.file_off + rel, value)
        else:
            self._put_u32(block.file_off + rel, value)

    def set_bank_name(self, block: AudioBlock, name: str, update_sample_prefixes: bool = True):
        """v0.20 behavior: bank_name and range sample-name prefixes are separate fields;
        editing only the bank label usually leaves samples pointing at the old bank
        (E_Old:LOW_ENGINE). By default the prefixes are updated together."""
        if block.file_off is None:
            raise PckError("block without a valid offset")
        self._put_fixed_string(block.file_off + 0x04, name, 0x20)
        changed = 0
        if update_sample_prefixes and (name or "").strip():
            for r in block.ranges:
                old = read_fixed_string(self.buf, r.file_off, 0x20)
                new = replace_sample_prefix(old, name.strip())
                if new != old:
                    if len(new.encode("ascii", errors="replace")) > 31:
                        raise PckError(f"sample_name too long after the bank change: {new!r}")
                    self._put_fixed_string(r.file_off, new, 0x20)
                    changed += 1
        return changed

    def set_engine_rev_sound(self, block: AudioBlock, text: str):
        if block.file_off is None:
            raise PckError("block without a valid offset")
        self._put_fixed_string(block.file_off + ENGINE_REV_SOUND_OFF, text, ENGINE_REV_SOUND_SIZE)

    def set_aux_string(self, raw_block: RawAuxBlock, rel_off: int, text: str):
        """Overwrites an ASCII string inside a raw auxiliary block, bounded by the
        original string's field (won't grow past the next non-string byte)."""
        if raw_block.file_off is None:
            raise PckError("auxiliary block without a valid offset")
        off = raw_block.file_off + rel_off
        old = read_fixed_string(self.buf, off, 0x20)
        if not old:
            raise PckError("there is no string at that offset to replace")
        limit = min(0x20, len(old) + 1)  # keep within the original field + terminator
        if len(text.encode("ascii", errors="replace")) >= limit:
            raise PckError(f"text too long (max {limit - 1} chars for this field)")
        self.buf[off:off + limit] = text.encode("ascii", errors="replace").ljust(limit, b"\x00")
        self.dirty = True

    def set_aux_float(self, raw_block: RawAuxBlock, rel_off: int, value: float):
        if raw_block.file_off is None:
            raise PckError("auxiliary block without a valid offset")
        self._put_f32(raw_block.file_off + rel_off, value)

    def set_range(self, block: AudioBlock, index: int, sample_name: str = None,
                  min_rpm: float = None, mid_rpm: float = None, max_rpm: float = None):
        if not (0 <= index < len(block.ranges)):
            raise PckError(f"range {index} out of bounds")
        roff = block.ranges[index].file_off
        if sample_name is not None:
            self._put_fixed_string(roff, sample_name, 0x20)
        if min_rpm is not None:
            self._put_f32(roff + 0x20, min_rpm)
        if max_rpm is not None:
            self._put_f32(roff + 0x24, max_rpm)
        if mid_rpm is not None:
            self._put_f32(roff + 0x28, mid_rpm)

    def set_curve_segments(self, block: AudioBlock, curve_key: str,
                           segments: List[Tuple[float, float, float, float, float, float]]):
        """segments: list of (x0,y0,cx,cy,x1,y1). A/B/C are recomputed per segment.
        Unused slots (up to 8) are refilled with the standard -1 pattern."""
        curve = block.curves.get(curve_key)
        if curve is None or curve.file_off is None or curve.points_file_off is None:
            raise PckError(f"curve {curve_key} without a valid point area")
        if len(segments) > CURVE_ROW_SLOTS:
            raise PckError(f"at most {CURVE_ROW_SLOTS} segments per curve")

        for i in range(CURVE_ROW_SLOTS):
            row_off = curve.points_file_off + i * CURVE_POINT_SIZE
            if row_off + CURVE_POINT_SIZE > len(self.buf):
                raise PckError("the point area runs past the file")
            if i < len(segments):
                x0, y0, cx, cy, x1, y1 = segments[i]
                a, b, c = fit_quadratic((x0, y0), (cx, cy), (x1, y1))
                vals = (x0, y0, cx, cy, x1, y1, a, b, c)
            else:
                vals = UNUSED_ROW
            for j, v in enumerate(vals):
                self._put_f32(row_off + j * 4, v)

        self._put_u32(curve.file_off + 0x14, len(segments))

    def set_profile_param(self, profile: ProfileBlock, index: int, value: float):
        if not (0 <= index < 22):
            raise PckError("parameter index out of bounds (0-21)")
        if profile.file_off is None:
            raise PckError("profile without a valid offset")
        self._put_f32(profile.file_off + 0x04 + index * 4, value)

    # -- donor import (ported from mc3_audio_curve_gui v0.20) --
    def _wr_bytes(self, dst_off, raw, report, counter):
        if bytes(self.buf[dst_off:dst_off + len(raw)]) != raw:
            self.buf[dst_off:dst_off + len(raw)] = raw
            self.dirty = True
            report[counter] = report.get(counter, 0) + 1
            return True
        return False

    def import_from_document(self, donor: PckDocument, mode: str = "conservative") -> dict:
        """Copies audio config from a donor PCK into this one, without ever copying
        donor pointers. mode='conservative': bank names, sample prefixes, aux strings,
        engine/exhaust RPMs and shift curves. mode='experimental': all safe scalar
        fields, full range tables, all curves (resampled if needed), EngineRevSound,
        aux strings+floats and Common params."""
        target = self.doc
        report = {"warnings": [], "skipped": 0}
        pairs, stats, pair_warnings = pair_audio_blocks_for_import(donor.blocks, target.blocks)
        report["pair_role_matched"] = stats["role_matched"]
        report["pair_root_rel_matched"] = stats["root_rel_matched"]
        report["pair_fallback_root_rel"] = stats["fallback_root_rel"]
        report["skipped"] += stats["unmatched"]
        report["warnings"].extend(pair_warnings)

        for dblock, tblock, _pmode in pairs:
            if tblock.file_off is None or dblock.file_off is None:
                report["skipped"] += 1
                continue
            if mode == "experimental":
                self._import_block_experimental(donor, dblock, tblock, report)
            else:
                self._import_block_conservative(donor, dblock, tblock, report)

        self._import_aux(donor, report, copy_floats=(mode == "experimental"))
        if mode == "experimental":
            self._import_commons(donor, report)
        return report

    def _import_block_conservative(self, donor, dblock, tblock, report):
        donor_bank = (dblock.bank_name or "").strip()
        if donor_bank:
            old_bank = read_fixed_string(self.buf, tblock.file_off + 0x04, 0x20)
            if old_bank != donor_bank:
                self._wr_bytes(tblock.file_off + 0x04, encode_fixed_string(donor_bank, 0x20),
                               report, "block_changed")

        common_active = min(max(0, tblock.active_range_count), max(0, dblock.active_range_count),
                            len(tblock.ranges), len(dblock.ranges))
        for i in range(common_active):
            tr = tblock.ranges[i]
            old_sample = read_fixed_string(self.buf, tr.file_off, 0x20)
            new_sample = replace_sample_prefix(old_sample, donor_bank)
            if new_sample != old_sample:
                self._wr_bytes(tr.file_off, encode_fixed_string(new_sample, 0x20),
                               report, "sample_changed")

        # conservative RPM copy: main engine/exhaust slots only
        if tblock.root_rel in MAIN_SLOT_RELS and dblock.root_rel in MAIN_SLOT_RELS:
            for rel in (0x24, 0x28):
                val = safe_f32(donor.buf, dblock.file_off + rel)
                if val > 0.0 and safe_f32(self.buf, tblock.file_off + rel) != val:
                    self._put_f32(tblock.file_off + rel, val)
                    report["rpm_changed"] = report.get("rpm_changed", 0) + 1
            for i in range(min(max(0, tblock.active_range_count), max(0, dblock.active_range_count),
                               len(tblock.ranges), len(dblock.ranges))):
                dr, tr = dblock.ranges[i], tblock.ranges[i]
                for rel in (0x20, 0x24, 0x28):
                    val = safe_f32(donor.buf, dr.file_off + rel)
                    if val > 0.0 and safe_f32(self.buf, tr.file_off + rel) != val:
                        self._put_f32(tr.file_off + rel, val)
                        report["rpm_changed"] = report.get("rpm_changed", 0) + 1

        # shift-event envelopes (gear-change feel follows the donor's samples)
        for curve_key in ("upshift_curve", "downshift_curve"):
            tc, dc = tblock.curves.get(curve_key), dblock.curves.get(curve_key)
            if tc and dc:
                self._import_curve(donor, dc, tc, curve_key, tblock.label, report,
                                    counter="shift_curve_changed")

    def _import_block_experimental(self, donor, dblock, tblock, report):
        if tblock.magic != dblock.magic:
            report["warnings"].append(f"{tblock.label}: magic differs from the donor; block skipped.")
            report["skipped"] += 1
            return
        self._wr_bytes(tblock.file_off + 0x04,
                       bytes(donor.buf[dblock.file_off + 0x04:dblock.file_off + 0x24]),
                       report, "block_changed")

        d_high = dblock.curves.get("high_pitch_curve")
        t_high = tblock.curves.get("high_pitch_curve")
        d_active = bool(d_high and d_high.point_count > 0)
        t_active = bool(t_high and t_high.point_count > 0)
        copy_high = d_active and t_active
        if t_active and not d_active:
            report["warnings"].append(f"{tblock.label}: donor High Pitch empty; kept the target's.")
        elif d_active and not t_active:
            report["warnings"].append(f"{tblock.label}: target has no active High Pitch; donor High Pitch ignored.")

        # all safe scalars; +0x38 range ptr and +0x88..+0x98 curve ptrs never copied
        scalar_ranges = [(0x24, 0x10), (0x34, 0x04),
                         (0x3C, 0x2C) if copy_high else (0x3C, 0x28),
                         (ENGINE_REV_SOUND_OFF, ENGINE_REV_SOUND_SIZE), (0x9C, 0x04)]
        for rel, size in scalar_ranges:
            raw = bytes(donor.buf[dblock.file_off + rel:dblock.file_off + rel + size])
            self._wr_bytes(tblock.file_off + rel, raw, report, "block_changed")

        # full range table (inactive rows included); range-table pointer untouched
        common_ranges = min(len(tblock.ranges), len(dblock.ranges))
        if common_ranges < len(dblock.ranges):
            report["warnings"].append(
                f"{tblock.label}: target has only {common_ranges}/{len(dblock.ranges)} ranges; partial copy.")
        for i in range(common_ranges):
            raw = bytes(donor.buf[dblock.ranges[i].file_off:dblock.ranges[i].file_off + RANGE_ENTRY_SIZE])
            self._wr_bytes(tblock.ranges[i].file_off, raw, report, "range_changed")

        for curve_key, _cl, _pk, _pr in CURVE_FIELDS:
            if curve_key == "high_pitch_curve" and not copy_high:
                continue
            tc, dc = tblock.curves.get(curve_key), dblock.curves.get(curve_key)
            if tc and dc:
                self._import_curve(donor, dc, tc, curve_key, tblock.label, report,
                                    counter="curve_changed")

    def _import_curve(self, donor, dc, tc, curve_key, target_label, report, counter):
        """Copies one curve: header values (never ptr_points) + rows, resampling when
        the donor has more active rows than the receiver has allocated."""
        if tc.file_off is None or dc.file_off is None:
            report["skipped"] += 1
            return
        if tc.magic != dc.magic:
            report["warnings"].append(f"{target_label}/{curve_key}: curve magic differs; skipped.")
            report["skipped"] += 1
            return
        if tc.points_file_off is None or dc.points_file_off is None:
            if dc.point_count or tc.point_count:
                report["warnings"].append(f"{target_label}/{curve_key}: no point table; skipped.")
            report["skipped"] += 1
            return

        for rel, size in ((0x04, 0x10), (0x1C, 0x04)):  # min/max XY + pad; never +0x18
            raw = bytes(donor.buf[dc.file_off + rel:dc.file_off + rel + size])
            self._wr_bytes(tc.file_off + rel, raw, report, counter)

        capacity = curve_allocated_row_capacity(len(self.buf), tc)
        donor_count = max(0, int(dc.point_count))
        donor_rows = dc.rows[:min(donor_count, len(dc.rows))]
        if capacity <= 0:
            if donor_count:
                report["warnings"].append(f"{target_label}/{curve_key}: target has no row capacity; skipped.")
                report["skipped"] += 1
            return
        if donor_count <= capacity:
            rows = [list(r[:CURVE_POINT_FLOATS]) for r in donor_rows]
            new_count = donor_count
        else:
            rows = resample_curve_rows(donor_rows, capacity)
            new_count = capacity
            report["warnings"].append(
                f"{target_label}/{curve_key}: donor has {donor_count} rows > capacity {capacity}; "
                f"curve resampled to {new_count}.")
        if safe_u32(self.buf, tc.file_off + 0x14) != new_count:
            self._put_u32(tc.file_off + 0x14, new_count)
            report[counter] = report.get(counter, 0) + 1
        for i, row in enumerate(rows):
            self._wr_bytes(tc.points_file_off + i * CURVE_POINT_SIZE, pack_curve_row(row),
                           report, counter)

    def _import_aux(self, donor, report, copy_floats: bool):
        donor_raw_by_rel = {r.root_rel: r for r in donor.raw_aux_blocks}
        for traw in self.doc.raw_aux_blocks:
            draw = donor_raw_by_rel.get(traw.root_rel)
            if draw is None or traw.file_off is None or draw.file_off is None:
                continue
            for rel_off, donor_text in iter_fixed_audio_strings(
                    donor.buf, draw.file_off, aux_scan_size_for_magic(draw.magic)):
                dst = traw.file_off + rel_off
                if dst + 0x20 > len(self.buf):
                    report["skipped"] += 1
                    continue
                if not is_probable_fixed_audio_string(self.buf, dst, 0x20):
                    continue
                self._wr_bytes(dst, encode_fixed_string(donor_text, 0x20), report, "aux_string_changed")

            if copy_floats and traw.magic == draw.magic:
                for entry in draw.float_preview:
                    rel_off = int(entry.get("rel_off", -1))
                    if rel_off < 4:
                        continue
                    dst = traw.file_off + rel_off
                    if dst + 4 > len(self.buf):
                        report["skipped"] += 1
                        continue
                    in_string = any(
                        int(s.get("rel_off", -999999)) <= rel_off <
                        int(s.get("rel_off", -999999)) + min(0x20, len(str(s.get("text", ""))) + 1)
                        for s in traw.found_string_entries)
                    if in_string:
                        continue
                    val = float(entry.get("float", 0.0))
                    if safe_f32(self.buf, dst) != val:
                        self._put_f32(dst, val)
                        report["aux_float_changed"] = report.get("aux_float_changed", 0) + 1
            elif copy_floats and traw.magic != draw.magic:
                report["warnings"].append(f"{traw.label}: aux magic differs; floats not copied.")

    def _import_commons(self, donor, report):
        donor_by_rel = {p.root_rel: p for p in donor.profiles}
        for tprof in self.doc.profiles:
            dprof = donor_by_rel.get(tprof.root_rel)
            if dprof is None or tprof.file_off is None or dprof.file_off is None:
                continue
            if tprof.magic != dprof.magic:
                report["warnings"].append(f"{tprof.label}: Common magic differs; skipped.")
                report["skipped"] += 1
                continue
            raw = bytes(donor.buf[dprof.file_off + 0x04:dprof.file_off + 0x60])
            self._wr_bytes(tprof.file_off + 0x04, raw, report, "common_changed")

    # -- persistence --
    def save(self, out_path=None, backup: bool = True) -> str:
        out_path = Path(out_path) if out_path else self.doc.path
        if backup and out_path.exists():
            bak = out_path.with_suffix(out_path.suffix + ".bak")
            if not bak.exists():
                shutil.copy2(out_path, bak)
        with open(out_path, "wb") as f:
            f.write(bytes(self.buf))
        return str(out_path)

    def reparse(self) -> PckDocument:
        """Re-parses the edited buffer (without saving) so views stay in sync."""
        tmp_doc = self.doc
        doc = parse_pck_bytes(bytes(self.buf), tmp_doc.path)
        return doc


def parse_pck_bytes(buf: bytes, path) -> PckDocument:
    import tempfile
    # parse_pck reads from disk; small shim to parse an in-memory buffer
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pck")
    try:
        tmp.write(buf)
        tmp.close()
        doc = parse_pck(tmp.name)
        doc.path = Path(path)
        return doc
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# bank resolution: find <bank>.bnk / <bank>.td for a block's bank_name
# ---------------------------------------------------------------------------

def find_bank_files(bank_name: str, search_dirs: List[str]) -> Tuple[Optional[str], Optional[str]]:
    """Case-insensitive search for bank_name.bnk and bank_name.td in search_dirs.
    Returns (bnk_path or None, td_path or None)."""
    if not bank_name:
        return None, None
    want_bnk = bank_name.lower() + ".bnk"
    want_td = bank_name.lower() + ".td"
    found_bnk = found_td = None
    for d in search_dirs:
        if not d or not os.path.isdir(d):
            continue
        try:
            entries = os.listdir(d)
        except OSError:
            continue
        for e in entries:
            el = e.lower()
            if el == want_bnk and not found_bnk:
                found_bnk = os.path.join(d, e)
            elif el == want_td and not found_td:
                found_td = os.path.join(d, e)
        if found_bnk and found_td:
            break
    return found_bnk, found_td


def default_bank_search_dirs(pck_path) -> List[str]:
    """Guesses likely bank folders relative to a vehicle pck inside an assets tree:
    <assets>/audio/banks, plus the pck's own folder."""
    dirs = []
    p = Path(pck_path).resolve()
    dirs.append(str(p.parent))
    for parent in p.parents:
        if parent.name.lower() == "assets":
            dirs.append(str(parent / "audio" / "banks"))
            break
    return dirs
