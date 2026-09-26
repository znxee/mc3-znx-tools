// MC2 scene packets through MC3's lowPsxGfx::AddVCLCall. No direct DMA writes.
#include "../../payload/mc3_mod.h"
#include "../../payload/mc3_bootargs.h"

enum {
    HOOK = 0x257AEC, SET_CAMERA = 0x2A5040, ADD = 0x526A60,
    FLUSH = 0x546C20, OPEN = 0x3991F0, READ = 0x3993A8,
    SIZE = 0x3997A8, CLOSE = 0x399748, VIEWPORT = 0x70E50C,
    MAGIC = 0x3251434D, VCLQ = 0x514C4356,
    BUFFER_BYTES = 5 * 1024 * 1024, MAX_BYTES = BUFFER_BYTES - 16
};

struct State {
    mc3_u32 base, bytes, ns, np, stab, ptab, mat, frame, calls, err, limit;
    mc3_u32 raw, tile, pending_tile, switch_frame, failed_tile, retry_frame, cull;
};
static State state;
static __attribute__((noinline)) State *st() { return &state; }
static void put(char c) { *(volatile unsigned char *)0x1000F180 = (unsigned char)c; }
static void hex8(mc3_u32 v) {
    for (int i = 28; i >= 0; i -= 4) {
        unsigned d = (v >> i) & 15;
        put((char)(d < 10 ? 48 + d : 55 + d));
    }
}
static void tag(char a,char b,char c,char d) { put(a);put(b);put(c);put(d);put(' '); }
static mc3_u32 word(mc3_u32 p) { return *(volatile mc3_u32 *)p; }
static float flt(const mc3_u32 *p) {
    union { mc3_u32 u; float f; } v; v.u = *p; return v.f;
}
static mc3_u32 bits(float f) {
    union { mc3_u32 u; float f; } v; v.f = f; return v.u;
}
static int ptr(mc3_u32 p) { return p >= 0x100000u && p < 0x2000000u && !(p & 3u); }
static mc3_u32 number(const char *p) {
    mc3_u32 n = 0;
    if (!p || *p < '0' || *p > '9') return 0;
    while (*p >= '0' && *p <= '9') {
        n = n * 10u + (mc3_u32)(*p++ - '0');
        if (n > 4096u) return 4096u;
    }
    return n;
}
static int packet(mc3_u32 base, mc3_u32 bytes, mc3_u32 off, mc3_u32 len) {
    if (off < 64u || (off & 15u) || len < 32u || (len & 15u) ||
        off > bytes || len > bytes - off) return 0;
    const mc3_u32 p = base + off;
    return word(p + 4u) == VCLQ && word(p + 12u) == len - 16u &&
           (word(p) & 0xFFFFu) == (len - 16u) / 16u &&
           (word(p) & 0xFFFFu) <= 0x3FFFu;
}
static char g_tile_path[] = "host0:/mc2t_0000.vcl";
static __attribute__((noinline)) char *tile_path() { return g_tile_path; }
static __attribute__((noinline)) int load(mc3_u32 tile) {
    char *path = tile_path();
    const mc3_u32 tx = tile >> 4, tz = tile & 15u;
    path[12] = (char)('0' + tx / 10u); path[13] = (char)('0' + tx % 10u);
    path[14] = (char)('0' + tz / 10u); path[15] = (char)('0' + tz % 10u);
    mc3_u32 h = MC3_CALL2(mc3_u32, OPEN, const char *, int)(path, 1);
    if (!h) { st()->err = 1; return 0; }
    int n = MC3_CALL1(int, SIZE, mc3_u32)(h);
    if (n < 64 || n > MAX_BYTES) {
        st()->err = 2; MC3_CALL1(void, CLOSE, mc3_u32)(h); return 0;
    }
    mc3_u32 raw = st()->raw;
    if (!raw) {
        raw = (mc3_u32)mc3_alloc(BUFFER_BYTES);
        if (!ptr(raw) || raw + BUFFER_BYTES >= 0x2000000u) {
            st()->err = 3; MC3_CALL1(void, CLOSE, mc3_u32)(h); return 0;
        }
        st()->raw = raw;
    }
    mc3_u32 base = (raw + 15u) & ~15u;
    mc3_u32 got = MC3_CALL3(mc3_u32, READ, mc3_u32, mc3_u32, mc3_u32)
                      (h, base, (mc3_u32)n);
    MC3_CALL1(void, CLOSE, mc3_u32)(h);
    if (got != (mc3_u32)n || word(base) != MAGIC || word(base+4) != 1u ||
        word(base+8) != (mc3_u32)n) {
        st()->err = 4; return 0;
    }
    mc3_u32 ns=word(base+12), np=word(base+16), so=word(base+20);
    mc3_u32 po=word(base+24), mo=word(base+28), size=(mc3_u32)n;
    if (!ns || ns>64u || !np || np>4096u || so>size || ns*8u>size-so ||
        po>size || np*32u>size-po || mo>size || 64u>size-mo) {
        st()->err=5; return 0;
    }
    for (mc3_u32 i=0;i<ns;++i) {
        mc3_u32 off=word(base+so+i*8u), len=word(base+so+i*8u+4u);
        if (!packet(base,size,off,len)) {
            st()->err=6; return 0;
        }
    }
    for (mc3_u32 i=0;i<np;++i) {
        mc3_u32 off=word(base+po+i*32u), len=word(base+po+i*32u+4u);
        if (!packet(base,size,off,len)) {
            st()->err=7; return 0;
        }
    }
    State *s=st();
    s->tile=tile; s->err=0;
    s->base=base;s->bytes=size;s->ns=ns;s->np=np;
    s->stab=so;s->ptab=po;s->mat=mo;
    s->limit=number(mc3_bootarg(MC3_ID('m','c','2','l')));
    s->cull=number(mc3_bootarg(MC3_ID('m','c','2','c')));
    tag('Q','L','O','D');hex8(tile);put(' ');hex8(size);put(' ');hex8(ns);put(' ');
    hex8(np);put(' ');hex8(s->limit);put('\n');
    tag('Q','C','U','L');hex8(s->cull);put('\n');
    return 1;
}
static int matrix(mc3_u32 cam, float out[16]) {
    mc3_u32 vp=word(VIEWPORT);
    if (!ptr(cam) || (cam&15u) || !ptr(vp)) return 0;
    float a[3][3], pos[3];
    for (int j=0;j<3;++j) {
        for (int i=0;i<3;++i) a[j][i]=flt((const mc3_u32 *)(cam+j*12+i*4));
        pos[j]=flt((const mc3_u32 *)(cam+36+j*4));
    }
    float v[16]={
        a[0][0],a[1][0],a[2][0],0.0f,
        a[0][1],a[1][1],a[2][1],0.0f,
        a[0][2],a[1][2],a[2][2],0.0f,
        -(pos[0]*a[0][0]+pos[1]*a[0][1]+pos[2]*a[0][2]),
        -(pos[0]*a[1][0]+pos[1]*a[1][1]+pos[2]*a[1][2]),
        -(pos[0]*a[2][0]+pos[1]*a[2][1]+pos[2]*a[2][2]),1.0f
    };
    float p[16];
    for (int i=0;i<16;++i) p[i]=flt((const mc3_u32 *)(vp+0x10+i*4));
    if (!(p[0]>0.2f && p[0]<10.0f && p[5]>0.2f && p[5]<10.0f &&
          p[11]<-0.5f && p[11]>-1.5f)) return 0;
    for (int r=0;r<4;++r) for (int c=0;c<4;++c) {
        float sum=0;
        for (int k=0;k<4;++k) sum+=v[r*4+k]*p[k*4+c];
        out[r*4+c]=sum;
    }
    return 1;
}
static int visible(const float m[16], const mc3_u32 *e) {
    float lo[3]={flt(e+2),flt(e+3),flt(e+4)};
    float hi[3]={flt(e+5),flt(e+6),flt(e+7)};
    mc3_u32 all=63u;int front=1;
    float x0=1e6f,x1=-1e6f,y0=1e6f,y1=-1e6f;
    for (int c=0;c<8;++c) {
        float x=(c&1)?hi[0]:lo[0],y=(c&2)?hi[1]:lo[1],z=(c&4)?hi[2]:lo[2];
        float cx=x*m[0]+y*m[4]+z*m[8]+m[12];
        float cy=x*m[1]+y*m[5]+z*m[9]+m[13];
        float cz=x*m[2]+y*m[6]+z*m[10]+m[14];
        float cw=x*m[3]+y*m[7]+z*m[11]+m[15];
        mc3_u32 f=0;
        if(cx>cw)f|=1;if(cx<-cw)f|=2;if(cy>cw)f|=4;
        if(cy<-cw)f|=8;if(cz>cw)f|=16;if(cz<-cw)f|=32;
        all&=f;
        if(cw>0.0001f) {
            float nx=cx/cw,ny=cy/cw;
            if(nx<x0)x0=nx;if(nx>x1)x1=nx;
            if(ny<y0)y0=ny;if(ny>y1)y1=ny;
        } else front=0;
    }
    return !all && (!front || x1-x0>=0.015625f || y1-y0>=0.015625f);
}
static mc3_u32 tile_for(mc3_u32 cam) {
    if (!ptr(cam)) return st()->tile;
    const float x = flt((const mc3_u32 *)(cam + 36u));
    const float z = flt((const mc3_u32 *)(cam + 44u));
    int tx = (int)((x + 2000.0f) / 500.0f);
    int tz = (int)((z + 1800.0f) / 500.0f);
    if (tx < 0) tx = 0; if (tx > 7) tx = 7;
    if (tz < 0) tz = 0; if (tz > 6) tz = 6;
    return ((mc3_u32)tx << 4) | (mc3_u32)tz;
}
extern "C" void scene_hook(mc3_u32 cam) {
    MC3_CALL1(void,SET_CAMERA,mc3_u32)(cam);
    State *s=st();++s->frame;
    const mc3_u32 target_tile = tile_for(cam);
    if (s->base && s->tile != target_tile) {
        if (s->pending_tile != target_tile) {
            s->pending_tile = target_tile;
            s->switch_frame = s->frame + 8u;
        }
        // Old packets must leave the game's graphics queue before reuse.
        if (s->frame < s->switch_frame) return;
        s->base = 0;
    } else if (s->base) {
        s->pending_tile = s->tile;
    }
    if ((!s->base || s->tile != target_tile) &&
        !(s->failed_tile == target_tile && s->frame < s->retry_frame)) {
        if (load(target_tile)) {
            s->failed_tile = 0xFFFFFFFFu;
        } else {
            s->failed_tile = target_tile;
            s->retry_frame = s->frame + 120u;
            tag('Q','E','R','R');hex8(target_tile);put(' ');hex8(s->err);put('\n');
        }
    }
    if(!s->base) return;
    float m[16];if(!matrix(cam,m))return;
    mc3_u32 *dst=(mc3_u32 *)(s->base+s->mat);
    for(int i=0;i<16;++i)dst[i]=bits(m[i]);
    MC3_CALL1(void,FLUSH,int)(0);
    __asm__ volatile("sync.l; sync.p" ::: "memory");
    for(mc3_u32 i=0;i<s->ns;++i) {
        const mc3_u32 *e=(const mc3_u32 *)(s->base+s->stab+i*8u);
        MC3_CALL1(void,ADD,mc3_u32)(s->base+e[0]);++s->calls;
    }
    mc3_u32 seen=0,sent=0;
    for(mc3_u32 i=0;i<s->np;++i) {
        const mc3_u32 *e=(const mc3_u32 *)(s->base+s->ptab+i*32u);
        if(s->cull && !visible(m,e))continue;
        ++seen;
        if(s->limit && sent>=s->limit)continue;
        MC3_CALL1(void,ADD,mc3_u32)(s->base+e[0]);
        ++s->calls;++sent;
    }
    if(s->frame<=12u || !(s->frame%120u)) {
        tag('Q','S','C','N');hex8(s->frame);put(' ');hex8(s->tile);put(' ');
        hex8(seen);put(' ');
        hex8(sent);put(' ');hex8(s->calls);put('\n');
    }
}
MC3_HOOK(HOOK,scene_hook);
