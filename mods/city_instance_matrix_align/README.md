# Instance Matrix34 alignment fix

One hook: `0x00256EB8`, the call to `DoSetWorld(Instance+0x20)`.
For the generated 18-group city, unaligned matrices are copied with scalar
loads/stores into an explicitly 16-byte-aligned stack buffer. DoSetWorld
copies all 48 bytes into its DMA packet synchronously, so the buffer need
not survive the return. All original draws remain enabled.

The builder used `align=4` for 96-byte Instance records. Downtown has all
1142 matrices at address modulo 16 = 4. DoSetWorld copies the DMA matrix
with LQ at `0x2ADBC8/0x2ADBE8/0x2ADBF8`, which align DOWN. Its later scalar
mirror at `0x70000000` reads the correct words and concealed the error in
the old WSHD tracer. Current capture contains 867 packets matching the
wrong aligned-down payload.

`IALN` diagnostic state captures up to 64 actual DMA uploads; it records
calls, corrections, packet errors, word mismatches, and the last sample.
An aligned matrix is forwarded untouched. Other cities are forwarded.

Activate with `city_instance_matrix_align.mod = defer_once`. Disable the
overlapping WSHD hook and disable `city_no_instances_test.mod` so objects
are visible. Start with a clean boot. A future PCK built with the corrected
16-byte Instance alignment no longer needs this compatibility hook.
