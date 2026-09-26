#include <cassert>
#include <cstdio>
#include "status_count.h"
int main() {
    assert(status_count(0,123,0,456)==0);
    assert(status_count(0x4D433354,5,0x4D433352,3)==8);
    assert(status_count(0x4D433354,5,0,200)==5);
    assert(status_count(0,200,0x4D433352,3)==3);
    assert(status_count(0x4D433354,0xFFFFFFFF,0x4D433352,3)==3);
    for (unsigned n=0;n<=1000;++n) {
        unsigned short t[22]; t[21]=0xCAFE;
        status_text(n,t);
        char expected[32]; std::snprintf(expected,sizeof(expected),"Mods loaded: %u",n>999?999:n);
        unsigned i=0;
        do { assert(t[i]==(unsigned char)expected[i]); } while(expected[i++]);
        assert(t[21]==0xCAFE);
    }
    std::puts("PASS: trace validation, boot+defer count, UTF-16 0..1000, buffer bounds");
}
