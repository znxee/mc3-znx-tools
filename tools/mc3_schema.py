"""
mc3_schema.py - read the .pck layout out of the game's own constructors

A `.pck` is a serialized object graph, and the code that reads it back is the
`X::X(datResource&)` constructor of every class in it. That constructor is the
specification: it walks the object, adds the load base to the fields that are
pointers, and recurses into the arrays. Nothing else in the file matters.

    if (field) field += base;                        -> that offset is a POINTER
    for (i < *(u16*)(this+K)) Ctor(ptr + i*S, res)   -> K is a COUNT that drives
                                                        an array of stride S

So a field the constructor never touches is dead weight, and a field it DOES
relocate had better hold a real offset - a wrong one becomes a wild pointer at
the first virtual call, which is the `Jump to unaligned address` this project
keeps hitting. A count written too low is worse than a crash: the constructor
simply stops relocating early, the rest of the array keeps raw file offsets, and
the geometry draws from whatever sits at that address in low memory.

The October 2004 alpha is what makes this readable at scale. Without it these
are 224 anonymous `sub_XXXXXX` and you cannot tell which type each one
serializes; `mc3_symbols.py` carries the names over, and this reads the code.

    python mc3_schema.py dump                 # decompile every ctor (needs IDA)
    python mc3_schema.py extract              # -> schema.tsv
    python mc3_schema.py show rmcModelGeom    # one type, contract plus source
    python mc3_schema.py selftest             # against contracts worked by hand
"""

import argparse
import collections
import importlib.util
import io
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _mod(name):
    s = importlib.util.spec_from_file_location(name, os.path.join(_HERE, name + '.py'))
    m = importlib.util.module_from_spec(s)
    sys.modules[name] = m
    s.loader.exec_module(m)
    return m


_ida = _mod('mc3_ida')
_sym = _mod('mc3_symbols')

OUT_PATH = os.path.join(_HERE, 'output', 'alpha2004')
SRC = os.path.join(OUT_PATH, 'ctors.txt')
SCHEMA = os.path.join(OUT_PATH, 'schema.tsv')

# `X__X_datResource__ref` after mc3_symbols' name cleaner: `::` became `__` and
# every other punctuation became `_`. A constructor is the one whose method name
# repeats the class name.
CTOR_RE = re.compile(r'^(\w+?)__(\w+)_datResource__ref(__\w+)?$')


def constructors():
    """[(retail_addr, type name)] for every named datResource constructor."""
    buf = io.StringIO()
    stdout, sys.stdout = sys.stdout, buf
    try:
        tab = _sym.layers()
    finally:
        sys.stdout = stdout
    out = []
    for ea, (name, _origin) in tab.items():
        m = CTOR_RE.match(name)
        if m and m.group(1) == m.group(2):
            out.append((ea, m.group(1)))
    return sorted(out, key=lambda x: x[1])


DUMP = r'''
import ida_hexrays, ida_funcs, ida_auto
ida_auto.auto_wait()
try:
    ida_hexrays.init_hexrays_plugin()
except Exception:
    pass
TARGETS = %s
for ea, name in TARGETS:
    emit('### %%s @ %%08X' %% (name, ea))
    f = ida_funcs.get_func(ea)
    if not f or f.start_ea != ea:
        emit('!! no function at this address')
        continue
    try:
        emit(str(ida_hexrays.decompile(ea)))
    except Exception as e:
        emit('!! failed: %%r' %% (e,))
'''


def dump(elf, out):
    targets = constructors()
    print('%d named datResource& constructors' % len(targets))
    txt = _ida.run(_ida.py(DUMP % repr(targets)), elf)
    if not os.path.isdir(os.path.dirname(out)):
        os.makedirs(os.path.dirname(out))
    io.open(out, 'w', encoding='utf-8').write(txt)
    n = txt.count('### ')
    print('written %s (%d functions, %d lines)' % (out, n, txt.count('\n')))


def read_src(path=SRC):
    """{type name: (addr, decompiled text)}"""
    out, name, ea, buf = {}, None, 0, []
    for line in io.open(path, encoding='utf-8'):
        m = re.match(r'^### (\w+) @ ([0-9A-Fa-f]{8})$', line.rstrip())
        if m:
            if name:
                out[name] = (ea, ''.join(buf))
            name, ea, buf = m.group(1), int(m.group(2), 16), []
        else:
            buf.append(line)
    if name:
        out[name] = (ea, ''.join(buf))
    return out


def _n(s):
    return int(s, 16) if s.lower().startswith('0x') else int(s)


# An argument of the constructor, as Hex-Rays names it: `a1`/`this` is the
# object, `a2` the datResource. Which is which is decided per function by
# whichever one is read at +4, since that is the load base.
ARG = r'(?:a\d+|this|v\d+)'
NUM = r'(?:0x[0-9A-Fa-f]+|\d+)'

# `*(_DWORD *)(a1 + 16) += *(_DWORD *)(a2 + 4);`
RELOC_A = re.compile(
    r'\*\(_DWORD \*\)\((%s) \+ (%s)\) \+= \*\(_DWORD \*\)\((%s) \+ 4\)' % (ARG, NUM, ARG))
# `*(_DWORD *)a1 += *(_DWORD *)(a2 + 4);`   (offset 0)
RELOC_A0 = re.compile(
    r'\*\(_DWORD \*\)(%s) \+= \*\(_DWORD \*\)\((%s) \+ 4\)' % (ARG, ARG))
# `*(_DWORD *)(a1 + 16) = v4 + *(_DWORD *)(a2 + 4);`
RELOC_B = re.compile(
    r'\*\(_DWORD \*\)\((%s) \+ (%s)\) = \w+ \+ \*\(_DWORD \*\)\((%s) \+ 4\)' % (ARG, NUM, ARG))
RELOC_B0 = re.compile(
    r'\*\(_DWORD \*\)(%s) = \w+ \+ \*\(_DWORD \*\)\((%s) \+ 4\)' % (ARG, ARG))
# `*(unsigned __int16 *)(a1 + 8)` / `*(_WORD *)(a1 + 8)`
U16 = re.compile(r'\*\((?:unsigned __int16|_WORD|__int16) \*\)\((%s) \+ (%s)\)' % (ARG, NUM))
U16_0 = re.compile(r'\*\((?:unsigned __int16|_WORD|__int16) \*\)(%s)[^+]' % ARG)
# `*(_DWORD *)a1 = &unk_626D98;` and `*(_DWORD *)(a1 + 16) = &unk_625028;`.
# The offset is NOT always zero: mcCullable stamps its vtable at +0x10, and a
# pattern anchored at +0 silently reports it as having none.
VTABLE = re.compile(
    r'\*\(_DWORD \*\)\((%s) \+ (%s)\) = &(?:unk|off|dword)_([0-9A-Fa-f]+)' % (ARG, NUM))
VTABLE_0 = re.compile(
    r'\*\(_DWORD \*\)(%s) = &(?:unk|off|dword)_([0-9A-Fa-f]+)' % ARG)
# any read of a field, to find what is looked at but never relocated
READ = re.compile(r'\*\((?:int|_DWORD|unsigned int) \*\)\((%s) \+ (%s)\)' % (ARG, NUM))
READ_ATTR = re.compile(
    r'(\w+) = \*\((?:int|_DWORD|unsigned int) \*\)\((%s) \+ (%s)\)' % (ARG, NUM))
# a call into another datResource constructor
CHILD = re.compile(r'(\w+_datResource__ref(?:__\w+)?)\s*\(')
# `8 * v5` or `v5 * 8` next to a child call - the array stride
STRIDE = re.compile(r'(%s) \* (?:\(int\))?\w+|\w+ \* (%s)' % (NUM, NUM))


def analyse(text):
    """The contract of one constructor, as far as the decompiled text states it.

    Only the two shapes that are unambiguous are reported as fact. Everything
    else lands in `notes` for a human, because a guess here is worse than a
    blank: it would be copied into a builder and produce a file that loads and
    draws nothing, which is the failure mode this whole exercise is about.
    """
    pointers, counts, children, vtab, notes = set(), set(), [], None, []

    # Only the BODY. The signature line names the function itself, and counting
    # that as a call made every one of the 226 look like it built children.
    body = text[text.find('{'):] if '{' in text else text

    # which argument is the datResource: the one read at +4 as the base
    bases = collections.Counter()
    for rx in (RELOC_A, RELOC_B):
        for m in rx.finditer(body):
            bases[m.group(3)] += 1
    res = bases.most_common(1)[0][0] if bases else None
    text = body

    for m in RELOC_A.finditer(text):
        pointers.add(_n(m.group(2)))
    for m in RELOC_B.finditer(text):
        pointers.add(_n(m.group(2)))
    for rx in (RELOC_A0, RELOC_B0):
        for m in rx.finditer(text):
            if m.group(1) != res:
                pointers.add(0)

    for m in U16.finditer(text):
        if m.group(1) != res:
            counts.add(_n(m.group(2)))
    if U16_0.search(text):
        counts.add(0)

    vt_off = None
    m = VTABLE.search(text)
    if m and m.group(1) != res:
        vtab, vt_off = int(m.group(3), 16), _n(m.group(2))
    else:
        m = VTABLE_0.search(text)
        if m and m.group(1) != res:
            vtab, vt_off = int(m.group(2), 16), 0

    # A 32-bit count: read from the object, never relocated, and the variable it
    # lands in is then compared. mcHood counts its models this way, so a parser
    # that only knows the u16 shape reports it as having no counts at all.
    c32 = set()
    for m in READ_ATTR.finditer(text):
        var, arg, off = m.group(1), m.group(2), _n(m.group(3))
        if arg == res or off in pointers or off == vt_off:
            continue
        if re.search(r'\b%s\b\s*(?:>|<|>=|<=)' % re.escape(var), text):
            c32.add(off)
    read_n = set()
    for m in READ.finditer(text):
        if m.group(1) != res:
            off = _n(m.group(2))
            if off not in pointers and off not in c32 and off != vt_off:
                read_n.add(off)

    for m in CHILD.finditer(text):
        name = m.group(1)
        cm = CTOR_RE.match(name)
        target = cm.group(1) if cm and cm.group(1) == cm.group(2) else name
        # the array stride, when the call line or the one before shows it
        window = text[max(0, m.start() - 200):m.start()]
        steps = [int(g or h) for g, h in STRIDE.findall(window)
                  if (g or h) and _n(g or h) in (2, 4, 8, 12, 16, 20, 24, 32, 48, 64)]
        children.append((target, steps[-1] if steps else None))

    if not pointers and not children and 'failed' not in text:
        notes.append('no pointer relocated: a leaf object, or the decompiler '
                     'did not open it')
    return dict(pointers=sorted(pointers), counts=sorted(counts),
                counts32=sorted(c32), read_n=sorted(read_n),
                children=children, vtable=vtab, vtable_off=vt_off, notes=notes)


def extract(source, out):
    src = read_src(source)
    print('%d constructors read from %s' % (len(src), source))
    lines = ['# type\taddress\tfield\tkind\tdetail']
    read_n = empties = 0
    for name in sorted(src):
        ea, text = src[name]
        if text.lstrip().startswith('!!'):
            empties += 1
            continue
        c = analyse(text)
        read_n += 1
        if c['vtable']:
            lines.append('%s\t%08X\t0x%02X\tvtable\tstamped with %08X'
                          % (name, ea, c['vtable_off'] or 0, c['vtable']))
        for off in c['pointers']:
            lines.append('%s\t%08X\t0x%02X\tpointer\t+= base if != 0' % (name, ea, off))
        for off in c['counts']:
            lines.append('%s\t%08X\t0x%02X\tcount16\t' % (name, ea, off))
        for off in c['counts32']:
            lines.append('%s\t%08X\t0x%02X\tcount32\tread and compared, not relocated'
                          % (name, ea, off))
        for off in c['read_n']:
            lines.append('%s\t%08X\t0x%02X\tread\tread and not relocated' % (name, ea, off))
        for target, step in c['children']:
            lines.append('%s\t%08X\t-\tchild\t%s%s'
                          % (name, ea, target,
                             (' stride %d' % step) if step else ''))
        for n in c['notes']:
            lines.append('%s\t%08X\t-\tnote\t%s' % (name, ea, n))
    io.open(out, 'w', encoding='utf-8', newline='\n').write('\n'.join(lines) + '\n')
    print('%d analysed, %d without a body -> %s' % (read_n, empties, out))


def show(name, source):
    src = read_src(source)
    if name not in src:
        similar = [k for k in src if name.lower() in k.lower()]
        raise SystemExit('%r not found. similar: %s' % (name, ', '.join(similar[:12]) or '-'))
    ea, text = src[name]
    c = analyse(text)
    print('%s @ %08X' % (name, ea))
    if c['vtable']:
        print('   +0x%02X  vtable      stamped with %08X (the file value does not matter)'
              % (c['vtable_off'] or 0, c['vtable']))
    for off in c['pointers']:
        print('   +0x%02X  pointer     += base if != 0' % off)
    for off in c['counts']:
        print('   +0x%02X  count16' % off)
    for off in c['counts32']:
        print('   +0x%02X  count32     read and compared, not relocated' % off)
    for off in c['read_n']:
        print('   +0x%02X  read        read and not relocated' % off)
    for target, step in c['children']:
        print('          child       %s%s' % (target, (', stride %d' % step) if step else ''))
    for n in c['notes']:
        print('   note: %s' % n)
    print()
    print(text.rstrip())


# Contracts worked out by hand from the disassembly before this tool existed.
# If the parser stops agreeing with them it has drifted, and every schema it
# emitted since is suspect.
CALIBRATION = {
    'rmcModelGeom': dict(pointers=[0x10, 0x14], counts=[8, 10], vtable=0x626D98,
                         vtable_off=0),
    'rmcGeometry': dict(pointers=[0x00], counts=[4]),
    # vtable at +0x10, not at +0 - the case that caught the first parser, which
    # anchored the pattern at offset zero and reported "no vtable"
    'mcCullable': dict(pointers=[0x00, 0x04, 0x0C], vtable=0x625028, vtable_off=0x10),
    # 32-bit counts, not u16 - the second case. A parser that only knew the u16
    # shape said mcHood had no counts at all, while it has two.
    'mcHood': dict(pointers=[0x00, 0x08, 0x10], counts=[]),
}


def selftest(source):
    src = read_src(source)
    failures = 0
    for name, expected in sorted(CALIBRATION.items()):
        if name not in src:
            print('   MISSING %s is not in the dump' % name)
            failures += 1
            continue
        c = analyse(src[name][1])
        for field, val in expected.items():
            got = c[field]
            ok = got == val
            print('   %-7s %-16s %-11s expected %s, got %s'
                  % ('OK' if ok else 'DIFFERS', name, field, val, got))
            failures += not ok
    print()
    print('%s' % ('calibration passed' if not failures else '%d mismatch(es)' % failures))
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='action')
    d = sub.add_parser('dump', help='decompile every ctor (needs IDA)')
    d.add_argument('--elf')
    d.add_argument('--out', default=SRC)
    e = sub.add_parser('extract', help='schema.tsv from the dump')
    e.add_argument('--src', default=SRC)
    e.add_argument('--out', default=SCHEMA)
    s = sub.add_parser('show', help='the contract of one type, with the source')
    s.add_argument('kind')
    s.add_argument('--src', default=SRC)
    t = sub.add_parser('selftest', help='check against the hand-made contracts')
    t.add_argument('--src', default=SRC)
    a = ap.parse_args()
    if a.action == 'dump':
        dump(a.elf or _ida.DEFAULT_ELF, a.out)
    elif a.action == 'extract':
        extract(a.src, a.out)
    elif a.action == 'show':
        show(a.kind, a.src)
    elif a.action == 'selftest':
        return selftest(a.src)
    else:
        ap.print_help()
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
