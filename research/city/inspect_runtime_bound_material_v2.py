#!/usr/bin/env python3
"""Compare geometry/material of a *_bnd.pck with its copy loaded in RAM."""

import os
import argparse
import importlib.util
import struct
import sys
from collections import Counter
from pathlib import Path


def u32(data, offset):
    return struct.unpack_from('<I', data, offset)[0]


def ee_memory(path):
    inject_path = Path(os.path.join(os.environ.get('MC3BOOT', 'mc3boot'), 'mc3_inject.py'))
    spec = importlib.util.spec_from_file_location('mc3_inject_bound_scan',
                                                   inject_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(inject_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ee_from_savestate(Path(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('state')
    parser.add_argument('pck')
    parser.add_argument('--tools', required=True)
    args = parser.parse_args()

    sys.path.insert(0, args.tools)
    import mc3_bound as MB

    ram = ee_memory(args.state)
    bound = MB.Bound(args.pck)
    for kind in (9, 10):
        node = bound.trees[kind]
        file_poly = bound.off(bound.u32(node + 0x6C))
        count = bound.u32(node + 0x50)
        needle = bound.data[file_poly:file_poly + min(512, count * 32)]
        hits = []
        at = ram.find(needle)
        while at >= 0:
            hits.append(at)
            at = ram.find(needle, at + 1)
        print('type%d: file node=%#x poly=%#x count=%d ram hits=%s' %
              (kind, node, file_poly, count,
               ','.join(hex(item) for item in hits) or 'none'))
        if len(hits) != 1:
            continue

        runtime_poly = hits[0]
        delta = runtime_poly - (bound.base + file_poly)
        runtime_node = bound.base + node + delta
        node_count = u32(ram, runtime_node + 0x50)
        node_poly = u32(ram, runtime_node + 0x6C)
        material_ptr = u32(ram, runtime_node + 0x70)
        runtime_indices = [ram[node_poly + index * 32 + 0x0C]
                           for index in range(node_count)]
        max_index = max(runtime_indices) if runtime_indices else -1
        palette = [u32(ram, material_ptr + 4 * index)
                   for index in range(max_index + 1)]
        file_blob = bound.data[file_poly:file_poly + count * 32]
        runtime_blob = ram[node_poly:node_poly + node_count * 32]
        print('  delta=%#x runtime node=%#x node poly=%#x' %
              (delta, runtime_node, node_poly))
        print('  node count=%d polygon bytes equal=%s' %
              (node_count, file_blob == runtime_blob))
        print('  local material histogram=%s' %
              dict(sorted(Counter(runtime_indices).items())))
        print('  global palette=%s' % palette)


if __name__ == '__main__':
    main()
