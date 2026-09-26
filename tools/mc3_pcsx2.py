"""Run PCSX2 from the command line and return what the EE/IOP console said.

Closes the iteration loop without a savestate: build the file, run, read the
log, fix, repeat. Before this every round cost the user a manual boot.

WHAT WAS NEEDED, and both things were measured, not assumed:

  * THE PATH NEEDS BACKSLASHES. With 'D:/MC3HostFS/mc3boot.elf' PCSX2 answers
    "Requested filename does not exist" even with the file there; with
    'D:\\MC3HostFS\\mc3boot.elf' it loads.
  * THE EE CONSOLE STARTS OFF. In PCSX2.ini, EnableEEConsole and
    EnableIOPConsole start as false and EnableFileLogging is already true -
    turning both on, everything the EE and the IOP print lands in the log file.

What that gives for free, and it is the most valuable finding: the IOP console
logs EVERY `open`. A modloader boot produces the eight lines in order -
mc3mod.bin, the game ELF, mc3boot.ini and each .mod marked '= 1'. You can see
what the loader opened, with which flag and in which order, without taking a
savestate.

CAVEAT: mc3boot's own report does NOT show up here. It uses scr_printf, which
writes to the screen framebuffer, not printf, which goes to the console. A mod
that wants to be read by this tool has to use the console.

Paths come from environment variables, so nothing personal is baked in:

    MC3_PCSX2        path to pcsx2-qt.exe            (default: pcsx2-qt.exe on PATH)
    MC3_PCSX2_INI    path to PCSX2.ini               (optional; only for the console check)
    MC3_HOSTFS       the HostFS folder with mc3boot.elf
    MC3_PCSX2_DEBUG  data folder of a debug profile  (used by --debug)

    python mc3_pcsx2.py --elf C:\\MC3HostFS\\mc3boot.elf --seconds 40
"""
import argparse
import os
import re
import subprocess
import sys
import time

PCSX2 = os.environ.get('MC3_PCSX2', 'pcsx2-qt.exe')
INI = os.environ.get('MC3_PCSX2_INI', '')
LOG = os.path.abspath('pcsx2_cli_log.txt')
HOSTFS = os.environ.get('MC3_HOSTFS', 'MC3HostFS')

# What is worth showing out of a 600-line log.
INTERESTING = re.compile(
    r'open name|ELF Loading|Disc override|Error|error|EE DECI2|'
    r'is executing|Loaded,|failed|Exception|assert', re.I)


def console_on(path):
    """(ee, iop) - both toggles start false in a fresh PCSX2."""
    if not os.path.isfile(path):
        return None, None
    s = open(path, encoding='utf-8', errors='replace').read()
    return ('EnableEEConsole = true' in s, 'EnableIOPConsole = true' in s)


DEBUG_PROFILE = os.environ.get('MC3_PCSX2_DEBUG', '')


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--elf', default=os.path.join(HOSTFS, 'mc3boot.elf'),
                    help='path of the ELF; it NEEDS backslashes')
    ap.add_argument('--seconds', type=float, default=40.0)
    ap.add_argument('--log', default=LOG)
    ap.add_argument('--all', dest='all_', action='store_true', help='print the whole log')
    ap.add_argument('--debug', action='store_true',
                    help='use the MC3_PCSX2_DEBUG profile: pause on the first '
                         'TLB Miss, open the debugger and load the symbols of '
                         'the debug ELF. NOT suitable for measuring exceptions - '
                         'with PauseOnTLBMiss PCSX2 pauses the VM and does NOT '
                         'write the line to the log (Console.Error sits in the '
                         'else branch of vtlb.cpp), so the count comes out zero.')
    a = ap.parse_args()

    ee, iop = console_on(INI)
    if ee is False or iop is False:
        print('  WARNING: %s console off in PCSX2.ini - the log will come out silent'
              % (' and '.join([n for n, v in (('EE', ee), ('IOP', iop)) if not v])))

    if os.path.exists(a.log):
        os.remove(a.log)
    cmd = [PCSX2]
    if a.debug:
        if not DEBUG_PROFILE:
            raise SystemExit('--debug needs MC3_PCSX2_DEBUG pointing to a data folder')
        # -datapath swaps the whole data directory, so the debug profile has its
        # own PCSX2.ini without contaminating the normal one. BIOS and memory
        # cards are already absolute paths and the savestates point to the same.
        cmd += ['-datapath', DEBUG_PROFILE, '-debugger']
    else:
        cmd += ['-batch', '-nogui']
    cmd += ['-logfile', a.log, '--', a.elf]
    t0 = time.time()
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        p.wait(timeout=a.seconds)
        reason = 'exited by itself'
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        reason = 'stopped on the timeout'
    print('  %s after %.1fs' % (reason, time.time() - t0))

    if not os.path.isfile(a.log):
        raise SystemExit('no log produced at %s' % a.log)
    lines = open(a.log, encoding='utf-8', errors='replace').read().splitlines()
    print('  log: %d lines' % len(lines))
    for l in lines:
        if a.all_ or INTERESTING.search(l):
            print('   ' + l[:160])


if __name__ == '__main__':
    main()
