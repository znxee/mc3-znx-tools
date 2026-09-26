# mc2_view - free camera over MC2's Los Angeles, drawn by MC3's own VU1 microcode

Not published: it needs files from the MC2 PS2 dump (`$MC2_PS2_ASSETS`).

1. Make the scene (once, ~10 s):

       python mc2_scene_blob.py "<...>/resource/losangeles/losangeles.rsc" ^
              --pieces 3000 --radius 320 --view --bin $MC3_HOSTFS/mc2view.bin

   `--radius` is the half-size in metres of the square around the densest 300 m cell;
   `--centre X Z` moves it (Santa Monica ramp: `--centre -820 -1110 --radius 130`).
   A bigger scene is slower and eventually stops fitting in VRAM (pieces are dropped).

2. Build the mod:  `sh mods/build_mod.sh mc2_view $MC3_HOSTFS`

3. In `$MC3_HOSTFS/mc3boot.ini` keep only what is needed:

       [mods]
       core.mod = bootstrap
       mc2_view.mod = shim

   Boot `mc3boot.elf` (no savestate). The game never starts: the mod takes over at
   `jal mcGame::Execute` and flies the camera.

Keys (the same USB keyboard freecam uses; the mod calls the game's own keyboard poll,
sub_234698, every frame because nothing else does in this state):

    W/S forward/back   A/D left/right   R/F up/down   arrows look
    Shift fast   Ctrl slow   Z/X field of view   M/N speed   H restart pose

Boot keys (`[boot]` in mc3boot.ini), all optional:

    pace = ms      minimum time per frame (default 100)
    minpx = N      skip pieces smaller than N pixels across (default 4; 0 = frustum test only)
    lodpx = N      pieces that look smaller than N pixels draw their far LOD (off by default)
    nocull = 1     send every piece every frame
    cama = 1       fly a short fixed route by itself (for screenshots)

Culling (per piece, on the EE, every frame): the 8 corners of the piece's box against the six clip
planes, then its size on screen. The visible pieces are strung together by rewriting the last tag of
each from `cnt` to `next` (address = the next visible piece); the last one points at an `end` tag.

WHY `pace`: PCSX2 draws on its own thread, and nothing the guest can read tells it when a frame is
finished (the GS FINISH bit is set when the GIF hands the packet over; a GS->host download does not
wait either). An EE loop that runs ahead queues frames, and the picture on screen is then caught
half drawn - about every other frame in the tests. A fixed period longer than the GS needs cures it:
on the 1075-piece scene with `minpx = 4` 60 ms was clean (6 of 6 shots), 30 ms had one half-drawn
frame in six. If you see flicker in a dense view, raise `pace`.

Measured on that scene (close view): about 10 fps at the default. Far LODs did not raise the frame
rate (the cost is the number of draw calls, not the number of triangles), hence off by default.
Every step of the camera is per FRAME, not per second; M/N change the step.
