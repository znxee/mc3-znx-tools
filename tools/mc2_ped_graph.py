#!/usr/bin/env python3
"""Pedestrian navigation graph (<city>.graph, MC3's mcBaGraph) for a MC2 city.

    python mc2_ped_graph.py --city losangeles --out out/losangeles.graph
    python mc2_ped_graph.py --city paris --out out/paris.graph --install

SOURCE (default --source rails): the pedestrian rails of the MC2 city's own
.aib - the rails with bit 3 (0x08) of the flag byte at +18, width 40 at +10.
They are where MC2's pedestrians walk (1413 in Los Angeles, 1305 in Paris,
almost every node of degree 2, 64 / 59 connected pieces). Every rail becomes
a strip of sidewalk nodes of WIDTH metres, cut into pieces of at most PIECE m,
two strips meeting share one mitred edge, and a junction of three or more
rails becomes a 'corner' node whose edges are exactly the strips' ends. Heights come from the rail nodes.

The older --source collision took every walkable polygon of MC2's collision
whose material is l_physics:sidewalkSG. Measured on the result, that is the
cause of pedestrians in odd places: 693 of its 2974 nodes stood more than 3 m
above the street (roofs, walkways up to 26 m) and it fell apart into 632
islands (largest 63 nodes, 190 of a single node), so a pedestrian spawned on
any fragment and walked nowhere.

COLLISION SOURCE, kept for comparison:

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
PED_RAIL = 0x08          # rail flag (+18) bit: pedestrian rail in MC2
WIDTH = 3.5              # sidewalk strip width, metres
PIECE = 20.0             # longest strip node, metres
MIN_RAIL = 4.0           # shorter pedestrian rails are contracted into their junction


def load_aib(path):
    """(nodes [(x, y, z)], rails [(a, b, flag)]) of an MC2/MC3 .aib."""
    d = open(path, 'rb').read()
    p, nodes, rails = 4, [], []
    while p + 6 <= len(d):
        tag, ln = struct.unpack_from('<HI', d, p)
        pl = d[p + 6:p + 6 + ln]
        if tag == 0x4101:
            nodes = [struct.unpack_from('<3f', pl, 28 * i) for i in range(ln // 28)]
        elif tag == 0x4109:
            rails = [struct.unpack_from('<HH', pl, 20 * i) + (pl[20 * i + 18],) for i in range(ln // 20)]
        p += 6 + ln
    return nodes, rails


def rail_polys(nodes, rails, width=WIDTH, piece=PIECE):
    """Sidewalk polygons from the pedestrian rails: (polygons, corner flags).

    Where two rails meet (almost every junction) the two strips share one
    mitred edge through the junction - no extra node, and a 1 m rail costs
    nothing. Where three or more meet, each strip ends on an edge
    perpendicular to it, far enough out that the ends do not overlap, and a
    'corner' node made of those end points (in angle order) ties them."""
    segs = sorted({(min(a, b), max(a, b)) for a, b, f in rails if f & PED_RAIL and a != b})
    hw = width / 2.0
    # Contract rails shorter than MIN_RAIL: MC2 has pedestrian rails of 1 m, and
    # a strip that short cannot end clear of its neighbours at a junction.
    parent = list(range(len(nodes)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for a, b in segs:
        if math.hypot(nodes[b][0] - nodes[a][0], nodes[b][2] - nodes[a][2]) < MIN_RAIL:
            parent[root(a)] = root(b)
    groups = collections.defaultdict(list)
    for a, b in segs:
        groups[root(a)].append(a)
        groups[root(b)].append(b)
    nodes = list(nodes)
    for r, members in groups.items():
        members = set(members)
        nodes[r] = tuple(sum(nodes[m][k] for m in members) / len(members) for k in range(3))
    segs = sorted({(min(root(a), root(b)), max(root(a), root(b))) for a, b in segs if root(a) != root(b)})
    inc = collections.defaultdict(list)
    for sg in segs:
        inc[sg[0]].append(sg)
        inc[sg[1]].append(sg)

    def other(j, sg):
        return sg[1] if sg[0] == j else sg[0]

    def unit(a, b):
        dx, dz = nodes[b][0] - nodes[a][0], nodes[b][2] - nodes[a][2]
        n = math.hypot(dx, dz) or 1.0
        return dx / n, dz / n

    def length(sg):
        return math.hypot(nodes[sg[1]][0] - nodes[sg[0]][0], nodes[sg[1]][2] - nodes[sg[0]][2])

    edge = {}                       # (junction, segment) -> two end points
    polys, corner = [], []
    for j, ss in inc.items():
        jx, jy, jz = nodes[j]
        if len(ss) == 1:
            ux, uz = unit(j, other(j, ss[0]))
            edge[j, ss[0]] = ((jx - uz * hw, jy, jz + ux * hw), (jx + uz * hw, jy, jz - ux * hw))
        elif len(ss) == 2:
            # chain prev -> j -> next; the mitre is the bisector of the two normals
            ax, az = unit(other(j, ss[0]), j)
            bx, bz = unit(j, other(j, ss[1]))
            mx, mz = -az - bz, ax + bx
            n = math.hypot(mx, mz)
            if n < 1e-3:                    # a U-turn: use the incoming normal
                mx, mz, n = -az, ax, 1.0
            mx, mz = mx / n, mz / n
            cos_half = max(abs(mx * -az + mz * ax), 0.35)
            L = hw / cos_half
            e = ((jx + mx * L, jy, jz + mz * L), (jx - mx * L, jy, jz - mz * L))
            edge[j, ss[0]] = edge[j, ss[1]] = e
        else:
            angs = sorted(math.atan2(*unit(j, other(j, sg))[::-1]) for sg in ss)
            gaps = [((angs[(i + 1) % len(angs)] - angs[i]) % (2 * math.pi)) or 2 * math.pi
                    for i in range(len(angs))]
            back = max(hw, hw / math.tan(max(min(gaps) / 2.0, math.radians(8))))
            pts = []
            for sg in ss:
                ux, uz = unit(j, other(j, sg))
                b = min(back, max(length(sg) - 0.3, 0.05))
                cx, cz = jx + ux * b, jz + uz * b
                e = ((cx - uz * hw, jy, cz + ux * hw), (cx + uz * hw, jy, cz - ux * hw))
                edge[j, sg] = e
                pts += list(e)
            pts.sort(key=lambda q: math.atan2(q[2] - jz, q[0] - jx))
            # two strips at a right angle touch: their shared end point appears twice
            uniq = []
            for q in pts:
                if not uniq or math.dist(q, uniq[-1]) > 0.06:
                    uniq.append(q)
            if len(uniq) > 1 and math.dist(uniq[0], uniq[-1]) <= 0.06:
                uniq.pop()
            polys.append(uniq)
            corner.append(True)
    for sg in segs:
        a, b = sg
        ux, uz = unit(a, b)
        side = lambda q, j: (q[0] - nodes[j][0]) * -uz + (q[2] - nodes[j][2]) * ux

        def ordered(j):
            p0, p1 = edge[j, sg]
            return (p0, p1) if side(p0, j) >= side(p1, j) else (p1, p0)
        la, ra = ordered(a)
        lb, rb = ordered(b)
        n = max(1, int(math.ceil(length(sg) / piece)))
        left = [tuple(la[k] + (lb[k] - la[k]) * i / n for k in range(3)) for i in range(n + 1)]
        right = [tuple(ra[k] + (rb[k] - ra[k]) * i / n for k in range(3)) for i in range(n + 1)]
        for i in range(n):
            polys.append([left[i], left[i + 1], right[i + 1], right[i]])
            corner.append(False)
    return polys, corner

def area(p):
    a = 0.0
    for i in range(len(p)):
        a += p[i][0] * p[(i + 1) % len(p)][2] - p[(i + 1) % len(p)][0] * p[i][2]
    return abs(a) / 2


def build(polys, corner_area, corner_flags=None):
    key = lambda v: (round(v[0] / 0.05), round(v[1] / 0.05), round(v[2] / 0.05))
    vid, verts = {}, []
    nodes, flags, flags_seen = [], [], []
    for p in polys:
        flags_seen.append(None)
        ids = []
        for v in p:
            k = key(v)
            if k not in vid:
                vid[k] = len(verts)
                verts.append(v)
            ids.append(vid[k])
        if len(set(ids)) == len(ids) and area(p) > 0.05:
            flags.append(corner_flags[len(flags_seen) - 1] if corner_flags else None)
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
    if corner_flags:
        types = ['corner' if flags[n] else 'sidewalk' for n in range(len(nodes))]
    else:
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
    ap.add_argument('--name', default=None, help='graph name written in the file (default <code>_graph)')
    ap.add_argument('--source', choices=('rails', 'collision'), default='rails')
    ap.add_argument('--width', type=float, default=WIDTH, help='sidewalk strip width (rails source)')
    ap.add_argument('--hostfs', default=os.environ.get('MC3_HOSTFS', 'MC3HostFS'))
    ap.add_argument('--corner', type=float, default=35.0, help='max area of a corner node (m2)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--install', action='store_true',
                    help='back up <hostfs>/ASSETS/city/<city>/<city>.graph and install')
    a = ap.parse_args()
    name = a.name or {'losangeles': 'm_graph', 'paris': 'p_graph'}.get(a.city, a.city[0] + '_graph')
    if a.source == 'rails':
        aib_nodes, rails = load_aib(os.path.join(a.mc2, 'city', a.city, a.city + '.aib'))
        polys, corner_flags = rail_polys(aib_nodes, rails, a.width)
        verts, nodes, conns, node_conns, types = build(polys, a.corner, corner_flags)
    else:
        rsc = R.Rsc(os.path.join(a.mc2, 'resource', a.city, a.city + '.rsc'))
        polys = sidewalk_polys(rsc)
        verts, nodes, conns, node_conns, types = build(polys, a.corner)
    isl = islands(nodes, conns, node_conns)
    print('%d polygons -> %d nodes (%d corner), %d vertices, %d connections' % (
        len(polys), len(nodes), types.count('corner'), len(verts), len(conns)))
    print('islands: %d, largest %s, single nodes %d' % (len(isl), isl[:8], isl.count(1)))
    write(a.out, name, verts, nodes, conns, node_conns, types)
    print('wrote %s (%d bytes)' % (a.out, os.path.getsize(a.out)))
    if a.install:
        dst = os.path.join(a.hostfs, 'ASSETS', 'city', a.city, a.city + '.graph')
        bak = dst + '.before_ped_rails'
        if not os.path.exists(bak):
            shutil.copy2(dst, bak)
        shutil.copy2(a.out, dst)
        print('installed (previous kept as %s)' % bak)
    return 0


if __name__ == '__main__':
    sys.exit(main())
