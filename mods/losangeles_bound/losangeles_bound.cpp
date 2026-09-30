// -----------------------------------------------------------------------------
//  losangeles_bound - Los Angeles's collision built at load time from MC2's own
//  losangeles.rsc, so losangeles_bnd.pck is not needed (and not loaded).
//
//  mcLayerCity::InitPhys asks datRscBuilder::LoadBuild (0x42E810, called at
//  0x1A937C) for "$/resources/city/losangeles_bnd": it reads the 128-byte header,
//  aligned_new(body, 128), reads the body and returns it; *out1 = the virtual
//  base the body's pointers were written against, *out2 = the body size.
//  mcPhysics then relocates every pointer by (body - base). For Los Angeles this
//  hook returns a body built here in exactly that form instead.
//
//  What goes in, all from files an install already has:
//    - losangeles.rsc (MC2 PS2, copied by the installer):
//        BND0 type 0x0B  "losangeles"         otgrid, 42 cells -> MC3 type 9
//        BND0 type 0x0A  "losangeles_quad_0"  quadtree         -> MC3 type 10
//      The cell nodes use MC3's node layout (+4C verts, +50 polys, +74/+78
//      vertex/polygon offsets, +80/+84 material handle table) and the 32-byte
//      polygon is MC3's too, with two differences fixed here: indices are
//      local to the cell (rebased), and a triangle is marked by index 3 == 0
//      alone (MC2 puts 0 in neighbour 3, MC3 wants 0xFFFF there).
//      The low byte of the area is the cell's material slot -> PMT0 handle ->
//      material name -> MC3 physical class -> index in the donor's table.
//    - tokyo_bnd.pck (MC3 retail): only 0x330 bytes of it, the objects that
//      are not geometry - mcPhysics, mcLevelBounds, the two bound wrappers,
//      both nodes, both payloads and both material tables. Its pointers are
//      moved to the compact layout below; all geometry is ours.
//
//  Both trees are built here (octree: cube root, octant bit0 x / bit1 z /
//  bit2 y; quadtree: bit0 x / bit1 z; leaf = start<<16 | bytes<<1 | 1; an
//  empty child is a zero-count leaf, never a null) - the same rules as
//  mc2_bnd_to_mc3.py, which reproduce the retail files.
//
//  Anything unexpected -> the original loader runs (losangeles_bnd.pck if one is
//  still installed). Markers (SIO):
//    RBN0 <stage> <detail>    failure; stage says where
//    RBN1 <body> <used>       done: body address and size
//    RBN2 <verts9> <polys9>   RBN3 <refs9> <blocks9 | depth << 24>
//    RBN4 <verts10|polys10<<16> <refs10 | blocks10 << 16>
// -----------------------------------------------------------------------------
#include "../../payload/mc3_mod.h"
#include "../../payload/mc3_heap.h"
#include "../mc2_city_config.h"

enum {
    BOUND_LOAD_CALL = 0x001A937C,     // jal 0x42E810 in mcLayerCity::InitPhys
    BOUND_LOAD = 0x0042E810,          // (path, type 9, &base, &size) -> body
    ALIGNED_NEW = 0x0042E668,
    ALIGNED_DELETE = 0x0042E6C0,
    ASSET_MANAGER = 0x006D557C,       // datAssetManager*; vtable +36 = Open
    ASSET_EXT_PCK = 0x00618D8C,       // the extension LoadBuild passes
    RACE_CONFIG_CURRENT = 0x00619B10, // mcRaceConfig*; +0 = city index
    STREAM_OPEN = 0x003991F0,
    STREAM_READ = 0x003993A8,
    STREAM_SEEK = 0x003996C0,
    STREAM_CLOSE = 0x00399748,
    STREAM_RAW = 1,

    RSC0 = 0x30435352,
    BND0 = 0x30444E42,
    VBASE = 0x06800000,               // tokyo_bnd's base; ours too
    TOKYO_BODY = 0x000CB140,
    SKELETON = 0x330,
    // body offsets of the donor objects after the move (see PIECES)
    NODE10 = 0x050, ROOT10 = 0x0CC, MAT10 = 0x0D0, ROOT9 = 0x0EC,
    PAY10 = 0x0F0, NODE9 = 0x1A0, MAT9 = 0x240, PAY9 = 0x280,
    MAT10_COUNT = 7, MAT9_COUNT = 15,

    PROF9 = 8, POR9 = 48,             // mc2_bnd_to_mc3 --prof 8 --por-folha 48
    PROF10 = 3, POR10 = 15,           // tokyo's quadtree is 3 deep
    QUAD_MAX = 2000,                  // phQuadtreeCell::ClassInit(2000,2000)
    MAX_CELLS = 64,
    MAX_HANDLES = 32,
    MAX_SOLID = 256,                  // instance boxes added as walls (pillars)
    HOOD_CHUNK = 4096,
    HEAP_MARGIN = 0x40000,
    CHUNK = 0x10000,
};

typedef unsigned short mc3_u16;

static __attribute__((noinline)) void put(char c) { *(volatile unsigned char *)0x1000F180 = (unsigned char)c; }
static void hex8(mc3_u32 v) {
    for (int i = 28; i >= 0; i -= 4) { unsigned d = (v >> i) & 15u; put((char)(d < 10 ? '0' + d : 'A' + d - 10)); }
}
static void mark(char a, char b, char c, char d, mc3_u32 x, mc3_u32 y) {
    put(a); put(b); put(c); put(d); put(' '); hex8(x); put(' '); hex8(y); put('\n');
}
static __attribute__((noinline)) float ffrom(mc3_u32 bits) {
    union { mc3_u32 u; float f; } v; v.u = bits; return v.f;
}
static inline float fsqrt(float x) {
    float r;
    __asm__("sqrt.s %0, %1" : "=f"(r) : "f"(x));
    return r;
}
static inline mc3_u32 rd32(const mc3_u8 *p) { return *(const mc3_u32 *)p; }
static inline void wr32(mc3_u8 *p, mc3_u32 v) { *(mc3_u32 *)p = v; }
static inline void wrf(mc3_u8 *p, float v) { *(float *)p = v; }
static mc3_u32 align16(mc3_u32 v) { return (v + 15u) & ~15u; }

// ------------------------------------------------------------------ streams
static int fail(mc3_u32 stage, mc3_u32 detail);
static __attribute__((noinline)) int seek_read(mc3_u32 h, mc3_u32 pos, void *dst, mc3_u32 n) {
    if (MC3_CALL2(int, STREAM_SEEK, mc3_u32, int)(h, (int)pos) < 0) return 0;
    mc3_u8 *p = (mc3_u8 *)dst;
    while (n) {
        const mc3_u32 k = n > CHUNK ? (mc3_u32)CHUNK : n;
        if (MC3_CALL3(mc3_u32, STREAM_READ, mc3_u32, mc3_u32, mc3_u32)(h, (mc3_u32)p, k) != k)
            return 0;
        p += k; n -= k;
    }
    return 1;
}
static void close_stream(mc3_u32 h) { if (h) MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(h); }

// datAssetManager::Open(name, ext, 0, 1, 0x620000) - the call LoadBuild makes,
// so tokyo_bnd is found wherever the game finds it (HostFS or disc).
static mc3_u32 open_asset(const char *name) {
    const mc3_u32 mgr = *(volatile mc3_u32 *)ASSET_MANAGER;
    if (!mc3_ptr_ok(mgr)) return 0;
    const mc3_u32 open = *(volatile mc3_u32 *)(*(volatile mc3_u32 *)mgr + 36u);
    return MC3_CALL6(mc3_u32, open, mc3_u32, const char *, mc3_u32, int, int, int)
        (mgr, name, *(volatile mc3_u32 *)ASSET_EXT_PCK, 0, 1, 0x620000);
}

// ------------------------------------------------------------------ materials
static int has(const char *s, const char *w) {
    for (; *s; ++s) {
        int i = 0;
        while (w[i] && s[i] && (s[i] | 0x20) == w[i]) ++i;
        if (!w[i]) return 1;
    }
    return 0;
}
// MC2 material name -> MC3 global material id (mc3_bound.physical_material_name)
static mc3_u32 physical_id(const char *name) {
    static const char k_cobble[] = "cobblestone", k_rail[] = "railroad",
        k_side[] = "sidewalk", k_conc[] = "concrete", k_stairs[] = "stairs",
        k_road[] = "road", k_asph[] = "asphalt", k_ashp[] = "ashphalt",
        k_cross[] = "crosswalk", k_dry[] = "alwaysdry", k_bridge[] = "bridge",
        k_build[] = "building", k_trunk[] = "treetrunk", k_wall[] = "wall",
        k_water[] = "water", k_grass[] = "grass", k_dirt[] = "dirt",
        k_metal[] = "metal", k_sewer[] = "sewer", k_alley[] = "alley",
        k_int[] = "interior", k_death[] = "death";
    const char *s = name;
    for (const char *p = name; *p; ++p) if (*p == ':') s = p + 1;
    if (has(s, k_cobble)) return 2;
    if (has(s, k_rail)) return 16;
    if (has(s, k_side) || has(s, k_conc) || has(s, k_stairs)) return 21;
    if (has(s, k_road) || has(s, k_asph) || has(s, k_ashp) || has(s, k_cross) ||
        has(s, k_dry) || has(s, k_bridge)) return 17;
    if (has(s, k_build) || has(s, k_trunk) || has(s, k_wall)) return 25;
    if (has(s, k_water)) return 26;
    if (has(s, k_grass)) return 8;
    if (has(s, k_dirt)) return 4;
    if (has(s, k_metal)) return 13;
    if (has(s, k_sewer)) return 20;
    if (has(s, k_alley)) return 1;
    if (has(s, k_int)) return 11;
    if (has(s, k_death)) return 3;
    return 0;
}

struct Build {
    mc3_u32 rsc, tokyo;
    mc3_u8 *body;
    mc3_u32 body_cap;
    // cache of PMT0 handle -> global material id
    mc3_u32 handle[MAX_HANDLES], global[MAX_HANDLES], handles;
    mc3_u32 err_stage, err_detail;
    // world boxes of the instances made solid (solid_instances): lo xyz, hi xyz
    float solid[MAX_SOLID][6];
    mc3_u32 solids, hoods;
    char line[HOOD_CHUNK + 256];
};
static Build g_build;
// every access through this: GCC otherwise shares one lui between accesses,
// which the module loader cannot relocate
static __attribute__((noinline)) Build *G() { return &g_build; }
static int fail(mc3_u32 stage, mc3_u32 detail) {
    G()->err_stage = stage; G()->err_detail = detail; return 0;
}

static mc3_u32 rsc_entry_offset(mc3_u32 index) {
    mc3_u8 e[8] __attribute__((aligned(16)));
    return seek_read(G()->rsc, 8u + 8u * index, e, 8u) ? rd32(e) : 0u;
}
// local index of a PMT0 handle in the donor table at `table`
static mc3_u32 material_local(mc3_u32 h, const mc3_u8 *table, mc3_u32 count) {
    mc3_u32 id = 0xFFFFFFFFu;
    for (mc3_u32 i = 0; i < G()->handles; ++i) if (G()->handle[i] == h) id = G()->global[i];
    if (id == 0xFFFFFFFFu) {
        mc3_u8 pmt[48] __attribute__((aligned(16)));
        const mc3_u32 off = rsc_entry_offset((h & 0x3FFFu) - 1u);
        id = 0u;
        if (off && seek_read(G()->rsc, off, pmt, 40u) && rd32(pmt) == 0x0FFFFF00u) {
            pmt[40] = 0;
            id = physical_id((const char *)pmt + 8);
        }
        if (G()->handles < MAX_HANDLES) { G()->handle[G()->handles] = h; G()->global[G()->handles++] = id; }
    }
    for (mc3_u32 i = 0; i < count; ++i) if (rd32(table + 4u * i) == id) return i;
    return 0u;
}

// ------------------------------------------------------------------ geometry
struct Source { mc3_u32 block, node; };   // rsc offsets of a BND0 block / node
// One MC2 node's vertices and polygons, straight into the body.
static int load_node(const Source &src, mc3_u8 *hdr, mc3_u8 *verts, mc3_u8 *polys,
                     mc3_u32 vbase, const mc3_u8 *table, mc3_u32 tcount) {
    const mc3_u32 nv = rd32(hdr + 0x4C), np = rd32(hdr + 0x50);
    if (!seek_read(G()->rsc, src.block + rd32(hdr + 0x74), verts, nv * 12u)) return fail(0x30, nv);
    if (!seek_read(G()->rsc, src.block + rd32(hdr + 0x78), polys, np * 32u)) return fail(0x31, np);
    mc3_u32 mcount = rd32(hdr + 0x84);
    if (mcount > 16u) return fail(0x32, mcount);
    mc3_u8 handles[64] __attribute__((aligned(16)));
    mc3_u8 lut[16];
    if (mcount && !seek_read(G()->rsc, src.block + rd32(hdr + 0x80), handles, mcount * 4u))
        return fail(0x33, mcount);
    for (mc3_u32 k = 0; k < mcount; ++k)
        lut[k] = (mc3_u8)material_local(rd32(handles + 4u * k), table, tcount);
    for (mc3_u32 k = 0; k < np; ++k) {
        mc3_u8 *p = polys + 32u * k;
        const mc3_u32 m = p[12];
        p[12] = m < mcount ? lut[m] : 0u;
        mc3_u16 *idx = (mc3_u16 *)(p + 16);
        const mc3_u32 corners = idx[3] == 0 ? 3u : 4u;
        for (mc3_u32 j = 0; j < corners; ++j) {
            if (idx[j] >= nv) return fail(0x34, k);
            idx[j] = (mc3_u16)(idx[j] + vbase);
        }
        idx[4] = idx[5] = idx[6] = idx[7] = 0xFFFFu;
    }
    return 1;
}

// ------------------------------------------------------------------ trees
struct Tree {
    const float *v;            // vertices (x y z)
    const mc3_u8 *p;           // polygons
    mc3_u32 np, fan, prof, por;
    // result of a pass
    mc3_u32 refs, blocks, depth, overflow;
    // emission (pass 2); null in the counting pass
    mc3_u16 *idx;
    mc3_u8 *blk;
    mc3_u32 blk_va;
    mc3_u8 *scratch, *scratch_end;
};

static void poly_box(const Tree *t, mc3_u32 k, float *lo, float *hi) {
    const mc3_u16 *idx = (const mc3_u16 *)(t->p + 32u * k + 16u);
    const mc3_u32 corners = idx[3] == 0 ? 3u : 4u;
    const float *q = t->v + 3u * idx[0];
    lo[0] = hi[0] = q[0]; lo[1] = hi[1] = q[1]; lo[2] = hi[2] = q[2];
    for (mc3_u32 j = 1; j < corners; ++j) {
        q = t->v + 3u * idx[j];
        for (int a = 0; a < 3; ++a) {
            if (q[a] < lo[a]) lo[a] = q[a];
            if (q[a] > hi[a]) hi[a] = q[a];
        }
    }
}

// Returns the slot value for this subtree (meaningful in the emission pass).
// `list` null = every polygon (the root).
static mc3_u32 build(Tree *t, const mc3_u16 *list, mc3_u32 n,
                     const float *lo, const float *hi, mc3_u32 d) {
    if (d > t->depth) t->depth = d;
    const mc3_u32 need = n * 3u + 4u;      // masks + the largest child list
    const int leaf = d >= t->prof || n <= t->por ||
                     (mc3_u32)(t->scratch_end - t->scratch) < need;
    if (leaf) {
        const mc3_u32 start = t->refs;
        if (start + n > 0xFFFFu) { t->overflow = 1u; return 1u; }
        if (t->idx)
            for (mc3_u32 i = 0; i < n; ++i) t->idx[start + i] = (mc3_u16)(list ? list[i] : i);
        t->refs += n;
        return (start << 16) | (n * 4u) | 1u;
    }
    const float half = ffrom(0x3F000000u);
    float mid[3];
    for (int a = 0; a < 3; ++a) mid[a] = (lo[a] + hi[a]) * half;
    // which children each polygon reaches: it already overlaps this box, so a
    // half is reached when the polygon reaches past the middle on that side
    mc3_u8 *mask = t->scratch;
    t->scratch += (n + 1u) & ~1u;
    for (mc3_u32 i = 0; i < n; ++i) {
        float blo[3], bhi[3];
        poly_box(t, list ? list[i] : i, blo, bhi);
        const mc3_u32 xl = blo[0] <= mid[0], xh = bhi[0] >= mid[0];
        const mc3_u32 zl = blo[2] <= mid[2], zh = bhi[2] >= mid[2];
        const mc3_u32 yl = t->fan == 4u || blo[1] <= mid[1];
        const mc3_u32 yh = t->fan == 4u || bhi[1] >= mid[1];
        mc3_u32 m = 0;
        for (mc3_u32 o = 0; o < t->fan; ++o) {
            const mc3_u32 bx = o & 1u, bz = (o >> 1) & 1u, by = (o >> 2) & 1u;
            if ((bx ? xh : xl) && (bz ? zh : zl) && (by ? yh : yl)) m |= 1u << o;
        }
        mask[i] = (mc3_u8)m;
    }
    const mc3_u32 b = t->blocks++;
    const mc3_u32 stride = 16u + 4u * t->fan;
    mc3_u8 *slots = t->blk ? t->blk + b * stride + 16u : 0;
    if (slots) {
        wr32(slots - 16, t->fan);
        for (int k = 4; k < 16; ++k) slots[k - 16] = 0xCDu;
    }
    mc3_u16 *child = (mc3_u16 *)t->scratch;
    for (mc3_u32 o = 0; o < t->fan; ++o) {
        const mc3_u32 bx = o & 1u, bz = (o >> 1) & 1u, by = (o >> 2) & 1u;
        float clo[3], chi[3];
        clo[0] = bx ? mid[0] : lo[0]; chi[0] = bx ? hi[0] : mid[0];
        clo[2] = bz ? mid[2] : lo[2]; chi[2] = bz ? hi[2] : mid[2];
        if (t->fan == 8u) { clo[1] = by ? mid[1] : lo[1]; chi[1] = by ? hi[1] : mid[1]; }
        else { clo[1] = lo[1]; chi[1] = hi[1]; }
        mc3_u32 m = 0;
        for (mc3_u32 i = 0; i < n; ++i)
            if (mask[i] & (1u << o)) child[m++] = (mc3_u16)(list ? list[i] : i);
        t->scratch = (mc3_u8 *)(child + ((m + 1u) & ~1u));
        const mc3_u32 v = build(t, child, m, clo, chi, d + 1u);
        t->scratch = (mc3_u8 *)child;
        if (slots) wr32(slots + 4u * o, v);
    }
    t->scratch = mask;
    return t->blk_va + b * stride + 16u;
}

static void init_tree(Tree *t, const mc3_u8 *v, const mc3_u8 *p, mc3_u32 np, mc3_u32 fan,
                      mc3_u32 prof, mc3_u32 por, mc3_u8 *scratch, mc3_u32 bytes) {
    t->v = (const float *)v; t->p = p; t->np = np; t->fan = fan; t->prof = prof; t->por = por;
    t->refs = t->blocks = t->depth = t->overflow = 0u;
    t->idx = 0; t->blk = 0; t->blk_va = 0u;
    t->scratch = scratch; t->scratch_end = scratch + bytes;
}

struct Box { float lo[3], hi[3], ctr[3], clo[3], chi[3], side; };
static void cube(const float *v, mc3_u32 nv, Box *b) {
    for (int a = 0; a < 3; ++a) b->lo[a] = b->hi[a] = v[a];
    for (mc3_u32 i = 1; i < nv; ++i)
        for (int a = 0; a < 3; ++a) {
            const float x = v[3u * i + a];
            if (x < b->lo[a]) b->lo[a] = x;
            if (x > b->hi[a]) b->hi[a] = x;
        }
    const float half = ffrom(0x3F000000u);
    b->side = b->hi[0] - b->lo[0];
    for (int a = 1; a < 3; ++a) if (b->hi[a] - b->lo[a] > b->side) b->side = b->hi[a] - b->lo[a];
    for (int a = 0; a < 3; ++a) {
        b->ctr[a] = (b->lo[a] + b->hi[a]) * half;
        b->clo[a] = b->ctr[a] - b->side * half;
        b->chi[a] = b->ctr[a] + b->side * half;
    }
}

// A counting pass with growing leaves until the tree fits its budget.
static int plan_tree(Tree *t, const Box &box, mc3_u32 max_refs, mc3_u32 max_blocks) {
    for (int tries = 0; tries < 6; ++tries) {
        mc3_u8 *s = t->scratch;
        t->refs = t->blocks = t->depth = t->overflow = 0u;
        t->idx = 0; t->blk = 0; t->blk_va = 0;
        build(t, 0, t->np, box.clo, box.chi, 0u);
        t->scratch = s;
        if (!t->overflow && t->refs <= max_refs && t->blocks <= max_blocks) return 1;
        t->por *= 2u;
    }
    return 0;
}

// Node, payload and root for one tree (field meanings in mc3_bound.py and
// mc2_bnd_to_mc3.py; +38/+3C are the radii around the centre / the origin).
static void finish_tree(mc3_u8 *body, mc3_u32 node, mc3_u32 pay, mc3_u32 rootslot,
                        const Tree &t, const Box &box, mc3_u32 nv, mc3_u32 root,
                        mc3_u32 verts_off, mc3_u32 polys_off, mc3_u32 idx_off) {
    float r1 = ffrom(0u), r2 = ffrom(0u);
    for (mc3_u32 i = 0; i < nv; ++i) {
        const float *q = t.v + 3u * i;
        float a = 0, b = 0;
        for (int k = 0; k < 3; ++k) {
            const float d = q[k] - box.ctr[k];
            a += d * d; b += q[k] * q[k];
        }
        if (a > r1) r1 = a;
        if (b > r2) r2 = b;
    }
    float cell = box.side;
    for (mc3_u32 i = 0; i < t.depth; ++i) cell = cell * ffrom(0x3F000000u);
    wr32(body + rootslot, root);
    mc3_u8 *n = body + node;
    for (int a = 0; a < 3; ++a) {
        wrf(n + 0x08 + 4 * a, box.lo[a]);
        wrf(n + 0x14 + 4 * a, box.hi[a]);
        wrf(n + 0x20 + 4 * a, box.ctr[a]);
        wrf(n + 0x2C + 4 * a, box.ctr[a]);
    }
    wrf(n + 0x38, fsqrt(r1));
    wrf(n + 0x3C, fsqrt(r2));
    wr32(n + 0x4C, nv);
    wr32(n + 0x50, t.np);
    wr32(n + 0x68, VBASE + verts_off);
    wr32(n + 0x6C, VBASE + polys_off);
    if (t.fan == 4u) wr32(n + 0x7C, root);
    mc3_u8 *p = body + pay;
    wr32(p + 0x08, t.np);
    wr32(p + 0x0C, nv);
    wr32(p + 0x10, t.refs);
    wr32(p + 0x14, VBASE + idx_off);
    wrf(p + 0x18, cell);
    for (int a = 0; a < 3; ++a) {
        wrf(p + 0x1C + 4 * a, box.clo[a]);
        wrf(p + 0x28 + 4 * a, box.chi[a]);
    }
}

// ------------------------------------------------------------------ skeleton
// tokyo_bnd.pck ranges (file offsets) that hold no geometry, in body order
static int load_skeleton(mc3_u8 *body) {
    static const mc3_u32 pieces[10] = {
        0x00080, 0x00150,   // mcPhysics, mcLevelBounds, node 10, root slot 10
        0x01750, 0x017C0,   // material table 10, root slot 9, payload 10
        0x01AC0, 0x01BC0,   // wrapper 10, node 9
        0xAC750, 0xAC7E0,   // material table 9, payload 9
        0xCB160, 0xCB1C0,   // wrapper 9
    };
    mc3_u32 at = 0;
    for (int i = 0; i < 10; i += 2) {
        if (!seek_read(G()->tokyo, pieces[i], body + at, pieces[i + 1] - pieces[i]))
            return fail(0x20, (mc3_u32)i);
        at += pieces[i + 1] - pieces[i];
    }
    // pointers into the donor -> the same object in the compact layout; the
    // geometry pointers are left 0 and written later
    for (mc3_u32 o = 0; o < SKELETON; o += 4u) {
        const mc3_u32 w = rd32(body + o);
        if (w < VBASE || w >= VBASE + TOKYO_BODY) continue;
        const mc3_u32 f = w - VBASE + 0x80u;
        mc3_u32 moved = 0, base = 0;
        for (int i = 0; i < 10; i += 2) {
            if (f >= pieces[i] && f < pieces[i + 1]) moved = VBASE + base + f - pieces[i];
            base += pieces[i + 1] - pieces[i];
        }
        wr32(body + o, moved);
    }
    if ((rd32(body + NODE10 + 4) & 0xFFu) != 10u || (rd32(body + NODE9 + 4) & 0xFFu) != 9u)
        return fail(0x21, rd32(body + NODE9 + 4));
    return 1;
}


// ------------------------------------------------------------------ pillars
// MC2's city collision has no freeway supports: l_inst_freeway_support_01x
// (52 in LA, 1.4 x 6.3 x 2.7 m, always at 0/90 degrees) is an instance, and
// the otgrid only holds three buried stubs at its foot - a car drove straight
// through every pillar under the freeways. Each such instance of the .hood
// files (copied by the installer, host0:/mc2/losangeles/city/) becomes four
// walls from its world box (emin/emax), appended to the type 9 geometry
// before the octree is built. Marker RBN5 <boxes> <hoods read>.
static mc3_u32 current_city(void);
static __attribute__((noinline)) const char *city_dir() {
    return mc2_city_dir(current_city());
}
static __attribute__((noinline)) const char *lvl_file() {
    return mc2_city_lvl(current_city());
}
static __attribute__((noinline)) const char *hood_ext() {
    static const char s[] = ".hood"; return s;
}
static __attribute__((noinline)) const char *solid_type() {
    return mc2_city_solid_type(current_city());
}
static __attribute__((noinline)) const char *kw_instance() {
    static const char s[] = "instance_component"; return s;
}
static __attribute__((noinline)) const char *kw_type() { static const char s[] = "type:"; return s; }
static __attribute__((noinline)) const char *kw_name() { static const char s[] = "name:"; return s; }
static __attribute__((noinline)) const char *kw_emin() { static const char s[] = "emin"; return s; }
static __attribute__((noinline)) const char *kw_emax() { static const char s[] = "emax"; return s; }
static int starts_with(const char *p, const char *w) {
    while (*w) if (*p++ != *w++) return 0;
    return 1;
}
static float parse_f(const char **pp) {
    const char *p = *pp;
    while (*p == ' ' || *p == '\t') ++p;
    int neg = 0;
    if (*p == '-') { neg = 1; ++p; }
    float v = ffrom(0u);
    while (*p >= '0' && *p <= '9') v = v * ffrom(0x41200000u) + (float)(*p++ - '0');
    if (*p == '.') {
        ++p;
        float f = ffrom(0x3DCCCCCDu);
        while (*p >= '0' && *p <= '9') { v += f * (float)(*p++ - '0'); f *= ffrom(0x3DCCCCCDu); }
    }
    *pp = p;
    return neg ? -v : v;
}
// Calls line(text, arg) for every line of a HostFS text file.
static int each_line(const char *path, void (*line)(char *, char *), char *arg) {
    const mc3_u32 h = MC3_CALL2(mc3_u32, STREAM_OPEN, const char *, int)(path, STREAM_RAW);
    if (!h) return 0;
    char *b = G()->line;
    mc3_u32 have = 0;
    for (;;) {
        const int got = MC3_CALL3(int, STREAM_READ, mc3_u32, void *, int)
            (h, b + have, (int)(HOOD_CHUNK - have));
        if (got > 0) have += (mc3_u32)got;
        mc3_u32 start = 0;
        for (mc3_u32 i = 0; i < have; ++i) {
            if (b[i] != '\n') continue;
            b[i] = 0;
            line(b + start, arg);
            start = i + 1u;
        }
        if (got <= 0) {                                  // last line without '\n'
            if (start < have) { b[have] = 0; line(b + start, arg); }
            break;
        }
        for (mc3_u32 i = start; i < have; ++i) b[i - start] = b[i];
        have -= start;
        if (have >= HOOD_CHUNK) have = 0;                // absurd line: drop it
    }
    close_stream(h);
    return 1;
}
// .hood line; arg[0] = 1 while inside a wanted instance_component
static void hood_line(char *l, char *arg) {
    while (*l == ' ' || *l == '\t') ++l;
    if (starts_with(l, kw_instance())) { arg[0] = 0; return; }
    if (starts_with(l, kw_type())) {
        const char *p = l + 5;
        while (*p == ' ' || *p == '\t') ++p;
        const char *w = solid_type();
        int same = 1;
        while (*w) if (*p++ != *w++) { same = 0; break; }
        if (same && (*p == 0 || *p == ' ' || *p == '\r' || *p == '\t')) arg[0] = 1;
        return;
    }
    if (!arg[0] || G()->solids >= MAX_SOLID) return;
    const int lo = starts_with(l, kw_emin()), hi = starts_with(l, kw_emax());
    if (!lo && !hi) return;
    const char *p = l + 4;
    float *box = G()->solid[G()->solids];
    for (int k = 0; k < 3; ++k) box[(hi ? 3 : 0) + k] = parse_f(&p);
    if (hi) { ++G()->solids; arg[0] = 0; }
}
static void join(char *out, const char *a, const char *b, const char *c) {
    while (*a) *out++ = *a++;
    while (*b) *out++ = *b++;
    while (c && *c) *out++ = *c++;
    *out = 0;
}
// .lvl line: "name: <hood>" -> read that hood. The .lvl itself is read first
// into a small list, because each_line shares one buffer.
static void lvl_line(char *l, char *names) {
    while (*l == ' ' || *l == '\t') ++l;
    if (!starts_with(l, kw_name())) return;
    char *n = l + 5;
    while (*n == ' ' || *n == '\t') ++n;
    mc3_u32 len = 0;
    while (n[len] && n[len] != ' ' && n[len] != '\r' && n[len] != '\t' && len < 31u) ++len;
    mc3_u32 at = 0;
    while (names[at]) at += 32u;                   // next free 32-byte slot
    if (at >= 32u * 31u) return;
    for (mc3_u32 i = 0; i < len; ++i) names[at + i] = n[i];
    names[at + len] = 0;
}
static void solid_instances() {
    G()->solids = G()->hoods = 0;
    char names[32 * 32];
    for (mc3_u32 i = 0; i < sizeof(names); ++i) names[i] = 0;
    char path[128];
    join(path, city_dir(), lvl_file(), 0);
    each_line(path, lvl_line, names);
    for (mc3_u32 at = 0; at < sizeof(names) && names[at]; at += 32u) {
        join(path, city_dir(), names + at, hood_ext());
        char state[4] = { 0, 0, 0, 0 };
        if (each_line(path, hood_line, state)) ++G()->hoods;
    }
    mark('R','B','N','5', G()->solids, G()->hoods);
}
// Four walls of box b: 8 vertices at vb.. (bottom 0-3, top 4-7: x0z0 x1z0
// x1z1 x0z1) and 4 quads. Corner order as MC2's own walls: from a to b along
// up x normal, then a top, a bottom, b bottom, b top.
static void write_box(const float *b, mc3_u8 *verts, mc3_u8 *polys, mc3_u32 vb) {
    for (int k = 0; k < 8; ++k) {
        const int c = k & 3;
        wrf(verts + 12u * k, (c == 1 || c == 2) ? b[3] : b[0]);
        wrf(verts + 12u * k + 4u, k < 4 ? b[1] : b[4]);
        wrf(verts + 12u * k + 8u, c >= 2 ? b[5] : b[2]);
    }
    const float h = b[4] - b[1];
    for (int w = 0; w < 4; ++w) {
        // +z at z1 (x0->x1), -z at z0 (x1->x0), +x at x1 (z1->z0), -x at x0 (z0->z1)
        const int nx = w == 2 ? 1 : w == 3 ? -1 : 0;
        const int nz = w == 0 ? 1 : w == 1 ? -1 : 0;
        const mc3_u32 a = w == 0 ? 3u : w == 1 ? 1u : w == 2 ? 2u : 0u;
        const mc3_u32 c = w == 0 ? 2u : w == 1 ? 0u : w == 2 ? 1u : 3u;
        mc3_u8 *p = polys + 32u * w;
        wrf(p + 0, (float)nx); wrf(p + 4, ffrom(0u)); wrf(p + 8, (float)nz);
        const float len = nx ? b[5] - b[2] : b[3] - b[0];
        wrf(p + 12, len * h);
        p[12] = 0;                                   // material slot 0 (low byte of the area)
        mc3_u16 *idx = (mc3_u16 *)(p + 16);
        idx[0] = (mc3_u16)(vb + 4u + a); idx[1] = (mc3_u16)(vb + a);
        idx[2] = (mc3_u16)(vb + c);      idx[3] = (mc3_u16)(vb + 4u + c);
        idx[4] = idx[5] = idx[6] = idx[7] = 0xFFFFu;
    }
}

// ------------------------------------------------------------------ main
static int find_bnd0(Source *grid, Source *quad) {
    mc3_u8 hdr[8] __attribute__((aligned(16)));
    if (!seek_read(G()->rsc, 0, hdr, 8u) || rd32(hdr) != RSC0) return fail(0x10, rd32(hdr));
    const mc3_u32 count = rd32(hdr + 4);
    mc3_u8 dir[512] __attribute__((aligned(16)));
    grid->block = quad->block = 0;
    for (mc3_u32 i = 0; i < count; i += 64u) {
        const mc3_u32 k = count - i < 64u ? count - i : 64u;
        if (!seek_read(G()->rsc, 8u + 8u * i, dir, 8u * k)) return fail(0x11, i);
        for (mc3_u32 j = 0; j < k; ++j) {
            if (rd32(dir + 8u * j + 4u) != BND0) continue;
            const mc3_u32 off = rd32(dir + 8u * j);
            mc3_u8 h[16] __attribute__((aligned(16)));
            if (!seek_read(G()->rsc, off, h, 16u)) return fail(0x12, off);
            if ((h[4] == 0x0Bu) && !grid->block) grid->block = off;
            if ((h[4] == 0x0Au) && !quad->block) quad->block = off;
        }
    }
    if (!grid->block || !quad->block) return fail(0x13, grid->block);
    grid->node = grid->block;
    quad->node = quad->block;
    return 1;
}

static mc3_u32 build_body(mc3_u32 *out_size) {
    static const char tokyo_name[] = "$/resources/city/tokyo_bnd";
    const char *const rsc_path = mc2_city_rsc(current_city());
    if (!rsc_path) return fail(0x01, 0);
    G()->rsc = MC3_CALL2(mc3_u32, STREAM_OPEN, const char *, int)(rsc_path, STREAM_RAW);
    if (!G()->rsc) return fail(0x01, 0);
    G()->tokyo = open_asset(tokyo_name);
    if (!G()->tokyo) return fail(0x02, 0);
    mc3_u8 th[128] __attribute__((aligned(16)));
    if (!seek_read(G()->tokyo, 0, th, 128u) || rd32(th) != VBASE || rd32(th + 4) != 9u ||
        rd32(th + 8) != 1u || rd32(th + 12) != TOKYO_BODY)
        return fail(0x03, rd32(th + 12));

    Source grid, quad;
    if (!find_bnd0(&grid, &quad)) return 0;
    // grid: +5C cells along x, +68 along z, +6C first cell, 0x90 apart
    mc3_u8 gh[0x74] __attribute__((aligned(16)));
    if (!seek_read(G()->rsc, grid.block, gh, 0x74u)) return fail(0x14, 0);
    const mc3_u32 cells = rd32(gh + 0x5C) * rd32(gh + 0x68), first = rd32(gh + 0x6C);
    if (!cells || cells > MAX_CELLS) return fail(0x15, cells);
    mc3_u8 qh[0x90] __attribute__((aligned(16)));
    if (!seek_read(G()->rsc, quad.block, qh, 0x90u)) return fail(0x16, 0);
    const mc3_u32 nv10 = rd32(qh + 0x4C), np10 = rd32(qh + 0x50);
    if (!np10 || nv10 > QUAD_MAX || np10 > QUAD_MAX) return fail(0x17, np10);

    mc3_u32 nv9 = 0, np9 = 0;
    for (mc3_u32 c = 0; c < cells; ++c) {
        mc3_u8 ch[0x90] __attribute__((aligned(16)));
        if (!seek_read(G()->rsc, grid.block + first + 0x90u * c, ch, 0x90u)) return fail(0x18, c);
        if (rd32(ch + 0x84) == 0xFFFFFFFFu) continue;
        nv9 += rd32(ch + 0x4C); np9 += rd32(ch + 0x50);
    }
    solid_instances();
    const mc3_u32 grid_v = nv9, grid_p = np9;
    nv9 += G()->solids * 8u; np9 += G()->solids * 4u;
    if (!np9 || nv9 > 0xFFFFu) return fail(0x19, nv9);

    // layout: skeleton | polys10 verts10 polys9 verts9 | idx10 blocks10 idx9 blocks9
    const mc3_u32 polys10 = align16(SKELETON);
    const mc3_u32 verts10 = align16(polys10 + np10 * 32u);
    const mc3_u32 polys9 = align16(verts10 + nv10 * 12u);
    const mc3_u32 verts9 = align16(polys9 + np9 * 32u);
    const mc3_u32 trees = align16(verts9 + nv9 * 12u);
    const mc3_u32 max_refs10 = np10 * 4u + 64u, max_blocks10 = np10 / 4u + 16u;
    mc3_u32 max_refs9 = np9 * 5u / 2u, max_blocks9 = np9 / 24u + 16u;
    if (max_refs9 > 0xFFFFu) max_refs9 = 0xFFFFu;
    const mc3_u32 cap = trees + align16(max_refs10 * 2u) + max_blocks10 * 32u +
                        align16(max_refs9 * 2u) + max_blocks9 * 48u + 64u;
    const mc3_u32 scratch_bytes = np9 * 6u + 4096u;
    // largest free block, not only the cursor-top gap (see mc3_heap_largest)
    if (mc3_heap_largest(mc3_heap_active()) < cap + 256u + HEAP_MARGIN)
        return fail(0x04, mc3_heap_largest(mc3_heap_active()));
    mc3_u8 *body = (mc3_u8 *)MC3_CALL2(mc3_u32, ALIGNED_NEW, mc3_u32, mc3_u32)(cap, 128u);
    if (!body) return fail(0x05, cap);
    G()->body = body; G()->body_cap = cap;
    for (mc3_u32 i = SKELETON; i < trees; i += 4u) wr32(body + i, 0xCDCDCDCDu);

    if (!load_skeleton(body)) return 0;
    wr32(body + 0, nv9);
    wr32(body + 4, np9);

    // geometry
    if (!load_node(quad, qh, body + verts10, body + polys10, 0u, body + MAT10, MAT10_COUNT))
        return 0;
    mc3_u32 vb = 0, pb = 0;
    for (mc3_u32 c = 0; c < cells; ++c) {
        mc3_u8 ch[0x90] __attribute__((aligned(16)));
        if (!seek_read(G()->rsc, grid.block + first + 0x90u * c, ch, 0x90u)) return fail(0x18, c);
        if (rd32(ch + 0x84) == 0xFFFFFFFFu) continue;
        if (!load_node(grid, ch, body + verts9 + vb * 12u, body + polys9 + pb * 32u,
                       vb, body + MAT9, MAT9_COUNT)) return 0;
        vb += rd32(ch + 0x4C); pb += rd32(ch + 0x50);
    }
    if (vb != grid_v || pb != grid_p) return fail(0x1A, vb);
    for (mc3_u32 i = 0; i < G()->solids; ++i, vb += 8u, pb += 4u)
        write_box(G()->solid[i], body + verts9 + vb * 12u, body + polys9 + pb * 32u, vb);
    close_stream(G()->rsc); G()->rsc = 0;
    close_stream(G()->tokyo); G()->tokyo = 0;

    // trees
    if (mc3_heap_largest(mc3_heap_active()) < scratch_bytes + 64u + HEAP_MARGIN)
        return fail(0x06, mc3_heap_largest(mc3_heap_active()));
    mc3_u8 *scratch = (mc3_u8 *)MC3_CALL2(mc3_u32, ALIGNED_NEW, mc3_u32, mc3_u32)(scratch_bytes, 16u);
    Tree t10, t9;
    init_tree(&t10, body + verts10, body + polys10, np10, 4u, PROF10, POR10, scratch, scratch_bytes);
    init_tree(&t9, body + verts9, body + polys9, np9, 8u, PROF9, POR9, scratch, scratch_bytes);
    Box b10, b9;
    cube(t10.v, nv10, &b10);
    cube(t9.v, nv9, &b9);
    int ok = plan_tree(&t10, b10, max_refs10, max_blocks10) &&
             plan_tree(&t9, b9, max_refs9, max_blocks9);
    mc3_u32 end = trees;
    if (ok) {
        const mc3_u32 idx10 = trees;
        const mc3_u32 blk10 = align16(idx10 + t10.refs * 2u);
        const mc3_u32 idx9 = blk10 + t10.blocks * 32u;
        const mc3_u32 blk9 = align16(idx9 + t9.refs * 2u);
        end = blk9 + t9.blocks * 48u;
        for (mc3_u32 i = trees; i < end; i += 4u) wr32(body + i, 0xCDCDCDCDu);
        for (int k = 0; k < 2; ++k) {
            Tree *t = k ? &t9 : &t10;
            const Box &bx = k ? b9 : b10;
            const mc3_u32 blk = k ? blk9 : blk10;
            t->refs = t->blocks = t->depth = t->overflow = 0u;
            t->idx = (mc3_u16 *)(body + (k ? idx9 : idx10));
            t->blk = body + blk;
            t->blk_va = VBASE + blk;
            const mc3_u32 root = build(t, 0, t->np, bx.clo, bx.chi, 0u);
            if (t->overflow) { ok = 0; break; }
            if (k) finish_tree(body, NODE9, PAY9, ROOT9, *t, bx, nv9, root, verts9, polys9, idx9);
            else finish_tree(body, NODE10, PAY10, ROOT10, *t, bx, nv10, root, verts10, polys10, idx10);
        }
    }
    MC3_CALL1(void, ALIGNED_DELETE, mc3_u32)((mc3_u32)scratch);
    if (!ok) return fail(0x40, t9.refs);
    mark('R','B','N','2', nv9, np9);
    mark('R','B','N','3', t9.refs, t9.blocks | (t9.depth << 24));
    mark('R','B','N','4', nv10 | (np10 << 16), t10.refs | (t10.blocks << 16));
    *out_size = end;
    return (mc3_u32)body;
}

static __attribute__((noinline)) mc3_u32 current_city(void) {
    const mc3_u32 cfg = *(volatile mc3_u32 *)RACE_CONFIG_CURRENT;
    return mc3_ptr_ok(cfg) ? *(volatile mc3_u32 *)cfg : 0xFFFFFFFFu;
}

extern "C" mc3_u32 bound_load_hook(mc3_u32 path, mc3_u32 type, mc3_u32 out1, mc3_u32 out2) {
    if (mc2_city_supported(current_city()) && type == 9u) {
        G()->rsc = G()->tokyo = 0; G()->body = 0; G()->handles = 0;
        G()->err_stage = G()->err_detail = 0;
        mc3_u32 size = 0;
        const mc3_u32 body = build_body(&size);
        if (body) {
            *(volatile mc3_u32 *)out1 = VBASE;
            *(volatile mc3_u32 *)out2 = size;
            mark('R','B','N','1', body, size);
            return body;
        }
        close_stream(G()->rsc);
        close_stream(G()->tokyo);
        if (G()->body) MC3_CALL1(void, ALIGNED_DELETE, mc3_u32)((mc3_u32)G()->body);
        mark('R','B','N','0', G()->err_stage, G()->err_detail);
    }
    return MC3_CALL4(mc3_u32, BOUND_LOAD, mc3_u32, mc3_u32, mc3_u32, mc3_u32)(path, type, out1, out2);
}
MC3_HOOK(BOUND_LOAD_CALL, bound_load_hook);

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main() { }
