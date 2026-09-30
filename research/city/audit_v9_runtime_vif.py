"""Compatibility entry point for the full relocation/count audit.

The old implementation compared VIF bytes at calculated addresses but omitted
the actual packet->VIF pointer and the entire CPV pointer chain. Delegate to the
complete auditor so a broken pointer cannot pass with intact nearby bytes.
"""
import os
import json
import sys

from audit_city_counts_runtime import Audit

STATE = os.path.join(os.environ.get('MC3_PCSX2_DATA', 'PCSX2'), 'sstates/SLUS-21355 (B3FD5361).01.p2s')
PCK = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools', 'output', 'losangeles_la_v9_cadeia_local.pck')

if __name__ == "__main__":
    result = Audit(PCK, STATE).run()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result["passed"] else 1)
