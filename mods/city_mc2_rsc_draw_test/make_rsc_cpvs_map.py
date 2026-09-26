#!/usr/bin/env python3
"""Map the RSCM targets and RINS placements to their MC2 CPVS draw chains.

In MC2 a lit model is drawn by a PCP0 in `<time>_<weather>_cpvs.rsc`, not by
its PMD0: the PCP0 carries the per-vertex colours inline and `ref`s the
geometry payloads of the PMD0s in the city container. The runtime reads the
original cpvs file and walks those chains itself; this file only says WHICH
PCP0 belongs to which draw, so it holds indices and no MC2 data:

    header   RCPM, 1, total bytes, target count, instance count, 0, 0, 0
    target   u32 per RSCM record: bit 31 = its PMD is drawn by a component
             PCP0 (skip the PMD); low 16 bits = PCP0 index + 1 when this is
             the record that draws that PCP0 (the lowest referenced one)
    instance u32 per RINS placement: PCP0 index + 1, or 0 = draw the PMDs

Only LOD 0 `_main` chains are used (`_refl` is the reflection pass). PCP0
indices and names are identical in all nine LA cpvs variants (checked here).

    python make_rsc_cpvs_map.py <assets/resource/losangeles/losangeles.rsc> OUT...
"""
import argparse
import glob
import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import mc2_rsc as R

RCPM = 0x4D504352
COVERED = 0x80000000


def pcp_names(cpvs):
    return {(h & 0x3FFF) - 1: n for h, (f, n) in cpvs.names.items() if f == 'PCP0'}


def build(rsc_path: Path, manifest: bytes, instance_bank: bytes):
    rsc = R.Rsc(str(rsc_path))
    folder = os.path.dirname(str(rsc_path))
    variants = sorted(glob.glob(os.path.join(folder, '*_cpvs.rsc')))
    if not variants:
        raise ValueError('no *_cpvs.rsc beside the city container')
    cpvs = R.Rsc(variants[0])
    names = pcp_names(cpvs)
    for other in variants[1:]:
        if pcp_names(R.Rsc(other)) != names:
            raise ValueError('%s numbers its PCP0s differently' % other)

    count = struct.unpack_from('<I', manifest, 12)[0]
    targets = [struct.unpack_from('<3I', manifest, 16 + i * 28) for i in range(count)]
    by_pmd = {pmd: i for i, (pmd, _model, _flags) in enumerate(targets)}
    pmap = R.payload_map(rsc)

    def referenced(index):
        """RSCM record indices a PCP0 reads geometry from, in chain order."""
        out = []
        for (_p, did, qwc, addr, _s) in R.pcp_chain(cpvs, index)[0]:
            if did != 'ref':
                continue
            src, want = ((addr >> 20) & 0xFFF) - 1, addr & 0xFFFF0
            hits = [c for c in (src, src + 4096, src + 8192)
                    if (want, qwc) in pmap.get(c, ()) and c in by_pmd]
            if len(hits) != 1:
                raise ValueError('PCP0 %d: ref %#x resolves to %s' % (index, addr, hits))
            out.append(by_pmd[hits[0]])
        return out

    target_words = [0] * count
    comps = inst = 0
    inst_pcp = {}
    for index, kind, key, lod, which in R.cpv_entries(cpvs):
        if lod != 0 or which != 'main':
            continue
        refs = referenced(index)
        if not refs:
            continue
        if kind == 'comp':
            owner = min(refs)
            if target_words[owner] & 0xFFFF:
                raise ValueError('two component PCP0s start at record %d' % owner)
            for r in set(refs):
                if targets[r][2] != 0:
                    raise ValueError('component PCP0 %d reads an instance template' % index)
                target_words[r] |= COVERED
            target_words[owner] |= index + 1
            comps += 1
        else:
            if key in inst_pcp:
                raise ValueError('instance key %s has two PCP0s' % (key,))
            inst_pcp[key] = (index, {targets[r][1] for r in refs})

    _components, instances = R.read_hoods(R.city_folder(str(rsc_path)))
    models = R.read_models(rsc)
    model_id = {m.name.lower(): i for i, m in enumerate(models)}
    n_inst = struct.unpack_from('<I', instance_bank, 16)[0]
    rec_off = struct.unpack_from('<I', instance_bank, 24)[0]
    if n_inst != len(instances):
        raise ValueError('RINS has %d placements, .hood %d' % (n_inst, len(instances)))
    instance_words = []
    for i, placed in enumerate(instances):
        mid = struct.unpack_from('<I', instance_bank, rec_off + i * 20)[0]
        if mid != model_id[placed.type.lower()]:
            raise ValueError('RINS placement %d is not %s' % (i, placed.type))
        got = inst_pcp.get((placed.type.lower(), int(placed.extension)))
        if got and got[1] != {mid}:
            raise ValueError('PCP0 %d of placement %d reads another model' % (got[0], i))
        instance_words.append(got[0] + 1 if got else 0)
        inst += bool(got)

    out = struct.pack('<8I', RCPM, 1, 32 + 4 * (count + n_inst), count, n_inst, 0, 0, 0)
    out += struct.pack('<%dI' % count, *target_words)
    out += struct.pack('<%dI' % n_inst, *instance_words)
    stats = {'variants': len(variants), 'component_pcp': comps,
             'covered_records': sum(1 for w in target_words if w & COVERED),
             'instances_with_pcp': inst, 'instances': n_inst, 'bytes': len(out)}
    return out, stats


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('rsc', type=Path, help='original assets/resource/<city>/<city>.rsc')
    ap.add_argument('manifest', type=Path)
    ap.add_argument('instances', type=Path)
    ap.add_argument('outputs', nargs='+', type=Path)
    a = ap.parse_args()
    data, stats = build(a.rsc, a.manifest.read_bytes(), a.instances.read_bytes())
    for out in a.outputs:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        print(out)
    print(', '.join('%s=%s' % kv for kv in stats.items()))


if __name__ == '__main__':
    main()
