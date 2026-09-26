"""Encode/patch the small per-stream 'extradata' header blobs that some BNK codecs
carry right before the raw payload (num_samples, loop points, ATRAC9 config, etc).

These mirror the process_extradata_* readers in bnk_sony.c. We never need a full
"writer from scratch": on MUX we always start from the extradata bytes captured at
DEMUX time and only patch the specific fields the user chose to override, so any
byte we don't understand (reserved/padding) is preserved as-is.
"""
import struct


def _pack(big_endian, fmt):
    return (">" if big_endian else "<") + fmt


def patch_extradata(codec: str, big_endian: bool, blob: bytearray, overrides: dict) -> bytearray:
    """blob: mutable copy of the original extradata bytes for one stream.
    overrides: subset of {"num_samples", "loop_start", "loop_length", "channels",
    "atrac9_info" (int)} - only present keys are patched."""
    if not overrides:
        return blob
    blob = bytearray(blob)

    def put_u32(off, val):
        struct.pack_into(_pack(big_endian, "I"), blob, off, val & 0xFFFFFFFF)

    def put_s32(off, val):
        struct.pack_into(_pack(big_endian, "i"), blob, off, val)

    if codec in ("PCM16", "PSX", "HEVAG") and len(blob) >= 0x20:
        # process_extradata_0x10_pcm_psx layout (relative to extradata start):
        # 0x10 num_samples, 0x14 channels, 0x18 loop_start, 0x1c loop_length
        if "num_samples" in overrides:
            put_s32(0x10, overrides["num_samples"])
        if "channels" in overrides:
            put_u32(0x14, overrides["channels"])
        if "loop_start" in overrides:
            put_s32(0x18, overrides["loop_start"])
        if "loop_length" in overrides:
            put_s32(0x1c, overrides["loop_length"])

    elif codec == "ATRAC9" and len(blob) >= 0x1c:
        if len(blob) == 0x14:  # process_extradata_0x14_atrac9
            if "atrac9_info" in overrides:
                struct.pack_into(">I", blob, 0x0c, overrides["atrac9_info"] & 0xFFFFFFFF)
            if "loop_length" in overrides:
                put_s32(0x14, overrides["loop_length"])
            if "loop_start" in overrides:
                put_s32(0x18, overrides["loop_start"])
        else:  # process_extradata_0x80_atrac9
            if "atrac9_info" in overrides:
                struct.pack_into(">I", blob, 0x14, overrides["atrac9_info"] & 0xFFFFFFFF)
            if "channels" in overrides:
                put_u32(0x1c, overrides["channels"])
            if "loop_length" in overrides:
                put_s32(0x24, overrides["loop_length"])
            if "loop_start" in overrides:
                put_s32(0x28, overrides["loop_start"])

    elif codec == "RIFF_ATRAC9" and len(blob) >= 0x2c:
        if "atrac9_info" in overrides:
            struct.pack_into(">I", blob, 0x14, overrides["atrac9_info"] & 0xFFFFFFFF)
        if "channels" in overrides:
            put_u32(0x1c, overrides["channels"])
        if "loop_length" in overrides:
            put_s32(0x24, overrides["loop_length"])
        if "loop_start" in overrides:
            put_s32(0x28, overrides["loop_start"])

    elif codec == "MPEG" and len(blob) >= 0x28:
        # process_extradata_0x80_mpeg (offset depends on caller, blob is already sliced to it)
        if "encoder_delay" in overrides:
            put_s32(0x20, overrides["encoder_delay"])
        if "num_samples" in overrides:
            put_s32(0x24, overrides["num_samples"])

    return blob
