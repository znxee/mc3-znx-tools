#include "freecam_keys.h"
#include <cassert>
#include <cstdio>
int main() {
    volatile unsigned short report[4]={0x1A,0,0,0}; // one keydown, then no new packets
    unsigned int previous=0, toggles=0, moving_frames=0;
    for (int frame=0;frame<240;++frame) {
        unsigned int held=fc_usb_held(0,report,1);
        if (held&FORWARD) ++moving_frames;
    }
    assert(moving_frames==240);
    assert(fc_usb_held(0,report,0)==0); // keyup report immediately stops motion
    report[0]=0x13;
    for (int frame=0;frame<240;++frame) {
        unsigned int held=fc_usb_held(0,report,1);
        toggles+=(held&~previous&TOGGLE)!=0; previous=held;
    }
    assert(toggles==1); // held P must not flicker the camera
    previous=fc_usb_held(0,report,0);
    toggles+=(fc_usb_held(0,report,1)&~previous&TOGGLE)!=0;
    assert(toggles==2);
    report[0]=0x1A; report[1]=0x07;
    assert(fc_usb_held(2,report,2)==(FORWARD|RIGHT|FAST));
    assert(fc_usb_held(0x20,report,2)==(FORWARD|RIGHT|FAST));
    assert(fc_usb_held(0x11,report,0)==SLOW);
    assert(fc_usb_held(0,report,65)==0);
    report[0]=0x1D; assert(fc_usb_held(0,report,1)==WIDE);
    report[0]=0x52; assert(fc_usb_held(0,report,1)==LOOK_U);
    report[0]=0; assert(fc_usb_held(0,report,1)==0);
    std::puts("PASS: 240 held frames without repeat, release, toggle edge, W+D+Shift, FOV, arrows, invalid count");
}
