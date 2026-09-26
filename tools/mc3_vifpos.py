"""Strict position decoder for MC3 PS2 city VIF packets.

The position stream is selected by the short-packet header and its VU address,
not by taking the first V3 UNPACK.  The latter is often a normal/attribute
stream and produces plausible vertex counts with completely false AABBs.
"""

import math
import struct


def _s8(v):
    return v - 0x100 if v & 0x80 else v


def _s32(v):
    return v - 0x100000000 if v & 0x80000000 else v


def _unpack_size(cmd, num):
    n = num or 256
    cols = ((cmd >> 2) & 3) + 1
    vl = cmd & 3
    size = n * 2 if vl == 3 else n * cols * (4 >> vl)
    return (size + 3) & ~3


def _commands(data):
    off = 0
    mode = 0
    cl = wl = 1
    row = (0, 0, 0, 0)
    while off < len(data):
        if off + 4 > len(data):
            raise ValueError('partial VIF command at %#x' % off)
        cmd_off = off
        word = struct.unpack_from('<I', data, off)[0]
        off += 4
        cmd = (word >> 24) & 0x7F
        num = (word >> 16) & 0xFF
        imm = word & 0xFFFF
        size = 0
        before = row
        if cmd in (0, 2, 3, 4, 6, 7, 0x10, 0x11, 0x13,
                   0x14, 0x15, 0x17):
            pass
        elif cmd == 1:
            cl = (imm & 0xFF) or 256
            wl = ((imm >> 8) & 0xFF) or 256
            if wl > cl:
                raise ValueError('STCYCL fill unsupported at %#x' % cmd_off)
        elif cmd == 5:
            mode = imm & 3
        elif cmd == 0x20:
            size = 4
        elif cmd in (0x30, 0x31):
            size = 16
        elif cmd == 0x4A:
            size = (num or 256) * 8
        elif cmd in (0x50, 0x51):
            size = (imm or 65536) * 16
        elif 0x60 <= cmd <= 0x7F:
            size = _unpack_size(cmd, num)
        else:
            raise ValueError('unknown VIF command %#x at %#x' % (cmd, cmd_off))
        if off + size > len(data):
            raise ValueError('VIF payload beyond packet at %#x' % cmd_off)
        yield {'off': cmd_off, 'payload': off, 'cmd': cmd, 'num': num,
               'imm': imm, 'size': size, 'mode': mode, 'row': before,
               'cl': cl, 'wl': wl}
        if cmd == 0x30:
            row = tuple(_s32(struct.unpack_from('<I', data, off + 4*k)[0])
                        for k in range(4))
        off += size


def _header(c, data):
    if not (c['cmd'] == 0x60 and (c['num'] or 256) == 2
            and (c['imm'] & 0x4000)):
        return None
    scale = struct.unpack_from('<f', data, c['payload'])[0]
    count = struct.unpack_from('<I', data, c['payload'] + 4)[0]
    if not math.isfinite(scale) or not (0.0 < scale <= 1.0):
        return None
    if not (0 < count <= 255):
        return None
    return c['imm'] & 0x3FF, scale, count


def _is_position(c, base, count):
    if not (0x60 <= c['cmd'] <= 0x7F):
        return False
    base_cmd = c['cmd'] & ~0x10
    if ((base_cmd >> 2) & 3) + 1 != 3:
        return False
    start = (base + 0x62) & 0x3FF
    addr = c['imm'] & 0x3FF
    n = c['num'] or 256
    return start <= addr < start + count and addr + n <= start + count


def decode_batches(data):
    """Return one list of dequantized XYZ tuples per short-packet chain."""
    batches = []
    current = []
    base = scale = count = None
    rows = 0
    row = [0, 0, 0, 0]

    def close():
        if count is None:
            return
        if rows != count:
            raise ValueError('position count %d != header %d' % (rows, count))
        batches.append(list(current))

    for c in _commands(data):
        h = _header(c, data)
        if h is not None:
            close()
            base, scale, count = h
            current = []
            rows = 0
            continue
        if c['cmd'] == 0x30:
            row = list(c['row'])
            row = [_s32(struct.unpack_from('<I', data, c['payload'] + 4*k)[0])
                   for k in range(4)]
            continue
        if base is None or not _is_position(c, base, count):
            continue
        if c['cmd'] & 0x10:
            raise ValueError('masked position UNPACK at %#x' % c['off'])
        cmd = c['cmd']
        n = c['num'] or 256
        p = c['payload']
        vals = []
        if cmd == 0x68:
            vals = [tuple(_s32(struct.unpack_from('<I', data,
                                                  p + 12*r + 4*k)[0])
                          for k in range(3)) for r in range(n)]
        elif cmd == 0x69:
            vals = [struct.unpack_from('<3h', data, p + 6*r) for r in range(n)]
        elif cmd == 0x6A:
            for r in range(n):
                raw = tuple(_s8(data[p + 3*r + k]) for k in range(3))
                if c['mode'] == 2:
                    prev = row if r == 0 else vals[-1]
                    vals.append(tuple(prev[k] + raw[k] for k in range(3)))
                elif c['mode'] == 0:
                    vals.append(raw)
                else:
                    raise ValueError('unsupported STMOD %d at %#x' %
                                     (c['mode'], c['off']))
        else:
            raise ValueError('unsupported position UNPACK %#x' % cmd)
        current.extend(tuple(v * scale for v in xyz) for xyz in vals)
        rows += len(vals)
        if vals and c['mode'] == 2:
            row[:3] = vals[-1]
    close()
    if not batches:
        raise ValueError('short packet header not found')
    return batches


def decode_positions(data):
    return [p for batch in decode_batches(data) for p in batch]
