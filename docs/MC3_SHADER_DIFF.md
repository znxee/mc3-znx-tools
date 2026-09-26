# MC3 PS2 city shader diff

Date: 2026-08-24  
Fork: MC3 Textures and Shaders

## Short result

The first controlled diff was done without changing any `.pck`.

- Material `63` is asphalt with high confidence: it shows up in 34 meshes,
  including the standard families `a_inst_2lane_*`, `a_inst_4lane_*` and
  `a_inst_6lane_*`.
- Slot `g0:m63` is not a `0xB0` basic shader: it is an owner-placed type 17
  wrapper of `0x40`, with two references. A blind `0xAC` diff starting from it
  runs into the following objects.
- In `g9`, the first texture-info of `m63` is exactly the same object
  `0x0686DB70` used by the basic shader `g9:m31`. That is why `g9:m31` is the
  controlled basic representative on the asphalt side.
- `g9:m481` is another basic shader of the same size and was chosen as the
  building/window side. The material shows up in
  `a_dt_blk_fivepoints_01x#geom3#3 *` and uses a square texture chain. That
  makes it a cheap candidate for glass, but the visual identification as glass
  is not proven yet.
- The safe pair to repeat the diff with is therefore `g9:m31` against
  `g9:m481`.

Command:

```powershell
python .\mc3_shader_diff.py "tools\output\atlanta_backup\atlanta_midnight_clear.pck" --a 31 --group-a 9 --b 481 --group-b 9
```

## Measured structure

Atlanta's inline basic shader takes `0xB0` bytes. `+0xAC` is the offset of the
last dword, not the total size.

| Offset in the shader | Size | Measured content |
|---:|---:|---|
| `+0x00` | 4 | `rmcShaderBasic` token/vtable |
| `+0x04` | 4 | type; `word & 0x7F == 0` |
| `+0x08` | 4 | pointer to the texture-info |
| `+0x0C` | 4 | padding (`CD`) |
| `+0x10` | `0xA0` | inline texture-info |

Each mip/TEX0 record inside the texture-info takes `0x10`, not `0x18`:

```text
u32 index_or_address
u16 transfer_width
u16 transfer_height
u32 TEX0_low
u32 TEX0_high    # 0x20000005 in the valid records measured
```

There are three consecutive records in both samples. The final meaning of the
first `u32` and of the `u16` selector is not proven yet; the names the
comparer uses are deliberately neutral.

## Controlled Atlanta diff

| Field | Basic asphalt `g9:m31` | Building/window candidate `g9:m481` |
|---|---|---|
| class/type/size | basic / 0 / `0xB0` | basic / 0 / `0xB0` |
| texture-info | `0x0686DB70` | `0x06874890` |
| selector | 3 | 2 |
| record 0 | index 166, transfer 32x46 | index 165, transfer 16x25 |
| TEX0 0 | PSMT8, nominal 128x64, TBW 2 | PSMT8, nominal 64x64, TBW 1 |
| record 1 | index 133, transfer 8x14 | index 132, transfer 4x9 |
| TEX0 1 | PSMT8, nominal 64x32 | PSMT8, nominal 32x32 |
| record 2 | index 100, transfer 2x6 | index 99, transfer 1x5 |
| TEX0 2 | PSMT8, nominal 32x16 | PSMT8, nominal 16x16 |

Both blocks keep the same shape, tokens, three `TEX0_high`, padding and most of
the tail. What changes is the selector, the three texture records and fields at
`+0x5C..+0x78`, `+0x90` and `+0x98`.

Negative control: comparing the same material `150` in `g0` and `g9`, selector,
indices, transfers and TEX0 stay identical; only `+0x5C..+0x78` changes. So
that range depends on the group/pass or on the placement in the dictionary and
cannot be treated on its own as the material's visual identity.

The full blocks of the controlled pair are identical across the nine Atlanta
combinations (dawn/dusk/midnight x clear/cloudy/rainy):

| Block | Abbreviated SHA-256 in all nine files |
|---|---|
| `g9:m31` | `74b2e30de856` |
| `g9:m481` | `f8acb6835831` |

## Why Tokyo was rejected

Tokyo has the same logical class. What changes are the tokens and the
serialisation form.

Every resource token measured uses a uniform shift of `+0x488`:

| Logical class | Atlanta/Detroit/SD | Tokyo |
|---|---:|---:|
| shader basic | `0x007A1EF8` | `0x007A2380` |
| shader named | `0x007A1F58` | `0x007A23E0` |
| shader instance | `0x007A1FC8` | `0x007A2450` |
| texture node | `0x007A20E0` | `0x007A2568` |
| texture info | `0x007A2320` | `0x007A27A8` |
| owner type 17 | `0x007B22B8` | `0x007B2740` |
| owner type 16 | `0x007B2318` | `0x007B27A0` |

Example measured in `tokyo_midnight_clear.pck`:

- `g0:m0` is at `0x06802330`, has token `0x007A2380` and type 0;
- the serialised header is only `0x0C` bytes;
- `+0x08` points to the external texture-info at `0x06804100`;
- that texture-info uses token `0x007A27A8` and the same `0xA0` layout.

Tokyo's basic headers are packed every 12 bytes, while the texture-info sits in
another region. Searching only for the literal `0x007A1EF8` and requiring
`pointer == object + 0x10` produces the false result "Tokyo does not have this
class". San Diego and Detroit use the base tokens, but also have the external
form; token profile and storage form are independent variables.

## Rule for extracting or generating

1. Detect the token profile; so far shifts of `0` and `+0x488` were measured.
2. Classify the slot by class and type before choosing the size.
3. Follow the `+0x08` pointer instead of assuming the sub-object is inline.
4. For an inline basic, copy/generate a `0x10` header + `0xA0` texture-info and
   relocate the pointer.
5. For an external basic, treat the `0x0C` header and the `0xA0` texture-info
   as two linked allocations.
6. For wrappers/instances, walk the class's references; never copy `0xAC`
   blindly starting from the slot.

That makes Tokyo a valid donor in principle. The rule still has to be
implemented in the city builder and the result proven at runtime.

## Tool and validation

`mc3_shader_diff.py` now:

- detects the Atlanta-family and Tokyo `+0x488` profiles;
- tells inline blocks from external resources;
- follows texture nodes and texture-info;
- decodes TEX0/PSM/nominal dimensions;
- normalises pointers in the diff;
- emits the external texture-info diff separately.

Validated, read-only, on:

| City | Groups | Slots/group | Filled | Profile | Basic observed |
|---|---:|---:|---:|---|---|
| Atlanta | 18 | 1329 | 4105 | base | inline `0xB0` in the pair |
| Detroit | 18 | 1154 | 4079 | base | `0x0C` header + external info |
| San Diego | 15 | 1516 | 4020 | base | `0x0C` header + external info |
| Tokyo | 18 | 1720 | 5970 | `+0x488` | `0x0C` header + external info |

Note: San Diego's nine main PCKs (three times of day x three weathers) have 15
groups. So `18` should not be promoted to a global constant without measuring
the exact context.
