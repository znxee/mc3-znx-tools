"""Small WAV helpers for audio preview: mono/stereo 16-bit PCM writing (in-memory or
file) and a simple linear-interpolation resampler. No external dependencies."""
import struct
from typing import List, Sequence


def build_wav_bytes(samples: Sequence[int], sample_rate: int, channels: int = 1) -> bytes:
    """samples: interleaved int16 values."""
    payload = struct.pack("<%dh" % len(samples), *samples)
    byte_rate = sample_rate * channels * 2
    block_align = channels * 2
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(payload), b"WAVE",
        b"fmt ", 16, 1, channels, sample_rate, byte_rate, block_align, 16,
        b"data", len(payload),
    )
    return header + payload


def write_wav(path: str, samples: Sequence[int], sample_rate: int, channels: int = 1):
    with open(path, "wb") as f:
        f.write(build_wav_bytes(samples, sample_rate, channels))


def resample_linear(samples: Sequence[int], src_rate: float, dst_rate: float) -> List[int]:
    """Mono linear-interpolation resampler (fine for preview purposes)."""
    if not samples or src_rate <= 0 or dst_rate <= 0:
        return []
    if abs(src_rate - dst_rate) < 1e-9:
        return list(samples)
    n_src = len(samples)
    n_dst = max(1, int(n_src * dst_rate / src_rate))
    out = []
    step = (n_src - 1) / max(1, n_dst - 1) if n_dst > 1 else 0.0
    for i in range(n_dst):
        pos = i * step
        i0 = int(pos)
        frac = pos - i0
        s0 = samples[i0]
        s1 = samples[i0 + 1] if i0 + 1 < n_src else s0
        out.append(int(s0 + (s1 - s0) * frac))
    return out


def clamp16(v: int) -> int:
    if v > 32767:
        return 32767
    if v < -32768:
        return -32768
    return int(v)
