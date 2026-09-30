#!/usr/bin/env python3
"""Independent byte-level audit for the nine mip+proxy PCK candidates."""

from __future__ import annotations

import os
import hashlib
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "output" / "mip_proxy_v7"
ACTIVE = Path(os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/resources/city'))
TEX1_MASK = 0xFFE00000
rows = []
passed = True

for candidate in sorted(CANDIDATES.glob("losangeles_*.pck")):
    source = ACTIVE / candidate.name
    audit = json.loads(candidate.with_suffix(".audit.json").read_text(encoding="utf-8"))
    old = source.read_bytes()
    new = candidate.read_bytes()
    allowed = set(range(0x0C, 0x10)) | set(range(0x80 + 0x228, 0x80 + 0x22C))
    for offset in audit["changed_tex1_file_offsets"]:
        allowed.update(range(offset, offset + 4))
        old_tex1 = struct.unpack_from("<Q", old, offset)[0]
        new_tex1 = struct.unpack_from("<Q", new, offset)[0]
        if (old_tex1 ^ new_tex1) & ~TEX1_MASK:
            passed = False
    unexpected = [
        index for index, (before, after) in enumerate(zip(old, new))
        if before != after and index not in allowed
    ]
    ids = audit["new_proxy_ids"]
    ok = (
        audit["texture_count"] == 510
        and audit["old_proxy_count"] == 0
        and audit["old_proxy_ids_zero"] == 510
        and audit["new_proxy_count"] == 510
        and ids == {"min": 1, "max": 510, "unique": 510, "zero": 0}
        and audit["changed_tex1_count"] == 510
        and audit["unchanged_contract"]
            == "geometry, shader objects, datChunkRefs and PPF"
        and not unexpected
        and len(new) > len(old)
    )
    passed &= ok
    rows.append({
        "file": candidate.name,
        "ok": ok,
        "source_sha256": hashlib.sha256(old).hexdigest(),
        "candidate_sha256": hashlib.sha256(new).hexdigest(),
        "source_size": len(old),
        "candidate_size": len(new),
        "prefix_changed_bytes": sum(a != b for a, b in zip(old, new)),
        "unexpected_prefix_changes": len(unexpected),
        "unexpected_examples": unexpected[:12],
        "proxy_count": audit["new_proxy_count"],
        "proxy_ids": ids,
        "tex1_changed": audit["changed_tex1_count"],
    })

report = {"passed": passed, "files": rows}
out = CANDIDATES / "independent_set_audit.json"
out.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
raise SystemExit(0 if passed else 1)

