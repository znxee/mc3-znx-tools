"""Append the measured mip-v6 TEX1 identity fix to the shared fork log."""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    command = [
        sys.executable,
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', 'tools', 'mc3_forks.py'),
        "add",
        "--fork", "Fork City TEXTURES",
        "--changed", (
            "MIP FALLBACK V6 TEX1 INSTALLED AND TESTED WITH THE COMO_TESTO.md METHOD. "
            "After v5-safe the user had no Exception any more, but the frontend "
            "showed No Image. A headless control boot confirmed modcity in the frontend "
            "(SES=00000005), span 219.7803 s and zero Exception/cpuTlbMiss, but revealed "
            "50 'TLB Miss' lines the old summariser ignores; 43 were in "
            "rmcTextureProxyPS2::Bind 0x2BA240/2BA2C4/2BA328/2BA588, with small "
            "addresses 4/8/2A/48/54/90. Cause: v4/v5 copied the donor's whole TEX1 "
            "from retail when turning mip2 into mip3. MC3 uses free TEX1 bits as the "
            "proxy identity; a binary audit proved that 453/453 expanded textures "
            "of each PCK received the donor's identity bits. V6 copies the retail "
            "descriptors/allocator, but keeps ALL the bits of the original TEX1 and "
            "only changes GS MXL bits 2..4 from 1 to 2. New A/B boot with the same "
            "mc3boot.ini: SES=00000005, span 219.7707 s, zero Exception and only the "
            "7 usual initial TLB Misses at 0x578648/42F2F4/432808; the 43 proxy "
            "misses disappeared entirely. Active Midnight PCK SHA256 "
            "9E7F484145687602BEC4841F132E6817E80194BFE96FADFFFAFD907A91B06CE6; "
            "safe PPF stays A25CEACB94FFABCA5A8A1FB7205C6FC40BB171E33B7A24AD0736E2C4810CA5CB. "
            "Source->PPF->PCK audit PASS, zero codec/palette/descriptor/multi-owner; "
            "diff of the nine PCKs PASS, zero unexpected bytes; PPF callbacks 0/1418 unsafe."
        ),
        "--affects", (
            "Nine modcity_{dawn,dusk,midnight}_{clear,cloudy,rainy}.pck. The PPF did "
            "not change from v5-safe. Separately, the sum of the seven '=1' mods was "
            "11572 against the 11384 limit; debug_draw, being last, could be refused. "
            "For the texture diagnosis period, peds.mod was changed from 1 to 0, "
            "keeping city_probe+debug_draw and bringing it to 8348/11384 (3036 free). "
            "90 s cave-safe boot: SES=00000005, span 89.7865 s, zero Exception and "
            "the same 7 initial misses, none from the proxy. No ELF, geometry, "
            "collision or binary mod was changed. Full before/after backup in "
            "backups/Fork City TEXTURES/2026-09-13_mip_fallback_v6_tex1; the previous "
            "mc3boot.ini is in before/mc3boot.ini and the three A/B logs are in after/logs."
        ),
        "--action", (
            "Do a clean boot in the PCSX2 GUI, without loading the savestate that "
            "serialised the v5 state. First check whether the frontend/city image is "
            "back; debug_draw now fits and should show. Then enter/drive and watch "
            "LOD 0/1/2 and black meshes. Keep peds.mod=0 during this diagnosis. "
            "If No Image persists despite the clean boot, save a new state in another "
            "slot and keep this v6 set for reading the RAM."
        ),
    ]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
