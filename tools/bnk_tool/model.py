"""Data structures describing a parsed .bnk (Sony 989SND/SCREAM) file."""
from dataclasses import dataclass, field
from typing import Optional, List

CODECS = ("NONE", "DUMMY", "EXTERNAL", "PSX", "PCM16", "MPEG", "ATRAC9", "HEVAG",
          "RIFF_ATRAC9", "XVAG_ATRAC9")

# codec -> file extension used when extracting raw streams
CODEC_EXT = {
    "PSX": ".vag",
    "HEVAG": ".hvag",
    "PCM16": ".pcm",
    "MPEG": ".mp3",
    "ATRAC9": ".at9raw",
    "RIFF_ATRAC9": ".at9",
    "XVAG_ATRAC9": ".xvag",
    "EXTERNAL": ".ext",
    "DUMMY": ".dummy",
    "NONE": ".bin",
}

# support tiers, purely informational (shown to the user)
FULL_SUPPORT_VERSIONS = {0x03, 0x04, 0x05, 0x08, 0x09, 0x0c, 0x0d, 0x0e, 0x0f, 0x10}
DEMUX_ONLY_VERSIONS = {0x01, 0x02}
EXPERIMENTAL_MUX_VERSIONS = {0x1a, 0x1c, 0x23}


@dataclass
class FieldRef:
    """Location of a patchable field inside the file, absolute byte offset."""
    offset: int
    size: int  # in bytes (2, 4 or 8)


@dataclass
class StreamEntry:
    index: int                     # 1-based subsong number
    name: str = ""
    bank_name: str = ""

    codec: str = "NONE"
    channels: int = 1
    sample_rate: int = 0
    loop_flag: bool = False
    loop_start: int = 0
    loop_end: int = 0
    num_samples: int = 0
    interleave: int = 0
    encoder_delay: int = 0

    sndh_offset: int = 0            # table3 per-stream header location
    start_offset: int = 0           # absolute offset of codec payload (post extradata)
    stream_size: int = 0            # payload size only (post extradata, pre postdata)
    extradata_offset: int = 0
    extradata_size: int = 0
    postdata_size: int = 0

    # table3 fields that must be patched on rebuild (relative-offset field + size field)
    stream_offset_field: Optional[FieldRef] = None   # value = new_abs_offset - data_offset
    stream_size_field: Optional[FieldRef] = None      # value = extradata+payload+postdata (total)

    # pitch/sample_rate storage varies by version: GROUP_TONE (v1-9) encodes it as a
    # center_note+center_fine byte pair; v0c-v10 store it directly as a float32; v1a+
    # hardcode 48000 (not stored anywhere, can't be overridden)
    center_note_field: Optional[FieldRef] = None
    center_fine_field: Optional[FieldRef] = None
    sample_rate_float_field: Optional[FieldRef] = None

    # extra raw metadata for versions where size is embedded in-stream, not in table3 (v1a/1c/23)
    embedded_header: bool = False
    stream_name_size: int = 0        # length prefix used by v1a/1c/23 mini header

    extradata_hex: str = ""          # raw bytes of the extradata blob (as read), hex-encoded
    warnings: List[str] = field(default_factory=list)

    # internal / parsing scratch fields (not part of the vgmstream struct, used across
    # the collect -> header -> data pipeline in parser.py)
    table2_entry_offset: Optional[int] = None
    subtype: int = 0
    atrac9_info: int = 0
    is_zlsd: bool = False    # lives in the separate ZLSD/prefetch section, not the main data section
    sound_index: Optional[int] = None   # table1 ("sound") index this stream's grain belongs to, if determinable
    size_field_raw_original: int = 0   # exact raw value read from stream_size_field before any scan fallback

    def total_table_size(self):
        """value that belongs in stream_size_field: extradata + payload + postdata."""
        return self.extradata_size + self.stream_size + self.postdata_size


@dataclass
class BnkFile:
    path: str = ""
    file_size: int = 0
    big_endian: bool = False
    version: int = 0            # 1 or 3 (SBv2 / SBlk)
    sections: int = 0

    sblk_offset: int = 0
    data_offset: int = 0
    data_size: int = 0
    zlsd_offset: int = 0
    zlsd_size: int = 0

    sblk_version: int = 0
    bank_name: str = ""

    # table layout (process_tables), kept for reference / debugging
    sounds_entries: int = 0
    grains_entries: int = 0
    stream_entries: int = 0
    table1_offset: int = 0
    table2_offset: int = 0
    table3_offset: int = 0
    table4_offset: int = 0
    table1_entry_size: int = 0
    table1_suboffset: int = 0
    table2_suboffset: int = 0
    total_subsongs: int = 0

    streams: List[StreamEntry] = field(default_factory=list)
    support_tier: str = "unknown"   # "full", "demux_only", "experimental", "unsupported"

    def summary_lines(self):
        lines = []
        lines.append(f"File: {self.path}")
        lines.append(f"Size: {self.file_size} bytes | Endian: {'BE' if self.big_endian else 'LE'}")
        lines.append(f"SBlk/SBv2 block version: {self.version} | sblk_version: 0x{self.sblk_version:02x} "
                     f"(suporte: {self.support_tier})")
        lines.append(f"data_offset=0x{self.data_offset:x} data_size=0x{self.data_size:x}")
        if self.zlsd_offset:
            lines.append(f"zlsd_offset=0x{self.zlsd_offset:x} zlsd_size=0x{self.zlsd_size:x}")
        lines.append(f"bank_name: {self.bank_name!r}")
        lines.append(f"Total de streams: {len(self.streams)}")
        return lines
