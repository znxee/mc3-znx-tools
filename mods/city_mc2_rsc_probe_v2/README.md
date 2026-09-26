# MC2 direct-RSC runtime validator v2

`city_mc2_rsc_probe_v2.mod` is a no-hook, one-shot diagnostic; it does not draw
or change `city_mc2_scene`. It opens the MC2 Los Angeles RSC, its RNT name table,
and `textures_original.rsc` through the game's Stream API.

The v2 probe scans the city RSC sequentially. It checks every CMI0 name and all
four near/far pairs (eight PMD0 handles), then walks every PMD0 DMA chain and
checks each CALL texture handle against the city RSC or shared texture-bank
directory. It keeps only the two block tables, a CMI-name bitmap, the small RNT,
and a 4 KiB scratch buffer in the game heap; it never allocates the full 9 MiB
city RSC.

Required HostFS root files:

```text
mc2_losangeles.rsc
mc2_losangeles.rnt
textures_original.rsc
city_mc2_rsc_probe_v2.mod
```

Build and stage from the toolkit:

```sh
cd "mods"
sh build_mod.sh city_mc2_rsc_probe_v2 /y/MC3HostFS
```

Temporarily enable under `[mods]` in `mc3boot.ini`:

```ini
city_mc2_rsc_probe_v2.mod = defer_once
```

Keep `city_mc2_scene.mod = shim` enabled for comparison. Expected log markers
are `RSC0`, `RSCN`, `RNT0`, `RSCV`, `TXTB`, and `RSCX`; any `RSCF` is a stage
and error code. In `RSCX`, `bad_calls` must be zero. This verifies container,
LOD, naming, and texture-handle reads at runtime, but not the VIF/VU rendering,
texture upload, CPVS colors, `.hood` placement, or visual correctness.

Test log: `Y:/Atomic Chat/modderG_iso_work/city_mc2_rsc_probe_correctname_20260923/runtime_v2/pcsx2_v2.log`.
