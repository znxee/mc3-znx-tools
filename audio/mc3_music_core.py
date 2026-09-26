#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mc3_music_core.py - UI-independent backend for the MC3 Music Manager.

Everything the GUI needs to organize and insert music into Midnight Club 3:
  - read streams.dat (Hash archive) + MC3_PS2_Streams.lst
  - read in-game display names from assets/fonts/mcstrings01.strtbl
  - build a new streams.dat (stream-copy, no full extraction)
  - update the .play playlists (city/garage prioritized)
  - patch every mcstrings*.strtbl with the new song labels

STRTBL text formatting (research results, retail values):
  - font name  : per entry; retail music uses "smallspace"; 24 .fonttex fonts
                 ship with the game (see list_fonts()).
  - size       : per entry (v2 table); retail music entries use 15
                 (menus use 19/3/1, the Next/Last-track labels use 7).
  - scale32    : per entry X/Y float scale; retail always [1.0, 1.0].
  - text       : UTF-16LE, so accents and special glyphs work; "\n" breaks
                 lines; retail music text is exactly '"Title"\nby Artist'.
                 PS2 button glyphs usable in strings: x=U+2297, tri=U+2206,
                 o=U+25CB, sq=U+20DE, L1/L2/R1/R2 = U+2514/U+255A/U+2518/U+255D.
"""
from __future__ import annotations

import json
import re
import shutil
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from MclHash import audio_hash            # hashes the .rsm names in streams.dat
import strtbl as strtbl_mod               # decode/encode .strtbl (Edness)
strtbl_mod.exists_prompt = lambda *a, **k: True   # never prompt on overwrite

# --------------------------------------------------------------------------- #
# Game paths and constants
# --------------------------------------------------------------------------- #
RSTM_OUT     = ROOT / "RSTM output"
STREAMS_DAT  = ROOT / "streams.dat"
STREAMS_LST  = ROOT / "MC3_PS2_Streams.lst"
PLAYLIST_DIR = ROOT / "assets" / "tune" / "audio" / "playlist" / "city"
FONTS_DIR    = ROOT / "assets" / "fonts"
VGMSTREAM    = ROOT / "vgmstream-win64" / "vgmstream-cli.exe"
PROJECT_FILE = ROOT / "music_project.json"
SCRATCH      = Path(tempfile.gettempdir()) / "mc3_music_manager"

CITIES = ["atlanta", "detroit", "sd", "tokyo"]
GENRES = ["Dancehall", "Drum_N_Bass", "HipHop", "Instrumental",
          "Rock", "Techno", "Transitions", "Win_Lose"]
GENRE_RACE = {          # genre -> race playlist prefix
    "Dancehall":   "dance_hall",
    "Drum_N_Bass": "drums_bass",
    "HipHop":      "rap",
    "Rock":        "pop",
    "Techno":      "techno",
}
FRAME = 0x10

# Max length of a music stream's internal name (the .rsm stem).  MC3 reads the
# name into a fixed-size buffer; names that are too long are not recognized.
# The longest name the retail game ships is 27 chars, so cap at 27.
MAX_NAME_LEN = 27

# streams.dat size ceiling.  The stock ELF converts the stream byte-offset to a
# DVD sector with a SIGNED shift (sra >>11), so any data at/after 2 GiB reads as
# a negative sector and fails.  Patching those shifts to UNSIGNED (srl) lifts the
# ceiling to the full 32-bit offset / ISO9660 file-size range (~4 GiB):
#   VA 0x4f9d10, 0x4f9d34, 0x4f9d60, 0x4f9d8c, 0x4fa340  (byte 0xC3 -> 0xC2)
# Set ELF_PATCHED_4GB=False if you run the unpatched (2 GiB) executable.
ELF_PATCHED_4GB = True
MAX_DAT_SIZE = 0xFF000000 if ELF_PATCHED_4GB else 0x80000000   # ~3.98 GiB / 2 GiB
LIMIT_GIB = MAX_DAT_SIZE / 2**30
SECTOR = 0x800

# Default insertion targets: the two playlists that are actually used a lot.
DEFAULT_TARGETS = {"city": True, "garage": True, "race": False, "raceeditor": False}

# Retail strtbl values for music entries.
DEFAULT_FONT  = "smallspace"
DEFAULT_SIZE  = 15
DEFAULT_SCALE = [1.0, 1.0]

# Handy glyphs the game font can render inside titles (verified in retail strings).
GLYPHS = [
    ("Cross",    "⊗"), ("Triangle", "∆"), ("Circle", "○"),
    ("Square",   "⃞"), ("L1", "└"), ("L2", "╚"),
    ("R1", "┘"), ("R2", "╝"), ("(R)", "®"),
    ("deg", "°"), ("...", "…"), ("nbsp", " "),
]


def default_fmt():
    """Per-item display-text formatting block (all retail defaults)."""
    return {"custom": False, "text": "",
            "font": DEFAULT_FONT, "size": DEFAULT_SIZE,
            "scale": list(DEFAULT_SCALE)}


def list_fonts():
    """Bitmap fonts shipped with the game (assets/fonts/*.fonttex)."""
    fonts = sorted(p.stem for p in FONTS_DIR.glob("*.fonttex"))
    return fonts or [DEFAULT_FONT]


def display_text(item):
    """Resolve the strtbl text of a plan item (template or custom override)."""
    fmt = item.get("fmt") or {}
    if fmt.get("custom") and fmt.get("text", "").strip():
        return fmt["text"]
    return f'"{item["title"]}"\nby {item["artist"]}'


# --------------------------------------------------------------------------- #
# Data helpers
# --------------------------------------------------------------------------- #
def rsm_info(path_or_bytes):
    """(sample_rate, channels, data_size, duration_s) from an RSTM header."""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        h = bytes(path_or_bytes[:0x28])
    else:
        with open(path_or_bytes, "rb") as f:
            h = f.read(0x28)
    if h[:4] != b"RSTM" or len(h) < 0x1C:
        return 0, 0, 0, 0.0
    rate = struct.unpack_from("<I", h, 0x8)[0]
    ch   = struct.unpack_from("<I", h, 0xC)[0]
    size = struct.unpack_from("<I", h, 0x18)[0]
    dur = size / (FRAME * ch) * 28 / rate if rate and ch else 0.0
    return rate, ch, size, dur


def fmt_dur(sec):
    return f"{int(sec) // 60}:{int(sec) % 60:02d}" if sec else "-"


def read_hash_index(dat_path):
    """[(hash, offs, size)] from the Hash archive header."""
    with open(dat_path, "rb") as f:
        assert f.read(4) == b"Hash", "streams.dat is not a Hash archive"
        count = int.from_bytes(f.read(4), "little")
        raw = f.read(count * 0xC)
    return [struct.unpack_from("<III", raw, i * 0xC) for i in range(count)]


def parse_play(path):
    """Entries of a .play file (skips the header and blank lines)."""
    lines = path.read_text(encoding="ascii", errors="replace").splitlines()
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln or ln.lower().startswith("num_songs"):
            continue
        out.append(ln)
    return out


def write_play(path, entries):
    data = f"num_songs: {len(entries)}\r\n" + "".join(e + "\r\n" for e in entries)
    path.write_bytes(data.encode("ascii"))


def backup_once(path):
    bak = path.with_suffix(path.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(path, bak)


def sanitize_name(s, limit=MAX_NAME_LEN):
    """Internal name: ASCII, no spaces/punctuation, capped at `limit` chars."""
    s = re.sub(r"[^A-Za-z0-9_]", "", s.replace(" ", ""))
    return (s or "Track")[:limit]


def sanitize_genre(s):
    """Genre folder name: ASCII, underscores instead of spaces."""
    s = re.sub(r"[^A-Za-z0-9_]", "", s.strip().replace(" ", "_"))
    return s or "Custom"


def guess_title_artist(filename):
    """'26 My Chemical Romance - I'm Not Okay (I Promise)' -> (title, artist)."""
    stem = re.sub(r"__s\d+$", "", filename)              # strip subsong suffix
    stripped = re.sub(r"^\s*\d+[\s._-]*", "", stem)      # strip track number...
    if stripped.strip():                                 # ...unless it empties it
        stem = stripped
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return title.strip(), artist.strip()
    return stem.strip(), ""


def play_line_for(genre, name):
    return f"music\\{genre}\\{name}"


def label_for(play_line):
    return play_line.replace("\\", "_").replace("/", "_")


def targets_summary(it):
    """Short human summary of where a plan item will go."""
    if it["genre"] == "Win_Lose":
        return f"raceover ({it.get('winlose', 'win')})"
    t = it.get("targets", DEFAULT_TARGETS)
    parts = []
    if t.get("city"):       parts.append("city")
    if t.get("garage"):     parts.append("garage")
    if t.get("race"):       parts.append("race")
    if t.get("raceeditor"): parts.append("editor")
    where = "+".join(parts) if parts else "none"
    return f"{where} [{','.join(c[:3] for c in it['cities'])}]"


# --------------------------------------------------------------------------- #
# Game library
# --------------------------------------------------------------------------- #
class GameLibrary:
    """streams.dat index + music track info + in-game display names."""

    def __init__(self):
        self.dat_index = {}      # lst_name -> (hash, offs, size)
        self.game_tracks = {}    # lst_name -> dict(genre, dur, play_path, display, size)
        self.labels_json = {}    # label(lower) -> (label, text)
        self.genres = list(GENRES)

    def load(self, log=print):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        names = [ln.strip() for ln in STREAMS_LST.read_text().splitlines() if ln.strip()]
        by_hash = {audio_hash(n): n for n in names}
        self.dat_index = {}
        matched = 0
        for h, offs, size in read_hash_index(STREAMS_DAT):
            name = by_hash.get(h)
            if name:
                self.dat_index[name] = (h, offs, size)
                matched += 1
        log(f"streams.dat: {matched} named entries of {len(names)} in the .lst")

        tbl = FONTS_DIR / "mcstrings01.strtbl"
        cache = SCRATCH / "mcstrings01.json"
        if tbl.exists():
            if not cache.exists() or cache.stat().st_mtime < tbl.stat().st_mtime:
                strtbl_mod.parse_strtbl(str(tbl), str(cache))
            data = json.load(open(cache, encoding="utf-8"))["data"]
            self.labels_json = {}
            for label, langs in data.items():
                txt = langs.get("Language 00", {}).get("text", "")
                self.labels_json[label.lower()] = (label, txt)

        self.game_tracks = {}
        with open(STREAMS_DAT, "rb") as f:
            for name, (h, offs, size) in self.dat_index.items():
                if not name.lower().startswith("music/"):
                    continue
                f.seek(offs)
                rate, ch, dsize, dur = rsm_info(f.read(0x28))
                genre = name.split("/")[1]
                play_path = "music\\" + "\\".join(name.split("/")[1:]).rsplit(".", 1)[0]
                label = label_for(play_path).lower()
                disp = self.labels_json.get(label, ("", ""))[1].replace("\n", " ")
                self.game_tracks[name] = {
                    "genre": genre, "dur": dur, "play_path": play_path,
                    "display": disp, "size": size,
                }
        for info in self.game_tracks.values():
            if info["genre"] not in self.genres:
                self.genres.append(info["genre"])

    def extract_track(self, name, out_path):
        """Extract one entry from streams.dat to a file (for previewing)."""
        h, offs, size = self.dat_index[name]
        with open(STREAMS_DAT, "rb") as f:
            f.seek(offs)
            Path(out_path).write_bytes(f.read(size))


# --------------------------------------------------------------------------- #
# Plan items
# --------------------------------------------------------------------------- #
def make_item(rsm, plan, mode="add", genre="Rock", target=""):
    """Build a plan item from a converted .rsm, unique against `plan`."""
    rsm = Path(rsm)
    title, artist = guess_title_artist(rsm.stem)
    name = sanitize_name((artist.replace(" ", "") + "_" if artist else "") +
                         title.replace(" ", ""))
    if not title:                              # e.g. FlatOut 2 "01", "02"
        name = sanitize_name(rsm.stem)
        title = rsm.stem
    name = unique_name(name, plan)
    return {
        "src": str(rsm), "mode": mode, "target": target,
        "genre": genre, "name": name,
        "title": title, "artist": artist or "?",
        "cities": list(CITIES), "targets": dict(DEFAULT_TARGETS),
        "winlose": "win", "fmt": default_fmt(),
    }


def unique_name(name, plan, exclude_idx=None, limit=MAX_NAME_LEN):
    """Unique internal name among ADD items, kept within the length cap."""
    name = name[:limit]
    used = {p["name"].lower() for j, p in enumerate(plan)
            if p["mode"] == "add" and j != exclude_idx}
    if name.lower() not in used:
        return name
    i = 2
    while True:
        suffix = f"_{i}"
        cand = name[:limit - len(suffix)] + suffix
        if cand.lower() not in used:
            return cand
        i += 1


def validate_plan(plan, lib):
    """List of error strings (empty = plan is exportable)."""
    errors, seen = [], set()
    existing_lower = {n.lower() for n in lib.dat_index}
    for it in plan:
        if not Path(it["src"]).exists():
            errors.append(f"Missing file: {it['src']}")
        if it["mode"] == "add":
            if len(it["name"]) > MAX_NAME_LEN:
                errors.append(f"Name too long (>{MAX_NAME_LEN}): {it['name']}")
            arc = f"Music/{it['genre']}/{it['name']}.rsm"
            if arc.lower() in existing_lower:
                errors.append(f"Name already in game: {arc}  (use Replace)")
            if arc.lower() in seen:
                errors.append(f"Duplicate name in plan: {arc}")
            seen.add(arc.lower())
    return errors


# --------------------------------------------------------------------------- #
# streams.dat size budget (MAX_DAT_SIZE hard limit)
# --------------------------------------------------------------------------- #
def _align(v, a=SECTOR):
    return (v + a - 1) & ~(a - 1)


def _disabled_music(name, disabled, lib):
    """True if `name` is a music track currently disabled in every playlist."""
    if not name or not name.lower().startswith("music/"):
        return False
    info = lib.game_tracks.get(name)
    return bool(info) and info["play_path"].lower() in disabled


def _build_entries(plan, disabled, lib, drop_disabled):
    """Ordered [(hash, source, name)] for the new archive.
    source = ('dat', offs, size) to copy from the original, or ('file', Path)."""
    reps = {it["target"]: it for it in plan if it["mode"] == "replace"}
    adds = [it for it in plan if it["mode"] == "add"]
    names_by_hash = {audio_hash(n): n for n in lib.dat_index}
    entries = []
    for h, offs, size in read_hash_index(STREAMS_DAT):
        name = names_by_hash.get(h)
        if name and name in reps:
            entries.append((h, ("file", Path(reps[name]["src"])), name))
        elif drop_disabled and _disabled_music(name, disabled, lib):
            continue                                   # reclaim its space
        else:
            entries.append((h, ("dat", offs, size), name))
    for it in adds:
        arc = f"Music/{it['genre']}/{it['name']}.rsm"
        entries.append((audio_hash(arc), ("file", Path(it["src"])), arc))
    return entries, names_by_hash


def _entry_size(source):
    return source[2] if source[0] == "dat" else Path(source[1]).stat().st_size


def project_dat_size(plan, disabled, lib, drop_disabled=False):
    """(total_bytes, entry_count) the exported streams.dat would occupy."""
    entries, _ = _build_entries(plan, disabled, lib, drop_disabled)
    total = _align(0x8 + len(entries) * 0xC)
    for _, source, _ in entries:
        total += _align(_entry_size(source))
    return total, len(entries)


def budget_report(plan, disabled, lib):
    """dict: current projected size, whether it fits, and what dropping the
    disabled music would save.  Used by the GUI budget meter."""
    total, n = project_dat_size(plan, disabled, lib, drop_disabled=False)
    dropped, _ = project_dat_size(plan, disabled, lib, drop_disabled=True)
    return {
        "size": total,
        "size_dropped": dropped,
        "limit": MAX_DAT_SIZE,
        "over": max(0, total - MAX_DAT_SIZE),
        "over_dropped": max(0, dropped - MAX_DAT_SIZE),
        "drop_savings": total - dropped,
        "entries": n,
    }


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
def _play_targets(it, city):
    """Playlist files a plan item should be added to, in the given city."""
    mdir = PLAYLIST_DIR / city / "music"
    genre = it["genre"]
    if genre == "Win_Lose":
        suffix = "raceover_music.play" if it.get("winlose") == "win" else "raceover_lose_music.play"
        out = [mdir / f"{city}_{suffix}"]
        out += [mdir / f"{pre}_{suffix}" for pre in GENRE_RACE.values()]
        return out
    tg = it.get("targets", DEFAULT_TARGETS)
    out = []
    if tg.get("city"):
        out.append(mdir / f"{city}.play")
    if tg.get("garage"):
        out.append(mdir / "garage.play")
    if tg.get("raceeditor"):
        out.append(mdir / "raceeditor.play")
    if tg.get("race") and genre in GENRE_RACE:
        out.append(mdir / f"{GENRE_RACE[genre]}_race_music.play")
    return out


def export_plan(plan, disabled, lib, out_dir, log=print, drop_disabled=False):
    """Full export: new streams.dat + .lst, updated .play, patched .strtbl.

    drop_disabled: omit disabled music tracks' data from the archive to reclaim
    space (they are removed from every playlist anyway)."""
    out_dir = Path(out_dir)
    adds = [it for it in plan if it["mode"] == "add"]
    reps = {it["target"]: it for it in plan if it["mode"] == "replace"}

    # ---------------- 1) streams.dat ----------------
    log("1/3  Building new streams.dat ...")
    out_dat = out_dir / "streams.dat"
    out_lst = out_dir / "MC3_PS2_Streams.lst"
    if out_dat.resolve() == STREAMS_DAT.resolve():
        raise RuntimeError("Output folder would overwrite the ORIGINAL streams.dat. "
                           "Pick a different folder (e.g. a 'build' subfolder).")

    entries, names_by_hash = _build_entries(plan, disabled, lib, drop_disabled)

    seen_h = set()
    for h, source, name in entries:
        if source[0] == "file" and h in seen_h:
            raise RuntimeError(f"Hash collision with {name}!")
        seen_h.add(h)

    n = len(entries)
    data_start = _align(0x8 + n * 0xC)
    projected = data_start + sum(_align(_entry_size(src)) for _, src, _ in entries)
    if projected > MAX_DAT_SIZE:
        over = projected - MAX_DAT_SIZE
        raise RuntimeError(
            f"Exported streams.dat would be {projected/2**30:.2f} GiB, over the "
            f"{LIMIT_GIB:.2f} GiB limit the game can read.\n"
            f"Free at least {over/2**20:.0f} MiB: remove or disable tracks"
            + (", or enable 'Drop disabled music'." if not drop_disabled else "."))
    out_dir.mkdir(parents=True, exist_ok=True)
    table = []
    with open(STREAMS_DAT, "rb") as src, open(out_dat, "wb") as dst:
        dst.seek(data_start)
        done = 0
        for h, source, name in entries:
            offs = dst.tell()
            if source[0] == "dat":
                _, soffs, ssize = source
                src.seek(soffs)
                remaining = ssize
                while remaining > 0:
                    chunk = src.read(min(remaining, 8 << 20))
                    dst.write(chunk)
                    remaining -= len(chunk)
                size = ssize
            else:
                data = source[1].read_bytes()
                dst.write(data)
                size = len(data)
            table.append((h, offs, size))
            pos = dst.tell()
            if pos & 0x7FF:
                dst.seek((pos // 0x800 + 1) * 0x800)
            done += 1
            if done % 400 == 0:
                log(f"    ... {done}/{n} files copied")
        end = dst.tell()
        if end & 0x7FF:
            dst.seek(((end // 0x800 + 1) * 0x800) - 1)
            dst.write(b"\x00")
        dst.seek(0)
        dst.write(b"Hash")
        dst.write(n.to_bytes(4, "little"))
        for h, offs, size in sorted(table):
            dst.write(h.to_bytes(4, "little"))
            dst.write(offs.to_bytes(4, "little"))
            dst.write(size.to_bytes(4, "little"))

    new_names = dict(names_by_hash)
    for it in adds:
        arc = f"Music/{it['genre']}/{it['name']}.rsm"
        new_names[audio_hash(arc)] = arc
    out_lst.write_text("\n".join(new_names[h] for h, _, _ in sorted(table)
                                 if h in new_names))
    log(f"    streams.dat: {n} entries  ({out_dat.stat().st_size / 1e9:.2f} GB)")

    # ---------------- 2) .play ----------------
    log("2/3  Updating .play playlists ...")
    add_map = {}
    for it in adds:
        line = play_line_for(it["genre"], it["name"])
        for city in it["cities"]:
            for tgt in _play_targets(it, city):
                if tgt.exists():
                    add_map.setdefault(tgt, []).append(line)

    changed = 0
    for playfile in sorted(PLAYLIST_DIR.glob("*/music/*.play")):
        entries_p = parse_play(playfile)
        new = [e for e in entries_p if e.lower() not in disabled]
        for line in add_map.get(playfile, []):
            if line.lower() not in {e.lower() for e in new}:
                new.append(line)
        if new != entries_p:
            backup_once(playfile)
            write_play(playfile, new)
            changed += 1
            log(f"    {playfile.relative_to(ROOT)}  ({len(entries_p)} -> {len(new)})")
    log(f"    {changed} playlist(s) modified")

    # ---------------- 3) .strtbl ----------------
    log("3/3  Patching .strtbl ...")
    texts = {}    # label -> (text, fmt)
    for it in adds:
        texts[label_for(play_line_for(it["genre"], it["name"]))] = \
            (display_text(it), it.get("fmt") or default_fmt())
    for name, it in reps.items():
        pp = lib.game_tracks[name]["play_path"]
        texts[label_for(pp)] = (display_text(it), it.get("fmt") or default_fmt())

    for tbl in sorted(FONTS_DIR.glob("mcstrings*.strtbl")):
        tmp_json = SCRATCH / (tbl.stem + "_patch.json")
        strtbl_mod.parse_strtbl(str(tbl), str(tmp_json))
        doc = json.load(open(tmp_json, encoding="utf-8"))
        data = doc["data"]
        lower_map = {k.lower(): k for k in data}
        ref_key = next((k for k in data if k.lower().startswith("music_")), None)
        if ref_key is None:
            log(f"    {tbl.name}: no music labels, skipped")
            continue
        ref = data[ref_key]
        patched = 0
        for label, (text, fmt) in texts.items():
            key = lower_map.get(label.lower(), label)
            entry = {}
            for lang, lang_data in ref.items():
                font = dict(lang_data["font"])          # keep scale8 etc.
                font["name"] = fmt.get("font", DEFAULT_FONT)
                font["scale32"] = list(fmt.get("scale", DEFAULT_SCALE))
                if "size" in font:                      # v2 tables only
                    font["size"] = int(fmt.get("size", DEFAULT_SIZE))
                entry[lang] = {"text": text, "font": font}
            data[key] = entry
            patched += 1
        tmp_json.write_text(json.dumps(doc, indent=4, ensure_ascii=False), encoding="utf-8")
        backup_once(tbl)
        strtbl_mod.parse_json(str(tmp_json), str(tbl))
        log(f"    {tbl.name}: {patched} label(s)")


def restore_backups():
    """Copy every .bak under assets back over the modified file. Returns count."""
    n = 0
    for bak in list(PLAYLIST_DIR.glob("*/music/*.play.bak")) + list(FONTS_DIR.glob("*.strtbl.bak")):
        shutil.copy2(bak, bak.with_suffix(""))
        n += 1
    return n


# --------------------------------------------------------------------------- #
# Project persistence
# --------------------------------------------------------------------------- #
def save_project(plan, disabled, path=PROJECT_FILE):
    data = {"plan": plan, "disabled": sorted(disabled)}
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_project(path=PROJECT_FILE):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    plan = data.get("plan", [])
    for it in plan:                            # back-compat defaults
        it.setdefault("targets", dict(DEFAULT_TARGETS))
        it.setdefault("cities", list(CITIES))
        it.setdefault("winlose", "win")
        it.setdefault("fmt", default_fmt())
        for k, v in default_fmt().items():
            it["fmt"].setdefault(k, v)
    disabled = set(data.get("disabled", []))
    return plan, disabled
