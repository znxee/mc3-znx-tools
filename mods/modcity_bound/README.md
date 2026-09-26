# modcity_bound

ModCity's collision built at load time from MC2's own `losangeles.rsc`, so
`modcity_bnd.pck` is neither installed nor loaded. INI: `modcity_bound.mod = shim`
(it has to be `shim`: with `[boot] nofe` the city loads before the first frame).

## What it hooks

`mcLayerCity::InitPhys` loads `$/resources/city/<city>_bnd` through
`datRscBuilder::LoadBuild` (0x0042E810, called at 0x001A937C). That reads the
128-byte header, `aligned_new(body, 128)`, reads the body and returns it, with
`*out1` = the virtual base the pointers were written against and `*out2` = the
body size; `mcPhysics(datResource&)` then relocates by `body - base`. For
ModCity (mcRaceConfig at 0x619B10, +0 == 5) the hook returns a body it built
itself, in that same form (base 0x06800000, allocated with the game's
`aligned_new`, so the city's `aligned_delete` frees it normally). Any other city
goes to the original loader untouched.

## Inputs (all already on an install)

| file | what is used |
|---|---|
| `host0:/mc2/losangeles/losangeles.rsc` (MC2 PS2, copied by the installer for the renderer) | BND0 type 0x0B `losangeles` (otgrid, 7x6 = 42 cells) -> MC3 type 9 octree; BND0 type 0x0A `losangeles_quad_0` -> MC3 type 10 quadtree; the PMT0 names of the materials |
| `$/resources/city/tokyo_bnd` (MC3 retail, opened like the game does) | 0x330 bytes: mcPhysics, mcLevelBounds, both wrappers, both nodes, both payloads, both material tables. Pointers are moved to the compact layout; no Tokyo geometry is used |

## MC2 BND0 vs MC3

- An MC2 cell/quad node has MC3's node layout: +4C verts, +50 polys, +74/+78
  vertex/polygon offsets (from the start of the BND0 block), +80/+84 material
  handle table (PMT0 handles, entry = `(h & 0x3FFF) - 1`), +8C its own tree.
  Grid header: +5C cells in x, +68 cells in z, +6C first cell, 0x90 apart; an
  empty cell has +84 = 0xFFFFFFFF.
- The 32-byte polygon is MC3's (normal, area with the material slot in the low
  byte, 4 x u16 index, 4 x u16 neighbour). Differences fixed here: indices are
  local to the cell (rebased to one global array); **a triangle is index 3 == 0
  alone** - MC2 writes 0 in neighbour 3, MC3 marks a triangle with 0xFFFF there.
  Reading MC2 with MC3's rule turns ~7800 triangles into quads reaching vertex 0
  (that blew the octree up to 3.1 M references). MC2 neighbours are edge
  indices; they are written 0xFFFF, as mc2_bnd_to_mc3.py always did.
- Materials: slot -> PMT0 name (`l_physics:roadSG`...) -> MC3 physical class
  (same rules as `mc3_bound.physical_material_name`) -> index in the donor's
  table.

## Trees

Built on the EE with the rules of `mc2_bnd_to_mc3.py` (cube root; octant bit0 x,
bit1 z, bit2 y; quadrant bit0 x, bit1 z - checked on tokyo's quadtree, 222/222
references in the right cell; leaf `start<<16 | bytes<<1 | 1`; empty child = a
zero-count leaf; 16-byte array header before each block as in retail). Octree
depth 8 / 48 per leaf, quadtree depth 3 / 15. A counting pass runs first and
doubles the per-leaf limit if the tree would not fit its budget.

Result for LA: type 9 = 29096 verts, 20379 polys, 45726 refs, 617 blocks,
depth 8; type 10 = 140 verts, 176 polys (limit 2000/2000), 347 refs, 10 blocks.
Body 0x1143F0 bytes (1.08 MB) against 0x1A5770 (1.65 MB) for the old
`modcity_bnd.pck`; the renderer sees ~550 KB more free heap. Build time ~1 s at
city load.

## Validation

`build_reference.py OUT.pck` is the Python twin. Its output passed
`mc3_bound` (leaves partition the list, 0 area mismatches) and booted in game.
The body the mod built was then dumped from a savestate (EE RAM at the RBN1
address) and compared with the twin: same geometry, material tables, node
fields and the same multiset of leaf contents in both trees.

## Markers

    RBN1 <body> <size>          built and returned
    RBN2 <verts9> <polys9>
    RBN3 <refs9> <blocks9 | depth << 24>
    RBN4 <verts10 | polys10 << 16> <refs10 | blocks10 << 16>
    RBN0 <stage> <detail>       failed; the original loader runs instead

## Caveat

This is MC2's native collision. It does not carry what Fork City COLLISION
added to `modcity_bnd.pck` (v18-v21: instance asphalt at crossings, curbs,
its own type-10 ground plane). MC2's own answer for road surfaces is the
`losangeles_quad_0` quadtree, which is now used as type 10. To go back: set
`modcity_bound.mod = 0` and restore `modcity_bnd.pck` from
`research/city/backups/Fork City MODS/2026-09-26_bound_from_rsc/before/`.
