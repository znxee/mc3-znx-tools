#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build modcity with a subset of LA's neighbourhoods, reproducing the v9 recipe
(local components + fixed chain), and install it into the 9 variants.

Usage:
    python build_hood.py l_downtown
    python build_hood.py l_downtown,l_ghetto          # cumulative
    python build_hood.py l_downtown --assemble-only   # only build, do not install

The .ppf, props, garage_props, fog, peds, traffic and bnd are NOT touched - the
.ppf is already a superset of the whole of LA, so it covers any subset of
neighbourhoods.
"""
import argparse, os, subprocess, sys, shutil, hashlib

ROOT = os.path.dirname(os.path.abspath(__file__))
PLACE = os.path.join(ROOT, 'output', 'la729_place.tsv')
MODELS = os.path.join(ROOT, 'output', 'mc2_losangeles', 'model')
PCK_MESH = os.path.join(ROOT, 'output', 'city_v7_vif_relocation_20260906',
                         'la729_local_components_20260906')
TEXTURES = os.path.join(ROOT, 'output', 'la_tex_pck')
ID_MAP = os.path.join(ROOT, 'output', 'shader_texture_route_20260905',
                       'native_texture_map.tsv')
TEX_MAP = os.path.join(ROOT, 'output', 'la_textures_per_model.json')
MANIF_PPF = os.path.join(ROOT, 'output', 'shader_texture_route_20260905',
                         'candidate', 'modcity_midnight_clear.manifest.json')
DONOR = os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/resources/city/atlanta_midnight_clear.pck')
DEST = os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/resources/city')
VARIANTS = ['dawn_clear', 'dawn_cloudy', 'dawn_rainy',
             'dusk_clear', 'dusk_cloudy', 'dusk_rainy',
             'midnight_clear', 'midnight_cloudy', 'midnight_rainy']

def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()

def filter_place(hoods, out_path):
    """Copy the 4 header lines + only the lines of the requested hoods."""
    wanted = set(hoods)
    lines = open(PLACE, encoding='utf-8', errors='replace').read().splitlines()
    hdr = lines[:4]                      # comments + column line
    idx_hood = hdr[3].split('\t').index('hood')
    body = []
    counts_ = {}
    for ln in lines[4:]:
        if not ln.strip():
            continue
        col = ln.split('\t')
        if len(col) <= idx_hood:
            continue
        h = col[idx_hood]
        if h in wanted:
            body.append(ln)
            counts_[h] = counts_.get(h, 0) + 1
    with open(out_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(hdr) + '\n' + '\n'.join(body) + '\n')
    return counts_

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('hoods', help='comma-separated hood names (l_downtown,...)')
    ap.add_argument('--assemble-only', action='store_true', help='do not install into the 9 variants')
    a = ap.parse_args()

    hoods = [h.strip() for h in a.hoods.split(',') if h.strip()]
    label = '_'.join(h.replace('l_', '') for h in hoods)
    place_tmp = os.path.join(ROOT, 'output', 'hood_test_%s_place.tsv' % label)
    out_path = os.path.join(ROOT, 'output', 'modcity_hood_%s.pck' % label)

    counter_ = filter_place(hoods, place_tmp)
    if not counter_:
        sys.exit('no line for: %s' % hoods)
    print('hoods:', ', '.join('%s=%d' % (k, v) for k, v in sorted(counter_.items())),
          '  total=%d' % sum(counter_.values()))

    cmd = [sys.executable, os.path.join(ROOT, 'mc3_city_build.py'),
           '--donor', DONOR, '--place', place_tmp, '--models-dir', MODELS,
           '--pck-mesh', PCK_MESH, '--textures', TEXTURES,
           '--identity-map', ID_MAP, '--texture-map', TEX_MAP,
           '--ppf-manifest', MANIF_PPF, '--max-inst', '8511',
 '--out-path', out_path]
    print('>>', ' '.join('"%s"' % c if ' ' in c else c for c in cmd))
    r = subprocess.run(cmd)
    if r.returncode != 0 or not os.path.exists(out_path):
        sys.exit('build failed (rc=%d)' % r.returncode)
    print('built: %s  (%d bytes, sha %s)' % (out_path, os.path.getsize(out_path), sha(out_path)[:8]))

    if a.assemble_only:
        print('--assemble-only: not installed.')
        return
    for v in VARIANTS:
        dst = os.path.join(DEST, 'modcity_%s.pck' % v)
        shutil.copyfile(out_path, dst)
    # verify
    target = sha(out_path)
    ok = sum(1 for v in VARIANTS
             if sha(os.path.join(DEST, 'modcity_%s.pck' % v)) == target)
    print('installed into %d/9 variants (hash checked).' % ok)
    print('REMINDER: .ppf/props/peds/traffic/bnd untouched; restart without an old savestate.')

if __name__ == '__main__':
    main()
