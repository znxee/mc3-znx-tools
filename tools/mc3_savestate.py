"""Take a PCSX2 savestate unattended, through PINE.

Rewritten after a first version failed: the mistake was REOPENING the socket on
every attempt. The PINE server (pcsx2/PINE.cpp, MainLoop) serves ONE client at
a time - accept, ClientLoop, close - so a client that drops its socket and opens
another leaves the server stuck reading the first one, and every following
connection waits in the backlog until it times out. The fix, taken from
mc3_pckbuild.py: ONE socket, kept; on IPC_FAIL (the VM is not up yet) carry on
on the SAME socket; only reconnect on a real I/O error.

Launched the same way as mc3_pckbuild: `-elf <elf> -fastboot -nogui <ISO>` - the
positional argument is the DISC (the game reads assets off the DVD), -elf only
chooses what to execute.

Paths come from the environment: MC3_PCSX2 (pcsx2-qt.exe), MC3_HOSTFS (the
folder with mc3boot.elf and the stripped ISO) and MC3_PCSX2_DATA (the PCSX2 data
folder holding sstates/).

    python mc3_savestate.py --seconds 50 --slot 3
"""
import argparse
import glob
import os
import socket
import struct
import subprocess
import time

PCSX2 = os.environ.get('MC3_PCSX2', 'pcsx2-qt.exe')
HOSTFS = os.environ.get('MC3_HOSTFS', 'MC3HostFS')
ELF = os.path.join(HOSTFS, 'mc3boot.elf')
ISO = os.path.join(HOSTFS, 'MC3Stripped.iso')
SSTATES = os.path.join(os.environ.get('MC3_PCSX2_DATA', os.path.expanduser('~/Documents/PCSX2')),
                       'sstates')
READ32, SAVESTATE = 0x02, 0x09


class Pine:
    def __init__(self, port=28011, timeout=5.0):
        self.s = socket.create_connection(('127.0.0.1', port), timeout=timeout)
        self.s.settimeout(timeout)

    def _call(self, body, reply_extra):
        self.s.sendall(struct.pack('<I', 4 + len(body)) + body)
        total = struct.unpack('<I', self._recv(4))[0]
        rest = self._recv(total - 4)
        if not rest or rest[0] != 0:
            raise NotReady(rest[0] if rest else -1)
        return rest[1:1 + reply_extra]

    def _recv(self, n):
        out = b''
        while len(out) < n:
            c = self.s.recv(n - len(out))
            if not c:
                raise IOError('PINE closed')
            out += c
        return out

    def read32(self, addr):
        return struct.unpack('<I', self._call(struct.pack('<BI', READ32, addr), 4))[0]

    def savestate(self, slot):
        self._call(struct.pack('<BB', SAVESTATE, slot & 0xFF), 0)

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


class NotReady(Exception):
    pass


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--seconds', type=float, default=50.0)
    ap.add_argument('--slot', type=int, default=3)
    ap.add_argument('--elf', default=ELF)
    ap.add_argument('--timeout', type=float, default=140.0)
    a = ap.parse_args()

    before = {f: os.path.getmtime(f) for f in glob.glob(os.path.join(SSTATES, '*.p2s'))}
    cmd = [PCSX2, '-elf', a.elf, '-fastboot', '-nogui']
    if os.path.isfile(ISO):
        cmd.append(ISO)
    proc = subprocess.Popen(cmd)
    print('  PCSX2 starting; initial wait %.0fs' % a.seconds)
    time.sleep(a.seconds)

    pine, saved = None, False
    limit = time.time() + a.timeout
    try:
        while time.time() < limit:
            if proc.poll() is not None:
                raise SystemExit('  the emulator exited early (%s)' % proc.returncode)
            if pine is None:
                try:
                    pine = Pine()
                    print('  PINE connected')
                except OSError:
                    time.sleep(2)
                    continue
            try:
                pine.read32(0x00100000)     # only confirms the VM answers
            except NotReady:
                time.sleep(2)               # VM not up yet; SAME socket
                continue
            except (IOError, OSError):
                pine = None                 # a real error: reconnect
                continue
            # the VM answers: save
            try:
                pine.savestate(a.slot)
                saved = True
                print('  savestate triggered in slot %d' % a.slot)
                break
            except NotReady as e:
                print('  savestate refused (result %s), trying again' % e)
                time.sleep(2)
        if not saved:
            raise SystemExit('  timed out without saving')
        # saving is asynchronous; wait for the file
        for _ in range(30):
            time.sleep(0.5)
            for f in glob.glob(os.path.join(SSTATES, '*.p2s')):
                if os.path.getmtime(f) > before.get(f, 0) + 0.1:
                    print('  SAVESTATE: %s (%d bytes)' % (f, os.path.getsize(f)))
                    return
        print('  it saved but no new file was seen (check %s)' % SSTATES)
    finally:
        if pine:
            pine.close()
        proc.kill()
        proc.wait()


if __name__ == '__main__':
    main()
