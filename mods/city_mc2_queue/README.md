# city_mc2_queue (experimental)

This is the next renderer experiment from FORK C++ MODS AND PATCHES. It adds
one MC2 VIF stream to `lowPsxGfx::AddVCLCall` after the live camera setter. The
game's own `SendBuffer` and scratchpad MFIFO then carry it to VIF1; this module
does not write DMA channel registers.

## Build the packet

`mc2piece.bin` must be the chain generated for the already-tested MC2 ramp.
Flatten its CNT/END DMA tags and VIF tag codes into the packet format expected
by `AddVCLCall`:

```powershell
python pack_vif_queue.py $MC3_HOSTFS\mc2piece.bin $MC3_HOSTFS\mc2piece.vcl
```

The packet is loaded once from `host0:/mc2piece.vcl`. Keep it under 256 KiB.
The converter validates that the source consists only of CNT tags ending in
END, the flattened stream is QWC-aligned, and the initial 64-QW VU data block
is present. It records the transform row offset in the 16-byte packet header.

## Build the module

Use the toolkit's `build_mod.sh city_mc2_queue`, then copy
`mc2piece.vcl` to HostFS and enable `city_mc2_queue.mod` only for a test boot.
The source folder is experimental; it is deliberately separate from
`city_mc2_inject` and is not added to the shared `mc3boot.ini`.

The hook is installed only at `SetCamera` (`0x257AEC`) and calls the original
camera setter. Each frame it rebuilds the packet's row-vector view-projection
matrix from the live camera `Matrix34` and the projection stored at
`gfxViewport* + 0x10` (the pointer is at `0x70E50C`). On the first city frame,
it anchors the piece 30 metres in front of that camera; later frames keep that
world position fixed so a freecam can orbit it. It flushes its data cache and
queues a REF via `AddVCLCall(0x526A60)`. The translation row must retain its
homogeneous W value of 1. A controlled capture now differs substantially from the no-mod
control and shows a large dark shape ahead of the camera, consistent with the
piece becoming visible; exact silhouette, scale, and placement still need
visual validation. `QADD` proves enqueue only, not rendering. The neighboring
city-render hook at `0x257BAC` is not used because
`city_texture_touch_test.mod` already hooks it in the current INI.
