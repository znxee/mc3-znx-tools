// FreeCam from CinematicClub, original by AlgumCorrupto (Paulo).
// Native SLUS-21355 port by modderG, 2026-09-05. GPL-3.0, see LICENSE.md.
#include "../../payload/mc3_mod.h"
#include "freecam_math.h"
#include "freecam_keys.h"
#include "freecam_coords.h"
#include <stddef.h>

struct FcState {
    mc3_u32 magic, version, size, status; // 0..15; status 1=installed, 2=conflict
    mc3_u32 active; float fov; mc3_u32 camera, frame; // 16..31, asm ABI
    volatile mc3_u32 host_seq, host_keys, host_x, host_y, host_focus, host_beat;
    mc3_u32 rx_keys, rx_focus; // 56..63: last fully committed host input
    FcMatrix matrix, live;
    FcVec velocity;
    float speed;
    mc3_u32 smoothing, previous, rx_seq, rx_x, rx_y, rx_beat;
    float rx_age;
    mc3_u32 live_camera, live_frame;
    float live_fov;
};
static_assert(offsetof(FcState,active)==16 && offsetof(FcState,fov)==20 &&
              offsetof(FcState,camera)==24 && offsetof(FcState,matrix)==64,
              "freecam assembly / PINE ABI");
extern "C" { FcState freecam_state __attribute__((aligned(16))); }
static __attribute__((noinline)) FcState *state() { return &freecam_state; }
struct FcCoordState {
    mc3_u32 font, draws;
    int x_tenths, y_tenths, z_tenths;
};
static FcCoordState g_coord;
static __attribute__((noinline)) FcCoordState *coord_state() { return &g_coord; }
static mc3_u32 word(mc3_u32 a) { return *(volatile mc3_u32 *)a; }
static bool ptr(mc3_u32 a,mc3_u32 n=4) {
    return a>=0x00100000u && a<=0x02000000u-n && !(a&3);
}
static bool number(float f) { return f>-fc_float(0x49742400u) && f<fc_float(0x49742400u); }
static bool valid_matrix(const FcMatrix &m) {
    const float *p=(const float *)&m;
    for (int i=0;i<12;++i) if (!number(p[i])) return false;
    return fc_dot(m.right,m.right)>fc_float(0x3f000000u) && fc_dot(m.right,m.right)<fc_float(0x3fc00000u) &&
           fc_dot(m.up,m.up)>fc_float(0x3f000000u) && fc_dot(m.up,m.up)<fc_float(0x3fc00000u) &&
           fc_dot(m.back,m.back)>fc_float(0x3f000000u) && fc_dot(m.back,m.back)<fc_float(0x3fc00000u);
}

extern "C" mc3_u32 freecam_camera_entry(mc3_u32 object) {
    // Preserve retail behavior when inactive, and obtain the live transform
    // from the actual caller rather than scanning heap signatures.
    mc3_u32 original=MC3_CALL1(mc3_u32,0x00515790u,mc3_u32)(object);
    FcState *s=state();
    if (!ptr(object,12) || !ptr(original,sizeof(FcMatrix))) return original;
    mc3_u32 view=word(object+8);
    if (!ptr(view,0x34)) return original;
    mc3_u32 camera=word(view+0x30);
    if (!ptr(camera,0x94) || !valid_matrix(*(const FcMatrix *)original)) return original;
    if (word(0x006144BCu)!=0) { s->active=0; return original; }
    if (s->active && camera!=s->camera) s->active=0;
    s->live=*(const FcMatrix *)original;
    s->live_camera=camera; s->live_frame=s->frame;
    s->live_fov=*(const float *)(camera+0x88);
    return s->active ? (mc3_u32)&s->matrix : original;
}

extern "C" void freecam_camera_gate();
extern "C" void freecam_fov_gate();
extern "C" void freecam_draw_frame(mc3_u32 game);
__asm__(
    ".text\n.set noreorder\n.align 4\n"
    ".globl freecam_camera_gate\nfreecam_camera_gate:\n"
    "j freecam_camera_entry\nnop\n"
    // Discoverable header at the destination of JAL 0x001A157C.
    ".word 0x4D433346\n.word freecam_state\n.word 1\n.word 64\n"
    ".globl freecam_fov_gate\nfreecam_fov_gate:\n"
    "lui $8, %hi(freecam_state)\naddiu $8,$8,%lo(freecam_state)\n"
    "lw $9,16($8)\nbeq $9,$zero,1f\nlwc1 $f12,0x88($v1)\n"
    "lw $9,24($8)\nbne $9,$v1,1f\nnop\n"
    "lwc1 $f12,20($8)\n"
    "1: jr $ra\nnop\n.set reorder\n");

struct txt_cursor {
    mc3_u32 flags;
    int cursor_x, cursor_y, clip_top, clip_right, clip_left, clip_bottom;
    int first_character, last_character, line_height;
    int measured_width, measured_height;
    float scale_x, scale_y;
    mc3_u32 colour;
};
struct text_layout { mc3_u32 x, y, sx, sy; };
extern "C" void freecam_draw_text(mc3_u32, const mc3_u16 *, txt_cursor *,
                                  const text_layout *);
__asm__(
    ".set noreorder\n.text\n.align 3\n"
    ".globl freecam_draw_text\nfreecam_draw_text:\n"
    "addiu $sp,$sp,-16\nsd $ra,0($sp)\n"
    "lwc1 $f12,0($a3)\nlwc1 $f13,4($a3)\n"
    "lwc1 $f14,8($a3)\nlwc1 $f15,12($a3)\n"
    "move $a3,$zero\nmove $8,$zero\n"
    "lui $t9,0x002b\nori $t9,$t9,0xc8f0\njalr $t9\nnop\n"
    "ld $ra,0($sp)\njr $ra\naddiu $sp,$sp,16\n"
    ".set reorder\n");

typedef void (*asset_push_fn)(mc3_u32,const char *);
typedef void (*asset_pop_fn)(mc3_u32);
static mc3_u32 load_coordinate_font()
{
    const mc3_u32 assets=word(0x006D557Cu);
    if (!ptr(assets,4)) return 0;
    const mc3_u32 vt=word(assets);
    if (!ptr(vt,0x18)) return 0;
    const mc3_u32 push=word(vt+0x10),pop=word(vt+0x14);
    if (!ptr(push,4) || !ptr(pop,4)) return 0;
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
static int coordinate_tenths(float value)
{
    if (!(value>-fc_float(0x47C35000u) && value<fc_float(0x47C35000u)))
        return 0;
    const bool negative=value<0;
    const float magnitude=negative?-value:value;
    unsigned tenths=(unsigned)(magnitude*fc_float(0x41200000u)+
                                fc_float(0x3F000000u));
    if (tenths>999999u) tenths=999999u;
    return negative && tenths ? -(int)tenths : (int)tenths;
}
static void draw_coordinate_line(mc3_u32 font,mc3_u16 axis,int tenths,
                                 mc3_u32 y)
{
    mc3_u16 text[16];
    fc_format_coord(axis,tenths,text);
    txt_cursor cursor={};
    cursor.flags=1; cursor.last_character=0x7FFFFFFF;
    cursor.scale_x=cursor.scale_y=fc_float(0x3F800000u);
    cursor.colour=0x80FFFFFFu;
    // x=52 leaves a real ~20 px margin after the widescreen HUD transform.
    text_layout layout={0x42500000u,y,0x3EE66666u,0x3EE66666u};
    freecam_draw_text(font,text,&cursor,&layout);
}
static void draw_coordinates(FcState *s)
{
    if (!s->active) return;
    FcCoordState *c=coord_state();
    if (!c->font) c->font=load_coordinate_font();
    if (!ptr(c->font,4)) return;
    c->x_tenths=coordinate_tenths(s->matrix.pos.x);
    c->y_tenths=coordinate_tenths(s->matrix.pos.y);
    c->z_tenths=coordinate_tenths(s->matrix.pos.z);
    const mc3_u32 old_view=word(0x0070E50Cu),ui_view=word(0x0070E508u);
    if (!ptr(old_view,4) || !ptr(ui_view,4)) return;
    MC3_CALL1(void,0x00527B50u,mc3_u32)(ui_view);
    draw_coordinate_line(c->font,'X',c->x_tenths,0x41C00000u);
    draw_coordinate_line(c->font,'Y',c->y_tenths,0x42200000u);
    draw_coordinate_line(c->font,'Z',c->z_tenths,0x42500000u);
    MC3_CALL1(void,0x00527B50u,mc3_u32)(old_view);
    ++c->draws;
}
extern "C" void freecam_draw_frame(mc3_u32 game)
{
    MC3_CALL1(void,0x001A2998u,mc3_u32)(game);
    draw_coordinates(state());
}

static bool install(FcState *s) {
    mc3_u32 cam_jal=0x0C000000u|(((mc3_u32)freecam_camera_gate>>2)&0x03FFFFFFu);
    mc3_u32 fov_jal=0x0C000000u|(((mc3_u32)freecam_fov_gate>>2)&0x03FFFFFFu);
    mc3_u32 draw_jal=0x0C000000u|(((mc3_u32)freecam_draw_frame>>2)&0x03FFFFFFu);
    mc3_u32 c=word(0x001A157C),f=word(0x0031F2A4);
    mc3_u32 d0=word(0x001A24D0),d1=word(0x001A2618),d2=word(0x001A2740);
    if ((c!=0x0C1455E4u && c!=cam_jal) || (f!=0xC46C0088u && f!=fov_jal) ||
        (d0!=0x0C068A66u && d0!=draw_jal) ||
        (d1!=0x0C068A66u && d1!=draw_jal) ||
        (d2!=0x0C068A66u && d2!=draw_jal) ||
        word(0x001A1580)!=0x8C44006Cu || word(0x0031F2A8)!=0x92020048u ||
        word(0x001A24D4)!=0x0220202Du || word(0x001A261C)!=0x0220202Du ||
        word(0x001A2744)!=0x0220202Du) {
        s->active=0; s->status=2; return false;
    }
    if (c!=cam_jal || f!=fov_jal ||
        d0!=draw_jal || d1!=draw_jal || d2!=draw_jal) {
        *(volatile mc3_u32 *)0x001A157C=cam_jal;
        *(volatile mc3_u32 *)0x0031F2A4=fov_jal;
        *(volatile mc3_u32 *)0x001A24D0=draw_jal;
        *(volatile mc3_u32 *)0x001A2618=draw_jal;
        *(volatile mc3_u32 *)0x001A2740=draw_jal;
        MC3_CALL1(void,0x00546C20u,int)(0);
        MC3_CALL1(void,0x00546C20u,int)(2);
    }
    s->status=1; return true;
}
static mc3_u32 keyboard() {
    if (!*(volatile mc3_u8 *)0x0062314Eu || !*(volatile mc3_u8 *)0x00615A38u ||
        !*(volatile mc3_u8 *)0x00615A39u) return 0;
    // 0x234698 clears the menu bitmap every read and fills only new/repeat
    // events. It is NOT a held-key bitmap. sceUsbKb's decoder (0x540890)
    // retains the full current HID report at 0x6F8EC8 after filtering events:
    // +0 LED, +4 modifiers, +8 count, +12 u16 raw USB usages (up to 64).
    // Keep reading this snapshot between USB packets; a release updates the
    // list, and disconnect is rejected by the availability guards above.
    return fc_usb_held(word(0x006F8ECCu),
        (const volatile mc3_u16 *)0x006F8ED4u,word(0x006F8ED0u));
}
static float axis(mc3_u32 keys,mc3_u32 pos,mc3_u32 neg) {
    return (keys&pos ? fc_float(0x3f800000u):fc_float(0x0u))-(keys&neg ? fc_float(0x3f800000u):fc_float(0x0u));
}
static void capture(FcState *s) {
    s->matrix=s->live; s->camera=s->live_camera;
    s->fov=fc_clamp(s->live_fov,fc_float(0x41200000u),fc_float(0x42f00000u));
    s->velocity={0,0,0}; s->active=1;
}
extern "C" void mod_main() __attribute__((section(".text.start")));
extern "C" void mod_main() {
    FcState *s=state();
    if (s->magic!=0x4D433346u) {
        // .bss is already zeroed by the module packer; avoid global constructors.
        s->version=1; s->size=sizeof(FcState); s->speed=fc_float(0x41c80000u);
        s->fov=fc_float(0x42200000u); s->rx_age=fc_float(0x3f800000u); s->magic=0x4D433346u;
    }
    if (!install(s)) return;
    ++s->frame;
    float dt=*(volatile float *)0x00618E20u;
    if (!(dt>fc_float(0x38d1b717u) && dt<fc_float(0x3dcccccdu))) dt=fc_float(0x3f800000u)/fc_float(0x42700000u);
    mc3_u32 keys=keyboard(); float dx=0,dy=0;
    s->rx_age+=dt;
    mc3_u32 seq=s->host_seq;
    if (!(seq&1u) && seq!=s->rx_seq) {
        mc3_u32 k=s->host_keys,x=s->host_x,y=s->host_y;
        mc3_u32 focus=s->host_focus,beat=s->host_beat;
        __asm__ volatile("" ::: "memory");
        if (seq==s->host_seq) {
            if (s->rx_age<fc_float(0x3f000000u) && focus) {
                dx=fc_clamp((float)(int)(x-s->rx_x),-fc_float(0x43480000u),fc_float(0x43480000u));
                dy=fc_clamp((float)(int)(y-s->rx_y),-fc_float(0x43480000u),fc_float(0x43480000u));
            }
            if (beat!=s->rx_beat) s->rx_age=0;
            s->rx_seq=seq; s->rx_x=x; s->rx_y=y; s->rx_beat=beat;
            s->rx_keys=k; s->rx_focus=focus;
        }
    }
    // A partially published packet is not a key release. Retain the previous
    // committed keys until the next full packet or the watchdog expires.
    if (s->rx_age<fc_float(0x3f000000u)) {
        keys=s->rx_focus?s->rx_keys:0;
    }
    mc3_u32 pressed=keys&~s->previous; s->previous=keys;
    bool ready=s->live_camera && (s->frame-s->live_frame)<=3 && word(0x006144BCu)==0;
    if (!ready) { s->active=0; s->velocity={0,0,0}; return; }
    if (pressed&TOGGLE) { if (s->active) s->active=0; else capture(s); }
    if (!s->active) { s->velocity={0,0,0}; return; }
    if (pressed&RECENTER) capture(s);
    if (pressed&SMOOTH) s->smoothing^=1;
    if (keys&LEVEL) fc_level(s->matrix);
    float yaw=axis(keys,LOOK_L,LOOK_R)*fc_float(0x3fa66666u)*dt-dx*fc_float(0x3b449ba6u);
    float pitch=axis(keys,LOOK_U,LOOK_D)*fc_float(0x3fa66666u)*dt-dy*fc_float(0x3b449ba6u);
    float roll=axis(keys,ROLL_L,ROLL_R)*fc_float(0x3f19999au)*dt;
    fc_turn(s->matrix,yaw,pitch,roll);
    s->fov=fc_clamp(s->fov+axis(keys,WIDE,NARROW)*fc_float(0x41c80000u)*dt,fc_float(0x41200000u),fc_float(0x42f00000u));
    s->speed=fc_clamp(s->speed*(fc_float(0x3f800000u)+axis(keys,SPEED_UP,SPEED_DOWN)*fc_float(0x3fc00000u)*dt),fc_float(0x3dcccccdu),fc_float(0x43fa0000u));
    float speed=s->speed*(keys&FAST?fc_float(0x40a00000u):fc_float(0x3f800000u))*(keys&SLOW?fc_float(0x3e4ccccdu):fc_float(0x3f800000u));
    FcVec v=fc_add(fc_add(fc_mul(s->matrix.back,axis(keys,BACK,FORWARD)),
                         fc_mul(s->matrix.right,axis(keys,RIGHT,LEFT))),
                         fc_mul(s->matrix.up,axis(keys,UP,DOWN)));
    if (fc_dot(v,v)>fc_float(0x3f800000u)) v=fc_unit(v);
    v=fc_mul(v,speed);
    float blend=s->smoothing?dt*fc_float(0x41200000u)/(fc_float(0x3f800000u)+dt*fc_float(0x41200000u)):fc_float(0x3f800000u);
    s->velocity=fc_add(fc_mul(s->velocity,fc_float(0x3f800000u)-blend),fc_mul(v,blend));
    s->matrix.pos=fc_add(s->matrix.pos,fc_mul(s->velocity,dt));
}
