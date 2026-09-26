"""
mc3_ida.py - drive IDA Pro headlessly against the Midnight Club 3 ELF

The PS2 executable is a MIPS R5900 ELF that IDA loads with no coaxing
(processor `r5900l`, ~12500 functions), so most format questions can be
answered from the code instead of by measuring bytes and guessing.

Two scripting backends:

  IDC        always available, no setup. Disassembly, xrefs, segments.
  IDAPython  needs `idapyswitch` pointed at a Python <= 3.9 (IDA 7.5 refuses
             3.10+). Only this side reaches Hex-Rays, so decompiling needs it.

The Hex-Rays plugin for MIPS is `hexmips.dll` and it loads under the 32-bit
`idat.exe`, not `idat64.exe` -- the 64-bit build has no MIPS decompiler.

The database is built once into a work directory and reused; the first run
pays the full auto-analysis (a couple of minutes), later runs are seconds.

    python mc3_ida.py decompile 0x2A9D18 0x2AD148
    python mc3_ida.py disasm 0x25B9D8 --count 40
    python mc3_ida.py vtable 0x626D98
    python mc3_ida.py xrefs 0x626D98
    python mc3_ida.py script my_script.py

Useful addresses found so far (SLUS_213.55):

    0x2A9D18   mesh relocation: walks the group table and the +0x14 array
    0x2AD148   relocates one group-table entry and its block list
    0x2A9E40   relocates one +0x14 slot (takes the group count)
    0x2AA638   relocates a nested [count-holder][pointer array] pair
    0x2A9CC8   mesh base class: sets flags in +0x04, relocates the material table
    0x2AA0E0   mesh destructor
    0x25B9D8   generic two-array relocator
    0x626D98   real mesh vtable (the 0x007A1E18 in the file is dead residue)
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

# The untouched executable first: the *_EDITED* ones are patched for hostfs and
# come and go, and format work should not depend on which one is lying around.
# `MC3HostFS/SLUS_213.55.ELF` is NOT the retail executable: it is byte-identical
# to SLUS_213.55_EDITED_FOLDERSTREAMS.ELF (md5 877ef8cd...), 157 bytes away from
# a clean rip, and every one of those bytes is in the FILE LAYER - IsDiscBuild()
# forced to 0 so assets.dat never mounts, the boot prefix changed from `cdrom0:\`
# to `host0:`, and the `$/` root changed from `T:\mc3\assets` to `ASSETS`. That
# is what makes the loose tree work, and it also means any conclusion about how
# the game finds files is a conclusion about the PATCHED build. The clean rip
# (md5 3e141315...) goes first so that analysis defaults to retail.
ELF_CANDIDATES = [os.environ.get('MC3_ELF', 'SLUS_213.55'), 'SLUS_213.55.ELF']
DEFAULT_ELF = next((x for x in ELF_CANDIDATES if os.path.isfile(x)), ELF_CANDIDATES[0])
# IDA 9.0 first: 16816 functions against 12538 for 7.5 on the same ELF, and it
# is a 64-bit build, which finally opens Offlbruno's `.i64` (17035 functions,
# 16943 with real names). It runs with Python 3.8, the SAME one 7.5 uses, so the
# two coexist without fighting over the `Python3TargetDLL` registry key.
#
# Mind the install: in one IDA 9.0.240925 install the IDAPython was from a build
# four months newer than the core and EVERY `_ida_*` module failed on a missing
# symbol. A consistent install works. Set MC3_IDA to its folder.
IDA_DIRS = [d for d in (os.environ.get('MC3_IDA', ''), r'C:/IDA Professional 9.0', r'C:/IDA 7.5') if d]


def find_ida():
    """The text-mode IDA, newest install first. Needs `plugins/hexmips.dll`."""
    for d in IDA_DIRS:
        exe = os.path.join(d, 'idat.exe')
        if os.path.isfile(exe):
            return exe
    raise SystemExit('idat.exe not found; add the install to IDA_DIRS')


def workdir():
    d = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
                     'mc3_ida')
    os.makedirs(d, exist_ok=True)
    return d


def database(elf):
    """A private copy of the target, so IDA's files never land next to the game.

    The copy is named after the source, not a fixed `mc3.elf`. Two targets used
    to share one database: pointing --elf at anything else silently threw away
    the analysis of the previous one and re-ran it from scratch. Worth having
    when the target is not the game at all - ps2dis.exe is a Win32 PE and IDA
    disassembles it perfectly well, which is how the VU encoding tables get
    lifted out of a tool that already knows them.
    """
    d = workdir()
    stem = re.sub(r'[^A-Za-z0-9_.-]', '_', os.path.basename(elf))
    dst = os.path.join(d, stem)
    # An `.i64`/`.idb` ALREADY is the database: copy and open it, without
    # re-analysing anything. That is how Offlbruno's database comes in - 17035
    # functions with 16943 real names, which no autoanalysis produces.
    if elf.lower().endswith(('.i64', '.idb')):
        if not os.path.isfile(dst) or os.path.getmtime(elf) > os.path.getmtime(dst):
            shutil.copy2(elf, dst)
        return dst
    if not os.path.isfile(dst) or os.path.getmtime(elf) > os.path.getmtime(dst):
        shutil.copy2(elf, dst)
        base = os.path.splitext(stem)[0]
        for junk in (base + '.idb', base + '.i64', stem + '.id0', stem + '.id1',
                     stem + '.id2', stem + '.nam', stem + '.til'):
            p = os.path.join(d, junk)
            if os.path.exists(p):
                os.remove(p)
    return dst


def run(script_src, elf=DEFAULT_ELF, suffix='.py'):
    """Run a script inside IDA and hand back whatever it wrote to `out`.

    The script gets `OUT` in its namespace (IDC: call `out_path()`), writes
    there, and must end with a quit -- IDA never returns to a prompt in `-A`
    mode but it will sit on the analysis if the script forgets.
    """
    d = workdir()
    db = database(elf)
    # ONE NAME PER PROCESS. The three artefacts (script, output and log) lived at
    # FIXED paths inside the shared workdir, and `out.txt` was also deleted at
    # the start - with two sessions running at the same time, one read the
    # other's result or got "script produced nothing" with no explanation. There
    # are several forks using IDA now; the per-target database was already
    # right, only this pair was not. The pid is enough: the process is what
    # serialises the calls.
    tag = 'p%d' % os.getpid()
    out = os.path.join(d, 'out_%s.txt' % tag)
    if os.path.exists(out):
        os.remove(out)
    path = os.path.join(d, 'script_%s%s' % (tag, suffix))
    log = os.path.join(d, 'ida_%s.log' % tag)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(script_src.replace('@OUT@', out.replace('\\', '/')))
    try:
        subprocess.run([find_ida(), '-A', '-L' + log, '-S' + path, db],
                       cwd=d, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(out):
            return open(out, encoding='utf-8', errors='replace').read()
        tail = open(log, errors='replace').read()[-1200:] if os.path.exists(log) else ''
        raise SystemExit('script produced nothing.\n--- ida.log ---\n' + tail)
    finally:
        for garbage in (path,):
            try:
                os.remove(garbage)
            except OSError:
                pass


PY_HEAD = """
import idaapi, idautils, idc, ida_funcs, ida_bytes, ida_auto, ida_pro
ida_auto.auto_wait()      # the first run on a fresh database has not finished
                          # analysing yet, and xrefs come back empty without this
try:
    import ida_hexrays
    HEX = ida_hexrays.init_hexrays_plugin()
except ImportError:
    HEX = False
_out = []                 # deliberately obscure: scripts shadow short names
def emit(s): _out.append(str(s))
"""

PY_TAIL = """
open(r'@OUT@', 'w', encoding='utf-8', errors='replace').write('\\n'.join(_out))
ida_pro.qexit(0)
"""


def py(body):
    return PY_HEAD + body + PY_TAIL


def cmd_decompile(a):
    body = 'if not HEX:\n    emit("no decompiler: run idapyswitch with Python <= 3.9")\n'
    body += 'for ea in %r:\n' % [int(x, 0) for x in a.address]
    body += """    emit('\\n' + '=' * 62)
    emit('%s @ %#x' % (idc.get_func_name(ea) or '?', ea))
    emit('=' * 62)
    try:
        emit(ida_hexrays.decompile(ea))
    except Exception as e:
        emit('failed: %r' % (e,))
"""
    print(run(py(body), a.elf))


def cmd_disasm(a):
    body = """
ea = %d
for _ in range(%d):
    emit('  %%08x  %%s' %% (ea, idc.GetDisasm(ea)))
    ea = idc.next_head(ea, idaapi.BADADDR)
    if ea == idaapi.BADADDR:
        break
""" % (int(a.address, 0), a.count)
    print(run(py(body), a.elf))


def cmd_vtable(a):
    body = """
vt = %d
for i in range(%d):
    p = idc.get_wide_dword(vt + 4 * i)
    f = ida_funcs.get_func(p)
    emit('  [%%2d] %%08x  %%s%%s' %% (i, p,
         (idc.get_func_name(p) if f else '(no function here)'),
         '' if (not f or f.start_ea == p) else ' *mid-function*'))
""" % (int(a.address, 0), a.count)
    print(run(py(body), a.elf))


def cmd_xrefs(a):
    body = """
ea = %d
for x in idautils.XrefsTo(ea):
    emit('  %%08x  %%-28s %%s' %% (x.frm, idc.get_func_name(x.frm) or '?',
                                  idc.GetDisasm(x.frm)))
""" % int(a.address, 0)
    print(run(py(body), a.elf))


def cmd_script(a):
    src = open(a.path, encoding='utf-8').read()
    print(run(src if '@OUT@' in src else py(src), a.elf,
              os.path.splitext(a.path)[1] or '.py'))


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--elf', default=DEFAULT_ELF)
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('decompile', help='Hex-Rays pseudocode for one or more functions')
    p.add_argument('address', nargs='+')
    p.set_defaults(func=cmd_decompile)

    p = sub.add_parser('disasm', help='raw instructions')
    p.add_argument('address')
    p.add_argument('--count', type=int, default=24)
    p.set_defaults(func=cmd_disasm)

    p = sub.add_parser('vtable', help='function pointers at an address')
    p.add_argument('address')
    p.add_argument('--count', type=int, default=12)
    p.set_defaults(func=cmd_vtable)

    p = sub.add_parser('xrefs', help='who references an address')
    p.add_argument('address')
    p.set_defaults(func=cmd_xrefs)

    p = sub.add_parser('script', help='run an IDAPython (.py) or IDC (.idc) file')
    p.add_argument('path')
    p.set_defaults(func=cmd_script)

    a = ap.parse_args()
    a.func(a)


if __name__ == '__main__':
    main()
