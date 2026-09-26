#!/bin/sh
# run_probe.sh <mod-name> <seconds> <outdir> [extra [boot] lines...]
# Boots PCSX2 with a MINIMAL mc3boot.ini (core + one probe mod), screenshots it,
# then restores the shared .ini no matter what.
MOD="$1"; SEC="$2"; OUT="$3"; shift 3
INI="$MC3_HOSTFS/mc3boot.ini"
cp "$INI" "$INI.probe_bak"
trap 'cp "$INI.probe_bak" "$INI"; rm -f "$INI.probe_bak"' EXIT
{ printf '[mods]\ncore.mod = bootstrap\n%s.mod = shim\n\n[boot]\n' "$MOD"; for l in "$@"; do printf '%s\n' "$l"; done; } > "$INI"
cd "$(dirname "$0")" && python mc3_pcsx2_shots.py --at "$SEC" --out "$OUT"
