"""Reproduce the original PCK with the backed-up builder, then compare the fix."""
import os
import contextlib
import hashlib
import io
import json
from pathlib import Path
import struct
import sys

HERE = Path(__file__).resolve().parent
TOOLKIT = Path(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tools'))
sys.path.insert(0, str(HERE.parent))
from audit_city_counts_runtime import Audit

BACKUP = TOOLKIT / 'backups/Fork City MODS 2/2026-09-08_instance_matrix_alignment/before'
OLD = HERE / 'downtown_reproduced_before.pck'
NEW = HERE / 'downtown_aligned_build_validation.pck'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    sys.argv = [str(TOOLKIT / 'mc3_city_build.py'),
                '--donor', os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/resources/city/atlanta_midnight_clear.pck'),
                '--place', str(TOOLKIT / 'output/hood_test_downtown_place.tsv'),
                '--models-dir', str(TOOLKIT / 'output/mc2_losangeles/model'),
                '--pck-mesh', str(TOOLKIT / 'output/city_v7_vif_relocation_20260906/la729_local_components_20260906'),
                '--max-inst', '8511', '--out-path', str(OLD)]
    ns = {'__name__': '__main__', '__file__': str(TOOLKIT / 'mc3_city_build.py')}
    log = io.StringIO()
    with contextlib.redirect_stdout(log):
        try:
            exec(compile((BACKUP / 'mc3_city_build.py').read_bytes(),
                         str(BACKUP / 'mc3_city_build.py'), 'exec'), ns)
        except SystemExit as exc:
            if exc.code not in (None, 0):
                raise
    (HERE / 'before_build.log').write_text(log.getvalue(), encoding='utf-8')
    assert sha(OLD) == sha(BACKUP / 'losangeles_midnight_clear.pck'), 'Recipe differs from active PCK'
    before, after = Audit(OLD), Audit(NEW)
    tab0, tab1 = before.off(before.u(0x98)), after.off(after.u(0x98))
    mapping = {}
    pairs = []
    for h in range(before.u(0x94)):
        e0, e1 = tab0 + h * 20, tab1 + h * 20
        count = before.u(e0 + 12)
        assert count == after.u(e1 + 12)
        if not count:
            continue
        a0 = before.off(before.u(e0 + 16))
        a1 = after.off(after.u(e1 + 16))
        for i in range(count):
            o0, o1 = a0 + i * 96, a1 + i * 96
            mapping[before.base + o0] = after.base + o1
            pairs.append((o0, o1))
            assert (after.base + o1 + 32) % 16 == 0
            assert before.d[o0 + 8:o0 + 96] == after.d[o1 + 8:o1 + 96], 'Instance data changed'
    for o0, o1 in pairs:
        for field in (0, 4):
            old_value = before.u(o0 + field)
            assert after.u(o1 + field) == mapping.get(old_value, old_value)
    mesh0, mesh1 = before.collect(), after.collect()
    assert mesh0 == mesh1, 'Mesh graph changed'
    from read_city_pass_trace import embedded_fingerprint
    for m in mesh0:
        assert embedded_fingerprint(before, m) == embedded_fingerprint(after, m)
    assert after.run()['passed']
    result = dict(reproduced_original_exactly=True, before_sha256=sha(OLD),
                  after_sha256=sha(NEW), instances_preserved=len(pairs),
                  matrix_words_preserved=len(pairs) * 12,
                  aligned_instances=len(pairs), instance_links_valid=True,
                  mesh_graph_identical=True, vif_meshes_identical=len(mesh0),
                  new_pck_structural_audit_passed=True)
    rendered = json.dumps(result, indent=2)
    print(rendered)
    (HERE / 'rebuild_verification.json').write_text(rendered + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
