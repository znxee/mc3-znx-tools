#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mc3_rstm_convert.py -- convert audio to Rockstar San Diego RSTM (.rsm)
                       for Midnight Club 3: DUB Edition (PS2).

The RSTM body is raw PS-ADPCM, stereo interleaved at 0x10 bytes.  Two paths:

  * COPY (bit-perfect, no re-encode) -- source is already PS-ADPCM
    (GENH, FSB, PS-ADPCM ADS/SS2, RWS, SND): the ADPCM nibbles are copied
    verbatim and only re-interleaved to 0x10, so the decoded waveform is
    identical (no generational loss).

  * ENCODE -- source is any other codec (EA-XA, PCM, MP3, FLAC, OGG, Opus,
    AAC, WAV, ...): the audio is decoded to 16-bit PCM and encoded once to
    PS-ADPCM with psxavenc.

Works on Windows, Linux and macOS: external tools are auto-detected (a ".exe"
on Windows, a bare binary on Linux/macOS, or anything found on PATH).

External tools:
  * psxavenc      (required for any ENCODE path)  -- PCM/MP3/... -> PS-ADPCM
  * vgmstream-cli (required for game rips)         -- EA-XA / TXTP / etc. decode
  * ffmpeg/ffprobe (optional)                      -- best decode of common audio

Examples:
  mc3_rstm_convert.py song.flac                 # one file  -> song.rsm
  mc3_rstm_convert.py song.mp3 -o out.rsm       # explicit output
  mc3_rstm_convert.py ./album  -o ./rsm         # whole folder (mirrors layout)
  mc3_rstm_convert.py --list-tools              # show detected tool paths
"""

import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT        = Path(__file__).resolve().parent
FRAME       = 0x10            # PS-ADPCM frame size (per channel), 28 samples
RSTM_HEADER = 0x800           # RSTM header/data start
MAX_RATE    = 48000           # RSTM sample-rate ceiling

# Common (non-game) audio the ENCODE path accepts directly.
AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac",
             ".wma", ".aif", ".aiff", ".mp4", ".mka", ".ape", ".wv", ".ac3",
             ".alac", ".caf", ".w64"}
# Pure support/metadata -- never converted.
IGNORE_EXT = {".txt", ".txth", ".lst", ".dat", ".bak", ".exe", ".dll", ".ini"}


# --------------------------------------------------------------------------- #
# Cross-platform tool discovery
# --------------------------------------------------------------------------- #
def find_tool(names, extra_dirs=()):
    """Locate an external tool by trying, in order: the given local dirs, then
    PATH.  Handles the ".exe" suffix on Windows automatically."""
    exe = ".exe" if os.name == "nt" else ""
    cands = []
    for n in names:
        cands += ([n + exe] if exe else []) + [n]
    for d in extra_dirs:
        for c in cands:
            p = Path(d) / c
            if p.is_file():
                return p
    for c in cands:
        w = shutil.which(c)
        if w:
            return Path(w)
    return None


VGMSTREAM = find_tool(["vgmstream-cli", "vgmstream_cli"],
                      [ROOT / "vgmstream-win64", ROOT / "vgmstream", ROOT])
PSXAVENC  = find_tool(["psxavenc"],
                      [ROOT, ROOT / "psxavenc-main" / "build" / "psxavenc",
                       ROOT / "psxavenc-main" / "build"])
FFMPEG    = find_tool(["ffmpeg"])
FFPROBE   = find_tool(["ffprobe"])


def require(tool, human):
    if tool is None:
        raise RuntimeError(f"{human} not found (install it or put it next to this script)")
    return str(tool)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def u32(b, off):
    return struct.unpack_from("<I", b, off)[0]


def u16(b, off):
    return struct.unpack_from("<H", b, off)[0]


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def probe(path, subsong=None):
    """dict(encoding, sample_rate, channels, interleave, streams) via vgmstream -m."""
    cmd = [require(VGMSTREAM, "vgmstream-cli"), "-m"]
    if subsong:
        cmd += ["-s", str(subsong)]
    cmd += [str(path)]
    out = subprocess.run(cmd, capture_output=True, text=True, errors="replace").stdout
    info = {"encoding": "", "sample_rate": 0, "channels": 0, "interleave": 0, "streams": 1}
    for line in out.splitlines():
        low = line.lower()
        if low.startswith("encoding:"):
            info["encoding"] = line.split(":", 1)[1].strip()
        elif low.startswith("sample rate:"):
            info["sample_rate"] = int(re.search(r"\d+", line).group())
        elif low.startswith("channels:"):
            info["channels"] = int(re.search(r"\d+", line).group())
        elif low.startswith("interleave:"):
            m = re.search(r"0x[0-9a-fA-F]+", line)
            if m:
                info["interleave"] = int(m.group(), 16)
        elif low.startswith("stream count:"):
            info["streams"] = int(re.search(r"\d+", line).group())
    return info


def probe_mean(path):
    """(sample_rate, channels) for a common audio file, or (0, 0) if unknown."""
    if FFPROBE:
        try:
            out = subprocess.run(
                [str(FFPROBE), "-v", "error", "-select_streams", "a:0",
                 "-show_entries", "stream=sample_rate,channels",
                 "-of", "default=noprint_wrappers=1", str(path)],
                capture_output=True, text=True).stdout
            d = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
            return int(d.get("sample_rate", 0) or 0), int(d.get("channels", 0) or 0)
        except Exception:
            pass
    if path.suffix.lower() == ".wav":
        try:
            b = path.read_bytes()[:0x2C]
            if b[:4] == b"RIFF" and b[8:12] == b"WAVE":
                return u32(b, 0x18), u16(b, 0x16)
        except Exception:
            pass
    return 0, 0


def is_psx(enc):
    return "playstation 4-bit adpcm" in enc.lower()


# --------------------------------------------------------------------------- #
# RSTM writer (header fields + SPU-flag normalization, matching rstm_build.py)
# --------------------------------------------------------------------------- #
def write_rstm(out_path, raw, sample_rate, channels, loop_start=None, loop_end=None):
    """raw : PS-ADPCM already interleaved at 0x10 for stereo (flat for mono)."""
    assert 1 <= channels <= 2, "RSTM supports mono/stereo only"
    assert sample_rate <= MAX_RATE, f"RSTM sample rate must be <= {MAX_RATE}"
    assert len(raw) % FRAME == 0, "PS-ADPCM data must be frame-aligned (0x10)"

    data = bytearray(raw)
    # Wipe the SPU loop/end flag byte (0x1 of every 16-byte frame).  It is
    # metadata, not audio -- the decoded PCM is untouched; MC3 wants it clean.
    data[0x1::0x10] = bytes(len(data) // 0x10)

    if loop_start is None or loop_end is None:      # default: no loop
        loop_start, loop_end = 0, len(data)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(b"\x00" * RSTM_HEADER)
        f.seek(0x0);  f.write(b"RSTM")
        f.seek(0x8);  f.write(struct.pack("<I", sample_rate))
        f.seek(0xC);  f.write(struct.pack("<I", channels))
        f.seek(0x18); f.write(struct.pack("<I", len(data)))
        f.seek(0x1C); f.write(struct.pack("<I", loop_start))
        f.seek(0x20); f.write(struct.pack("<I", loop_end))
        f.seek(RSTM_HEADER)
        f.write(data)


# --------------------------------------------------------------------------- #
# Interleave helpers (lossless byte reshuffling -- ADPCM frames untouched)
# --------------------------------------------------------------------------- #
def deinterleave(data, channels, interleave):
    if channels == 1:
        return [bytearray(data)]
    chans = [bytearray() for _ in range(channels)]
    block = channels * interleave
    full = (len(data) // block) * block
    pos, c = 0, 0
    while pos < full:
        chans[c].extend(data[pos:pos + interleave])
        pos += interleave
        c = (c + 1) % channels
    rem = len(data) - full
    if rem:
        if rem % channels:
            raise ValueError(f"trailing bytes {rem} not divisible by {channels} channels")
        per = rem // channels
        for c in range(channels):
            chans[c].extend(data[full + c * per: full + (c + 1) * per])
    return chans


def reinterleave_0x10(chans):
    lens = {len(c) for c in chans}
    if len(lens) != 1:
        n = (min(lens) // FRAME) * FRAME
        chans = [c[:n] for c in chans]
    if len(chans) == 1:
        return bytes(chans[0])
    out = bytearray()
    L = len(chans[0])
    for off in range(0, L, FRAME):
        for c in chans:
            out += c[off:off + FRAME]
    return bytes(out)


# --------------------------------------------------------------------------- #
# COPY extractors  ->  (raw_0x10, sample_rate, channels)   [no re-encode]
# --------------------------------------------------------------------------- #
def extract_genh(path):
    b = path.read_bytes()
    if b[:4] != b"GENH":
        raise ValueError("not a GENH file")
    channels    = u32(b, 0x04)
    interleave  = u32(b, 0x08)
    sample_rate = u32(b, 0x0C)
    codec       = u32(b, 0x18)
    start       = u32(b, 0x1C)
    header_size = u32(b, 0x20)
    if header_size == 0:
        start = header_size = 0x800
    data_size = u32(b, 0x50) if header_size >= 0x100 else 0
    if data_size == 0:
        data_size = len(b) - start
    if codec != 0:
        raise ValueError(f"GENH codec {codec} is not PS-ADPCM (0)")
    raw = b[start:start + data_size]
    return reinterleave_0x10(deinterleave(raw, channels, interleave)), sample_rate, channels


def extract_fsb(path):
    b = path.read_bytes()
    if b[:4] not in (b"FSB3", b"FSB4"):
        raise ValueError("not an FSB3/FSB4 file")
    n_sub    = u32(b, 0x04)
    shdrsize = u32(b, 0x08)
    datasize = u32(b, 0x0C)
    if n_sub != 1:
        raise ValueError(f"multi-subsong FSB ({n_sub}) not supported in copy path")
    data_start = 0x18 + shdrsize
    raw = b[data_start:data_start + datasize]
    meta = probe(path)
    if not is_psx(meta["encoding"]):
        raise ValueError(f"FSB codec is {meta['encoding']}, not PS-ADPCM")
    raw = raw[: (len(raw) // FRAME) * FRAME]
    return raw, meta["sample_rate"], meta["channels"]


def extract_ads(path):
    b = path.read_bytes()
    if b[:4] != b"SShd":
        raise ValueError("not an SShd/ADS file")
    sshd_size   = u32(b, 0x04)
    encoding    = u32(b, 0x08)
    sample_rate = u32(b, 0x0C)
    channels    = u32(b, 0x10)
    interleave  = u32(b, 0x14)
    if encoding != 0x10:
        raise ValueError("ADS is not PS-ADPCM (encoding != 0x10)")
    body = b[sshd_size + 0x8:]
    if body[:4] != b"SSbd":
        raise ValueError("SSbd body not found")
    ssbd_size = u32(body, 0x04)
    raw = body[0x8:0x8 + ssbd_size]
    il = interleave if channels > 1 else FRAME
    if channels > 1 and il != FRAME:
        raw = reinterleave_0x10(deinterleave(raw, channels, il))
    raw = raw[: (len(raw) // FRAME) * FRAME]
    return raw, sample_rate, channels


# --------------------------------------------------------------------------- #
# RWS - RenderWare Stream (0x80D), PS2 PS-ADPCM.  Lossless de-block per subsong.
# --------------------------------------------------------------------------- #
RWS_PSX_CODEC = 0xD9EA9798


def _rws_string_size(b, off):
    for i in range(255):
        if b[off + i] == 0:
            return i + (0x10 - (i % 0x10))
    return 0


def parse_rws(path):
    b = path.read_bytes()
    if u32(b, 0x00) != 0x0000080D:
        raise ValueError("not an RWS 0x80D file")
    if u32(b, 0x04) + 0x0C != len(b):
        raise ValueError("RWS file size mismatch")
    if u32(b, 0x0C) != 0x0000080E:
        raise ValueError("RWS header chunk missing")
    header_size = u32(b, 0x10)
    data_offset = 0x0C + 0x0C + header_size
    if u32(b, data_offset) != 0x0000080F:
        raise ValueError("RWS data chunk missing")

    off = 0x18
    total_segments = u32(b, off + 0x20)
    total_layers = u32(b, off + 0x28)
    if not (1 <= total_segments <= 0x10000 and 1 <= total_layers <= 0x10000):
        raise ValueError("RWS looks big-endian / unsupported (PS2 LE only)")
    off += 0x50
    off += _rws_string_size(b, off)

    seg = []
    for _ in range(total_segments):
        seg.append((u32(b, off + 0x18), u32(b, off + 0x1C)))
        off += 0x20
    usable = []
    for _ in range(total_segments * total_layers):
        usable.append(u32(b, off)); off += 0x04
    off += 0x10 * total_segments
    for _ in range(total_segments):
        off += _rws_string_size(b, off)

    layers = []
    block_layers_size = 0
    for _ in range(total_layers):
        interleave = u16(b, off + 0x18)
        frame_size = u16(b, off + 0x1A)
        block_size = u32(b, off + 0x20)
        layer_start = u32(b, off + 0x24)
        block_layers_size += u32(b, off + 0x10)
        layers.append((interleave, frame_size, block_size, layer_start))
        off += 0x28

    lcfg = []
    for _ in range(total_layers):
        sr = u32(b, off + 0x00)
        ch = b[off + 0x0D]
        codec = u32(b, off + 0x1C)
        lcfg.append((sr, ch, codec))
        off += 0x2C
        if codec == 0xF86215B0:
            off += 0x60
        off += 0x04

    subs = []
    for sub in range(1, total_segments * total_layers + 1):
        tl = ((sub - 1) % total_layers) + 1
        ts = ((sub - 1) // total_layers) + 1
        sr, ch, codec = lcfg[tl - 1]
        interleave, frame_size, block_size, layer_start = layers[tl - 1]
        subs.append({
            "index": sub,
            "start": data_offset + 0x0C + seg[ts - 1][1] + layer_start,
            "usable": usable[sub - 1],
            "block_size": block_size,
            "full_block": block_layers_size,
            "channels": ch,
            "sample_rate": sr,
            "codec": codec,
        })
    return b, subs


def deblock_rws(b, s):
    ch = s["channels"]
    if ch not in (1, 2):
        raise ValueError(f"RWS channels={ch} unsupported")
    if s["usable"] % ch:
        raise ValueError("RWS usable size not divisible by channels")
    interleave = s["block_size"] // ch
    per_ch_total = s["usable"] // ch
    per_ch = [bytearray() for _ in range(ch)]
    pos = s["start"]
    remaining = per_ch_total
    while remaining > 0:
        take = min(interleave, remaining)
        for i in range(ch):
            o = pos + interleave * i
            per_ch[i] += b[o:o + take]
        remaining -= take
        pos += s["full_block"]
    return reinterleave_0x10(per_ch)


# --------------------------------------------------------------------------- #
# SND - "snda" header table pointing into a companion .dat bigfile (mono PSX).
# --------------------------------------------------------------------------- #
def parse_snd(path):
    b = path.read_bytes()
    if b[:4] != b"snda":
        raise ValueError("not a 'snda' file")
    count = u32(b, 0x08)
    spacing = 0x40
    subs = []
    for i in range(count):
        base = i * spacing
        name = b[0x20 + base:0x20 + base + 0x14].split(b"\x00")[0].decode("ascii", "replace")
        subs.append({
            "name": name or f"s{i:02d}",
            "sample_rate": u32(b, 0x34 + base),
            "start": u32(b, 0x38 + base),
            "size": u32(b, 0x40 + base),
        })
    return subs


# --------------------------------------------------------------------------- #
# ENCODE paths  ->  (raw_0x10, sample_rate, channels)
# --------------------------------------------------------------------------- #
def _psxavenc(src, rate, channels, tmpdir):
    """Encode a decodable audio file (WAV etc.) to raw PS-ADPCM at 0x10."""
    raw = tmpdir / "enc.raw"
    if channels == 1:
        cmd = [require(PSXAVENC, "psxavenc"), "-t", "spu", "-f", str(rate),
               "-a", "16", "-n", "-D", str(src), str(raw)]
    else:
        cmd = [require(PSXAVENC, "psxavenc"), "-t", "spui", "-f", str(rate), "-c", "2",
               "-i", "16", "-a", "16", "-n", "-D", str(src), str(raw)]
    subprocess.run(cmd, check=True, capture_output=True)
    data = raw.read_bytes()
    return data[: (len(data) // FRAME) * FRAME]


def encode_audio(path, tmpdir, rate=None, channels=None):
    """Common audio (MP3/FLAC/OGG/Opus/AAC/WAV/...) -> PS-ADPCM RSTM body."""
    sr, ch = probe_mean(path)
    rate = min(int(rate) if rate else (sr or 44100), MAX_RATE)
    channels = int(channels) if channels else (ch or 2)
    channels = 2 if channels >= 2 else 1
    src = Path(path)
    # ffmpeg (if present) normalizes to s16le WAV at the exact rate/channels,
    # guaranteeing decodability and avoiding a second resample in psxavenc.
    if FFMPEG:
        wav = tmpdir / "dec.wav"
        subprocess.run([str(FFMPEG), "-y", "-hide_banner", "-loglevel", "error",
                        "-i", str(path), "-ar", str(rate), "-ac", str(channels),
                        "-c:a", "pcm_s16le", str(wav)], check=True, capture_output=True)
        src = wav
    return _psxavenc(src, rate, channels, tmpdir), rate, channels


def reencode_game(path, subsong, tmpdir):
    """Game rip (EA-XA / PCM SS2 / TXTP / ...) decoded by vgmstream, then encoded."""
    meta = probe(path, subsong)
    rate, ch = meta["sample_rate"], meta["channels"]
    if ch < 1 or rate < 1:
        raise ValueError("vgmstream could not read stream info")
    rate = min(rate, MAX_RATE)
    ch = 2 if ch >= 2 else 1
    wav = tmpdir / "dec.wav"
    cmd = [require(VGMSTREAM, "vgmstream-cli"), "-o", str(wav)]
    if subsong:
        cmd += ["-s", str(subsong)]
    cmd += [str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return _psxavenc(wav, rate, ch, tmpdir), rate, ch


# --------------------------------------------------------------------------- #
# Per-file dispatch
# --------------------------------------------------------------------------- #
class Result:
    def __init__(self, method, note=""):
        self.method = method      # copy | reencode | deferred | error | skip
        self.note = note


def convert_one(src, out_dir, name, args):
    ext = src.suffix.lower()

    def emit(raw, rate, ch, out_name=name):
        out = out_dir / f"{out_name}.rsm"
        if out.exists() and not args.overwrite:
            return Result("skip", "exists")
        if args.dry_run:
            return None
        ls = le = None
        if args.loop_full:
            ls, le = 0, len(raw) - FRAME * ch
        write_rstm(out, raw, rate, ch, ls, le)
        return None

    try:
        # ---- common audio (MP3/FLAC/OGG/Opus/AAC/WAV/...) -> ENCODE ----------
        if ext in AUDIO_EXT:
            with tempfile.TemporaryDirectory() as td:
                raw, rate, ch = encode_audio(src, Path(td), args.rate, args.channels)
                return emit(raw, rate, ch) or Result("reencode", f"audio {rate}Hz {ch}ch")

        # ---- COPY paths (no re-encode) --------------------------------------
        if ext == ".genh":
            raw, rate, ch = extract_genh(src)
            return emit(raw, rate, ch) or Result("copy", "genh 0x1000->0x10")

        if ext == ".fsb":
            meta = probe(src)
            if meta["streams"] == 1:
                raw, rate, ch = extract_fsb(src)
                return emit(raw, rate, ch) or Result("copy", "fsb raw 0x10")
            return reencode_multi(src, meta["streams"], out_dir, name, args)

        if ext in (".ss2", ".ads"):
            b = src.read_bytes()
            if b[:4] == b"SShd" and u32(b, 0x08) == 0x10:      # PS-ADPCM ADS
                raw, rate, ch = extract_ads(src)
                return emit(raw, rate, ch) or Result("copy", "ads raw")
            with tempfile.TemporaryDirectory() as td:          # PCM SS2 -> encode
                raw, rate, ch = reencode_game(src, None, Path(td))
                return emit(raw, rate, ch) or Result("reencode", "pcm->psx")

        # ---- game rips that need decode+encode ------------------------------
        if ext in (".sng", ".txtp"):
            with tempfile.TemporaryDirectory() as td:
                raw, rate, ch = reencode_game(src, None, Path(td))
                return emit(raw, rate, ch) or Result("reencode", "ea-xa->psx")

        if ext == ".mpf":
            if list(src.parent.glob("*.txtp")):
                return Result("skip", "covered by .txtp")
            meta = probe(src)
            return Result("deferred",
                          f"EA interactive music, {meta['streams']} segments - "
                          f"curate whole tracks with .txtp")

        if ext == ".mus":
            return Result("skip", "data for .mpf/.txtp")

        # ---- RWS / SND : lossless PS-ADPCM de-block / bigfile extract --------
        if ext == ".rws":
            b, subs = parse_rws(src)
            if any(s["codec"] != RWS_PSX_CODEC for s in subs):
                return Result("deferred", "RWS non-PSX codec not handled")
            for s in subs:
                out = out_dir / f"{name}__s{s['index']:02d}.rsm"
                if out.exists() and not args.overwrite:
                    continue
                if not args.dry_run:
                    write_rstm(out, deblock_rws(b, s), s["sample_rate"], s["channels"])
            return Result("copy", f"rws {len(subs)} subsongs de-blocked")

        if ext == ".snd":
            dat = src.with_suffix(".dat")
            if not dat.exists():
                return Result("error", f"companion .dat not found: {dat.name}")
            db = dat.read_bytes()
            for s in parse_snd(src):
                out = out_dir / f"{s['name']}.rsm"
                if out.exists() and not args.overwrite:
                    continue
                if args.dry_run:
                    continue
                raw = db[s["start"]:s["start"] + s["size"]]
                raw = raw[: (len(raw) // FRAME) * FRAME]
                write_rstm(out, raw, s["sample_rate"], 1)
            return Result("copy", "snd mono from .dat")

        if ext in IGNORE_EXT:
            return Result("skip", "support file")
        return Result("skip", f"unhandled ext {ext}")

    except subprocess.CalledProcessError as e:
        detail = (e.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        return Result("error", f"tool failed: {detail[0]}")
    except Exception as e:
        return Result("error", str(e))


def reencode_multi(src, streams, out_dir, name, args):
    with tempfile.TemporaryDirectory() as td:
        for s in range(1, streams + 1):
            out = out_dir / f"{name}__s{s:02d}.rsm"
            if out.exists() and not args.overwrite:
                continue
            if args.dry_run:
                continue
            raw, rate, ch = reencode_game(src, s, Path(td))
            write_rstm(out, raw, rate, ch)
    return Result("reencode", f"{streams} subsongs")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
TAGS = {"copy": "COPY ", "reencode": "ENC  ", "deferred": "DEFER",
        "error": "ERROR", "skip": "skip "}


def convert_file(src, out_dir, name, args):
    res = convert_one(src, out_dir, name, args)
    if res is None:
        res = Result("copy")
    return res


def run_batch(src_root, out_root, args):
    files = sorted(p for p in src_root.rglob("*") if p.is_file())
    stats, deferred, errors, done = {}, [], [], 0
    for src in files:
        rel_dir = src.parent.relative_to(src_root)
        if args.only and args.only.lower() not in str(rel_dir).lower():
            continue
        if src.suffix.lower() in IGNORE_EXT:
            continue
        res = convert_file(src, out_root / rel_dir, src.stem, args)
        stats[res.method] = stats.get(res.method, 0) + 1
        if res.method in ("copy", "reencode", "deferred", "error"):
            print(f"[{TAGS[res.method]}] {rel_dir}/{src.name}"
                  + (f"   ({res.note})" if res.note else ""))
        if res.method == "deferred":
            deferred.append(f"{rel_dir}/{src.name}  -- {res.note}")
        if res.method == "error":
            errors.append(f"{rel_dir}/{src.name}  -- {res.note}")
        done += 1
        if args.limit and done >= args.limit:
            break

    print("\n" + "=" * 60 + "\nSUMMARY")
    for k in ("copy", "reencode", "deferred", "skip", "error"):
        if k in stats:
            print(f"  {k:9}: {stats[k]}")
    print(f"  output   : {out_root}")
    for label, items in (("DEFERRED", deferred), ("ERRORS", errors)):
        if items:
            print(f"\n{label}:")
            for it in items:
                print("   -", it)
    return 1 if errors else 0


def main():
    ap = argparse.ArgumentParser(
        prog="mc3_rstm_convert.py",
        description="Convert audio (MP3/FLAC/OGG/Opus/AAC/WAV and PS2 game rips) "
                    "to Midnight Club 3 RSTM (.rsm).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               "  %(prog)s song.flac                 one file  -> song.rsm\n"
               "  %(prog)s song.mp3 -o out.rsm       explicit output\n"
               "  %(prog)s ./album  -o ./rsm         whole folder (mirrors layout)\n"
               "  %(prog)s --list-tools              show detected tool paths")
    ap.add_argument("input", nargs="?", default="Musics to insert",
                    help="audio file or folder to convert")
    ap.add_argument("-o", "--output",
                    help="output .rsm (file input) or output folder (folder input)")
    ap.add_argument("-r", "--rate", type=int,
                    help=f"force output sample rate in Hz (default: source, capped at {MAX_RATE})")
    ap.add_argument("-c", "--channels", type=int, choices=(1, 2),
                    help="force output channel count (default: source)")
    ap.add_argument("--loop-full", action="store_true",
                    help="mark the whole stream as looping")
    ap.add_argument("--only", default="",
                    help="folder input: only process sub-folders containing this substring")
    ap.add_argument("--overwrite", action="store_true", help="overwrite existing .rsm")
    ap.add_argument("--dry-run", action="store_true", help="report the plan, write nothing")
    ap.add_argument("--limit", type=int, default=0, help="folder input: stop after N files")
    ap.add_argument("--list-tools", action="store_true",
                    help="print detected external tool paths and exit")
    args = ap.parse_args()

    if args.list_tools:
        for name, tool in (("psxavenc", PSXAVENC), ("vgmstream-cli", VGMSTREAM),
                           ("ffmpeg", FFMPEG), ("ffprobe", FFPROBE)):
            print(f"  {name:14}: {tool if tool else '(not found)'}")
        return 0

    inp = Path(args.input)
    if not inp.exists():
        die(f"input not found: {inp}")

    if inp.is_file():
        if args.output:
            outp = Path(args.output)
            if outp.is_dir() or str(args.output).endswith(("/", "\\")):
                out_dir, name = outp, inp.stem
            else:
                out_dir, name = outp.parent, outp.stem
        else:
            out_dir, name = inp.parent, inp.stem
        res = convert_file(inp, out_dir if str(out_dir) else Path("."), name, args)
        note = f"   ({res.note})" if res.note else ""
        print(f"[{TAGS.get(res.method, '?')}] {inp.name} -> {out_dir}/{name}.rsm{note}"
              if res.method != "error" else f"[ERROR] {inp.name}: {res.note}")
        return 1 if res.method == "error" else 0

    out_root = Path(args.output) if args.output else ROOT / "RSTM output"
    return run_batch(inp.resolve(), out_root.resolve(), args)


if __name__ == "__main__":
    sys.exit(main())
