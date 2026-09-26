"""Low level big/little-endian binary helpers mirroring vgmstream's read_u32 etc."""
import struct


class BinaryReader:
    def __init__(self, data: bytes, big_endian: bool):
        self.data = data
        self.big_endian = big_endian
        self._pfx = ">" if big_endian else "<"

    def size(self):
        return len(self.data)

    def u8(self, off):
        return self.data[off]

    def s8(self, off):
        v = self.data[off]
        return v - 256 if v >= 128 else v

    def u16(self, off):
        return struct.unpack_from(self._pfx + "H", self.data, off)[0]

    def u32(self, off):
        return struct.unpack_from(self._pfx + "I", self.data, off)[0]

    def s32(self, off):
        return struct.unpack_from(self._pfx + "i", self.data, off)[0]

    def u64(self, off):
        return struct.unpack_from(self._pfx + "Q", self.data, off)[0]

    def f32(self, off):
        return struct.unpack_from(self._pfx + "f", self.data, off)[0]

    def id32be(self, off):
        """reads 4 raw bytes and compares as a big-endian fourcc string, like is_id32be()"""
        return self.data[off:off + 4]

    def is_id32be(self, off, fourcc: str):
        return self.data[off:off + 4] == fourcc.encode("ascii")

    def read_cstring(self, off, maxlen):
        end = off
        limit = min(len(self.data), off + maxlen)
        while end < limit and self.data[end] != 0:
            end += 1
        return self.data[off:end].decode("latin-1", errors="replace")


def guess_endian32_bnk(data: bytes, off=0x00):
    """BNK-specific endianness guess: the 'version' field must be 1 or 3."""
    le = struct.unpack_from("<I", data, off)[0]
    if le in (1, 3):
        return False  # little endian
    be = struct.unpack_from(">I", data, off)[0]
    if be in (1, 3):
        return True  # big endian
    return False


def get_id32le(fourcc: str) -> int:
    return struct.unpack("<I", fourcc.encode("ascii"))[0]
