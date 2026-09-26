#include "../../payload/mc3_mod.h"

enum {
    STREAM_OPEN = 0x003991F0,
    STREAM_READ = 0x003993A8,
    STREAM_SIZE = 0x003997A8,
    STREAM_CLOSE = 0x00399748,
    RSC0 = 0x30435352,
    CMI0 = 0x30494D43,
    PMD0 = 0x30444D50,
    TEX0 = 0x30584554,
    PAL0 = 0x304C4150,
    MIP0 = 0x3050494D,
    MAX_BLOCKS = 8192,
    READ_CHUNK = 512
};

struct ProbeState {
    mc3_u32 done;
    char path[32];
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

static mc3_u32 fourcc(const mc3_u8 *p)
{
    return (mc3_u32)p[0] | ((mc3_u32)p[1] << 8) |
           ((mc3_u32)p[2] << 16) | ((mc3_u32)p[3] << 24);
}

static const char *rsc_path()
{
    char *p = st()->path;
    p[0]='h'; p[1]='o'; p[2]='s'; p[3]='t'; p[4]='0'; p[5]=':'; p[6]='/';
    p[7]='m'; p[8]='c'; p[9]='2'; p[10]='_'; p[11]='l'; p[12]='o';
    p[13]='s'; p[14]='a'; p[15]='n'; p[16]='g'; p[17]='e'; p[18]='l';
    p[19]='e'; p[20]='s'; p[21]='.'; p[22]='r'; p[23]='s'; p[24]='c';
    p[25]=0;
    return p;
}

static mc3_u32 open_rsc()
{
    return MC3_CALL2(mc3_u32, STREAM_OPEN, const char *, int)
        (rsc_path(), 1);
}

static int read_exact(mc3_u32 stream, void *dst, mc3_u32 bytes)
{
    return MC3_CALL3(mc3_u32, STREAM_READ, mc3_u32, mc3_u32, mc3_u32)
        (stream, (mc3_u32)dst, bytes) == bytes;
}

/* Read a small range by streaming past the preceding bytes; no whole-file
   allocation or seek dependency is needed for this first probe. */
static int read_at(mc3_u32 offset, void *dst, mc3_u32 bytes)
{
    mc3_u32 stream = open_rsc();
    if (!stream) return 0;
    mc3_u8 scratch[READ_CHUNK] __attribute__((aligned(16)));
    mc3_u32 skipped = 0;
    int ok = 1;
    while (skipped < offset) {
        mc3_u32 n = offset - skipped;
        if (n > READ_CHUNK) n = READ_CHUNK;
        if (!read_exact(stream, scratch, n)) { ok = 0; break; }
        skipped += n;
    }
    if (ok) ok = read_exact(stream, dst, bytes);
    MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(stream);
    return ok;
}

static void error(mc3_u32 code)
{
    tag('R','S','C','E'); hex8(code); put('\n');
}

extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main()
{
    ProbeState *const s = st();
    if (s->done) return;
    s->done = 1;

    mc3_u32 stream = open_rsc();
    if (!stream) { error(1); return; }
    const int signed_size = MC3_CALL1(int, STREAM_SIZE, mc3_u32)(stream);
    if (signed_size < 8) {
        MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(stream);
        error(2); return;
    }
    const mc3_u32 file_size = (mc3_u32)signed_size;
    mc3_u8 header[8] __attribute__((aligned(16)));
    if (!read_exact(stream, header, 8u) || le32(header) != RSC0) {
        MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(stream);
        error(3); return;
    }
    const mc3_u32 count = le32(header + 4);
    if (!count || count > MAX_BLOCKS || count > (file_size - 8u) / 8u) {
        MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(stream);
        error(4); return;
    }

    const mc3_u32 table_bytes = count * 8u;
    const mc3_u32 table_end = 8u + table_bytes;
    mc3_u8 *table = (mc3_u8 *)mc3_alloc(table_bytes);
    if (!table) {
        MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(stream);
        error(5); return;
    }
    if (!read_exact(stream, table, table_bytes)) {
        mc3_free(table);
        MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(stream);
        error(6); return;
    }
    MC3_CALL1(void, STREAM_CLOSE, mc3_u32)(stream);

    mc3_u32 cmi_entries = 0, pmd_entries = 0;
    mc3_u32 tex_entries = 0, pal_entries = 0, mip_entries = 0;
    mc3_u32 first_cmi = 0xFFFFFFFFu, first_cmi_size = 0;
    mc3_u32 previous = table_end;
    int valid = 1;
    for (mc3_u32 i = 0; i < count; ++i) {
        const mc3_u8 *e = table + i * 8u;
        const mc3_u32 off = le32(e);
        if (off < previous || off > file_size) { valid = 0; break; }
        const mc3_u32 end = (i + 1u < count) ? le32(table + (i + 1u) * 8u)
                                               : file_size;
        if (end < off || end > file_size) { valid = 0; break; }
        const mc3_u32 size = end - off;
        const mc3_u32 type = fourcc(e + 4);
        if (type == CMI0) {
            ++cmi_entries;
            if (first_cmi == 0xFFFFFFFFu) {
                first_cmi = i;
                first_cmi_size = size;
            }
        } else if (type == PMD0) ++pmd_entries;
        else if (type == TEX0) ++tex_entries;
        else if (type == PAL0) ++pal_entries;
        else if (type == MIP0) ++mip_entries;
        previous = off;
    }
    if (!valid || first_cmi == 0xFFFFFFFFu || first_cmi_size < 64u) {
        mc3_free(table); error(7); return;
    }

    const mc3_u32 cmi_off = le32(table + first_cmi * 8u);
    mc3_u8 cmi[64] __attribute__((aligned(16)));
    if (!read_at(cmi_off, cmi, sizeof(cmi))) {
        mc3_free(table); error(8); return;
    }
    const mc3_u32 handle = le32(cmi + 0x10u);
    if (!(handle & 0x4000u) || !(handle & 0x3FFFu)) {
        mc3_free(table); error(9); return;
    }
    const mc3_u32 pmd_index = (handle & 0x3FFFu) - 1u;
    if (pmd_index >= count || fourcc(table + pmd_index * 8u + 4u) != PMD0) {
        mc3_free(table); error(10); return;
    }
    const mc3_u32 pmd_off = le32(table + pmd_index * 8u);
    const mc3_u32 pmd_end = pmd_index + 1u < count
        ? le32(table + (pmd_index + 1u) * 8u) : file_size;
    const mc3_u32 pmd_size = pmd_end - pmd_off;
    mc3_free(table);

    mc3_u8 pmd_head[16] __attribute__((aligned(16)));
    if (pmd_size < sizeof(pmd_head) || !read_at(pmd_off, pmd_head, sizeof(pmd_head))) {
        error(11); return;
    }

    tag('R','S','C','0'); hex8(file_size); put(' '); hex8(count); put(' ');
    hex8(table_end); put('\n');
    tag('R','S','C','N'); hex8(cmi_entries); put(' '); hex8(pmd_entries); put(' ');
    hex8(tex_entries); put(' '); hex8(pal_entries); put(' '); hex8(mip_entries); put('\n');
    tag('R','S','C','M'); hex8(cmi_off); put(' '); hex8(le32(cmi)); put(' ');
    hex8(le32(cmi + 8)); put(' '); hex8(le32(cmi + 12)); put(' ');
    hex8(handle); put(' '); hex8(pmd_index); put('\n');
    tag('R','S','C','P'); hex8(pmd_off); put(' '); hex8(pmd_size); put(' ');
    hex8(le32(pmd_head)); put(' '); hex8(le32(pmd_head + 4)); put(' ');
    hex8(le32(pmd_head + 8)); put(' '); hex8(le32(pmd_head + 12)); put('\n');
}
