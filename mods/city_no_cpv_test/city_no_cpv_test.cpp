// Diagnostic A/B for generated City geometry.
// Keep the exact model, shader group, VIF packets, Matrix34 and draw passes,
// but route the six City DrawCpv calls through rmcModel::Draw.  If the stacked
// geometry disappears, the corruption is in CPV injection/selection; if it
// remains, CPV is not the cause.  This is intentionally a test, not a fix.

#include "../../payload/mc3_mod.h"

enum {
    RMC_MODEL_DRAW = 0x002A9918u,
};

extern "C" mc3_u32 city_no_cpv_test(mc3_u32 model, mc3_u32 shader_group,
                                      mc3_u32 shader_data, mc3_u32 bucket,
                                      mc3_u32 arg5, mc3_u32 cpv_index)
{
    (void)cpv_index;
    return MC3_CALL5(mc3_u32, RMC_MODEL_DRAW,
                     mc3_u32, mc3_u32, mc3_u32, mc3_u32, mc3_u32)(
        model, shader_group, shader_data, bucket, arg5);
}

// mcCityModelType::Render: render passes 2, 8 and 64.
MC3_HOOK(0x0024C42Cu, city_no_cpv_test);
MC3_HOOK(0x0024C47Cu, city_no_cpv_test);
MC3_HOOK(0x0024C508u, city_no_cpv_test);

// mcInstCityModelType::Render: the same three passes.
MC3_HOOK(0x002570B4u, city_no_cpv_test);
MC3_HOOK(0x00257104u, city_no_cpv_test);
MC3_HOOK(0x00257190u, city_no_cpv_test);
