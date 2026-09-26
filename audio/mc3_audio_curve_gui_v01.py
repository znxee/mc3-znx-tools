#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MC3 Vehicle Audio Curve GUI v0.3

PCK-first prototype.

Main changes from v0.1:
- loads .pck directly through File > Open PCK;
- no PatternData JSON is required from the user;
- parses the audio-root structure internally using the current audio pattern layout;
- shows Engine / Exhaust / Auxiliary audio blocks, RPM ranges and curves;
- can export the parsed data as JSON for debugging/research, but JSON is not an input requirement.

This version is still read-only. It is intentionally conservative before direct PCK writing is added.
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
    0x007A1DF0: "350Z / SRT4 style AudioBlockFull / Aux Audio",
    0x007A30F8: "DeVille style AudioBlockFull",
}

PROFILE_MAGICS = {
    0x007A1A80: "350Z / SRT4 style Profile",
    0x007A2D88: "DeVille style Profile",
}

CURVE_MAGIC = 0x0079E730
RANGE_ENTRY_SIZE = 0x2C
RANGE_ALLOCATED_COUNT = 8
CURVE_POINT_FLOATS = 9
CURVE_POINT_SIZE = CURVE_POINT_FLOATS * 4

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

ROOT_POINTERS = [
    ("ptr_explosion_18", "Explosion / Impact", 0x18, "raw", "Auxiliary / Explosion"),
    ("ptr_unknown_1C", "Unknown 1C", 0x1C, "raw", "Auxiliary / Unknown"),
    ("ptr_wind_20", "Wind", 0x20, "raw", "Auxiliary / Wind"),
    ("ptr_unknown_28", "Unknown 28", 0x28, "raw", "Auxiliary / Unknown"),
    ("ptr_curve_or_controller_2C", "Curve / Controller", 0x2C, "raw", "Auxiliary / Unknown"),

    ("ptr_tireskid_0_30", "Tire Skid 0", 0x30, "raw", "Auxiliary / Tire Skids"),
    ("ptr_tireskid_1_34", "Tire Skid 1", 0x34, "raw", "Auxiliary / Tire Skids"),
    ("ptr_tireskid_2_38", "Tire Skid 2", 0x38, "raw", "Auxiliary / Tire Skids"),

    ("ptr_suspension_0_3C", "Suspension 0", 0x3C, "raw", "Auxiliary / Suspension"),
    ("ptr_suspension_1_40", "Suspension 1", 0x40, "raw", "Auxiliary / Suspension"),
    ("ptr_suspension_2_44", "Suspension 2", 0x44, "raw", "Auxiliary / Suspension"),

    ("ptr_gearshift_0_48", "Gearshift 0", 0x48, "raw_bundle", "Auxiliary / Gearshift"),
    ("ptr_gearshift_1_4C", "Gearshift 1", 0x4C, "raw_bundle", "Auxiliary / Gearshift"),
    ("ptr_gearshift_2_50", "Gearshift 2", 0x50, "raw_bundle", "Auxiliary / Gearshift"),

    ("ptr_backfire_0_54", "Backfire 0", 0x54, "raw_bundle", "Auxiliary / Backfire"),
    ("ptr_backfire_1_58", "Backfire 1", 0x58, "raw_bundle", "Auxiliary / Backfire"),
    ("ptr_backfire_2_5C", "Backfire 2", 0x5C, "raw_bundle", "Auxiliary / Backfire"),

    ("ptr_aux_audio_0_60", "Aux Audio 0", 0x60, "block", "Auxiliary / Aux Audio"),
    ("ptr_aux_audio_1_64", "Aux Audio 1", 0x64, "block", "Auxiliary / Aux Audio"),
    ("ptr_aux_audio_2_68", "Aux Audio 2", 0x68, "block", "Auxiliary / Aux Audio"),

    ("ptr_slot0_engine_6C", "Slot 0 Engine", 0x6C, "block", "Engine / Root A"),
    ("ptr_slot1_engine_70", "Slot 1 Engine", 0x70, "block", "Engine / Root A"),
    ("ptr_slot2_engine_74", "Slot 2 Engine", 0x74, "block", "Engine / Root A"),
    ("ptr_slot3_engine_78", "Slot 3 Engine", 0x78, "block", "Engine / Root A"),
    ("ptr_slot4_engine_7C", "Slot 4 Engine", 0x7C, "block", "Engine / Root A"),

    ("ptr_slot0_exhaust_80", "Slot 0 Exhaust", 0x80, "block", "Exhaust / Root B"),
    ("ptr_slot1_exhaust_84", "Slot 1 Exhaust", 0x84, "block", "Exhaust / Root B"),
    ("ptr_slot2_exhaust_88", "Slot 2 Exhaust", 0x88, "block", "Exhaust / Root B"),
    ("ptr_slot3_exhaust_8C", "Slot 3 Exhaust", 0x8C, "block", "Exhaust / Root B"),
    ("ptr_slot4_exhaust_90", "Slot 4 Exhaust", 0x90, "block", "Exhaust / Root B"),

    ("ptr_slot0_profile_94", "Slot 0 Profile", 0x94, "profile", "Profiles"),
    ("ptr_slot1_profile_98", "Slot 1 Profile", 0x98, "profile", "Profiles"),
    ("ptr_slot2_profile_9C", "Slot 2 Profile", 0x9C, "profile", "Profiles"),
    ("ptr_slot3_profile_A0", "Slot 3 Profile", 0xA0, "profile", "Profiles"),
    ("ptr_slot4_profile_A4", "Slot 4 Profile", 0xA4, "profile", "Profiles"),
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


def format_hex(value: Optional[int], width: int = 8) -> str:
    if value is None:
        return "-"
    return f"0x{int(value) & ((1 << (width * 4)) - 1):0{width}X}"


def sample_suffix(sample_name: str) -> str:
    return sample_name.split(":", 1)[1] if ":" in sample_name else sample_name


def likely_sample_role(ranges: List["AudioRange"], active_count: int) -> str:
    active = ranges[:max(0, min(active_count, len(ranges)))]
    joined = " ".join(sample_suffix(r.sample_name).upper() for r in active)
    has_engine = "_ENGINE" in joined or joined.endswith("ENGINE")
    has_tail = "_TAIL" in joined or joined.endswith("TAIL")
    has_turbo = "TURBO" in joined or "BLOW" in joined
    if has_engine and not has_tail:
        return "ENGINE samples"
    if has_tail and not has_engine:
        return "TAIL samples"
    if has_engine and has_tail:
        return "MIXED ENGINE/TAIL"
    if has_turbo:
        return "TURBO/AUX samples"
    return "unknown samples"


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


@dataclass
class CurveData:
    key: str
    label: str
    ptr_key: str
    ptr_va: int = 0
    file_off: Optional[int] = None
    magic: int = 0
    unk_04: int = 0
    unk_08: int = 0
    unk_0C: float = 0.0
    unk_10: float = 0.0
    point_count: int = 0
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
            "unk_04": self.unk_04,
            "unk_08": self.unk_08,
            "unk_0C": self.unk_0C,
            "unk_10": self.unk_10,
            "point_count": self.point_count,
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
            "preview_hex": self.preview_hex,
            "warnings": self.warnings,
        }


@dataclass
class ProfileBlock:
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


ParsedItem = AudioBlock | RawAuxBlock | ProfileBlock


@dataclass
class PckAudioDocument:
    path: Path
    buf: bytes
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
    profiles: List[ProfileBlock] = field(default_factory=list)
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
            "slot_engine_sounds": {b.key: b.to_dict() for b in self.blocks if b.category == "Engine / Root A"},
            "slot_exhaust_sounds": {b.key: b.to_dict() for b in self.blocks if b.category == "Exhaust / Root B"},
            "aux_audio_blocks": {b.key: b.to_dict() for b in self.blocks if b.category == "Auxiliary / Aux Audio"},
            "raw_auxiliary": {b.key: b.to_dict() for b in self.raw_aux_blocks},
            "profiles": {p.key: p.to_dict() for p in self.profiles},
            "warnings": self.warnings,
        }


# -----------------------------------------------------------------------------
# PCK Parser
# -----------------------------------------------------------------------------

class PckAudioParser:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.buf = self.path.read_bytes()
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
            elif kind == "profile":
                profile = self.parse_profile_block(doc, key, label, category, root_rel, ptr_name, ptr_va, file_off)
                doc.profiles.append(profile)
            else:
                raw = self.parse_raw_aux(doc, key, label, category, root_rel, ptr_name, ptr_va, file_off, kind)
                doc.raw_aux_blocks.append(raw)

        # Stable, user-facing order.
        cat_order = {
            "Engine / Root A": 0,
            "Exhaust / Root B": 1,
            "Auxiliary / Aux Audio": 2,
        }
        doc.blocks.sort(key=lambda b: (cat_order.get(b.category, 99), b.root_rel))
        doc.raw_aux_blocks.sort(key=lambda b: (b.category, b.root_rel))
        doc.profiles.sort(key=lambda p: p.root_rel)
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
            ranges=ranges,
            warnings=warnings,
        )

        for curve_key, curve_label, ptr_key, ptr_rel in CURVE_FIELDS:
            ptr_curve = safe_u32(self.buf, file_off + ptr_rel)
            block.curves[curve_key] = self.parse_curve(curve_key, curve_label, ptr_key, ptr_curve)

        return block

    def parse_curve(self, curve_key: str, curve_label: str, ptr_key: str, ptr_va: int) -> CurveData:
        curve = CurveData(key=curve_key, label=curve_label, ptr_key=ptr_key, ptr_va=ptr_va)
        curve_off = self.va_to_off_optional(ptr_va, 0x20)
        curve.file_off = curve_off
        if curve_off is None:
            curve.warning = f"Invalid curve pointer {format_hex(ptr_va)}."
            return curve

        curve.magic = safe_u32(self.buf, curve_off)
        curve.unk_04 = safe_u32(self.buf, curve_off + 0x04)
        curve.unk_08 = safe_u32(self.buf, curve_off + 0x08)
        curve.unk_0C = safe_f32(self.buf, curve_off + 0x0C)
        curve.unk_10 = safe_f32(self.buf, curve_off + 0x10)
        curve.point_count = safe_u32(self.buf, curve_off + 0x14)
        curve.ptr_points = safe_u32(self.buf, curve_off + 0x18)
        curve.pad_1C = safe_u32(self.buf, curve_off + 0x1C)

        if curve.magic != CURVE_MAGIC:
            curve.warning = f"Unexpected curve magic {format_hex(curve.magic)}."

        if curve.point_count > 64:
            curve.warning = (curve.warning + " " if curve.warning else "") + f"Suspicious point_count={curve.point_count}."
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
        found_strings = read_loose_ascii_strings(self.buf, file_off + 0x04, preview_size)

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
            preview_hex=preview_hex,
            warnings=warnings,
        )

    def parse_profile_block(
        self,
        doc: PckAudioDocument,
        key: str,
        label: str,
        category: str,
        root_rel: int,
        ptr_name: str,
        ptr_va: int,
        file_off: int,
    ) -> ProfileBlock:
        warnings: List[str] = []
        magic = safe_u32(self.buf, file_off)
        if magic not in PROFILE_MAGICS:
            warnings.append(f"Unexpected profile magic {format_hex(magic)}.")
        params = []
        for i in range(22):
            params.append(safe_f32(self.buf, file_off + 0x04 + i * 4))
        unk_5C = safe_u32(self.buf, file_off + 0x5C)
        return ProfileBlock(
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
        self.root.title("MC3 Audio Curve GUI v0.3")
        self.root.geometry("1480x900")
        self.root.minsize(1120, 700)

        self.doc: Optional[PckAudioDocument] = None
        self.item_map: Dict[str, Any] = {}
        self.selected: Optional[Any] = None
        self.current_path: Optional[Path] = None

        self.curve_visible = {key: tk.BooleanVar(value=True) for key, _, _, _ in CURVE_FIELDS}
        self.curve_scale_mode = tk.StringVar(value="global")  # "global" or "fit"
        self.show_point_labels = tk.BooleanVar(value=True)
        self.show_control_points = tk.BooleanVar(value=False)

        self._setup_style()
        self._build_ui()

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
        ttk.Button(toolbar, text="Export Parsed JSON", command=self.export_parsed_json).pack(side=tk.LEFT, padx=4, pady=4)

        ttk.Label(toolbar, text="Curve visibility:", style="Muted.TLabel").pack(side=tk.LEFT, padx=(20, 4))
        for key, label, _, _ in CURVE_FIELDS:
            cb = ttk.Checkbutton(toolbar, text=label, variable=self.curve_visible[key], command=self.redraw_curve_canvas)
            cb.pack(side=tk.LEFT, padx=3)

        ttk.Label(toolbar, text="Scale:", style="Muted.TLabel").pack(side=tk.LEFT, padx=(16, 4))
        ttk.Radiobutton(toolbar, text="Global 0-1", variable=self.curve_scale_mode, value="global", command=self.redraw_curve_canvas).pack(side=tk.LEFT, padx=2)
        ttk.Radiobutton(toolbar, text="Fit to view", variable=self.curve_scale_mode, value="fit", command=self.redraw_curve_canvas).pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(toolbar, text="Point labels", variable=self.show_point_labels, command=self.redraw_curve_canvas).pack(side=tk.LEFT, padx=(12, 3))
        ttk.Checkbutton(toolbar, text="Control pts", variable=self.show_control_points, command=self.redraw_curve_canvas).pack(side=tk.LEFT, padx=3)

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
        right.add(top_right, weight=4)
        right.add(bottom_right, weight=3)

        self.summary = tk.Text(
            top_right,
            height=8,
            bg=self.PANEL,
            fg=self.FG,
            insertbackground=self.FG,
            relief=tk.FLAT,
            wrap=tk.WORD,
        )
        self.summary.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(8, 4))
        self.summary.configure(state=tk.DISABLED)

        self.curve_canvas = tk.Canvas(top_right, bg=self.PANEL, highlightthickness=0)
        self.curve_canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(4, 8))
        self.curve_canvas.bind("<Configure>", lambda _event: self.redraw_curve_canvas())

        ranges_frame = ttk.Frame(bottom_right)
        curves_frame = ttk.Frame(bottom_right)
        raw_frame = ttk.Frame(bottom_right)
        bottom_right.add(ranges_frame, text="Ranges")
        bottom_right.add(curves_frame, text="Curve Rows")
        bottom_right.add(raw_frame, text="Raw / Warnings")

        self.ranges_tree = ttk.Treeview(
            ranges_frame,
            columns=("idx", "active", "sample", "min", "max", "mid", "off"),
            show="headings",
            height=8,
        )
        for col, text, width, anchor in [
            ("idx", "#", 45, "e"),
            ("active", "Active", 65, "center"),
            ("sample", "Sample", 360, "w"),
            ("min", "Min RPM", 100, "e"),
            ("max", "Max RPM", 100, "e"),
            ("mid", "Mid RPM", 100, "e"),
            ("off", "File Off", 110, "e"),
        ]:
            self.ranges_tree.heading(col, text=text)
            self.ranges_tree.column(col, width=width, anchor=anchor)
        self.ranges_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=8)
        ranges_scroll = ttk.Scrollbar(ranges_frame, orient=tk.VERTICAL, command=self.ranges_tree.yview)
        ranges_scroll.pack(side=tk.RIGHT, fill=tk.Y, pady=8)
        self.ranges_tree.configure(yscrollcommand=ranges_scroll.set)

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
        self.curve_rows_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=8)
        curve_scroll = ttk.Scrollbar(curves_frame, orient=tk.VERTICAL, command=self.curve_rows_tree.yview)
        curve_scroll.pack(side=tk.RIGHT, fill=tk.Y, pady=8)
        self.curve_rows_tree.configure(yscrollcommand=curve_scroll.set)

        self.raw_text = tk.Text(
            raw_frame,
            bg=self.PANEL,
            fg=self.FG,
            insertbackground=self.FG,
            relief=tk.FLAT,
            wrap=tk.NONE,
        )
        self.raw_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=8)
        raw_scroll_y = ttk.Scrollbar(raw_frame, orient=tk.VERTICAL, command=self.raw_text.yview)
        raw_scroll_y.pack(side=tk.RIGHT, fill=tk.Y, pady=8)
        self.raw_text.configure(yscrollcommand=raw_scroll_y.set)

        self.set_summary("Open a vehicle .pck through File > Open PCK.")

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Open PCK...", command=self.open_pck_dialog, accelerator="Ctrl+O")
        file_menu.add_command(label="Reload", command=self.reload_current, accelerator="F5")
        file_menu.add_separator()
        file_menu.add_command(label="Export Parsed JSON...", command=self.export_parsed_json)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=file_menu)

        view_menu = tk.Menu(menubar, tearoff=False)
        for key, label, _, _ in CURVE_FIELDS:
            view_menu.add_checkbutton(label=label, variable=self.curve_visible[key], command=self.redraw_curve_canvas)
        view_menu.add_separator()
        scale_menu = tk.Menu(view_menu, tearoff=False)
        scale_menu.add_radiobutton(label="Global 0-1", variable=self.curve_scale_mode, value="global", command=self.redraw_curve_canvas)
        scale_menu.add_radiobutton(label="Fit to view", variable=self.curve_scale_mode, value="fit", command=self.redraw_curve_canvas)
        view_menu.add_cascade(label="Graph Scale", menu=scale_menu)
        view_menu.add_checkbutton(label="Point labels", variable=self.show_point_labels, command=self.redraw_curve_canvas)
        view_menu.add_checkbutton(label="Control points", variable=self.show_control_points, command=self.redraw_curve_canvas)
        menubar.add_cascade(label="View", menu=view_menu)

        self.root.config(menu=menubar)
        self.root.bind("<Control-o>", lambda _event: self.open_pck_dialog())
        self.root.bind("<F5>", lambda _event: self.reload_current())

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
        self.selected = None
        self.root.title(f"MC3 Audio Curve GUI v0.3 - {path.name}")
        self.rebuild_tree()
        self.show_document_summary()
        self.clear_tables()
        self.redraw_curve_canvas()

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
        if self.doc.profiles:
            categories.setdefault("Profiles", []).extend(self.doc.profiles)

        order = [
            "Engine / Root A",
            "Exhaust / Root B",
            "Auxiliary / Aux Audio",
            "Auxiliary / Backfire",
            "Auxiliary / Gearshift",
            "Auxiliary / Explosion",
            "Auxiliary / Wind",
            "Auxiliary / Tire Skids",
            "Auxiliary / Suspension",
            "Auxiliary / Unknown",
            "Profiles",
        ]
        seen = set()
        for cat in order + sorted(k for k in categories if k not in order):
            if cat not in categories or cat in seen:
                continue
            seen.add(cat)
            cat_id = self.tree.insert(root_id, "end", text=cat, open=cat in ("Engine / Root A", "Exhaust / Root B"))
            self.item_map[cat_id] = cat
            for item in categories[cat]:
                text = getattr(item, "display_label", getattr(item, "label", str(item)))
                child_id = self.tree.insert(cat_id, "end", text=text, open=False)
                self.item_map[child_id] = item

    def on_tree_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        item = self.item_map.get(sel[0])
        self.selected = item
        if isinstance(item, PckAudioDocument):
            self.show_document_summary()
        elif isinstance(item, AudioBlock):
            self.show_audio_block(item)
        elif isinstance(item, RawAuxBlock):
            self.show_raw_aux(item)
        elif isinstance(item, ProfileBlock):
            self.show_profile(item)
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
        for tree in (self.ranges_tree, self.curve_rows_tree):
            tree.delete(*tree.get_children())
        self.set_raw_text("")

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
            f"Parsed AudioBlockFull blocks: {len(doc.blocks)} | Raw aux previews: {len(doc.raw_aux_blocks)} | Profiles: {len(doc.profiles)}",
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
        items.extend([b for b in self.doc.blocks if b.category == category])
        items.extend([b for b in self.doc.raw_aux_blocks if b.category == category])
        if category == "Profiles":
            items.extend(self.doc.profiles)
        lines = [f"Category: {category}", f"Items: {len(items)}", ""]
        for item in items:
            ptr_va = getattr(item, "ptr_va", 0)
            file_off = getattr(item, "file_off", None)
            lines.append(f"{getattr(item, 'label', 'item')} | VA {format_hex(ptr_va)} | file {format_hex(file_off)}")
        self.set_summary("\n".join(lines))
        self.clear_tables()

    def show_audio_block(self, block: AudioBlock):
        lines = [
            f"{block.label} ({block.category})",
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
        ]
        if block.warnings:
            lines.append("")
            lines.append("Warnings:")
            lines.extend(f"- {w}" for w in block.warnings)
        for curve in block.curves.values():
            if curve.warning:
                lines.append(f"- {curve.label}: {curve.warning}")
        self.set_summary("\n".join(lines))
        self.fill_ranges_table(block)
        self.fill_curve_rows_table(block)
        self.fill_raw_for_block(block)

    def show_raw_aux(self, raw: RawAuxBlock):
        lines = [
            f"{raw.label} ({raw.category})",
            f"Root pointer: {raw.ptr_name} @ root+0x{raw.root_rel:02X} = {format_hex(raw.ptr_va)} -> file {format_hex(raw.file_off)}",
            f"Magic: {format_hex(raw.magic)}",
            f"possible_name_0: {raw.possible_name_0 or '<empty>'}",
            f"possible_name_1: {raw.possible_name_1 or '<empty>'}",
            "",
            "Readable strings found in preview:",
        ]
        if raw.found_strings:
            lines.extend(f"- {s}" for s in raw.found_strings[:40])
        else:
            lines.append("- <none>")
        if raw.warnings:
            lines.append("")
            lines.append("Warnings:")
            lines.extend(f"- {w}" for w in raw.warnings)
        self.set_summary("\n".join(lines))
        self.clear_tables()
        self.set_raw_text("Raw preview hex:\n" + raw.preview_hex)

    def show_profile(self, profile: ProfileBlock):
        lines = [
            f"{profile.label} ({profile.category})",
            f"Root pointer: {profile.ptr_name} @ root+0x{profile.root_rel:02X} = {format_hex(profile.ptr_va)} -> file {format_hex(profile.file_off)}",
            f"Magic: {format_hex(profile.magic)} ({PROFILE_MAGICS.get(profile.magic, 'unknown')})",
            f"unk_5C: {format_hex(profile.unk_5C)}",
            "",
            "params[22]:",
        ]
        for i, value in enumerate(profile.params):
            lines.append(f"  [{i:02d}] {value:.9g}")
        if profile.warnings:
            lines.append("")
            lines.append("Warnings:")
            lines.extend(f"- {w}" for w in profile.warnings)
        self.set_summary("\n".join(lines))
        self.clear_tables()

    def fill_ranges_table(self, block: AudioBlock):
        self.ranges_tree.delete(*self.ranges_tree.get_children())
        for r in block.ranges:
            self.ranges_tree.insert(
                "",
                "end",
                values=(
                    r.index,
                    "yes" if r.active else "no",
                    r.sample_name,
                    f"{r.min_rpm:.6g}",
                    f"{r.max_rpm:.6g}",
                    f"{r.mid_rpm:.6g}",
                    f"0x{r.file_off:08X}",
                ),
            )

    def fill_curve_rows_table(self, block: AudioBlock):
        self.curve_rows_tree.delete(*self.curve_rows_tree.get_children())
        for key, label, _, _ in CURVE_FIELDS:
            curve = block.curves.get(key)
            if not curve:
                continue
            for i, row in enumerate(curve.rows):
                values = [label, i]
                values.extend(f"{v:.6g}" for v in row[:CURVE_POINT_FLOATS])
                self.curve_rows_tree.insert("", "end", values=values)

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
    def sample_curve_row(row: List[float], steps: int = 32) -> List[Tuple[float, float]]:
        """Sample one MC3 audio curve span.

        Current validated interpretation:
        row[0:6] = start/control/end points: (x0,y0), (cx,cy), (x1,y1)
        row[6:9] = quadratic coefficients A/B/C, evaluated as y = A*x*x + B*x + C.
        """
        if not AudioCurveGUI.curve_row_valid(row):
            return []
        x0, _y0, _cx, _cy, x1, _y1, a, b, c = row[:9]
        if math.isclose(x0, x1):
            y = a * x0 * x0 + b * x0 + c
            return [(x0, y)]
        lo, hi = (x0, x1) if x0 <= x1 else (x1, x0)
        n = max(2, int(steps))
        pts: List[Tuple[float, float]] = []
        for i in range(n + 1):
            x = lo + (hi - lo) * (i / n)
            y = a * x * x + b * x + c
            if math.isfinite(x) and math.isfinite(y):
                pts.append((x, y))
        if x0 > x1:
            pts.reverse()
        return pts

    @staticmethod
    def sampled_curve_points(curve: CurveData) -> List[Tuple[float, float]]:
        pts: List[Tuple[float, float]] = []
        for row in curve.rows[:max(0, int(curve.point_count))]:
            seg = AudioCurveGUI.sample_curve_row(row)
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

    @staticmethod
    def normalized_rpm_label(block: AudioBlock, x: float) -> str:
        if block.max_rpm_rec > block.min_rpm_rec:
            rpm = block.min_rpm_rec + x * (block.max_rpm_rec - block.min_rpm_rec)
            return f"{rpm:.0f} rpm"
        return ""

    def redraw_curve_canvas(self):
        canvas = getattr(self, "curve_canvas", None)
        if canvas is None:
            return
        canvas.delete("all")
        w = max(10, canvas.winfo_width())
        h = max(10, canvas.winfo_height())
        canvas.create_rectangle(0, 0, w, h, fill=self.PANEL, outline="")

        selected = self.selected
        if not isinstance(selected, AudioBlock):
            self.draw_canvas_message("Select an AudioBlockFull item to view curves.")
            return

        visible_curves: List[CurveData] = []
        sampled_by_key: Dict[str, List[Tuple[float, float]]] = {}
        for key, _label, _ptr_key, _rel in CURVE_FIELDS:
            if self.curve_visible[key].get():
                c = selected.curves.get(key)
                if c:
                    sampled = self.sampled_curve_points(c)
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

        if math.isclose(xmin, xmax):
            xmax = xmin + 1.0
        if math.isclose(ymin, ymax):
            ymax = ymin + 1.0

        def sx(x: float) -> float:
            return margin_l + (x - xmin) / (xmax - xmin) * plot_w

        def sy(y: float) -> float:
            return margin_t + (1.0 - (y - ymin) / (ymax - ymin)) * plot_h

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
            text=f"{scale_label} | row = start/control/end + y=A*x²+B*x+C",
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
            for idx, x, y in knot_points:
                px, py = sx(x), sy(y)
                canvas.create_oval(px - 4, py - 4, px + 4, py + 4, fill=color, outline="")
                if self.show_point_labels.get():
                    canvas.create_text(px + 7, py - 7, text=str(idx), anchor="w", fill=color, font=("Consolas", 8))

            if self.show_control_points.get():
                for idx, x, y in self.curve_control_points(c):
                    px, py = sx(x), sy(y)
                    canvas.create_rectangle(px - 3, py - 3, px + 3, py + 3, outline=color)
                    if self.show_point_labels.get():
                        canvas.create_text(px + 7, py + 7, text=f"c{idx}", anchor="w", fill=color, font=("Consolas", 8))

            canvas.create_rectangle(legend_x, legend_y - 5, legend_x + 12, legend_y + 7, fill=color, outline="")
            canvas.create_text(legend_x + 18, legend_y + 1, anchor="w", text=f"{c.label} ({c.point_count})", fill=self.FG, font=("Segoe UI", 9))
            legend_y += 18

    def draw_canvas_message(self, message: str):
        w = max(10, self.curve_canvas.winfo_width())
        h = max(10, self.curve_canvas.winfo_height())
        self.curve_canvas.create_text(w / 2, h / 2, text=message, fill=self.MUTED, font=("Segoe UI", 11))


def main(argv: List[str]) -> int:
    # Tiny CLI parse mode for quick validation without opening a GUI:
    #   python mc3_audio_curve_gui_v02.py --dump vp_ram_04.pck
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
