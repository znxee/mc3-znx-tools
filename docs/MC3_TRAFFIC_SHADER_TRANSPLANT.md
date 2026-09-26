# Traffic-stock and full vehicle shaders in traffic PCKs

`mc3_traffic_mlod.py` defaults to the lean `traffic-stock` profile.  It imports
no shader or texture from the player vehicle: every non-emissive donor material
maps to the target traffic car's existing `ambient_body.shadert`, while every
`emissive_*` material maps to its existing
`ambient_emissive_brakelight.shadert`.  The separate decals mesh is omitted.

```powershell
python mc3_traffic_mlod.py `
  "$MC3_HOSTFS\ASSETS\resources\city\atlanta_traffic.pck" `
  "$MC3_HOSTFS\ASSETS\resources\vehicle\vp_350z_04\vp_350z_04.pck" `
  --vehicle va_civic_sh --lod MLOD --stock `
  --uv-profile civic-share `
  --out output\traffic\teste_mlod_stock_350z_traffic_stock.pck
```

The former 20-material transplant remains available for analysis with
`--shader-profile full`.  That path uses `mc3_shader_transplant.py` and returns
the mapping `{vp shader index: new traffic shader index}`.

The old diagnostic path still exists:

```powershell
python mc3_traffic_mlod.py TRAFFIC VP --vehicle NAME --material 2 --out TEST.pck
```

That explicitly forces every body group to one existing traffic shader and
does not transplant anything.

## Traffic-stock profile

- The target vehicle keeps its own root, physics, tuning, audio, skeleton,
  wheels and shadow.
- The complete target shader group stays byte-identical.  No new group is
  created and no donor local, texture node, descriptor or pixel payload enters
  the traffic PCK.
- Decal/vinyl geometry is left out.  Carpaint, chrome, glass, rubber, trim,
  license plate and all other non-emissive groups become retail traffic body
  material; headlights, reverse lights, tail lights and brake lights become
  the retail traffic emissive material.
- Mapping is by shader name and target group contents, not fixed indices.  On
  the measured Atlanta and Tokyo Civic groups, body is index 3 and emissive is
  index 2.

Only the chosen vehicle's vroot mesh slot and the PCK body size change.  The
self-check requires the original shader-group pointer to remain unchanged and
every generated material index to fit its five retail slots.

## One-texture Civic UV profile

Player-car paint is colored by its shader, so its source UVs are not an atlas
for traffic use.  The retail `va_civic_sh` instead draws from the single
128x64 `va_civic_share.tex`.  Preserving 350Z UVs while replacing all those
materials with `ambient_body` therefore produces arbitrary Civic fragments on
the imported body even though the PCK is structurally valid.

`--uv-profile civic-share` is the lean visual path for this exact target.  It
keeps the retail texture untouched and moves every vertex to an interior pixel
selected by the donor shader role:

- carpaint and discarded decal roles -> silver body;
- window/colored glass -> neutral near-black glass (the traffic color
  randomizer turned the former blue swatch brown/orange on some spawns);
- black matte, rubber and trim -> black;
- chrome/default shiny -> bright or medium gray;
- tail/brake lights -> the colored lamp swatch;
- head/reverse lights and license plate -> light swatches.

The result is intentionally a material-color approximation, not a baked 350Z
skin: it preserves the visual separation needed by a traffic car without
reintroducing player-car shaders, customization textures, decals or vinyl.
`--uv-profile preserve` remains the default and is the right choice for other
traffic targets until their own one-texture preset is measured.  The tool
rejects `civic-share` for any vehicle other than `va_civic_sh` and verifies the
UV round trip at the PS2 packet precision of 1/4096 before embedding the mesh.

## Full profile

With `--shader-profile full`:

- Wheel/shadow shader entries stay at their original target indices.
- Every body group keeps its donor material, remapped to a newly appended
  per-vehicle `rmcShaderGroup`.
- The full donor local-parameter list is cloned in order because compiled
  shader locals refer to those positions.
- Reachable shader resources, texture nodes, strings/dictionary references,
  descriptors and inline pixel/palette payloads are cloned and rebased.
- A donor texture reference whose `+0x0C` points outside the donor PCK keeps
  its lookup name, but its serialized fallback is rewritten to the target
  traffic container's already-materialized white 8x8 texture (`__envmap__` in
  Atlanta; the structurally equivalent anonymous placeholder in Tokyo).  This
  matters during city load: if the named vehicle texture has not been registered
  yet, the original foreign address would otherwise be relocated into an
  invalid `0xFC...` EE pointer and crash on the first instanced draw.
- Texture payloads are treated as opaque bytes.  A pixel word that happens to
  resemble a PCK address is never rewritten as a pointer.

No existing object moves.  The full profile repoints the chosen vehicle's
`root+0x08` group pointer in addition to its vroot mesh slot.

The reader matches serialized objects by shape rather than fixed vtable values.
The same path therefore accepts Atlanta-family and Tokyo traffic token profiles.

## 350Z stock results

The default traffic-stock MLOD has 57 material groups, 149 strips, 4,906
vertices and 3,292 triangles.  It contains zero `carpaint.shadert` strings,
zero donor shaders/textures and uses only target materials 2 and 3.  Atlanta
produces 517,568 bytes; Tokyo produces 531,088 bytes.  Both pass the structural
self-check.  Enabling `civic-share` keeps all 57 groups and 3,292 rendered
triangles.  Collapsing UV seams lets the packet repacker share more vertices,
so the clean Atlanta output becomes 516,928 bytes; the self-check proves the
group and triangle topology did not change.

The opt-in full recut has 59 material groups, 153 strips, 5,004 vertices and
3,344 triangles.  Its separate decals piece owns two material groups.

The 20 donor indices used are:

```text
1, 3, 4, 5, 6, 7, 8, 9, 14, 17, 18, 19, 20, 23, 24, 25, 26, 46, 47, 48
```

They map consecutively to traffic indices 2 through 21.  The Atlanta test
clones 58 dependency segments and two inline textures (66,656 bytes of texture
payload).  Reopening the output verifies every material index, shader name,
group count, local parameter and PCK pointer bound.

The full profile's structural/offline checks also pass for Atlanta and Tokyo,
but its player-car `carpaint` path is deliberately not used by traffic-stock.
A console/game boot remains the final visual validation of the lean result.
