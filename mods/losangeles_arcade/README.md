# Added-city Arcade support

`losangeles_arcade.mod` extends the Arcade city cycle from four entries to seven
and treats registered cities 5 and 6 as unlocked. Retail cities are unchanged;
the empty `nocity` record at index 4 is still skipped because it has no races.

Paris depends on `city_paris_slot7.mod` registering index 6 and on a valid
`ASSETS/tune/race/paris.loc`. The verified Paris catalog has 24 entries: the
standard Cruise plus 23 races converted from the user's own MC2 files.

The converter `mc2_races_to_mc3.py --mc2-city paris` removes nested MC2
`PreRacePath` blocks. MC3 does not parse those blocks correctly and otherwise
passes a null car name to the opponent factory after the first occurrence.

Runtime marker: `RARC` reports the guarded menu patch result.

