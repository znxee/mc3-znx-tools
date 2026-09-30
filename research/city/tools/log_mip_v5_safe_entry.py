"""Append the mip-v5 runtime callback fix to the shared fork log."""

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
            "MIP FALLBACK V5-SAFE INSTALLED AFTER DIAGNOSING THE R5900 CRASH. "
            "The 13/09 12:35 savestate with PC=CDCDCDCD has exactly 510 "
            "Mod City TexturePS2 (56 mip1 + 454 mip3); 165 resident chunks had "
            "datCallback/release_callback at chunk+0x18 = CDCDCDCD. In the "
            "automatic 11:56 slot 1 backup, with no crash and still 56 mip1 "
            "+ 453 mip2 + 1 mip3, the 190 resident chunks had that callback "
            "equal to zero. The build_mc2_city_ppf.py emitter initialised the header "
            "with 0xCD and did not zero +0x18; fixed to write NULL, and verify now "
            "rejects any non-null callback. First I restored the pre-v4 set "
            "E75A7013..., then generated and installed v5-safe: the PPF differs from "
            "v4 only in the 1418 callbacks, all now zero, SHA256 "
            "A25CEACB94FFABCA5A8A1FB7205C6FC40BB171E33B7A24AD0736E2C4810CA5CB. "
            "The nine PCKs stay SHA256 30D98F5B... and keep 56 mip1 + 454 mip3. "
            "Header auditor: 0/1418 unsafe callbacks; independent auditor "
            "source->PPF->PCK PASS, zero codec/palette/descriptor mismatch and zero "
            "multi-owner; diff of the nine PCKs PASS, zero unexpected change."
        ),
        "--affects", (
            "The nine $MC3_HOSTFS/ASSETS/resources/city/losangeles_{dawn,dusk,midnight}_"
            "{clear,cloudy,rainy}.pck and losangeles_midnight_clear.ppf. Does not change "
            "the ELF, C++ mods, geometry, collision or PCSX2 configuration. Pre-v5 "
            "backup in backups/Fork City TEXTURES/2026-09-13_mip_fallback_v5_safe/before/city. "
            "Crash savestate kept in $MC3_WORK/output/"
            "mip_fallback_crash_20260913/crash_after_mip_v4.p2s."
        ),
        "--action", (
            "TEST WITH A CLEAN BOOT/RELOAD OF THE CITY; do not continue the crash "
            "savestate, since it already serialised the CDCDCDCD chunk pointers. The "
            "first discriminator is confirming the R5900 exception is gone while "
            "driving and forcing page-in/page-out; then judge visually how many "
            "meshes and LODs show. If there is a new exception, save to another slot "
            "without overwriting the evidence state."
        ),
    ]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
