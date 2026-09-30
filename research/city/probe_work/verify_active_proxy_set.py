#!/usr/bin/env python3
import os
import hashlib
import json
from pathlib import Path

candidate = Path(__file__).resolve().parents[1] / "output" / "mip_proxy_v7"
active = Path(os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/resources/city'))
rows = []
for source in sorted(candidate.glob("losangeles_*.pck")):
    target = active / source.name
    left = source.read_bytes()
    right = target.read_bytes()
    rows.append({
        "file": source.name,
        "match": left == right,
        "sha256": hashlib.sha256(right).hexdigest(),
        "bytes": len(right),
    })
report = {"passed": len(rows) == 9 and all(row["match"] for row in rows), "files": rows}
print(json.dumps(report, indent=2))
raise SystemExit(0 if report["passed"] else 1)

