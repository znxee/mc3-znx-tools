#!/usr/bin/env python3
"""Box occluders (<city>.occlude, format 3) for a MC2 city, from MC2's own collision.

MC3 hides a car that stands behind a building with mcOccluderSystem::IsAABoxOccluded
(0x39B890, called by rmcCarModel::CullUpdate): the car is skipped whole - body,
headlight glows, light reflections on the road, the light it throws on walls.
The boxes come from ASSETS/city/<city>/<city>.occlude. MC2 has no such boxes
(its .occlude holds a few tunnel quads), so behind MC2 buildings every car
counted as visible: only its body was hidden by the Z buffer, and its lights
showed through walls.

How the buildings are found, on a 2 m grid over the BND0 collision grid:
  1. walls (|normal y| < 0.5) that reach down to street level block the cells
     they cross;
  2. a flood fill from street cells (a floor within 2 m of the local ground)
     through unblocked cells marks everything reachable from the street;
  3. the cells walls enclose and the fill never reached are building interior.
     The space under ramps and freeways has no walls, so it is reached and never
     becomes an occluder; a building with a gap in its walls is skipped (safe).
  4. each interior region gets the LOWEST of its roof floors and of the tops of
     the walls around it, minus a margin, so no box pokes out of the building;
  5. regions are cut into axis-aligned rectangles (largest first) and written as
     boxes from y -100 (as the retail boxes) up to that top.
MC2's own occluder quads can be appended as plane occluders (--planes; their
facing in MC3 is untested, so they are left out by default).
No box may touch an aib lane (car or pedestrian): a car or a camera inside an
occluder would be culled.

Testing: in the spectator modes (mc2s = N, Nf) MC3 culls from the PLAYER's
camera, not the one on screen - with occluders loaded the watched car vanishes
whenever it is outside the player camera's frustum. Use mc2s = <N>c (chase,
mc3_race_tour.py --chase), where the game's own camera draws.

    python mc2_occluders.py --city losangeles --out out/occ/losangeles.occlude --png out/occ/la.png
    python mc2_occluders.py --city paris --out $MC3_HOSTFS/ASSETS/city/paris/paris.occlude
"""
import argparse
import collections
import math
import os
import struct
import sys

import numpy as np

import mc2_rsc as R

MC2 = os.environ.get('MC2_PS2_ASSETS', 'mc2_ps2/assets')
CELL = 2.0
MIN_HEIGHT = 6.0      # a box must rise this much above the street around it
MIN_AREA = 64.0       # m^2
TOP_MARGIN = 1.0
MAX_BOXES = 455      # the most any retail city has (tokyo 175, atlanta 340, sd 430, detroit 455)
VOID_REACH = 40.0     # m: void behind a facade counted as that building
MAX_REGION = 30000.0  # m^2: a bigger enclosed area is not a building (skipped, reported)


def polygons(city):
    """(floors, walls): lists of (ny, [(x, y, z), ...]) from the BND0 grid."""
    rsc = R.Rsc(os.path.join(MC2, 'resource', city, city + '.rsc'))
    d = rsc.data
    u = lambda o: struct.unpack_from('<I', d, o)[0]
    floors, walls, seen = [], [], set()
    # the octree grid (kind 0x0B) and the `_quad_0` blocks (kind 0x0A, one node
    # each): many roads have their floor only in the latter
    nodes = []
    for off, fcc, size in rsc.entries:
        if fcc != 'BND0' or d[off + 4] not in (0x0A, 0x0B):
            continue
        if d[off + 4] == 0x0A:
            nodes.append((off, off))
            continue
        cells, first = u(off + 0x5C) * u(off + 0x68), u(off + 0x6C)
        nodes += [(off, off + first + 0x90 * c) for c in range(cells)
                  if u(off + first + 0x90 * c + 0x84) != 0xFFFFFFFF]
    for off, node in nodes:
        nv, npol = u(node + 0x4C), u(node + 0x50)
        vo, po = off + u(node + 0x74), off + u(node + 0x78)
        verts = [struct.unpack_from('<3f', d, vo + 12 * k) for k in range(nv)]
        for k in range(npol):
            ny = struct.unpack_from('<f', d, po + 32 * k + 4)[0]
            idx = struct.unpack_from('<4H', d, po + 32 * k + 16)
            pts = tuple(verts[j] for j in idx[:3 if idx[3] == 0 else 4])
            key = tuple(round(v, 2) for p in pts for v in p)
            if key in seen:          # a polygon on a cell border is stored in both cells
                continue
            seen.add(key)
            if ny >= 0.3:
                floors.append(pts)
            elif abs(ny) < 0.5:
                walls.append(pts)
    return floors, walls


def aib_segments(city):
    """Both ends of every rail (car lanes and pedestrian rails) of MC2's aib."""
    d = open(os.path.join(MC2, 'city', city, city + '.aib'), 'rb').read()
    p, nodes, rails = 4, [], []
    while p + 6 <= len(d):
        tag, ln = struct.unpack_from('<HI', d, p)
        pl = d[p + 6:p + 6 + ln]
        if tag == 0x4101:
            nodes = [struct.unpack_from('<3f', pl, 28 * i) for i in range(ln // 28)]
        elif tag == 0x4109:
            rails = [struct.unpack_from('<HH', pl, 20 * i) for i in range(ln // 20)]
        p += 6 + ln
    return [(nodes[a], nodes[b]) for a, b in rails if a < len(nodes) and b < len(nodes)]


def plane_quads(city):
    path = os.path.join(MC2, 'city', city, city + '.occlude')
    out = []
    if not os.path.exists(path):
        return out
    lines = [l.strip() for l in open(path) if l.strip()]
    i = 0
    while i < len(lines):
        if lines[i].startswith('verts: 4'):
            out.append([tuple(float(v) for v in lines[i + 2 + k].split()) for k in range(4)])
            i += 6
        else:
            i += 1
    return out


class Grid(object):
    def __init__(self, floors, walls):
        xs = [p[0] for poly in floors + walls for p in poly]
        zs = [p[2] for poly in floors + walls for p in poly]
        self.x0, self.z0 = math.floor(min(xs)) - 4, math.floor(min(zs)) - 4
        self.nx = int((max(xs) - self.x0) / CELL) + 5
        self.nz = int((max(zs) - self.z0) / CELL) + 5

    def cell(self, x, z):
        return int((x - self.x0) / CELL), int((z - self.z0) / CELL)

    def centre(self, i, j):
        return self.x0 + (i + 0.5) * CELL, self.z0 + (j + 0.5) * CELL


def point_in(poly, x, z):
    inside = False
    n = len(poly)
    for k in range(n):
        ax, az = poly[k][0], poly[k][2]
        bx, bz = poly[(k + 1) % n][0], poly[(k + 1) % n][2]
        if (az > z) != (bz > z) and x < ax + (bx - ax) * (z - az) / (bz - az):
            inside = not inside
    return inside


def floor_y(poly, x, z):
    """Height of the (planar) polygon at x, z."""
    a, b, c = poly[0], poly[1], poly[2]
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    if abs(ny) < 1e-6:
        return max(p[1] for p in poly)
    return a[1] - (nx * (x - a[0]) + nz * (z - a[2])) / ny


def largest_rectangle(m):
    """(area, row, col, height, width) of the largest all-True rectangle of m."""
    rows, cols = m.shape
    heights = np.zeros(cols, np.int32)
    best = None
    for r in range(rows):
        heights = np.where(m[r], heights + 1, 0)
        stack = []
        for c in range(cols + 1):
            hc = heights[c] if c < cols else 0
            start = c
            while stack and stack[-1][1] >= hc:
                s0, sh = stack.pop()
                area = int(sh) * (c - s0)
                if sh and (best is None or area > best[0]):
                    best = (area, r - int(sh) + 1, s0, int(sh), c - s0)
                start = s0
            stack.append((start, hc))
    return best


def rendered(city, g, local):
    """From MC2's own drawn geometry (LOD 0 of every component, baked in world
    space, and of every instance whose model is bigger than a prop - radius
    >= 10 m - placed with its .hood matrix):
      roof    highest near-horizontal surface over each cell (-inf = none);
      facade  cells crossed by a vertical triangle that reaches down to street
              level (a building's wall, which collision often leaves open);
      top     highest point of those vertical triangles over each cell.
    MC2 does not model the roof of most low buildings, so `top` is what gives
    their height."""
    rsc = R.Rsc(os.path.join(MC2, 'resource', city, city + '.rsc'))
    models = R.models_by_name(rsc)
    comps, insts = R.read_hoods(os.path.join(MC2, 'city', city))
    cache = {}

    def model_tris(m):
        if m.name not in cache:
            out = []
            for h in R.model_blocks(m, 0):
                got = rsc.resolve(h)
                if got is None:
                    continue
                for b in R.read_pmd(rsc, got[1])[0]:
                    for i, j, k in b.triangles():
                        out.append((b.pos[i], b.pos[j], b.pos[k]))
            cache[m.name] = np.array(out, np.float32).reshape(-1, 3, 3)
        return cache[m.name]

    parts, placed = [], 0
    for c in comps:
        m = models.get(c.name.lower())
        if m is not None:
            parts.append(model_tris(m))
    for inst in insts:
        m = models.get(inst.type.lower())
        if m is None or m.radius < 10.0:
            continue
        t = model_tris(m)
        if len(t):
            parts.append(t @ np.array(inst.rot, np.float32) + np.array(inst.pos, np.float32))
            placed += 1
    tris = np.concatenate([p for p in parts if len(p)])
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    ln = np.linalg.norm(n, axis=1)
    ok = ln > 1e-6
    tris, n, ln = tris[ok], n[ok], ln[ok]
    flat = np.abs(n[:, 1]) > 0.8 * ln
    vert = np.abs(n[:, 1]) < 0.3 * ln

    def samples(sel):
        """(points, owner index) on a ~1 m barycentric lattice of each triangle."""
        area = np.linalg.norm(np.cross(sel[:, 1] - sel[:, 0], sel[:, 2] - sel[:, 0]), axis=1) / 2
        steps = np.clip(np.ceil(np.sqrt(area)).astype(np.int32), 1, 64)
        for s in np.unique(steps):
            idx = np.nonzero(steps == s)[0]
            u, v = np.meshgrid(np.arange(s + 1), np.arange(s + 1), indexing='ij')
            keep = (u + v) <= s
            u, v = u[keep] / s, v[keep] / s
            t = sel[idx]
            pts = t[:, None, 0] + u[None, :, None] * (t[:, None, 1] - t[:, None, 0]) + \
                v[None, :, None] * (t[:, None, 2] - t[:, None, 0])
            yield pts.reshape(-1, 3), np.repeat(idx, len(u))

    def cells(pts):
        i = ((pts[:, 0] - g.x0) / CELL).astype(np.int64)
        j = ((pts[:, 2] - g.z0) / CELL).astype(np.int64)
        inb = (i >= 0) & (i < g.nx) & (j >= 0) & (j < g.nz)
        return i, j, inb

    roof = np.full((g.nx, g.nz), -np.inf, np.float32)
    for pts, _ in samples(tris[flat]):
        i, j, inb = cells(pts)
        np.maximum.at(roof, (i[inb], j[inb]), pts[inb, 1])
    facade = np.zeros((g.nx, g.nz), bool)
    top = np.full((g.nx, g.nz), -np.inf, np.float32)
    vt = tris[vert]
    vmin, vmax = vt[:, :, 1].min(axis=1), vt[:, :, 1].max(axis=1)
    for pts, owner in samples(vt):
        i, j, inb = cells(pts)
        i, j, owner = i[inb], j[inb], owner[inb]
        np.maximum.at(top, (i, j), vmax[owner])
        grounded = (vmin[owner] <= local[i, j] + 1.5) & (vmax[owner] >= local[i, j] + 2.5)
        facade[i[grounded], j[grounded]] = True
    print('rendered: %d flat / %d vertical triangles (%d components, %d instances)' % (
        int(flat.sum()), int(vert.sum()), len(comps), placed))
    return roof, facade, top


def build(city, png=None):
    floors, walls = polygons(city)
    g = Grid(floors, walls)
    print('%s: %d floor / %d wall polygons, grid %dx%d (%.0f m)' % (city, len(floors), len(walls), g.nx, g.nz, CELL))
    low = np.full((g.nx, g.nz), np.inf, np.float32)    # lowest collision floor over the cell centre
    for poly in floors:
        i0, j0 = g.cell(min(p[0] for p in poly), min(p[2] for p in poly))
        i1, j1 = g.cell(max(p[0] for p in poly), max(p[2] for p in poly))
        for i in range(max(i0, 0), min(i1, g.nx - 1) + 1):
            for j in range(max(j0, 0), min(j1, g.nz - 1) + 1):
                x, z = g.centre(i, j)
                if point_in(poly, x, z):
                    low[i, j] = min(low[i, j], floor_y(poly, x, z))
    has = np.isfinite(low)
    # local ground: the lowest floor within ~20 m
    r = int(20 / CELL)
    pad = np.pad(np.where(has, low, np.inf), r, constant_values=np.inf)
    local = np.full((g.nx, g.nz), np.inf, np.float32)
    for di in range(-r, r + 1, 2):
        for dj in range(-r, r + 1, 2):
            local = np.minimum(local, pad[r + di:r + di + g.nx, r + dj:r + dj + g.nz])
    local = np.where(np.isfinite(local), local, 0.0)
    # blockers: collision walls and drawn facades that reach street level
    blocked = np.zeros((g.nx, g.nz), bool)
    walltop = np.full((g.nx, g.nz), -np.inf, np.float32)
    for poly in walls:
        ymin, ymax = min(p[1] for p in poly), max(p[1] for p in poly)
        a = min(poly, key=lambda p: (p[0], p[2]))
        b = max(poly, key=lambda p: (p[0], p[2]))
        n = max(1, int(math.dist((a[0], a[2]), (b[0], b[2])) / (CELL / 3)))
        for k in range(n + 1):
            i, j = g.cell(a[0] + (b[0] - a[0]) * k / n, a[2] + (b[2] - a[2]) * k / n)
            if 0 <= i < g.nx and 0 <= j < g.nz and ymin <= local[i, j] + 1.5 and ymax >= local[i, j] + 2.5:
                blocked[i, j] = True
                walltop[i, j] = max(walltop[i, j], ymax)
    roof, facade, ftop = rendered(city, g, local)
    # the flood below uses collision walls only: a drawn facade is no barrier to
    # a car (the player drives where the aib has no lane: covered passages,
    # yards); facades give heights and bound the void
    walltop = np.maximum(walltop, np.where(facade, ftop, -np.inf))
    # flood from the roads (aib car lanes and pedestrian rails), over floor only:
    # MC2 blocks are one flat floor under walls, so a floor proves nothing, and
    # nothing drives over the void
    street = np.zeros_like(blocked)
    for (ax, ay, az), (bx, by, bz) in aib_segments(city):
        n = max(1, int(math.hypot(bx - ax, bz - az) / (CELL / 2)))
        for k in range(n + 1):
            i, j = g.cell(ax + (bx - ax) * k / n, az + (bz - az) * k / n)
            if 0 <= i < g.nx and 0 <= j < g.nz and not blocked[i, j] and has[i, j]:
                street[i, j] = True
    reach = street.copy()
    todo = collections.deque(zip(*np.nonzero(street)))
    while todo:
        i, j = todo.popleft()
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            a, b = i + di, j + dj
            if 0 <= a < g.nx and 0 <= b < g.nz and not reach[a, b] and not blocked[a, b] and has[a, b]:
                reach[a, b] = True
                todo.append((a, b))
    enclosed = ~reach & ~blocked
    # a roof counts only where nothing drivable is under it (a floor well below
    # the roof is a passage or a garage, not a solid building)
    roofed = np.isfinite(roof) & (roof - local >= MIN_HEIGHT) & (~has | (low >= roof - 2.0))
    blocked |= facade
    # regions of enclosed cells: a small one away from the map edge is a building
    # (its height = the walls around it); anywhere, a cell under a drawn roof well
    # above the street is solid up to that roof (hills, roofed buildings)
    label = np.full((g.nx, g.nz), -1, np.int32)
    walled = np.zeros_like(blocked)
    nreg = nwall = 0
    for i, j in zip(*np.nonzero(enclosed)):
        if label[i, j] >= 0:
            continue
        cells, q, border = [], [(i, j)], False
        label[i, j] = nreg
        while q:
            a, b = q.pop()
            cells.append((a, b))
            for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                c, e = a + da, b + db
                if not (0 <= c < g.nx and 0 <= e < g.nz):
                    border = True
                elif enclosed[c, e] and label[c, e] < 0:
                    label[c, e] = nreg
                    q.append((c, e))
        nreg += 1
        if not border and len(cells) * CELL * CELL <= MAX_REGION:
            ci, cj = zip(*cells)
            walled[list(ci), list(cj)] = True
            nwall += 1
    solid = enclosed & (walled | roofed)
    # a walled cell's height: the top of its nearest wall (multi-source BFS from
    # the wall cells, through walled cells only) - a low fence nearby must not
    # cut the building next to it, and a far wall must not lift it
    # The void (no floor at all) behind MC2's facades is where the rest of those
    # buildings would be; it is often joined to the void around the map, so it
    # never counts as walled. Nothing drives or is drawn there: a void cell up to
    # VOID_REACH m from a wall or facade is solid up to that wall's top.
    void = enclosed & ~has
    near_top = np.full((g.nx, g.nz), -np.inf, np.float32)
    dist = np.full((g.nx, g.nz), 1 << 30, np.int32)
    todo = collections.deque()
    for i, j in zip(*np.nonzero(blocked & np.isfinite(walltop))):
        near_top[i, j] = walltop[i, j]
        dist[i, j] = 0
        todo.append((i, j))
    reach_cells = int(VOID_REACH / CELL)
    while todo:
        i, j = todo.popleft()
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            a, b = i + di, j + dj
            if not (0 <= a < g.nx and 0 <= b < g.nz) or np.isfinite(near_top[a, b]):
                continue
            if walled[a, b] or (void[a, b] and dist[i, j] < reach_cells):
                near_top[a, b] = near_top[i, j]
                dist[a, b] = dist[i, j] + 1
                todo.append((a, b))
    solid |= void & np.isfinite(near_top)
    walled = walled | (void & np.isfinite(near_top))
    print('enclosed regions %d, walled %d; solid cells %d (walled %d, roofed %d)' % (
        nreg, nwall, int(solid.sum()), int((solid & walled).sum()), int((solid & roofed).sum())))
    if os.environ.get('OCC_DEBUG'):
        np.savez_compressed(os.environ['OCC_DEBUG'], solid=solid, walled=walled, roofed=roofed, roof=roof,
                            near_top=near_top, local=local, blocked=blocked, walltop=walltop, reach=reach, has=has, facade=facade, low=low,
                            x0=g.x0, z0=g.z0)
    # per cell: the drawn roof, or for a walled cell the top of its nearest wall;
    # only cells standing MIN_HEIGHT above the street go into boxes
    cell_top = np.maximum(np.where(roofed, roof, -np.inf), np.where(walled, near_top, -np.inf))
    solid &= np.isfinite(cell_top) & (cell_top - local >= MIN_HEIGHT)
    # rectangles, largest first, per connected piece of `solid`
    boxes = []
    seen = np.zeros_like(solid)
    for i, j in zip(*np.nonzero(solid)):
        if seen[i, j]:
            continue
        cells, q = [], [(i, j)]
        seen[i, j] = True
        while q:
            a, b = q.pop()
            cells.append((a, b))
            for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                c, e = a + da, b + db
                if 0 <= c < g.nx and 0 <= e < g.nz and solid[c, e] and not seen[c, e]:
                    seen[c, e] = True
                    q.append((c, e))
        if len(cells) * CELL * CELL < MIN_AREA:
            continue
        ci = np.array([c[0] for c in cells]); cj = np.array([c[1] for c in cells])
        i0, j0 = ci.min(), cj.min()
        m = np.zeros((ci.max() - i0 + 1, cj.max() - j0 + 1), bool)
        m[ci - i0, cj - j0] = True
        while True:
            best = largest_rectangle(m)
            if best is None or best[0] * CELL * CELL < MIN_AREA:
                break
            area, r0, c0, h, w = best
            m[r0:r0 + h, c0:c0 + w] = False
            a0, b0 = i0 + r0, j0 + c0
            sl = (slice(a0, a0 + h), slice(b0, b0 + w))
            rtop = float(cell_top[sl].min())     # the lowest cell of the rectangle
            base = float(np.max(local[sl]))
            if not np.isfinite(rtop) or rtop - base < MIN_HEIGHT:
                continue
            x0, z0 = g.x0 + a0 * CELL, g.z0 + b0 * CELL
            # shrink by half a cell: the rectangle edge may sit on a wall cell's border
            boxes.append((x0 + CELL / 2, z0 + CELL / 2, x0 + h * CELL - CELL / 2, z0 + w * CELL - CELL / 2,
                          rtop - TOP_MARGIN, area * CELL * CELL))
    # safety: no box may touch a lane (a car or a pedestrian can be there)
    lane = [(ax + (bx - ax) * k / 8, az + (bz - az) * k / 8)
            for (ax, ay, az), (bx, by, bz) in aib_segments(city) for k in range(9)]
    lane = np.array(lane, np.float32)
    keep = []
    for b in boxes:
        x0, z0, x1, z1 = b[0] - 2, b[1] - 2, b[2] + 2, b[3] + 2
        if not np.any((lane[:, 0] >= x0) & (lane[:, 0] <= x1) & (lane[:, 1] >= z0) & (lane[:, 1] <= z1)):
            keep.append(b)
    print('boxes touching a lane dropped: %d of %d' % (len(boxes) - len(keep), len(boxes)))
    boxes = keep
    boxes.sort(key=lambda b: -b[5])
    boxes = boxes[:MAX_BOXES]
    print('boxes: %d (%.0f m2), smallest %.0f m2' % (len(boxes), sum(b[5] for b in boxes), boxes[-1][5] if boxes else 0))
    if png:
        from PIL import Image
        img = np.zeros((g.nz, g.nx, 3), np.uint8)
        img[has.T] = (60, 60, 60)
        img[blocked.T] = (200, 40, 40)
        img[solid.T] = (90, 40, 120)
        for x0, z0, x1, z1, top, area in boxes:
            i0, j0 = g.cell(x0, z0)
            i1, j1 = g.cell(x1, z1)
            img[j0:j1 + 1, i0:i1 + 1] = (40, 200, 90)
        Image.fromarray(img[::-1]).save(png)
        print('map ->', png)
    return boxes, plane_quads(city)


BOX_TOPOLOGY = """edges: 12
0 1
2 3
4 5
6 7
0 2
1 3
2 4
3 5
4 6
5 7
6 0
7 1
faces: 6
faceIndices: 4
ignoreFace: 0
2 3 1 0
edgeIndices: 4
1 5 0 4
faceIndices: 4
ignoreFace: 0
4 5 3 2
edgeIndices: 4
2 7 1 6
faceIndices: 4
ignoreFace: 0
6 7 5 4
edgeIndices: 4
3 9 2 8
faceIndices: 4
ignoreFace: 0
0 1 7 6
edgeIndices: 4
0 11 3 10
faceIndices: 4
ignoreFace: 0
3 5 7 1
edgeIndices: 4
7 9 11 5
faceIndices: 4
ignoreFace: 0
4 2 0 6
edgeIndices: 4
6 4 10 8
"""


def write(path, boxes, planes):
    """Format 3 as the retail files: topology of atlanta's box_0 (vertex order
    A lo, B lo, A hi, B hi, D hi, C hi, D lo, C lo for a footprint A B C D with
    the same winding), then MC2's quads as plane occluders."""
    out = ['version: 3', 'num_geoms: %d' % len(boxes), 'num_primitives: %d' % len(planes), '']
    bottom = -100.0
    for n, (x0, z0, x1, z1, top, area) in enumerate(boxes):
        # footprint winding as atlanta box_0: A=(x0,z1) B=(x1,z1) C=(x1,z0) D=(x0,z0)
        A, B, C, D = (x0, z1), (x1, z1), (x1, z0), (x0, z0)
        cx, cy, cz = (x0 + x1) / 2, (bottom + top) / 2, (z0 + z1) / 2
        rad = math.sqrt(((x1 - x0) / 2) ** 2 + ((top - bottom) / 2) ** 2 + ((z1 - z0) / 2) ** 2)
        out.append(';|occluders|boxOccluders|box_%d\t[%d]' % (n, n))
        out.append('verts: 8')
        out.append('activate: 40')
        out.append('sphere: %.7g %.7g %.7g %.7g' % (cx, cy, cz, rad))
        for (x, z), y in ((A, bottom), (B, bottom), (A, top), (B, top), (D, top), (C, top), (D, bottom), (C, bottom)):
            out.append('%.7g\t%.7g\t%.7g' % (x, y, z))
        out.append(BOX_TOPOLOGY)
    for n, quad in enumerate(planes):
        out.append(';|occluders|planeOccluders|plane%d\t[%d]' % (n, n))
        out.append('verts: 4')
        out.append('activate: 0')
        for p in quad:
            out.append('%.8g %.8g %.8g' % p)
        out.append('')
    open(path, 'w', newline='\n').write('\n'.join(out) + '\n')
    print('wrote', path)


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--city', default='losangeles')
    ap.add_argument('--out', required=True)
    ap.add_argument('--png')
    ap.add_argument('--planes', action='store_true',
                    help="also write MC2's own occluder quads as plane occluders (untested in MC3)")
    a = ap.parse_args()
    boxes, planes = build(a.city, a.png)
    write(a.out, boxes, planes if a.planes else [])
    return 0


if __name__ == '__main__':
    sys.exit(main())
