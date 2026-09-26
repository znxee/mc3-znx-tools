# no_road_reflectors_test

A/B diagnosis, not a final fix. The module makes
`mcRoadReflectorSystem::RenderAllTypes` (`0x001DBD58`) return immediately.

It only changes the code when it finds the two original opcodes of the retail
ELF: `27BDFFE0 3C020062`. Then it writes `03E00008 00000000` (`jr ra; nop`) and
flushes the EE caches.

Use it through the modloader:

```ini
[mods]
core.mod = 1
no_road_reflectors_test.mod = defer_once
```

For the control, change only the last line to `0`. Keep boot, assets and the
other options identical. A black screen, an overlay or a loading screen does not
count as a rendered city; log the first error/PC and save a savestate on each
run.
