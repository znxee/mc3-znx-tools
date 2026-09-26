# MC2 direct-RSC reader prototype

This isolated `defer_once` probe reads the original PS2 `losangeles.rsc` from
HostFS. It parses the `RSC0` block directory, reads the first `CMI0` model
descriptor, resolves its first near-LOD handle to a `PMD0` block, and reads the
first 16 bytes of that display-list block. It reports the file size, block
counts, model position/radius bit patterns, handle/index, and PMD0 prefix to the
PCSX2 log as `RSC0`, `RSCN`, `RSCM`, and `RSCP` records.

It deliberately does **not** draw anything. This checks direct file access and
the first stages of the RSC consumer without changing `city_mc2_scene` or
hooking its camera. For the full runtime directory/name/texture audit, use
`city_mc2_rsc_probe_v2.mod` and `README_v2.md`.

## Build and stage

Copy this directory to `$MC3BOOT/mods/city_mc2_rsc_probe`, then build with:

```sh
cd "mods"
sh build_mod.sh city_mc2_rsc_probe /y/MC3HostFS
```

Place the original file at `$MC3_HOSTFS/mc2_losangeles.rsc`. To run on the next
boot, add under `[mods]` in `mc3boot.ini`:

```ini
city_mc2_rsc_probe.mod = defer_once
```

Keep `city_mc2_scene.mod = shim` enabled; this probe has no hooks and should
coexist with it. Do not put this test module in `output/release`.
