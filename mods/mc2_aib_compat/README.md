# mc2_aib_compat

Lets MC3's rail network loader read a MC2 `<city>.aib` whole. Shim, 1.2 KB,
2 hooks. `mc3boot.ini`: `mc2_aib_compat.mod = shim`. Needed by Los Angeles (whose
`losangeles_city.aib` is MC2's `losangeles.aib`, unchanged) and by Paris.

## 1. Traffic controls (tag 0x4105)

MC3 record: u16 count, count u16 road ids, two floats (10.0 and 2.5 in every
retail file). MC2 stops after the ids. The reader (sub_506F30) always takes
the floats, 8 bytes past MC2's record, and `TaggedStream::ReadTag`
(0x5B86F8) ends the whole stream when a reader went past its record. So
everything after the first traffic control was lost: the other controls and
the aiRouter section (0x4106: box, 50 m cells 68x52, cell -> road lists).
The call at 0x503DD8 is hooked: count and ids, the floats only when the record
has room for them (record end = TaggedStream+8), else the retail values;
then the reader's own tail (+28/+32 copies, ValidateCrossTraffic 0x507A50).
Marker `RAIB 1 <count>` on the first short record.

MC2's router section also has 0x4206 and 325 x 0x4207 (a road x road routing
table) where MC3 has 0x4202/0x4204; the loader skips unknown tags, so MC3's
routing table stays empty. Nothing measured needs it so far.

## 2. Rail flags (corrected 2026-09-28)

MC2's peds walk the SAME aib: bit 3 (0x08) of a rail marks a PEDESTRIAN rail -
all 1413 of LA's have width field +10 = 40, e.g. the two paths 2.5 m outside
the outer lanes of a 6-lane avenue and the loops around planters. Bit 1 is set
on car lanes and pedestrian rails alike. The first version cleared bit 1 on
every rail without bit 5 and opened the sidewalks to traffic (10 of 33 cars
were on a pedestrian rail). Keeping bit 1 stopped initial placement there.
After a second player report of cars on the sidewalk, the compatibility rule
was hardened to reproduce native MC3's closed-rail flags exactly: MC2
pedestrian rails get both bits 1 and 5 (`0x22`), while only car rails lose bit
1. Marker:
`RAIF <car rails opened | total pedestrian rails closed << 16> <rails>`.

### first version, for the record

Rail records (0x4109, 20 bytes, flags at +18) have MC3's layout but bit 1
means something else: MC3 (Tokyo) sets it on 398 of 4354 rails, always with
bit 5; MC2's LA on 4500 of 5183. MC3's traffic placement
(bPlaceVehicleInRoad 0x21B9D0) skips a rail with bit 1, so no car was ever put
on a road. For a MC2 file (a short 0x4105 record was seen) bit 1 is cleared on
rails without bit 5 when LoadRailNetwork returns (hook on its call, 0x5032C4).
Marker `RAIF <changed> <rails>` (LA: 4145 of 5183).

## Result

Measured in savestates: ambient traffic cars on a rail, Los Angeles 0/33 before,
33/33 after (Tokyo 33/33); router box loaded (-1860..1494, 68x52). In game the
cars drive the LA streets (traffic only gets a body near the player - the
spectator camera far from the player shows none, in Tokyo too).

Paris uses its original 486,216-byte `paris.aib` unchanged. The hardened load
reports `RAIB 1 2` and `RAIF 0x05190841 0x00000EDD`: 2,113 car rails opened,
1,305 pedestrian rails closed with both native flags, 3,805 rails total. A
60-second live scan read the current rail pointer of every active ambient car
and found zero cars on a bit-3 pedestrian rail. The watched cars matched their
cubic-Hermite road rails within 1.1 m. An offline curve-versus-collision scan
also found zero non-bit-3 rail centers inside Paris sidewalk polygons.

The same `.aib` remains the source for both systems; the runtime flag mapping
is what prevents ambient traffic from using the pedestrian subset. Paris'
router minimum y is -17.301518, so the renderer sets the AI fall threshold
from that city-specific value instead of reusing Los Angeles.

To rebuild the module, from `modloader/mods` in the configured PS2 toolchain:

```sh
sh build_mod.sh mc2_aib_compat $MC3_HOSTFS
```
