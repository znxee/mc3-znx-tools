"""
mc3_hood_fill.py - replace every unique mesh of ONE hood of an MC3 PS2 city

Step towards importing a whole neighbourhood. A city is seven hoods and the hood
table hangs off MapRoot+0x14 (count) and +0x18 (pointer); each 20-byte entry is

    +00 -> a 176-byte record whose first bytes are the hood NAME, inline
    +04  unique count      +08 -> unique component array (28 bytes each)
    +0C  instance count    +10 -> instance array (96 bytes each)

and the name matches the prefix on that hood's meshes exactly. In atlanta:

    hood 0 bh (51 uniq)   1 dt (183)   2 fwy (75)   3 lf (90)
    hood 4 mt (155)       5 ug (43)    6 we (54)

`ug` is the smallest and it is underground, so a failed experiment does not
disturb anything the player can see. That is the one to start with.

Every replacement reuses `mc2_to_mc3.py`: nothing already in the file moves, the
new geometry and block tables are APPENDED and only the target mesh's two
pointers are rewritten. The group COUNT of each mesh is preserved - the material
table carries one shader index per group, and collapsing the groups makes the
game read past the end of it and hang.

Do this in steps and boot between them. `--limit N` stops after N meshes, so a
black screen can be bisected instead of guessed at: the loader fails silently and
the main thread spins on a `while(1);` left over from a stripped assert, which
looks exactly like a frozen emulator.

    python mc3_hood_fill.py CITY.pck --hood ug --xmod M.xmod --limit 1 --out-path out.pck
    python mc3_hood_fill.py CITY.pck --hood ug --xmod M.xmod --out-path out.pck
"""

import argparse
import importlib.util
import io
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SPEC = importlib.util.spec_from_file_location(
    'mc2_to_mc3', os.path.join(_HERE, 'experimental', 'mc2_to_mc3.py'))
mc2 = importlib.util.module_from_spec(_SPEC)
sys.modules['mc2_to_mc3'] = mc2
_SPEC.loader.exec_module(mc2)
_VP_SPEC = importlib.util.spec_from_file_location(
    'mc3_vifpos', os.path.join(_HERE, 'mc3_vifpos.py'))
vifpos = importlib.util.module_from_spec(_VP_SPEC)
_VP_SPEC.loader.exec_module(vifpos)


def hoods(p):
    """[(name, uniq_count, uniq_array, inst_count, inst_array)] in file order."""
    nh, hp = p.U(0x80 + 0x14), p.U(0x80 + 0x18)
    hb = p.F(hp)
    out = []
    for h in range(nh):
        e = hb + 20 * h
        rec = p.F(p.U(e))
        name = bytes(p.data[rec:rec + 32]).split(b'\0')[0].decode('latin1', 'ignore')
        out.append((name,) + struct.unpack_from('<4I', p.data, e + 4))
    return out


def hood_uniques(p, idx):
    """[(name, model_offset)] of the meshes owned by that hood's unique array.

    The unique components are the hood's OWN geometry; the instance array is
    mostly `a_inst_*` props shared with every other hood, so filling those would
    change the whole city instead of one neighbourhood.
    """
    _name, nu, au, _ni, _ai = hoods(p)[idx]
    if not p.inside(p.F(au)):        # inside() takes a FILE offset, not a VA
        return []
    ab = p.F(au)
    out, seen_one = [], set()
    for k in range(nu):
        comp = ab + 28 * k
        dp = p.U(comp + 0x0C)
        if not p.inside(p.F(dp)):
            continue
        cd = p.F(dp)
        nm = bytes(p.data[cd + 0x70:cd + 0xB0]).split(b'\0')[0].decode('latin1', 'ignore')
        # Go to the mesh through the component, NOT by name. The name here
        # carries a `#geomN` suffix (`a_ug_underground_01x#geom1`) while the
        # model object stores it without one, so a name lookup finds nothing.
        #
        # And read ALL TEN slots of `buffers[2][5]` at +0x3C, not just the first.
        # They hold the LOD variants and they are separate meshes: of the 43
        # unique components of atlanta's `ug`, 20 have more than one slot filled
        # (13 have two, 6 have three, one has five), and some - `#geom23`,
        # `#geom28` - have NOTHING in slot 0. Replacing only slot 0 leaves those
        # components completely untouched and leaves every other component's
        # distant LOD showing the ORIGINAL geometry at the ORIGINAL place, which
        # in game looks like pieces of the old location following you around.
        meshes = []
        for slot in range(10):
            mp = p.U(cd + 0x3C + 4 * slot)
            if not p.inside(p.F(mp)):
                continue
            mesh = p.F(mp)
            if mesh not in seen_one:
                seen_one.add(mesh)
                meshes.append(mesh)
        if meshes:
            out.append((nm or 'comp@%06X' % comp, meshes, comp, cd))
    return out


def read_hood_mc2(path):
    """[(name, emin, emax)] of the unique components of an MC2 .hood.

    The file is plain text and the parser in the game (`sub_5248D0` of the
    unprotected `testapp.exe`, the twin of the packed `mc2.exe`) reads exactly
    three things per unique component: `name:`, `emin`, `emax`. No parent, no
    transform, no chain - so there IS no hierarchy, and the geometry is already
    in world coordinates. Verified: the wfargo .xmod spans 640.000..800.000 in x,
    byte for byte the emin/emax the .hood declares.

    That is what makes a coherent port possible: the models already know where
    they stand relative to each other, so the whole set can move as one rigid
    block instead of each mesh being placed on its own.
    """
    out = []
    current = None
    with io.open(path, encoding='latin1') as f:
        for line in f:
            t = line.split()
            if not t:
                continue
            if t[0] == 'unique_component':
                current = {}
            elif current is None:
                continue
            elif t[0] == 'name:' and len(t) >= 2:
                current['name'] = t[1]
            elif t[0] in ('emin', 'emax') and len(t) >= 4:
                current[t[0]] = tuple(float(x) for x in t[1:4])
            elif t[0] == '}':
                if current.get('name') and 'emin' in current and 'emax' in current:
                    out.append((current['name'], current['emin'], current['emax']))
                current = None
    return out


def read_cc(name, folder):
    """The component's `.cc` manifest: (passes of lod 0, bounding sphere).

    Next to every `.xmod` sits a small ASCII manifest that says which passes each
    LOD actually draws, plus the sphere in world coordinates:

        inst_cpv: 1
        bounding_sphere: 720.000000 97.649986 122.500000 136.610764
        lod 0 {
            main
            refl
        }
        lod 1 {
        }

    This is the authority on what a component is made of. Guessing `_main` works
    for buildings and silently loses every road: `l_downtown_rd_01` has no main
    at all, its lod 0 is `refl` alone, because in MC2 the road IS the reflective
    surface. Nine of downtown's thirty-four components are roads and junctions.
    """
    p = os.path.join(folder, name + '.cc')
    if not os.path.isfile(p):
        return None, None
    passes, sphere, inside = [], None, False
    for text_line in io.open(p, encoding='latin1'):
        t = text_line.strip()
        if t.startswith('bounding_sphere:'):
            try:
                sphere = tuple(float(x) for x in t.split(':', 1)[1].split())
            except ValueError:
                pass
        elif t.startswith('lod '):
            inside = t.split()[1] == '0'
        elif t.startswith('}'):
            inside = False
        elif inside and t and t != '{':
            passes.append(t)
    return passes, sphere


def xmod_de(name, folder):
    """The .xmod that holds a .hood component's geometry, or None.

    The .hood names it `l_downtown_rd_38_x#geom` while the file on disk is
    `l_downtown_rd_38_x#geom_0_main.xmod`. Which suffix carries the geometry is
    not fixed - the `.cc` manifest decides - so ask it first and only fall back
    to the `_main` guess when there is no manifest.
    """
    passes, _esf = read_cc(name, folder)
    for pass_name in (passes or ()):
        for i in range(3):
            p = os.path.join(folder, '%s_%d_%s.xmod' % (name, i, pass_name))
            if os.path.isfile(p):
                return p
    # The MC2 export uses `.mod` and MC3's `.xmod`, the same text format (the log
    # itself records that the game's reader is the same). Accept both.
    for suf in ('_0_main.xmod', '_1_main.xmod', '_2_main.xmod',
                '_0_main.mod', '_1_main.mod', '_2_main.mod'):
        p = os.path.join(folder, name + suf)
        if os.path.isfile(p):
            return p
    return None


def xmod_radius(path):
    """Bounding radius of an .xmod, read cheaply.

    Only the `v` lines are parsed - no adjuncts, no strips. Converting all 815
    models of the MC2 city just to measure them takes minutes; this takes a
    second, and the full conversion then runs on the handful actually chosen.
    """
    lo = [1e30, 1e30, 1e30]
    hi = [-1e30, -1e30, -1e30]
    with io.open(path, encoding='latin1') as f:
        for line in f:
            # the lines are indented, so match the TOKEN and not the raw prefix
            q = line.split()
            if q and q[0] == 'v' and len(q) >= 4:
                try:
                    v = (float(q[1]), float(q[2]), float(q[3]))
                except ValueError:
                    continue
                for a in range(3):
                    lo[a] = min(lo[a], v[a])
                    hi[a] = max(hi[a], v[a])
    if lo[0] > hi[0]:
        return 0.0
    # Measure from the SAME anchor the converter uses - centre in x/z, base in
    # y - or the number is not comparable with what comes out the other end.
    # Measuring from the model's own origin made a 164-radius target get a model
    # that converted to 94.
    anc = ((lo[0] + hi[0]) / 2.0, lo[1], (lo[2] + hi[2]) / 2.0)
    return max(((x - anc[0]) ** 2 + (y - anc[1]) ** 2 + (z - anc[2]) ** 2) ** 0.5
               for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2]))


def catalogue(folder):
    """[(radius, path)] of the `_main` models, sorted by radius.

    `_refl` and `_emissive` are companion passes of the same object, not
    standalone geometry, so only `_main` is offered.
    """
    out = []
    for f in sorted(os.listdir(folder)):
        if f.endswith(('.xmod', '.mod')) and '_main' in f:
            r = xmod_radius(os.path.join(folder, f))
            if r > 0.1:
                out.append((r, os.path.join(folder, f)))
    out.sort()
    return out


def cell_box(grid, xw, zw, radius):
    """The 4 bytes of the tile box, clamped INSIDE the grid the file declares.

    Clamping to 0..255 is not enough, and that was the difference between
    "vanishes sometimes" and "vanishes always". `mcCity::CellIndexFromPos`
    (0x257BE8) returns -1 for any cell with `rel_x < 0`, `rel_x >= MapRoot+0x26C`,
    `rel_z < 0` or `rel_z >= MapRoot+0x270`, and the caller - `CellGrid::Insert`
    (0x259658) - returns without inserting. Outside the grid is not aggressive
    culling: it is invisible, with no crash and no warning.

    Retail never falls into that case. Measured on atlanta, tokyo and detroit, W
    and H come exactly from the extents and ZERO objects have a tile box outside
    them. An object that escapes by a little is better clamped to the edge than lost.
    """
    cx, ox, cz, oz = grid[:4]
    w = (grid[4] if len(grid) > 4 and grid[4] else 256)
    h = (grid[5] if len(grid) > 5 and grid[5] else 256)

    def range_(v0, v1, base, step, n):
        a = int((v0 - base) // step)
        b = int((v1 - base) // step)
        return max(0, min(n - 1, a)), max(0, min(n - 1, b))

    x0, x1 = range_(xw - radius, xw + radius, ox, cx, w)
    z0, z1 = range_(zw - radius, zw + radius, oz, cz, h)
    return bytes((x0, x1, z0, z1))


def reposition(p, comp, cd, radius, grid):
    """Resize the culling of a unique component after its geometry changed.

    A unique component has no transform - it is 28 bytes - and what places it is
    the bound sphere on its ComponentData: **+0x2C is the centre in WORLD
    coordinates and +0x38 the radius**, while the vertices themselves are local
    and centred on the origin. Verified on atlanta's `ug` hood: geom1 sits at
    (-324.74, 32.84, 783.94) with radius 52.45, and 52.45 is exactly the extent
    of its own local vertices.

    The centre is left ALONE - that is the placement, and the point of a hood
    fill is to change what is drawn, not where. Only the radius and the tile box
    move, and they have to: a tunnel piece is 52 units across, a replacement
    building is 195 tall, and leaving the old radius means the culling drops it
    before the camera ever gets close. That is why the first fill loaded
    perfectly and showed nothing.
    """
    cxw, cyw, czw = struct.unpack_from('<3f', p.data, cd + 0x2C)
    struct.pack_into('<f', p.data, cd + 0x38, radius)      # ComponentData radius
    struct.pack_into('<f', p.data, comp + 0x18, radius)    # the component's own
    if grid:
        p.data[comp + 0x14:comp + 0x18] = cell_box(grid, cxw, czw, radius)
    return (cxw, cyw, czw)


def aabb_local(p, mesh):
    """(lo, hi) from the packet header-selected VIF position stream."""
    ngrp = struct.unpack_from('<H', p.data, mesh + 8)[0]
    if not p.inside(p.F(p.U(mesh + 0x10))):
        return None
    gt = p.F(p.U(mesh + 0x10))
    lo = [1e30, 1e30, 1e30]
    hi = [-1e30, -1e30, -1e30]
    for gi in range(ngrp):
        lp, cnt, _ = struct.unpack_from('<IHH', p.data, gt + 8 * gi)
        if not p.inside(p.F(lp)) or cnt > 4096:
            break
        for bi in range(cnt):
            bp, qw, nv = struct.unpack_from('<IHH', p.data, p.F(lp) + 8 * bi)
            if not p.inside(p.F(bp)) or not (3 <= nv <= 256):
                continue
            b = p.F(bp)
            try:
                pts = vifpos.decode_positions(bytes(p.data[b:b + 16 * qw]))
            except ValueError:
                continue
            if len(pts) != nv:
                continue
            for xyz in pts:
                for a, v in enumerate(xyz):
                    lo[a] = min(lo[a], v)
                    hi[a] = max(hi[a], v)
    return (lo, hi) if lo[0] < hi[0] else None


def centre_on_origin(blocks):
    """Re-anchor the model on its own AABB centre, and say where that centre was.

    This sidesteps an ambiguity instead of resolving it. `cd+0x2C` is either the
    translation of the model origin (A) or the centre of the bound sphere (B),
    and the two readings differ by exactly the local AABB centre. Vanilla city
    meshes are mostly built centred on their own origin, which is why both
    readings fit the stock data equally well - a tile-box test over all 479
    unique meshes scores A at 2.65 units of error and B at 2.43, far too close to
    call.

    Centre the imported model the same way and the local centre becomes zero, so
    A and B COINCIDE and the question stops mattering. It also removes the big
    error: anchoring base-at-y=0, as the converter does, puts the local centre at
    half the model height - 97 units on the wfargo tower - and that is what one
    of the two readings would be off by.
    """
    pts = [c for pos, _u, _nr, _o in blocks for c in pos]
    lo = [min(q[a] for q in pts) for a in range(3)]
    hi = [max(q[a] for q in pts) for a in range(3)]
    cen = [(lo[a] + hi[a]) / 2.0 for a in range(3)]
    outside = [([(q[0] - cen[0], q[1] - cen[1], q[2] - cen[2]) for q in pos], u, nr, o)
            for pos, u, nr, o in blocks]
    return outside, cen


def align_to(blocks, target):
    """Shift the new geometry onto the footprint the old geometry occupied.

    `cd+0x2C` is where the model's ORIGIN goes - confirmed because the stored
    radius matches the distance from the origin to the furthest vertex, not from
    the local AABB centre (geom21: radius 90.3, origin 90.3, centre 80.0). But
    the old geometry was not centred on that origin: `#geom22` sits 81 units away
    in z and `#geom24` 45 in x. The converter anchors the imported model centred
    in x/z with its base at y=0, so dropping it in unshifted lands it on the old
    ORIGIN instead of where the old building actually stood - off by up to about
    fifty units, which is what shows up in game as "close but not right".

    So the new model is moved to put its footprint centre over the old footprint
    centre, and its base at the old base.
    """
    lo, hi = target
    dx = (lo[0] + hi[0]) / 2.0
    dy = lo[1]
    dz = (lo[2] + hi[2]) / 2.0
    if abs(dx) < 0.05 and abs(dy) < 0.05 and abs(dz) < 0.05:
        return blocks, (0.0, 0.0, 0.0)
    outside = [([(q[0] + dx, q[1] + dy, q[2] + dz) for q in pos], u, nr, o)
            for pos, u, nr, o in blocks]
    return outside, (dx, dy, dz)


def recentre_sphere(p, cd, old_target, new_lo, new_hi):
    """Rewrite cd+0x2C under the OTHER reading of what that field means.

    Two readings survive the evidence and they disagree once the geometry is
    replaced:

    A. `cd+0x2C` is the TRANSLATION of the model origin. Then replacing geometry
       needs no change there, and the new model just has to be shifted onto the
       old footprint - that is what `alinha()` does.
    B. `cd+0x2C` is the CENTRE OF THE BOUND SPHERE, i.e. translation plus the
       local AABB centre. Then it has to be rewritten whenever the geometry
       changes shape, or the object is drawn off by the difference between the
       old and new local centres.

    They are indistinguishable on stock data because most city meshes are built
    centred on their own origin, so the two coincide. The radius almost decides
    it - it matches the distance from the ORIGIN on most meshes, which argues for
    A - but not on all of them (`#geom22` stores 148 against 133 from the origin,
    `a_bh_blk_04x#geom` stores 108 against 73), so it is not conclusive.

    Under B: translation = old_centre - old_local_centre, and the new centre is
    that translation plus the new local centre.
    """
    lo, hi = old_target
    old_centre = [(lo[a] + hi[a]) / 2.0 for a in range(3)]
    new_centre = [(new_lo[a] + new_hi[a]) / 2.0 for a in range(3)]
    c = list(struct.unpack_from('<3f', p.data, cd + 0x2C))
    new = [c[a] - old_centre[a] + new_centre[a] for a in range(3)]
    struct.pack_into('<3f', p.data, cd + 0x2C, *new)
    return tuple(new[a] - c[a] for a in range(3))


def unify_material(p, mesh, index):
    """Point every group of a mesh at ONE material index.

    Not the same thing as "an untextured shader", and that distinction matters:
    there is no untextured shader to point at. The city names only four -
    road_reflector, water, city_specular and moon - and across every .pck the
    whole vocabulary is 24 names, none of them flat/unlit/vertex-colour; ordinary
    city geometry goes through an unnamed default path, and the named ones are
    MORE specialised, not less.

    What this does instead is make the look uniform. A replaced mesh inherits the
    original's per-group material list, so a 13-group tunnel piece renders the
    imported model in 13 different materials at once, which is unreadable.
    Forcing one index - combined with a constant UV - gives one material and one
    texel everywhere, which is flat enough to judge shape by.

    The index must be one the file already uses. 3 is the most common in
    atlanta's own geometry (109 groups), so it is a safe pick.
    """
    ngrp = struct.unpack_from('<H', p.data, mesh + 8)[0]
    mt = p.U(mesh + 0x0C)
    if not p.inside(p.F(mt)):
        return 0
    mo = p.F(mt)
    for gi in range(ngrp):
        struct.pack_into('<H', p.data, mo + 2 * gi, index)
    return ngrp


def swap(p, mesh, blocks):
    """Swap one mesh's geometry. Returns (blocks written, groups) or None.

    The new geometry is APPENDED and the mesh is repointed - nothing moves, which
    is what makes the swap safe. The price is that the old one stays in the file.
    Its ranges are noted in `p._dead` BEFORE the swap, so mc3_compact can give
    those bytes back later; without that, porting the whole city almost doubles
    the file and blows the size ceiling of a city .pck.
    """
    ngrp = struct.unpack_from('<H', p.data, mesh + 8)[0]
    if not (1 <= ngrp <= 64):
        return None
    # the ranges this mesh already had become FREE space for the next ones.
    # Nothing is freed before the swap itself reads the sample payload at +0x14.
    # The mesh's WHOLE REGION (tables, lists, packets and payloads) becomes free
    # space for the NEXT ones - never before the swap itself, because
    # emit_mesh_region reads the payload sample from this same mesh's +0x14
    # chain. Freeing only the packets does not work: the emit asks for ONE
    # contiguous region of the whole size, and a loose packet is always smaller.
    dead = []
    try:
        import mc3_compact
        dead = mc3_compact.mesh_region(p, mesh)
    except Exception:
        pass
    n, r = divmod(len(blocks), ngrp)
    cut, k = [], 0
    for i in range(ngrp):
        m = max(1, n + (1 if i < r else 0))
        cut.append(blocks[k:k + m] or [blocks[0]])
        k += m
    if k < len(blocks):
        cut[-1] += blocks[k:]
    sc = mc2.pick_scale([b for g in cut for b in g])
    blobs = [[mc2.make_block(pos, uv, nr, sc, j % 2 == 1)
              for j, (pos, uv, nr, _o) in enumerate(g)] for g in cut]
    grp_at, t14_at = mc2.emit_mesh_region(p, mesh, cut, blobs)
    # SPACE REUSE TURNED OFF. The ranges `mesh_region` returns are not
    # reliable: one of them swallows live structure, and the allocator writes
    # over it. The symptom was a mesh with `+0x08` at 0xCDCD (52685 "groups")
    # and a material table pointing outside the file - that is, a mesh object
    # overwritten by padding.
    #
    # The gain was small anyway (13.88 -> 13.02 MB); what solves the size is not
    # writing the whole piece into every LOD slot.
    if False and dead:
        if not hasattr(p, 'free'):
            p.free = []
        p.free += dead
        p.free.sort(key=lambda x: -x[1])
    struct.pack_into('<I', p.data, mesh + mc2.MESH_BLOCKS, p.V(grp_at))
    struct.pack_into('<I', p.data, mesh + mc2.MESH_PERBLOCK, p.V(t14_at))
    return sum(len(g) for g in cut), ngrp


CATEGORIES = ('road', 'building', 'wall', 'vegetation', 'pole', 'interior', 'other')


def category(name):
    """Which kind of surface a mesh name belongs to.

    Both games name the city with the same family of patterns (`_rd_`, `fwy`,
    `blk`, `_inst_alpha_`, `wall`), so the same rule classifies MC2's source name
    and MC3's target name - which is what allows MEASURING on the target which
    index each category uses instead of guessing.
    """
    n = name.lower()
    # DO NOT "FIX" THIS RULE WITHOUT READING WHAT FOLLOWS.
    #
    # Los Angeles names its crossings `l_inst_2lane_3wayint_g_x`, and the `int_`
    # test matches inside "3wayint_g", so 97 pieces of asphalt fall into
    # 'interior'. That is ugly and I tried to fix it - sending lane/wayint to
    # 'road' and requiring `_int_` for interior. The game hung while loading.
    #
    # The reason is indirect and worth keeping: with the crossings out, the
    # 'interior' category is left with almost no mesh, loses its dominant index
    # (154) and falls on a leftover index - it came out 0. Then
    # `fill_semantic_slots` went to clone a shader into the 7 null slot-0
    # entries, and slot 0 belongs to the INSTANCE class, which the cloner copies
    # with 16 bytes. Endless loading.
    #
    # The wrong classification is cosmetic: a crossing with an interior material
    # still draws. The shader path does not forgive. If this is ever touched,
    # measure the set of gaps FIRST with /tmp/cmp.py and check that no new class
    # came in.
    if '_rd_' in n or 'fwy' in n or 'road' in n or 'ramp' in n:
        return 'road'
    if 'wall' in n or 'fence' in n or 'barrier' in n or 'irongate' in n:
        return 'wall'
    if ('alpha' in n or 'tree' in n or 'bush' in n or 'shrub' in n
            or 'palm' in n or 'planter' in n):
        return 'vegetation'
    if ('light' in n or 'lamp' in n or 'pole' in n or 'sign' in n
            or 'meter' in n or 'hydrant' in n):
        return 'pole'
    if 'blk' in n or 'bld' in n or 'building' in n or 'house' in n:
        return 'building'
    if 'int_' in n or 'interior' in n:
        return 'interior'
    return 'other'


def safe_slots(p):
    """Material indices filled in ALL the shader groups.

    `rmcModel::FixupShaders` (0x2A8A18) dereferences `array[index]` and calls
    the virtual at +24 without checking for null. An empty slot in the group that
    gets used leads to a call through a null pointer - that is what hung when I
    picked index 1002, which in atlanta exists in 2 of the 18 groups.

    Retail does NOT follow this rule - 1041 of the 1046 indices atlanta uses are
    empty in some group - because the original mesh only takes part in the
    passes where its material exists. Since here the material is swapped
    underneath, the only provably safe choice is one that holds in any group.
    There are 5 in atlanta (1, 150, 631, 684, 685) and 15 in tokyo.
    """
    r = 0x80
    ng = p.U(r + 8)
    base = p.F(p.U(r + 0x0C))
    if not ng or not p.inside(base):
        return []
    n0 = struct.unpack_from('<H', p.data, base + 8)[0]
    full = [0] * n0
    for g in range(ng):
        data = p.F(p.U(base + 16 * g + 4))
        if not p.inside(data):
            continue
        for i in range(n0):
            if p.U(data + 4 * i):
                full[i] += 1
    return [i for i, c in enumerate(full) if c == ng]


def materials_by_category(p, safe_only=True):
    """{category: material index}, measured on the target.

    Each category gets the index the target uses most in that category AMONG
    THOSE THAT EXIST IN ALL GROUPS, by default. `safe_only=False` recovers
    retail's original semantic preference; `fill_semantic_slots` uses it to know
    WHICH gaps are worth filling.

    With few safe slots several categories end up sharing an index - in atlanta
    there are 5 - and that is better than a single index for the whole city and
    much better than an index that hangs.
    """
    import collections
    allowed = set(safe_slots(p)) if safe_only else None
    tab = collections.defaultdict(collections.Counter)

    def count(name, meshes):
        c = category(name)
        for m in meshes:
            n = struct.unpack_from('<H', p.data, m + 8)[0]
            mt = p.F(p.U(m + 0x0C))
            if not p.inside(mt):
                continue
            for k in range(n):
                v = struct.unpack_from('<H', p.data, mt + 2 * k)[0]
                if allowed is None or v in allowed:
                    tab[c][v] += 1

    for i in range(len(hoods(p))):
        for nm, meshes, _c, _cd in hood_uniques(p, i):
            count(nm, meshes)
    _slots, kinds = instance_kinds(p)
    for _va, (nm, _cd, meshes) in kinds.items():
        count(nm, meshes)

    candidates = (allowed if allowed is not None else
                  set(v for c in tab.values() for v in c))
    if not candidates:
        return {}
    overall = collections.Counter()
    for c in tab:
        overall.update(tab[c])
    default = overall.most_common(1)[0][0] if overall else sorted(safe)[0]

    # A different category, a different index whenever a safe slot is left: in
    # atlanta there are 5, and using them all separates road, building, wall,
    # vegetation and pole on screen. Without this the categories with no
    # dominant index of their own all fall on the same number and the city is
    # one block again.
    outside = {}
    used_set = set()
    for c in CATEGORIES:
        for v, _n in tab[c].most_common():
            if v not in used_set:
                outside[c] = v
                used_set.add(v)
                break
    # A CATEGORY WITHOUT A MEASUREMENT GETS NO INVENTED INDEX WHEN THE RESULT
    # WILL DRIVE SHADER CLONING.
    #
    # With `safe_only=True` this is the final table: every category needs SOME
    # index, so a leftover is fine. With `safe_only=False` the caller is
    # `fill_semantic_slots`, which will CLONE shaders into the null slots of the
    # returned index - and an index taken from the leftovers stands for nothing.
    # That is how 'interior' became 0, the cloner went to fill 7 slot-0 entries
    # of the INSTANCE class with 16 bytes, and the loading hung.
    if safe_only:
        free = [v for v in sorted(candidates) if v not in used_set]
        for c in CATEGORIES:
            if c in outside:
                continue
            outside[c] = free.pop(0) if free else default
    return outside


# Logical tokens of the Atlanta profile. Tokyo uses the same tokens with +0x488.
_VT_BASIC = 0x007A1EF8
_VT_NAMED = 0x007A1F58
_VT_INSTANCE = 0x007A1FC8
_VT_TEXTURE_NODE = 0x007A20E0
_VT_TEXTURE_INFO = 0x007A2320
_VT_OWNER_16 = 0x007B2318
_VT_OWNER_17 = 0x007B22B8
_SHADER_TOKENS = {_VT_BASIC, _VT_NAMED, _VT_INSTANCE,
                  _VT_OWNER_16, _VT_OWNER_17,
                  _VT_TEXTURE_NODE, _VT_TEXTURE_INFO}


def _token_shader(v):
    """Normalise an Atlanta/Tokyo token to the logical Atlanta value."""
    for shift in (0, 0x488):
        if v - shift in _SHADER_TOKENS:
            return v - shift
    return None


def _cloneable_shader_size(p, src):
    """Bytes belonging to the shader, including reachable inline resources.

    A fixed size cannot be copied. `rmcShaderBasic` can be just a 12-byte header
    pointing outside or 0xB0 bytes with an inline TextureInfo. The owner-placed
    17 used by Atlanta's material 154 has a header, a 0xA0 TextureInfo and a 0x10
    TextureNode, 0xD0 in total. Copying only the class's 0x40 bytes leaves the
    inner pointer aiming at data that was not cloned.
    """
    if not p.inside(src + 8):
        raise ValueError('shader outside the file at %#x' % src)
    vt = _token_shader(p.U(src))
    sub = p.F(p.U(src + 8))
    delta = sub - src if p.inside(sub) else None
    if vt == _VT_BASIC:
        size = 0xB0 if delta == 0x10 else 0x0C
    elif vt == _VT_INSTANCE:
        size = 0x20 if delta == 0x10 else 0x30 if delta == 0x20 else 0x10
    elif vt in (_VT_OWNER_16, _VT_OWNER_17):
        size = 0x40
        # These two fields own this family's inline resources.
        # External resources stay shared and are not part of the copy.
        for rel in (8, 0x0C):
            target = p.F(p.U(src + rel))
            if not p.inside(target) or not src <= target < src + 0x400:
                continue
            kind = _token_shader(p.U(target))
            if kind == _VT_TEXTURE_INFO:
                size = max(size, target - src + 0xA0)
            elif kind == _VT_TEXTURE_NODE:
                size = max(size, target - src + 0x10)
    elif vt == _VT_NAMED:
        size = 0x30
    else:
        raise ValueError('unknown shader class at %#x: %#x'
                         % (src, p.U(src)))
    if src + size > len(p.data):
        raise ValueError('shader truncated at %#x (size %#x)' % (src, size))
    return size


def _clone_shader(p, src):
    """Copy a retail shader and relocate only its internal pointers."""
    size = _cloneable_shader_size(p, src)
    dst = p.append(bytes(p.data[src:src + size]), 16)
    for rel in range(0, size, 4):
        value = p.U(dst + rel)
        target = p.F(value)
        if src <= target < src + size:
            struct.pack_into('<I', p.data, dst + rel,
                             p.V(dst + (target - src)))
    return dst, size


def fill_semantic_slots(p):
    """Make the preferred materials of ALL seven categories safe.

    First measure the semantic choice on the still-retail file, without
    filtering the null slots. For each chosen index, keep every shader that
    already exists and fill only the missing groups with an INDEPENDENT COPY of
    the nearest retail shader of the same index. There is no pointer aliasing
    between slots, no existing byte moves and the growth is a few KiB.

    In Atlanta this fills 30 gaps / 5344 bytes, takes the universal slots from
    5 to 11 and frees exactly:
      road=164, building=1, wall=698, vegetation=45,
      pole=1002, interior=154, other=3.
    """
    chosen = materials_by_category(p, safe_only=False)
    if not chosen:
        return {}
    before = set(safe_slots(p))
    r = 0x80
    ng = p.U(r + 8)
    base = p.F(p.U(r + 0x0C))
    if not ng or not p.inside(base):
        raise ValueError('invalid shader group table')
    descr = []
    for g in range(ng):
        d = base + 16 * g
        arr = p.F(p.U(d + 4))
        n = struct.unpack_from('<H', p.data, d + 8)[0]
        if not p.inside(arr) or arr + 4 * n > len(p.data):
            raise ValueError('invalid shader array in group %d' % g)
        descr.append((arr, n))

    copies = new_bytes = 0
    by_index = {}
    for index in sorted(set(chosen.values())):
        if any(index >= n for _arr, n in descr):
            raise ValueError('semantic material %d outside the table' % index)
        origins = []
        missing_n = []
        for g, (arr, _n) in enumerate(descr):
            va = p.U(arr + 4 * index)
            if va:
                src = p.F(va)
                _cloneable_shader_size(p, src)  # validate before writing
                origins.append((g, src))
            else:
                missing_n.append(g)
        if not origins:
            raise ValueError('semantic material %d has no donor shader'
                             % index)
        by_index[index] = len(missing_n)
        for g in missing_n:
            _sg, src = min(origins, key=lambda q: abs(q[0] - g))
            dst, size = _clone_shader(p, src)
            struct.pack_into('<I', p.data, descr[g][0] + 4 * index, p.V(dst))
            copies += 1
            new_bytes += size

    after = set(safe_slots(p))
    lost_n = set(chosen.values()) - after
    if lost_n:
        raise ValueError('slots continuaram inseguros: %s'
                         % ', '.join(map(str, sorted(lost_n))))
    print('  semantic shaders: %d gaps cloned, +%d bytes; '
          'safe slots %d -> %d'
          % (copies, new_bytes, len(before), len(after)))
    print('  materials freed: %s'
          % ', '.join('%s=%d' % (c, chosen[c]) for c in CATEGORIES))
    return {'materials': chosen, 'gaps': by_index,
            'copies': copies, 'bytes': new_bytes,
            'safe_before': sorted(before), 'safe_after': sorted(after)}


def instance_kinds(p):
    """(slots, kinds) of the target: the 96-byte records and the ComponentData
    they point to.

    An instance does NOT carry geometry: `+0x0C` points to the KIND's
    ComponentData, and it is the kind that has the ten LOD slots at `+0x3C`. In
    atlanta there are 3608 instances for 231 kinds, and none of those kinds is
    among the 651 unique components - they are separate objects.
    """
    slots, kinds = [], {}
    hb = p.F(p.U(0x80 + 0x18))
    for h in range(p.U(0x80 + 0x14)):
        e = hb + 20 * h
        cnt, arr = p.U(e + 0x0C), p.F(p.U(e + 0x10))
        if not p.inside(arr):
            continue
        for k in range(cnt):
            o = arr + 96 * k
            slots.append(o)
            va = p.U(o + 0x0C)
            if va in kinds or not p.inside(p.F(va)):
                continue
            cd = p.F(va)
            meshes = []
            for lod in range(10):
                mp = p.U(cd + 0x3C + 4 * lod)
                if p.inside(p.F(mp)):
                    meshes.append(p.F(mp))
            name = bytes(p.data[cd + 0x70:cd + 0xB0]).split(b'\0')[0]
            kinds[va] = (name.decode('latin1', 'ignore'), cd, meshes)
    return slots, kinds


def free_targets(p, meshes):
    """Put the packets of ALL the meshes that will be swapped into the free-space
    pool, BEFORE the first allocation.

    Freeing only after each swap barely helps: the Los Angeles meshes are larger
    than the donor's, so a freshly freed range rarely fits the next allocation
    and the file grows anyway (13.88 -> 13.67 MB). With the whole pool available
    from the start, the fit improves a lot.

    It is safe: `mesh_packets` reads the block LIST, not the content, and
    `emit_mesh_region` takes its payload sample from the `+0x14` chain, which is
    not in the pool. A mesh can end up pointing at bytes already rewritten
    between the freeing and its own swap, but it will be swapped anyway.
    """
    try:
        import mc3_compact
    except Exception:
        return 0
    if not hasattr(p, 'free'):
        p.free = []
    seen_one = set()
    for m in meshes:
        if m in seen_one:
            continue
        seen_one.add(m)
        p.free += mc3_compact.mesh_region(p, m)
    # largest first: fit the big meshes before slicing up the pool
    p.free.sort(key=lambda x: -x[1])
    return sum(t for _a, t in p.free)


def port_instances(p, place, folder, convert, grid, material=None):
    """Fill the target INSTANCE slots with the MC2 instances.

    THE INSTANCES ARE CHAINED PER KIND, and ignoring that hangs the game.
    Measured on atlanta: of the 3608 records, 3377 have `+0x00` pointing to
    another instance OF THE SAME KIND and none points to a different kind; there
    are 231 chains, one per kind, each with `+0x04 == 0` at the head. `+0x00` is
    the next and `+0x04` the previous.

    The first version of this repointed `+0x0C` freely, treating the 3608 slots
    as a single bank - the chains started mixing kinds and the game stayed in
    endless loading. Here each whole CHAIN gets a single MC2 kind, so the
    chaining stays true.
    """
    import collections
    import mc3_city_build as cb
    lines, _ext = cb.read_place(place)
    insts = [l for l in lines if l['kind'] == 'inst']
    if not insts:
        return 0

    material_table = None if material is not None else materials_by_category(p)
    slots, kinds = instance_kinds(p)
    by_target_kind = collections.defaultdict(list)
    for o in slots:
        by_target_kind[p.U(o + 0x0C)].append(o)
    targets = [(len(v), va) for va, v in by_target_kind.items() if kinds.get(va, (0, 0, []))[2]]
    targets.sort(reverse=True)
    print('  instances: %d slots in %d kind chains' % (len(slots), len(targets)))

    source = collections.Counter(l['name'] for l in insts)
    by_name = collections.defaultdict(list)
    for l in insts:
        by_name[l['name']].append(l)
    order = [n for n, _c in source.most_common()]

    placed = used_set = 0
    ceiling = getattr(p, 'ceiling_bytes', 0)
    cut_kinds = 0
    for i, (n_slots, va) in enumerate(targets):
        if i >= len(order):
            break
        # SIZE BUDGET. The city .pck has a ceiling - 12.08 MB loads, 13.88
        # hangs - and each new instance kind costs a copy of the mesh. The kinds
        # come sorted by how many instances use each one, so cutting at the end
        # of the budget cuts what shows up least.
        if ceiling and len(p.data) > ceiling:
            cut_kinds += 1
            # the cut kind keeps the donor's geometry; if its instances stay
            # where they are, the old city shows up again in the middle of the
            # new one. So they are parked outside the world, with a tiny radius
            # - the mesh stays valid, it is just never seen.
            for o in by_target_kind[va]:
                struct.pack_into('<12f', p.data, o + 0x20,
                                 1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0,
                                 0.0, -100000.0, 0.0)
                struct.pack_into('<f', p.data, o + 0x18, 0.01)
                struct.pack_into('<3f', p.data, o + 0x50, 0.0, -100000.0, 0.0)
            continue
        name = order[i]
        l0 = by_name[name][0]
        fname = ('%s_%s.mod' % (name, l0['lod'])) if l0['part'] == '-' else               ('%s_%s_%s.mod' % (name, l0['lod'], l0['part']))
        pck_path = os.path.join(folder, fname)
        if not os.path.isfile(pck_path):
            continue
        got = convert(pck_path)
        if not got:
            continue
        blocks, _r = got
        blocks, _c = centre_on_origin(blocks)
        _nm, cd, meshes = kinds[va]
        mat = material if material is not None else             (material_table.get(category(name)) if material_table else None)
        ok = False
        for j, mm in enumerate(meshes):
            if swap(p, mm, blocks if j == 0 else blocks[:1]):
                if j == 0:
                    ok = True
            if mat is not None:
                unify_material(p, mm, mat)
        if not ok:
            continue
        pts = [q for pos, _u, _n, _o in blocks for q in pos]
        radius = max((q[0] ** 2 + q[1] ** 2 + q[2] ** 2) ** 0.5 for q in pts)
        struct.pack_into('<f', p.data, cd + 0x38, radius)
        used_set += 1

        # spread over the city, not the first ones: a chain with 20 places for
        # 629 bushes would only show one corner of the map
        queue = by_name[name]
        target_slots = by_target_kind[va]
        if len(queue) > len(target_slots):
            step = len(queue) / float(len(target_slots))
            queue = [queue[int(k * step)] for k in range(len(target_slots))]
        for k, o in enumerate(target_slots):
            if k < len(queue):
                l = queue[k]
                m = [float(l[c]) for c in ('m00', 'm01', 'm02', 'm10', 'm11',
                                           'm12', 'm20', 'm21', 'm22',
                                           'tx', 'ty', 'tz')]
                centre = tuple((float(l['emin' + e]) + float(l['emax' + e])) / 2.0
                               for e in 'xyz')
                placed += 1
            else:
                # a leftover place: parked far away, with a tiny radius, so
                # culling never reaches it. Zeroing the matrix does not work - a
                # zero-norm row is a degenerate matrix.
                m = [1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0, 0.0, -100000.0, 0.0]
                centre = (0.0, -100000.0, 0.0)
            struct.pack_into('<12f', p.data, o + 0x20, *m)
            struct.pack_into('<f', p.data, o + 0x18,
                             radius if k < len(queue) else 0.01)
            struct.pack_into('<3f', p.data, o + 0x50, *centre)
            struct.pack_into('<I', p.data, o + 0x1C, k)
            if grid:
                rr = radius if k < len(queue) else 0.0
                p.data[o + 0x14:o + 0x18] = cell_box(
                    grid, centre[0], centre[2], rr)
    print('  %d kind chains remapped, %d instances placed%s'
          % (used_set, placed,
             ', %d kinds cut by the budget' % cut_kinds if cut_kinds else ''))
    return placed


def port_city(p, place, folder, convert, material=None, limit=None,
                 instances=False, fill_shaders=True,
                 donor_grid=False):
    """Fill EVERY hood of the target with a whole MC2 city, in TRUE coordinates.

    `porta_hood` maps one MC2 hood onto one target hood and rescales it to that
    hood's footprint. That is right for one neighbourhood and wrong for a city:
    each hood would get its own scale and the layout would come apart.

    Here nothing is rescaled. Every component goes to its own world position
    from the table, the world extents of the target are rewritten to the
    source's, and the culling grid is recomputed from those extents - MC2's
    `.lvl` says `dimensions 90 81` for Los Angeles and 90 * 40 = 3600 is exactly
    its x extent, so both games tile the world at 40 units and the grid carries
    over unchanged.

    Slots are consumed hood by hood; what is left over is emptied. The hood a
    component lands in does not decide its culling - the tile box at
    `component+0x14` does, and that is computed from the real position.
    """
    import mc3_city_build as cb
    lines, ext = cb.read_place(place)
    comps = [l for l in lines if l['kind'] == 'comp']
    if limit:
        comps = comps[:limit]

    if material is None and fill_shaders:
        fill_semantic_slots(p)
    material_table = None if material is not None else materials_by_category(p)
    if material_table:
        print('  material by semantics, measured on the target: %s'
              % ', '.join('%s=%d' % (c, material_table[c]) for c in CATEGORIES))
    hs = hoods(p)
    slots = []
    for i in range(len(hs)):
        for nm, meshes, comp, cd in hood_uniques(p, i):
            slots.append((i, nm, meshes, comp, cd))
    print('  target: %d hood(s), %d slots; source: %d components'
          % (len(hs), len(slots), len(comps)))

    r = 0x80
    n_before, w_before, h_before = struct.unpack_from('<3i', p.data, r + 0x268)
    if ext.get('extents_min') and ext.get('extents_max'):
        for off, key in ((0x238, 'extents_min'), (0x244, 'extents_max')):
            for j, v in enumerate(ext[key][:3]):
                struct.pack_into('<f', p.data, r + off + 4 * j, v)
        # THE CULLING GRID ORIGIN LIVES AT MapRoot+0x274/+0x278, in CELLS, and
        # rewriting only the extents leaves the two in disagreement.
        #
        # Read from the ELF: `mcCity::CalcAbsCellXZ` (0x257CC8) is `floor(x / 40)`
        # and `floor(z / 40)`, with no origin at all; what removes the origin is
        # `CalcRelCellXZ` (0x257C60), subtracting `mcCity+0x274` and `+0x278`.
        # Confirmed in the four retail cities, 8 of 8 values:
        # +0x274 == floor(extents_min.x / 40) and +0x278 == floor(min.z / 40).
        #
        # The previous file had the Los Angeles extents and the atlanta origin:
        # -33/-34 instead of -50/-43, so the whole culling was shifted 17 cells
        # in x and 9 in z - 680 and 360 world units.
        import math
        cx0 = int(math.floor(ext['extents_min'][0] / 40.0))
        cz0 = int(math.floor(ext['extents_min'][2] / 40.0))
        struct.pack_into('<2i', p.data, r + 0x274, cx0, cz0)
        # THE GRID SIZE GOES TOGETHER WITH THE EXTENTS, and writing only the
        # origin left half the city invisible.
        #
        # `MapRoot+0x26C` and `+0x270` are the limits of `rel_x` and `rel_z`, and
        # `+0x268` is the cell count the game allocates (0x2594D0 does
        # `malloc(+0x268 * 4)`; another consumer at 0x25B090 uses 12 bytes per
        # cell). `mcCity::CellIndexFromPos` (0x257BE8) returns
        # `rel_z * W + rel_x` and -1 outside that - and the object does not enter
        # the grid.
        #
        # The retail invariant, checked on atlanta (64x62), tokyo (90x69) and
        # detroit (96x75): W and H come exactly from the extents and ZERO objects
        # are left out. The previous file had the Los Angeles extents (91x82
        # cells) with the atlanta grid (64x62): everything falling at x >= 64 or
        # z >= 62 was dropped without warning.
        gw = int(math.floor(ext['extents_max'][0] / 40.0)) - cx0 + 1
        gh = int(math.floor(ext['extents_max'][2] / 40.0)) - cz0 + 1
        gw = max(1, min(256, gw))
        gh = max(1, min(256, gh))
        if donor_grid:
            gw, gh = w_before, h_before
        else:
            struct.pack_into('<3i', p.data, r + 0x268, gw * gh, gw, gh)
        grid = (40.0, cx0 * 40.0, 40.0, cz0 * 40.0, gw, gh)
        print('  extents rewritten to x %.0f..%.0f  z %.0f..%.0f;'
              ' cell origin +0x274/+0x278 = %d, %d'
              % (ext['extents_min'][0], ext['extents_max'][0],
                 ext['extents_min'][2], ext['extents_max'][2], cx0, cz0))
        print('  cell grid %d x %d = %d (was %d x %d = %d)'
              % (gw, gh, gw * gh, w_before, h_before, n_before))
    else:
        grid = mc2.tile_grid(p)

    made_n = used_set = 0
    for l in comps:
        if used_set >= len(slots):
            break
        if l['part'] == '-':
            fname = '%s_%s.mod' % (l['name'], l['lod'])
        else:
            fname = '%s_%s_%s.mod' % (l['name'], l['lod'], l['part'])
        pck_path = os.path.join(folder, fname)
        if not os.path.isfile(pck_path):
            continue
        got = convert(pck_path)
        if not got:
            continue
        blocks, _r = got
        blocks, _c = centre_on_origin(blocks)
        _i, _nm, meshes, comp, cd = slots[used_set]
        used_set += 1
        # ONLY THE FIRST LOD SLOT. Atlanta has 1076 meshes for 651
        # components: giving the same LA geometry to every slot writes the whole
        # piece up to ten times and is what blows the size ceiling. The other
        # slots keep a zero group count - they do not draw, and the far LOD
        # simply does not show.
        mat = material if material is not None else             (material_table.get(category(l['name'])) if material_table else None)
        ok = False
        for j, mm in enumerate(meshes):
            # LOD 0 gets the whole piece; the other slots get ONE block.
            #
            # Giving the full geometry to all ten slots is what blew the size
            # ceiling. But ZEROING the group count of the others does not work:
            # the empty LOD makes PCSX2 abort with
            # "FQC = 0 on VIF FIFO READ" - something picks that LOD and ends up
            # reading a FIFO nobody filled. One block is cheap and is still a
            # real mesh.
            if swap(p, mm, blocks if j == 0 else blocks[:1]):
                if j == 0:
                    ok = True
            if mat is not None:
                unify_material(p, mm, mat)
        if not ok:
            continue
        world = tuple((float(l['emin' + e]) + float(l['emax' + e])) / 2.0
                      for e in 'xyz')
        struct.pack_into('<3f', p.data, cd + 0x2C, *world)
        pts = [q for pos, _u, _n, _o in blocks for q in pos]
        radius = max((q[0] ** 2 + q[1] ** 2 + q[2] ** 2) ** 0.5 for q in pts)
        reposition(p, comp, cd, radius, grid)
        made_n += 1

    if instances:
        port_instances(p, place, folder, convert, grid, material)

    empties = 0
    for _i, _nm, meshes, _c, _cd in slots[used_set:]:
        for mm in meshes:
            struct.pack_into('<H', p.data, mm + 8, 0)
        empties += 1
    print('  %d components ported, %d slots emptied' % (made_n, empties))

    # LAST, once every tile box is in place: the per-cell index. Without it
    # the city loads and does not show - the game walks the MapRoot+0x1C table,
    # which still lists the donor's objects in the donor's cells. See
    # mc3_cellindex.
    try:
        import mc3_cellindex
        res = mc3_cellindex.rebuild(p)
    except Exception as e:
        print('  WARNING: per-cell index not rebuilt (%s)' % e)
        res = None
    if res:
        print('  per-cell index rebuilt: %d cells (%d occupied), '
              '%d unique + %d instance listed, +%d bytes'
              % (res['cells'], res['occupied'], res['uniques'],
                 res['instances'], res['bytes']))
    return made_n


def port_hood(p, targets, source, folder, convert, grid, requested_scale,
               material, without_sphere):
    """Move a whole MC2 hood in as one rigid block.

    Matching mesh-by-mesh on size, which is what the catalogue mode does, cannot
    produce a neighbourhood: it drops unrelated buildings onto unrelated
    footprints. Here the SET moves together - each component keeps its position
    relative to its neighbours, because MC2 stores geometry in world coordinates
    and the .hood declares each component's world box.

    The two sides never have the same number of slots (LA downtown offers 25
    usable uniques, atlanta's `ug` has 43), so components are assigned to the
    nearest free slot by mapped position, and **whatever is left over is
    emptied** - otherwise the original geometry stays behind and mixes with the
    import, which is exactly the mess the LOD bug produced.
    """
    lo = [min(a[k] for _n, a, _b in source) for k in range(3)]
    hi = [max(b[k] for _n, _a, b in source) for k in range(3)]
    cen_src = [(lo[k] + hi[k]) / 2.0 for k in range(3)]

    target_pos = [struct.unpack_from('<3f', p.data, cd + 0x2C) for _n, _m, _c, cd in targets]
    tlo = [min(q[k] for q in target_pos) for k in range(3)]
    thi = [max(q[k] for q in target_pos) for k in range(3)]
    cen_dst = [(tlo[k] + thi[k]) / 2.0 for k in range(3)]

    # HEIGHT. Y is not part of the tile mapping - `mcCity::CalcAbsCellXZ` is
    # `floor(x/40)` and `floor(z/40)` and never reads Y - so height decides
    # nothing about where a model is filed, only whether it looks buried.
    #
    # Seating each component on the base of the slot it replaces was wrong for
    # anything FLAT. A road is 73 x 0.1 x 103, so its base is its top, and the
    # slot it lands in is a tunnel piece whose lowest point is the foot of a
    # wall, well under the floor - every road went under the ground and none of
    # them was ever visible. It also threw away the neighbourhood's own vertical
    # structure: street below, building above, freeway overhead.
    #
    # So the hood moves as one rigid block in Y too. Each component keeps its
    # own height relative to the MC2 ground, scaled by the same factor, and the
    # whole set is offset once so that MC2's street level lands on the target's.
    target_floor = []
    for j, (_n, meshes, _c, cd) in enumerate(targets):
        base = None
        for mm in meshes:
            ab = aabb_local(p, mm)
            if ab:
                base = target_pos[j][1] + ab[0][1]
                break
        if base is None:      # unreadable mesh: fall back to the sphere radius
            base = target_pos[j][1] - struct.unpack_from('<f', p.data, cd + 0x38)[0]
        target_floor.append(base)
    ordered = sorted(target_floor)
    floor_dst = ordered[len(ordered) // 2]      # median: robust against a deep pit
    floor_src = min(a[1] for _n, a, _b in source)
    print('  ground: MC2 %.1f -> target %.1f (median of %d slots)'
          % (floor_src, floor_dst, len(target_floor)))

    if requested_scale:
        esc = requested_scale
    else:
        esc = min((thi[k] - tlo[k]) / (hi[k] - lo[k])
                  for k in (0, 2) if hi[k] - lo[k] > 1) or 1.0
    print('  source %.0f x %.0f, target %.0f x %.0f  ->  scale %.3f'
          % (hi[0] - lo[0], hi[2] - lo[2], thi[0] - tlo[0], thi[2] - tlo[2], esc))

    free = list(range(len(targets)))
    made_n = 0
    for name, emin, emax in source:
        pck_path = xmod_de(name, folder)
        if not pck_path:
            continue
        got = convert(pck_path, esc)
        if not got:
            continue
        blocks, _r = got
        blocks, _c = centre_on_origin(blocks)
        c_src = [(emin[k] + emax[k]) / 2.0 for k in range(3)]
        dest = [cen_dst[k] + (c_src[k] - cen_src[k]) * esc if k != 1 else 0.0
                   for k in range(3)]
        if not free:
            break
        j = min(free, key=lambda i: sum((target_pos[i][k] - dest[k]) ** 2
                                          for k in (0, 2)))
        free.remove(j)
        _nm, meshes, comp, cd = targets[j]
        pts = [c for pos, _u, _nr, _o in blocks for c in pos]
        radius = max((q[0] ** 2 + q[1] ** 2 + q[2] ** 2) ** 0.5 for q in pts)
        # base at the height the piece had in MC2, measured from the ground there
        # and scaled; the blocks are already centred, so the centre sits half a
        # height above
        high = max(q[1] for q in pts) - min(q[1] for q in pts)
        dest[1] = floor_dst + (emin[1] - floor_src) * esc + high / 2.0
        ok = False
        for mm in meshes:
            if swap(p, mm, blocks):
                ok = True
            if material is not None:
                unify_material(p, mm, material)
        if not ok:
            continue
        struct.pack_into('<3f', p.data, cd + 0x2C, *dest)
        if not without_sphere:
            reposition(p, comp, cd, radius, grid)
        made_n += 1
        print('   ok %-40s -> (%7.0f %6.0f %7.0f)  radius %.0f'
              % (name[:40], dest[0], dest[1], dest[2], radius))

    # what is left has to go, otherwise the old city stays underneath
    for i in free:
        _nm, meshes, _c, _cd = targets[i]
        for mm in meshes:
            struct.pack_into('<H', p.data, mm + 8, 0)
    print('  %d components ported; %d target slots emptied'
          % (made_n, len(free)))
    return made_n


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('pck')
    ap.add_argument('--hood', required=True, help='hood name (bh, dt, fwy, lf, mt, ug, we)')
    ap.add_argument('--xmod', default=mc2.XMOD)
    ap.add_argument('--xmoddir', help='a directory of .xmod; each target mesh gets '
                                      'the model whose radius is closest to its own')
    ap.add_argument('--uncompacted', action='store_true',
                    help='do not give back the bytes of the replaced geometry')
    ap.add_argument('--ceiling-mb', type=float, default=11.8,
                    help='stop adding instance kinds once the '
                         'file goes past this size')
    ap.add_argument('--with-instances', action='store_true',
                    help='also fill the target instance slots')
    ap.add_argument('--mc2city', metavar='PLACE.tsv',
                    help='port the WHOLE city in real coordinates, using '
                         'all the target hoods; rewrites the extents')
    ap.add_argument('--mc2hood', metavar='FILE.hood',
                    help='port a WHOLE MC2 hood keeping the relative'
                         ' layout, and empty the slots left over')
    ap.add_argument('--scale', type=float, default=1.0)
    ap.add_argument('--limit', type=int, default=0, help='stop after N meshes')
    ap.add_argument('--light', type=int, default=mc2.LIGHT)
    ap.add_argument('--normals', action='store_true')
    ap.add_argument('--stack', metavar='X,Y,Z',
                    help='EXPERIMENT: put the SAME cd+0x2C in every mesh. If'
                         ' that field is the placement, the objects pile up at that'
                         ' point; if they do not, the placement is somewhere else')
    ap.add_argument('--centred', action='store_true',
                    help='anchor the model at its own centre (like most vanilla'
                         ' meshes). Makes the two readings of cd+0x2C'
                         ' agree, so the ambiguity stops mattering')
    ap.add_argument('--sphere-centre', action='store_true',
                    help='treat cd+0x2C as the SPHERE CENTRE and rewrite it, instead'
                         ' of treating it as the origin translation. Implies'
                         ' --unaligned: they are alternative readings, they do not add up')
    ap.add_argument('--unaligned', action='store_true',
                    help='do not move the new model onto the old footprint; use'
                         ' to compare with the previous behaviour')
    ap.add_argument('--material', type=int,
                    help='force ONE material index in every group (3 is the'
                         ' most used in atlanta). Together with --fixed-uv it gives'
                         ' a flat surface, uniform across meshes')
    ap.add_argument('--donor-grid', action='store_true',
                    help='do not grow the cell grid: keep the donor W/H/count '
                         'and only redo the per-cell index inside it. '
                         'Useful to separate the two variables in a test.')
    ap.add_argument('--without-shader-fill', action='store_true',
                    help='when importing a whole city, keep only the material'
                         ' slots that already exist in the 18 groups; by default'
                         ' the seven semantic materials are filled with'
                         ' independent retail clones')
    ap.add_argument('--fixed-uv', metavar='U,V',
                    help='lock every UV to a single value -> flat surface')
    ap.add_argument('--uvdebug', action='store_true',
                    help='a DIFFERENT constant UV per mesh: each object comes out'
                         ' in a distinct flat colour, which tells them apart on'
                         ' screen without relying on the texture being right')
    ap.add_argument('--keep-sphere', action='store_true',
                    help='do not touch the radius or the tile AABB (culling will '
                         'hide a model larger than the original)')
    ap.add_argument('--out-path')
    a = ap.parse_args()
    globals()['LIGHT'] = a.light
    mc2.LIGHT = max(0, min(255, a.light))
    mc2.NORMALS = bool(a.normals)
    if a.fixed_uv:
        mc2.FIXED_UV = tuple(float(x) for x in a.fixed_uv.split(','))

    p = mc2.Pck(a.pck)
    hs = hoods(p)
    print('%s: %d hoods' % (os.path.basename(a.pck), len(hs)))
    for i, (name, nu, _au, ni, _ai) in enumerate(hs):
        print('   %d %-5s %4d unique  %4d instance%s'
              % (i, name, nu, ni, '   <- target' if name == a.hood else ''))
    idx = next((i for i, h in enumerate(hs) if h[0] == a.hood), None)
    if idx is None:
        raise SystemExit('no hood named %r' % a.hood)

    cache = {}

    def convert(file_path, esc=None):
        """(blocks, radius) for one .xmod, converted once and reused.

        The cache is keyed on the UV too: with `--uvdebug` the same model is
        converted again per mesh with a different constant UV, so caching on the
        path alone would hand every mesh the first colour.
        """
        factor = a.scale if esc is None else esc
        key = (file_path, mc2.FIXED_UV, factor)
        if key in cache:
            return cache[key]
        v, t1, adj, strips = mc2.read_xmod(file_path)
        bl, _c = mc2.strips_to_blocks(v, t1, adj, strips)
        if factor != 1.0 and bl:
            bl = [([tuple(c * factor for c in q) for q in pos], u, nr, o)
                  for pos, u, nr, o in bl]
        if not bl or len(bl) > mc2.MAX_BLOCKS:
            cache[key] = None              # retail never exceeds 88 blocks
            return None
        pts = [c for pos, _u, _nr, _o in bl for c in pos]
        r = max((q[0] ** 2 + q[1] ** 2 + q[2] ** 2) ** 0.5 for q in pts)
        cache[key] = (bl, r)
        return cache[key]

    if a.mc2city:
        folder = a.xmoddir or os.path.join(os.path.dirname(a.mc2city), 'model')
        if a.ceiling_mb:
            p.ceiling_bytes = int(a.ceiling_mb * 1048576)
        port_city(p, a.mc2city, folder, convert, a.material, a.limit,
                     a.with_instances, not a.without_shader_fill,
                     a.donor_grid)
        out = a.out_path or (os.path.splitext(a.pck)[0] + '_city.pck')
        if not a.uncompacted:
            import mc3_compact
            r = mc3_compact.compact(p, getattr(p, '_dead', []))
            if len(r) == 4:
                print('  compacted: %.2f MB -> %.2f MB (%d pointers moved, %d lost)'
                      % (r[0]/1048576.0, r[1]/1048576.0, r[2], r[3]))
        p.save(out)
        print('written %s (%d bytes)' % (out, len(p.data)))
        raise SystemExit(0)

    cat = []
    if a.xmoddir:
        cat = catalogue(a.xmoddir)
        if not cat:
            raise SystemExit('no usable `_main` .xmod/.mod in %s' % a.xmoddir)
        print()
        print('catalogue: %d `_main` models, radius from %.0f to %.0f'
              % (len(cat), cat[0][0], cat[-1][0]))
    else:
        r = convert(a.xmod)
        if not r:
            raise SystemExit('%s does not convert within the %d-block limit'
                             % (a.xmod, mc2.MAX_BLOCKS))
        print()
        print('single model: %d blocks, %d triangles, radius %.1f'
              % (len(r[0]), sum(len(x[0]) - 2 for x in r[0]), r[1]))
    grid = mc2.tile_grid(p)
    print('tile grid: %s'
          % ('cell %.1f x %.1f' % (grid[0], grid[2]) if grid else 'not fitted'))

    def pick(target_radius):
        """The catalogue entry whose radius is closest to what it replaces.

        Matching by size keeps the swap sane: a tunnel piece of radius 52 gets a
        model of about that size instead of a 217-unit tower, so the culling, the
        collision and the general look of the block stay in proportion. Entries
        that fail to convert are dropped and the next closest is tried.
        """
        for _d, file_path in sorted(((abs(r - target_radius), c) for r, c in cat))[:12]:
            got = convert(file_path)
            if got:
                return file_path, got
        return None, None

    names = hood_uniques(p, idx)
    if a.mc2hood:
        source = read_hood_mc2(a.mc2hood)
        folder = a.xmoddir or os.path.join(os.path.dirname(a.mc2hood), 'models')
        print()
        print('MC2 %s: %d unique_component' % (os.path.basename(a.mc2hood), len(source)))
        port_hood(p, names, source, folder, convert, grid,
                   a.scale if a.scale != 1.0 else None,
                   a.material, a.keep_sphere)
        out = a.out_path or (os.path.splitext(a.pck)[0] + '_hood.pck')
        p.save(out)
        print('written %s (%d bytes)' % (out, len(p.data)))
        return
    if a.limit:
        names = names[:a.limit]
    before = len(p.data)
    made_n = failed_n = 0
    offs_ = (0.0, 0.0, 0.0)
    for i_target, (nm, meshes, comp, cd) in enumerate(names):
        if a.uvdebug:
            # spread the constant UV over the atlas so consecutive meshes land on
            # different texels and therefore come out different flat colours
            mc2.FIXED_UV = (0.08 + 0.16 * (i_target % 6), 0.08 + 0.16 * ((i_target // 6) % 6))
        radius_before = struct.unpack_from('<f', p.data, cd + 0x38)[0]
        if cat:
            file_path, got = pick(radius_before)
            if not got:
                failed_n += 1
                print('   -- %-30s nothing in the catalogue converted' % nm[:30])
                continue
            blocks, radius = got
            de = os.path.basename(file_path).split('#')[0]
        else:
            blocks, radius = convert(a.xmod)
            de = os.path.basename(a.xmod).split('#')[0]
        target = aabb_local(p, meshes[0])
        if a.stack:
            q = tuple(float(x) for x in a.stack.split(','))
            blocks, _cen = centre_on_origin(blocks)
            struct.pack_into('<3f', p.data, cd + 0x2C, *q)
            offs_ = q
        elif a.centred:
            # the model becomes centred on the origin; cd+0x2C gets the world
            # centre the old geometry occupied, which under reading A is
            # `cd2C + old_local_centre` and under B is `cd2C` itself -- the
            # difference between the two drops to the old local centre, whose
            # median is 0 in x/z and 6.4 in y, instead of the ~97 from before
            blocks, _cen = centre_on_origin(blocks)
            if target:
                old = [(target[0][k] + target[1][k]) / 2.0 for k in range(3)]
                c0 = struct.unpack_from('<3f', p.data, cd + 0x2C)
                new = [c0[k] + old[k] for k in range(3)]
                struct.pack_into('<3f', p.data, cd + 0x2C, *new)
                offs_ = tuple(new[k] - c0[k] for k in range(3))
        elif a.sphere_centre:
            if target:
                pts = [c for pos, _u, _nr, _o in blocks for c in pos]
                nlo = [min(q[a2] for q in pts) for a2 in range(3)]
                nhi = [max(q[a2] for q in pts) for a2 in range(3)]
                offs_ = recentre_sphere(p, cd, target, nlo, nhi)
        elif not a.unaligned:
            if target:
                blocks, offs_ = align_to(blocks, target)
        # every LOD of this component gets the same geometry, or the distant
        # ones keep drawing what used to be there
        rs = [swap(p, mm, blocks) for mm in meshes]
        r = next((x for x in rs if x), None)
        if r is None:
            failed_n += 1
            print('   -- %-30s none of the %d LODs had a usable group' % (nm, len(meshes)))
            continue
        r = (sum(x[0] for x in rs if x), r[1])
        if a.material is not None:
            for mm in meshes:
                unify_material(p, mm, a.material)
        c = reposition(p, comp, cd, radius, grid) if not a.keep_sphere else None
        made_n += 1
        print('   ok %-22s <- %-26s %d LOD %3d bl  radius %.0f->%.0f  shift (%.0f %.0f %.0f)'
              % (nm[:22], de[:26], len(meshes), r[0], radius_before,
                 radius if c else radius_before, *offs_))

    print()
    print('%d meshes swapped, %d skipped; file %d -> %d bytes (+%.1f MB)'
          % (made_n, failed_n, before, len(p.data), (len(p.data) - before) / 1048576.0))
    out = a.out_path or (os.path.splitext(a.pck)[0] + '_hood.pck')
    p.save(out)
    print('written %s' % out)


if __name__ == '__main__':
    main()
