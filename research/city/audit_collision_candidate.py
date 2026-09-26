#!/usr/bin/env python3
"""Audit two MC3 city bounds and report every binary difference."""
import os
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys


TOOLKIT = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))
sys.path.insert(0, str(TOOLKIT))
from mc3_bound import Bound, check


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def tree_summary(bound, kind):
    node = bound.trees[kind]
    payload = bound.off(bound.u32(node + (0x94 if kind == 9 else 0x78)))
    _root, index_offset, index_count = bound.payload(node, kind)
    indices = struct.unpack_from('<%dH' % index_count, bound.data, index_offset)
    leaves = sorted(bound.leaves(node, kind))
    cursor = 0
    partition_ok = True
    for start, count, _slot in leaves:
        if start != cursor:
            partition_ok = False
            break
        cursor += count
    ok_area, bad_area = check(bound, kind)
    vertices, polygons = bound.geometry(node)
    return {
        'node_offset': node,
        'payload_offset': payload,
        'node_vertices': len(vertices),
        'node_polygons': len(polygons),
        'payload_polygons': bound.u32(payload + 0x08),
        'payload_vertices': bound.u32(payload + 0x0C),
        'payload_references': bound.u32(payload + 0x10),
        'leaf_count': len(leaves),
        'leaf_partition_ok': partition_ok and cursor == index_count,
        'max_polygon_reference': max(indices) if indices else None,
        'polygon_references_in_range': not indices or max(indices) < len(polygons),
        'areas_ok': ok_area,
        'areas_bad': bad_area,
    }


def summarize(path):
    bound = Bound(path)
    return {
        'path': str(path),
        'size': len(bound.data),
        'sha256': sha256(path),
        'root_vertices': bound.verts_total,
        'root_polygons': bound.polys_total,
        'trees': {str(kind): tree_summary(bound, kind) for kind in sorted(bound.trees)},
    }


def differences(a, b):
    if len(a) != len(b):
        return {'same_size': False, 'old_size': len(a), 'new_size': len(b)}
    offsets = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    ranges = []
    for off in offsets:
        if not ranges or off != ranges[-1][-1] + 1:
            ranges.append([off])
        else:
            ranges[-1].append(off)
    return {
        'same_size': True,
        'different_bytes': len(offsets),
        'ranges': [
            {
                'start': run[0],
                'end': run[-1],
                'old_hex': a[run[0]:run[-1] + 1].hex().upper(),
                'new_hex': b[run[0]:run[-1] + 1].hex().upper(),
            }
            for run in ranges
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('old')
    parser.add_argument('new')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    old_bytes = Path(args.old).read_bytes()
    new_bytes = Path(args.new).read_bytes()
    report = {
        'old': summarize(args.old),
        'new': summarize(args.new),
        'binary_diff': differences(old_bytes, new_bytes),
    }
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    print(rendered)
    if args.out:
        args.out.write_text(rendered + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
