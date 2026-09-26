// CinematicClub free-camera math, adapted by modderG, 2026-09-05.
// GPL-3.0; original camera conventions by AlgumCorrupto (Paulo).
#ifndef MC3_FREECAM_MATH_H
#define MC3_FREECAM_MATH_H
// Keep float constants in instructions: v2 packer cannot pair interleaved
// constant-pool HI16/LO16 references belonging to different symbols.
static __attribute__((noinline)) float fc_float(unsigned int word) {
    __asm__ volatile("" : "+r"(word));
    union { unsigned int u; float f; } v; v.u=word; return v.f;
}
struct FcVec { float x, y, z; };
struct FcMatrix { FcVec right, up, back, pos; };
static inline float fc_abs(float x) { return x < 0 ? -x : x; }
static inline float fc_clamp(float x, float lo, float hi) {
    return x < lo ? lo : (x > hi ? hi : x);
}
static inline FcVec fc_add(FcVec a, FcVec b) { return {a.x+b.x,a.y+b.y,a.z+b.z}; }
static inline FcVec fc_mul(FcVec a, float b) { return {a.x*b,a.y*b,a.z*b}; }
static inline float fc_dot(FcVec a, FcVec b) { return a.x*b.x+a.y*b.y+a.z*b.z; }
static inline FcVec fc_cross(FcVec a, FcVec b) {
    return {a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x};
}
static inline FcVec fc_unit(FcVec v) {
    float sq=fc_dot(v,v);
    if (!(sq > fc_float(0x358637bdu) && sq < fc_float(0x49742400u))) return {0,0,0};
    float r=fc_float(0x3f800000u);
    // Newton inverse square root; camera axes stay close to unit length.
    if (sq > fc_float(0x40800000u)) { while (sq > fc_float(0x40800000u)) { sq*=fc_float(0x3e800000u); r*=fc_float(0x3f000000u); } }
    while (sq < fc_float(0x3e800000u)) { sq*=fc_float(0x40800000u); r*=fc_float(0x40000000u); }
    float t=fc_float(0x3f19999au);
    for (int i=0;i<7;++i) t=t*(fc_float(0x3fc00000u)-fc_float(0x3f000000u)*sq*t*t);
    return fc_mul(v,r*t);
}
static inline FcVec fc_rotate(FcVec v, FcVec axis, float angle) {
    // Each update clamps angles to +/-0.5 radians; these Taylor polynomials
    // have <1e-7 absolute error here and need no PS2/libc float-call ABI.
    float a=fc_clamp(angle,-fc_float(0x3f000000u),fc_float(0x3f000000u)), a2=a*a;
    float s=a*(fc_float(0x3f800000u)+a2*(-fc_float(0x3f800000u)/fc_float(0x40c00000u)+a2*(fc_float(0x3f800000u)/fc_float(0x42f00000u)-a2/fc_float(0x459d8000u))));
    float c=fc_float(0x3f800000u)+a2*(-fc_float(0x3f000000u)+a2*(fc_float(0x3f800000u)/fc_float(0x41c00000u)-a2/fc_float(0x44340000u)));
    return fc_add(fc_add(fc_mul(v,c),fc_mul(fc_cross(axis,v),s)),
                  fc_mul(axis,fc_dot(axis,v)*(fc_float(0x3f800000u)-c)));
}
static inline void fc_turn(FcMatrix &m,float yaw,float pitch,float roll) {
    const FcVec world_up={0,1,0};
    m.right=fc_rotate(m.right,world_up,yaw);
    m.up=fc_rotate(m.up,world_up,yaw);
    m.back=fc_rotate(m.back,world_up,yaw);
    m.back=fc_rotate(m.back,m.right,pitch);
    m.up=fc_rotate(m.up,m.right,pitch);
    m.right=fc_rotate(m.right,m.back,roll);
    m.back=fc_unit(m.back);
    m.up=fc_unit(fc_cross(m.back,m.right));
    m.right=fc_unit(fc_cross(m.up,m.back));
}
static inline void fc_level(FcMatrix &m) {
    const FcVec world_up={0,1,0};
    FcVec right=fc_cross(world_up,m.back);
    if (fc_dot(right,right)>fc_float(0x38d1b717u)) {
        m.right=fc_unit(right); m.up=fc_unit(fc_cross(m.back,m.right));
    }
}
#endif
