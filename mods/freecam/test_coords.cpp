#include <cassert>
#include <cstdio>
#include "freecam_coords.h"
static void check(char axis,int tenths,const char *expected) {
    unsigned short out[16]; for (int i=0;i<16;++i) out[i]=0xCAFE;
    fc_format_coord((unsigned short)axis,tenths,out);
    unsigned i=0;
    do { assert(out[i]==(unsigned char)expected[i]); } while(expected[i++]);
    assert(out[15]==0xCAFE);
}
int main() {
    check('X',0,"X: 0.0"); check('Y',1,"Y: 0.1"); check('Z',-1,"Z: -0.1");
    check('X',99,"X: 9.9"); check('X',100,"X: 10.0");
    check('Y',999,"Y: 99.9"); check('Y',1000,"Y: 100.0");
    check('Z',-12345,"Z: -1234.5");
    check('X',999999,"X: 99999.9"); check('X',1000000,"X: 99999.9");
    std::puts("PASS: signed X/Y/Z formatting, decimal boundaries and buffer bounds");
}
