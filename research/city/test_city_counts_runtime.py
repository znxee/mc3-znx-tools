"""Regression tests against real city layouts with isolated in-memory damage."""
import os
from collections import Counter
import contextlib
import io
import struct
import unittest

from audit_city_counts_runtime import Audit


CURRENT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'research', 'city', 'instance_alignment_20260908/downtown_aligned_build_validation.pck')
BACKUP = os.path.join(os.environ.get('MC3_WORK', '.'), 'backups', 'Fork City MODS 2/2026-09-08_instance_matrix_alignment/before')
HISTORICAL = BACKUP + r'\losangeles_midnight_clear.pck'
STATE = BACKUP + r'\SLUS-21355 (B3FD5361).01.p2s'


class CountAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = Audit(CURRENT)
        cls.meshes = cls.baseline.collect()
        cls.runtime = Audit(HISTORICAL, STATE)
        cls.runtime_meshes = cls.runtime.collect()

    def fresh_file(self):
        a = Audit(CURRENT)
        a.d = bytearray(a.d)
        return a

    def test_installed_baseline(self):
        self.assertTrue(Audit(CURRENT).run()['passed'])

    def test_truncated_material_groups(self):
        a = self.fresh_file()
        m, info = next((m, i) for m, i in self.meshes.items() if a.h(m+8)>1)
        struct.pack_into('<H', a.d, m+8, 1)
        a.mesh(m, info)
        self.assertEqual(a.errors['group_count'], 1)

    def test_truncated_instance_cpv_slots(self):
        a = self.fresh_file()
        m, info = next((m, i) for m, i in self.meshes.items() if i['slots']>1)
        struct.pack_into('<H', a.d, m+10, 1)
        a.mesh(m, info)
        self.assertEqual(a.errors['cpv_slot_count'], 1)

    def test_truncated_packet_list(self):
        a = self.fresh_file()
        m, info = next(iter(self.meshes.items()))
        gt = a.off(a.u(m+16))
        struct.pack_into('<H', a.d, gt+4, 0)
        a.mesh(m, info)
        self.assertGreater(a.errors['packet_count_vs_cpv'], 0)

    def test_runtime_baseline(self):
        result = Audit(HISTORICAL, STATE).run()
        self.assertEqual(result['errors'], {
            'instance_matrix_alignment': 1142,
            'runtime_instance_matrix_alignment': 1142,
        })

    def test_rejects_old_unaligned_file(self):
        result = Audit(HISTORICAL).run()
        self.assertFalse(result['passed'])
        self.assertEqual(result['errors'], {'instance_matrix_alignment': 1142})

    def test_raw_runtime_leaf_with_intact_expected_payload(self):
        a = Audit(HISTORICAL)
        a.ram = bytearray(self.runtime.ram)
        a.delta = self.runtime.delta
        m, info = next(iter(self.runtime_meshes.items()))
        gt = a.off(a.u(m+16))
        lst = a.off(a.u(gt))
        leaf_va = a.u(lst)
        raw_file_offset = leaf_va - a.base
        runtime_field = a.base + lst + a.delta
        struct.pack_into('<I', a.ram, runtime_field, raw_file_offset)
        a.mesh(m, info)
        self.assertEqual(a.errors['runtime_vif_leaf'], 1)
        self.assertEqual(a.errors['runtime_vif_payload'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
