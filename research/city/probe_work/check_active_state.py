from __future__ import annotations

import os
import hashlib
from pathlib import Path


FILES = [
    Path(os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'mc3boot.ini')),
    Path(os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'mc3boot.elf')),
    Path(os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'core.mod')),
    Path(os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'patches_menu.mod')),
    Path(os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'city_texture_touch_test.mod')),
    Path(os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS/resources/city/losangeles_midnight_clear.pck')),
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


for path in FILES:
    stat = path.stat()
    print(f"FILE {path}")
    print(f"  size={stat.st_size} mtime_ns={stat.st_mtime_ns}")
    print(f"  sha256={sha256(path)}")

ini = FILES[0].read_text(encoding="utf-8", errors="replace")
print("INI_RELEVANT")
for line in ini.splitlines():
    folded = line.casefold()
    if any(token in folded for token in ("core", "patches_menu", "city_texture_touch_test")):
        print(f"  {line}")
