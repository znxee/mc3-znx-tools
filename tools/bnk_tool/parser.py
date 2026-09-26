"""
Parser for Sony .bnk (989SND/SCREAM) files, translated from vgmstream's bnk_sony.c.

Unlike vgmstream (which parses one target subsong at a time, as requested by the
player), this module walks the tables once and extracts *every* stream's metadata
plus enough field locations to patch the file safely (for MUX), without needing to
decode/re-encode any audio.
"""
import struct
from typing import List, Tuple, Optional

from .binary_reader import BinaryReader, guess_endian32_bnk, get_id32le
from .model import BnkFile, StreamEntry, FieldRef, FULL_SUPPORT_VERSIONS, DEMUX_ONLY_VERSIONS, \
    EXPERIMENTAL_MUX_VERSIONS
from .spu_pitch import center_to_sample_rate
from .ps_adpcm import ps_find_loop_offsets, ps_bytes_to_samples

STREAM_NAME_SIZE = 0x100

GROUP_TONE = (0x01, 0x02, 0x03, 0x04, 0x05, 0x08, 0x09)      # "tone" header layout
GROUP_TABLE3_08_10 = (0x08, 0x09, 0x0c, 0x0d, 0x0e, 0x0f, 0x10)
GROUP_MODERN = (0x1a, 0x1c, 0x23)


class BnkParseError(Exception):
    pass


def _support_tier(sblk_version: int) -> str:
    if sblk_version in FULL_SUPPORT_VERSIONS:
        return "full"
    if sblk_version in DEMUX_ONLY_VERSIONS:
        return "demux_only"
    if sblk_version in EXPERIMENTAL_MUX_VERSIONS:
        return "experimental"
    return "unsupported"


def parse_bnk(path: str) -> BnkFile:
    with open(path, "rb") as f:
        data = f.read()
    return parse_bnk_bytes(data, path)


def parse_bnk_bytes(data: bytes, path: str = "") -> BnkFile:
    big_endian = guess_endian32_bnk(data)
    br = BinaryReader(data, big_endian)

    h = BnkFile(path=path, file_size=len(data), big_endian=big_endian)

    version = br.u32(0x00)
    if version not in (1, 3):
        raise BnkParseError(f"Unknown header (version={version}), does not look like a Sony/SCREAM .bnk")
    h.version = version

    sections = br.u32(0x04)
    if sections < 2 or sections > 3:
        raise BnkParseError(f"invalid number of sections ({sections})")
    h.sections = sections

    h.sblk_offset = br.u32(0x08)
    h.data_offset = br.u32(0x10)
    h.data_size = br.u32(0x14)
    if sections >= 3:
        h.zlsd_offset = br.u32(0x18)
        h.zlsd_size = br.u32(0x1c)

    if h.sblk_offset > 0x20:
        raise BnkParseError(f"invalid sblk_offset (0x{h.sblk_offset:x})")

    if version == 1:
        _parse_bnk_v1(br, h)
    else:
        _parse_bnk_v3(br, h)

    h.support_tier = _support_tier(h.sblk_version)
    return h


# ------------------------------------------------------------------------
# version==1 top level (SBv2 header)
# ------------------------------------------------------------------------
def _parse_bnk_v1(br: BinaryReader, h: BnkFile):
    if h.big_endian:
        raise BnkParseError("big-endian SBv2 (.bnk v1) is not supported")

    if not br.is_id32be(h.sblk_offset + 0x00, "SBv2"):
        raise BnkParseError("expected the 'SBv2' tag at sblk_offset")

    sblk_version = br.u32(h.sblk_offset + 0x04)  # always read as LE here (matches C's read_u32le)
    if sblk_version == 0x02000000:
        sblk_version = 0x02
    elif sblk_version != 0x02:
        raise BnkParseError(f"unexpected SBv2 version (0x{sblk_version:x})")
    h.sblk_version = sblk_version

    _process_tables(br, h)
    entries = _collect_table3_entries(br, h)
    _process_headers_and_data(br, h, entries)


# ------------------------------------------------------------------------
# version==3 top level (SBlk header)
# ------------------------------------------------------------------------
def _parse_bnk_v3(br: BinaryReader, h: BnkFile):
    read_u32 = br.u32
    if read_u32(h.sblk_offset + 0x00) != get_id32le("SBlk"):
        raise BnkParseError("expected the 'SBlk' tag at sblk_offset")

    h.sblk_version = read_u32(h.sblk_offset + 0x04)

    _process_tables(br, h)
    entries = _collect_table3_entries(br, h)
    _process_headers_and_data(br, h, entries)
    _process_names(br, h)
    _process_zlsd(br, h)


# ------------------------------------------------------------------------
# table offsets (process_tables in bnk_sony.c)
# ------------------------------------------------------------------------
def _process_tables(br: BinaryReader, h: BnkFile):
    read_u16 = br.u16
    read_u32 = br.u32
    v = h.sblk_version
    so = h.sblk_offset

    if v == 0x01:
        h.sounds_entries = read_u16(so + 0x16)
        h.grains_entries = read_u16(so + 0x18)
        h.stream_entries = read_u16(so + 0x1a)
        h.table1_offset = so + read_u32(so + 0x1c)
        h.table2_offset = so + read_u32(so + 0x20)
        h.table3_offset = h.table2_offset
        h.table4_offset = 0
        h.table1_entry_size = 0
        h.table1_suboffset = 0
        h.table2_suboffset = 0

    elif v == 0x02:
        h.sounds_entries = read_u16(so + 0x14)
        h.grains_entries = read_u16(so + 0x16)
        h.stream_entries = read_u16(so + 0x1a)
        h.table1_offset = so + read_u32(so + 0x1c)
        h.table2_offset = so + read_u32(so + 0x20)
        h.table3_offset = so + read_u32(so + 0x24)
        h.table4_offset = 0
        h.table1_entry_size = 0
        h.table1_suboffset = 0
        h.table2_suboffset = 0

    elif v in (0x03, 0x04, 0x05, 0x08, 0x09):
        h.sounds_entries = read_u16(so + 0x16)
        h.grains_entries = read_u16(so + 0x18)
        h.stream_entries = read_u16(so + 0x1a)
        h.table1_offset = so + read_u32(so + 0x1c)
        h.table2_offset = so + read_u32(so + 0x20)
        h.table3_offset = so + read_u32(so + 0x34)
        h.table4_offset = so + read_u32(so + 0x38)
        h.table1_entry_size = 0x0c
        h.table1_suboffset = 0x08
        h.table2_suboffset = 0x00

    elif v in (0x0c, 0x0d, 0x0e, 0x0f, 0x10):
        h.table1_offset = so + read_u32(so + 0x18)
        h.table2_offset = so + read_u32(so + 0x1c)
        h.table3_offset = so + read_u32(so + 0x2c)
        h.table4_offset = so + read_u32(so + 0x30)
        h.sounds_entries = read_u16(so + 0x38)
        h.grains_entries = read_u16(so + 0x3a)
        h.stream_entries = read_u16(so + 0x3c)
        h.table1_entry_size = 0x24
        h.table1_suboffset = 0x0c
        h.table2_suboffset = 0x00

    elif v in (0x1a, 0x1c, 0x23):
        bank_name_offset = so + (0x1c if v <= 0x1c else 0x20)
        tables_offset = so + (0x120 if v <= 0x1c else 0x128)
        counts_offset = tables_offset + (0x98 if v <= 0x1c else 0xb0)
        h.table3_offset = so + read_u32(tables_offset + 0x08)
        h.stream_entries = read_u16(counts_offset + 0x06)
        h.bank_name = br.read_cstring(bank_name_offset, 0x100)
        h.sounds_entries = 0
        h.grains_entries = 0
        h.table1_offset = 0
        h.table2_offset = 0
        h.table4_offset = 0
        h.table1_entry_size = 0
        h.table1_suboffset = 0
        h.table2_suboffset = 0

    else:
        raise BnkParseError(f"unknown SBlk version: 0x{v:x}")


# ------------------------------------------------------------------------
# gather (index, table3_entry_offset, table2_entry_offset) for *every* stream
# (equivalent to running process_headers's target-selection loop for all i)
# ------------------------------------------------------------------------
def _collect_table3_entries(br: BinaryReader, h: BnkFile) -> List[Tuple[int, int, Optional[int]]]:
    read_u8 = br.u8
    read_u32 = br.u32
    v = h.sblk_version
    out = []

    if v == 0x01:
        idx = 0
        for i in range(h.grains_entries):
            t = read_u32(h.table2_offset + i * 0x28 + 0x00)
            if t != 0x01:
                continue
            idx += 1
            out.append((idx, i * 0x28 + 0x08, None))

    elif v == 0x02:
        idx = 0
        for i in range(h.grains_entries):
            subsongs = read_u8(h.table2_offset + i * 0x08 + 0x00)
            suboffset = read_u32(h.table2_offset + i * 0x08 + 0x04)
            for j in range(subsongs):
                idx += 1
                entry = suboffset - (h.table3_offset - h.sblk_offset) + j * 0x18
                out.append((idx, entry, None))

    elif v in GROUP_TONE or v in (0x0c, 0x0d, 0x0e, 0x0f, 0x10):
        idx = 0
        for i in range(h.grains_entries):
            value = read_u32(h.table2_offset + i * 0x08 + h.table2_suboffset + 0x00)
            subinfo = value & 0xFFFF
            subtype = (value >> 16) & 0xFFFF
            if subtype != 0x0100:
                continue
            idx += 1
            out.append((idx, subinfo, i * 0x08))

    elif v in GROUP_MODERN:
        for i in range(h.stream_entries):
            out.append((i + 1, i * 0x08, None))

    else:
        raise BnkParseError(f"unknown SBlk version: 0x{v:x}")

    return out


# ------------------------------------------------------------------------
# per-stream header + data parsing (process_headers "parse sounds" switch +
# process_data), run once per collected entry instead of once per target
# ------------------------------------------------------------------------
def _find_sound_index(br: BinaryReader, h: BnkFile, table2_entry_offset):
    """Which table1 ('sound') entry groups this stream's grain, if determinable.
    Mirrors the table1 search _process_names() does per version, but runs regardless
    of whether table4 (in-file names) is even present - needed to match streams
    against an external .td sidecar file instead."""
    if table2_entry_offset is None or h.table1_offset == 0 or h.sounds_entries == 0:
        return None
    v = h.sblk_version
    read_u32 = br.u32
    read_u8 = br.u8

    if v in (0x03, 0x04, 0x05):
        for i in range(h.sounds_entries):
            entry_offset = read_u32(h.table1_offset + i * h.table1_entry_size + 0x08)
            entry_count = read_u8(h.table1_offset + i * h.table1_entry_size + 0x04)
            if entry_offset <= table2_entry_offset < entry_offset + entry_count * 0x08:
                return i
        return None

    if v in (0x08, 0x09, 0x0c, 0x0d, 0x0e, 0x0f, 0x10):
        found = None
        for i in range(h.sounds_entries):
            entry_offset = read_u32(h.table1_offset + i * h.table1_entry_size + h.table1_suboffset + 0x00)
            if entry_offset <= table2_entry_offset:
                found = i
        return found

    return None


def _process_headers_and_data(br: BinaryReader, h: BnkFile, entries):
    read_u16 = br.u16
    read_u32 = br.u32
    read_f32 = br.f32
    read_u64 = br.u64

    h.total_subsongs = len(entries)

    for index, table3_entry_offset, table2_entry_offset in entries:
        s = StreamEntry(index=index)
        s.table2_entry_offset = table2_entry_offset
        s.sound_index = _find_sound_index(br, h, table2_entry_offset)
        sndh_offset = h.table3_offset + table3_entry_offset
        s.sndh_offset = sndh_offset
        v = h.sblk_version

        # ---- header ("parse sounds") ----
        if v in GROUP_TONE:
            center_note = br.u8(sndh_offset + 0x02)
            center_fine = br.u8(sndh_offset + 0x03)
            stream_flags = read_u16(sndh_offset + 0x0e)
            stream_offset = read_u32(sndh_offset + 0x10)
            stream_size = read_u32(sndh_offset + 0x14) if v >= 0x03 else 0
            s.stream_offset_field = FieldRef(sndh_offset + 0x10, 4)
            s.stream_size_field = FieldRef(sndh_offset + 0x14, 4) if v >= 0x03 else None
            s._stream_flags = stream_flags
            s._stream_offset = stream_offset
            s._stream_size_table = stream_size
            s.size_field_raw_original = stream_size
            s.center_note_field = FieldRef(sndh_offset + 0x02, 1)
            s.center_fine_field = FieldRef(sndh_offset + 0x03, 1)
            # note=60 ("middle C"), fine=0: bnk_sony.c always calls spu2_note_to_pitch with
            # these fixed values, only center_note/center_fine vary per stream
            s.sample_rate = center_to_sample_rate(center_note, center_fine)

        elif v in (0x0c, 0x0d, 0x0e, 0x0f, 0x10):
            stream_flags = read_u16(sndh_offset + 0x12)
            stream_offset = read_u32(sndh_offset + 0x44)
            stream_size = read_u32(sndh_offset + 0x48)
            s.stream_offset_field = FieldRef(sndh_offset + 0x44, 4)
            s.stream_size_field = FieldRef(sndh_offset + 0x48, 4)
            s._stream_flags = stream_flags
            s._stream_offset = stream_offset
            s._stream_size_table = stream_size
            s.size_field_raw_original = stream_size
            s.sample_rate_float_field = FieldRef(sndh_offset + 0x4c, 4)
            s.sample_rate = int(read_f32(sndh_offset + 0x4c))

        elif v in GROUP_MODERN:
            stream_offset = read_u32(sndh_offset + 0x00)
            s.stream_offset_field = FieldRef(sndh_offset + 0x00, 4)
            s.stream_size_field = None  # size is embedded in the data blob itself
            s._stream_flags = 0
            s._stream_offset = stream_offset
            s._stream_size_table = 0
            s.sample_rate = 48000
            s.embedded_header = True

        else:
            raise BnkParseError(f"unknown SBlk version: 0x{v:x}")

        _process_stream_data(br, h, s)
        h.streams.append(s)


def _process_extradata_base(br, s, info_offset):
    read_u32 = br.u32
    s.subtype = read_u32(info_offset + 0x00)
    s.extradata_size = read_u32(info_offset + 0x08) + 0x10


def _process_extradata_0x10_pcm_psx(br, s, info_offset):
    read_u32 = br.u32
    read_s32 = br.s32
    s.num_samples = read_s32(info_offset + 0x10)
    s.channels = read_u32(info_offset + 0x14)
    s.loop_start = read_s32(info_offset + 0x18)
    s._loop_length = read_s32(info_offset + 0x1c)


def _process_extradata_0x14_atrac9(br, s, info_offset):
    read_u32 = br.u32
    read_s32 = br.s32
    if read_u32(info_offset + 0x08) != 0x14:
        s.warnings.append("unexpected ATRAC9 extradata size")
        return
    s.atrac9_info = br.u32(info_offset + 0x0c) if not br.big_endian else br.u32(info_offset + 0x0c)
    s._loop_length = read_s32(info_offset + 0x14)
    s.loop_start = read_s32(info_offset + 0x18)


def _process_extradata_0x80_atrac9(br, s, info_offset):
    read_u32 = br.u32
    read_s32 = br.s32
    if read_u32(info_offset + 0x10) != 0x80:
        s.warnings.append("unexpected ATRAC9 extradata size")
        return
    s.atrac9_info = br.u32(info_offset + 0x14)
    s.channels = read_u32(info_offset + 0x1c)
    s._loop_length = read_s32(info_offset + 0x24)
    s.loop_start = read_s32(info_offset + 0x28)


def _process_extradata_0x80_mpeg(br, s, info_offset):
    read_s32 = br.s32
    s.encoder_delay = read_s32(info_offset + 0x20)
    s.num_samples = read_s32(info_offset + 0x24)


def _process_stream_data(br: BinaryReader, h: BnkFile, s: StreamEntry):
    read_u32 = br.u32
    read_u64 = br.u64
    v = h.sblk_version
    s.start_offset = h.data_offset + s._stream_offset

    if v in (0x01, 0x02, 0x03, 0x04, 0x05):
        s.channels = 1

        if v <= 0x03 and s._stream_size_table == 0 and (s._stream_flags & 0x80) == 0:
            # early versions don't store PS-ADPCM size; scan for the silence/end frame
            max_offset = h.file_size
            size = 0x10
            offset = h.data_offset + s._stream_offset + 0x10
            while offset < max_offset:
                lo = read_u64(offset + 0x00) if offset + 8 <= max_offset else 1
                hi = read_u64(offset + 0x08) if offset + 16 <= max_offset else 1
                if lo == 0 and hi == 0:
                    break
                size += 0x10
                b0 = read_u32(offset + 0x00) if offset + 4 <= max_offset else None
                b1 = read_u32(offset + 0x04) if offset + 8 <= max_offset else None
                if b0 is not None and b1 is not None and (
                        (b0 == 0x00077777 and b1 == 0x77777777) or (b0 == 0x00070000 and b1 == 0x00000000)):
                    break
                offset += 0x10
            s._stream_size_table = size
            s.warnings.append(
                "size found by scanning (the size field in the table is 0 in this bank); "
                "when replacing this stream, the new audio has to end with a silence/end frame "
                "that is valid so the size keeps being detected correctly")

        s.stream_size = s._stream_size_table
        s.interleave = s.stream_size // max(s.channels, 1)

        if s._stream_flags & 0x80:
            s.codec = "PCM16"
        elif s._stream_flags & 0x1000:
            _process_extradata_0x80_mpeg(br, s, s.start_offset + 0x00)
            s.extradata_size = 0x80
            s.codec = "MPEG"
        else:
            found, loop_start, loop_end = ps_find_loop_offsets(
                br.data, s.start_offset, s.stream_size, s.channels, s.interleave)
            if found:
                s.loop_start = loop_start
                s.loop_end = loop_end
            s.codec = "PSX"

    elif v in (0x08, 0x09):
        s.stream_size = s._stream_size_table
        subtype = read_u32(s.start_offset + 0x00)
        extradata_size = read_u32(s.start_offset + 0x04) + 0x08
        s.subtype = subtype
        s.extradata_size = extradata_size

        if subtype == 0x00000000:
            s.channels = 1
            s.codec = "PSX"
        elif subtype in (0x00000001, 0x00000004):
            s.channels = 1 if subtype == 0x01 else 2
            s.codec = "PCM16"
        elif subtype in (0x00000002, 0x00000003, 0x00000005):
            s.channels = 1 if subtype == 0x02 else 2
            if h.big_endian:
                _process_extradata_0x80_mpeg(br, s, s.start_offset + 0x08)
                s.codec = "MPEG"
            else:
                _process_extradata_0x14_atrac9(br, s, s.start_offset)
                s.codec = "ATRAC9"
        else:
            raise BnkParseError(f"unknown BNK subtype: 0x{subtype:08x}")

    elif v == 0x0c:
        s.stream_size = s._stream_size_table
        _process_extradata_base(br, s, s.start_offset)
        if h.big_endian:
            if s.subtype == 0x00000000:
                _process_extradata_0x10_pcm_psx(br, s, s.start_offset)
                s.codec = "PSX"
            elif s.subtype == 0x00000001:
                _process_extradata_0x10_pcm_psx(br, s, s.start_offset)
                s.codec = "PCM16"
            elif s.subtype == 0x00000003:
                _process_extradata_0x80_mpeg(br, s, s.start_offset + 0x10)
                s.channels = 2
                s.codec = "MPEG"
            else:
                raise BnkParseError(f"unknown v0c (BE) subtype: 0x{s.subtype:08x}")
        else:
            if s.subtype in (0x00000000, 0x00000001):
                _process_extradata_0x10_pcm_psx(br, s, s.start_offset)
                s.codec = "PCM16"
            elif s.subtype == 0x00010000:
                _process_extradata_0x10_pcm_psx(br, s, s.start_offset)
                s.codec = "PSX"
            else:
                raise BnkParseError(f"unknown v0c (LE) subtype: 0x{s.subtype:08x}")

    elif v in (0x0d, 0x0e, 0x0f, 0x10):
        s.stream_size = s._stream_size_table
        _process_extradata_base(br, s, s.start_offset)
        if s.subtype in (0x00000001, 0x00000004):
            _process_extradata_0x10_pcm_psx(br, s, s.start_offset)
            s.codec = "PCM16"
        elif s.subtype in (0x00000000, 0x00000003):
            _process_extradata_0x10_pcm_psx(br, s, s.start_offset)
            s.codec = "HEVAG"
        elif s.subtype in (0x00000002, 0x00000005):
            _process_extradata_0x80_atrac9(br, s, s.start_offset)
            s.codec = "ATRAC9"
        elif s.subtype in (0x00030000, 0x00030001, 0x00030002):
            _process_extradata_0x80_atrac9(br, s, s.start_offset)
            s.codec = "RIFF_ATRAC9"
        else:
            raise BnkParseError(f"unknown v0d+ subtype: 0x{s.subtype:08x}")

    elif v in (0x1a, 0x1c, 0x23):
        if s._stream_offset == 0xFFFFFFFF:
            s.channels = 1
            s.codec = "DUMMY"
            s.stream_size = 0
            return

        info_offset = s.start_offset
        stream_name_size = read_u64(info_offset + 0x00)
        stream_name_offset = info_offset + 0x08
        info_offset += stream_name_size + 0x08

        eff_name_size = stream_name_size
        if stream_name_size >= 0x100 or stream_name_size <= 0:
            eff_name_size = 0x100
        s.name = br.read_cstring(stream_name_offset, eff_name_size)
        s.stream_name_size = stream_name_size

        stream_total_size = read_u64(info_offset + 0x00)
        stream_total_size += 0x08 + stream_name_size + 0x08
        s.stream_size = stream_total_size
        info_offset += 0x08

        if info_offset + 0x08 > h.data_offset + h.data_size or read_u32(info_offset + 0x04) != 0x01:
            s.channels = 1
            s.codec = "EXTERNAL"
        else:
            _process_extradata_base(br, s, info_offset)
            s.extradata_size += 0x08 + stream_name_size + 0x08
            subtype_hi = s.subtype >> 16
            if subtype_hi == 0x0000:
                _process_extradata_0x10_pcm_psx(br, s, info_offset)
                s.codec = "PCM16"
            elif subtype_hi in (0x0001, 0x0003):
                _process_extradata_0x80_atrac9(br, s, info_offset)
                s.codec = "RIFF_ATRAC9"
            else:
                raise BnkParseError(f"unknown v1a+ subtype: 0x{s.subtype:08x}")

    else:
        raise BnkParseError(f"unknown SBlk version: 0x{v:x}")

    s.start_offset += s.extradata_size
    s.stream_size -= s.extradata_size
    s.stream_size -= s.postdata_size

    if s.loop_start < 0:
        s.loop_start = 0
        s._loop_length = 0
    if getattr(s, "_loop_length", 0):
        s.loop_end = s.loop_start + s._loop_length
    s.loop_flag = (s.loop_start >= 0) and (s.loop_end > 0)

    # mirrors init_vgmstream_bnk_sony's switch(h.codec): interleave defaults + num_samples
    # derived from byte size for the codecs that don't store it explicitly
    if s.codec == "PCM16":
        if s.interleave == 0:
            s.interleave = 0x02
        s.num_samples = s.stream_size // max(s.channels, 1) // 2
    elif s.codec in ("PSX", "HEVAG"):
        if s.interleave == 0:
            s.interleave = 0x10
        s.num_samples = ps_bytes_to_samples(s.stream_size, s.channels)


# ------------------------------------------------------------------------
# names (process_names) - best-effort, only used for friendlier filenames
# ------------------------------------------------------------------------
def _process_names(br: BinaryReader, h: BnkFile):
    read_u16 = br.u16
    read_u32 = br.u32
    read_u8 = br.u8
    v = h.sblk_version

    if h.table4_offset <= h.sblk_offset:
        return

    try:
        if v == 0x03:
            h.bank_name = br.read_cstring(h.table4_offset, STREAM_NAME_SIZE)
            table4_entries_offset = h.table4_offset + 0x18
            table4_names_offset = h.table4_offset + read_u32(h.table4_offset + 0x08)

            for s in h.streams:
                table4_entry_id = None
                for i in range(h.sounds_entries):
                    entry_offset = read_u32(h.table1_offset + i * h.table1_entry_size + 0x08)
                    entry_count = read_u8(h.table1_offset + i * h.table1_entry_size + 0x04)
                    if entry_offset <= s.table2_entry_offset < entry_offset + entry_count * 0x08:
                        table4_entry_id = i
                        break
                if table4_entry_id is None:
                    continue

                found = None
                for i in range(32):
                    entry_idx = read_u16(table4_entries_offset + i * 2)
                    name_off = table4_names_offset + entry_idx * 0x14
                    while read_u8(name_off + 0x00):
                        chk = (read_u8(name_off + 0x00) + read_u8(name_off + 0x04)
                               + read_u8(name_off + 0x08) + read_u8(name_off + 0x0C)) & 0x1F
                        if chk != i:
                            found = "__bad_chain__"
                            break
                        if read_u16(name_off + 0x10) == table4_entry_id:
                            found = br.read_cstring(name_off, STREAM_NAME_SIZE)
                            break
                        name_off += 0x14
                    if found:
                        break
                if found and found != "__bad_chain__":
                    s.name = found
                s.bank_name = h.bank_name

        elif v in (0x04, 0x05):
            h.bank_name = br.read_cstring(h.table4_offset, STREAM_NAME_SIZE)
            table4_entries_offset = h.table4_offset + read_u32(h.table4_offset + 0x08)
            table4_names_offset = h.table4_offset + read_u32(h.table4_offset + 0x0C)

            for s in h.streams:
                table4_entry_id = None
                for i in range(h.sounds_entries):
                    entry_offset = read_u32(h.table1_offset + i * h.table1_entry_size + 0x08)
                    entry_count = read_u8(h.table1_offset + i * h.table1_entry_size + 0x04)
                    if entry_offset <= s.table2_entry_offset < entry_offset + entry_count * 0x08:
                        table4_entry_id = i
                        break
                if table4_entry_id is None:
                    continue
                for i in range(h.sounds_entries):
                    if read_u16(table4_entries_offset + i * 0x10 + 0x0C) == table4_entry_id:
                        name_off = table4_names_offset + read_u32(table4_entries_offset + i * 0x10)
                        s.name = br.read_cstring(name_off, STREAM_NAME_SIZE)
                        break
                s.bank_name = h.bank_name

        elif v in (0x08, 0x09, 0x0c, 0x0d, 0x0e, 0x0f, 0x10):
            h.bank_name = br.read_cstring(h.table4_offset, STREAM_NAME_SIZE)
            table4_entries_offset = h.table4_offset + read_u32(h.table4_offset + 0x08)
            table4_names_offset = table4_entries_offset + 0x10 * h.sounds_entries

            for s in h.streams:
                table4_entry_id = -1
                for i in range(h.sounds_entries):
                    entry_offset = read_u32(h.table1_offset + i * h.table1_entry_size + h.table1_suboffset + 0x00)
                    if entry_offset <= s.table2_entry_offset:
                        table4_entry_id = i
                for i in range(h.sounds_entries):
                    entry_id = read_u16(table4_entries_offset + i * 0x10 + 0x0c)
                    if entry_id == table4_entry_id:
                        name_off = table4_names_offset + read_u32(table4_entries_offset + i * 0x10 + 0x00)
                        s.name = br.read_cstring(name_off, STREAM_NAME_SIZE)
                        break
                s.bank_name = h.bank_name
    except (IndexError, struct.error):
        for s in h.streams:
            s.warnings.append("failed to read names (table4); names may be missing")


# ------------------------------------------------------------------------
# ZLSD (external stream prefetch) - appended as extra XVAG_ATRAC9 streams
# ------------------------------------------------------------------------
def _process_zlsd(br: BinaryReader, h: BnkFile):
    if not h.zlsd_offset:
        return
    read_u32 = br.u32

    if read_u32(h.zlsd_offset + 0x00) != get_id32le("ZLSD"):
        h.streams and h.streams[-1].warnings.append("invalid ZLSD block, ignored")
        return

    zlsd_subsongs = read_u32(h.zlsd_offset + 0x08)
    zlsd_table_offset = read_u32(h.zlsd_offset + 0x0C)
    if zlsd_subsongs < 1 or not zlsd_table_offset:
        return

    base_index = len(h.streams)
    for i in range(zlsd_subsongs):
        entry_offset = h.zlsd_offset + zlsd_table_offset + i * 0x18
        s = StreamEntry(index=base_index + i + 1)
        s.start_offset = entry_offset + 0x04 + read_u32(entry_offset + 0x04)
        s.stream_size = read_u32(entry_offset + 0x0C)
        stream_name_hash = read_u32(entry_offset + 0x00)
        s.name = f"{stream_name_hash} [pre]"
        s.channels = 1
        s.codec = "XVAG_ATRAC9"
        s.sndh_offset = entry_offset
        s.is_zlsd = True
        # size/offset are absolute + self-describing (XVAG); no extra field patch needed
        # beyond the entry_offset+0x04 relative-offset field itself:
        s.stream_offset_field = FieldRef(entry_offset + 0x04, 4)
        s.stream_size_field = FieldRef(entry_offset + 0x0C, 4)
        if not br.is_id32be(s.start_offset, "XVAG"):
            s.warnings.append("ZLSD stream does not start with 'XVAG', it may be wrong")
        h.streams.append(s)
    h.total_subsongs = len(h.streams)
