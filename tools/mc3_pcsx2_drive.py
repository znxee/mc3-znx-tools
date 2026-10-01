#!/usr/bin/env python3
"""Drive the game through its menus: boot PCSX2 and play a script of waits,
button presses, screenshots and savestates.

    python mc3_pcsx2_drive.py --out out/drive "wait 30" "shot title" \
        "press start" "wait 3" "press down 2" "press cross" "shot menu"

Steps (seconds are wall-clock):
    wait S                pause S seconds
    press BUTTON [N]      tap a pad button N times (up down left right cross
                          circle square triangle start select l1 r1)
    hold BUTTON S         keep a button down for S seconds (BUTTON may be
                          several joined by +, e.g. cross+right)
    down BUTTON / up BUTTON  press / release and keep going (shots while held)
    burst NAME N          N screen grabs as fast as possible (NAME_000.png ...)
    key KEYS [N]          tap raw keyboard keys N times (1..9, 0, backspace,
                          enter, shift, ctrl; several joined by +, e.g.
                          shift+2) - for mods that read the USB keyboard
    shot NAME             screenshot to OUT/NAME.png
    state                 savestate to slot 1 (F1) - overwrites slot 1

The pad of this setup is bound to SDL only, so for the run a keyboard binding
is ADDED to [Pad1] of PCSX2.ini next to each SDL one (PCSX2 keeps several
bindings per button as repeated keys). The .ini is copied first and put back
at the end, also on error or Ctrl+C. Keep the machine idle: keys go to the
focused window and screenshots grab the screen region (see mc3_pcsx2_shots).
"""
import argparse
import os
import shutil
import subprocess
import time

import mc3_pcsx2_shots as S

INI = os.path.join(os.environ.get('MC3_PCSX2_DATA', os.path.expanduser('~/Documents/PCSX2')), 'inis/PCSX2.ini')
# pad button -> (PCSX2 key name, Windows virtual key, extended)
KEYS = {
    'up': ('Up', 0x26, True), 'down': ('Down', 0x28, True),
    'left': ('Left', 0x25, True), 'right': ('Right', 0x27, True),
    'cross': ('K', 0x4B, False), 'circle': ('L', 0x4C, False),
    'square': ('J', 0x4A, False), 'triangle': ('I', 0x49, False),
    'start': ('Return', 0x0D, False), 'select': ('Backspace', 0x08, False),
    'l1': ('Q', 0x51, False), 'r1': ('E', 0x45, False),
}
RAW = {str(d): (0x30 + d, False) for d in range(10)}
RAW.update({'backspace': (0x08, False), 'enter': (0x0D, False), 'shift': (0x10, False),
            'ctrl': (0x11, False), 'alt': (0x12, False), 'f9': (0x78, False), 'tab': (0x09, False)})   # ctrl+alt+f9 = video capture
PAD_NAMES = {'up': 'Up', 'down': 'Down', 'left': 'Left', 'right': 'Right',
             'cross': 'Cross', 'circle': 'Circle', 'square': 'Square',
             'triangle': 'Triangle', 'start': 'Start', 'select': 'Select',
             'l1': 'L1', 'r1': 'R1'}


def add_keyboard(ini_text):
    out, section = [], None
    for line in ini_text.splitlines():
        out.append(line)
        s = line.strip()
        if s.startswith('['):
            section = s
            continue
        if section == '[Pad1]' and '=' in s:
            key = s.split('=', 1)[0].strip()
            for button, pad in PAD_NAMES.items():
                if key == pad and 'SDL-0/' in s:
                    out.append('%s = Keyboard/%s' % (pad, KEYS[button][0]))
    return '\n'.join(out) + '\n'


def key(vk, extended, down):
    flags = (1 if extended else 0) | (0 if down else 2)
    S.user32.keybd_event(vk, 0, flags, 0)


def focus(pid):
    wins = S.windows_of(pid)
    if wins:
        S.user32.SetForegroundWindow(wins[0][1])


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--elf', default=os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'mc3boot.elf'))
    ap.add_argument('--out', required=True)
    ap.add_argument('--statefile', help='start from this .p2s instead of booting (pcsx2 -statefile)')
    ap.add_argument('steps', nargs='+')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    backup = INI + '.drive_backup'
    shutil.copy2(INI, backup)
    p = None
    try:
        text = open(INI, encoding='utf-8').read()
        open(INI, 'w', encoding='utf-8').write(add_keyboard(text))
        cmd = [S.PCSX2, '-batch', '-nogui', '-logfile', S.LOG]
        if a.statefile:
            cmd += ['-statefile', a.statefile]
        cmd += ['--', a.elf]
        p = subprocess.Popen(cmd,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0 = time.time()
        for step in a.steps:
            w = step.split()
            if p.poll() is not None:
                print('  PCSX2 exited'); break
            if w[0] == 'wait':
                time.sleep(float(w[1]))
            elif w[0] in ('press', 'hold'):
                buttons = [KEYS[b] for b in w[1].split('+')]   # cross+right: together
                times = int(w[2]) if w[0] == 'press' and len(w) > 2 else 1
                for _ in range(times):
                    focus(p.pid)
                    for _name, vk, ext in buttons:
                        key(vk, ext, True)
                    time.sleep(float(w[2]) if w[0] == 'hold' else 0.15)
                    for _name, vk, ext in buttons:
                        key(vk, ext, False)
                    time.sleep(0.45)
            elif w[0] in ('down', 'up'):         # keep a button held across other steps
                focus(p.pid)
                for b in w[1].split('+'):
                    key(KEYS[b][1], KEYS[b][2], w[0] == 'down')
            elif w[0] == 'key':
                keys = [RAW[k] for k in w[1].split('+')]
                for _ in range(int(w[2]) if len(w) > 2 else 1):
                    focus(p.pid)
                    for vk, ext in keys:
                        key(vk, ext, True)
                    time.sleep(0.2)
                    for vk, ext in reversed(keys):
                        key(vk, ext, False)
                    time.sleep(0.5)
            elif w[0] == 'shot':
                path = os.path.join(a.out, w[1] + '.png')
                print('  %5.1fs  %s  %s' % (time.time() - t0, S.shoot(p.pid, path), path))
            elif w[0] == 'burst':               # burst NAME N: N grabs back to back (~15-25/s)
                wins = S.windows_of(p.pid)
                if wins:
                    rect = S.client_rect(wins[0][1])
                    frames = [S.ImageGrab.grab(bbox=rect, all_screens=True) for _ in range(int(w[2]))]
                    for i, img in enumerate(frames):
                        img.save(os.path.join(a.out, '%s_%03d.png' % (w[1], i)))
                    print('  %5.1fs  burst %s x%d' % (time.time() - t0, w[1], len(frames)))
            elif w[0] == 'state':
                print('  %5.1fs  savestate -> %s' % (time.time() - t0, S.save_state(p.pid)))
            else:
                print('  unknown step: %s' % step)
    finally:
        if p is not None and p.poll() is None:
            p.kill()
            p.wait()
        shutil.copy2(backup, INI)
        os.remove(backup)


if __name__ == '__main__':
    main()
