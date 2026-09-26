"""
mc3_perf.py - read/write the performance parameters of a car PCK.

Schema ported from the user's ImHex pattern, which was only possible by
comparing the "leftover" (Xbox) files against the values inside the PCKs.

The pointers live in the PCK header (from 0x90 on):
    0x98 VehInput | 0xA0 VehDamage | 0xA4 VehModel | 0xA8 VehAudio
    0xB4 VehSimBase | 0xB8 VehSimMods | 0xBC VehGyroBase | 0xC0 VehGyroMods
    0xC4 VehZone | 0xC8 VehMods
Every struct starts with the vtable pointer (+0x00) and, almost always, a ptr to
the config name (+0x04) -- that is why the useful fields start after that.
From VehSim+0x54 on come the subsystem pointers (engine, gearbox, wheels...).

Field notation: ("f",name)=float  ("u",name)=u32  ("h",name)=u16
                (None,n)=skip n bytes
"""
import struct

VEH_HDR_PTRS = {
    0x98: "VehInput", 0xA0: "VehDamage", 0xA4: "VehModel", 0xA8: "VehAudio",
    0xB4: "VehSimBase", 0xB8: "VehSimMods", 0xBC: "VehGyroBase",
    0xC0: "VehGyroMods", 0xC4: "VehZone", 0xC8: "VehMods",
}


def _F(*names):
    return [("f", n) for n in names]


PERF_SCHEMA = {
    "VehInput": (0x0C, _F("SSSThreshold", "SSSValue") + [(None, 4)] +
                 _F("WheelRange", "FrtWhlAmpDiv", "FrtWhlAmpExp") +
                 [("u", "ConstantMag"), ("u", "SpringMag"), ("u", "DamperMag")] +
                 _F("SteerRate", "HandBrakeRate", "DriftRate")),

    "VehDamage": (0x0C, _F("MaxDamage", "MedDamage", "ImpactThreshold",
                           "WipeoutThreshold", "WeightxferThreshold",
                           "ExplodeThreshold", "JoltThreshold", "FatalBlow",
                           "WipeoutDelay", "DamageOutDelay") + [(None, 8)] +
                  _F("ExplodeImpulse", "SparkStartColorR", "SparkStartColorG",
                     "SparkStartColorB", "SparkStartColorA", "SparkEndColorR",
                     "SparkEndColorG", "SparkEndColorB", "SparkEndColorA",
                     "SparkVelVarA", "SparkVelVarB", "SparkVelVarC",
                     "RoofSparkVelVarA", "RoofSparkVelVarB", "RoofSparkVelVarC",
                     "SparkLifeTime", "SparkEmitMod", "SparkSize") +
                  [("u", "BottomOutCap")]),

    "VehModel": (0x0C, _F("SuspensionLimitFront", "SuspensionLimitRear",
                          "TireDiameterLimitFront", "TireDiameterLimitRear",
                          "TireExtentFront", "TireExtentRear",
                          "SuspensionClearanceFront", "SuspensionClearanceRear",
                          "TireSteerLimit", "GroundClearance")),

    "VehSim": (0x0C, _F("ModelOffsetX", "ModelOffsetY", "ModelOffsetZ",
                        "CenterOfMassX", "CenterOfMassY", "CenterOfMassZ", "Mass",
                        "SizeX", "SizeY", "SizeZ", "InertiaBoxX", "InertiaBoxY",
                        "InertiaBoxZ", "BoundFriction", "BoundElasticity",
                        "BoundGravity", "AirGravity") + [("u", "DriveTrainType")]),

    "VehGyro": (0x0C, _F("Turn", "Drift", "Spin180", "Reverse", "Yaw", "Pitch",
                         "RollTorque", "Lean", "Wheelie", "TurnFactor") + [(None, 4)] +
                _F("TurnLimit", "TurnDamp", "TurnBias", "LeanFactor", "LeanAngle",
                   "LeanAngleMax", "LeanRate", "LeanLimit", "LeanDamp",
                   "LeanImpulseUp", "LeanImpulseDown", "LeanSpeedMin",
                   "LeanSteerLimit", "LeanCOG", "NoiseAngle", "WheelieAngle",
                   "WheelieRate", "WheelieLimit", "WheelieDamp", "RollLimit",
                   "RollDamp", "DriftThrust", "DriftDecay", "WheelieBoostAcc",
                   "WheelieBoostDuration", "WobbleAmplitude", "WobbleFrequency",
                   "BurnoutRate", "BurnoutLimit", "BurnoutTurn", "BurnoutTurnRev",
                   "BurnoutLean", "BurnoutGrip", "ReverseAcc", "ReverseLimit")),

    "VehZone": (0x0C, _F("CenterOfMassX", "CenterOfMassY", "CenterOfMassZ",
                         "SSSValue", "StaticFric", "SlidingFric", "OptSlipPercent",
                         "SteeringLimit", "TurnBias", "TurnFactor", "TurnTorque",
                         "TurnLimit", "TurnDamp", "bikes_LeanFactor",
                         "bikes_LeanTorque", "bikes_LeanLimit", "bikes_LeanDamp",
                         "bikes_LeanRate")),

    "Wheel": (0x0C, _F("TireDispLimitLat", "TireDispLimitLong", "TireDampCoefLat",
                       "TireDampCoefLong", "SurfaceDragCoefLat", "SurfaceDragCoefLong",
                       "SteeringLimit", "CamberLimit", "WobbleLimit", "AxleLimit",
                       "BrakeCoef", "HandBrakeCoef", "SteeringOffset", "TravelLimit",
                       "TravelExtent", "SuspensionOffset", "SuspensionLimit",
                       "SuspensionExtent", "SuspensionClamp", "SuspensionFactor",
                       "SuspensionDampCoef", "SuspensionDampFactor",
                       "OptimumSlipPercent", "StaticFriction", "SlidingFriction")),

    "Engine": (0x00, _F("AngInertia", "MaxHorsePower", "IdleRPM", "OptRPM", "MaxRPM",
                        "EngageRPM", "GCL", "GearChangeThrottle", "BoostDuration",
                        "BoostHP")),

    "Transmission": (0x00, [("u", "ManualNumGears")] +
                     _F("Reverse", "Low", "High", "UpShiftBias", "DownShiftBiasMin",
                        "DownShiftBiasMax") + [("u", "MaxDownshifts")] +
                     _F("GearBias", "GearChangeTime")),

    "Drivetrain": (0x00, _F("AngInertia", "BrakeDynamicCoef", "BrakeStaticCoef")),
    "Axle": (0x00, _F("TorqueCoef", "DampCoef")),

    "Aero": (0x04, _F("AngCDampX", "AngCDampY", "AngCDampZ", "AngVelDampX",
                      "AngVelDampY", "AngVelDampZ", "AngVel2DampX", "AngVel2DampY",
                      "AngVel2DampZ", "Unknown", "Down") + [(None, 8)] +
             _F("DragSlipStream", "Drag")),

    "Fluid": (0x00, _F("Damp", "Unknown", "Current") + [(None, 4)] +
              _F("Buoyancy", "MinBuoyancy", "SinkRate")),

    "Nitro": (0x00, [("h", "Nitros"), (None, 2)] +
              _F("NitroBoostExp", "NitroBoostAmount", "NitroBoostTime", "NitroFov",
                 "FovOutTime", "FovInTime", "FlameTime", "FlameSize")),

    "SSTurbo": (0x00, _F("TurboBoostExp", "TurboBoostAmount", "TurboUseTime",
                         "TurboRegenTime", "TurboFalloffTime", "ChargedFalloffTime",
                         "StayFullTime", "LoseChargeSpeed", "LoseChargeSpeed2",
                         "Unknown", "FovOutTime", "FovInTime", "TurboFOV", "TurboDist",
                         "BlurOffTime", "BlurStartAmount", "EnvelopeD1", "EnvelopeD2",
                         "EnvelopeD3", "FlameTime", "FlameSize", "SmokeSize") +
                [(None, 8)] +
                _F("SmokeStartR", "SmokeStartG", "SmokeStartB", "SmokeStartA",
                   "SmokeEndR", "SmokeEndG", "SmokeEndB", "SmokeEndA")),

    "Hydraulics": (0x00, _F("ReleaseTime", "FrontRise", "BackRise", "FrontDrop",
                            "BackDrop", "RateRise", "RateDrop", "SuspensionExtent",
                            "SuspensionDamp", "MassOffset", "Unknown")),

    "AIInfo": (0x04, _F("TurnConst", "HBEarlyTime", "HBSteerMultiplier",
                        "MaxBrakeDecel", "SteerScalar", "SteerGamma",
                        "CarFrictionHandling", "FrictionMultiplier",
                        "SlidingFrictionFactor", "Unknown")),
}

# order of the subsystem pointers starting at VehSim + 0x54
VEHSIM_SUBS = ("WheelFront", "WheelBack", "Engine", "Transmission", "Drivetrain",
               "Freetrain", "AxleFront", "AxleBack", "Aero", "Fluid", None,
               "Nitro", "SSTurbo", "UnknownModule", "Hydraulics", "AIInfo")

SUB_SCHEMA = {"WheelFront": "Wheel", "WheelBack": "Wheel", "AxleFront": "Axle",
              "AxleBack": "Axle", "Freetrain": "Drivetrain"}

DRIVETRAIN_TYPES = {0: "RWD", 1: "FWD", 2: "AWD / Rear Burnout", 3: "AWD / Front Burnout"}


def _ptr_ok(pck, v):
    """True when `v` looks like a pointer into this file.

    The old test was a hardcoded PS2 window (0x06000000..0x07000000), which
    silently rejected every PSP file: MC3 PSP maps around 0x0A0D1F00 and MC:LA
    around 0x0109DF80. Deriving the window from the file's own base makes the
    same code work on all three."""
    return pck.va <= v <= pck.va + len(pck.data) and pck.inside(pck.F(v))


def _subs_offset(pck, sim_off, candidates=(0x54, 0x74)):
    """Where the run of subsystem pointers starts inside a VehSim.

    PS2 puts it at +0x54. PSP pads its vectors to 16 bytes -- you can see the
    0xCDCDCDCD fillers inside the struct -- which pushes the run to +0x74, so a
    hardcoded offset finds nothing there and every subsystem (engine, gearbox,
    wheels, axles) silently disappears. Pick whichever candidate starts the
    longest run of valid pointers.
    """
    best, best_n = None, 0
    for c in candidates:
        n = 0
        for i in range(len(VEHSIM_SUBS)):
            o = sim_off + c + 4*i
            if not pck.inside(o):
                break
            if _ptr_ok(pck, pck.U(o)):
                n += 1
        if n > best_n:
            best, best_n = sim_off + c, n
    return best if best_n >= 4 else None


PAD_WORD = 0xCDCDCDCD

# mcSpotlightTuneParams lives outside the normal header tune-pointer run.  The
# main vehicle type owns it at rmcCarModelType+0x3864. Offsets and names are
# exact matches for mcSpotlightTuneParams::FileIO in the retail ELF.
SPOTLIGHT_FIELDS = (
    ("ColorR", "f", 0x10),
    ("ColorG", "f", 0x14),
    ("ColorB", "f", 0x18),
    ("Brightness", "f", 0x1C),
    ("XScale", "f", 0x20),
    ("YScale", "f", 0x24),
    ("ZScale", "f", 0x28),
    ("XOffset", "f", 0x2C),
    ("YOffset", "f", 0x30),
    ("ZOffset", "f", 0x34),
    ("HiBeamYScale", "f", 0x38),
    ("Sharpness", "f", 0x40),
    ("Falloff", "f", 0x44),
    ("AimTweak", "f", 0x48),
    ("HiBeamTweak", "f", 0x54),
    ("RenderIntensity", "u", 0x60),
)


def _spotlight(pck):
    """Editable spotlight fields for a PS2 main vp_*.pck, or an empty list."""
    try:
        if pck.U(4) != 0x0038E030 or not _ptr_ok(pck, pck.U(0x88)):
            return []
        model = pck.F(pck.U(0x88))
        if not pck.inside(model + 0x3864):
            return []
        spot_va = pck.U(model + 0x3864)
        if not _ptr_ok(pck, spot_va):
            return []
        spot = pck.F(spot_va)
        out = []
        for name, typ, rel in SPOTLIGHT_FIELDS:
            off = spot + rel
            if not pck.inside(off):
                return []
            value = (struct.unpack_from("<f", pck.data, off)[0]
                     if typ == "f" else pck.U(off))
            out.append((name, typ, off, value))
        return out
    except (IndexError, struct.error):
        return []


def _is_filler(pck, o):
    """True when the word at `o` is not a real float field.

    Two kinds of filler sit between these fields, and which one appears where
    changes per console:
      * 0xCDCDCDCD alignment padding -- PS2 aligns a colour vec4 to 16 bytes,
        PSP aligns every member to 16 bytes (a vec3 takes 16, even Mass does);
      * a vtable pointer -- e.g. the Xbox Fluid struct carries one at +0x00 that
        the PS2 one does not.
    Both read back as an absurd float: 0xCDCDCDCD is -4.3e+08 and a small code
    address is a denormal (0x005E8910 -> 8.7e-39). Real physics values are
    neither, so this test separates them without a per-console layout.
    """
    if not pck.inside(o):
        return False
    u = pck.U(o)
    if u == PAD_WORD:
        return True
    v = struct.unpack_from("<f", pck.data, o)[0]
    return v != 0.0 and abs(v) < 1e-20        # denormal => an address, not data


def _skip_filler(pck, o, limit=64):
    n = 0
    while n < limit and _is_filler(pck, o):
        o += 4
        n += 1
    return o


def _read(pck, base_off, schema_name):
    start, fields = PERF_SCHEMA[schema_name]
    o = base_off + start
    out = []
    for typ, name in fields:
        if typ is None:
            # `name` is a byte count. These skips exist to jump alignment
            # padding or a vtable pointer, so only jump what is really filler:
            # on Xbox the 8 bytes the SSTurbo schema skips hold real colour
            # values, and skipping them blindly shifted every smoke field.
            # Sub-word skips (Nitro has a 2-byte one after a u16) can't be
            # inspected as words, so those stay unconditional.
            if name % 4 or o % 4:
                o += name
            else:
                end = o + name
                while o < end and _is_filler(pck, o):
                    o += 4
            continue
        if typ == "f":
            o = _skip_filler(pck, o)
        if not pck.inside(o):
            break
        if typ == "f":
            out.append((name, typ, o, struct.unpack_from("<f", pck.data, o)[0]))
            o += 4
        elif typ == "u":
            out.append((name, typ, o, pck.U(o)))
            o += 4
        elif typ == "h":
            out.append((name, typ, o, pck.U16(o)))
            o += 2
    return out


def sections(pck):
    """{section: [(field, type, file_offset, value)]}. Empty if the file is not
    a PS2 car PCK (psppck files do not use this header)."""
    out = {}
    ptr = {}
    for slot, nm in VEH_HDR_PTRS.items():
        try:
            p = pck.U(slot)
        except Exception:
            continue
        if _ptr_ok(pck, p):
            ptr[nm] = pck.F(p)

    for nm in ("VehInput", "VehDamage", "VehModel", "VehZone"):
        if nm in ptr:
            out[nm] = _read(pck, ptr[nm], nm)

    for mode, simk, gyrok in (("Base", "VehSimBase", "VehGyroBase"),
                              ("Mods", "VehSimMods", "VehGyroMods")):
        if simk in ptr:
            out["%s / VehSim" % mode] = _read(pck, ptr[simk], "VehSim")
            subs = _subs_offset(pck, ptr[simk])
            if subs is None:
                continue
            for i, sname in enumerate(VEHSIM_SUBS):
                if not sname:
                    continue
                e = subs + 4*i
                if not pck.inside(e):
                    break
                sp = pck.U(e)
                if not _ptr_ok(pck, sp):
                    continue
                so = pck.F(sp)
                sch = SUB_SCHEMA.get(sname, sname)
                if pck.inside(so) and sch in PERF_SCHEMA:
                    out["%s / %s" % (mode, sname)] = _read(pck, so, sch)
        if gyrok in ptr:
            out["%s / VehGyro" % mode] = _read(pck, ptr[gyrok], "VehGyro")
    spotlight = _spotlight(pck)
    if spotlight:
        out["Rendering / Spotlight"] = spotlight
    return out


# ---------------------------------------------------------------------------
# VehMods: real names, taken from the Xbox leftovers (`xbox leftover/vehicle/
# *.vehmods`, which are the text sources). Each category has its own fields and
# a list of sub-mods (the parts the player buys in the shop).
# Confirmed field by field against the 350z binary.
# ---------------------------------------------------------------------------

VEHMODS_CATEGORIES = {
    0: ("Engine", ["EngineHP", "EngineRPM", "GearRatios", "Drag", "Wheelie",
                   "Burnout", "Nitro", "Wobble", "Backfire"],
        ["Exhaust", "Headers", "Intake", "Turbo", "Upgrade", "Chip", "Nitro"]),
    1: ("Trans", ["ShiftTime", "Boost", "NumGears", "GearRatios"],
        ["Clutch", "Gears"]),
    2: ("Brake", ["BrakeCoef", "HandbrakeCoef"], ["Brakes"]),
    3: ("Suspension", ["Spring", "Shock", "Drift", "Turn", "SSS", "SimCG",
                       "Spin", "Axle", "Inertia", "AngDamp"],
        ["ShocksAndSpring", "SwayBar"]),
    4: ("Tire", ["Tread", "Sidewall", "Compound"], ["Tires"]),
    5: ("Hydraulics", ["Pump"], ["Pneumatic", "Hydraulic"]),
}


def vehmods_decode(vals):
    """Split a category u16 list into named records.

    Record format: u16 category | u16 num_levels |
    u16 progression[4] (0, L1%, [L2%], 100) | u16 fields[...] of the category.
    Returns (category_name, [(sub_mod, [(field, value, index_in_vals)])]).
    """
    if len(vals) < 6:
        return ("?", [])
    cat = vals[0]
    name, fields, subs = VEHMODS_CATEGORIES.get(cat, ("cat%d" % cat, [], []))
    rec = 6 + len(fields)            # size of one record, in u16 units
    out = []
    i = 0
    n = 0
    # the category lists sit back to back in the file with no separator, so
    # stopping at the known sub-mod count avoids running into the next category.
    while i + rec <= len(vals) and n < len(subs):
        lv = vals[i+1]
        prog = vals[i+2:i+6]
        body = [(fields[j] if j < len(fields) else "field%d" % j,
                 vals[i+6+j], i+6+j) for j in range(len(fields))]
        sub = subs[n] if n < len(subs) else "sub%d" % n
        # L1/L2 are the progression (percentage of each upgrade level)
        head = [("num_levels", lv, i+1)] + \
               [("L%dPercentage" % (k), prog[k], i+2+k) for k in range(1, min(lv+1, 4))]
        out.append((sub, head + body))
        i += rec
        n += 1
    return (name, out)


# ---------------------------------------------------------------------------
# VehMods (slot 0xC8) - upgrade table
#
# Layout: +0x00 vtable | +0x04 name ptr | +0x08 0 | +0x0C..+0x20 six pointers,
# one per upgrade category. Each target is a list of records:
#     u16 category       (0..5; the same index as the slot)
#     u16 num_upgrades   (2, 3 or 1)
#     u16 progression[num_upgrades+1]  -> 0/50/100, 0/33/67/100 or 0/100
#     u16 effects[...]   -> how much each upgrade improves, in %
# The list ends with 0xCDCD and may repeat the record (Base and Mods).
#
# Measured on 350z/srt4/deville/ducati: the categories and the progressions are
# the same everywhere; what changes per vehicle are the EFFECTS (350z 15/5/5/15,
# deville 17/7/7/17, ducati 30/30/30/30) -- and on the bikes several categories
# come zeroed. The meaning of each category (engine, gearbox, tires...) is still
# inference; what is confirmed is the structure.
# ---------------------------------------------------------------------------

def vehmods(pck):
    """[(index, file_off, [u16...])] of the six upgrade lists."""
    try:
        p = pck.U(0xC8)
    except Exception:
        return []
    if not _ptr_ok(pck, p):
        return []
    mo = pck.F(p)
    if not pck.inside(mo):
        return []
    out = []
    for k in range(8):
        v = pck.U(mo + 0x0C + 4*k)
        if not _ptr_ok(pck, v):
            break
        s = pck.F(v)
        if not pck.inside(s):
            break
        # Engine has 7 sub-mods x 15 u16 = 105, so the cap must be generous
        vals = []
        for j in range(256):
            if not pck.inside(s + 2*j):
                break
            u = pck.U16(s + 2*j)
            if u == 0xCDCD:
                break
            vals.append(u)
        out.append((k, s, vals))
    return out


def write(pck, file_off, typ, value):
    if typ == "f":
        struct.pack_into("<f", pck.data, file_off, float(value))
    elif typ == "u":
        struct.pack_into("<I", pck.data, file_off, int(value) & 0xFFFFFFFF)
    elif typ == "h":
        struct.pack_into("<H", pck.data, file_off, int(value) & 0xFFFF)
