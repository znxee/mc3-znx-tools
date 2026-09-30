#!/usr/bin/env python3
"""Add a car-lane shortcut to a converted MC2 race, for a leg the AI drives badly.

The AI steers at a race line that is PLANAR (shortcut nodes keep only x and z,
aiBrain::GenerateTargetFromRaceSpheres passes y = 0). On a leg with no shortcut
(edge ... -1) the target is a straight-ish line between the two waypoints, so
where two carriageways run at different heights the cars can drop into the wrong
one and hit a wall. A shortcut whose nodes follow the aib's directed car lanes
from one waypoint to the next keeps them on the right carriageway.

    python mc2_race_shortcut.py --city paris --race paris_ian_checkpoint_one --leg 99 100
    python mc2_race_shortcut.py --city paris --race RACE --leg 99 100 --dry-run

A leg is two waypoint ids (aiRoad ids of the city aib). The tool appends a
Shortcut block, bumps NumShortcut and points every Route edge that goes from the
first to the second waypoint with shortcut -1 at the new block. A leg that
already has a shortcut is refused; to redo one, regenerate the race first
(mc2_races_to_mc3.py) or restore the backup.
"""
import argparse
import collections
import glob
import heapq
import math
import os
import re
import struct
import sys

RACES = os.environ.get('MC3_RACES', os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/tune/race'))
CITIES = os.environ.get('MC3_CITIES', os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/city'))
SPEED = 6.0          # node speed field (same scale as the converted MC2 shortcuts)
SPACING = 30.0       # metres between nodes along the lane path


def load_aib(city):
    folder = 'losangeles' if city == 'losangeles' else city
    d = open('%s/%s/%s_city.aib' % (CITIES, folder, folder), 'rb').read()
    p, nodes, rails, roads = 4, [], [], []
    while p + 6 <= len(d):
        tag, ln = struct.unpack_from('<HI', d, p)
        pl = d[p + 6:p + 6 + ln]
        if tag == 0x4101:
            nodes = [struct.unpack_from('<3f', pl, 28 * i) for i in range(ln // 28)]
        elif tag == 0x4109:
            rails = [pl[20 * i:20 * i + 20] for i in range(ln // 20)]
        elif tag == 0x4107:
            n = struct.unpack_from('<H', pl, 0)[0]
            x, z = struct.unpack_from('<2f', pl, 2 + n + 2)
            roads.append((x, z))
        p += 6 + ln
    return nodes, rails, roads


def seg_dist(p, a, b):
    """Distance from 2D point p to the segment a-b."""
    ax, az, bx, bz = a[0], a[2], b[0], b[2]
    dx, dz = bx - ax, bz - az
    t = 0.0 if dx == 0 and dz == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - az) * dz) / (dx * dx + dz * dz)))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (az + t * dz))


def lanes_at(nodes, rails, xz, y, reach):
    """Car rails (a, b) whose segment passes within `reach` m of xz at about height y."""
    out = []
    for r in rails:
        if r[18] & 8:
            continue
        a, b = struct.unpack_from('<HH', r, 0)
        if min(abs(nodes[a][1] - y), abs(nodes[b][1] - y)) > 3.0:
            continue
        d = seg_dist(xz, nodes[a], nodes[b])
        if d <= reach:
            out.append((d, a, b))
    return out


def lane_path(nodes, rails, src_xz, dst_xz, y=0.0):
    """Shortest directed run of car rails from the lane at src_xz to the lane at dst_xz.

    The waypoint sits ON a lane, not on a node: the run leaves from the end node of the
    rail that passes the source waypoint and arrives at the start node of the rail
    that passes the destination one (rail flag bit 3 = pedestrian, skipped).
    """
    for reach in (6.0, 12.0, 20.0):
        srcs, dsts = lanes_at(nodes, rails, src_xz, y, reach), lanes_at(nodes, rails, dst_xz, y, reach)
        if srcs and dsts:
            break
    else:
        return None
    adj = collections.defaultdict(list)
    for r in rails:
        if not r[18] & 8:
            a, b = struct.unpack_from('<HH', r, 0)
            adj[a].append((b, math.dist(nodes[a], nodes[b])))
    OFF = 10.0      # 1 m of sideways offset costs as much as 10 m of driving
    goal = {}
    for d, a, _ in dsts:
        goal[a] = min(goal.get(a, 1e18), d)
    dist, prev, pq = {}, {}, []
    for d, _, b in srcs:
        if d * OFF < dist.get(b, 1e18):
            dist[b] = d * OFF
            heapq.heappush(pq, (d * OFF, b))
    end, best = None, 1e18
    while pq:
        dd, u = heapq.heappop(pq)
        if dd >= best:
            break
        if dd > dist.get(u, 1e18):
            continue
        if u in goal and dd + goal[u] * OFF < best:
            end, best = u, dd + goal[u] * OFF
        for v, ln in adj[u]:
            if dd + ln < dist.get(v, 1e18):
                dist[v] = dd + ln
                prev[v] = u
                heapq.heappush(pq, (dd + ln, v))
    if end is None:
        return None
    seq = [end]
    while seq[-1] in prev:
        seq.append(prev[seq[-1]])
    return [nodes[i] for i in seq[::-1]]


def densify(points, spacing):
    out = [points[0]]
    for a, b in zip(points, points[1:]):
        n = max(1, int(math.dist((a[0], a[2]), (b[0], b[2])) // spacing))
        for k in range(1, n + 1):
            t = k / n
            out.append(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))
    return out


def block(src, dst, pts):
    lines = ['Shortcut', '{', '\tSourceIntersection %d ' % src, '\tDestinationIntersection %d ' % dst,
             '\tnumShortcutNodes %d ' % len(pts)]
    for x, y, z in pts:
        lines.append('\t%f\t%f\t%f  %f  0  -1.000000  -1.000000  -1.000000  ' % (x, y, z, SPEED))
    lines.append('}')
    return '\n'.join(lines) + '\n'


def add_leg(text, aib, src, dst, y=0.0, max_detour=2.5, force=False):
    """Return (new race text, info) with a lane shortcut on the leg src -> dst.

    `text` uses '\n' line ends; `aib` is load_aib(city). Raises ValueError with the
    reason when the leg cannot or should not be changed.
    """
    nodes, rails, roads = aib
    count = int(re.search(r'NumShortcut (\d+)', text).group(1))
    if len(re.findall(r'^Shortcut\n', text, re.M)) != count:
        raise ValueError('NumShortcut %d does not match the Shortcut blocks' % count)
    pts = lane_path(nodes, rails, roads[src], roads[dst], y)
    if not pts:
        raise ValueError('no directed lane path %d -> %d at y %.1f' % (src, dst, y))
    pts = densify([(roads[src][0], 0.0, roads[src][1])] + pts + [(roads[dst][0], 0.0, roads[dst][1])], SPACING)
    length = sum(math.dist((p[0], p[2]), (q[0], q[2])) for p, q in zip(pts, pts[1:]))
    straight = math.dist(roads[src], roads[dst])
    if length > max_detour * straight + 150.0 and not force:
        raise ValueError('lane path %d -> %d is %.0f m for %.0f m in a straight line (a loop: check it by hand, '
                         'or force it)' % (src, dst, length, straight))

    # Route edges name waypoints by list index: 'edge <from> <to> <prob> <shortcut>'
    edited = 0

    def fix_route(m):
        nonlocal edited
        seg = m.group(0)
        ids = [int(l.split()[1]) for l in seg.split('\n') if l.strip().startswith('Waypoint ')]

        def fix_edge(e):
            nonlocal edited
            f, t = int(e.group(1)), int(e.group(2))
            if 0 <= f < len(ids) and 0 <= t < len(ids) and (ids[f], ids[t]) == (src, dst):
                if e.group(4) != '-1':
                    raise ValueError('leg %d -> %d already has shortcut %s' % (src, dst, e.group(4)))
                edited += 1
                return e.group(0)[:e.start(4) - e.start(0)] + str(count)
            return e.group(0)
        return re.sub(r'edge (\d+)\s+(-?\d+)(\s+[\d.]+\s+)(-?\d+)', fix_edge, seg)

    text = re.sub(r'Route\n\t\{\n(?:.*\n)*?\t\}\n', fix_route, text)
    if not edited:
        raise ValueError('no Route edge goes %d -> %d' % (src, dst))
    text = text.replace('NumShortcut %d ' % count, 'NumShortcut %d ' % (count + 1), 1)
    at = text.index('PlayerPosDir')
    text = text[:at] + block(src, dst, pts) + text[at:]
    return text, {'nodes': len(pts), 'length': length, 'straight': straight, 'edges': edited, 'index': count}


def refine_shortcut(text, index, spacing=30.0, knot=10.0, min_turn=45.0):
    """Add nodes to an EXISTING shortcut so the smoothed race line stays close to its polyline.

    The line the AI follows is a smooth curve through the nodes; with a long straight and
    a sharp turn it cuts the corner across whatever stands inside it (a planter, a wall
    corner). Extra nodes every `spacing` m and a knot `knot` m before and after each turn
    sharper than `min_turn` degrees hold the curve on the polyline.
    """
    pat = re.compile(r'Shortcut\n\{\n(\tSourceIntersection -?\d+ \n\tDestinationIntersection -?\d+ \n)'
                     r'\tnumShortcutNodes (\d+) \n((?:\t[^\n]*\n)*)\}\n')
    blocks = list(pat.finditer(text))
    if not 0 <= index < len(blocks):
        raise ValueError('no shortcut %d (race has %d)' % (index, len(blocks)))
    m = blocks[index]
    rows = [ln.split() for ln in m.group(3).strip('\n').split('\n')]
    pts = [(float(r[0]), float(r[1]), float(r[2]), r[3:]) for r in rows]

    def lerp(a, b, t):
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t, a[3])

    def turn(a, b, c):
        v1, v2 = (b[0] - a[0], b[2] - a[2]), (c[0] - b[0], c[2] - b[2])
        n1, n2 = math.hypot(*v1), math.hypot(*v2)
        if n1 < 1e-3 or n2 < 1e-3:
            return 0.0
        return math.degrees(math.acos(max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))))

    out = [pts[0]]
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        length = math.hypot(b[0] - a[0], b[2] - a[2])
        cuts = set()
        if length > 1.5 * spacing:
            cuts.update(k / int(length // spacing + 1) for k in range(1, int(length // spacing + 1)))
        step = min(knot, 0.4 * length) / length if length > 0 else 0.0
        if i > 0 and turn(pts[i - 1], a, b) >= min_turn:
            cuts.add(step)
        if i + 2 < len(pts) and turn(a, b, pts[i + 2]) >= min_turn:
            cuts.add(1.0 - step)
        # drop cuts that would crowd a node (< 3 m)
        keep = sorted(t for t in cuts if 3.0 / max(length, 1e-6) < t < 1.0 - 3.0 / max(length, 1e-6))
        out.extend(lerp(a, b, t) for t in keep)
        out.append(b)
    lines = ['\t%f\t%f\t%f  %s  ' % (x, y, z, '  '.join(tail)) for x, y, z, tail in out]
    new = 'Shortcut\n{\n%s\tnumShortcutNodes %d \n%s\n}\n' % (m.group(1), len(out), '\n'.join(lines))
    return text[:m.start()] + new + text[m.end():], {'before': len(pts), 'after': len(out)}


def insert_node(text, index, after, x, z):
    """Insert a node at (x, z) into shortcut `index` after its node `after` (0-based).

    For a turn right at the foot of a narrow ramp: a node on the ramp's axis some metres
    before the foot makes the smoothed line arrive aligned instead of cutting the corner.
    Height is taken from the previous node (the race line is planar anyway).
    """
    pat = re.compile(r'Shortcut\n\{\n(\tSourceIntersection -?\d+ \n\tDestinationIntersection -?\d+ \n)'
                     r'\tnumShortcutNodes (\d+) \n((?:\t[^\n]*\n)*)\}\n')
    blocks = list(pat.finditer(text))
    if not 0 <= index < len(blocks):
        raise ValueError('no shortcut %d (race has %d)' % (index, len(blocks)))
    m = blocks[index]
    rows = m.group(3).strip('\n').split('\n')
    if not 0 <= after < len(rows) - 1:
        raise ValueError('shortcut %d has %d nodes; cannot insert after %d' % (index, len(rows), after))
    prev, nxt = rows[after].split(), rows[after + 1].split()
    rows.insert(after + 1, '\t%f\t%s\t%f  %s  ' % (x, prev[1], z, '  '.join(nxt[3:])))
    new = 'Shortcut\n{\n%s\tnumShortcutNodes %d \n%s\n}\n' % (m.group(1), len(rows), '\n'.join(rows))
    return text[:m.start()] + new + text[m.end():], {'before': len(rows) - 1, 'after': len(rows)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--city', default='paris')
    ap.add_argument('--race', required=True)
    ap.add_argument('--leg', nargs=2, type=int, metavar=('FROM', 'TO'))
    ap.add_argument('--refine', nargs='+', type=int, metavar='N',
                    help='instead of a lane shortcut: add corner-holding nodes to these existing shortcuts (0-based)')
    ap.add_argument('--insert', nargs=4, type=float, action='append', metavar=('N', 'AFTER', 'X', 'Z'),
                    help='insert a node (x, z) into existing shortcut N after its node AFTER (repeatable)')
    ap.add_argument('--min-turn', type=float, default=45.0,
                    help='--refine: turns sharper than this (degrees) get knots before and after')
    ap.add_argument('--y', type=float, default=0.0, help='height of the carriageway the leg should use')
    ap.add_argument('--force', action='store_true', help='accept a lane path much longer than the straight line')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    hits = glob.glob(os.path.join(RACES, '**', a.race + '.rac'), recursive=True)
    if not hits:
        sys.exit('race not found: %s' % a.race)
    path = hits[0]
    text = open(path, encoding='latin1', newline='').read()
    nl = '\r\n' if '\r\n' in text else '\n'
    text = text.replace('\r\n', '\n')
    if not a.leg and not a.refine and not a.insert:
        sys.exit('give --leg FROM TO, --refine N or --insert N AFTER X Z')
    try:
        if a.insert:
            for n, after, x, z in a.insert:
                text, info = insert_node(text, int(n), int(after), x, z)
                print('%s shortcut %d: node (%.1f, %.1f) after %d, %d -> %d nodes' % (
                    a.race, n, x, z, after, info['before'], info['after']))
        elif a.refine:
            for n in a.refine:
                text, info = refine_shortcut(text, n, min_turn=a.min_turn)
                print('%s shortcut %d: %d -> %d nodes' % (a.race, n, info['before'], info['after']))
        else:
            text, info = add_leg(text, load_aib(a.city), a.leg[0], a.leg[1], a.y, force=a.force)
            print('%s leg %d -> %d: %d nodes, %.0f m (straight %.0f m), %d Route edge(s) now use shortcut %d' % (
                a.race, a.leg[0], a.leg[1], info['nodes'], info['length'], info['straight'], info['edges'],
                info['index']))
    except ValueError as e:
        sys.exit('%s: %s' % (a.race, e))
    if a.dry_run:
        print('dry run - file not written')
        return 0
    open(path, 'w', encoding='latin1', newline='').write(text.replace('\n', nl))
    print('wrote', path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
