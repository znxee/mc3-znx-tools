"""Tiny capstone helpers for the retail ELF, meant for an interactive session.

    >>> from mc3_mipsx import callers, start, dump
    >>> [hex(x) for x in callers(0x2A8FA0)]     # every jal to a function
    >>> hex(start(0x2A9000))                     # walk back to the addiu sp,sp,-N
    >>> dump(0x2A8FA0, 40)                       # disassemble 40 instructions

The ELF comes from MC3_ELF. Needs `pip install capstone`.
"""
import os
import struct
from capstone import *
D=open(os.environ.get("MC3_ELF", "SLUS_213.55"),"rb").read()
BASE=0x1a0000; OFF=0x80; SZ=0x4d7074
def v2f(va): return OFF+(va-BASE)
def f2v(o): return BASE+(o-OFF)
MD=Cs(CS_ARCH_MIPS, CS_MODE_MIPS64|CS_MODE_LITTLE_ENDIAN)
def callers(target):
    out=[]
    for o in range(OFF,OFF+SZ-4,4):
        w=struct.unpack_from("<I",D,o)[0]
        if (w>>26)==3:
            va=((f2v(o)+4)&0xf0000000)|((w&0x3ffffff)<<2)
            if va==target: out.append(f2v(o))
    return out
def start(va,maxk=900):
    o=v2f(va)
    for k in range(0,maxk):
        p=o-4*k
        w=struct.unpack_from("<I",D,p)[0]
        if (w>>26)==0x09 and ((w>>21)&31)==29 and ((w>>16)&31)==29 and (w&0xffff)>=0x8000:
            return f2v(p)
    return None
def dump(va,n=60):
    o=v2f(va)
    for i in MD.disasm(D[o:o+4*n], va):
        print("%08x  %-9s %s"%(i.address,i.mnemonic,i.op_str))
