#!/usr/bin/env python3
"""What stands at a point of a MC2 city, and which of its pieces get MC2's
per-vertex colour (a `_0_main` PCP0 in <time>_cpvs.rsc) and which do not.

    python mc2_cpv_probe.py --city losangeles -493 900 [--reach 5]

For every component (world-baked model) and instance (template + matrix) whose
.hood box covers the point: the model, its LOD 0 PMD0 pieces, the `_0_main`
PCP0 that colours it (if any) and which pieces that PCP0 references. A piece no
PCP0 references is drawn by the renderer with the flat non-CPVS colour, which
shows as a seam against lit neighbours.
"""
import argparse
import os
import sys

import mc2_rsc as R

MC2 = os.environ.get('MC2_PS2_ASSETS', 'mc2_ps2/assets')


def load(city, tod):
    rsc = R.Rsc(os.path.join(MC2, 'resource', city, city + '.rsc'))
    cpvs = R.open_cpvs(rsc, tod)
    comps, insts = R.read_hoods(os.path.join(MC2, 'city', city))
    models = R.models_by_name(rsc)
    pcp = {}
    for index, kind, key, lod, pas in R.cpv_entries(cpvs):
        if lod == 0 and pas == 'main':
            pcp[(kind, key)] = index
    return rsc, cpvs, comps, insts, models, pcp


def refs(rsc, cpvs, index):
    """PMD0 entry indices a PCP0 takes geometry from."""
    pmap = R.payload_map(rsc)
    out = []
    for (_p, did, qwc, addr, _s) in R.pcp_chain(cpvs, index)[0]:
        if did != 'ref':
            continue
        src, want = ((addr >> 20) & 0xFFF) - 1, addr & 0xFFFF0
        for cand in (src, src + 4096, src + 8192):
            if (want, qwc) in pmap.get(cand, ()):
                if cand not in out:
                    out.append(cand)
                break
    return out


def pieces(rsc, model):
    out = []
    for h in R.model_blocks(model, 0):
        got = rsc.resolve(h)
        if got is not None:
            out.append(got[1])
    return out


def describe(rsc, cpvs, model, index):
    ps = pieces(rsc, model)
    used = refs(rsc, cpvs, index) if index is not None else []
    lit = [p for p in ps if p in used]
    flat = [p for p in ps if p not in used]
    return ps, lit, flat


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--city', default='losangeles')
    ap.add_argument('--tod', default='midnight_clear')
    ap.add_argument('x', type=float)
    ap.add_argument('z', type=float)
    ap.add_argument('--reach', type=float, default=5.0)
    a = ap.parse_args()
    rsc, cpvs, comps, insts, models, pcp = load(a.city, a.tod)
    near = lambda o: (o.emin[0] - a.reach <= a.x <= o.emax[0] + a.reach and
                      o.emin[2] - a.reach <= a.z <= o.emax[2] + a.reach)
    for c in comps:
        if not near(c):
            continue
        m = models.get(c.name.lower())
        idx = pcp.get(('comp', c.name.lower()))
        if m is None:
            print('component %s: no model' % c.name)
            continue
        ps, lit, flat = describe(rsc, cpvs, m, idx)
        print('component %-40s pcp %-6s pieces %s lit %s FLAT %s  box x %.0f..%.0f z %.0f..%.0f y %.1f..%.1f' % (
            c.name, idx, ps, lit, flat, c.emin[0], c.emax[0], c.emin[2], c.emax[2], c.emin[1], c.emax[1]))
    for i in insts:
        if not near(i):
            continue
        m = models.get(i.type.lower())
        idx = pcp.get(('inst', (i.type.lower(), i.extension)))
        if m is None:
            print('instance %s ext %d: no model' % (i.type, i.extension))
            continue
        ps, lit, flat = describe(rsc, cpvs, m, idx)
        print('instance  %-40s ext %-5d pcp %-6s pieces %s lit %s FLAT %s  box x %.0f..%.0f z %.0f..%.0f y %.1f..%.1f' % (
            i.type, i.extension, idx, ps, lit, flat, i.emin[0], i.emax[0], i.emin[2], i.emax[2],
            i.emin[1], i.emax[1]))
    return 0


if __name__ == '__main__':
    sys.exit(main())
