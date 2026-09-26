# city_no_cpv_test

Temporary A/B of the city draw path. The six `rmcModel::DrawCpv` call sites in
the component and instance renderers are redirected to `rmcModel::Draw`,
keeping model, shader group, shader data, bucket, argument 5, VIF packets,
Matrix34 and passes.

Reading the test:

- if the flattened/stacked geometry disappears, the cause is in the injection
  or selection of the CPV stream;
- if it stays the same, the CPV route is cleared and the next target is the
  position consumption by the VU/common draw path.

It is not a final fix: vertex colours and lighting can change while the module
is active.
