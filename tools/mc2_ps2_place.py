"""Bridge: MC2 PS2 city -> the place.tsv and .mod files mc3_city_build reads.

The LA port used to be fed by the MC2 PC export. Fork MC2 PS2 decoded the PS2
build (mc2_rsc.py) and it is a better source for three reasons they measured:

  * the geometry is already in PS2 form - each PMD0 batch is ONE TRIANGLE STRIP,
    which is exactly what mc3_city_build consumes;
  * the winding rule is the SAME on both sides. mc2_rsc's Batch.triangles()
    uses (i-2,i-1,i) for even i and (i-1,i-2,i) for odd i, and mc2_to_mc3's
    tris_of() uses the identical one. There is no winding conversion in between;
  * the placement comes from the text .hood files, with 294 unique_component
    baked in world space and 8500 instance_component with a matrix.

WHAT THIS BRIDGE DOES NOT SOLVE, noted because the fork warned about it:

  * 24 of the 8500 matrices have a NEGATIVE DETERMINANT - the PS2 swapped
    mirrored meshes for mirrored matrices, and the PC has none. Where the
    determinant is negative the winding flips and the face shows reversed. Are
    they MARKED in the tsv's `hood` column? No - they are counted and reported,
    and --without-mirrored drops them, which is the safe behaviour until the
    builder knows how to flip indices.
  * 1734 of the matrices have a scale != 1 and 1487 of those are non-uniform.
    The MC3 cull radius is LOCAL to the kind, so a scaled instance can vanish
    early. The fork measured the worst ratio at 2.54x and the largest absolute
    slack at 7.71 units.

    python mc2_ps2_place.py <losangeles.rsc> --out output/mc2_la_ps2
"""
import argparse
import struct
import importlib.util
import io
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SPEC = importlib.util.spec_from_file_location(
    'mc2_rsc', os.path.join(_HERE, 'mc2_rsc.py'))
rsc_mod = importlib.util.module_from_spec(_SPEC)
sys.modules['mc2_rsc'] = rsc_mod
_SPEC.loader.exec_module(rsc_mod)

HEADER = ('kind\tname\tlod\tpart\tm00\tm01\tm02\tm10\tm11\tm12\tm20\tm21'
             '\tm22\ttx\tty\ttz\teminx\teminy\teminz\temaxx\temaxy\temaxz\thood')


def cpv_index(cpvs):
    """PCP0 name -> entry index."""
    out = {}
    for h, (fcc, name) in cpvs.names.items():
        if fcc == 'PCP0':
            out[name] = (h & 0x3FFF) - 1
    return out


def model_cpv(name, mapping):
    """The CPV entry that serves this model, and whether it is exact.

    In the 294 baked components the PCP0 name is '<name>_0_main' and the colour
    is EXACT, because each component has its own mesh. In the instances the name
    is '<owner>_<kind><extension>_<lod>_<main|refl>' - so MC2 keeps colour PER
    INSTANCE (13,756 PCP0 for 8,774 batches), while MC3 shares one mesh per kind.
    So for an instance kind only ONE instance can be picked as representative,
    and the colour of the others comes out approximate. Flagged in the return
    value so the report does not hide it.
    """
    exact = mapping.get('%s_0_main' % name)
    if exact is not None:
        return exact, True
    target = '_%s' % name.lower()
    for k in sorted(mapping):
        kl = k.lower()
        if kl.endswith('_0_main') and target in kl:
            return mapping[k], False
    return None, False


def model_strips(rsc, model, cpvs=None, ent_cpv=None,
                    dup_adc=True):
    """(positions, uvs, strips) of a model, with ADC already honoured.

    A batch is a strip. An ADC byte with 0x80 says "do not draw the triangle
    ending here", and the PC text format has no such concept.

    MY FIRST VERSION BROKE THE STRIP at that point, and it was wrong. The MC2
    and MC3 winding is (i-2,i-1,i) for EVEN i and (i-1,i-2,i) for ODD i, with
    the parity counted from the start of the strip. Restarting the strip resets
    that count: if the cut falls on an even k, every following triangle of that
    piece comes out with the winding swapped, the face points inwards and
    vanishes in back-face culling. Since the cut lands on even or odd by chance,
    this erased close to half the faces after each ADC - it was the "lots of
    faces not showing" the user saw on screen.

    The right translation is to DUPLICATE TWO VERTICES instead of cutting:

        ... k-2, k-1, [k-1, k-1], k, k+1 ...

    The three triangles born at the duplicated indices are degenerate (zero
    area, they do not draw), and one of them sits exactly where the triangle the
    ADC wanted to suppress was. And since it is TWO vertices, the parity of
    everything after stays intact. The strip stays whole, which on top of that
    produces fewer degenerate bridges in strips_to_blocks.
    """
    pos, uv, colour, strip_list = [], [], [], []
    if cpvs is not None and ent_cpv is not None:
        # The chain that DRAWS is the PCP0 one: it does `ref` into the geometry
        # inside the PMD0 and adds the colour, so read_pcp returns pos/uv/adc/cpv
        # together and there is no risk of the two sides getting out of order.
        # Found by Fork MC2 PS2; using read_pmd in parallel would be reinventing
        # the pairing.
        batches_ = rsc_mod.read_pcp(rsc, cpvs, ent_cpv)
    else:
        batches_ = []
        for h in rsc_mod.model_blocks(model, lod=None):
            r = rsc.resolve(h)
            if r is not None:
                batches_.extend(rsc_mod.read_pmd(rsc, r[1])[0])
    if True:
        for batch in batches_:
            base = len(pos)
            pos.extend(batch.pos)
            uv.extend(batch.uv)
            # element 0 of the CPV stream IS A COLOUR, unlike the ADC one, where
            # it is the vertex count - the fork measured that only 123 of 50,170
            # batches coincide by chance. So the first one is not skipped here.
            colour.extend(getattr(batch, 'cpv', None) or [0] * len(batch.pos))
            current = [base, base + 1] if len(batch.pos) >= 2 else []
            for k in range(2, len(batch.pos)):
                if dup_adc and k < len(batch.adc) and (batch.adc[k] & 0x80):
                    current += [current[-1], current[-1]]
                current.append(base + k)
            if len(current) >= 3:
                strip_list.extend(strip_part(current))
    return pos, uv, colour, strip_list


# The retail limit, measured on the 1353 atlanta city meshes.
MAX_VERTS = 48
STRIP = 'str'          # see the note in write_mod


def strip_part(t):
    """Break a long strip into pieces of at most MAX_VERTS, without changing any
    triangle.

    strips_to_blocks NEVER splits a strip: it only decides whether to
    CONCATENATE the next one into the current block. That worked with MC2 PC,
    where the strips are tiny (the wfargo tower has 287 strips, almost all of 4
    vertices), and breaks with the PS2, where a batch is one long strip -
    measured on the 723 models, 62.9%% of the blocks went over 48 vertices and
    the largest had 131.

    AND THAT BLOWS UP IN VU1: the block's three streams sit at fixed addresses
    with 48 qwords each - normal at 160, uv at 208, position at 256. A
    131-vertex block writes 131 qwords from 160 on and overruns the other two,
    so the position ends up read from where the UV was. On screen that becomes
    a giant triangle across the scene, with VU1 at 99.9%%.

    THE SPLIT KEEPS THE PARITY. The winding is (i-2,i-1,i) for even i and
    (i-1,i-2,i) for odd i, counted from the start of the strip, so a new piece
    can only start at an EVEN offset. The pieces have 48 vertices and advance by
    46, overlapping the last two - 46 is even, so every start lands on an even
    index and the winding stays the same. The overlap of two is what keeps the
    chain of triangles connected across the cut.
    """
    # 46 and not 48: strips_to_blocks' `open_()` adds ONE duplicated vertex in
    # front of every `stp` strip so it lands on an odd index, and with 48-vertex
    # pieces the block came out with 49 - one more than the VU1 region.
    # 46 is even, so the 44 step is too, and the parity still holds.
    ceiling = MAX_VERTS - 2
    if len(t) <= ceiling:
        return [t]
    step = ceiling - 2
    out = []
    s = 0
    while s < len(t) - 2:
        chunk = t[s:s + ceiling]
        if len(chunk) >= 3:
            out.append(chunk)
        s += step
    return out


def write_mod(path, pos, uv, colour, strip_list):
    """The text mc2_to_mc3's read_xmod already knows how to read.

    `adj` uses fields 1 and 4 (vertex and uv); the `packet` at the start resets
    the strip index base, so everything here is indexed globally.
    """
    with io.open(path, 'w', encoding='latin1') as f:
        for x, y, z in pos:
            f.write('v %.6f %.6f %.6f\n' % (x, y, z))
        for u, w in uv:
            f.write('t1 %.6f %.6f\n' % (u, w))
        f.write('packet %d %d\n' % (len(pos), len(strip_list)))
        # Field 3 of `adj` is the COLOUR. In the MC2 format the tuple is
        # `adj <vertex> <normal> <colour> <uv>` and read_xmod already read
        # fields 1 and 4; now it reads 3 as well. It is where the palette index
        # fits without inventing a new record in the format.
        for i in range(len(pos)):
            f.write('adj %d 0 %d %d\n' % (i, colour[i] if i < len(colour) else 0, i))
        # `str` AND NOT `stp`, and this was MEASURED, not chosen.
        #
        # strips_to_blocks maps `stp` to odd=True, and its open_() prepends a
        # duplicated vertex so the strip lands on an ODD index. That mapping was
        # measured on MC2 PC, whose convention is the REVERSE of MC3's. The PS2
        # data is not: it comes from a real PS2 display list, with the same
        # (i-2,i-1,i) for even i that MC3 uses - so the strip has to start on an
        # EVEN index, without the extra vertex, which is what `str` produces.
        #
        # Measured block by block against Fork MC2 PS2's decoder on 120 models:
        # with `stp` 0 of 120 are right and 34,817 triangles are just REVERSED;
        # with `str` it is 120 of 120 and zero reversed. On screen `stp` showed
        # up as a row of alternating triangles, with most faces vanishing in
        # back-face culling.
        for t in strip_list:
            f.write('str %d %s\n' % (len(t), ' '.join(str(i) for i in t)))


def determinant_(m):
    return (m[0] * (m[4] * m[8] - m[5] * m[7])
            - m[1] * (m[3] * m[8] - m[5] * m[6])
            + m[2] * (m[3] * m[7] - m[4] * m[6]))


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('rsc')
    ap.add_argument('--out', default='output/mc2_la_ps2')
    ap.add_argument('--cpvs', metavar='CPVS.RSC',
                    help='MC2 PS2 <hour>_cpvs.rsc: carries the per-vertex colour')
    ap.add_argument('--angel', action='store_true',
                    help='emit the full Angel Studios dialect: parity per '
                         'strip, material per texture and colour per vertex. It '
                         'is what mc3_mesh_convert and the game itself read, and '
                         'the toolkit read_xmod reads it too, because it is a superset')
    ap.add_argument('--strip', choices=('stp', 'str'), default='str',
                    help='strip parity; str is the measured right one (120/120)')
    ap.add_argument('--without-adc', action='store_true',
                    help='ignore the ADC bit instead of degenerating the triangle')
    ap.add_argument('--without-mirrored', action='store_true',
                    help='drop the instances with a negative determinant')
    a = ap.parse_args()

    globals()['STRIP'] = a.strip
    rsc = rsc_mod.Rsc(a.rsc)
    cpvs = rsc_mod.Rsc(a.cpvs) if a.cpvs else None
    cpv_map = cpv_index(cpvs) if cpvs else {}
    palette = None
    if cpvs:
        for _o, _f, _sz in cpvs.entries:
            if _f == 'CPP0':
                _b = cpvs.data[_o:_o + _sz]
                palette = [struct.unpack_from('<3B', _b, _k * 4)
                          for _k in range(256)]
                break
    byName = rsc_mod.models_by_name(rsc)
    folder = rsc_mod.city_folder(a.rsc)
    comps, insts = rsc_mod.read_hoods(folder)

    dmod = os.path.join(a.out, 'model')
    if not os.path.isdir(dmod):
        os.makedirs(dmod)

    # ---- the models, once each
    needs = set([c.name for c in comps] + [i.type for i in insts])
    written = 0
    empties = []
    exact_colour = approx_colour = no_colour = 0
    for name in sorted(needs):
        m = byName.get(name.lower()) or byName.get(name)
        if m is None:
            empties.append(name)
            continue
        ent, exact = model_cpv(name, cpv_map) if cpvs else (None, False)
        if a.angel:
            batches_ = (rsc_mod.read_pcp(rsc, cpvs, ent)
                     if (cpvs and ent is not None) else [])
            if not batches_:
                batches_ = []
                for h in rsc_mod.model_blocks(m, lod=None):
                    r = rsc.resolve(h)
                    if r is not None:
                        batches_.extend(rsc_mod.read_pmd(rsc, r[1])[0])
            def _tex(h):
                nm = rsc.texture_name(h)
                return (nm or 'no_texture').replace(' ', '_')
            if write_mod_angel(os.path.join(dmod, '%s_0_main.mod' % name),
                                 batches_, palette, _tex):
                written += 1
                if cpvs:
                    if ent is None:
                        no_colour += 1
                    elif exact:
                        exact_colour += 1
                    else:
                        approx_colour += 1
            else:
                empties.append(name)
            continue
        pos, uv, colour, strip_list = model_strips(rsc, m, cpvs, ent,
                                              not a.without_adc)
        if not strip_list:
            empties.append(name)
            continue
        write_mod(os.path.join(dmod, '%s_0_main.mod' % name), pos, uv, colour, strip_list)
        written += 1
        if cpvs:
            if ent is None:
                no_colour += 1
            elif exact:
                exact_colour += 1
            else:
                approx_colour += 1
    print('models: %d written, %d without geometry' % (written, len(empties)))
    if cpvs:
        print('per-vertex colour: %d exact (component), %d approximate (one '
              'instance stands for the kind), %d without colour'
              % (exact_colour, approx_colour, no_colour))

    # ---- the place.tsv
    lines = []
    mirrored = 0
    for c in comps:
        lines.append(['comp', c.name, '0', 'main',
                       '1', '0', '0', '0', '1', '0', '0', '0', '1',
                       '0', '0', '0']
                      + ['%.6f' % x for x in list(c.emin) + list(c.emax)]
                      + [c.hood])
    for i in insts:
        m = [x for line in i.rot for x in line]
        if determinant_(m) < 0:
            mirrored += 1
            if a.without_mirrored:
                continue
        lines.append(['inst', i.type, '0', 'main']
                      + ['%.6f' % x for x in m]
                      + ['%.6f' % x for x in i.pos]
                      + ['%.6f' % x for x in list(i.emin) + list(i.emax)]
                      + [i.hood])

    mn = [min(float(l[16 + k]) for l in lines) for k in range(3)]
    mx = [max(float(l[19 + k]) for l in lines) for k in range(3)]
    out_path = os.path.join(a.out, 'losangeles_ps2_place.tsv')
    with io.open(out_path, 'w', encoding='latin1') as f:
        f.write('# %s: %d hoods (source: MC2 PS2)\n'
                % (os.path.splitext(os.path.basename(a.rsc))[0],
                   len(set(c.hood for c in comps) | set(i.hood for i in insts))))
        f.write('# extents_min\t%.6f\t%.6f\t%.6f\n' % tuple(mn))
        f.write('# extents_max\t%.6f\t%.6f\t%.6f\n' % tuple(mx))
        f.write(HEADER + '\n')
        for l in lines:
            f.write('\t'.join(l) + '\n')

    ncomp = sum(1 for l in lines if l[0] == 'comp')
    ninst = len(lines) - ncomp
    print('place: %d components + %d instances -> %s' % (ncomp, ninst, out_path))
    print('extents: x %.1f..%.1f  y %.1f..%.1f  z %.1f..%.1f'
          % (mn[0], mx[0], mn[1], mx[1], mn[2], mx[2]))
    print('instances with a NEGATIVE determinant: %d%s'
          % (mirrored, ' (dropped)' if a.without_mirrored else
             ' (kept - their winding will come out reversed)'))



# ---------------------------------------------------------------------------
#  THE TEXT ROUTE
# ---------------------------------------------------------------------------
#
# The game builds the .pck by itself: rmcModel::Create reads .mesh/.mod as TEXT
# and rmcGeometry::Packet::Init 0x2AB110 is what EMITS THE VIF. Stripping,
# parity, ADC and block splitting are its job. Emitting VIF packets by hand,
# which is what this bridge used to do, was the origin of the last three
# defects: ADC parity (6,486 wrong triangles), overflowing 48 vertices per
# block (giant triangle, VU1 at 99.9%) and a global stp/str (most faces
# vanishing).
#
# Here the bridge emits the real Angel Studios dialect - what
# mc3_mesh_convert.py reads - instead of the simplified dialect only the
# toolkit's read_xmod understood.
#
# THREE THINGS THE DIALECT SOLVES FOR FREE:
#
#   * PARITY PER STRIP. Retail .mod files MIX `str` and `stp` in the same
#     packet (arrow4.mod has 16 str, one stp and another str). The reader's rule
#     is `(i-2,i-1,i)` when `(i even) == (str)`, so `str` is the natural PS2/MC3
#     winding and `stp` the reversed one. So a piece starting at an EVEN offset
#     comes out `str` and one starting at an ODD offset comes out `stp`, and ADC
#     no longer needs a duplicated vertex.
#   * MATERIAL PER TEXTURE. Each PMD0 batch carries a texture handle; grouping
#     by it gives several `mtl`, instead of the single material index the
#     generated city had been using.
#   * COLOUR PER VERTEX. The dialect has `c r g b a` with four floats and the
#     `adj` points to it. mc3_mesh_convert STILL drops that channel when reading
#     - the C++ FORK noted it and said Cpv is the reader's top improvement
#     priority - but the text already comes out right, so once it reads it the
#     city gains colour without touching anything here.

MOD_HEADER = ('version: 1.10\n'
           'verts: %d\nnormals: %d\ncolors: %d\ntex1s: %d\ntex2s: 0\n'
           'tangents: 0\nmaterials: %d\nadjuncts: %d\nprimitives: %d\n'
           'matrices: 1\nreskins: 1\n\n')


def chunks_per_adc(n, adc):
    """[(offset, [local indices])] of a batch, cutting at the ADC.

    No vertex is duplicated: the cut becomes a new piece and its parity is
    chosen by the offset, which is what the dialect lets us say.
    """
    out = []
    start_ = 0
    for k in range(2, n):
        if k < len(adc) and (adc[k] & 0x80):
            if k - start_ >= 3:
                out.append((start_, list(range(start_, k))))
            start_ = k - 1
    if n - start_ >= 3:
        out.append((start_, list(range(start_, n))))
    return out


def write_mod_angel(path, batches_, palette, tex_name):
    """A .mod in the dialect mc3_mesh_convert (and the game) read."""
    V, T1, C = [], [], []
    colour_index = {}
    packets = []                      # (texture, [adj], [(kind, [locals])])
    for b in batches_:
        base_v = len(V)
        V.extend(b.pos)
        T1.extend(b.uv)
        cpv = getattr(b, 'cpv', None) or [0] * len(b.pos)
        adjs = []
        for k in range(len(b.pos)):
            idx = cpv[k] if k < len(cpv) else 0
            if idx not in colour_index:
                colour_index[idx] = len(C)
                r, g, bl = palette[idx] if palette else (128, 128, 128)
                C.append((r / 128.0, g / 128.0, bl / 128.0, 1.0))
            adjs.append((base_v + k, 0, colour_index[idx], base_v + k))
        prims = []
        for offs_, idxs in chunks_per_adc(len(b.pos), b.adc):
            # an EVEN offset keeps the winding; an ODD one reverses it
            prims.append(('str' if offs_ % 2 == 0 else 'stp', idxs))
        if prims:
            packets.append((tex_name(b.texture), adjs, prims))

    if not packets:
        return 0
    per_mtl = {}
    for tex, adjs, prims in packets:
        per_mtl.setdefault(tex, []).append((adjs, prims))
    n_adj = sum(len(a) for _t, a, _p in packets)
    n_tri = sum(len(i) - 2 for _t, _a, p in packets for _k, i in p)

    with io.open(path, 'w', encoding='utf-8') as f:
        f.write(MOD_HEADER % (len(V), 1, len(C), len(T1), len(per_mtl),
                           n_adj, n_tri))
        for x, y, z in V:
            f.write('v\t%f\t%f\t%f\n' % (x, y, z))
        f.write('n\t0.000000\t1.000000\t0.000000\n')
        for c in C:
            f.write('c\t%f\t%f\t%f\t%f\n' % c)
        for u, w in T1:
            f.write('t1\t%f\t%f\n' % (u, w))
        f.write('\n')
        for tex, blocks in per_mtl.items():
            f.write('mtl %s {\n\tpackets:\t%d\n\tprimitives:\t%d\n'
                    '\ttextures:\t1\n\tillum: diffuse\n'
                    '\tambient:\t0.000000 0.000000 0.000000\n'
                    '\tdiffuse:\t1.000000 1.000000 1.000000\n'
                    '\tspecular:\t0.000000 0.000000 0.000000\n'
                    '\ttexture: 0 "%s"\n\tattributes:\t0\n}\n\n'
                    % (tex, len(blocks),
                       sum(len(i) - 2 for _a, p in blocks for _k, i in p), tex))
            for adjs, prims in blocks:
                f.write('packet %d %d 1 {\n' % (len(adjs), len(prims)))
                for v, n, c, t in adjs:
                    f.write('\tadj\t%d\t%d\t%d\t%d\t-1\t0\n' % (v, n, c, t))
                base = adjs[0][0]
                for kind, idxs in prims:
                    f.write('\t%s\t%d\t%s\n'
                            % (kind, len(idxs), '\t'.join(str(i) for i in idxs)))
                f.write('\tmtx 0 \n}\n\n')
    return len(packets)

if __name__ == '__main__':
    main()
