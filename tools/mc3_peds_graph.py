#!/usr/bin/env python3
"""Put a pedestrian navigation graph into a city's *_peds.pck.

The pedestrians do NOT walk on ASSETS/city/<city>/<city>.graph at run time:
retail has no reader for that text any more, only mcBaGraph(datResource&).
The graph they use is compiled into <city>_peds.pck (header type 0x21), as
the first member of the root mcCityLifeSimulatorData at file offset 0x80:

    root+0x00  vertex array   Vector3 (12 B)            +0x04 u16 count, u16 capacity
    root+0x08  node array     40 B each                 +0x0C count, capacity
    root+0x10  connection     u16 vertex1, vertex2, node (6 B)   +0x14
    root+0x1C  node user data 3 B each                  +0x20

    node: +0x00 u16 vertex[8], +0x10 u16 connection[8] (unused slots 0xCDCD),
          +0x20 u8 vertex count, +0x21 u8 connection count,
          +0x22 u8 type (0 sidewalk, 2 corner), +0x23 u8 1,
          +0x24 float (the area for corners; read by nothing we could find)
    user data: 00 01 01 for a sidewalk, 00 00 00 for a corner (tokyo)

A peds pack copied from Tokyo therefore makes Los Angeles' and Paris'
pedestrians walk Tokyo's 1084 sidewalks, which is what "pedestrians in random
places" was. mcFeedSurfaceManager builds its spawn list from the graph at run
time, so nothing else in the pack holds node indices.

The new arrays are APPENDED and the root repointed - nothing already in the
file moves, the old arrays stay behind unused - and the header's size word
(+0x0C = file size - 0x80) is updated.

    python mc3_peds_graph.py losangeles_peds.pck losangeles.graph
    python mc3_peds_graph.py paris_peds.pck paris.graph --out paris_peds.new.pck
"""
import argparse
import math
import shutil
import struct
import sys

import mc3_graph as G

PEDS_TYPE = 0x21
ROOT = 0x80
PAD = 0xCDCD
TYPES = {'sidewalk': 0, 'corner': 2}
USERDATA = {'sidewalk': b'\x00\x01\x01', 'corner': b'\x00\x00\x00'}


def area_xz(points):
    return abs(sum(points[i][0] * points[(i + 1) % len(points)][2] -
                   points[(i + 1) % len(points)][0] * points[i][2]
                   for i in range(len(points)))) / 2.0


def arrays(graph):
    V, C, N = graph['verts'], graph['conns'], graph['nodes']
    if len(V) > 0xFFFF or len(N) > 0xFFFF or len(C) > 0xFFFF:
        raise ValueError('graph too large for u16 indices')
    verts = b''.join(struct.pack('<3f', *v) for v in V)
    conns = b''.join(struct.pack('<3H', a, b, n) for a, b, n in C)
    nodes, user = bytearray(), bytearray()
    for n in N:
        if len(n['verts']) > 8 or len(n['conns']) > 8:
            raise ValueError('node with %d vertices / %d connections (max 8)'
                             % (len(n['verts']), len(n['conns'])))
        vs = list(n['verts']) + [PAD] * (8 - len(n['verts']))
        cs = list(n['conns']) + [PAD] * (8 - len(n['conns']))
        nodes += struct.pack('<8H8HBBBBf', *vs, *cs, len(n['verts']), len(n['conns']),
                             TYPES.get(n['type'], 0), 1, area_xz([V[i] for i in n['verts']]))
        user += USERDATA.get(n['type'], USERDATA['sidewalk'])
    return verts, bytes(nodes), conns, bytes(user)


def install(pck, graph):
    data = bytearray(pck)
    base, kind = struct.unpack_from('<II', data, 0)
    if kind != PEDS_TYPE:
        raise ValueError('not a peds pack (type 0x%X)' % kind)
    if struct.unpack_from('<I', data, 12)[0] != len(data) - ROOT:
        raise ValueError('size word does not match the file')
    blocks = arrays(graph)
    counts = (len(graph['verts']), len(graph['nodes']), len(graph['conns']), len(graph['nodes']))
    fields = (0x00, 0x08, 0x10, 0x1C)
    for blob, n, field in zip(blocks, counts, fields):
        while len(data) % 16:
            data.append(0)
        va = base + len(data) - ROOT
        data += blob
        struct.pack_into('<IHH', data, ROOT + field, va, n, n)
    while len(data) % 16:
        data.append(0)
    struct.pack_into('<I', data, 12, len(data) - ROOT)
    return bytes(data)


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('pck')
    ap.add_argument('graph', help='text <city>.graph (mc2_ped_graph.py writes one)')
    ap.add_argument('--out', help='write here instead of replacing the pack (the original is kept as .before_graph)')
    a = ap.parse_args()
    g = G.read(a.graph)
    new = install(open(a.pck, 'rb').read(), g)
    out = a.out or a.pck
    if not a.out:
        shutil.copy2(a.pck, a.pck + '.before_graph')
    open(out, 'wb').write(new)
    print('%s: %d vertices, %d nodes, %d connections -> %d bytes' % (
        out, len(g['verts']), len(g['nodes']), len(g['conns']), len(new)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
