#!/usr/bin/env python3
"""Switch PCSX2 to the profile closest to a real PS2, and back.

    python mc3_pcsx2_accurate.py on       keeps PCSX2.ini.before_accurate first
    python mc3_pcsx2_accurate.py off      puts that copy back
    python mc3_pcsx2_accurate.py status

Why: PCSX2's recompiler meets an access to an unmapped address (a TLB miss)
by logging "TLB Miss, pc=... addr=..." and reading zero, so the game goes on;
the console takes the exception and stops. Its interpreter does what the
console does (vtlb_Miss: Cpu == &intCpu raises cpuTlbMissR/W). Measured on
this project: LA and Paris froze on their loading screen on a real PS2 (OPL
over SMB) and under this profile, never under the default one - the city
sound-bank table had 4 entries and cities 5/6 read past it.

The profile:
    [EmuCore/CPU/Recompiler]  EE, IOP, VU0, VU1 interpreted; EE cache
                              emulated (data cache: catches cached writes
                              that never reach RAM before a DMA); no fastmem
    [EmuCore/Speedhacks]      normal EE cycles, no MTVU, no instant VU1, no
                              INTC/wait-loop skipping, no fast CDVD

It runs at 5-9% of full speed. The way through: boot and walk the menus with
the normal profile, take a savestate, switch this on and load the state with
mc3_pcsx2_drive.py --statefile (press buttons with `hold`, not `press`: at
this speed a short tap is missed). Booting mc3boot from HostFS under the
interpreter stops on the loader screen ("entering 001A0008"); use a disc
image. A quicker check with the recompiler: PauseOnTLBMiss = true in
[EmuCore/CPU/Recompiler] stops on the first TLB miss instead of reading zero.

The .ini is PCSX2's own (MC3_PCSX2_DATA, default ~/Documents/PCSX2).
Do not run two PCSX2 drivers at once: they share one backup file.
"""
import os
import re
import shutil
import sys

DATA = os.environ.get('MC3_PCSX2_DATA', os.path.expanduser('~/Documents/PCSX2'))
INI = os.path.join(DATA, 'inis', 'PCSX2.ini')
KEEP = INI + '.before_accurate'

SETTINGS = {
    'EmuCore/CPU/Recompiler': {
        'EnableEE': 'false', 'EnableIOP': 'false', 'EnableVU0': 'false', 'EnableVU1': 'false',
        'EnableEECache': 'true', 'EnableFastmem': 'false',
    },
    'EmuCore/Speedhacks': {
        'EECycleRate': '0', 'EECycleSkip': '0', 'fastCDVD': 'false', 'IntcStat': 'false',
        'WaitLoop': 'false', 'vuFlagHack': 'false', 'vuThread': 'false', 'vu1Instant': 'false',
    },
}


def apply(text):
    out, section = [], None
    seen = {s: set() for s in SETTINGS}

    def missing():
        if section in SETTINGS:
            for k, v in SETTINGS[section].items():
                if k not in seen[section]:
                    out.append('%s = %s' % (k, v))

    for line in text.splitlines():
        m = re.match(r'\s*\[(.+)\]\s*$', line)
        if m:
            missing()
            section = m.group(1)
            out.append(line)
            continue
        m = re.match(r'\s*([^=\s]+)\s*=', line)
        if section in SETTINGS and m and m.group(1) in SETTINGS[section]:
            k = m.group(1)
            out.append('%s = %s' % (k, SETTINGS[section][k]))
            seen[section].add(k)
            continue
        out.append(line)
    missing()
    return '\n'.join(out) + '\n'


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if what == 'on':
        if not os.path.exists(KEEP):
            shutil.copy2(INI, KEEP)
        text = open(INI, encoding='utf-8').read()
        open(INI, 'w', encoding='utf-8').write(apply(text))
        print('accurate profile on (original kept in %s)' % KEEP)
    elif what == 'off':
        if not os.path.exists(KEEP):
            raise SystemExit('no %s: nothing to put back' % KEEP)
        shutil.copy2(KEEP, INI)
        os.remove(KEEP)
        print('original PCSX2.ini back')
    else:
        print('accurate profile %s' % ('ON' if os.path.exists(KEEP) else 'off'))


if __name__ == '__main__':
    main()
