#include "freecam_math.h"
#include <cassert>
#include <cstdio>
int main() {
    FcVec n=fc_unit({3,4,0});
    assert(fc_abs(n.x-0.6f)<0.00001f && fc_abs(n.y-0.8f)<0.00001f);
    FcMatrix m={{1,0,0},{0,1,0},{0,0,1},{10,20,30}};
    for(int i=0;i<10000;++i) fc_turn(m,0.004f,0.003f,-0.002f);
    assert(fc_abs(fc_dot(m.right,m.right)-1)<0.00001f);
    assert(fc_abs(fc_dot(m.up,m.up)-1)<0.00001f);
    assert(fc_abs(fc_dot(m.back,m.back)-1)<0.00001f);
    assert(fc_abs(fc_dot(m.right,m.up))<0.00001f);
    assert(fc_abs(fc_dot(m.up,m.back))<0.00001f);
    assert(m.pos.x==10 && m.pos.y==20 && m.pos.z==30);
    fc_level(m); assert(fc_abs(m.right.y)<0.00001f);
    FcMatrix a={{1,0,0},{0,1,0},{0,0,1},{0,0,0}}, b=a;
    for(int i=0;i<30;++i) fc_turn(a,1.0f/30,0,0);
    for(int i=0;i<120;++i) fc_turn(b,1.0f/120,0,0);
    assert(fc_abs(a.back.x-b.back.x)<0.00001f);
    assert(fc_abs(a.back.z-b.back.z)<0.00001f);
    std::puts("PASS: normalization, 10000 rotations, orthogonality, position, roll reset, 30/120fps equivalence");
}
