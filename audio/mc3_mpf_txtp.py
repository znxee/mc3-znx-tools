#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mc3_mpf_txtp.py  --  Curate EA MPF/MUS interactive music into per-track .txtp files.

The NFS .mpf files expose many subsongs through vgmstream.  Some are full songs
(2-5 min), many are tiny interactive fragments (1-4 s) the game stitches live.
This tool probes every subsong's duration and writes one .txtp per subsong that
is long enough to be a real track (>= --min-seconds), pointing at <mpf>#<n>.

The .txtp are dropped NEXT TO the .mpf, so mc3_rstm_convert.py picks them up
automatically (its .txtp path re-encodes EA-XA -> PS-ADPCM) and skips the raw
.mpf ("covered by .txtp").  Folders that already have .txtp (e.g. NFSU2, TFATF)
are left untouched -- they are already curated.

Usage:
    python mc3_mpf_txtp.py                       # all NFS mpf, keep >= 60 s
    python mc3_mpf_txtp.py --only "Most Wanted"  # one game
    python mc3_mpf_txtp.py --min-seconds 90      # stricter track threshold
    python mc3_mpf_txtp.py --all                 # every subsong (no filter)
    python mc3_mpf_txtp.py --dry-run             # just report, write nothing
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT      = Path(__file__).resolve().parent
SRC_ROOT  = ROOT / "Musics to insert"
VGMSTREAM = ROOT / "vgmstream-win64" / "vgmstream-cli.exe"


def probe_subsong(path, n):
    """Return (seconds, samples, sample_rate) for subsong n."""
    out = subprocess.run([str(VGMSTREAM), "-m", "-s", str(n), str(path)],
                         capture_output=True, text=True, errors="replace").stdout
    samples = rate = 0
    for line in out.splitlines():
        low = line.lower()
        if low.startswith("stream total samples:") or low.startswith("play duration:"):
            m = re.search(r"(\d+)\s+samples", line)
            if m:
                samples = int(m.group(1))
        elif low.startswith("sample rate:"):
            m = re.search(r"\d+", line)
            if m:
                rate = int(m.group())
    sec = samples / rate if rate else 0.0
    return sec, samples, rate


def subsong_count(path):
    out = subprocess.run([str(VGMSTREAM), "-m", str(path)],
                         capture_output=True, text=True, errors="replace").stdout
    m = re.search(r"stream count:\s*(\d+)", out)
    return int(m.group(1)) if m else 1


def curate_mpf(mpf, min_seconds, keep_all, dry_run):
    n = subsong_count(mpf)
    width = max(2, len(str(n)))
    kept, skipped = [], 0
    print(f"\n{mpf.relative_to(SRC_ROOT)}  ({n} subsongs)")
    for i in range(1, n + 1):
        sec, samples, rate = probe_subsong(mpf, i)
        if keep_all or sec >= min_seconds:
            kept.append((i, sec))
        else:
            skipped += 1
        if i % 100 == 0:
            print(f"    ...probed {i}/{n}")

    for i, sec in kept:
        txtp = mpf.with_name(f"{mpf.stem}_s{i:0{width}d}.txtp")
        if not dry_run:
            txtp.write_text(f"{mpf.name}#{i}\n", encoding="utf-8")
    mm = lambda s: f"{int(s)//60}:{int(s)%60:02d}"
    total = sum(s for _, s in kept)
    print(f"    kept {len(kept)} track(s) [{mm(total)} total], skipped {skipped} fragment(s)"
          + (" [dry-run]" if dry_run else ""))
    if kept:
        preview = ", ".join(f"s{i}={mm(s)}" for i, s in kept[:6])
        print(f"    e.g. {preview}{' ...' if len(kept) > 6 else ''}")
    return len(kept), skipped


def main():
    global SRC_ROOT
    ap = argparse.ArgumentParser(description="Generate curated .txtp for EA MPF interactive music")
    ap.add_argument("--src", default=str(SRC_ROOT))
    ap.add_argument("--only", default="", help="only folders containing this substring")
    ap.add_argument("--min-seconds", type=float, default=60.0, help="minimum duration to count as a track")
    ap.add_argument("--all", action="store_true", help="keep every subsong (no duration filter)")
    ap.add_argument("--dry-run", action="store_true", help="report only, write no .txtp")
    args = ap.parse_args()

    SRC_ROOT = Path(args.src).resolve()
    if not VGMSTREAM.exists():
        sys.exit(f"missing vgmstream-cli: {VGMSTREAM}")

    mpfs = sorted(p for p in SRC_ROOT.rglob("*") if p.suffix.lower() == ".mpf")
    total_kept = total_skip = 0
    handled = 0
    for mpf in mpfs:
        if args.only and args.only.lower() not in str(mpf.relative_to(SRC_ROOT)).lower():
            continue
        # leave already-curated folders (NFSU2/TFATF) untouched
        existing = [p for p in mpf.parent.glob("*.txtp")
                    if not p.stem.startswith(mpf.stem + "_s")]
        if existing:
            print(f"\n{mpf.relative_to(SRC_ROOT)}  -- skip (folder already has curated .txtp)")
            continue
        k, s = curate_mpf(mpf, args.min_seconds, args.all, args.dry_run)
        total_kept += k
        total_skip += s
        handled += 1

    print("\n" + "=" * 60)
    print(f"MPF handled : {handled}")
    print(f"tracks kept : {total_kept}")
    print(f"fragments   : {total_skip} (skipped)")
    if not args.dry_run and total_kept:
        print("\nNext: run  python mc3_rstm_convert.py  -- the new .txtp will be")
        print("converted (EA-XA -> PS-ADPCM) and the .mpf skipped automatically.")


if __name__ == "__main__":
    main()
