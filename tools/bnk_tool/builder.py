"""Builds a Sony .bnk (SBlk v3) entirely from scratch - no template file needed.

The byte layout below was reverse-engineered from two independent real-game .bnk files
(both SBlk v3, no table4/embedded names) by cross-checking every field against vgmstream's
own reader (bnk_sony.c) and vgmstream-cli's decoded output:

  top header (0x18 bytes):
    +0x00 u32 version=3          +0x04 u32 sections=2
    +0x08 u32 sblk_offset(=0x18) +0x0c u32 sblk_size (=data_offset-sblk_offset)
    +0x10 u32 data_offset         +0x14 u32 data_size

  SBlk pre-table header (fixed 0x40 bytes, confirmed identical size in both samples):
    +0x00 "SBlk"                 +0x04 u32 sblk_version=3
    +0x08 u32 flags (=4 in both samples; meaning unconfirmed, kept as observed)
    +0x0c u32 =0 ("block id")    +0x10 u32 =0 ("block number"+padding)
    +0x14 u16 =0                 +0x16 u16 sounds_entries
    +0x18 u16 grains_entries     +0x1a u16 stream_entries
    +0x1c u32 table1_offset_rel  +0x20 u32 table2_offset_rel
    +0x24 u32 =0x5040 (unconfirmed meaning; identical constant in both samples)
    +0x28 u32 data_size (dup.)   +0x2c u32 "RAM size" (=data_size in both samples)
    +0x30 u32 =0 (next block)    +0x34 u32 table3_offset_rel
    +0x38 u32 =0 (table4_offset_rel; 0 means "no table4")
    +0x3c u32 =0

  table1 ("sounds", 0x0c bytes/entry): +0x00 u32 mystery/priority (observed 0x78 most
    often) +0x04 u8 entry_count +0x05..07 reserved (0x00,0x01,0x00 in real-content
    samples) +0x08 u32 entry_offset (byte offset into table2)

  table2 ("grains", 0x08 bytes/entry): +0x00 u16 subinfo (table3-relative offset)
    +0x02 u16 subtype (0x0100 = sound) +0x04 u32 =0

  table3 ("waveforms"/sndh, 0x18 bytes/entry - the "tone" struct):
    +0x00 u8 priority +0x01 u8 volume +0x02 u8 center_note +0x03 u8 center_fine
    +0x04 u8 pan +0x05..09 reserved +0x0a u16 ADSR1 +0x0c u16 ADSR2
    +0x0e u16 stream_flags (0x40=loop, 0x80=PCM16, 0x1000=old MPEG)
    +0x10 u32 stream_offset (relative to data_offset) +0x14 u32 stream_size (total,
    incl. any extradata - written explicitly/nonzero, sidestepping the scan-based
    fallback some original files rely on; confirmed compatible with vgmstream)

Fields whose exact real-world semantics we couldn't confirm (table1's mystery field,
the SBlk "flags"/"VAB address"/"RAM size" values) are set to the values consistently
observed in both reference files. This has only been validated against vgmstream, not
against the actual game engine - treat a from-scratch bank as higher-risk than one
built by patching an existing file (see mux.py), and test it in-game before shipping.
"""
import os
import re
import struct
from dataclasses import dataclass, field
from typing import List, Optional

from .spu_pitch import sample_rate_to_center
from .genh import build_genh, is_genh_supported, GenhError
from .td import build_td

SBLK_HEADER_SIZE = 0x40
TABLE1_ENTRY_SIZE = 0x0c
TABLE2_ENTRY_SIZE = 0x08
TABLE3_ENTRY_SIZE = 0x18

DEFAULT_TABLE1_PRIORITY = 0x78
DEFAULT_SBLK_FLAGS = 0x04
DEFAULT_VAB_ADDRESS = 0x5040


class BuildError(Exception):
    pass


@dataclass
class GrainSpec:
    """One physical waveform (a stream/subsong). codec: "PSX" or "PCM16" (mono only -
    the v3 "tone" table layout has no per-grain channel count field)."""
    codec: str
    sample_rate: int
    payload: bytes                 # raw codec bytes; bake loop points in beforehand (ps_adpcm.set_ps_adpcm_loop)
    loop_flag: bool = False
    big_endian_pcm: bool = False


@dataclass
class SoundSpec:
    """One named "sound" (.td entry / table1 entry), grouping 1+ grains."""
    name: str
    grains: List[GrainSpec] = field(default_factory=list)
    priority: int = DEFAULT_TABLE1_PRIORITY
    td_flag: int = 1


def _pack_u32(v):
    return struct.pack("<I", v & 0xFFFFFFFF)


def _pack_u16(v):
    return struct.pack("<H", v & 0xFFFF)


def build_bnk_v3(sounds: List[SoundSpec]):
    """Returns (bnk_bytes, manifest_dict, td_text). manifest_dict has the same shape
    demux() produces, so mux.py can later patch this file just like a template."""
    if not sounds:
        raise BuildError("no sound given")

    grains = []          # flattened (sound_idx, grain) list, in final table2/table3 order
    for si, snd in enumerate(sounds):
        if not snd.grains:
            raise BuildError(f"sound '{snd.name}' has no grain/audio")
        for g in snd.grains:
            if g.codec not in ("PSX", "PCM16"):
                raise BuildError(f"sound '{snd.name}': codec '{g.codec}' not supported by the v3 builder "
                                  f"(use PSX ou PCM16 - mono)")
            grains.append((si, g))

    sounds_entries = len(sounds)
    grains_entries = len(grains)

    table1_offset_rel = SBLK_HEADER_SIZE
    table2_offset_rel = table1_offset_rel + sounds_entries * TABLE1_ENTRY_SIZE
    table3_offset_rel = table2_offset_rel + grains_entries * TABLE2_ENTRY_SIZE
    sblk_size = table3_offset_rel + grains_entries * TABLE3_ENTRY_SIZE

    sblk_offset = 0x18
    data_offset = sblk_offset + sblk_size

    # --- data section + table3 entries ---
    data = bytearray()
    table3 = bytearray(grains_entries * TABLE3_ENTRY_SIZE)
    manifest_streams = []

    grain_start_offsets = []  # per-grain offset relative to data_offset
    for gi, (si, g) in enumerate(grains):
        grain_start_offsets.append(len(data))
        data += g.payload

    for gi, (si, g) in enumerate(grains):
        stream_offset = grain_start_offsets[gi]
        stream_size = len(g.payload)
        center_note, center_fine, achieved_sr = sample_rate_to_center(g.sample_rate)

        stream_flags = 0
        if g.loop_flag:
            stream_flags |= 0x40
        if g.codec == "PCM16":
            stream_flags |= 0x80

        entry_off = gi * TABLE3_ENTRY_SIZE
        table3[entry_off + 0x00] = 0                       # priority (unused by vgmstream)
        table3[entry_off + 0x01] = 0                       # volume
        table3[entry_off + 0x02] = center_note
        table3[entry_off + 0x03] = center_fine
        # +0x04..+0x09 left at 0 (pan/reserved/pitch bend, unused by vgmstream)
        struct.pack_into("<H", table3, entry_off + 0x0e, stream_flags)
        struct.pack_into("<I", table3, entry_off + 0x10, stream_offset)
        struct.pack_into("<I", table3, entry_off + 0x14, stream_size)

        sndh_abs = sblk_offset + table3_offset_rel + entry_off
        manifest_streams.append({
            "index": gi + 1,
            "name": sounds[si].name,
            "name_source": "td",
            "sound_index": si,
            "td_flag": sounds[si].td_flag,
            "codec": g.codec,
            "channels": 1,
            "sample_rate": achieved_sr,
            "loop_flag": g.loop_flag,
            "loop_start": 0,
            "loop_end": 0,
            "interleave": stream_size,
            "encoder_delay": 0,
            "sndh_offset": sndh_abs,
            "entry_data_offset": data_offset + stream_offset,
            "entry_total_size": stream_size,
            "extradata_size": 0,
            "postdata_size": 0,
            "extradata_hex": "",
            "stream_offset_field": {"offset": sndh_abs + 0x10, "size": 4},
            "stream_size_field": {"offset": sndh_abs + 0x14, "size": 4},
            "stream_size_field_original_value": stream_size,
            "center_note_field": {"offset": sndh_abs + 0x02, "size": 1},
            "center_fine_field": {"offset": sndh_abs + 0x03, "size": 1},
            "sample_rate_float_field": None,
            "stream_flags_field": {"offset": sndh_abs + 0x0e, "size": 2},
            "embedded_header": False,
            "stream_name_size": 0,
            "file": None,
            "container": "n/a",
            "warnings": [],
            "is_zlsd": False,
        })

    # --- table2 (grains) ---
    table2 = bytearray(grains_entries * TABLE2_ENTRY_SIZE)
    for gi in range(grains_entries):
        off = gi * TABLE2_ENTRY_SIZE
        subinfo = gi * TABLE3_ENTRY_SIZE   # 1:1 grain -> table3 slot, same order
        struct.pack_into("<H", table2, off + 0x00, subinfo)
        struct.pack_into("<H", table2, off + 0x02, 0x0100)  # subtype: sound
        # +0x04 reserved = 0

    # --- table1 (sounds) ---
    table1 = bytearray(sounds_entries * TABLE1_ENTRY_SIZE)
    grain_cursor = 0
    for si, snd in enumerate(sounds):
        off = si * TABLE1_ENTRY_SIZE
        struct.pack_into("<I", table1, off + 0x00, snd.priority)
        table1[off + 0x04] = len(snd.grains)
        table1[off + 0x05] = 0x00
        table1[off + 0x06] = 0x01
        table1[off + 0x07] = 0x00
        struct.pack_into("<I", table1, off + 0x08, grain_cursor * TABLE2_ENTRY_SIZE)
        grain_cursor += len(snd.grains)

    # --- SBlk pre-table header ---
    sblk_header = bytearray(SBLK_HEADER_SIZE)
    sblk_header[0x00:0x04] = b"SBlk"
    struct.pack_into("<I", sblk_header, 0x04, 3)
    struct.pack_into("<I", sblk_header, 0x08, DEFAULT_SBLK_FLAGS)
    struct.pack_into("<I", sblk_header, 0x0c, 0)
    struct.pack_into("<I", sblk_header, 0x10, 0)
    struct.pack_into("<H", sblk_header, 0x14, 0)
    struct.pack_into("<H", sblk_header, 0x16, sounds_entries)
    struct.pack_into("<H", sblk_header, 0x18, grains_entries)
    struct.pack_into("<H", sblk_header, 0x1a, grains_entries)  # stream_entries == grains_entries
    struct.pack_into("<I", sblk_header, 0x1c, table1_offset_rel)
    struct.pack_into("<I", sblk_header, 0x20, table2_offset_rel)
    struct.pack_into("<I", sblk_header, 0x24, DEFAULT_VAB_ADDRESS)
    struct.pack_into("<I", sblk_header, 0x28, len(data))
    struct.pack_into("<I", sblk_header, 0x2c, len(data))
    struct.pack_into("<I", sblk_header, 0x30, 0)
    struct.pack_into("<I", sblk_header, 0x34, table3_offset_rel)
    struct.pack_into("<I", sblk_header, 0x38, 0)
    struct.pack_into("<I", sblk_header, 0x3c, 0)

    sblk_block = bytes(sblk_header) + bytes(table1) + bytes(table2) + bytes(table3)
    assert len(sblk_block) == sblk_size, f"{len(sblk_block)} != {sblk_size}"

    # --- top-level header ---
    top = bytearray(0x18)
    struct.pack_into("<I", top, 0x00, 3)              # version
    struct.pack_into("<I", top, 0x04, 2)               # sections (no ZLSD)
    struct.pack_into("<I", top, 0x08, sblk_offset)
    struct.pack_into("<I", top, 0x0c, sblk_size)
    struct.pack_into("<I", top, 0x10, data_offset)
    struct.pack_into("<I", top, 0x14, len(data))

    bnk_bytes = bytes(top) + sblk_block + bytes(data)

    manifest = {
        "source_file": None,
        "source_size": len(bnk_bytes),
        "big_endian": False,
        "version": 3,
        "sections": 2,
        "sblk_offset": sblk_offset,
        "data_offset": data_offset,
        "data_size": len(data),
        "zlsd_offset": 0,
        "zlsd_size": 0,
        "sblk_version": 3,
        "support_tier": "full",
        "bank_name": "",
        "td": {"version": "3.0", "sounds": [{"name": s.name, "index": i, "flag": s.td_flag}
                                             for i, s in enumerate(sounds)]},
        "streams": manifest_streams,
    }

    td_sounds = [{"name": s.name, "index": i, "flag": s.td_flag} for i, s in enumerate(sounds)]
    td_text = build_td(td_sounds)

    return bnk_bytes, manifest, td_text


def grain_from_genh(path: str) -> GrainSpec:
    """Convenience: build a GrainSpec straight from a .genh file (as produced by demux.py
    or hand-crafted), stripping its header. Loop points must already be baked into the
    raw payload for PSX (see ps_adpcm.set_ps_adpcm_loop) - the GENH header's declared
    loop is only used to set the informational stream_flags loop bit."""
    from .genh import is_genh_file, read_genh
    with open(path, "rb") as f:
        data = f.read()
    if not is_genh_file(data):
        raise BuildError(f"{path}: not a .genh")
    gh = read_genh(data)
    if gh["codec"] not in ("PSX", "PCM16"):
        raise BuildError(f"{path}: GENH codec {gh['genh_codec_raw']} not supported by the v3 builder")
    payload = data[gh["start_offset"]:gh["start_offset"] + gh["data_size"]]
    return GrainSpec(codec=gh["codec"], sample_rate=gh["sample_rate"], payload=payload,
                      loop_flag=gh["loop_flag"])


_LEADING_NUM_RE = re.compile(r"^\d+[_\-. ]*(.*)$")


def sound_name_from_filename(filename: str) -> str:
    """Derives a sound name from a file name, stripping a leading numeric prefix like
    the one demux.py itself writes ("001_HIGH_ENGINE.genh" -> "HIGH_ENGINE")."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    m = _LEADING_NUM_RE.match(stem)
    name = m.group(1).strip("_- ") if m else stem
    return name or stem


def group_genh_files(paths: List[str]):
    """Groups a list of .genh paths by their derived sound name (preserving first-seen
    order), so e.g. "001_HIGH_ENGINE.genh" + "002_HIGH_ENGINE.genh" become one sound
    with 2 grains - matching how the original bnk groups pitch-layered variants."""
    order = []
    groups = {}
    for p in sorted(paths, key=lambda p: os.path.basename(p).lower()):
        name = sound_name_from_filename(p)
        if name not in groups:
            groups[name] = []
            order.append(name)
        groups[name].append(p)
    return [(name, groups[name]) for name in order]


def sounds_from_genh_folder(folder: str) -> List[SoundSpec]:
    """Batch mode for BUILD: turns every .genh file in a folder into sounds, one per
    group of same-named files (see group_genh_files), naming each sound after the file
    name so the generated .td matches automatically - no need to type names by hand."""
    paths = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".genh")]
    if not paths:
        raise BuildError(f"no .genh file found in: {folder}")

    sounds = []
    for name, group_paths in group_genh_files(paths):
        grains = [grain_from_genh(p) for p in group_paths]
        sounds.append(SoundSpec(name=name, grains=grains))
    return sounds
