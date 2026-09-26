// CTT11: distance-aware texture touching plus per-draw texture fallback probe.
//
// CTT9 fixed duplicate TouchShaders calls, but forced f12=0.0 for every model.
// CTT10 reads the LOD already chosen by the retail city renderer at 0x006BE810,
// reconstructs a float which maps back to that LOD, and passes it in f12.

#include "../../payload/mc3_mod.h"

#define MC3_LOG_OWNER 0x31505442u // bytes "BTP1"
#include "../../payload/mc3_log.h"

enum {
    RMC_MODEL_TOUCH_SHADERS = 0x002A8AF0u,
    RMC_MODEL_DRAW          = 0x002A9918u,
    RMC_MODEL_DRAW_CPV      = 0x002A9A88u,
    CITY_MODEL_SELECT       = 0x0024C9C0u,
    CITY_RENDER_DISPATCH    = 0x0024C380u,
    INST_RENDER_DISPATCH    = 0x00257008u,
    CULLABLE_RENDER         = 0x0024A860u,
    OBSTACLE_DEBUG_DRAW     = 0x004FFAC0u,
    CURRENT_CITY            = 0x00615B40u,
    CURRENT_TEXTURE_LOD     = 0x006BE810u,
    GLOBAL_MIP_SCALE        = 0x00615FE8u,
    PAGEFILE_INSTANCES      = 0x00714CC0u,
    PAGE_REQUEST_COUNT      = 0x00714358u,
    TEXTURE_VRAM_TOP        = 0x0070FBE8u,
    TEXTURE_VRAM_CURRENT    = 0x0070FBECu,
    TARGET_PAGEFILE_ID      = 123u,
    TARGET_PAGE_STRIDE      = 0x11000u,
    GENERATED_GROUP_COUNT   = 18u,
    CACHE_SIZE              = 1024u,
    CACHE_MASK              = CACHE_SIZE - 1u,
    TRACE_MAGIC             = 0x30315443u, // bytes "CT10"
    TEXTURE_PROXY_CURRENT   = 0x0070FA48u,
    PROBE_MAGIC             = 0x31505442u, // bytes "BTP1"
    PROBE_SIZE              = 512u,
    PROBE_MASK              = PROBE_SIZE - 1u,
    PROBE_CAPTURE_FRAMES    = 1800u,
};

struct city_texture_touch_report_v10 {
    mc3_u32 magic, version, current_city, current_descs, active_checks;
    mc3_u32 dispatch_calls, selector_calls, selector_nonzero;
    mc3_u32 draw_calls, draw_cpv_calls, touch_calls, touch_ready;
    mc3_u32 frame_calls, frame_epoch, touch_requests, touch_skipped;
    mc3_u32 cache_collisions, cache_full;
    mc3_u32 last_dispatch_kind, last_dispatch_object, last_dispatch_owner;
    mc3_u32 last_dispatch_pass, last_dispatch_arg;
    mc3_u32 last_selector_container, last_selector_index, last_selector_lod;
    mc3_u32 last_selector_result;
    mc3_u32 last_cpv_model, last_cpv_group, last_cpv_arg5, last_cpv_index;
    mc3_u32 last_cpv_slot_count, last_cpv_roots, last_cpv_root;
    mc3_u32 max_cpv_index, oob_calls, clamp_calls;
    mc3_u32 first_oob_model, first_oob_group, first_oob_arg5;
    mc3_u32 first_oob_index, first_oob_slot_count, first_oob_roots;
    mc3_u32 first_oob_root;

    mc3_u32 current_texture_lod, last_touch_lod, last_touch_distance_bits;
    mc3_u32 lod0_frame, lod1_frame, lod2_frame, lod3plus_frame;
    mc3_u32 lod0_total, lod1_total, lod2_total, lod3plus_total;
    mc3_u32 touch_lod_upgrades;

    mc3_u32 pagefile, page_count, page_stride, page_state_table;
    mc3_u32 pages_resident, pages_pending, pages_unrequested, page_requests;
    mc3_u32 vram_top_blocks, vram_current_blocks, vram_free_blocks;
    mc3_u32 overlay_calls, overlay_draws, overlay_font, overlay_font_attempts;
};

struct touch_cache_entry {
    mc3_u32 model, group, epoch, finest_lod;
};

enum probe_bind_status {
    PROBE_BIND_NONE  = 0u,
    PROBE_BIND_FULL  = 1u,
    PROBE_BIND_PROXY = 2u,
    PROBE_BIND_BLACK = 3u,
};

struct black_probe_report_v11 {
    mc3_u32 magic, version, bytes, capacity, capture_frames;
    mc3_u32 start_frame, last_frame, active, current_city;
    mc3_u32 model_scans, texture_checks, invalid_models, invalid_groups;
    mc3_u32 invalid_materials, nonbasic_shaders, no_basic_scans;
    mc3_u32 unique_textures, table_full;
    mc3_u32 frame_full, frame_proxy, frame_black;
    mc3_u32 frame_pending, frame_unrequested;
    mc3_u32 total_full, total_proxy, total_black;
    mc3_u32 total_pending, total_unrequested, recovered;
    mc3_u32 last_texture, last_model, last_status, last_lod, last_page_word;
    mc3_u32 proxy_table, proxy_count, first_no_basic_model;
};

struct black_probe_entry_v11 {
    mc3_u32 texture, proxy_id, first_model, last_model;
    mc3_u32 first_frame, last_frame, first_page_word, last_page_word;
    mc3_u32 full_checks, proxy_checks, black_checks, recovered_frame;
    mc3_u32 last_status, last_page_state;
};

struct city_black_probe_block_v11 {
    black_probe_report_v11 report;
    black_probe_entry_v11 entries[PROBE_SIZE];
};

struct txt_cursor {
    mc3_u32 flags;
    int cursor_x, cursor_y, clip_top, clip_right, clip_left, clip_bottom;
    int first_character, last_character, line_height;
    int measured_width, measured_height;
    mc3_u32 scale_x_bits, scale_y_bits, colour;
};

struct text_layout { mc3_u32 x, y, sx, sy; };

extern "C" {
volatile city_texture_touch_report_v10 g_city_texture_touch_v10 = {
    TRACE_MAGIC, 10u
};
volatile city_black_probe_block_v11 g_city_black_probe_v11 = {
    { PROBE_MAGIC, 11u, sizeof(city_black_probe_block_v11), PROBE_SIZE,
      PROBE_CAPTURE_FRAMES }
};
mc3_u32 touch_shaders_lod_v10(mc3_u32 model, mc3_u32 group,
                              mc3_u32 distance_bits);
void ctt10_draw_text(mc3_u32 font, const mc3_u16 *text,
                     txt_cursor *cursor, const text_layout *layout);
}

static touch_cache_entry g_touch_cache[CACHE_SIZE];

static inline int sane_ptr(mc3_u32 value);

static __attribute__((noinline)) volatile city_black_probe_block_v11 *
probe_block_ptr()
{
    return &g_city_black_probe_v11;
}

static __attribute__((noinline)) void btp_put(char value)
{
    *(volatile mc3_u8 *)0x1000F180u = (mc3_u8)value;
}

static void btp_hex8(mc3_u32 value)
{
    for (int shift = 28; shift >= 0; shift -= 4) {
        const mc3_u32 digit = (value >> shift) & 15u;
        btp_put((char)(digit < 10u ? '0' + digit : 'A' + digit - 10u));
    }
}

static void btp_space_hex(mc3_u32 value)
{
    btp_put(' ');
    btp_hex8(value);
}

static void btp_sio_sample()
{
    volatile black_probe_report_v11 &r = probe_block_ptr()->report;
    if (!r.start_frame || r.last_frame < r.start_frame ||
        (r.last_frame - r.start_frame) % 300u != 299u)
        return;
    btp_put('B'); btp_put('T'); btp_put('P'); btp_put('1');
    btp_space_hex(r.last_frame);
    btp_space_hex(r.frame_full);
    btp_space_hex(r.frame_proxy);
    btp_space_hex(r.frame_black);
    btp_space_hex(r.frame_pending);
    btp_space_hex(r.frame_unrequested);
    btp_space_hex(r.unique_textures);
    btp_space_hex(r.recovered);
    btp_space_hex(r.invalid_materials + r.invalid_groups);
    btp_put('\n');
}

static mc3_u32 probe_hash(mc3_u32 texture)
{
    return ((texture >> 4) * 0x9E3779B9u) & PROBE_MASK;
}

static mc3_u32 probe_page_state(mc3_u32 page_word)
{
    const mc3_u32 file_id = page_word >> 23;
    const mc3_u32 page = page_word & 0x007FFFFFu;
    if (file_id != TARGET_PAGEFILE_ID) return 0xFFFFFFFEu;
    const mc3_u32 pagefile = *(volatile mc3_u32 *)(
        PAGEFILE_INSTANCES + 4u * file_id);
    if (!sane_ptr(pagefile)) return 0xFFFFFFFDu;
    const mc3_u32 count = *(volatile mc3_u32 *)(pagefile + 0u);
    const mc3_u32 table = *(volatile mc3_u32 *)(pagefile + 12u);
    if (page >= count || !sane_ptr(table)) return 0xFFFFFFFCu;
    return *(volatile mc3_u32 *)(table + 4u * page);
}

static int probe_capture_active()
{
    volatile black_probe_report_v11 &r = probe_block_ptr()->report;
    const mc3_u32 frame = g_city_texture_touch_v10.frame_epoch;
    if (!r.start_frame) r.start_frame = frame ? frame : 1u;
    r.last_frame = frame;
    r.current_city = g_city_texture_touch_v10.current_city;
    r.active = (frame - r.start_frame) < r.capture_frames;
    return r.active != 0u;
}

static black_probe_entry_v11 *probe_find_entry(mc3_u32 texture)
{
    volatile city_black_probe_block_v11 *block = probe_block_ptr();
    volatile black_probe_report_v11 &r = block->report;
    mc3_u32 slot = probe_hash(texture);
    for (mc3_u32 probe = 0u; probe < PROBE_SIZE; ++probe) {
        volatile black_probe_entry_v11 &entry = block->entries[slot];
        if (!entry.texture) {
            entry.texture = texture;
            ++r.unique_textures;
            return (black_probe_entry_v11 *)&entry;
        }
        if (entry.texture == texture)
            return (black_probe_entry_v11 *)&entry;
        slot = (slot + 1u) & PROBE_MASK;
    }
    ++r.table_full;
    return 0;
}

static void probe_texture(mc3_u32 texture, mc3_u32 model, mc3_u32 lod)
{
    volatile black_probe_report_v11 &r = probe_block_ptr()->report;
    black_probe_entry_v11 *raw = probe_find_entry(texture);
    if (!raw) return;
    volatile black_probe_entry_v11 &entry = *raw;
    const mc3_u32 frame = g_city_texture_touch_v10.frame_epoch;
    entry.last_model = model;
    if (entry.last_frame == frame) return; // one sample per visible texture/frame

    const mc3_u32 mip_count = *(volatile mc3_u8 *)(texture + 0x86u);
    if (!mip_count || mip_count > 16u) {
        ++r.invalid_materials;
        return;
    }
    const mc3_u32 ref = texture + 0x48u + 12u * (mip_count - 1u);
    const mc3_u32 resident = *(volatile mc3_u32 *)(ref + 0u);
    const mc3_u32 page_word = *(volatile mc3_u32 *)(ref + 8u);
    const mc3_u32 proxy_id =
        (*(volatile mc3_u32 *)(texture + 0x88u) >> 21) & 0x7FFu;
    const mc3_u32 proxy_table = *(volatile mc3_u32 *)TEXTURE_PROXY_CURRENT;
    const mc3_u32 proxy_count = sane_ptr(proxy_table)
        ? *(volatile mc3_u16 *)(proxy_table + 0u) : 0u;
    r.proxy_table = proxy_table;
    r.proxy_count = proxy_count;

    mc3_u32 status = PROBE_BIND_FULL;
    if (!resident) {
        status = proxy_id && sane_ptr(proxy_table) && proxy_id <= proxy_count
            ? PROBE_BIND_PROXY : PROBE_BIND_BLACK;
    }
    const mc3_u32 page_state = resident ? 0xFFFFFFFFu
                                         : probe_page_state(page_word);

    if (!entry.first_frame) {
        entry.proxy_id = proxy_id;
        entry.first_model = model;
        entry.first_frame = frame;
        entry.first_page_word = page_word;
    }
    if (entry.last_status && entry.last_status != PROBE_BIND_FULL &&
        status == PROBE_BIND_FULL) {
        ++r.recovered;
        if (!entry.recovered_frame) entry.recovered_frame = frame;
        MC3_LOG4("recovered tex/model/lod/page", texture, model, lod, page_word);
    }
    if (status != entry.last_status) {
        const mc3_u32 lod_proxy = (lod << 16) | proxy_id;
        if (status == PROBE_BIND_PROXY)
            MC3_LOG4("proxy tex/model/lodid/page", texture, model,
                     lod_proxy, page_word);
        else if (status == PROBE_BIND_BLACK)
            MC3_LOG4("BLACK tex/model/lodid/page", texture, model,
                     lod_proxy, page_word);
    }

    ++r.texture_checks;
    if (status == PROBE_BIND_FULL) {
        ++r.frame_full; ++r.total_full; ++entry.full_checks;
    } else if (status == PROBE_BIND_PROXY) {
        ++r.frame_proxy; ++r.total_proxy; ++entry.proxy_checks;
    } else {
        ++r.frame_black; ++r.total_black; ++entry.black_checks;
    }
    if (!resident && page_state == 0u) {
        ++r.frame_unrequested; ++r.total_unrequested;
    } else if (!resident && page_state != 0xFFFFFFFFu &&
               page_state != 0xFFFFFFFEu && page_state != 0xFFFFFFFDu &&
               page_state != 0xFFFFFFFCu && (page_state & 1u)) {
        ++r.frame_pending; ++r.total_pending;
    }
    entry.last_frame = frame;
    entry.last_page_word = page_word;
    entry.last_status = status;
    entry.last_page_state = page_state;
    r.last_texture = texture;
    r.last_model = model;
    r.last_status = status;
    r.last_lod = lod;
    r.last_page_word = page_word;
}

static void probe_model_textures(mc3_u32 model, mc3_u32 group, mc3_u32 lod)
{
    if (!probe_capture_active()) return;
    volatile black_probe_report_v11 &r = probe_block_ptr()->report;
    ++r.model_scans;
    if (!sane_ptr(model)) { ++r.invalid_models; return; }
    if (!sane_ptr(group)) { ++r.invalid_groups; return; }
    const mc3_u32 group_count = *(volatile mc3_u16 *)(model + 8u);
    const mc3_u32 materials = *(volatile mc3_u32 *)(model + 12u);
    const mc3_u32 slots = *(volatile mc3_u32 *)(group + 4u);
    const mc3_u32 slot_count = *(volatile mc3_u16 *)(group + 8u);
    if (!group_count || group_count > 256u || !sane_ptr(materials) ||
        !slot_count || slot_count > 2048u || !sane_ptr(slots)) {
        ++r.invalid_groups;
        return;
    }
    mc3_u32 basic = 0u;
    for (mc3_u32 i = 0u; i < group_count; ++i) {
        const mc3_u32 material = *(volatile mc3_u16 *)(materials + 2u * i);
        if (material >= slot_count) { ++r.invalid_materials; continue; }
        const mc3_u32 shader = *(volatile mc3_u32 *)(slots + 4u * material);
        if (!sane_ptr(shader)) { ++r.invalid_materials; continue; }
        const mc3_u32 type = *(volatile mc3_u32 *)(shader + 4u) & 0x7Fu;
        if (type != 0u) { ++r.nonbasic_shaders; continue; }
        const mc3_u32 texture = *(volatile mc3_u32 *)(shader + 8u);
        if (!sane_ptr(texture)) { ++r.invalid_materials; continue; }
        ++basic;
        probe_texture(texture, model, lod);
    }
    if (!basic) {
        ++r.no_basic_scans;
        if (!r.first_no_basic_model) r.first_no_basic_model = model;
    }
}

// a2 carries the IEEE-754 bits; the game ABI expects this float in f12.
__asm__(
    ".set noreorder\n"
    ".globl touch_shaders_lod_v10\n"
    ".ent touch_shaders_lod_v10\n"
    "touch_shaders_lod_v10:\n"
    "mtc1  $6, $f12\n"
    "lui   $2, 0x002A\n"
    "ori   $2, $2, 0x8AF0\n"
    "jr    $2\n"
    "nop\n"
    ".end touch_shaders_lod_v10\n"
    ".set reorder\n"
);

// txtFontTex::Draw uses the game's mixed float ABI (f12-f15).
__asm__(
    ".set noreorder\n.text\n.align 3\n"
    ".globl ctt10_draw_text\n"
    "ctt10_draw_text:\n"
    "addiu $sp,$sp,-16\n"
    "sd $ra,0($sp)\n"
    "lwc1 $f12,0($a3)\n"
    "lwc1 $f13,4($a3)\n"
    "lwc1 $f14,8($a3)\n"
    "lwc1 $f15,12($a3)\n"
    "move $a3,$zero\nmove $8,$zero\n"
    "lui $t9,0x002b\nori $t9,$t9,0xc8f0\n"
    "jalr $t9\nnop\n"
    "ld $ra,0($sp)\n"
    "jr $ra\naddiu $sp,$sp,16\n"
    ".set reorder\n"
);

static inline int sane_ptr(mc3_u32 value)
{
    return value >= 0x00100000u && value < 0x02000000u && !(value & 3u);
}

static int generated_city_active()
{
    const mc3_u32 city = *(volatile mc3_u32 *)CURRENT_CITY;
    if (!sane_ptr(city) ||
        *(volatile mc3_u16 *)(city + 8u) != GENERATED_GROUP_COUNT)
        return 0;
    g_city_texture_touch_v10.current_city = city;
    g_city_texture_touch_v10.current_descs =
        *(volatile mc3_u32 *)(city + 12u);
    ++g_city_texture_touch_v10.active_checks;
    return 1;
}

static mc3_u32 touch_hash(mc3_u32 model, mc3_u32 group)
{
    return ((model >> 4) ^ ((group >> 4) * 0x9E3779B9u)) & CACHE_MASK;
}

static mc3_u32 float_bits(float value)
{
    union { float f; mc3_u32 u; } convert;
    convert.f = value;
    return convert.u;
}

// GetTextureLod uses the exponent of distance*sm_GlobalMipScale.  Powers of
// two stay clear of its half-step boundary: 4/scale -> LOD1, 8/scale -> LOD2.
static mc3_u32 distance_bits_for_lod(mc3_u32 lod)
{
    if (!lod) return 0u;
    if (lod > 15u) lod = 15u;
    const float scale = *(volatile float *)GLOBAL_MIP_SCALE;
    if (!(scale > 0.000001f && scale < 1000000.0f)) return 0u;
    float target = 4.0f;
    for (mc3_u32 level = 1u; level < lod; ++level) target *= 2.0f;
    return float_bits(target / scale);
}

static void count_selected_lod(mc3_u32 lod)
{
    g_city_texture_touch_v10.current_texture_lod = lod;
    if (lod == 0u) {
        ++g_city_texture_touch_v10.lod0_frame;
        ++g_city_texture_touch_v10.lod0_total;
    } else if (lod == 1u) {
        ++g_city_texture_touch_v10.lod1_frame;
        ++g_city_texture_touch_v10.lod1_total;
    } else if (lod == 2u) {
        ++g_city_texture_touch_v10.lod2_frame;
        ++g_city_texture_touch_v10.lod2_total;
    } else {
        ++g_city_texture_touch_v10.lod3plus_frame;
        ++g_city_texture_touch_v10.lod3plus_total;
    }
}

static void call_touch(mc3_u32 model, mc3_u32 group, mc3_u32 lod)
{
    const mc3_u32 bits = distance_bits_for_lod(lod);
    g_city_texture_touch_v10.last_touch_lod = lod;
    g_city_texture_touch_v10.last_touch_distance_bits = bits;
    ++g_city_texture_touch_v10.touch_calls;
    if (touch_shaders_lod_v10(model, group, bits))
        ++g_city_texture_touch_v10.touch_ready;
}

static void touch_model_once(mc3_u32 model, mc3_u32 group)
{
    if (!sane_ptr(model) || !sane_ptr(group)) return;
    mc3_u32 lod = *(volatile mc3_u32 *)CURRENT_TEXTURE_LOD;
    if (lod > 15u) lod = 15u;
    count_selected_lod(lod);
    ++g_city_texture_touch_v10.touch_requests;

    const mc3_u32 epoch = g_city_texture_touch_v10.frame_epoch;
    mc3_u32 slot = touch_hash(model, group);
    for (mc3_u32 probe = 0u; probe < CACHE_SIZE; ++probe) {
        touch_cache_entry &entry = g_touch_cache[slot];
        if (entry.epoch != epoch) {
            entry.model = model;
            entry.group = group;
            entry.epoch = epoch;
            entry.finest_lod = lod;
            probe_model_textures(model, group, lod);
            call_touch(model, group, lod);
            return;
        }
        if (entry.model == model && entry.group == group) {
            if (lod < entry.finest_lod) {
                entry.finest_lod = lod;
                ++g_city_texture_touch_v10.touch_lod_upgrades;
                probe_model_textures(model, group, lod);
                call_touch(model, group, lod);
            } else {
                ++g_city_texture_touch_v10.touch_skipped;
            }
            return;
        }
        ++g_city_texture_touch_v10.cache_collisions;
        slot = (slot + 1u) & CACHE_MASK;
    }
    ++g_city_texture_touch_v10.cache_full;
    probe_model_textures(model, group, lod);
    call_touch(model, group, lod);
}

static mc3_u32 record_cpv_and_guard(mc3_u32 model, mc3_u32 group,
                                    mc3_u32 arg5, mc3_u32 index)
{
    mc3_u32 slots = 0u, roots = 0u, root = 0u;
    if (sane_ptr(model)) {
        slots = *(volatile mc3_u16 *)(model + 10u);
        roots = *(volatile mc3_u32 *)(model + 20u);
        if (sane_ptr(roots) && index < slots)
            root = *(volatile mc3_u32 *)(roots + 4u * index);
    }
    g_city_texture_touch_v10.last_cpv_model = model;
    g_city_texture_touch_v10.last_cpv_group = group;
    g_city_texture_touch_v10.last_cpv_arg5 = arg5;
    g_city_texture_touch_v10.last_cpv_index = index;
    g_city_texture_touch_v10.last_cpv_slot_count = slots;
    g_city_texture_touch_v10.last_cpv_roots = roots;
    g_city_texture_touch_v10.last_cpv_root = root;
    if (index > g_city_texture_touch_v10.max_cpv_index)
        g_city_texture_touch_v10.max_cpv_index = index;
    if (!slots || index >= slots || !sane_ptr(roots) || !sane_ptr(root)) {
        ++g_city_texture_touch_v10.oob_calls;
        if (!g_city_texture_touch_v10.first_oob_model) {
            g_city_texture_touch_v10.first_oob_model = model;
            g_city_texture_touch_v10.first_oob_group = group;
            g_city_texture_touch_v10.first_oob_arg5 = arg5;
            g_city_texture_touch_v10.first_oob_index = index;
            g_city_texture_touch_v10.first_oob_slot_count = slots;
            g_city_texture_touch_v10.first_oob_roots = roots;
            g_city_texture_touch_v10.first_oob_root = root;
        }
        if (slots && sane_ptr(roots) && sane_ptr(*(volatile mc3_u32 *)roots)) {
            ++g_city_texture_touch_v10.clamp_calls;
            return 0u;
        }
    }
    return index;
}

static void sample_streaming_state()
{
    const mc3_u32 pagefile = *(volatile mc3_u32 *)(
        PAGEFILE_INSTANCES + 4u * TARGET_PAGEFILE_ID);
    g_city_texture_touch_v10.pagefile = pagefile;
    g_city_texture_touch_v10.pages_resident = 0u;
    g_city_texture_touch_v10.pages_pending = 0u;
    g_city_texture_touch_v10.pages_unrequested = 0u;
    if (sane_ptr(pagefile)) {
        const mc3_u32 count = *(volatile mc3_u32 *)(pagefile + 0u);
        const mc3_u32 stride = *(volatile mc3_u32 *)(pagefile + 4u);
        const mc3_u32 table = *(volatile mc3_u32 *)(pagefile + 12u);
        g_city_texture_touch_v10.page_count = count;
        g_city_texture_touch_v10.page_stride = stride;
        g_city_texture_touch_v10.page_state_table = table;
        if (count <= 2048u && stride == TARGET_PAGE_STRIDE && sane_ptr(table)) {
            for (mc3_u32 i = 0u; i < count; ++i) {
                const mc3_u32 state = *(volatile mc3_u32 *)(table + 4u * i);
                if (!state) ++g_city_texture_touch_v10.pages_unrequested;
                else if (state & 1u) ++g_city_texture_touch_v10.pages_pending;
                else ++g_city_texture_touch_v10.pages_resident;
            }
        }
    }
    g_city_texture_touch_v10.page_requests =
        *(volatile mc3_u32 *)PAGE_REQUEST_COUNT;
    const mc3_u32 top = *(volatile mc3_u32 *)TEXTURE_VRAM_TOP;
    const mc3_u32 current = *(volatile mc3_u32 *)TEXTURE_VRAM_CURRENT;
    g_city_texture_touch_v10.vram_top_blocks = top;
    g_city_texture_touch_v10.vram_current_blocks = current;
    g_city_texture_touch_v10.vram_free_blocks = top >= current ? top-current : 0u;
}

static __attribute__((noinline)) int append_ascii(
    mc3_u16 *out, int pos, int cap, const char *text)
{
    while (*text && pos + 1 < cap) out[pos++] = (mc3_u8)*text++;
    out[pos] = 0;
    return pos;
}

static __attribute__((noinline)) int append_u32(
    mc3_u16 *out, int pos, int cap, mc3_u32 value)
{
    mc3_u16 reverse[10];
    int count = 0;
    do {
        reverse[count++] = (mc3_u16)('0' + value % 10u);
        value /= 10u;
    } while (value && count < 10);
    while (count && pos + 1 < cap) out[pos++] = reverse[--count];
    out[pos] = 0;
    return pos;
}

// Keep every rodata address behind its own function.  The relocatable-module
// packer intentionally rejects a single shared HI16 feeding multiple LO16s.
static __attribute__((noinline)) const char *text_lod() { return "CTT10 LOD 0:"; }
static __attribute__((noinline)) const char *text_lod1() { return " 1:"; }
static __attribute__((noinline)) const char *text_lod2() { return " 2:"; }
static __attribute__((noinline)) const char *text_lod3() { return " 3+:"; }
static __attribute__((noinline)) const char *text_ppf() { return "PPF123 resident:"; }
static __attribute__((noinline)) const char *text_slash() { return "/"; }
static __attribute__((noinline)) const char *text_pending() { return " pending:"; }
static __attribute__((noinline)) const char *text_queue() { return " queue:"; }
static __attribute__((noinline)) const char *text_vram() { return "VRAM free:"; }
static __attribute__((noinline)) const char *text_blocks() { return " blocks touch:"; }
static __attribute__((noinline)) const char *text_bind() { return "BIND full:"; }
static __attribute__((noinline)) const char *text_proxy() { return " proxy:"; }
static __attribute__((noinline)) const char *text_black() { return " BLACK:"; }
static __attribute__((noinline)) const char *text_recovered() { return " rec:"; }
static __attribute__((noinline)) const char *text_probe() { return "PROBE tex:"; }
static __attribute__((noinline)) const char *text_unreq() { return " unreq:"; }
static __attribute__((noinline)) const char *text_bad() { return " bad:"; }

static void clear_cursor(txt_cursor &cursor)
{
    volatile mc3_u32 *words = (volatile mc3_u32 *)&cursor;
    for (mc3_u32 i=0; i<sizeof(cursor)/4u; ++i) words[i]=0u;
    cursor.flags=1u;
    cursor.last_character=0x7FFFFFFF;
    cursor.scale_x_bits=cursor.scale_y_bits=0x3F800000u;
    cursor.colour=0xC0FFFFFFu;
}

typedef void (*asset_push_fn)(mc3_u32,const char *);
typedef void (*asset_pop_fn)(mc3_u32);

static mc3_u32 load_overlay_font()
{
    const mc3_u32 assets=*(volatile mc3_u32 *)0x006D557Cu;
    if (!sane_ptr(assets)) return 0u;
    const mc3_u32 vtable=*(volatile mc3_u32 *)assets;
    if (!sane_ptr(vtable)) return 0u;
    const mc3_u32 push=*(volatile mc3_u32 *)(vtable+0x10u);
    const mc3_u32 pop=*(volatile mc3_u32 *)(vtable+0x14u);
    if (!sane_ptr(push) || !sane_ptr(pop)) return 0u;
    char name[14];
    name[0]='s'; name[1]='p'; name[2]='a'; name[3]='c'; name[4]='e';
    name[5]='o'; name[6]='u'; name[7]='t'; name[8]='-'; name[9]='b';
    name[10]='o'; name[11]='l'; name[12]='d'; name[13]=0;
    const char *dir=MC3_CALL(const char *,0x004B72C8u)();
    ((asset_push_fn)push)(assets,dir);
    mc3_u32 font=MC3_CALL2(mc3_u32,0x002BB880u,const char *,int)(name,0);
    if (font) MC3_CALL2(void,0x002BDB70u,mc3_u32,int)(font,0);
    ((asset_pop_fn)pop)(assets);
    return font;
}

static void draw_line(mc3_u32 font, mc3_u16 *text, mc3_u32 y)
{
    txt_cursor cursor;
    clear_cursor(cursor);
    // txtFontTex::Draw centres each line on x; 220 keeps these diagnostic
    // lines inside the left-hand safe area without colliding with PCSX2 stats.
    text_layout layout={0x435C0000u,y,0x3EE66666u,0x3EE66666u};
    ctt10_draw_text(font,text,&cursor,&layout);
}

static void draw_overlay()
{
    if (!generated_city_active()) return;
    sample_streaming_state();
    mc3_u32 font=g_city_texture_touch_v10.overlay_font;
    if (!sane_ptr(font) &&
        (!g_city_texture_touch_v10.overlay_font_attempts ||
         !(g_city_texture_touch_v10.overlay_calls % 60u))) {
        ++g_city_texture_touch_v10.overlay_font_attempts;
        font=load_overlay_font();
        g_city_texture_touch_v10.overlay_font=font;
    }
    if (!sane_ptr(font)) return;
    const mc3_u32 old_view=*(volatile mc3_u32 *)0x0070E50Cu;
    const mc3_u32 ui_view=*(volatile mc3_u32 *)0x0070E508u;
    if (!sane_ptr(old_view) || !sane_ptr(ui_view)) return;
    MC3_CALL1(void,0x00527B50u,mc3_u32)(ui_view);

    mc3_u16 line[72];
    int p=0;
    p=append_ascii(line,p,72,text_lod());
    p=append_u32(line,p,72,g_city_texture_touch_v10.lod0_frame);
    p=append_ascii(line,p,72,text_lod1());
    p=append_u32(line,p,72,g_city_texture_touch_v10.lod1_frame);
    p=append_ascii(line,p,72,text_lod2());
    p=append_u32(line,p,72,g_city_texture_touch_v10.lod2_frame);
    p=append_ascii(line,p,72,text_lod3());
    append_u32(line,p,72,g_city_texture_touch_v10.lod3plus_frame);
    draw_line(font,line,0x42700000u); // 60, below the stock profiler line

    p=0;
    p=append_ascii(line,p,72,text_ppf());
    p=append_u32(line,p,72,g_city_texture_touch_v10.pages_resident);
    p=append_ascii(line,p,72,text_slash());
    p=append_u32(line,p,72,g_city_texture_touch_v10.page_count);
    p=append_ascii(line,p,72,text_pending());
    p=append_u32(line,p,72,g_city_texture_touch_v10.pages_pending);
    p=append_ascii(line,p,72,text_queue());
    append_u32(line,p,72,g_city_texture_touch_v10.page_requests);
    draw_line(font,line,0x42900000u); // 72

    p=0;
    p=append_ascii(line,p,72,text_vram());
    p=append_u32(line,p,72,g_city_texture_touch_v10.vram_free_blocks);
    p=append_ascii(line,p,72,text_slash());
    p=append_u32(line,p,72,g_city_texture_touch_v10.vram_top_blocks);
    p=append_ascii(line,p,72,text_blocks());
    append_u32(line,p,72,g_city_texture_touch_v10.touch_calls);
    draw_line(font,line,0x42A80000u); // 84

    volatile black_probe_report_v11 &probe = probe_block_ptr()->report;
    p=0;
    p=append_ascii(line,p,72,text_bind());
    p=append_u32(line,p,72,probe.frame_full);
    p=append_ascii(line,p,72,text_proxy());
    p=append_u32(line,p,72,probe.frame_proxy);
    p=append_ascii(line,p,72,text_black());
    p=append_u32(line,p,72,probe.frame_black);
    p=append_ascii(line,p,72,text_recovered());
    append_u32(line,p,72,probe.recovered);
    draw_line(font,line,0x42C00000u); // 96

    p=0;
    p=append_ascii(line,p,72,text_probe());
    p=append_u32(line,p,72,probe.unique_textures);
    p=append_ascii(line,p,72,text_pending());
    p=append_u32(line,p,72,probe.frame_pending);
    p=append_ascii(line,p,72,text_unreq());
    p=append_u32(line,p,72,probe.frame_unrequested);
    p=append_ascii(line,p,72,text_bad());
    append_u32(line,p,72,probe.invalid_materials + probe.invalid_groups);
    draw_line(font,line,0x42D80000u); // 108

    MC3_CALL1(void,0x00527B50u,mc3_u32)(old_view);
    ++g_city_texture_touch_v10.overlay_draws;
}

extern "C" void city_frame_v10()
{
    mc3_u32 epoch=g_city_texture_touch_v10.frame_epoch+1u;
    if (!epoch) epoch=1u;
    g_city_texture_touch_v10.frame_epoch=epoch;
    ++g_city_texture_touch_v10.frame_calls;
    g_city_texture_touch_v10.lod0_frame=0u;
    g_city_texture_touch_v10.lod1_frame=0u;
    g_city_texture_touch_v10.lod2_frame=0u;
    g_city_texture_touch_v10.lod3plus_frame=0u;
    volatile black_probe_report_v11 &probe = probe_block_ptr()->report;
    probe.frame_full=0u;
    probe.frame_proxy=0u;
    probe.frame_black=0u;
    probe.frame_pending=0u;
    probe.frame_unrequested=0u;
    MC3_CALL(void,CULLABLE_RENDER)();
    btp_sio_sample();
}

extern "C" void city_streaming_overlay_v10(mc3_u32 obstacle_grid)
{
    MC3_CALL1(void,OBSTACLE_DEBUG_DRAW,mc3_u32)(obstacle_grid);
    ++g_city_texture_touch_v10.overlay_calls;
    draw_overlay();
}

extern "C" mc3_u32 city_model_select_v10(mc3_u32 container,
                                          mc3_u32 index,mc3_u32 lod)
{
    const int active=generated_city_active();
    const mc3_u32 result=MC3_CALL3(mc3_u32,CITY_MODEL_SELECT,
        mc3_u32,mc3_u32,mc3_u32)(container,index,lod);
    if (active) {
        ++g_city_texture_touch_v10.selector_calls;
        g_city_texture_touch_v10.last_selector_container=container;
        g_city_texture_touch_v10.last_selector_index=index;
        g_city_texture_touch_v10.last_selector_lod=lod;
        g_city_texture_touch_v10.last_selector_result=result;
        if (result) ++g_city_texture_touch_v10.selector_nonzero;
    }
    return result;
}

static void record_dispatch(mc3_u32 kind,mc3_u32 object,
                            mc3_u32 owner,mc3_u32 pass,mc3_u32 arg)
{
    if (!generated_city_active()) return;
    ++g_city_texture_touch_v10.dispatch_calls;
    g_city_texture_touch_v10.last_dispatch_kind=kind;
    g_city_texture_touch_v10.last_dispatch_object=object;
    g_city_texture_touch_v10.last_dispatch_owner=owner;
    g_city_texture_touch_v10.last_dispatch_pass=pass;
    g_city_texture_touch_v10.last_dispatch_arg=arg;
}

extern "C" mc3_u32 city_render_dispatch_v10(mc3_u32 object,mc3_u32 owner,
                                             mc3_u32 pass,mc3_u32 arg)
{
    record_dispatch(0u,object,owner,pass,arg);
    return MC3_CALL4(mc3_u32,CITY_RENDER_DISPATCH,
        mc3_u32,mc3_u32,mc3_u32,mc3_u32)(object,owner,pass,arg);
}

extern "C" mc3_u32 inst_render_dispatch_v10(mc3_u32 object,mc3_u32 owner,
                                             mc3_u32 pass,mc3_u32 arg)
{
    record_dispatch(1u,object,owner,pass,arg);
    return MC3_CALL4(mc3_u32,INST_RENDER_DISPATCH,
        mc3_u32,mc3_u32,mc3_u32,mc3_u32)(object,owner,pass,arg);
}

extern "C" mc3_u32 city_draw_touch_v10(mc3_u32 model,mc3_u32 group,
                                        mc3_u32 shader_data,mc3_u32 pass,
                                        mc3_u32 arg5)
{
    if (generated_city_active()) {
        ++g_city_texture_touch_v10.draw_calls;
        touch_model_once(model,group);
    }
    return MC3_CALL5(mc3_u32,RMC_MODEL_DRAW,
        mc3_u32,mc3_u32,mc3_u32,mc3_u32,mc3_u32)(
        model,group,shader_data,pass,arg5);
}

extern "C" mc3_u32 city_draw_cpv_touch_v10(mc3_u32 model,mc3_u32 group,
                                            mc3_u32 shader_data,mc3_u32 pass,
                                            mc3_u32 arg5,mc3_u32 cpv_index)
{
    mc3_u32 guarded_index=cpv_index;
    if (generated_city_active()) {
        ++g_city_texture_touch_v10.draw_cpv_calls;
        guarded_index=record_cpv_and_guard(model,group,arg5,cpv_index);
        touch_model_once(model,group);
    }
    return MC3_CALL6(mc3_u32,RMC_MODEL_DRAW_CPV,
        mc3_u32,mc3_u32,mc3_u32,mc3_u32,mc3_u32,mc3_u32)(
        model,group,shader_data,pass,arg5,guarded_index);
}

MC3_HOOK(0x00257BACu,city_frame_v10);
MC3_HOOK(0x001A2EDCu,city_streaming_overlay_v10);
MC3_HOOK(0x0024BFC4u,city_render_dispatch_v10);
MC3_HOOK(0x00256F78u,inst_render_dispatch_v10);
MC3_HOOK(0x0024C3F8u,city_model_select_v10);
MC3_HOOK(0x0024C448u,city_model_select_v10);
MC3_HOOK(0x0024C490u,city_model_select_v10);
MC3_HOOK(0x0024C4D4u,city_model_select_v10);
MC3_HOOK(0x00257080u,city_model_select_v10);
MC3_HOOK(0x002570D0u,city_model_select_v10);
MC3_HOOK(0x00257118u,city_model_select_v10);
MC3_HOOK(0x0025715Cu,city_model_select_v10);
MC3_HOOK(0x0024C42Cu,city_draw_cpv_touch_v10);
MC3_HOOK(0x0024C47Cu,city_draw_cpv_touch_v10);
MC3_HOOK(0x0024C4C0u,city_draw_touch_v10);
MC3_HOOK(0x0024C508u,city_draw_cpv_touch_v10);
MC3_HOOK(0x002570B4u,city_draw_cpv_touch_v10);
MC3_HOOK(0x00257104u,city_draw_cpv_touch_v10);
MC3_HOOK(0x00257148u,city_draw_touch_v10);
MC3_HOOK(0x00257190u,city_draw_cpv_touch_v10);
