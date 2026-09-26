"""Reads vehicle performance data (vehsim Engine + Trans) straight from a MC3 vehicle
.pck, using the pointer layout mapped by the MC3 PerformanceEditor (ps2,psp):

  header +0x00: u32 ptr_va (base_va = ptr_va - 0x80)
  header +0xB4: vehsim Base pointer | +0xB8: vehsim Mods pointer (PS2 .pck)
  vehsim +0x54: pointer table: wheelfront, wheelback, engine, trans, drivetrain, ...

Engine struct (10 f32): AngInertia, MaxHorsePower, IdleRPM, OptRPM, MaxRPM, EngageRPM,
GCL, GearChangeThrottle, BoostDuration, BoostHP.
Trans struct: ManualNumGears (u32), Reverse, Low, High, UpshiftBias, DownshiftBiasMin,
DownshiftBiasMax, MaxDownshifts (u32), GearBias, GearChangeTime.

Semantics validated against the plain-text leftovers in assets/tune/vehicle/*.vehsim
(e.g. vp_350z_04_base.vehsim). Low/High read as the top speed reached at MaxRPM in
1st/top gear; per-gear speeds are interpolated between them (GearBias shapes the
spacing - exact game formula unconfirmed, power-curve approximation used here).

Only PS2 .pck supported for now (.psppck shifts the vehsim pointer table by +0x20).
"""
import struct
from dataclasses import dataclass
from typing import List, Optional


class VehsimError(Exception):
    pass


@dataclass
class EnginePerf:
    ang_inertia: float = 0.0
    max_horsepower: float = 0.0
    idle_rpm: float = 0.0
    opt_rpm: float = 0.0
    max_rpm: float = 0.0
    engage_rpm: float = 0.0
    gcl: float = 0.0
    gear_change_throttle: float = 0.0
    boost_duration: float = 0.0
    boost_hp: float = 0.0


@dataclass
class TransPerf:
    num_gears: int = 0     # raw ManualNumGears field (counts reverse + neutral)
    reverse: float = 0.0
    low: float = 0.0
    high: float = 0.0
    upshift_bias: float = 0.0
    downshift_bias_min: float = 0.0
    downshift_bias_max: float = 0.0
    max_downshifts: int = 0
    gear_bias: float = 0.0
    gear_change_time: float = 0.0

    @property
    def forward_gears(self) -> int:
        """Actual number of forward gears. MC3's ManualNumGears also counts reverse
        and neutral, so a 350Z reads 8 but really has 6 forward gears (8-2). Confirmed
        against real cars (350Z/Ducati 999 = 6-speeds, Charger 69 = 4-speed)."""
        return max(1, self.num_gears - 2)

    def gear_speeds(self) -> List[float]:
        """Approximate top speed (at MaxRPM) per forward gear, interpolated Low..High.
        GearBias (<1 packs the upper gears closer together) shapes the spacing."""
        n = self.forward_gears
        if n == 1:
            return [self.high or self.low or 1.0]
        bias = self.gear_bias if 0.05 <= self.gear_bias <= 5.0 else 1.0
        out = []
        for i in range(n):
            t = i / (n - 1)
            out.append(self.low + (self.high - self.low) * (t ** bias))
        return out


@dataclass
class VehiclePerf:
    engine: EnginePerf
    trans: TransPerf
    variant: str = "base"     # "base" ou "mods"

    def summary(self) -> str:
        e, t = self.engine, self.trans
        return (f"[{self.variant}] Idle {e.idle_rpm:.0f} | Max {e.max_rpm:.0f} RPM | "
                f"{t.forward_gears} gears | Low/High {t.low:.0f}/{t.high:.0f} | "
                f"shift {t.gear_change_time:.2f}s")


def _u32(buf, off):
    return struct.unpack_from("<I", buf, off)[0]


def _f32(buf, off):
    return struct.unpack_from("<f", buf, off)[0]


def _valid_off(buf, off, size=4):
    return off is not None and 0 <= off and off + size <= len(buf)


def parse_vehicle_perf(pck_path_or_bytes, variant: str = "base") -> VehiclePerf:
    """variant: 'base' (stock) or 'mods' (fully upgraded performance data)."""
    if isinstance(pck_path_or_bytes, (bytes, bytearray)):
        buf = bytes(pck_path_or_bytes)
    else:
        with open(pck_path_or_bytes, "rb") as f:
            buf = f.read()

    if len(buf) < 0x100:
        raise VehsimError("file too small to be a vehicle pck")

    ptr_va = _u32(buf, 0x00)
    base_va = ptr_va - 0x80

    def va2off(va, size=4):
        off = va - base_va
        return off if _valid_off(buf, off, size) else None

    header_rel = 0xB4 if variant == "base" else 0xB8
    vehsim_off = va2off(_u32(buf, header_rel), 0x94)
    if vehsim_off is None:
        raise VehsimError(f"invalid vehsim pointer ({variant}) - does this pck have a performance section?")

    ptrs_off = vehsim_off + 0x54
    engine_off = va2off(_u32(buf, ptrs_off + 0x08), 10 * 4)
    trans_off = va2off(_u32(buf, ptrs_off + 0x0C), 10 * 4)
    if engine_off is None or trans_off is None:
        raise VehsimError("invalid engine/trans pointers in the vehsim")

    engine = EnginePerf(
        ang_inertia=_f32(buf, engine_off + 0x00),
        max_horsepower=_f32(buf, engine_off + 0x04),
        idle_rpm=_f32(buf, engine_off + 0x08),
        opt_rpm=_f32(buf, engine_off + 0x0C),
        max_rpm=_f32(buf, engine_off + 0x10),
        engage_rpm=_f32(buf, engine_off + 0x14),
        gcl=_f32(buf, engine_off + 0x18),
        gear_change_throttle=_f32(buf, engine_off + 0x1C),
        boost_duration=_f32(buf, engine_off + 0x20),
        boost_hp=_f32(buf, engine_off + 0x24),
    )
    trans = TransPerf(
        num_gears=_u32(buf, trans_off + 0x00),
        reverse=_f32(buf, trans_off + 0x04),
        low=_f32(buf, trans_off + 0x08),
        high=_f32(buf, trans_off + 0x0C),
        upshift_bias=_f32(buf, trans_off + 0x10),
        downshift_bias_min=_f32(buf, trans_off + 0x14),
        downshift_bias_max=_f32(buf, trans_off + 0x18),
        max_downshifts=_u32(buf, trans_off + 0x1C),
        gear_bias=_f32(buf, trans_off + 0x20),
        gear_change_time=_f32(buf, trans_off + 0x24),
    )

    # sanity: RPMs plausible?
    if not (100 <= engine.idle_rpm <= 20000 and 1000 <= engine.max_rpm <= 30000):
        raise VehsimError(f"implausible RPM values (idle={engine.idle_rpm}, max={engine.max_rpm}); "
                          "pck without performance data or a different format (psppck?)")
    if not (1 <= trans.num_gears <= 12):
        raise VehsimError(f"implausible number of gears: {trans.num_gears}")

    return VehiclePerf(engine=engine, trans=trans, variant=variant)
