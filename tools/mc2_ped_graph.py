#!/usr/bin/env python3
"""Pedestrian navigation graph (<city>.graph, MC3's mcBaGraph) for a MC2 city,
built from the sidewalks of MC2's own collision.

    python mc2_ped_graph.py --out out/losangeles.graph
    python mc2_ped_graph.py --out out/losangeles.graph --install   # backs up and installs as losangeles.graph

Source: the otgrid BND0 of <city>.rsc (the collision losangeles_bound gives the
game). Every walkable polygon whose material is l_physics:sidewalkSG becomes a
graph node; vertices are merged by position (the otgrid repeats them per
cell); two nodes are connected where they share an edge (MC3 keeps one
Connection per side: Vertex1, Vertex2, the node on that side). Small nodes
(--corner m2) with two or more neighbours are 'corner' - in Tokyo's graph the
corners are the 10 m2 pieces at block corners that tie the sidewalk strips
together - the rest 'sidewalk'. UserData 1.0 per node, as in every retail
graph. Prints the node, edge and island counts.
"""
import argparse
import collections
import math
import os
import shutil
import struct
import sys

import mc2_rsc as R

MC2 = os.environ.get('MC2_PS2_ASSETS', 'mc2_ps2/assets')
MATERIAL = 'l_physics:sidewalkSG'


def sidewalk_polys(rsc):
    d = rsc.data
    u = lambda o: struct.unpack_from('<I', d, o)[0]
    grid = [i for i, (off, fcc, size) in enumerate(rsc.entries)
            if fcc == 'BND0' and d[off + 4] == 0x0B][0]
    off = rsc.entries[grid][0]
    cells, first = u(off + 0x5C) * u(off + 0x68), u(off + 0x6C)

    def pmt(h):
        o = rsc.entries[(h & 0x3FFF) - 1][0]
        return d[o + 8:o + 48].split(b'\0')[0].decode('ascii', 'replace')

    out = []
    for c in range(cells):
        node = off + first + 0x90 * c
        if u(node + 0x84) == 0xFFFFFFFF:
            continue
        nv, npol = u(node + 0x4C), u(node + 0x50)
        vo, po = off + u(node + 0x74), off + u(node + 0x78)
        mats = [pmt(u(off + u(node + 0x80) + 4 * k)) for k in range(u(node + 0x84))]
        verts = [struct.unpack_from('<3f', d, vo + 12 * k) for k in range(nv)]
        for k in range(npol):
            ny = struct.unpack_from('<f', d, po + 32 * k + 4)[0]
            m = d[po + 32 * k + 12]
            if ny < 0.3 or m >= len(mats) or mats[m] != MATERIAL:
                continue
            idx = struct.unpack_from('<4H', d, po + 32 * k + 16)
            out.append([verts[j] for j in idx[:3 if idx[3] == 0 else 4]])
    return out


def area(p):
    a = 0.0
    for i in range(len(p)):
        a += p[i][0] * p[(i + 1) % len(p)][2] - p[(i + 1) % len(p)][0] * p[i][2]
    return abs(a) / 2


def build(polys, corner_area):
    key = lambda v: (round(v[0] / 0.05), round(v[1] / 0.05), round(v[2] / 0.05))
    vid, verts = {}, []
    nodes = []
    for p in polys:
        ids = []
        for v in p:
            k = key(v)
            if k not in vid:
                vid[k] = len(verts)
                verts.append(v)
            ids.append(vid[k])
        if len(set(ids)) == len(ids) and area(p) > 0.05:
            # MC3's nodes wind like Tokyo's: counter-clockwise seen from above
            s = sum(verts[ids[i]][0] * verts[ids[(i + 1) % len(ids)]][2] -
                    verts[ids[(i + 1) % len(ids)]][0] * verts[ids[i]][2] for i in range(len(ids)))
            nodes.append(ids if s > 0 else ids[::-1])
    # shared edges
    owners = collections.defaultdict(list)
    for n, ids in enumerate(nodes):
        for i in range(len(ids)):
            a, b = ids[i], ids[(i + 1) % len(ids)]
            owners[(min(a, b), max(a, b))].append(n)
    conns, node_conns = [], [[] for _ in nodes]
    for (a, b), ns in owners.items():
        if len(ns) != 2 or ns[0] == ns[1]:
            continue
        n0, n1 = ns
        # one Connection per side: this node's list points at the one naming
        # the node on the other side
        conns.append((a, b, n1)); node_conns[n0].append(len(conns) - 1)
        conns.append((a, b, n0)); node_conns[n1].append(len(conns) - 1)
    types = ['corner' if area([verts[i] for i in ids]) <= corner_area and len(node_conns[n]) >= 2
             else 'sidewalk' for n, ids in enumerate(nodes)]
    return verts, nodes, conns, node_conns, types


def islands(nodes, conns, node_conns):
    seen, sizes = set(), []
    for s in range(len(nodes)):
        if s in seen:
            continue
        stack, n = [s], 0
        seen.add(s)
        while stack:
            k = stack.pop()
            n += 1
            for c in node_conns[k]:
                o = conns[c][2]
                if o not in seen:
                    seen.add(o)
                    stack.append(o)
        sizes.append(n)
    return sorted(sizes, reverse=True)


def write(path, name, verts, nodes, conns, node_conns, types):
    lo = [min(v[k] for v in verts) for k in range(3)]
    hi = [max(v[k] for v in verts) for k in range(3)]
    L = []
    L.append('MinExtents %f\t%f\t%f ' % tuple(lo))
    L.append('MaxExtents %f\t%f\t%f ' % tuple(hi))
    L.append('Graphs 1')
    L.append('{')
    L.append('\tGraph %s' % name)
    L.append('\t{')
    L.append('\t\tVertexArray %d' % len(verts))
    L.append('\t\t{')
    L += ['\t\t\t%f\t%f\t%f ' % v for v in verts]
    L.append('\t\t}')
    L.append('\t\tPlaneArray 1')
    L.append('\t\t{')
    L.append('\t\t\t0.000000\t1.000000\t0.000000\t1.000000 ')
    L.append('\t\t}')
    L.append('\t\tConnectionArray %d' % len(conns))
    L.append('\t\t{')
    for i, (a, b, n) in enumerate(conns):
        L += ['\t\t\tConnection %d' % i, '\t\t\t{', '\t\t\t\tVertex1 %d ' % a,
              '\t\t\t\tVertex2 %d ' % b, '\t\t\t\tGraphNode %d ' % n, '\t\t\t}']
    L.append('\t\t}')
    L.append('\t\tGraphNodeArray %d' % len(nodes))
    L.append('\t\t{')
    for i, ids in enumerate(nodes):
        L += ['\t\t\tGraphNode %d' % i, '\t\t\t{', '\t\t\t\tVertices %d' % len(ids), '\t\t\t\t{']
        L += ['\t\t\t\t\t%d ' % v for v in ids]
        L += ['\t\t\t\t}', '\t\t\t\tConnections %d' % len(node_conns[i]), '\t\t\t\t{']
        L += ['\t\t\t\t\t%d ' % c for c in node_conns[i]]
        L += ['\t\t\t\t}', '\t\t\t\tNormal 0', '\t\t\t\tType %s' % types[i], '\t\t\t}']
    L.append('\t\t}')
    L.append('\t\tNodeUserDataArray %d' % len(nodes))
    L.append('\t\t{')
    for i in range(len(nodes)):
        L += ['\t\t\tUserData %d' % i, '\t\t\t{', '\t\t\t\t1.000000', '\t\t\t}']
    L.append('\t\t}')
    L.append('\t}')
    L.append('}')
    open(path, 'w', newline='\r\n').write('\n'.join(L) + '\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--city', default='losangeles')
    ap.add_argument('--mc2', default=MC2)
    ap.add_argument('--name', default='m_graph', help='graph name written in the file')
    ap.add_argument('--corner', type=float, default=35.0, help='max area of a corner node (m2)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--install', action='store_true',
                    help='back up $MC3_HOSTFS/ASSETS/city/losangeles/losangeles.graph and install')
    a = ap.parse_args()
    rsc = R.Rsc(os.path.join(a.mc2, 'resource', a.city, a.city + '.rsc'))
    polys = sidewalk_polys(rsc)
    verts, nodes, conns, node_conns, types = build(polys, a.corner)
    isl = islands(nodes, conns, node_conns)
    print('%d sidewalk polygons -> %d nodes (%d corner), %d vertices, %d connections' % (
        len(polys), len(nodes), types.count('corner'), len(verts), len(conns)))
    print('islands: %d, largest %s, single nodes %d' % (len(isl), isl[:8], isl.count(1)))
    write(a.out, a.name, verts, nodes, conns, node_conns, types)
    print('wrote %s (%d bytes)' % (a.out, os.path.getsize(a.out)))
    if a.install:
        dst = os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/city/losangeles/losangeles.graph')
        bak = dst + '.before_mc2_peds'
        if not os.path.exists(bak):
            shutil.copy2(dst, bak)
        shutil.copy2(a.out, dst)
        print('installed (previous kept as %s)' % bak)
    return 0


if __name__ == '__main__':
    sys.exit(main())
