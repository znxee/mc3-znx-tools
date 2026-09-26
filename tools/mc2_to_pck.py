"""mc2_to_pck.py - Midnight Club 2 (PS2) city geometry -> MC3 `.mesh.pck`

Closes the last gap between `mc2_rsc.py`, which decodes MC2's `PMD0`, and
`mc3_mesh_convert.py`, which writes MC3 packets. Everything it needs was
measured, not guessed:

  * a `PMD0` batch IS one triangle strip with ADC flags, and `Batch.triangles()`
    uses the same parity rule as `tris_of_strip` here -- (k-2,k-1,k) on even k,
    (k-1,k-2,k) on odd -- so the topology maps across untouched.
  * MC3's packets are written with `--keep-strips` (restrip=False), which cuts
    only the strips longer than a packet. Measured over the 294 baked Los
    Angeles components: 6370 packets against 39709 the re-stripifying way, and
    3.27 MB against 5.34 MB, with triangles AND winding identical in 294 of 294.

What does NOT come across, and why:

  * The VIF chain itself. MC2 unpacks position as V3-16u at base+4 and decodes
    it as 131072 + raw/64 - bias, with the base coming from `itop`; MC3 unpacks
    at the fixed VU address 0x00EE and multiplies by a scale. Different
    addresses, formats and microprogram, so the geometry crosses as DATA and is
    re-emitted in MC3's own packet.
  * The material index. A `.mesh.pck` stores a u16 slot per group, not a name,
    and the slot belongs to a shader group that lives outside the mesh. The MC2
    texture NAME is written to the manifest so it can be mapped; without
    `--materials` every group gets slot 0.
  * Per-vertex colour. MC2 keeps it in `<hour>_cpvs.rsc` as a palette index, not
    in the geometry, so the meshes come out with the default diffuse.

Usage:

    python mc2_to_pck.py <city.rsc>                   # everything, to output/
    python mc2_to_pck.py <city.rsc> --filter rd_23    # just the ones matching
    python mc2_to_pck.py <city.rsc> --materials m.tsv # texture name -> slot
    python mc2_to_pck.py <city.rsc> --manifest-only    # tables only, no .pck

Writes into the output folder: one `.mesh.pck` per model, a
`<city>_place.tsv` in the same column layout `mc3_city_build.py --place`
reads, and a `<city>_materials.tsv` pairing each group with the MC2 texture
name it draws with.
"""
import argparse
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mc2_rsc as R
import mc3_mesh_convert as M
import mc3_output


IDENT = (1, 0, 0, 0, 1, 0, 0, 0, 1)


def batches_of(rsc, model, lod=None):
    got = []
    for h in R.model_blocks(model, lod):
        r = rsc.resolve(h)
        if r is not None:
            got.extend(R.read_pmd(rsc, r[1])[0])
    return got


def mesh_from_batches(rsc, batches):
    """One MC3 Mesh, one group per MC2 texture, one strip per batch.

    The fixed-point exponent is fitted to the model's own extent, the way
    rmcModelGeom::Load fits it when the game loads text: a city piece in world
    coordinates would saturate s16 at the converter's default 1/4096.
    """
    allpos = [p for b in batches for p in b.pos]
    if not allpos:
        return None, []
    scale = M._fit_scale(allpos)
    inv = 1.0 / scale
    by = collections.OrderedDict()
    for b in batches:
        by.setdefault(b.texture, []).append(b)
    mesh = M.Mesh()
    mesh.scale = scale
    mesh.source = 'mc2-pmd0'
    textures = []
    for handle, bs in by.items():
        strips = []
        for b in bs:
            st = []
            for k, p in enumerate(b.pos):
                uv = b.uv[k] if k < len(b.uv) else (0.0, 0.0)
                # MC2's 0x80 is the GS ADC bit, which is exactly what Vtx.skip
                # means here: do not close a triangle on this vertex.
                skip = bool(k < len(b.adc) and (b.adc[k] & 0x80))
                st.append(M.Vtx(tuple(M._clampi(c * inv, -32768, 32767) for c in p),
                                uv, (0.0, 0.0, 1.0), skip))
            if len(st) >= 3:
                strips.append(st)
        if strips:
            mesh.groups.append({'material': 0, 'strips': strips})
            textures.append(rsc.texture_name(handle) or 'none')
    if not mesh.groups:
        return None, []
    # MC2 ships no normals; without these every surface exports flat with a zero
    # normal, and the normal stream is also where MC3 carries the ADC bit.
    M._recompute_normals(mesh)
    return mesh, textures


def read_extents(folder, city, comps, insts):
    """extents of `<city>.lvl`, falling back to the data box if there is none."""
    lvl = os.path.join(folder, city + '.lvl')
    if os.path.isfile(lvl):
        txt = open(lvl, 'r', errors='replace').read()
        got = {}
        for key in ('extents_min', 'extents_max'):
            m = re.search(key + r'\s*\{([^}]*)\}', txt)
            if m:
                got[key] = [float(x) for x in m.group(1).split()][:3]
        if len(got) == 2:
            return got['extents_min'], got['extents_max']
    boxes = [c.emin for c in comps] + [i.emin for i in insts]
    high_ = [c.emax for c in comps] + [i.emax for i in insts]
    if not boxes:
        return [0.0] * 3, [0.0] * 3
    return ([min(v[k] for v in boxes) for k in range(3)],
            [max(v[k] for v in high_) for k in range(3)])


def filename(name):
    return re.sub(r'[^A-Za-z0-9_.#-]', '_', name)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('file_')
    ap.add_argument('--out')
    ap.add_argument('--filter', dest='filter_')
    ap.add_argument('--lod', type=int)
    ap.add_argument('--materials', help='TSV with "texture_name<TAB>slot"')
    ap.add_argument('--limit', type=int)
    ap.add_argument('--manifest-only', action='store_true')
    a = ap.parse_args()

    rsc = R.Rsc(a.file_)
    city = os.path.splitext(os.path.basename(rsc.path))[0]
    out = a.out or os.path.join(mc3_output.folder(), 'mc2_pck', city)
    if not os.path.isdir(out):
        os.makedirs(out)

    mapping = {}
    if a.materials:
        for l in open(a.materials, 'r', errors='replace'):
            p = l.split('#')[0].split('\t')
            if len(p) >= 2 and p[0].strip():
                mapping[p[0].strip().lower()] = int(p[1], 0)

    city_folder = R.city_folder(rsc.path)
    comps, insts = R.read_hoods(city_folder)
    byKind = collections.defaultdict(list)
    for i in insts:
        byKind[i.type.lower()].append(i)
    # The .lvl's own extents, not the bounding box of the data: the cell grid
    # is derived from them, and they are round numbers the data does not quite
    # reach (LA declares -2000..1600 while the geometry stops at -1989.7).
    emin, emax = read_extents(city_folder, city, comps, insts)

    lines = []
    mats = []
    n_pck = n_group = n_tri = 0
    bytes_pck = 0
    models_dir = R.read_models(rsc)
    if a.filter_:
        models_dir = [m for m in models_dir if a.filter_ in m.name]
    if a.limit:
        models_dir = models_dir[:a.limit]

    for mo in models_dir:
        bs = batches_of(rsc, mo, a.lod)
        if not bs:
            continue
        mesh, textures = mesh_from_batches(rsc, bs)
        if mesh is None:
            continue
        for gi, tex in enumerate(textures):
            slot = mapping.get(os.path.basename(tex.replace('\\', '/')).lower(), 0)
            mesh.groups[gi]['material'] = slot
            mats.append((mo.name, gi, tex, slot))
        fname = filename(mo.name) + '.mesh.pck'
        if not a.manifest_only:
            bytes_pck += M.write_pck(mesh, os.path.join(out, fname), restrip=False)
        n_pck += 1
        n_group += len(mesh.groups)
        n_tri += mesh.stats()[3]

        key = mo.name.lower()
        if key in byKind:
            for i in byKind[key]:
                lines.append(('inst', mo.name,
                               [i.rot[0][0], i.rot[0][1], i.rot[0][2],
                                i.rot[1][0], i.rot[1][1], i.rot[1][2],
                                i.rot[2][0], i.rot[2][1], i.rot[2][2]],
                               i.pos, i.emin, i.emax, i.hood))
        else:
            c = next((c for c in comps if c.name.lower() == key), None)
            lines.append(('comp', mo.name, list(IDENT), (0.0, 0.0, 0.0),
                           c.emin if c else (0, 0, 0), c.emax if c else (0, 0, 0),
                           c.hood if c else '-'))

    place = os.path.join(out, city + '_place.tsv')
    with open(place, 'w') as f:
        f.write('# %s: generated by mc2_to_pck.py from %s\n'
                % (city, os.path.basename(rsc.path)))
        f.write('# extents_min\t%f\t%f\t%f\n' % tuple(emin))
        f.write('# extents_max\t%f\t%f\t%f\n' % tuple(emax))
        f.write('\t'.join(['kind', 'name', 'lod', 'part',
                           'm00', 'm01', 'm02', 'm10', 'm11', 'm12',
                           'm20', 'm21', 'm22', 'tx', 'ty', 'tz',
                           'eminx', 'eminy', 'eminz', 'emaxx', 'emaxy', 'emaxz',
                           'hood']) + '\n')
        for kind, name, rot, pos, mn, mx, hood in lines:
            f.write('%s\t%s\t0\tmain\t%s\t%s\t%s\t%s\t%s\n'
                    % (kind, name,
                       '\t'.join('%f' % x for x in rot),
                       '\t'.join('%f' % x for x in pos),
                       '\t'.join('%f' % x for x in mn),
                       '\t'.join('%f' % x for x in mx),
                       # without the extension, which is how mc2_port's
                       # place.tsv already named the hood
                       os.path.splitext(hood)[0]))

    table = os.path.join(out, city + '_materials.tsv')
    with open(table, 'w') as f:
        f.write('# model\tgroup\tmc2_texture\tslot\n')
        for name, gi, tex, slot in mats:
            f.write('%s\t%d\t%s\t%d\n' % (name, gi, tex, slot))

    distinct = len({t for _, _, t, _ in mats})
    print('%s' % out)
    print('  %d models -> .mesh.pck  (%d groups, %d triangles, %d bytes)'
          % (n_pck, n_group, n_tri, bytes_pck))
    print('  %s  %d lines (%d comp, %d inst)'
          % (os.path.basename(place), len(lines),
             sum(1 for l in lines if l[0] == 'comp'),
             sum(1 for l in lines if l[0] == 'inst')))
    print('  %s  %d groups, %d distinct textures, %d mapped to slot != 0'
          % (os.path.basename(table), len(mats), distinct,
             sum(1 for _, _, _, s in mats if s)))
    if not mapping:
        print('  WARNING: without --materials, every group came out with slot 0. The')
        print('  texture name is in _materials.tsv for you to map.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
