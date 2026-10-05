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
                          enter, shift, ctrl, f1, f2, f8, f9, tab, space;
                          several joined by +, e.g.
                          shift+2) - for mods that read the USB keyboard
    shot NAME             screenshot to OUT/NAME.png
    state                 savestate to slot 1 (F1) - overwrites slot 1

Steps that look at the menu (menu_state.mod loaded, read through PINE - see
mc3_menu_state.py). TEXT is matched case-insensitively against the selected
row (`=TEXT`: the whole row, so =ordered_race is not Unordered Race);
`row:TEXT` against every row of the active menu; `state:N` is the menu
shell's screen number (print it with `menu`). Use _ for a space
("select go_to_menus"). If a condition fails the run stops there and
prints what the menu showed.
    menu                  print the menu state
    until TEXT [S]        wait until TEXT matches (default 60 s)
    select TEXT [BUTTON] [N]  press BUTTON (default down) until the selected
                          row matches TEXT, at most N presses (default 24)
    push BUTTON TEXT [N]  press BUTTON, wait up to 3 s for TEXT, again up to N
                          times (default 10) - for screens that take input late
                          or have no list (the title: `push cross havefun`)
    expect TEXT           stop unless TEXT matches now
Screens seen (state numbers): 41 title "PRESS START", 40 profiles, 20 main
menu. --skip-fmv and --add-mod 'frontend_no_timeout.mod = defer' make the boot
fast and keep the title from falling back to the attract video.

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
import mc3_menu_state as MS

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
            'ctrl': (0x11, False), 'alt': (0x12, False), 'f1': (0x70, False), 'f2': (0x71, False), 'f8': (0x77, False), 'f9': (0x78, False), 'tab': (0x09, False), 'space': (0x20, False)})   # ctrl+alt+f9 = video capture
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


def port_in_use(port):
    import socket
    s = socket.socket()
    try:
        s.bind(('127.0.0.1', port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def set_pine(ini_text, port):
    out = []
    for line in ini_text.splitlines():
        k = line.split('=', 1)[0].strip()
        if k == 'EnablePINE':
            line = 'EnablePINE = true'
        elif k == 'PINESlot':
            line = 'PINESlot = %d' % port
        out.append(line)
    return '\n'.join(out) + '\n'


class StepFailed(Exception):
    pass


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
    ap.add_argument('--iso', help='boot this disc image instead of --elf')
    ap.add_argument('--gui', action='store_true', help='run with the main window (no -batch -nogui)')
    ap.add_argument('--disc', help='disc image to mount under --elf (as the game list does: ELF + ISO, so the GameDB fixes of the disc serial apply)')
    ap.add_argument('--no-hostfs', action='store_true',
                    help='HostFs = false for this run (a disc test: mc3boot reads host0: first)')
    ap.add_argument('--pine-port', type=int, default=None,
                    help='PINE port for this run (default 28011, or 28012 when another '
                         'PCSX2 already holds 28011)')
    ap.add_argument('--skip-fmv', action='store_true',
                    help="rename the intro videos (ASSETS/VIDEO/MC3INTRO, ROCKSTAR, SDLOGO .PSS, next "
                         "to --elf) to .PSS.off for the run - the game boots without them")
    ap.add_argument('--add-mod', action='append', default=[],
                    help="'name.mod = defer' line put in [mods] of the mc3boot.ini next to --elf for the "
                         "run (e.g. frontend_no_timeout.mod = defer, menu_state.mod = defer)")
    ap.add_argument('--boot', action='append', default=[],
                    help="KEY=VALUE put in [boot] of the mc3boot.ini next to --elf for the run (e.g. fesd=1)")
    ap.add_argument('steps', nargs='+')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    root = os.path.dirname(os.path.abspath(a.elf))
    videos = [os.path.join(root, 'ASSETS', 'VIDEO', n + '.PSS')
              for n in ('MC3INTRO', 'ROCKSTAR', 'SDLOGO')] if a.skip_fmv else []
    boot_ini = os.path.join(root, 'mc3boot.ini')
    boot_ini_text = None
    port = a.pine_port or (28012 if port_in_use(28011) else 28011)
    menu = [None]

    def state(timeout=90.0):
        """The menu state; waits for PINE and the module up to `timeout` s."""
        end = time.time() + timeout
        while True:
            if menu[0] is None:
                try:
                    menu[0] = MS.MenuState(port, timeout=3.0)
                except OSError:
                    menu[0] = None
            if menu[0] is not None:
                st = menu[0].read()
                if st is not None:
                    return st
            if time.time() > end:
                raise StepFailed('no menu state through PINE port %d (menu_state.mod loaded?)' % port)
            time.sleep(0.3)

    def settled(st, quiet=0.3, limit=3.0):
        """Read until the block stops changing for `quiet` s: a menu that
        rebuilds its list (the Arcade's city row) passes through empty and
        partial states that would fail a match."""
        end = time.time() + limit
        last, since = st.seq, time.time()
        while time.time() < end:
            time.sleep(0.05)
            st = state()
            if st.seq != last:
                last, since = st.seq, time.time()
            elif time.time() - since >= quiet:
                break
        return st

    def tap(button):
        _name, vk, ext = KEYS[button]
        focus(p.pid)
        key(vk, ext, True)
        time.sleep(0.15)
        key(vk, ext, False)
        time.sleep(0.25)

    def text_of(word):
        return word.replace('_', ' ')

    backup = INI + '.drive_backup'
    shutil.copy2(INI, backup)
    p = None
    try:
        text = open(INI, encoding='utf-8').read()
        text = add_keyboard(text)
        text = set_pine(text, port)
        if a.no_hostfs:
            text = text.replace('HostFs = true', 'HostFs = false')
        open(INI, 'w', encoding='utf-8').write(text)
        for v in videos:
            if os.path.exists(v):
                os.replace(v, v + '.off')
        if (a.add_mod or a.boot) and os.path.exists(boot_ini):
            boot_ini_text = open(boot_ini, 'rb').read()
            nl = b'\r\n' if b'\r\n' in boot_ini_text else b'\n'
            text_b = boot_ini_text
            if a.add_mod:
                k = text_b.index(b'[mods]') + len(b'[mods]') + len(nl)
                text_b = text_b[:k] + b''.join(m.encode() + nl for m in a.add_mod) + text_b[k:]
            if a.boot:
                k = text_b.index(b'[boot]') + len(b'[boot]') + len(nl)
                text_b = text_b[:k] + b''.join(
                    ('%s = %s' % tuple(kv.split('=', 1))).encode() + nl for kv in a.boot) + text_b[k:]
            open(boot_ini, 'wb').write(text_b)
        cmd = [S.PCSX2, '-logfile', S.LOG] if a.gui else [S.PCSX2, '-batch', '-nogui', '-logfile', S.LOG]
        if a.statefile:
            cmd += ['-statefile', a.statefile]
        if a.disc and not a.iso:
            cmd += ['-elf', a.elf, '--', a.disc]
        else:
            cmd += ['--', a.iso or a.elf]
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
            elif w[0] == 'menu':
                print('  %5.1fs  %s' % (time.time() - t0, str(state()).replace('\n', '\n         ')))
            elif w[0] == 'until':
                want, limit = text_of(w[1]), float(w[2]) if len(w) > 2 else 60.0
                end = time.time() + limit
                while not state(limit).matches(want):
                    if time.time() > end:
                        raise StepFailed('until %s: not after %g s\n%s' % (want, limit, state()))
                    time.sleep(0.2)
                print('  %5.1fs  until %s: ok' % (time.time() - t0, want))
            elif w[0] == 'lists':               # every list menu, for finding what is where
                state()
                for i, m, inp, vis, page, row, rows, text in menu[0].lists():
                    print('  %5.1fs  [%d] %08x input %x visible %d page %d row %d/%d %r'
                          % (time.time() - t0, i, m, inp, vis, page, row, rows, text))
            elif w[0] == 'push':                # push BUTTON TEXT [N]: press until TEXT matches
                button, want = w[1], text_of(w[2])
                limit = int(w[3]) if len(w) > 3 else 10
                st = state()
                for _ in range(limit):
                    if st.matches(want):
                        break
                    tap(button)
                    end = time.time() + 3.0
                    while time.time() < end:
                        st = state()
                        if st.matches(want):
                            break
                        time.sleep(0.1)
                if not st.matches(want):
                    raise StepFailed('push %s until %s: not after %d presses\n%s'
                                     % (button, want, limit, st))
                print('  %5.1fs  push %s until %s: ok' % (time.time() - t0, button, want))
            elif w[0] == 'expect':
                st = state()
                if not st.matches(text_of(w[1])):
                    raise StepFailed('expect %s: no\n%s' % (text_of(w[1]), st))
                print('  %5.1fs  expect %s: ok' % (time.time() - t0, text_of(w[1])))
            elif w[0] == 'select':
                want = text_of(w[1])
                button = w[2] if len(w) > 2 else 'down'
                limit = int(w[3]) if len(w) > 3 else 24
                st = state()
                for _ in range(limit):
                    if st.matches(want):
                        break
                    seq = st.seq
                    tap(button)
                    end = time.time() + 2.0
                    while time.time() < end:          # until the menu moved
                        st = state()
                        if st.seq != seq:
                            break
                        time.sleep(0.05)
                    st = settled(st)
                if not st.matches(want):
                    raise StepFailed('select %s: not found pressing %s %d times\n%s'
                                     % (want, button, limit, st))
                print('  %5.1fs  select %s: ok (%s)' % (time.time() - t0, want, st.selected))
            else:
                print('  unknown step: %s' % step)
    except StepFailed as e:
        print('  STOPPED: %s' % e)
        raise SystemExit(2)
    finally:
        if menu[0] is not None:
            menu[0].close()
        if p is not None and p.poll() is None:
            p.kill()
            p.wait()
        shutil.copy2(backup, INI)
        os.remove(backup)
        if boot_ini_text is not None:
            open(boot_ini, 'wb').write(boot_ini_text)
        for v in videos:
            if os.path.exists(v + '.off') and not os.path.exists(v):
                os.replace(v + '.off', v)


if __name__ == '__main__':
    main()
