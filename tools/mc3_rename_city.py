#!/usr/bin/env python3
"""Rename an added MC3 city everywhere in the HostFS tree (paths and text).

The MC3 name of a city (what the registry mod writes into the slot: sd,
atlanta, paris, ...) is the key every asset path is built from:
city/<name>/, resources/city/<name>_<tod>_<weather>.pck, tune/race/<name>.loc,
tune/effects/<name>_*, tune/audio/playlist/city/<name>/, texture/hud_map_<name>
and so on, and the race files name it inside (`City <name>`, race paths
<name>_cruise_race01). None of the binary .pck/.ppf store it. So a rename is:

  1. every file and directory under ASSETS whose name holds OLD -> NEW
     (deepest first), and the root <old>_*.mod files;
  2. OLD -> NEW inside every TEXT file under ASSETS that holds it (the string
     tables are binary and are done by --strtbl with Edness strtbl.py);
  3. mc3boot.ini: the [mods] lines of the renamed .mod files and `city = OLD`.

The registry mod itself (city_paris_slot7) must be rebuilt with NEW.

    python mc3_rename_city.py --old OLD --new NEW --dry-run
    python mc3_rename_city.py --old OLD --new NEW
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile


def is_text(data):
    if b'\0' in data[:4096]:
        return False
    try:
        data.decode('latin1')
    except UnicodeDecodeError:
        return False
    printable = sum(1 for b in data[:4096] if 32 <= b < 127 or b in (9, 10, 13))
    return printable >= 0.95 * min(len(data), 4096)


def rename_paths(root, old, new, dry):
    moved = 0
    for here, dirs, files in os.walk(root, topdown=False):
        for name in files + dirs:
            if old not in name.lower():
                continue
            src = os.path.join(here, name)
            dst = os.path.join(here, re.sub(re.escape(old), new, name, flags=re.I))
            if os.path.exists(dst):
                raise SystemExit('refusing: %s already exists' % dst)
            if not dry:
                os.rename(src, dst)
            moved += 1
    return moved


def rewrite_text(root, old, new, dry):
    changed = []
    for here, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(here, name)
            if name.lower().endswith('.strtbl'):
                continue
            with open(path, 'rb') as fh:
                data = fh.read()
            if old.encode() not in data or not is_text(data):
                continue
            if not dry:
                with open(path, 'wb') as fh:
                    fh.write(data.replace(old.encode(), new.encode()))
            changed.append(path)
    return changed


def rename_strtbl(fonts, tool, old, new, dry):
    """Keys and texts holding OLD (CM_<old>, <old>, race paths) -> NEW."""
    for name in ('mcstrings01', 'mcstrings01_pal'):
        path = os.path.join(fonts, name + '.strtbl')
        if not os.path.exists(path):
            continue
        work = tempfile.mkdtemp()
        shutil.copy2(path, os.path.join(work, name + '.strtbl'))
        subprocess.run([sys.executable, tool, 'dec', name + '.strtbl'], cwd=work, check=True,
                       stdout=subprocess.DEVNULL)
        jpath = os.path.join(work, name + '.json')
        j = json.load(open(jpath, encoding='utf-8'))
        out, renamed = {}, 0
        for key, value in j['data'].items():
            nk = key.replace(old, new)
            if nk != key:
                renamed += 1
                if nk in j['data']:
                    raise SystemExit('refusing: %s already has %s' % (name, nk))
            out[nk] = value
        j['data'] = out
        print('  %s: %d keys renamed' % (name, renamed))
        if dry:
            continue
        json.dump(j, open(jpath, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        os.remove(os.path.join(work, name + '.strtbl'))
        subprocess.run([sys.executable, tool, 'enc', name + '.json'], cwd=work, check=True,
                       stdout=subprocess.DEVNULL)
        shutil.copy2(os.path.join(work, name + '.strtbl'), path)


def rewrite_ini(ini, old, new, dry):
    data = open(ini, 'rb').read()
    text = data.decode('latin1')
    lines, n = [], 0
    for line in text.split('\n'):
        s = line.strip().lower()
        if s.startswith(old + '_') and '.mod' in s:
            line = line.replace(old, new, 1)
            n += 1
        elif re.match(r'city\s*=\s*%s\s*$' % re.escape(old), s):
            line = line.replace(old, new)
            n += 1
        lines.append(line)
    if not dry:
        open(ini, 'wb').write('\n'.join(lines).encode('latin1'))
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--hostfs', default=os.environ.get('MC3_HOSTFS', os.environ.get('MC3_HOSTFS', 'MC3HostFS')))
    ap.add_argument('--old', required=True)
    ap.add_argument('--new', required=True)
    ap.add_argument('--strtbl', default=os.environ.get('MC3_STRTBL', 'strtbl.py'))
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    assets = os.path.join(a.hostfs, 'ASSETS')
    changed = rewrite_text(assets, a.old, a.new, a.dry_run)
    print('text files rewritten: %d' % len(changed))
    for p in changed:
        print('  ' + os.path.relpath(p, a.hostfs))
    print('paths renamed under ASSETS: %d' % rename_paths(assets, a.old, a.new, a.dry_run))
    mods = [f for f in os.listdir(a.hostfs) if f.lower().startswith(a.old + '_') and f.endswith('.mod')]
    for f in mods:
        dst = os.path.join(a.hostfs, f.replace(a.old, a.new, 1))
        if os.path.exists(dst):
            raise SystemExit('refusing: %s already exists' % dst)
        if not a.dry_run:
            os.rename(os.path.join(a.hostfs, f), dst)
    print('root .mod renamed: %s' % ', '.join(mods))
    print('mc3boot.ini lines: %d' % rewrite_ini(os.path.join(a.hostfs, 'mc3boot.ini'),
                                               a.old, a.new, a.dry_run))
    rename_strtbl(os.path.join(assets, 'fonts'), a.strtbl, a.old, a.new, a.dry_run)


if __name__ == '__main__':
    main()
