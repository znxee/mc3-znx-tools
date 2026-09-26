"""Boot PCSX2 headless and screenshot its render window at fixed times.

mc3_pcsx2.py answers "what did the console say". This answers "what was on
screen" - the only way to tell a hang from a long load or a quiet race, since
the game prints nothing once it is past boot.

The capture is the SCREEN region under the window, not the window's own DC:
D3D12 swap chains come back black through PrintWindow/BitBlt of the window, so
the window is raised and its rectangle grabbed from the desktop instead. It
must therefore not be covered - keep the machine idle while this runs.

    python mc3_pcsx2_shots.py --at 10 20 40 60 --out shots/nofe

PINE is not used: pcsx2-qt v1.7 dev with -nogui keeps its own PINE client
connected and serves one client at a time (see memory mc3-savestate-diag).
A savestate is taken instead by pressing the SaveStateToSlot hotkey (F1) into
the focused window - `--state-at 60` - which gives the whole of EE RAM at that
instant. It OVERWRITES slot 1 of whatever ELF is booted; back it up first.
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import time

from PIL import ImageGrab

PCSX2 = os.environ.get('MC3_PCSX2', 'pcsx2-qt.exe')
LOG = os.path.join(os.environ.get('MC3_PCSX2_DATA', os.path.expanduser('~/Documents/PCSX2')),
                   'logs', 'cli_test.txt')

user32 = ctypes.windll.user32
user32.SetProcessDPIAware()


def windows_of(pid):
    """Visible top-level windows owned by pid, largest first."""
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            area = (r.right - r.left) * (r.bottom - r.top)
            if area > 0:
                found.append((area, hwnd, (r.left, r.top, r.right, r.bottom)))
        return True

    user32.EnumWindows(cb, 0)
    found.sort(reverse=True)
    return found


def client_rect(hwnd):
    """The client area in screen coordinates - the picture, without borders."""
    r = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    tl = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(tl))
    return (tl.x, tl.y, tl.x + r.right, tl.y + r.bottom)


def shoot(pid, path):
    wins = windows_of(pid)
    if not wins:
        return 'no window yet'
    hwnd = wins[0][1]
    user32.ShowWindow(hwnd, 9)          # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.4)                     # let the compositor put it on top
    img = ImageGrab.grab(bbox=client_rect(hwnd), all_screens=True)
    img.save(path)
    return '%dx%d' % img.size


VK_F1 = 0x70
SSTATES = os.path.join(os.environ.get('MC3_PCSX2_DATA', 'PCSX2'), 'sstates')


def newest_state():
    files = [os.path.join(SSTATES, f) for f in os.listdir(SSTATES) if f.endswith('.p2s')]
    return max(files, key=os.path.getmtime) if files else None


def save_state(pid, timeout=30.0):
    """Press F1 into the PCSX2 window and wait for a fresh .p2s to finish."""
    wins = windows_of(pid)
    if not wins:
        return 'no window'
    before = time.time()
    user32.SetForegroundWindow(wins[0][1])
    time.sleep(0.3)
    user32.keybd_event(VK_F1, 0, 0, 0)
    time.sleep(0.1)
    user32.keybd_event(VK_F1, 0, 2, 0)          # KEYEVENTF_KEYUP
    deadline = before + timeout
    last_size = -1
    while time.time() < deadline:
        time.sleep(1.0)
        p = newest_state()
        if p and os.path.getmtime(p) >= before:
            size = os.path.getsize(p)
            if size == last_size and size > 0:  # stopped growing: written
                return p
            last_size = size
    return 'no savestate appeared'


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--elf', default=os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'mc3boot.elf'))
    ap.add_argument('--at', type=float, nargs='+', required=True,
                    help='seconds after launch to take each screenshot')
    ap.add_argument('--out', required=True, help='directory for the PNGs')
    ap.add_argument('--state-at', type=float,
                    help='also press F1 (savestate to slot) at this second')
    ap.add_argument('--statefile',
                    help='start from this .p2s instead of booting (pcsx2 -statefile)')
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    cmd = [PCSX2, '-batch', '-nogui', '-logfile', LOG]
    if a.statefile:
        cmd += ['-statefile', a.statefile]
    cmd += ['--', a.elf]
    t0 = time.time()
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    events = [(t, 'shot') for t in a.at]
    if a.state_at is not None:
        events.append((a.state_at, 'state'))
    try:
        for t, kind in sorted(events):
            delay = t0 + t - time.time()
            if delay > 0:
                time.sleep(delay)
            if p.poll() is not None:
                print('  PCSX2 exited at %.1fs' % (time.time() - t0))
                break
            if kind == 'state':
                print('  %5.1fs  savestate -> %s' % (time.time() - t0, save_state(p.pid)))
                continue
            path = os.path.join(a.out, 't%03d.png' % int(t))
            print('  %5.1fs  %s  %s' % (time.time() - t0, shoot(p.pid, path), path))
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()


if __name__ == '__main__':
    main()
