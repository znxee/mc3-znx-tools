"""Map WSHD runtime model pointers back to PCK types and place.tsv rows."""
import os
import argparse
import importlib.util
from pathlib import Path
import struct
import sys

TOOLKIT = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def gap(a, b):
    """Largest absolute difference between two AABBs."""
    return max(abs(a[side][axis] - b[side][axis])
               for side in range(2) for axis in range(3))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('pck')
    ap.add_argument('--place', required=True)
    ap.add_argument('--delta', required=True, type=lambda x: int(x, 0))
    ap.add_argument('models', nargs='+', type=lambda x: int(x, 0))
    args = ap.parse_args()

    audit = load('cityaudit_wshd', TOOLKIT / 'mc3_cityaudit.py')
    pck, hf = audit._open(args.pck)
    instances, types = audit.collect(pck, hf)
    rows = audit.read_place(args.place)
    by_matrix = {}
    for row in rows:
        by_matrix.setdefault(audit._mat(row), []).append(row)

    for runtime_model in args.models:
        serialized = runtime_model - args.delta
        found = []
        for type_va, (_stored_name, cd, meshes) in types.items():
            for mesh in meshes:
                if pck.V(mesh) == serialized:
                    found.append((type_va, cd, mesh))
        print('runtime model %08X -> serialized %08X' % (runtime_model, serialized))
        if not found:
            print('  NOT FOUND')
            continue
        for type_va, cd, mesh in found:
            local = audit._aabb_from_vif(pck, mesh)
            owned = [(off, mat) for off, va, mat, _center, _radius in instances
                     if va == type_va]
            print('  type %08X  cd file+%06X  mesh file+%06X  instances %d'
                  % (type_va, cd, mesh, len(owned)))
            if local:
                print('  VIF AABB min=(%.3f, %.3f, %.3f) max=(%.3f, %.3f, %.3f)'
                      % tuple(local[0] + local[1]))
            candidates = {}
            for off, mat in owned:
                world_ptr = pck.V(off + 0x20) + args.delta
                print('  Instance file+%06X world=%08X T=(%.3f, %.3f, %.3f)'
                      % (off, world_ptr, mat[9], mat[10], mat[11]))
                for row in by_matrix.get(mat, []):
                    key = (row['name'], row['lod'], row['part'], row['hood'])
                    source_box = audit._box(row)
                    world_box = (audit._transform(local[0], local[1], mat)
                                 if local else None)
                    candidates[key] = (gap(world_box, source_box)
                                       if world_box else 1e30)
            for key, error in sorted(candidates.items(), key=lambda item: item[1])[:12]:
                print('    world AABB source gap %9.4f  %s lod=%s part=%s hood=%s'
                      % ((error,) + key))
        print()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
