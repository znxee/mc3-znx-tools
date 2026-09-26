# city_no_instances_test

Diagnostic A/B for the v4 -> v5 regression.  It suppresses only the four
`mcInstCityModel` draw callsites while the generated 18-group city is active.
Unique/component geometry is left untouched.  Outside that city the wrappers
forward the original arguments to `rmcModelGeom::Draw` / `DrawCpv`.

This is not a final fix: it intentionally hides every city instance.  If the
origin pile disappears, the malformed submission is in the instance
population exposed by the class counts enabled in v5; if it remains, inspect
the unique/component path instead.
