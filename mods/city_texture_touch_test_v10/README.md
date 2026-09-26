# city_texture_touch_test_v10

CTT10 removes CTT9's forced `f12=0.0`. At every city draw it reads the LOD
already selected by retail `rmcTextureFactoryPS2::SetTextureLod` from
`0x006BE810`, reconstructs a float that maps to that same level through
`GetTextureLod`, and passes it to `rmcModel::TouchShaders`.

The epoch cache keeps the finest LOD requested for each `(model, shader group)`.
A repeated pair is touched again only if a later instance in the same frame is
closer and needs a finer mip. This preserves near geometry without requesting
LOD0 for every distant instance.

The in-game overlay shows:

- per-frame LOD 0/1/2/3+ selection counts;
- resident/pending PPF pages for fileId 123 and the global request queue;
- free/total texture-memory blocks (one block is 256 bytes).

The report magic is `CT10`; use `read_city_touch_trace_v10.py` on an extracted
`eeMemory.bin`. Load the installed generic module as `defer_once`:

```ini
city_texture_touch_test.mod = defer_once
```

The overlay hook is the retail call at `0x001A2EDC` to
`aiObstacleGrid::DebugDraw`; the original is called first. It does not conflict
with `debug_draw.mod` (`0x001A2EB4`) or freecam's three `mcGame::Draw` hooks.
