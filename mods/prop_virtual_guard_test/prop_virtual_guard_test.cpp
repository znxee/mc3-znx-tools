// -----------------------------------------------------------------------------
// prop_virtual_guard_test - diagnostic guard for the first city prop render.
//
// The stock call at 0x0038CC98 is `jalr v0`, where v0 should be a game-code
// method.  A partially initialized prop can instead supply an address outside
// the executable image.  Valid calls tail-call the original method unchanged;
// invalid ones are recorded and terminate only the current list walk.  A
// second guard rejects a renderer work-list whose count field is actually a
// stale pointer, avoiding the out-of-bounds loop observed in save 06.  A third
// guard skips only invalid shader virtual calls exposed after v4 advanced.  A
// fourth guard rejects the corrupt default renderer-state pointer exposed by
// save 08 before it can publish a VIF1 DIRECT packet with no GIF EOP.  Version
// 7 also records the caller frame behind rmcModel::Draw when a shader dispatch
// is rejected, which identifies who supplied the bad s5/group pointer. Version
// 8 snapshots the 92-slot prop shader group immediately before and after the
// stock ResourcePageIn call, separating load-time corruption from a later
// lifetime/overwrite bug without using the shared MC3_LOG header address.
// Version 9 also performs the one-shot guard setup from that hook: a module
// declaring MC3_HOOK is hook-driven and is not entered by the frame dispatcher.
// Version 10 traces every static call to rmcShaderGroup::rmcShaderGroup and the
// generic props-context fixup. Version 11 covers all three direct fixup call
// sites. Version 12 detours the function entry itself, because the Atlanta
// context reaches 0x00397330 through jalr rather than any direct call site.
// Version 13 corrects the trace phase: the identifying fields are populated by
// the virtual datResource read at 0x00397390, so an entry-time filter can never
// see them.  A second detour at 0x00397398 snapshots the context after that
// read and immediately before stock relocates context+0xB8. Version 14 fixes
// the object identity: that function constructs mcPropType children, while the
// malformed group belongs to mcPropManagerData+0x0C. Its Place routine calls
// the group constructor directly at 0x00394364, so that site is the target
// regardless of the already-transformed contents of group+8. The two mcPropType
// entry detours are no longer installed. Version 15 records every direct
// constructor call in the shared ring.  The v14 state proved that the manager
// call receives both group+4 and group+8 with loader+4 already applied, so the
// earlier writer must be identified before changing behavior. Version 16
// replaces the incomplete direct-call coverage with an entry detour on
// 0x002B3600; a single early call-site hook bootstraps that detour, which then
// sees direct calls, jalr calls and callback-driven calls by their saved RA.
// All use the relocatable MC3K logger v2 supplied by the C++ fork.
// -----------------------------------------------------------------------------

#define MC3_LOG_OWNER 0x50564744u /* 'PVGD' */
#include "../../payload/mc3_log.h"

enum {
    CALL_SITE      = 0x0038CC98u,
    ORIGINAL_JALR  = 0x0040F809u,
    PACKET_SITE    = 0x0025C02Cu,
    ORIGINAL_PACKET_JAL = 0x0C096968u, // jal 0x0025A5A0
    PACKET_FUNCTION = 0x0025A5A0u,
    SHADER_SITE_V0 = 0x002A99E4u,
    SHADER_SITE_V1 = 0x002A9A48u,
    ORIGINAL_SHADER_V0 = 0x0040F809u, // jalr v0
    ORIGINAL_SHADER_V1 = 0x0060F809u, // jalr v1
    STATE_SITE      = 0x00528A08u,
    STATE_FIELD_SITE = 0x00528A0Cu,
    ORIGINAL_STATE_LOAD = 0x8C444880u, // lw a0,0x4880(v0)
    ORIGINAL_STATE_FIELD = 0x8C85004Cu, // lw a1,0x4c(a0)
    STATE_SKIP      = 0x00528A98u,
    CONTEXT_ENTRY   = 0x00397330u,
    CONTEXT_RESUME  = 0x00397338u,
    ORIGINAL_CONTEXT_0 = 0x27BDFFC0u, // addiu sp,sp,-64
    ORIGINAL_CONTEXT_1 = 0xFFB20010u, // sd s2,16(sp)
    CONTEXT_POST_READ = 0x00397398u,
    CONTEXT_POST_NONZERO = 0x003973A4u,
    CONTEXT_POST_ZERO = 0x003973B4u,
    ORIGINAL_POST_READ_0 = 0x8E4300B8u, // lw v1,0xb8(s2)
    ORIGINAL_POST_READ_1 = 0x50600005u, // beqzl v1,0x3973b4
    GROUP_CTOR_ENTRY = 0x002B3600u,
    GROUP_CTOR_RESUME = 0x002B3608u,
    ORIGINAL_GROUP_CTOR_0 = 0x27BDFFF0u, // addiu sp,sp,-16
    ORIGINAL_GROUP_CTOR_1 = 0x3C020062u, // lui v0,0x62
    CODE_MIN       = 0x00100000u,
    CODE_MAX       = 0x00700000u,
    FLUSH_CACHE    = 0x00546C20u,
    REPORT_MAGIC   = 0x44475650u, // bytes "PVGD"
};

struct prop_virtual_guard_report {
    mc3_u32 magic;
    mc3_u32 version;
    mc3_u32 installed;
    mc3_u32 patched;
    mc3_u32 original_word;
    mc3_u32 hits;
    mc3_u32 object;
    mc3_u32 index;
    mc3_u32 vtable;
    mc3_u32 method;
    mc3_u32 manager;
    mc3_u32 array;
    mc3_u32 slot_value;
    mc3_u32 caller_ra;
    mc3_u32 refreshed;
    mc3_u32 skipped;
    mc3_u32 packet_hits;
    mc3_u32 packet_object;
    mc3_u32 packet_view;
    mc3_u32 packet_count;
    mc3_u32 packet_caller_ra;
    mc3_u32 packet_original_word;
    mc3_u32 packet_patched;
    mc3_u32 packet_list_offset;
    mc3_u32 packet_limit;
    mc3_u32 shader_hits;
    mc3_u32 shader_object;
    mc3_u32 shader_vtable;
    mc3_u32 shader_method;
    mc3_u32 shader_model;
    mc3_u32 shader_group;
    mc3_u32 shader_geom;
    mc3_u32 shader_caller_ra;
    mc3_u32 shader_site;
    mc3_u32 shader_original_v0;
    mc3_u32 shader_original_v1;
    mc3_u32 shader_patched;
    mc3_u32 state_hits;
    mc3_u32 state_object;
    mc3_u32 state_reason;
    mc3_u32 state_caller_ra;
    mc3_u32 state_skipped;
    mc3_u32 state_original_load;
    mc3_u32 state_original_field;
    mc3_u32 state_patched;
    mc3_u32 shader_draw_caller_ra;
    mc3_u32 shader_draw_parent_ra;
    mc3_u32 shader_draw_saved_s0;
    mc3_u32 shader_draw_saved_s1;
    mc3_u32 shader_draw_saved_s2;
    mc3_u32 shader_draw_saved_s3;
    mc3_u32 shader_draw_saved_s4;
    mc3_u32 shader_draw_saved_s5;
    mc3_u32 shader_draw_saved_s6;
    mc3_u32 shader_source_kind;
    mc3_u32 shader_source_owner;
    mc3_u32 shader_source_selector;
    mc3_u32 shader_source_base;
    mc3_u32 shader_source_expected;
    mc3_u32 shader_group_vtable;
    mc3_u32 shader_group_slots;
    mc3_u32 shader_group_slot4;
    mc3_u32 pagein_calls;
    mc3_u32 pagein_target_hits;
    mc3_u32 pagein_group;
    mc3_u32 pagein_loader;
    mc3_u32 pagein_before_vtable;
    mc3_u32 pagein_before_slots;
    mc3_u32 pagein_before_counts;
    mc3_u32 pagein_before_list;
    mc3_u32 pagein_before_slot0;
    mc3_u32 pagein_before_slot4;
    mc3_u32 pagein_after_vtable;
    mc3_u32 pagein_after_slots;
    mc3_u32 pagein_after_counts;
    mc3_u32 pagein_after_list;
    mc3_u32 pagein_after_slot0;
    mc3_u32 pagein_after_slot4;
    mc3_u32 constructor_calls;
    mc3_u32 constructor_target_hits;
    mc3_u32 constructor_site;
    mc3_u32 constructor_group;
    mc3_u32 constructor_loader;
    mc3_u32 constructor_delta;
    mc3_u32 constructor_before_slots;
    mc3_u32 constructor_after_slots;
    mc3_u32 constructor_before_counts;
    mc3_u32 constructor_after_counts;
    mc3_u32 constructor_before_list;
    mc3_u32 constructor_after_list;
    mc3_u32 constructor_before_slot0;
    mc3_u32 constructor_before_slot4;
    mc3_u32 constructor_after_slot0;
    mc3_u32 constructor_after_slot4;
    mc3_u32 context_calls;
    mc3_u32 context_target_hits;
    mc3_u32 context_site;
    mc3_u32 context_addr;
    mc3_u32 context_loader;
    mc3_u32 context_delta;
    mc3_u32 context_before_b8;
    mc3_u32 context_predicted_group;
    mc3_u32 context_after_b8;
    mc3_u32 context_manager;
    mc3_u32 context_after_vtable;
    mc3_u32 context_after_slots;
    mc3_u32 context_after_counts;
    mc3_u32 context_after_list;
    mc3_u32 entry_calls;
    mc3_u32 entry_target_hits;
    mc3_u32 entry_original0;
    mc3_u32 entry_original1;
    mc3_u32 entry_patched;
    mc3_u32 post_read_calls;
    mc3_u32 post_read_target_hits;
    mc3_u32 post_read_original0;
    mc3_u32 post_read_original1;
    mc3_u32 post_read_patched;
};

extern "C" {
volatile prop_virtual_guard_report g_prop_guard = {
    REPORT_MAGIC, 16u
};
}

extern "C" void prop_virtual_dispatch();
extern "C" void render_packet_dispatch();
extern "C" void shader_virtual_dispatch_v0();
extern "C" void shader_virtual_dispatch_v1();
extern "C" void renderer_state_dispatch();
extern "C" void context_entry_dispatch();
extern "C" void context_post_read_dispatch();
extern "C" void group_constructor_entry_dispatch();
extern "C" void mod_main();

static int valid_ee_pointer(mc3_u32 p)
{
    return (p & 3u) == 0u && p >= 0x00100000u && p < 0x02000000u;
}

static void snapshot_group_slots(mc3_u32 slots, mc3_u32 *slot0,
                                 mc3_u32 *slot4)
{
    if (!valid_ee_pointer(slots)) {
        *slot0 = 0u;
        *slot4 = 0u;
        return;
    }
    volatile mc3_u32 *const table = (volatile mc3_u32 *)slots;
    *slot0 = table[0];
    *slot4 = table[4];
}

// 0x002B3600 is rmcShaderGroup::rmcShaderGroup(datResource&), not a passive
// PageIn callback.  It adds loader+4 to group+4 every time it runs, relocates
// the linked list at group+0x0C and then places the shader slots.  Hook every
// direct call site so a route missed by v8/v9 cannot escape the trace.
extern "C" void shader_group_constructor_entry_capture(mc3_u32 *group,
                                                        mc3_u32 loader,
                                                        mc3_u32 caller_ra);
extern "C" void shader_group_constructor_entry_capture(mc3_u32 *group,
                                                        mc3_u32 loader,
                                                        mc3_u32 caller_ra)
{
    g_prop_guard.constructor_calls += 1u;
    const int valid = valid_ee_pointer((mc3_u32)group);
    const mc3_u32 counts = valid ? group[2] : 0u;
    const mc3_u32 group_addr = (mc3_u32)group;
    const mc3_u32 site = caller_ra >= 8u ? caller_ra - 8u : caller_ra;
    const int target = valid &&
        (site == 0x00394364u ||
         counts == 0x005C005Cu ||
         group_addr == g_prop_guard.context_predicted_group ||
         group_addr == g_prop_guard.context_after_b8);
    const mc3_u32 delta = valid_ee_pointer(loader)
        ? ((volatile mc3_u32 *)loader)[1] : 0u;

    MC3_LOG4("ctor entry site/group/slots/count", site, group_addr,
             valid ? group[1] : 0u, counts);

    if (target) {
        g_prop_guard.constructor_target_hits += 1u;
        g_prop_guard.constructor_site = site;
        g_prop_guard.constructor_group = (mc3_u32)group;
        g_prop_guard.constructor_loader = loader;
        g_prop_guard.constructor_delta = delta;
        g_prop_guard.constructor_before_slots = group[1];
        g_prop_guard.constructor_before_counts = group[2];
        g_prop_guard.constructor_before_list = group[3];
        snapshot_group_slots(group[1],
            (mc3_u32 *)&g_prop_guard.constructor_before_slot0,
            (mc3_u32 *)&g_prop_guard.constructor_before_slot4);
        MC3_LOG4("ctor target site/group/delta/slots", site, group,
                 delta, group[1]);
        MC3_LOG4("ctor target count/list/slot0/4", group[2], group[3],
                 g_prop_guard.constructor_before_slot0,
                 g_prop_guard.constructor_before_slot4);
    }
}

// This is the earliest direct constructor call seen in every Atlanta load.
// Its only job is to install the entry detour before forwarding the call; the
// entry detour itself records this call and every later direct/indirect one.
static mc3_u32 group_constructor_bootstrap(mc3_u32 *group, mc3_u32 loader)
{
    if (!g_prop_guard.installed)
        mod_main();
    return MC3_CALL2(mc3_u32, GROUP_CTOR_ENTRY,
                     mc3_u32 *, mc3_u32)(group, loader);
}
MC3_HOOK(0x0025928Cu, group_constructor_bootstrap);

// Entry words 0x27BDFFF0/0x3C020062 become `j dispatch; nop`. Preserve the
// constructor arguments and its original RA, record the pre-mutation header,
// replay both stock words, and resume at 0x002B3608.
__asm__(
    ".set noreorder\n"
    ".set noat\n"
    ".globl group_constructor_entry_dispatch\n"
    ".ent group_constructor_entry_dispatch\n"
    "group_constructor_entry_dispatch:\n"
    "addiu $29, $29, -32\n"
    "sw    $4, 0($29)\n"
    "sw    $5, 4($29)\n"
    "sd    $31, 16($29)\n"
    "move  $6, $31\n"
    "jal   shader_group_constructor_entry_capture\n"
    "nop\n"
    "lw    $4, 0($29)\n"
    "lw    $5, 4($29)\n"
    "ld    $31, 16($29)\n"
    "addiu $29, $29, 32\n"
    "addiu $29, $29, -16\n"
    "lui   $2, 0x0062\n"
    "lui   $26, 0x002B\n"
    "ori   $26, $26, 0x3608\n"
    "jr    $26\n"
    "nop\n"
    ".end group_constructor_entry_dispatch\n"
    ".set at\n"
    ".set reorder\n"
);

// The target context does not use any of the three direct jal sites below: it
// reaches 0x00397330 through jalr. The entry detour calls this capture before
// replaying the stock prologue. caller_ra is the untouched RA from that jalr.
extern "C" void context_entry_capture(mc3_u32 *context, mc3_u32 loader,
                                      mc3_u32 caller_ra);
extern "C" void context_entry_capture(mc3_u32 *context, mc3_u32 loader,
                                      mc3_u32 caller_ra)
{
    g_prop_guard.entry_calls += 1u;
    (void)context;
    (void)loader;
    (void)caller_ra;
}

// The resource virtual at 0x00397390 has returned here.  Only now do +BC/+C0/
// +C4 contain the serialized counts which uniquely identify the Atlanta props
// context.  Stock has not yet added loader+4 to +B8, so this is the phase the
// v11/v12 trace intended to observe.
extern "C" void context_post_read_capture(mc3_u32 *context, mc3_u32 loader);
extern "C" void context_post_read_capture(mc3_u32 *context, mc3_u32 loader)
{
    g_prop_guard.post_read_calls += 1u;
    g_prop_guard.context_calls += 1u;
    const int target = valid_ee_pointer((mc3_u32)context) &&
        context[0xBCu / 4u] == 0x052Bu &&
        context[0xC0u / 4u] == 0x007Fu &&
        context[0xC4u / 4u] == 0x0464u;
    const mc3_u32 before_b8 = target ? context[0xB8u / 4u] : 0u;
    const mc3_u32 delta = valid_ee_pointer(loader)
        ? ((volatile mc3_u32 *)loader)[1] : 0u;

    if (target) {
        g_prop_guard.post_read_target_hits += 1u;
        g_prop_guard.context_target_hits += 1u;
        g_prop_guard.context_site = CONTEXT_POST_READ;
        g_prop_guard.context_addr = (mc3_u32)context;
        g_prop_guard.context_loader = loader;
        g_prop_guard.context_delta = delta;
        g_prop_guard.context_before_b8 = before_b8;
        g_prop_guard.context_predicted_group = before_b8
            ? before_b8 + delta : 0u;
        g_prop_guard.context_manager = context[0xB4u / 4u];
        MC3_LOG4("ctx before ctx/b8/mgr/loader", context, before_b8,
                 context[0xB4u / 4u], loader);
        MC3_LOG4("ctx before site/delta/predict/count", CONTEXT_POST_READ, delta,
                 g_prop_guard.context_predicted_group,
                 context[0xBCu / 4u]);
        const mc3_u32 predicted = g_prop_guard.context_predicted_group;
        if (valid_ee_pointer(predicted)) {
            volatile mc3_u32 *const group =
                (volatile mc3_u32 *)predicted;
            g_prop_guard.context_after_vtable = group[0];
            g_prop_guard.context_after_slots = group[1];
            g_prop_guard.context_after_counts = group[2];
            g_prop_guard.context_after_list = group[3];
            MC3_LOG4("ctx pre group vtbl/slots/count/list", group[0], group[1],
                     group[2], group[3]);
        }
    }
}

// The first two stock instructions are replaced by `j dispatch; nop`.
// Preserve a0/a1 and the original caller RA across the C capture, then replay
// both instructions exactly and resume at 0x00397338.
__asm__(
    ".set noreorder\n"
    ".set noat\n"
    ".globl context_entry_dispatch\n"
    ".ent context_entry_dispatch\n"
    "context_entry_dispatch:\n"
    "addiu $29, $29, -32\n"
    "sw    $4, 0($29)\n"
    "sw    $5, 4($29)\n"
    "sd    $31, 16($29)\n"
    "move  $6, $31\n"
    "jal   context_entry_capture\n"
    "nop\n"
    "lw    $4, 0($29)\n"
    "lw    $5, 4($29)\n"
    "ld    $31, 16($29)\n"
    "addiu $29, $29, 32\n"
    "addiu $29, $29, -64\n"
    "sd    $18, 16($29)\n"
    "lui   $26, 0x0039\n"
    "ori   $26, $26, 0x7338\n"
    "jr    $26\n"
    "nop\n"
    ".end context_entry_dispatch\n"
    ".set at\n"
    ".set reorder\n"
);

// Replace lw/beqzl at 0x00397398 with a phase-correct capture.  The stock
// branch-likely executes its delay-slot load only on the zero path; reproduce
// that explicitly, then resume at the exact original destinations.
__asm__(
    ".set noreorder\n"
    ".set noat\n"
    ".globl context_post_read_dispatch\n"
    ".ent context_post_read_dispatch\n"
    "context_post_read_dispatch:\n"
    "addiu $29, $29, -32\n"
    "sw    $2, 0($29)\n"
    "sd    $31, 16($29)\n"
    "move  $4, $18\n"
    "move  $5, $19\n"
    "jal   context_post_read_capture\n"
    "nop\n"
    "lw    $2, 0($29)\n"
    "ld    $31, 16($29)\n"
    "addiu $29, $29, 32\n"
    "lw    $3, 184($18)\n"
    "bnez  $3, 61f\n"
    "nop\n"
    "lw    $3, 176($18)\n"
    "lui   $26, 0x0039\n"
    "ori   $26, $26, 0x73B4\n"
    "jr    $26\n"
    "nop\n"
    "61:\n"
    "lui   $26, 0x0039\n"
    "ori   $26, $26, 0x73A4\n"
    "jr    $26\n"
    "nop\n"
    ".end context_post_read_dispatch\n"
    ".set at\n"
    ".set reorder\n"
);

// The frontend renderer loads its cloned default state from 0x006F4880 at
// 0x00528A08 and, when it is already current, publishes its six-qword payload
// directly to VIF1.  Save 08 is the first state in which that global is not the
// normal aligned object 0x0071AC80: it contains 0x007100AF, while the preceding
// word contains the other half of the same stray 64-bit write.  The resulting
// REF points into the strings ga_plate_2/ga_font_2; its fake GIFtag has EOP=0
// and leaves PATH2 open forever.
//
// Replace the load and its dependent field load with one guarded dispatch.  A
// valid object must be aligned, live in EE RAM, and carry the exact stock
// DMA/VIF header plus an EOP GIFtag.  Valid input recreates both original loads
// and resumes at 0x00528A10.  Invalid input is recorded and skips only this
// cloned-state packet, resuming at 0x00528A98 where stock code selects the
// independent base state from 0x006F4864.
__asm__(
    ".set noreorder\n"
    ".set noat\n"
    ".globl renderer_state_dispatch\n"
    ".ent renderer_state_dispatch\n"
    "renderer_state_dispatch:\n"
    "lui   $4, 0x006F\n"
    "lw    $4, 0x4880($4)\n"
    "beqz  $4, 31f\n"
    "addiu $5, $0, 1\n"
    "andi  $26, $4, 0xF\n"
    "bnez  $26, 31f\n"
    "addiu $5, $0, 2\n"
    "lui   $27, 0x0010\n"
    "sltu  $26, $4, $27\n"
    "bnez  $26, 31f\n"
    "addiu $5, $0, 3\n"
    "lui   $27, 0x0200\n"
    "sltu  $26, $4, $27\n"
    "beqz  $26, 31f\n"
    "addiu $5, $0, 3\n"
    "lw    $26, 0($4)\n"
    "lui   $27, 0x6000\n"
    "ori   $27, $27, 0x0006\n"
    "bne   $26, $27, 31f\n"
    "addiu $5, $0, 4\n"
    "lw    $26, 8($4)\n"
    "lui   $27, 0x1100\n"
    "bne   $26, $27, 31f\n"
    "addiu $5, $0, 5\n"
    "lw    $26, 12($4)\n"
    "lui   $27, 0x5000\n"
    "ori   $27, $27, 0x0006\n"
    "bne   $26, $27, 31f\n"
    "addiu $5, $0, 5\n"
    "lhu   $26, 16($4)\n"
    "andi  $26, $26, 0x8000\n"
    "beqz  $26, 31f\n"
    "addiu $5, $0, 6\n"
    "lw    $5, 76($4)\n"
    "jr    $31\n"
    "nop\n"
    "31:\n"
    "lui   $26, %hi(g_prop_guard)\n"
    "addiu $26, $26, %lo(g_prop_guard)\n"
    "lw    $27, 148($26)\n"
    "addiu $27, $27, 1\n"
    "sw    $27, 148($26)\n"
    "sw    $4,  152($26)\n"
    "sw    $5,  156($26)\n"
    "sw    $31, 160($26)\n"
    "lw    $27, 164($26)\n"
    "addiu $27, $27, 1\n"
    "sw    $27, 164($26)\n"
    "lui   $27, 0x0052\n"
    "ori   $27, $27, 0x8A98\n"
    "jr    $27\n"
    "nop\n"
    ".end renderer_state_dispatch\n"
    ".set at\n"
    ".set reorder\n"
);

// Entry registers at the replaced call:
//   v0 = target method, v1 = object's vtable, a0 = object (delay slot),
//   s3 = prop manager, sp+0x38 = caller's saved s3 = original prop index.
// k0/k1 are reserved kernel temporaries and can be used without changing the
// contract of a valid game method.  On an invalid target, v4 records both the
// bad node and the current array head, then exits this routine through
// 0x38CE80.  The next stock invocation reloads array[index] normally.  It must
// never restart the head from inside the same linked-list walk: save 07 proved
// that the stale pointer can be the tail reachable from that very head.
__asm__(
    ".set noreorder\n"
    ".set noat\n"
    ".globl prop_virtual_dispatch\n"
    ".ent prop_virtual_dispatch\n"
    "prop_virtual_dispatch:\n"
    "andi  $26, $2, 3\n"
    "bnez  $26, 2f\n"
    "nop\n"
    "lui   $27, 0x0010\n"
    "sltu  $26, $2, $27\n"
    "bnez  $26, 2f\n"
    "nop\n"
    "lui   $27, 0x0070\n"
    "sltu  $26, $2, $27\n"
    "beqz  $26, 2f\n"
    "nop\n"
    "jr    $2\n"
    "nop\n"
    "2:\n"
    "lui   $26, %hi(g_prop_guard)\n"
    "addiu $26, $26, %lo(g_prop_guard)\n"
    "lw    $27, 20($26)\n"
    "addiu $27, $27, 1\n"
    "sw    $27, 20($26)\n"
    "sw    $4,  24($26)\n"
    "lw    $27, 56($29)\n"
    "sw    $27, 28($26)\n"
    "sw    $3,  32($26)\n"
    "sw    $2,  36($26)\n"
    "sw    $19, 40($26)\n"
    "lw    $2,  40($19)\n"
    "sw    $2,  44($26)\n"
    "sll   $27, $27, 2\n"
    "addu  $27, $27, $2\n"
    "lw    $2,  0($27)\n"
    "sw    $2,  48($26)\n"
    "sw    $31, 52($26)\n"
    "3:\n"
    "lui   $26, %hi(g_prop_guard)\n"
    "addiu $26, $26, %lo(g_prop_guard)\n"
    "lw    $27, 60($26)\n"
    "addiu $27, $27, 1\n"
    "sw    $27, 60($26)\n"
    "lui   $27, 0x0038\n"
    "ori   $27, $27, 0xCE80\n"
    "jr    $27\n"
    "nop\n"
    ".end prop_virtual_dispatch\n"
    ".set at\n"
    ".set reorder\n"
);

// rmcModel::Draw has two branches which dispatch shader->vtable[0x28].  Save
// 07/v4 reached the first one with model 0x00BC3EE0 (a_prop_stlt_02x), shader
// group 0x009D6B90 and geometry 0.  The model requests shader index 0x50, but
// the resulting object word is 0x4E3AFFFF and its virtual target is zero.
// Valid shader methods remain exact tail-calls; invalid ones return to the
// stock loop so only that geometry is omitted and the diagnostic can advance.
__asm__(
    ".set noreorder\n"
    ".set noat\n"
    ".globl shader_virtual_dispatch_v0\n"
    ".ent shader_virtual_dispatch_v0\n"
    "shader_virtual_dispatch_v0:\n"
    "andi  $26, $2, 3\n"
    "bnez  $26, 20f\n"
    "nop\n"
    "lui   $27, 0x0010\n"
    "sltu  $26, $2, $27\n"
    "bnez  $26, 20f\n"
    "nop\n"
    "lui   $27, 0x0070\n"
    "sltu  $26, $2, $27\n"
    "beqz  $26, 20f\n"
    "nop\n"
    "jr    $2\n"
    "nop\n"
    "20:\n"
    "lui   $26, %hi(g_prop_guard)\n"
    "addiu $26, $26, %lo(g_prop_guard)\n"
    "lw    $27, 100($26)\n"
    "addiu $27, $27, 1\n"
    "sw    $27, 100($26)\n"
    "sw    $4,  104($26)\n"
    "sw    $3,  108($26)\n"
    "sw    $2,  112($26)\n"
    "sw    $5,  116($26)\n"
    "sw    $21, 120($26)\n"
    "sw    $7,  124($26)\n"
    "sw    $31, 128($26)\n"
    "sw    $0,  132($26)\n"
    "j     shader_bad_trace_common\n"
    "nop\n"
    ".end shader_virtual_dispatch_v0\n"
    ".set at\n"
    ".set reorder\n"
);

__asm__(
    ".set noreorder\n"
    ".set noat\n"
    ".globl shader_virtual_dispatch_v1\n"
    ".ent shader_virtual_dispatch_v1\n"
    "shader_virtual_dispatch_v1:\n"
    "andi  $26, $3, 3\n"
    "bnez  $26, 21f\n"
    "nop\n"
    "lui   $27, 0x0010\n"
    "sltu  $26, $3, $27\n"
    "bnez  $26, 21f\n"
    "nop\n"
    "lui   $27, 0x0070\n"
    "sltu  $26, $3, $27\n"
    "beqz  $26, 21f\n"
    "nop\n"
    "jr    $3\n"
    "nop\n"
    "21:\n"
    "lui   $26, %hi(g_prop_guard)\n"
    "addiu $26, $26, %lo(g_prop_guard)\n"
    "lw    $27, 100($26)\n"
    "addiu $27, $27, 1\n"
    "sw    $27, 100($26)\n"
    "sw    $4,  104($26)\n"
    "sw    $2,  108($26)\n"
    "sw    $3,  112($26)\n"
    "sw    $5,  116($26)\n"
    "sw    $21, 120($26)\n"
    "sw    $7,  124($26)\n"
    "sw    $31, 128($26)\n"
    "addiu $27, $0, 1\n"
    "sw    $27, 132($26)\n"
    "j     shader_bad_trace_common\n"
    "nop\n"
    ".end shader_virtual_dispatch_v1\n"
    ".set at\n"
    ".set reorder\n"
);

// rmcModel::Draw allocates 0x40 bytes and preserves the caller's s0-s6 at
// sp+0x00..0x30, followed by its real caller RA at sp+0x38.  The jalr hook's
// own RA (reported above) only names 0x002A99EC/0x002A9A50 and therefore could
// not answer who supplied s5.  This common invalid-only tail records the frame.
//
// The prop path at 0x003B1F18 is especially useful: its live s2 is the owner
// used to build a1, so Draw's saved s2 is the exact source object.  At that
// call site selector owner+4 chooses either *(owner+8) or *(owner+8)-0x10.
// Direct caller 0x002A9AD8 is a wrapper; its parent RA is at Draw sp+0x80.
__asm__(
    ".set noreorder\n"
    ".set noat\n"
    ".globl shader_bad_trace_common\n"
    ".ent shader_bad_trace_common\n"
    "shader_bad_trace_common:\n"
    "lui   $26, %hi(g_prop_guard)\n"
    "addiu $26, $26, %lo(g_prop_guard)\n"
    "lw    $27, 56($29)\n"
    "sw    $27, 180($26)\n"
    "sw    $0,  184($26)\n"
    "lw    $2,  0($29)\n"
    "sw    $2,  188($26)\n"
    "lw    $2,  8($29)\n"
    "sw    $2,  192($26)\n"
    "lw    $2,  16($29)\n"
    "sw    $2,  196($26)\n"
    "lw    $2,  24($29)\n"
    "sw    $2,  200($26)\n"
    "lw    $2,  32($29)\n"
    "sw    $2,  204($26)\n"
    "lw    $2,  40($29)\n"
    "sw    $2,  208($26)\n"
    "lw    $2,  48($29)\n"
    "sw    $2,  212($26)\n"
    "sw    $0,  216($26)\n"
    "sw    $0,  220($26)\n"
    "sw    $0,  224($26)\n"
    "sw    $0,  228($26)\n"
    "sw    $0,  232($26)\n"
    "sw    $0,  236($26)\n"
    "sw    $0,  240($26)\n"
    "sw    $0,  244($26)\n"

    // Resolve the only Draw wrapper separately so the report still names the
    // call site which supplied the wrapper's a1.
    "lui   $2, 0x002A\n"
    "ori   $2, $2, 0x9AD8\n"
    "bne   $27, $2, 40f\n"
    "nop\n"
    "addiu $2, $0, 2\n"
    "sw    $2, 216($26)\n"
    "lw    $2, 128($29)\n"
    "sw    $2, 184($26)\n"
    "40:\n"

    // Direct prop draw: recover and validate the owner before dereferencing
    // the two fields which produced a1/s5.
    "lui   $2, 0x003B\n"
    "ori   $2, $2, 0x1F20\n"
    "bne   $27, $2, 44f\n"
    "nop\n"
    "addiu $2, $0, 1\n"
    "sw    $2, 216($26)\n"
    "lw    $2, 16($29)\n"
    "sw    $2, 220($26)\n"
    "andi  $27, $2, 3\n"
    "bnez  $27, 44f\n"
    "nop\n"
    "lui   $27, 0x0010\n"
    "sltu  $27, $2, $27\n"
    "bnez  $27, 44f\n"
    "nop\n"
    "lui   $27, 0x0200\n"
    "sltu  $27, $2, $27\n"
    "beqz  $27, 44f\n"
    "nop\n"
    "lbu   $27, 4($2)\n"
    "sw    $27, 224($26)\n"
    "lw    $2, 8($2)\n"
    "sw    $2, 228($26)\n"
    "bnez  $27, 43f\n"
    "move  $27, $2\n"
    "addiu $27, $2, -16\n"
    "43:\n"
    "sw    $27, 232($26)\n"
    "44:\n"

    // Snapshot the malformed group header itself.  slot[4] is the value the
    // city fork observed as the mcPropBase vtable in its static capture.
    "andi  $2, $21, 3\n"
    "bnez  $2, 49f\n"
    "nop\n"
    "lui   $27, 0x0010\n"
    "sltu  $2, $21, $27\n"
    "bnez  $2, 49f\n"
    "nop\n"
    "lui   $27, 0x0200\n"
    "sltu  $2, $21, $27\n"
    "beqz  $2, 49f\n"
    "nop\n"
    "lw    $2, 0($21)\n"
    "sw    $2, 236($26)\n"
    "lw    $27, 4($21)\n"
    "sw    $27, 240($26)\n"
    "andi  $2, $27, 3\n"
    "bnez  $2, 49f\n"
    "nop\n"
    "lui   $2, 0x0010\n"
    "sltu  $2, $27, $2\n"
    "bnez  $2, 49f\n"
    "nop\n"
    "lui   $2, 0x0200\n"
    "sltu  $2, $27, $2\n"
    "beqz  $2, 49f\n"
    "nop\n"
    "lw    $2, 16($27)\n"
    "sw    $2, 244($26)\n"
    "49:\n"
    "move  $2, $0\n"
    "jr    $31\n"
    "nop\n"
    ".end shader_bad_trace_common\n"
    ".set at\n"
    ".set reorder\n"
);

// Save 06 reached 0x0025B290 with a0=0x01BED340.  The first per-view count at
// a0+0x64 was 0x019DF2DC: a pointer, not a count.  The stock loop consequently
// walked hundreds of thousands of words into 0x1Fxxxxxx and eventually made
// PCSX2 assert on an empty VIF1 FIFO.  This call-site guard lets ordinary work
// lists through.  The stock function clears four pointer banks, so every count
// is bounded by its corresponding bank capacity, not just the first one.
__asm__(
    ".set noreorder\n"
    ".set noat\n"
    ".globl render_packet_dispatch\n"
    ".ent render_packet_dispatch\n"
    "render_packet_dispatch:\n"
    "move  $3, $0\n"
    "andi  $2, $4, 3\n"
    "bnez  $2, 9f\n"
    "nop\n"
    "lui   $2, 0x0010\n"
    "sltu  $2, $4, $2\n"
    "bnez  $2, 9f\n"
    "nop\n"
    "lui   $2, 0x0200\n"
    "sltu  $2, $4, $2\n"
    "beqz  $2, 9f\n"
    "nop\n"
    "sltiu $2, $5, 2\n"
    "beqz  $2, 9f\n"
    "nop\n"
    "sll   $2, $5, 2\n"
    "addu  $27, $4, $2\n"
    "lw    $3, 100($27)\n"
    "sltiu $2, $3, 0x191\n"
    "beqz  $2, 5f\n"
    "nop\n"
    "lw    $3, 108($27)\n"
    "sltiu $2, $3, 0x65\n"
    "beqz  $2, 6f\n"
    "nop\n"
    "lw    $3, 116($27)\n"
    "sltiu $2, $3, 0xBB9\n"
    "beqz  $2, 7f\n"
    "nop\n"
    "lw    $3, 124($27)\n"
    "sltiu $2, $3, 0x65\n"
    "beqz  $2, 8f\n"
    "nop\n"
    "lui   $2, 0x0025\n"
    "ori   $2, $2, 0xA5A0\n"
    "jr    $2\n"
    "nop\n"
    "5:\n"
    "addiu $2, $0, 0x64\n"
    "addiu $27, $0, 0x190\n"
    "b     4f\n"
    "nop\n"
    "6:\n"
    "addiu $2, $0, 0x6C\n"
    "addiu $27, $0, 0x64\n"
    "b     4f\n"
    "nop\n"
    "7:\n"
    "addiu $2, $0, 0x74\n"
    "addiu $27, $0, 0xBB8\n"
    "b     4f\n"
    "nop\n"
    "8:\n"
    "addiu $2, $0, 0x7C\n"
    "addiu $27, $0, 0x64\n"
    "b     4f\n"
    "nop\n"
    "9:\n"
    "move  $2, $0\n"
    "move  $27, $0\n"
    "4:\n"
    "lui   $26, %hi(g_prop_guard)\n"
    "addiu $26, $26, %lo(g_prop_guard)\n"
    "sw    $2,  92($26)\n"
    "sw    $27, 96($26)\n"
    "lw    $27, 64($26)\n"
    "addiu $27, $27, 1\n"
    "sw    $27, 64($26)\n"
    "sw    $4,  68($26)\n"
    "sw    $5,  72($26)\n"
    "sw    $3,  76($26)\n"
    "sw    $31, 80($26)\n"
    "move  $2, $0\n"
    "jr    $31\n"
    "nop\n"
    ".end render_packet_dispatch\n"
    ".set at\n"
    ".set reorder\n"
);

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    volatile mc3_u32 *const site = (volatile mc3_u32 *)CALL_SITE;
    volatile mc3_u32 *const packet_site = (volatile mc3_u32 *)PACKET_SITE;
    volatile mc3_u32 *const shader_site_v0 = (volatile mc3_u32 *)SHADER_SITE_V0;
    volatile mc3_u32 *const shader_site_v1 = (volatile mc3_u32 *)SHADER_SITE_V1;
    volatile mc3_u32 *const state_site = (volatile mc3_u32 *)STATE_SITE;
    volatile mc3_u32 *const state_field_site =
        (volatile mc3_u32 *)STATE_FIELD_SITE;
    volatile mc3_u32 *const context_post_read =
        (volatile mc3_u32 *)CONTEXT_POST_READ;
    volatile mc3_u32 *const group_ctor_entry =
        (volatile mc3_u32 *)GROUP_CTOR_ENTRY;
    const mc3_u32 original = *site;
    const mc3_u32 packet_original = *packet_site;
    const mc3_u32 shader_original_v0 = *shader_site_v0;
    const mc3_u32 shader_original_v1 = *shader_site_v1;
    const mc3_u32 state_original_load = *state_site;
    const mc3_u32 state_original_field = *state_field_site;
    const mc3_u32 post_read_original0 = context_post_read[0];
    const mc3_u32 post_read_original1 = context_post_read[1];
    const mc3_u32 group_ctor_original0 = group_ctor_entry[0];
    const mc3_u32 group_ctor_original1 = group_ctor_entry[1];

    g_prop_guard.installed = 1u;
    g_prop_guard.patched = 0u;
    g_prop_guard.original_word = original;
    g_prop_guard.hits = 0u;
    g_prop_guard.refreshed = 0u;
    g_prop_guard.skipped = 0u;
    g_prop_guard.packet_hits = 0u;
    g_prop_guard.packet_original_word = packet_original;
    g_prop_guard.packet_patched = 0u;
    g_prop_guard.packet_list_offset = 0u;
    g_prop_guard.packet_limit = 0u;
    g_prop_guard.shader_hits = 0u;
    g_prop_guard.shader_original_v0 = shader_original_v0;
    g_prop_guard.shader_original_v1 = shader_original_v1;
    g_prop_guard.shader_patched = 0u;
    g_prop_guard.state_hits = 0u;
    g_prop_guard.state_skipped = 0u;
    g_prop_guard.state_original_load = state_original_load;
    g_prop_guard.state_original_field = state_original_field;
    g_prop_guard.state_patched = 0u;
    g_prop_guard.shader_draw_caller_ra = 0u;
    g_prop_guard.shader_draw_parent_ra = 0u;
    g_prop_guard.shader_draw_saved_s0 = 0u;
    g_prop_guard.shader_draw_saved_s1 = 0u;
    g_prop_guard.shader_draw_saved_s2 = 0u;
    g_prop_guard.shader_draw_saved_s3 = 0u;
    g_prop_guard.shader_draw_saved_s4 = 0u;
    g_prop_guard.shader_draw_saved_s5 = 0u;
    g_prop_guard.shader_draw_saved_s6 = 0u;
    g_prop_guard.shader_source_kind = 0u;
    g_prop_guard.shader_source_owner = 0u;
    g_prop_guard.shader_source_selector = 0u;
    g_prop_guard.shader_source_base = 0u;
    g_prop_guard.shader_source_expected = 0u;
    g_prop_guard.shader_group_vtable = 0u;
    g_prop_guard.shader_group_slots = 0u;
    g_prop_guard.shader_group_slot4 = 0u;
    g_prop_guard.pagein_calls = 0u;
    g_prop_guard.pagein_target_hits = 0u;
    g_prop_guard.pagein_group = 0u;
    g_prop_guard.pagein_loader = 0u;
    g_prop_guard.pagein_before_vtable = 0u;
    g_prop_guard.pagein_before_slots = 0u;
    g_prop_guard.pagein_before_counts = 0u;
    g_prop_guard.pagein_before_list = 0u;
    g_prop_guard.pagein_before_slot0 = 0u;
    g_prop_guard.pagein_before_slot4 = 0u;
    g_prop_guard.pagein_after_vtable = 0u;
    g_prop_guard.pagein_after_slots = 0u;
    g_prop_guard.pagein_after_counts = 0u;
    g_prop_guard.pagein_after_list = 0u;
    g_prop_guard.pagein_after_slot0 = 0u;
    g_prop_guard.pagein_after_slot4 = 0u;
    g_prop_guard.constructor_calls = 0u;
    g_prop_guard.constructor_target_hits = 0u;
    g_prop_guard.constructor_site = 0u;
    g_prop_guard.constructor_group = 0u;
    g_prop_guard.constructor_loader = 0u;
    g_prop_guard.constructor_delta = 0u;
    g_prop_guard.constructor_before_slots = 0u;
    g_prop_guard.constructor_after_slots = 0u;
    g_prop_guard.constructor_before_counts = 0u;
    g_prop_guard.constructor_after_counts = 0u;
    g_prop_guard.constructor_before_list = 0u;
    g_prop_guard.constructor_after_list = 0u;
    g_prop_guard.constructor_before_slot0 = 0u;
    g_prop_guard.constructor_before_slot4 = 0u;
    g_prop_guard.constructor_after_slot0 = 0u;
    g_prop_guard.constructor_after_slot4 = 0u;
    g_prop_guard.context_calls = 0u;
    g_prop_guard.context_target_hits = 0u;
    g_prop_guard.context_site = 0u;
    g_prop_guard.context_addr = 0u;
    g_prop_guard.context_loader = 0u;
    g_prop_guard.context_delta = 0u;
    g_prop_guard.context_before_b8 = 0u;
    g_prop_guard.context_predicted_group = 0u;
    g_prop_guard.context_after_b8 = 0u;
    g_prop_guard.context_manager = 0u;
    g_prop_guard.context_after_vtable = 0u;
    g_prop_guard.context_after_slots = 0u;
    g_prop_guard.context_after_counts = 0u;
    g_prop_guard.context_after_list = 0u;
    g_prop_guard.entry_calls = 0u;
    g_prop_guard.entry_target_hits = 0u;
    g_prop_guard.entry_original0 = group_ctor_original0;
    g_prop_guard.entry_original1 = group_ctor_original1;
    g_prop_guard.entry_patched = 0u;
    g_prop_guard.post_read_calls = 0u;
    g_prop_guard.post_read_target_hits = 0u;
    g_prop_guard.post_read_original0 = post_read_original0;
    g_prop_guard.post_read_original1 = post_read_original1;
    g_prop_guard.post_read_patched = 0u;

    if (original == (mc3_u32)ORIGINAL_JALR) {
        const mc3_u32 target = (mc3_u32)&prop_virtual_dispatch;
        *site = 0x0C000000u | ((target >> 2) & 0x03FFFFFFu);
        g_prop_guard.patched = 1u;
    }

    if (packet_original == (mc3_u32)ORIGINAL_PACKET_JAL) {
        const mc3_u32 target = (mc3_u32)&render_packet_dispatch;
        *packet_site = 0x0C000000u | ((target >> 2) & 0x03FFFFFFu);
        g_prop_guard.packet_patched = 1u;
    }

    if (shader_original_v0 == (mc3_u32)ORIGINAL_SHADER_V0) {
        const mc3_u32 target = (mc3_u32)&shader_virtual_dispatch_v0;
        *shader_site_v0 = 0x0C000000u | ((target >> 2) & 0x03FFFFFFu);
        g_prop_guard.shader_patched |= 1u;
    }

    if (shader_original_v1 == (mc3_u32)ORIGINAL_SHADER_V1) {
        const mc3_u32 target = (mc3_u32)&shader_virtual_dispatch_v1;
        *shader_site_v1 = 0x0C000000u | ((target >> 2) & 0x03FFFFFFu);
        g_prop_guard.shader_patched |= 2u;
    }

    if (state_original_load == (mc3_u32)ORIGINAL_STATE_LOAD &&
        state_original_field == (mc3_u32)ORIGINAL_STATE_FIELD) {
        const mc3_u32 target = (mc3_u32)&renderer_state_dispatch;
        *state_field_site = 0u;
        *state_site = 0x0C000000u | ((target >> 2) & 0x03FFFFFFu);
        g_prop_guard.state_patched = 1u;
    }

    if (group_ctor_original0 == (mc3_u32)ORIGINAL_GROUP_CTOR_0 &&
        group_ctor_original1 == (mc3_u32)ORIGINAL_GROUP_CTOR_1) {
        const mc3_u32 target = (mc3_u32)&group_constructor_entry_dispatch;
        group_ctor_entry[1] = 0u;
        group_ctor_entry[0] =
            0x08000000u | ((target >> 2) & 0x03FFFFFFu);
        g_prop_guard.entry_patched = 1u;
    }

    if (!g_prop_guard.patched && !g_prop_guard.packet_patched &&
        !g_prop_guard.shader_patched && !g_prop_guard.state_patched &&
        !g_prop_guard.entry_patched && !g_prop_guard.post_read_patched)
        return;

    MC3_CALL1(void, FLUSH_CACHE, int)(0);
    MC3_CALL1(void, FLUSH_CACHE, int)(2);
}
