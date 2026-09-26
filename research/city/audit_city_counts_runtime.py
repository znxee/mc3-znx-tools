"""Audit generated City counts and follow every runtime pointer, including leaves.

File validation uses independent group-table prefixes, Instance CPV indices,
and CPV block/vertex counts. Runtime checks require a matching loaded PCK;
file virtual addresses are never classified by an arbitrary low-address cutoff.
"""
import os
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys

TOOLKIT = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class Audit:
    def __init__(self, path, state=None):
        self.path = Path(path)
        self.d = self.path.read_bytes()
        self.base = struct.unpack_from('<I', self.d)[0] - 0x80
        self.stats = Counter()
        self.errors = Counter()
        self.examples = []
        self.ram = None
        self.delta = None
        if state:
            inj = module('inject_count_audit', Path(os.environ.get("MC3BOOT", "mc3boot")) / "mc3_inject.py")
            self.ram = inj.ee_from_savestate(state)
            self.city = self.ru(0x615B40)
            self.delta = self.city - self.u(0)
            if self.ru(self.city + 0x14) != self.u(0x94):
                raise ValueError('Savestate/PCK hood counts differ; wrong build')

    def u(self, off):
        return struct.unpack_from('<I', self.d, off)[0]

    def h(self, off):
        return struct.unpack_from('<H', self.d, off)[0]

    def off(self, va, size=1):
        off = va - self.base
        if not va or off < 0x80 or off + size > len(self.d):
            raise ValueError(f'file pointer {va:08X} length {size} outside PCK')
        return off

    def ru(self, va):
        if not 0 <= va <= len(self.ram) - 4:
            raise ValueError(f'runtime pointer {va:08X} outside EE RAM')
        return struct.unpack_from('<I', self.ram, va)[0]

    def fail(self, category, context, **values):
        self.errors[category] += 1
        if len(self.examples) < 24:
            self.examples.append(dict(category=category, context=context, **values))

    def pointer(self, field, category, context):
        """Read the real runtime field; compare against the relocated file VA."""
        va = self.u(field)
        if self.ram is not None:
            actual = self.ru(self.base + field + self.delta)
            expected = va + self.delta if va else 0
            self.stats['runtime_' + category + '_checked'] += 1
            if actual != expected:
                self.fail('runtime_' + category, context,
                          field=f'{self.base + field + self.delta:08X}',
                          actual=f'{actual:08X}', expected=f'{expected:08X}',
                          file=f'{va:08X}')
        return va

    def scalar(self, off, length, category, context):
        if self.ram is not None:
            r = self.base + off + self.delta
            if self.ram[r:r+length] != self.d[off:off+length]:
                self.fail('runtime_' + category, context, file_off=f'{off:08X}')

    def collect(self):
        types = {}
        nh = self.u(0x94)
        table = self.off(self.pointer(0x98, 'hood_table', 'root'), 20 * nh)
        for h in range(nh):
            ent = table + 20 * h
            for kind, size, at in [('component', 28, 4), ('instance', 96, 12)]:
                n = self.u(ent + at)
                arr = self.off(self.pointer(ent+at+4, 'hood_array', (h, kind)), n*size)
                self.scalar(ent+at, 4, 'hood_count', (h, kind))
                self.stats[kind + 's'] += n
                for i in range(n):
                    rec = arr + size*i
                    cd = self.off(self.pointer(rec+12, 'type', (h, kind, i)), 0x70)
                    info = types.setdefault(cd, dict(kind=kind, slots=1))
                    if kind == 'instance':
                        matrix = self.base + rec + 0x20
                        if matrix & 15:
                            self.fail('instance_matrix_alignment', (h, kind, i),
                                      address=f'{matrix:08X}', mod16=matrix & 15)
                        if self.ram is not None and (matrix + self.delta) & 15:
                            self.fail('runtime_instance_matrix_alignment',
                                      (h, kind, i),
                                      address=f'{matrix + self.delta:08X}',
                                      mod16=(matrix + self.delta) & 15)
                        info['slots'] = max(info['slots'], self.u(rec+0x1c)+1)
                        self.scalar(rec+0x20, 48, 'matrix', (h, kind, i))
        meshes = {}
        for cd, info in types.items():
            for lod in range(10):
                va = self.pointer(cd+0x3c+4*lod, 'lod', (info['kind'], cd, lod))
                if not va:
                    continue
                mesh = self.off(va, 0x20)
                required = meshes.setdefault(mesh, dict(kind=info['kind'], slots=info['slots']))
                required['slots'] = max(required['slots'], info['slots'])
        self.stats['types'] = len(types)
        return meshes

    def mesh(self, m, info):
        ctx = dict(mesh=f'{self.base+m:08X}', kind=info['kind'])
        ng, ns = self.h(m+8), self.h(m+10)
        self.scalar(m+8, 4, 'mesh_counts', ctx)
        table = self.off(self.pointer(m+16, 'geometry_table', ctx), ng*8)
        prefix = self.u(table-16)
        self.stats['meshes'] += 1
        self.stats[info['kind']+'_meshes'] += 1
        if 1 <= prefix <= 4096 and prefix != ng:
            self.fail('group_count', ctx, declared=ng, prefix=prefix)
        if ns < info['slots']:
            self.fail('cpv_slot_count', ctx, declared=ns, required=info['slots'])
        if not ng or not ns:
            self.fail('empty_generated_mesh', ctx, groups=ng, slots=ns)
            return
        materials = self.off(self.pointer(m+12, 'materials', ctx), ng*2)
        self.scalar(materials, ng*2, 'material_values', ctx)
        slot_array = self.off(self.pointer(m+20, 'cpv_slots', ctx), ns*4)
        blocks = []
        for g in range(ng):
            gc = dict(ctx, group=g)
            desc = table + g*8
            count = self.h(desc+4)
            self.scalar(desc+4, 4, 'packet_counts', gc)
            lst = self.off(self.pointer(desc, 'packet_list', gc), count*8)
            vertices = []
            for b in range(count):
                bc = dict(gc, block=b)
                entry = lst+b*8
                qw, nv = self.h(entry+4), self.h(entry+6)
                self.scalar(entry+4, 4, 'packet_dimensions', bc)
                ptr = self.pointer(entry, 'vif_leaf', bc)
                payload = self.off(ptr, qw*16)
                self.scalar(payload, qw*16, 'vif_payload', bc)
                vertices.append(nv)
                self.stats['packets'] += 1
            blocks.append(vertices)
        self.stats['groups'] += ng
        for s in range(ns):
            sc = dict(ctx, slot=s)
            tab = self.off(self.pointer(slot_array+s*4, 'cpv_group_table', sc), ng*8)
            self.stats['cpv_slots'] += 1
            for g, vertices in enumerate(blocks):
                gc = dict(sc, group=g)
                pa = self.off(self.pointer(tab+g*8, 'cpv_counts', gc), 4)
                nb = self.u(pa)
                if nb != len(vertices):
                    self.fail('packet_count_vs_cpv', gc, geometry=len(vertices), cpv=nb)
                pb = self.off(self.pointer(tab+g*8+4, 'cpv_payload_table', gc), nb*4)
                self.off(self.base+pa, 4+nb*4)
                self.scalar(pa, 4+nb*4, 'cpv_counts', gc)
                self.stats['cpv_groups'] += 1
                for b in range(min(nb, len(vertices))):
                    bc = dict(gc, block=b)
                    nv = self.u(pa+4+4*b)
                    if nv != vertices[b]:
                        self.fail('vertex_count_vs_cpv', bc, geometry=vertices[b], cpv=nv)
                    leaf = self.off(self.pointer(pb+b*4, 'cpv_leaf', bc), nv)
                    self.scalar(leaf, nv, 'cpv_payload', bc)
                    self.stats['cpv_blocks'] += 1

    def run(self):
        meshes = self.collect()
        for m, info in sorted(meshes.items()):
            try:
                self.mesh(m, info)
            except (ValueError, struct.error) as err:
                self.fail('invalid_structure', f'{m:08X}', detail=str(err))
        return dict(path=str(self.path), sha256=hashlib.sha256(self.d).hexdigest(),
                    runtime=self.ram is not None, delta=self.delta,
                    stats=dict(self.stats), errors=dict(self.errors), examples=self.examples,
                    passed=not self.errors)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('pck')
    ap.add_argument('--state')
    ap.add_argument('--report')
    args = ap.parse_args()
    result = Audit(args.pck, args.state).run()
    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    print(rendered)
    if args.report:
        Path(args.report).write_text(rendered+'\n', encoding='utf-8')
    sys.exit(0 if result['passed'] else 1)
