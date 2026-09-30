# Paris city slot

`city_paris_slot7.mod` grows the city registry from six records to seven,
registers Paris at index 6, and updates every guarded city-table bound used by
the runtime. This includes `mc::LoadCityData` at `0x004B79D8`; without that
word change Paris registers visually but receives zero races.

The registered hoods are `p_bh`, `p_dt`, `p_fwy`, `p_lf`, `p_mt`, `p_ug`, and
`p_we`. The patch is guarded against the measured retail words and refuses a
partial application. `CCPT 00000008` confirms all eight words were changed;
`CCR6 00000018` confirms the verified Paris catalog of 24 entries.

