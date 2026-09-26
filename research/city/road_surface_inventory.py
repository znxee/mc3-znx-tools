"""Inventory LA asphalt and palm groups across placement, geometry and texture routes."""
import os
import collections, json, re, sys
from pathlib import Path

T = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))
W = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'research', 'city'))
sys.path[:0] = [str(T), str(W)]
import mc3_city_build as city
from city_vif_recenter import pck_packets

rows, _ = city.read_place(str(T / 'output/la729_place.tsv'))
key = lambda r: ('%s_%s_%s' % (r['name'], r['lod'], r['part'])).lower()
by_key = collections.defaultdict(list)
for row in rows:
    by_key[key(row)].append(row)

textures = json.loads((T / 'output/la_textures_per_model.json').read_text())
native_names = []
for line in (T / 'output/shader_texture_route_20260905/native_texture_map.tsv').read_text().splitlines():
    cells = line.split('\t')
    if len(cells) >= 3 and cells[0].endswith('.tex'):
        native_names.append(cells[0][:-4].lower())
native_slot = {name: i for i, name in enumerate(native_names)}
manifest = json.loads((T / 'output/shader_texture_route_20260905/candidate/modcity_midnight_clear.manifest.json').read_text())
ppf = {x['texture'][:-4].lower(): x for x in manifest['textures']}

mesh_dir = W / 'output/la729_local_components_20260906'
items = []
for model, names in textures.items():
    blob = (mesh_dir / (model + '.mesh.pck')).read_bytes()
    groups = collections.defaultdict(lambda: {'packets': 0, 'vertices': 0})
    for group, _packet, _off, _size, vertices in pck_packets(blob):
        groups[group]['packets'] += 1
        groups[group]['vertices'] += vertices
    placement = by_key.get(model, [])
    for group, texture in enumerate(names):
        if not re.search(r'ashphalt|palm', texture, re.I):
            continue
        route = ppf[texture.lower()]
        items.append({
            'family': 'asphalt' if 'ashphalt' in texture.lower() else 'palm',
            'model': model,
            'kind': placement[0]['kind'] if placement else 'unused',
            'part': model.rsplit('_', 1)[-1],
            'placements': len(placement),
            'hoods': sorted(set(r['hood'] for r in placement)),
            'group': group,
            'texture': texture,
            'city_shader_slot': native_slot[texture.lower()],
            'ppf_texture_index': route['texture_index'],
            'source_format': route['source_format'],
            **groups[group],
        })

def summary(family):
    active = [x for x in items if x['family'] == family and x['kind'] != 'unused']
    active_models = set(x['model'] for x in active)
    placement_rows = [r for model in active_models for r in by_key[model]]
    return {
        'active_groups': len(active),
        'active_models': len(active_models),
        'object_placements': len(placement_rows),
        'group_placements': sum(x['placements'] for x in active),
        'unique_matrices': len(set(tuple(float(r[c]) for c in
            ('m00','m01','m02','m10','m11','m12','m20','m21','m22','tx','ty','tz'))
            for r in placement_rows if r['kind'] == 'inst')),
        'translation_range': {axis: [min((float(r[axis]) for r in placement_rows), default=None),
                                     max((float(r[axis]) for r in placement_rows), default=None)]
                              for axis in ('tx','ty','tz')},
        'packets': sum(x['packets'] for x in active),
        'vertices': sum(x['vertices'] for x in active),
        'by_kind_part': dict(sorted(collections.Counter(
            '%s/%s' % (x['kind'], x['part']) for x in active).items())),
        'textures': dict(sorted(collections.Counter(x['texture'] for x in active).items())),
    }

report = {
    'source_city_sha256': '7239c64fa5f6e2c2693421fec00bfc6a0a52444c195e7949e298a6604d147ce5',
    'asphalt': summary('asphalt'),
    'palm': summary('palm'),
    'interpretation': [
        'Asphalt spans component and instance geometry, so missing asphalt is not explained only by instance matrices.',
        'Most asphalt groups are refl, but a main control population exists; trace both render paths.',
        'The native texture map selects dumps by internal identity; misleading external dump filenames are expected.',
        'Shader/PPF indices need not match: city shaders carry page/offset refs resolved by texture name.',
    ],
    'groups': items,
}
out = W / 'output/road_surface_inventory_20260906.json'
out.write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps({k: report[k] for k in ('asphalt', 'palm', 'interpretation')}, indent=2))
