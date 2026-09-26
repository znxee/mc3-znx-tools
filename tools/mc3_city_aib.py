"""mc3_city_aib.py - generate the .graph and the _pvs.aib of a new city.

Both files live OUTSIDE the .pck, in ASSETS/city/<city>/, and carry the city's
identity: the .graph is the pedestrian navigation graph (text) and the _pvs.aib
is a TSV1 container whose first record is the extents.

Measured on 12/09: modcity.graph and modcity_pvs.aib were BYTE-FOR-BYTE COPIES
of tokyo's - same sha256 - so Los Angeles ran with Tokyo's pedestrian graph and
extents (-1873.8..1433.9 against LA's -2000..1600), and the _pvs.aib still
declared the name 'tokyo' in record id=08.

TSV1 FORMAT (the same as _city.aib and _pvs.aib):

    'TSV1' followed by records [id:u8][mark:u8][len:u32][payload]

    The mark is 'A' (0x41) or 'B' (0x42); I saw no others in the four cities.

    id 0x0C  24 bytes   extents: min x,y,z then max x,y,z (6 floats)
    id 0x00  10 bytes   version header
    id 0x08  36 bytes   city name, zero-terminated and padded ASCII
    id 0x05  44 bytes   starts with the same 6 extent floats
    id 0x01, 0x09, 0x03, 0x04, 0x07, 0x0D   visibility data
    id 0x06, 0x02                            small, copied as they are

The length comes from the container, so an EMPTY array is representable - and
it already shows up in retail: atlanta's id 0x06 has len 0.

What this generator writes is NOT a computed PVS: it is an EMPTY PVS with the
right identity, which swaps "another city's data" for "no data", keeping the
record sequence the engine expects. If the engine needs real visibility, this is
the starting point, not the end.
"""
import argparse
import os
import struct

NUL = bytes(1)

# visibility arrays: they come out empty, because the source city's describe
# other geometry and are worse than nothing
VISIBILITY = (0x01, 0x09, 0x03, 0x04, 0x07, 0x0D)


def read_records(path):
    """Sequence of (id, mark, payload) of a TSV1."""
    d = open(path, 'rb').read()
    if d[:4] != b'TSV1':
        raise ValueError('%s is not TSV1' % path)
    outside, off = [], 4
    while off + 6 <= len(d):
        tid, marks = d[off], d[off + 1]
        if marks not in (0x41, 0x42):
            raise ValueError('unexpected mark %02X at +%06X' % (marks, off))
        n = struct.unpack_from('<I', d, off + 2)[0]
        outside.append((tid, marks, d[off + 6:off + 6 + n]))
        off += 6 + n
    return outside


def write_records(path, records):
    with open(path, 'wb') as f:
        f.write(b'TSV1')
        for tid, marks, body in records:
            f.write(struct.pack('<BBI', tid, marks, len(body)))
            f.write(body)


def extents_from_place(path):
    """(minx, miny, minz, maxx, maxy, maxz) read from the place header."""
    mn = mx = None
    for line in open(path, encoding='utf-8', errors='replace'):
        p = line.rstrip('\n').split('\t')
        if p[0] == '# extents_min':
            mn = tuple(float(v) for v in p[1:4])
        elif p[0] == '# extents_max':
            mx = tuple(float(v) for v in p[1:4])
        if mn and mx:
            return mn + mx
    raise ValueError('the place does not declare extents_min/extents_max')


def make_pvs(out_path, city, ext, reference):
    """_pvs.aib with the new city's identity and no visibility data."""
    outside = []
    for tid, marks, body in read_records(reference):
        if tid == 0x0C:
            body = struct.pack('<6f', *ext)
        elif tid == 0x08:
            name = city.encode('ascii')[:len(body) - 1]
            body = name + NUL * (len(body) - len(name))
        elif tid == 0x05 and len(body) >= 24:
            body = struct.pack('<6f', *ext) + body[24:]
        elif tid in VISIBILITY:
            body = b''
        outside.append((tid, marks, body))
    write_records(out_path, outside)


def make_graph(out_path, code, ext):
    """.graph with the right extents and an empty pedestrian graph."""
    L = ['MinExtents %f\t%f\t%f ' % ext[0:3],
         'MaxExtents %f\t%f\t%f ' % ext[3:6],
         'Graphs 1',
         '{',
         '\tGraph %s_graph' % code,
         '\t{']
    for block in ('VertexArray', 'NormalArray', 'ConnectionArray',
                  'GraphNodeArray', 'NodeUserDataArray'):
        L.append('\t\t%s 0 ' % block)
        L.append('\t\t{')
        L.append('\t\t}')
    L.append('\t}')
    L.append('}')
    # the retail files use CRLF
    open(out_path, 'wb').write(('\r\n'.join(L) + '\r\n').encode('ascii'))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--place', required=True, help='where the extents come from')
    p.add_argument('--city', default='losangeles',
                   help='name written into record 0x08')
    p.add_argument('--code', default='m', help='graph name prefix')
    p.add_argument('--reference', required=True,
                   help='retail _pvs.aib the record sequence comes from')
    p.add_argument('--out-path', required=True, help='ASSETS/city/<city> folder')
    p.add_argument('--prefix', default='modcity', help='file prefix')
    a = p.parse_args(argv)

    ext = extents_from_place(a.place)
    print('extents: min %.1f %.1f %.1f  max %.1f %.1f %.1f' % ext)
    g = os.path.join(a.out_path, a.prefix + '.graph')
    v = os.path.join(a.out_path, a.prefix + '_pvs.aib')
    make_graph(g, a.code, ext)
    make_pvs(v, a.city, ext, a.reference)
    print('  %s (%d bytes)' % (g, os.path.getsize(g)))
    print('  %s (%d bytes)' % (v, os.path.getsize(v)))

    # reopen what was just written, instead of trusting what was written
    records_ = read_records(v)
    found_it = {}
    for tid, marks, body in records_:
        found_it.setdefault(tid, body)
    read_one = struct.unpack('<6f', found_it[0x0C])
    if any(abs(x - y) > 0.01 for x, y in zip(read_one, ext)):
        raise SystemExit('extents do not match: %s' % (read_one,))
    name = found_it[0x08].split(NUL)[0].decode('ascii')
    if name != a.city:
        raise SystemExit('name does not match: %r' % name)
    left_over = [('%02X' % t) for t, _, c in records_ if t in VISIBILITY and c]
    if left_over:
        raise SystemExit('visibility left over in %s' % ', '.join(left_over))
    print('  reopened: %d records, extents and name of %s, empty visibility'
          % (len(records_), name))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
