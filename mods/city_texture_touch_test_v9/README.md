# city_texture_touch_test_v9

CTT9 keeps CTT8's generated-city gate and CPV bounds report, but calls stock
`rmcModel::TouchShaders` at most once per `(model, shader group)` in each city
frame.  The frame epoch comes from the retail call at `0x00257BAC` to
`mcCullableMgr::Render` (`0x0024A860`).

Load this module as `defer_once`; its 1024-entry epoch cache lives in game-owned
heap memory and its hooks remain installed after initialization.

Expected healthy evidence after entering Mod City:

- `touch_requests` grows with Draw/DrawCpv;
- `touch_calls` is substantially lower and `touch_skipped` grows;
- `cache_full=0`;
- `oob_calls=0` and `clamp_calls=0` with the cardinality-fixed city PCK.
