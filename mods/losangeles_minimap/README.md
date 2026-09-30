# losangeles_minimap

The HUD map (minimap and pause map) for the added cities (city index 5 and
up). Shim, 1.7 KB, 15 hooks. `mc3boot.ini`: `losangeles_minimap.mod = shim`.

## Why it was black

1. The map image comes from the streamed hudmap build
   (`$/resources/city/hudmap.pck/.ppf`), a table of `rmcTexturePS2*` indexed
   by city (+3 = locked variant): 0 sd, 1 atlanta, 2 detroit, 3 tokyo,
   4 atlanta_locked, 5 detroit_locked, then race maps. City 5 got
   `hud_map_detroit_locked`.
2. The world->texture scale comes from a per-city table of 32 bytes at
   `0x6BDE20` (hudMap::SharedDataAllTypes) filled for cities 0..3 only
   (`sub_285438`); city 5 read past its end.

## What it does

- The four calls of the map lookup `sub_28D378` (hudMap::DrawMap 0x27D4AC,
  SetCurrentCity 0x282460, pause map 0x289C9C / 0x289CC8) return, for an added
  city, `mcTextureFactory::Create("hud_map_<city name>")` - the factory call
  hudMap::Init uses for its loose icons. The name comes from the city record
  (`*(0x619D4C) + 76 * city`), so Paris gets `hud_map_paris`.
- The eleven calls of `sub_285508(table, city)` answer, for city >= 4, an
  entry of our own filled by the game's `sub_285540` from
  mcCity::GetCityExtendsForMap (for Los Angeles: losangeles.lvl's extents).
- Marker `RMMP <city> <texture>`.

## What the installer copies

MC2's own LA map, unchanged, twice:

    assets/texture/hud_map_la.tex -> ASSETS/texture/hud_map_losangeles.tex
                                  -> ASSETS/texture/hud_map_losangeles_proxy.tex

512x256 4 bpp; it covers exactly losangeles.lvl's extents (x -2000..1600,
z -1720..1520), north (+z) at the top - checked against the road collision and
in game (car at -153, 88 on the matching spot). Paris: `hud_map_paris.tex` ->
`hud_map_paris.tex` / `_proxy`; the original 65,614-byte texture is paired
with Paris's measured extents x -1880..1520 and z -1640..1360. Arcade smoke
tests placed the car on the matching plaza/road features in the image.

## Map alignment (2026-09-29)

The MC2 picture does NOT cover the extents of `<city>.lvl`, which the game
uses for the quad (u = (x - min x)/span x, v = (max z - z)/span z): drawn that
way the streets sat off the real ones - in LA about 25 m at the centre and up
to ~150 m in the south, in Paris tens of metres (the picture is painted for a
nearly square rectangle, 512x256 pixels stretched onto it). The icons were
right because they use the same extents entry; only the picture was off.

`mc2_map_fit.py --city <folder> --tex <hud_map_x.tex>` (tools) finds the
rectangle the picture was painted for: the road pixels of the image (bright
green/olive lines) are correlated, blurred, with the city's own road network
(the lane lines of the original aib, or the road collision polygons), by
coordinate descent on min/max x and z. Fitted (residual 1-3 pixels, i.e.
roughly 7-20 m; the collision and the aib give the same numbers within a few
metres except LA's southern edge, which has few roads):

| city | .lvl extents | fitted (what the picture covers) |
|---|---|---|
| LA | x -2000..1600, z -1720..1520 | x -1888..1536, z -1938..1520 |
| Paris | x -1880..1520, z -1640..1360 | x -1598..1478, z -1620..1521 |

They live in `payload/mc2_city_config.h` (`mc2_city_map_extent`, bit patterns)
and `map_entry_hook` overwrites the min/max/span/1/span of the entry the game
filled (`[minX, maxX, spanX, 1/spanX, minZ, maxZ, spanZ, 1/spanZ]`, 32 bytes).
Checked in game with the car parked on 3 busy intersections of the aib per
city (the arrow now sits on a street crossing; with the .lvl extents it sat in
the middle of a block in Paris) and in an Arcade race in each city (opponents
on the road under their icons, checkpoint arrow in the right direction).
