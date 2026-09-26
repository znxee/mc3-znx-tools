"""Build/read GENH headers (vgmstream's generic header for headerless streams),
translated from src/meta/genh.c. Used so DEMUXed streams are directly playable/editable
in any GENH-aware tool (foobar2000, Audacity via vgmstream plugin, etc.) without needing
our manifest.json, and so MUX can accept an edited .genh back and recover the raw payload
plus whatever metadata the user tweaked in the header.
"""
import struct

MAGIC = b"GENH"
HEADER_SIZE = 0x100  # we always write a full extended header (simpler, tiny overhead)

# only the genh_type codes we can actually produce/consume for BNK codecs
GENH_TYPE_PSX = 0
GENH_TYPE_PCM16BE = 3
GENH_TYPE_PCM16LE = 4
GENH_TYPE_MPEG = 8

_BNK_CODEC_TO_GENH = {
    "PSX": GENH_TYPE_PSX,
    "MPEG": GENH_TYPE_MPEG,
    # PCM16 resolved separately depending on the BNK's endianness (see codec_to_genh_type)
}

_GENH_TO_BNK_CODEC = {
    GENH_TYPE_PSX: "PSX",
    GENH_TYPE_PCM16BE: "PCM16",
    GENH_TYPE_PCM16LE: "PCM16",
    GENH_TYPE_MPEG: "MPEG",
}


class GenhError(Exception):
    pass


def codec_to_genh_type(codec: str, big_endian_pcm: bool = False):
    if codec == "PCM16":
        return GENH_TYPE_PCM16BE if big_endian_pcm else GENH_TYPE_PCM16LE
    return _BNK_CODEC_TO_GENH.get(codec)


def is_genh_supported(codec: str) -> bool:
    return codec_to_genh_type(codec) is not None or codec == "PCM16"


def build_genh(codec: str, channels: int, sample_rate: int, interleave: int,
               num_samples: int, loop_flag: bool, loop_start: int, loop_end: int,
               big_endian_pcm: bool = False) -> bytes:
    gtype = codec_to_genh_type(codec, big_endian_pcm)
    if gtype is None:
        raise GenhError(f"codec {codec} has no equivalent GENH type")

    h = bytearray(HEADER_SIZE)
    h[0:4] = MAGIC
    struct.pack_into("<I", h, 0x04, channels)
    struct.pack_into("<I", h, 0x08, interleave or 0)
    struct.pack_into("<I", h, 0x0c, sample_rate)
    struct.pack_into("<i", h, 0x10, loop_start if loop_flag else -1)
    struct.pack_into("<i", h, 0x14, loop_end if loop_flag else 0)
    struct.pack_into("<I", h, 0x18, gtype)
    struct.pack_into("<I", h, 0x1c, HEADER_SIZE)   # start_offset
    struct.pack_into("<I", h, 0x20, HEADER_SIZE)   # header_size
    struct.pack_into("<i", h, 0x40, num_samples)
    struct.pack_into("<i", h, 0x44, 0)             # skip_samples
    h[0x48] = 0                                    # skip_samples_mode: autodetect
    h[0x4b] = 0                                    # codec_mode
    struct.pack_into("<I", h, 0x50, 0)             # data_size: 0 = rest of file
    struct.pack_into("<I", h, 0x54, 0)             # interleave_last
    return bytes(h)


def read_genh(data: bytes) -> dict:
    """Mirrors parse_genh() in genh.c. Returns a dict with the fields we care about."""
    if data[0:4] != MAGIC:
        raise GenhError("not a GENH file (signature missing)")

    def u32(off):
        return struct.unpack_from("<I", data, off)[0]

    def s32(off):
        return struct.unpack_from("<i", data, off)[0]

    channels = u32(0x04)
    interleave = u32(0x08)
    sample_rate = u32(0x0c)
    loop_start_sample = s32(0x10)
    loop_end_sample = s32(0x14)
    genh_codec = u32(0x18)
    start_offset = u32(0x1c)
    header_size = u32(0x20)

    if header_size == 0:
        start_offset = 0x800
        header_size = 0x800

    if header_size > start_offset:
        raise GenhError("header_size larger than start_offset (invalid GENH)")
    if header_size < 0x24:
        raise GenhError("header_size below the minimum (0x24)")

    num_samples = 0
    data_size = 0
    if header_size >= 0x100:
        num_samples = s32(0x40)
        data_size = u32(0x50)

    if data_size == 0:
        data_size = len(data) - start_offset
    if num_samples <= 0:
        num_samples = loop_end_sample

    return {
        "channels": channels,
        "interleave": interleave,
        "sample_rate": sample_rate,
        "loop_start_sample": loop_start_sample,
        "loop_end_sample": loop_end_sample,
        "loop_flag": loop_start_sample != -1,
        "codec": _GENH_TO_BNK_CODEC.get(genh_codec),
        "genh_codec_raw": genh_codec,
        "start_offset": start_offset,
        "header_size": header_size,
        "num_samples": num_samples,
        "data_size": data_size,
    }


def is_genh_file(path_or_bytes) -> bool:
    if isinstance(path_or_bytes, (bytes, bytearray)):
        head = bytes(path_or_bytes[:4])
    else:
        with open(path_or_bytes, "rb") as f:
            head = f.read(4)
    return head == MAGIC
