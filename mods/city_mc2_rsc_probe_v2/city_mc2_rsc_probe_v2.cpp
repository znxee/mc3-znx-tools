#include "../../payload/mc3_mod.h"

enum {
    STREAM_OPEN = 0x003991F0,
    STREAM_READ = 0x003993A8,
    STREAM_SIZE = 0x003997A8,
    STREAM_CLOSE = 0x00399748,
    RSC0 = 0x30435352,
    RNT0 = 0x30544E52,
    CMI0 = 0x30494D43,
    PMD0 = 0x30444D50,
    TEX0 = 0x30584554,
    PAL0 = 0x304C4150,
    MIP0 = 0x3050494D,
    MAX_BLOCKS = 8192,
    SKIP_BYTES = 4096,
    RNT_LIMIT = 0x00100000
};

struct ProbeState { mc3_u32 done; char path[40]; };
struct Container {
    mc3_u32 stream, file_size, count, table, table_bytes, table_end;
};
struct Results {
    mc3_u32 cmi, pmd, tex, pal, mip;
    mc3_u32 near_models, far_models, near_handles, far_handles;
    mc3_u32 calls, local_calls, shared_calls, null_calls, bad_calls;
    mc3_u32 named, name_hash;
};

static ProbeState g_state;
static __attribute__((noinline)) ProbeState *st() { return &g_state; }

static void put(char c) { *(volatile unsigned char *)0x1000F180 = (unsigned char)c; }
static void hex8(mc3_u32 v)
{
    for (int i = 28; i >= 0; i -= 4) {
        const mc3_u32 d = (v >> i) & 15u;
        put((char)(d < 10u ? '0' + d : 'A' + d - 10u));
    }
}
static void tag(char a, char b, char c, char d)
{
    put(a); put(b); put(c); put(d); put(' ');
}
static mc3_u32 le32(const mc3_u8 *p)
{
    return (mc3_u32)p[0] | ((mc3_u32)p[1] << 8) |
           ((mc3_u32)p[2] << 16) | ((mc3_u32)p[3] << 24);
}
static mc3_u32 fourcc(const mc3_u8 *p) { return le32(p); }
static mc3_u32 entry_offset(const Container *c, mc3_u32 i)
{ return le32((const mc3_u8 *)c->table + i * 8u); }
static mc3_u32 entry_type(const Container *c, mc3_u32 i)
{ return fourcc((const mc3_u8 *)c->table + i * 8u + 4u); }
static mc3_u32 entry_end(const Container *c, mc3_u32 i)
{ return i + 1u < c->count ? entry_offset(c, i + 1u) : c->file_size; }

static void set_path(const char *src)
{
    char *dst = st()->path;
    mc3_u32 i = 0;
    while (src[i] && i < sizeof(st()->path) - 1u) { dst[i] = src[i]; ++i; }
    dst[i] = 0;
}
static mc3_u32 open_path(const char *path)
{
    set_path(path);
    return MC3_CALL2(mc3_u32, STREAM_OPEN, const char *, int)(st()->path, 1);
}
static int read_exact(mc3_u32 stream, void *dst, mc3_u32 bytes)
{
    return MC3_CALL3(mc3_u32, STREAM_READ, mc3_u32, mc3_u32, mc3_u32)
        (stream, (mc3_u32)dst, bytes) == bytes;
}
static int skip_exact(mc3_u32 stream, mc3_u32 bytes, mc3_u8 *scratch)
{
    while (bytes) {
        const mc3_u32 n = bytes > SKIP_BYTES ? SKIP_BYTES : bytes;
        if (!read_exact(stream, scratch, n)) return 0;
        bytes -= n;
    }
    return 1;
}
static void close_container(Container *c)
{
    if (c->stream) MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(c->stream);
    c->stream = 0;
    mc3_free((void *)c->table);
    c->table = 0;
}

/* Leaves the stream positioned immediately after the directory table. */
static int open_container(const char *path, mc3_u32 magic, Container *c)
{
    mc3_u8 head[8] __attribute__((aligned(16)));
    c->stream = open_path(path);
    if (!c->stream) return 1;
    const int signed_size = MC3_CALL1(int, STREAM_SIZE, mc3_u32)(c->stream);
    if (signed_size < 8) return 2;
    c->file_size = (mc3_u32)signed_size;
    if (!read_exact(c->stream, head, sizeof(head)) || le32(head) != magic)
        return 3;
    c->count = le32(head + 4);
    if (!c->count || c->count > MAX_BLOCKS ||
        c->count > (c->file_size - 8u) / 8u) return 4;
    c->table_bytes = c->count * 8u;
    c->table_end = 8u + c->table_bytes;
    c->table = (mc3_u32)mc3_alloc(c->table_bytes);
    if (!c->table) return 5;
    if (!read_exact(c->stream, (void *)c->table, c->table_bytes)) return 6;
    mc3_u32 previous = c->table_end;
    for (mc3_u32 i = 0; i < c->count; ++i) {
        const mc3_u32 off = entry_offset(c, i);
        if (off < previous || off > c->file_size) return 7;
        previous = off;
    }
    return 0;
}

static int handle_type(const Container *city, const Container *shared,
                       mc3_u32 handle, mc3_u32 wanted, int *is_shared)
{
    if (!handle) return 1; /* legal empty slot / untextured DMA CALL */
    if (!(handle & 0x3FFFu)) return 0;
    const Container *box = (handle & 0x4000u) ? city : shared;
    const mc3_u32 index = (handle & 0x3FFFu) - 1u;
    if (index >= box->count || entry_type(box, index) != wanted) return 0;
    if (is_shared) *is_shared = box == shared;
    return 1;
}

/* RNT0 records bind human-readable CMI0 names to RSC entry handles. */
static int load_names(const Container *city, mc3_u8 **named_out,
                      mc3_u32 *name_count, mc3_u32 *hash_out,
                      mc3_u32 *rnt_size_out, mc3_u32 *rnt_records_out)
{
    mc3_u32 stream = open_path("host0:/mc2_losangeles.rnt");
    if (!stream) return 1;
    const int signed_size = MC3_CALL1(int, STREAM_SIZE, mc3_u32)(stream);
    if (signed_size < 24 || (mc3_u32)signed_size > RNT_LIMIT) {
        MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(stream); return 2;
    }
    const mc3_u32 size = (mc3_u32)signed_size;
    mc3_u8 *data = (mc3_u8 *)mc3_alloc(size);
    if (!data) { MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(stream); return 3; }
    const int got = read_exact(stream, data, size);
    MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(stream);
    if (!got || fourcc(data) != RNT0) { mc3_free(data); return 4; }
    const mc3_u32 records = le32(data + 4);
    if (records > (size - 24u) / 12u) { mc3_free(data); return 5; }
    const mc3_u32 strings = 24u + records * 12u;
    mc3_u8 *named = (mc3_u8 *)mc3_alloc(city->count);
    if (!named) { mc3_free(data); return 6; }
    for (mc3_u32 i = 0; i < city->count; ++i) named[i] = 0;

    mc3_u32 count = 0, hash = 2166136261u;
    for (mc3_u32 i = 0; i < records; ++i) {
        const mc3_u8 *r = data + 24u + i * 12u;
        if (fourcc(r + 4) != CMI0) continue;
        const mc3_u32 off = le32(r), handle = le32(r + 8);
        if (off < strings || off >= size || !(handle & 0x4000u) ||
            !(handle & 0x3FFFu)) { mc3_free(named); mc3_free(data); return 7; }
        const mc3_u32 index = (handle & 0x3FFFu) - 1u;
        if (index >= city->count || entry_type(city, index) != CMI0 || named[index]) {
            mc3_free(named); mc3_free(data); return 8;
        }
        mc3_u32 end = off;
        while (end < size && data[end]) {
            hash = (hash ^ data[end]) * 16777619u;
            ++end;
        }
        if (end == off || end == size) { mc3_free(named); mc3_free(data); return 9; }
        hash = (hash ^ 0u) * 16777619u;
        named[index] = 1;
        ++count;
    }
    mc3_free(data);
    *named_out = named;
    *name_count = count;
    *hash_out = hash;
    *rnt_size_out = size;
    *rnt_records_out = records;
    return 0;
}

static int scan_pmd(mc3_u32 stream, mc3_u32 size, const Container *city,
                    const Container *shared, mc3_u8 *scratch, Results *r)
{
    mc3_u32 used = 0;
    int terminal = 0;
    mc3_u32 last_id = 0xFFFFFFFFu;
    while (used < size) {
        mc3_u8 h[16] __attribute__((aligned(16)));
        if (size - used < sizeof(h) || !read_exact(stream, h, sizeof(h))) return 1;
        used += sizeof(h);
        const mc3_u32 lo = le32(h), addr = le32(h + 4);
        const mc3_u32 id = (lo >> 28) & 7u, qwc = lo & 0xFFFFu;
        const mc3_u32 payload = qwc * 16u;
        if (payload > size - used) return 2;
        if (id == 5u) {
            ++r->calls;
            if (!addr) ++r->null_calls;
            else {
                int is_shared = 0;
                if (!handle_type(city, shared, addr, TEX0, &is_shared)) ++r->bad_calls;
                else if (is_shared) ++r->shared_calls;
                else ++r->local_calls;
            }
        } else if (id != 6u) return 3; /* measured PMD chains: call ... ret */
        if (!skip_exact(stream, payload, scratch)) return 4;
        used += payload;
        last_id = id;
        if (id == 6u) {
            terminal = 1;
            if (used != size) return 5;
            break;
        }
    }
    ++r->pmd;
    return terminal && last_id == 6u ? 0 : 6;
}

/* One sequential pass over the RSC; no per-model rewind or whole-file buffer. */
static int scan_city(Container *city, const Container *shared,
                     const mc3_u8 *named, mc3_u8 *scratch, Results *r)
{
    mc3_u32 pos = city->table_end;
    for (mc3_u32 i = 0; i < city->count; ++i) {
        const mc3_u32 off = entry_offset(city, i), end = entry_end(city, i);
        if (off < pos || end < off || end > city->file_size) return 1;
        if (!skip_exact(city->stream, off - pos, scratch)) return 2;
        pos = off;
        const mc3_u32 type = entry_type(city, i), size = end - off;
        if (type == CMI0) {
            mc3_u8 cmi[64] __attribute__((aligned(16)));
            if (size < sizeof(cmi) || !read_exact(city->stream, cmi, sizeof(cmi))) return 3;
            pos += sizeof(cmi);
            ++r->cmi;
            if (!named[i]) return 4;
            int near = 0, far = 0;
            for (mc3_u32 j = 0; j < 4; ++j) {
                const mc3_u32 h0 = le32(cmi + 0x10u + j * 8u);
                const mc3_u32 h1 = le32(cmi + 0x14u + j * 8u);
                const mc3_u32 count = (h0 != 0u) + (h1 != 0u);
                if (j < 2u) r->near_handles += count;
                else r->far_handles += count;
                if (count) { if (j < 2u) near = 1; else far = 1; }
                if ((h0 && !handle_type(city, shared, h0, PMD0, 0)) ||
                    (h1 && !handle_type(city, shared, h1, PMD0, 0))) return 5;
            }
            r->near_models += (mc3_u32)near;
            r->far_models += (mc3_u32)far;
        } else if (type == PMD0) {
            const int err = scan_pmd(city->stream, size, city, shared, scratch, r);
            if (err) return 10u + (mc3_u32)err;
            pos = end;
        } else {
            if (!skip_exact(city->stream, size, scratch)) return 6;
            pos = end;
            if (type == TEX0) ++r->tex;
            else if (type == PAL0) ++r->pal;
            else if (type == MIP0) ++r->mip;
        }
    }
    return pos == city->file_size ? 0 : 7;
}

static void emit(const Container *city, const Container *shared,
                 mc3_u32 rnt_size, mc3_u32 rnt_records, const Results *r)
{
    tag('R','S','C','0'); hex8(city->file_size); put(' '); hex8(city->count);
    put(' '); hex8(city->table_end); put('\n');
    tag('R','S','C','N'); hex8(r->cmi); put(' '); hex8(r->pmd); put(' ');
    hex8(r->tex); put(' '); hex8(r->pal); put(' '); hex8(r->mip); put('\n');
    tag('R','N','T','0'); hex8(rnt_size); put(' '); hex8(rnt_records); put(' ');
    hex8(r->named); put(' '); hex8(r->name_hash); put('\n');
    tag('R','S','C','V'); hex8(r->near_models); put(' '); hex8(r->far_models);
    put(' '); hex8(r->near_handles); put(' '); hex8(r->far_handles); put('\n');
    tag('T','X','T','B'); hex8(shared->file_size); put(' '); hex8(shared->count);
    put(' '); hex8(shared->table_end); put('\n');
    tag('R','S','C','X'); hex8(r->calls); put(' '); hex8(r->local_calls); put(' ');
    hex8(r->shared_calls); put(' '); hex8(r->null_calls); put(' ');
    hex8(r->bad_calls); put('\n');
}

static void error(mc3_u32 stage, mc3_u32 code)
{ tag('R','S','C','F'); hex8(stage); put(' '); hex8(code); put('\n'); }

static void run_probe()
{
    Container city = {0}, shared = {0};
    Results r = {0};
    mc3_u8 *named = 0, *scratch = 0;
    mc3_u32 rnt_size = 0, rnt_records = 0, name_count = 0, name_hash = 0;
    mc3_u32 stage = 0, code = 0;

    stage = 1; code = (mc3_u32)open_container("host0:/mc2_losangeles.rsc", RSC0, &city);
    if (code) goto fail;
    stage = 2; code = (mc3_u32)open_container("host0:/textures_original.rsc", RSC0, &shared);
    if (code) goto fail;
    stage = 3; code = (mc3_u32)load_names(&city, &named, &name_count,
                                          &name_hash, &rnt_size, &rnt_records);
    if (code) goto fail;
    r.named = name_count; r.name_hash = name_hash;
    stage = 4; scratch = (mc3_u8 *)mc3_alloc(SKIP_BYTES);
    if (!scratch) { code = 1; goto fail; }
    stage = 5; code = (mc3_u32)scan_city(&city, &shared, named, scratch, &r);
    if (code) goto fail;
    if (r.cmi != name_count) { stage = 6; code = r.cmi; goto fail; }
    if (r.pmd == 0 || r.bad_calls) { stage = 7; code = r.bad_calls; goto fail; }
    emit(&city, &shared, rnt_size, rnt_records, &r);
    goto cleanup;

fail:
    error(stage, code);
cleanup:
    if (city.stream) close_container(&city);
    if (shared.stream) close_container(&shared);
    mc3_free(named);
    mc3_free(scratch);
}

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    ProbeState *const s = st();
    if (s->done) return;
    s->done = 1;
    run_probe();
}
