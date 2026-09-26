# mc3_rstm_convert

Convert audio into **Rockstar San Diego RSTM** (`.rsm`) files for
**Midnight Club 3: DUB Edition** (PS2).

It takes either a **common audio file** (MP3, FLAC, OGG, Opus, AAC, WAV, …) or a
**PS2 game rip** (GENH, FSB, ADS/SS2, RWS, SND, EA‑XA, …) and produces a `.rsm`
ready to be packed into `streams.dat`.

Runs on **Windows, Linux and macOS** — external tools are auto‑detected.

---

## How it works

The RSTM body is raw **PS‑ADPCM**, stereo interleaved at `0x10` bytes. The tool
picks the best method per input:

| Method | When | Quality |
| :----- | :--- | :------ |
| **COPY** (bit‑perfect) | source is already PS‑ADPCM (GENH, FSB, PS‑ADPCM ADS/SS2, RWS, SND) | the ADPCM nibbles are copied verbatim and only re‑interleaved to `0x10` — the decoded waveform is **identical**, no generational loss |
| **ENCODE** | anything else (MP3/FLAC/OGG/Opus/AAC/WAV, EA‑XA, PCM SS2, …) | decoded to 16‑bit PCM, then encoded **once** to PS‑ADPCM with `psxavenc` (filter search + noise shaping) |

RSTM constraints (enforced automatically): mono/stereo only, sample rate ≤ 48000 Hz
(higher rates are resampled down, extra channels are down‑mixed to stereo).

---

## Requirements

Python 3.8+ and, depending on what you convert:

| Tool | Needed for | Notes |
| :--- | :--------- | :---- |
| **psxavenc** | every ENCODE (audio files, game rips that aren't PS‑ADPCM) | https://github.com/spicyjpeg/psxavenc |
| **vgmstream‑cli** | game rips only (EA‑XA, TXTP, PCM SS2, …) | https://github.com/vgmstream/vgmstream |
| **ffmpeg / ffprobe** | optional; best decode + rate/channel detection for common audio | if absent, files are fed straight to `psxavenc` |

You do **not** need vgmstream to convert normal MP3/FLAC/etc. — only `psxavenc`
(plus `ffmpeg`, recommended).

### Building the tools on Linux

`psxavenc` (Meson + FFmpeg dev libraries):

```bash
sudo apt install meson ninja-build pkg-config build-essential \
  libavformat-dev libavcodec-dev libavutil-dev libswresample-dev libswscale-dev
cd psxavenc-main && meson setup build && meson compile -C build
# binary: build/psxavenc/psxavenc
```

`vgmstream-cli` — build from source or grab a Linux release from the vgmstream
project. `ffmpeg` is in every distro's package manager.

### Tool discovery

The script looks for each tool, in order:

1. Next to the script (`vgmstream-win64/`, `psxavenc-main/build/psxavenc/`, or the
   script folder itself).
2. On your `PATH`.

On Windows it also tries the `.exe` suffix automatically. Check what was found:

```bash
python mc3_rstm_convert.py --list-tools
```

---

## Usage

```
mc3_rstm_convert.py [options] <input>

  input                audio file or folder to convert
  -o, --output PATH    output .rsm (file input) or output folder (folder input)
  -r, --rate HZ        force output sample rate (default: source, capped at 48000)
  -c, --channels {1,2} force channel count (default: source)
  --loop-full          mark the whole stream as looping
  --overwrite          overwrite existing .rsm
  --dry-run            report the plan, write nothing
  --only SUBSTR        folder input: only sub-folders whose path contains SUBSTR
  --limit N            folder input: stop after N files
  --list-tools         print detected tool paths and exit
```

### Examples

```bash
# One file -> song.rsm (next to the input)
python mc3_rstm_convert.py song.flac

# Explicit output name
python mc3_rstm_convert.py song.mp3 -o menu_theme.rsm

# Force 32 kHz mono, looping
python mc3_rstm_convert.py jingle.wav -o jingle.rsm -r 32000 -c 1 --loop-full

# Convert a whole folder, mirroring its sub-folder layout into ./rsm
python mc3_rstm_convert.py ./album -o ./rsm --overwrite

# Batch a folder of game rips (default input folder is "Musics to insert")
python mc3_rstm_convert.py "Musics to insert" -o "RSTM output"
```

---

## Supported inputs

**Common audio (ENCODE):** `.wav .mp3 .flac .ogg .oga .opus .m4a .aac .wma .aif
.aiff .mp4 .mka .ape .wv .ac3 .alac .caf .w64`

**Game rips:**

| Format | Method | Notes |
| :----- | :----- | :---- |
| `.genh` | COPY | PS‑ADPCM, re‑interleaved `0x1000`→`0x10` |
| `.fsb`  | COPY | FSB3/FSB4 single subsong, already `0x10` |
| `.ss2` / `.ads` | COPY or ENCODE | PS‑ADPCM ADS is copied; PCM SS2 is encoded |
| `.rws`  | COPY | RenderWare 0x80D, de‑blocked per subsong (`name__sNN.rsm`) |
| `.snd`  | COPY | reads the companion `.dat`, one `.rsm` per named track |
| `.sng` / `.txtp` | ENCODE | EA‑XA → PS‑ADPCM via vgmstream + psxavenc |
| `.mpf`  | deferred | EA interactive music (hundreds of segments) — curate with `.txtp` first |

The batch mode prints a per‑file log and a summary (copy / reencode / deferred /
skip / error).

---

## Notes

* **Bit‑perfect** applies only to the COPY path: the PS‑ADPCM samples are never
  re‑encoded, only the byte interleave is changed. ENCODE paths add exactly one
  generation of PS‑ADPCM quantization.
* The SPU loop/end flag byte (byte `0x1` of every 16‑byte frame) is zeroed — it
  is metadata, not audio, and MC3 expects it clean. The decoded waveform is
  unaffected.
* `MFAudio` and `PS2STR` (used by the older `rstm_build.py`) are Windows‑only;
  `psxavenc` replaces both and is cross‑platform.
* Plain `ffmpeg` cannot encode PSX‑ADPCM (it only decodes it) — `psxavenc` does
  the encoding.
