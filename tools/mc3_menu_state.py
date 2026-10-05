#!/usr/bin/env python3
"""Read what the MC3 front end shows, through PCSX2's PINE - the screen
(menu shell state number), the active menu's rows and which one the cursor is
on - from the block the menu_state.mod module keeps (registry id 'MNUS', see
its source).

    python mc3_menu_state.py            # print the current state once
    python mc3_menu_state.py --watch    # print every change (Ctrl+C stops)

PCSX2 needs EnablePINE = true (PINESlot 28011) and no other PINE client: the
server serves one client at a time (ReShade's telemetry add-on used to hold it).

As a library (mc3_pcsx2_drive.py's conditional steps use it):

    m = MenuState()
    s = m.read()        # None until the module has published its block
    s.state, s.selected, s.rows, s.row
"""
import argparse
import socket
import struct
import time

REG_HDR = 0x0061D050
REG_MAGIC = 0x4D433353          # 'MC3S'
MNUS = 0x4D4E5553               # 'MNUS'
READ32, READ64 = 0x02, 0x03
BLOCK = 88 + 24 * 32            # size of menu_state's block (version 3)


class PineError(Exception):
    pass


class Pine:
    """One socket, kept: the server serves a single client and a client that
    reconnects leaves it stuck on the old one (see mc3_savestate.py)."""

    def __init__(self, port=28011, timeout=5.0):
        self.s = socket.create_connection(('127.0.0.1', port), timeout=timeout)
        self.s.settimeout(timeout)

    def _recv(self, n):
        out = b''
        while len(out) < n:
            c = self.s.recv(n - len(out))
            if not c:
                raise PineError('PINE closed the connection')
            out += c
        return out

    def batch(self, body, reply_size):
        self.s.sendall(struct.pack('<I', 4 + len(body)) + body)
        total = struct.unpack('<I', self._recv(4))[0]
        rest = self._recv(total - 4)
        if not rest or rest[0] != 0:
            raise PineError('PINE refused (VM not running?)')
        return rest[1:1 + reply_size]

    def read32(self, addr):
        return struct.unpack('<I', self.batch(struct.pack('<BI', READ32, addr), 4))[0]

    def read(self, addr, size):
        """`size` bytes from `addr` (8-aligned), in one message of 64-bit reads."""
        n = (size + 7) // 8
        body = b''.join(struct.pack('<BI', READ64, addr + 8 * i) for i in range(n))
        return self.batch(body, 8 * n)[:size]

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


class State:
    def __init__(self, raw):
        u = lambda o: struct.unpack_from('<I', raw, o)[0]
        txt = lambda o, n: raw[o:o + n].split(b'\0')[0].decode('ascii', 'replace')
        self.version, self.seq, self.frames = u(4), u(8), u(12)
        self.menu, self.page, self.row, self.count = u(16), u(20), u(24), u(28)
        self.state, self.box = u(32), u(36)
        self.selected = txt(40, 48)
        self.rows = [txt(88 + 32 * i, 32) for i in range(min(self.count, 24))]

    def __str__(self):
        lines = ['state %d  %s %08x  row %d of %d  seq %d' % (
            self.state, 'box' if self.box else 'menu', self.menu, self.row, self.count, self.seq)]
        for i, r in enumerate(self.rows):
            lines.append('  %s %s' % ('>' if i == self.row else ' ', r))
        return '\n'.join(lines)

    def matches(self, text):
        """Case-insensitive: `text` in the selected row (`=text`: the whole row);
        `state:N` = the shell
        state; `row:TEXT` = some row of the active menu holds TEXT."""
        t = text.lower()
        if t.startswith('='):
            return self.selected.lower().strip() == t[1:].strip()
        if t.startswith('state:'):
            return str(self.state) == t[6:]
        if t.startswith('row:'):
            return any(t[4:] in r.lower() for r in self.rows)
        return t in self.selected.lower()


class MenuState:
    def __init__(self, port=28011, timeout=5.0):
        self.pine = Pine(port, timeout)
        self.addr = None

    def locate(self):
        p = self.pine
        if p.read32(REG_HDR) != REG_MAGIC:
            return None
        base, cap = p.read32(REG_HDR + 28), p.read32(REG_HDR + 32)
        if not base or not cap or cap > 1024:
            return None
        table = p.read(base, 8 * cap)
        for i in range(cap):
            ident, addr = struct.unpack_from('<II', table, 8 * i)
            if ident == MNUS:
                return addr
        return None

    def read(self):
        """The current State, or None (VM not running yet, module not loaded)."""
        try:
            return self._read()
        except (PineError, OSError):
            return None

    def _read(self):
        if self.addr is None:
            self.addr = self.locate()
            if self.addr is None:
                return None
        base = self.addr & ~7
        raw = self.pine.read(base, BLOCK + 8)[self.addr - base:]
        if struct.unpack_from('<I', raw, 0)[0] != MNUS:
            self.addr = None
            return None
        return State(raw)

    def lists(self):
        """Every list menu among the menu widgets (a diagnostic, read straight
        from the game): [(index, address, has input, visible, page, row, rows,
        selected text)]. The active one is index 60 (widgets + 240)."""
        p = self.pine
        ok = lambda a: 0x100000 <= a < 0x2000000 and not a & 3
        out = []
        try:
            holder = p.read32(0x617ADC)
            widgets = p.read32(holder + 12) if ok(holder) else 0
            if not ok(widgets):
                return out
            table = p.read(widgets, 4 * 100)
            for i in range(100):
                m = struct.unpack_from('<I', table, 4 * i)[0]
                if not ok(m):
                    continue
                vt = p.read32(m)
                if not ok(vt) or p.read32(vt + 52) != 0x5879F0:
                    continue
                f = p.read(m + 656, 128)
                u = lambda o: struct.unpack_from('<I', f, o - 656)[0]
                page = u(704)
                if page >= 8:
                    continue
                rows, row = u(668 + 4 * page), u(720 + 16 * page)
                text = ''
                if rows and row < rows:
                    cols, ncols, rr, rpp = u(680), u(664), u(684), u(660)
                    items = p.read32(rr + 16 * (page * rpp + row) + 8)
                    for c in range(min(ncols, 8)):
                        if p.read32(cols + 12 * c) & 0xFFFF:
                            a = p.read32(items + 8 * c)
                            if ok(a & ~3):
                                raw = p.read(a & ~7, 72)[a - (a & ~7):]
                                for k in range(0, 64, 2):
                                    ch = struct.unpack_from('<H', raw, k)[0]
                                    if not ch:
                                        break
                                    text += chr(ch) if 32 <= ch < 127 else '?'
                            break
                out.append((i, m, p.read32(m + 248), p.read32(m + 68) & 1, page, row, rows, text))
        except (PineError, OSError):
            pass
        return out

    def close(self):
        self.pine.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--watch', action='store_true', help='print every change')
    ap.add_argument('--port', type=int, default=28011)
    a = ap.parse_args()
    m = MenuState(a.port)
    try:
        last = None
        while True:
            s = m.read()
            if s is None:
                if not a.watch:
                    print('menu_state block not found (module not loaded yet?)')
                    return 1
            elif not a.watch or s.seq != last:
                print(s, flush=True)
                if a.watch:
                    print(flush=True)
                last = s.seq
            if not a.watch:
                return 0
            time.sleep(0.1)
    except KeyboardInterrupt:
        return 0
    finally:
        m.close()


if __name__ == '__main__':
    raise SystemExit(main())
