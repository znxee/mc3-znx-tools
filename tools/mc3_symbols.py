"""
mc3_symbols.py - carry the alpha build's function names over to the retail ELF

The October 2004 alpha ships `MC.MAP`, a linker map with 20275 symbols: full C++
signatures, sizes, and the .obj each came from. The retail executable has none of
that. Both put `.text` at 0x1a0000 but they are different builds, so addresses do
not line up and the bytes differ wherever a relocation does.

What survives a rebuild is what a function TALKS ABOUT. A function that
references the strings "$/resources/city/%s_bnd" and "hood" in one build
references them in the other, so the set of string literals a function touches is
a fingerprint that ignores addresses entirely. Matching on that needs no
disassembly heuristics and no call-graph alignment.

    python mc3_symbols.py dump  --elf ALPHA   --map MC.MAP --out alpha.tsv
    python mc3_symbols.py dump  --elf RETAIL              --out retail.tsv
    python mc3_symbols.py match alpha.tsv retail.tsv --out names.tsv
    python mc3_symbols.py apply names.tsv --elf RETAIL

`dump` runs inside IDA through mc3_ida.py. With `--map` it first creates a
function at every address the map lists - the alpha's autoanalysis does not find
them all on its own, and decompiling an address with no function there silently
returns nothing.
"""

import argparse
import collections
import importlib.util
import io
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_S = importlib.util.spec_from_file_location('mc3_ida', os.path.join(_HERE, 'mc3_ida.py'))
_ida = importlib.util.module_from_spec(_S)
sys.modules['mc3_ida'] = _ida
_S.loader.exec_module(_ida)

MAP_RE = re.compile(r'^([0-9a-f]{8}) ([0-9a-f]{8})\s+\d+\s+(.*)$')

# The shared symbol store. Every fork of this investigation reads and writes the
# same files, so a name worked out while chasing collision shows up in the next
# session that opens the city loader. Precedence is manual > neighbour > graph >
# string: a hand-verified name always wins over a machine guess, and re-running
# the machine passes never clobbers it.
#
# The three machine passes reach different things, in this order of coverage:
#   match      what a function SAYS      (string literals)      1224 pairs
#   graph      the company it keeps      (callers and callees)  3219 pairs
#   neighbours where it SITS             (source order + size)  6304 pairs
#   token      the DATA it stamps        (vtables, globals)      183 pairs
#   data       the alpha's named GLOBALS carried across         1013 pairs
#
# Measured, not assumed: with the string layer held out of the anchors and used
# as ground truth, the neighbour pass - the one that looks weakest - reproduces
# 505 of 508 of its pairs, 99.4%.
SYMBOLS_DIR = os.path.join(_HERE, 'symbols')
LAYERS = ('names.tsv', 'names_graph.tsv', 'names_neighbours.tsv',
           'names_token.tsv', 'names_data.tsv', 'names_manual.tsv')


def read_map(path):
    """[(addr, size, name)] of the real symbols in a linker map."""
    out = []
    for line in io.open(path, encoding='latin1'):
        m = MAP_RE.match(line)
        if not m:
            continue
        name = m.group(3).strip()
        if (not name or name.startswith('.') or name.endswith(('.obj', '.o'))
                or '.obj)' in name):
            continue
        addr, size = int(m.group(1), 16), int(m.group(2), 16)
        if addr and size:
            out.append((addr, size, name))
    return out


DUMP = r'''
import ida_auto, ida_funcs, ida_bytes, idautils, idc, ida_name
from ida_xref import fl_CN, fl_CF, dr_R, dr_W, dr_O

ida_auto.auto_wait()
CREATE = %s
for ea in CREATE:
    f = ida_funcs.get_func(ea)
    if not f or f.start_ea != ea:
        ida_funcs.add_func(ea)
if CREATE:
    ida_auto.auto_wait()

# every string, once, with the addresses that cite it
text = {}
for s in idautils.Strings():
    t = str(s)
    if 4 <= len(t) <= 120:
        text[s.ea] = t

per_function = {}
for sea, t in text.items():
    for x in idautils.XrefsTo(sea):
        f = ida_funcs.get_func(x.frm)
        if f:
            per_function.setdefault(f.start_ea, set()).add(t)

for fea in idautils.Functions():
    f = ida_funcs.get_func(fea)
    ss = sorted(per_function.get(fea, ()))
    # The NAME the database already has. In a database WITH SYMBOLS (the alpha's
    # .i64) this replaces the whole MC.MAP: 16943 named functions against the
    # ~2000 the map reached, because the map only helped where there was a string.
    nm = ida_name.get_name(fea) or ''
    if nm.startswith(('sub_', 'nullsub_', 'unknown_', 'j_sub_', 'loc_')):
        nm = ''
    # who this function calls. We emit EVERY function, the silent ones included:
    # they do not match by string but are reachable through the graph, and they
    # are exactly the interesting ones (the .pck loader cites almost no text).
    ch = set()
    for ea in idautils.FuncItems(fea):
        for x in idautils.XrefsFrom(ea, 0):
            if x.type in (fl_CN, fl_CF):
                g = ida_funcs.get_func(x.to)
                if g and g.start_ea != fea:
                    ch.add(g.start_ea)
    # DATA the function references. This is how the class of functions that
    # cite no string at all is reached: `*(a1) = &unk_626F20` references a
    # VTABLE, and a vtable can be matched across builds by the set of functions
    # it contains. Strings are left out, they already come in another way.
    dr = set()
    for ea in idautils.FuncItems(fea):
        for x in idautils.XrefsFrom(ea, 0):
            if x.type in (dr_R, dr_W, dr_O) and x.to not in text:
                if not ida_funcs.get_func(x.to):
                    dr.add(x.to)
    _out.append('%%08X	%%d	%%s	|	%%s	|	%%s	|	%%s'
                %% (fea, f.end_ea - f.start_ea, nm.replace('	', ' '),
                    '	'.join(s.replace('	', ' ') for s in ss),
                    ' '.join('%%08X' %% c for c in sorted(ch)),
                    ' '.join('%%08X' %% c for c in sorted(dr))))
'''


def dump(elf, mapping, out):
    create = []
    if mapping:
        create = sorted({a for a, _s, _n in read_map(mapping)})
        print('%s: %d map addresses' % (os.path.basename(mapping), len(create)))
    txt = _ida.run(_ida.py(DUMP % repr(create)), elf)
    io.open(out, 'w', encoding='utf-8').write(txt)
    print('written %s (%d lines)' % (out, txt.count('\n')))


def read_dump(path):
    """{addr: (size, name, frozenset(strings), tuple(callees))}

    Line: `addr TAB size TAB name TAB | TAB string... TAB | TAB calls`.
    The NAME comes from the database itself - in one with symbols (the alpha's
    `.i64`) it covers 17059 of 17154 functions, against the ~2000 `MC.MAP`
    reached, because the map only helped where there was a string to anchor.
    """
    out = {}
    for line in io.open(path, encoding='utf-8'):
        p = line.rstrip().split(chr(9))
        if len(p) < 3:
            continue
        try:
            addr, size = int(p[0], 16), int(p[1])
        except ValueError:
            continue
        name = p[2].strip()
        rest = p[3:]
        strings, calls = [], ()
        if '|' in rest:
            k = rest.index('|')
            rest2 = rest[k + 1:]
            if '|' in rest2:
                m = rest2.index('|')
                strings = rest2[:m]
                if m + 1 < len(rest2):
                    calls = tuple(int(x, 16) for x in rest2[m + 1].split() if x)
            else:
                strings = rest2
        out[addr] = (size, name, frozenset(x for x in strings if x), calls)
    return out


def read_data(path):
    """{addr: frozenset(DATA addresses the function references)}.

    A separate reader on purpose: `read_dump` is used by other passes (and by
    another fork editing this file in parallel), so changing the tuple it
    returns would break someone else's code. This one only reads the fourth field.
    """
    out = {}
    for line in io.open(path, encoding='utf-8'):
        p = line.rstrip().split(chr(9))
        if len(p) < 3:
            continue
        try:
            addr = int(p[0], 16)
        except ValueError:
            continue
        parts, current = [], []
        for x in p[3:]:
            if x == '|':
                parts.append(current)
                current = []
            else:
                current.append(x)
        parts.append(current)
        data = ()
        if len(parts) > 3:
            data = tuple(int(x, 16) for x in ' '.join(parts[3]).split() if x)
        out[addr] = frozenset(data)
    return out


def read_anchors(paths):
    """{alpha_addr: retail_addr} from any of the shared layer files.

    Every layer writes the alpha address in the fifth column, so one reader
    serves all of them, and a file that is not there yet is simply skipped.
    """
    par = {}
    for file_path in paths:
        if not os.path.isfile(file_path):
            continue
        for l in io.open(file_path, encoding='utf-8'):
            if l.startswith('#'):
                continue
            p = l.rstrip().split(chr(9))
            if len(p) >= 5:
                try:
                    par[int(p[4], 16)] = int(p[0], 16)
                except ValueError:
                    pass
    return par


def _comatch(A, R, dA, dR, par, rounds, minimum):
    """The two-sided pass shared by `tokens` and `data`.

    Both subcommands run the same loop and differ only in which half of the
    result they keep, so the loop lives here. `tokens` wants the functions it
    named; `data` wants `dpar`, the alpha-data-to-retail-data pairing that the
    loop has always computed and, until now, thrown away.

    Returns (par, dpar, dscore, new_functions).
    """
    def per_data(D):
        inv = collections.defaultdict(set)
        for f, ds in D.items():
            for d in ds:
                inv[d].add(f)
        return inv

    invA, invR = per_data(dA), per_data(dR)
    print('%d function anchors; %d data in the alpha, %d in retail'
          % (len(par), len(invA), len(invR)))
    weightR = {d: 1.0 / (1 + len(fs)) for d, fs in invR.items()}

    dpar, dscore = {}, {}
    new_total = []
    for round_ in range(1, rounds + 1):
        cand = collections.defaultdict(collections.Counter)
        for da, fs in invA.items():
            if da in dpar:
                continue
            for fa in fs:
                fr = par.get(fa)
                if fr is None:
                    continue
                for dr in dR.get(fr, ()):
                    cand[da][dr] += weightR.get(dr, 0.01)
        best_d, inv_d = {}, {}
        for da, c in cand.items():
            best_d[da] = c.most_common(1)[0]
        for da, (dr, sc) in best_d.items():
            if dr not in inv_d or sc > inv_d[dr][1]:
                inv_d[dr] = (da, sc)
        used_set = set(dpar.values())
        n_d = 0
        for dr, (da, sc) in inv_d.items():
            if best_d[da][0] == dr and dr not in used_set and sc >= minimum:
                dpar[da] = dr
                dscore[da] = sc
                n_d += 1

        ja_r = set(par.values())
        porDR = collections.defaultdict(list)
        for fr, ds in dR.items():
            if fr in ja_r:
                continue
            for d in ds:
                porDR[d].append(fr)
        candf = collections.defaultdict(collections.Counter)
        for fa, ds in dA.items():
            if fa in par:
                continue
            for da in ds:
                dr = dpar.get(da)
                if dr is None:
                    continue
                w = weightR.get(dr, 0.01)
                for fr in porDR.get(dr, ()):
                    candf[fa][fr] += w
        best_f, inv_f = {}, {}
        for fa, c in candf.items():
            top = c.most_common(2)
            fr, sc = top[0]
            best_f[fa] = (fr, sc, top[1][1] if len(top) > 1 else 0.0)
        for fa, (fr, sc, _s) in best_f.items():
            if fr not in inv_f or sc > inv_f[fr][1]:
                inv_f[fr] = (fa, sc)
        new_items = []
        for fr, (fa, sc) in inv_f.items():
            m = best_f.get(fa)
            if not m or m[0] != fr or sc < minimum:
                continue
            if m[2] > 0 and sc < m[2] * 1.5:
                continue
            sa, sr = A[fa][0], R[fr][0]
            if sa and sr and not (0.4 <= float(sr) / sa <= 2.5):
                continue
            new_items.append((fr, fa, sc))
        for fr, fa, _sc in new_items:
            par[fa] = fr
        new_total.extend(new_items)
        print('   round %d: +%d data, +%d functions; total %d data, %d functions'
              % (round_, n_d, len(new_items), len(dpar), len(par)))
        if not new_items and not n_d:
            break
    return par, dpar, dscore, new_total


def tokens(alpha_tsv, retail_tsv, anchors, out, rounds, minimum):
    """Match functions by the DATA they reference, and data by the functions.

    The class left without a name - `sub_2B3600`, `sub_25D4F0`, the city's
    `Place`/`datResource&` - is tiny and SILENT: of the 335 functions with
    "datResource" in the name in the alpha, **2 cite any string**. Neither the
    text match nor the graph match gets into that cluster, because its neighbours
    are in the same situation.

    But they are not silent about DATA: each one stamps a vtable
    (`*(a1) = &unk_626F20`). And a vtable matches across builds without depending
    on the address: **two vtables are the same class when they contain the same
    already-matched functions**. That gives a co-matching on two fronts that feed
    each other:

        alpha_data     <-> retail_data     by the already-matched FUNCTIONS referencing them
        alpha_function <-> retail_function by the already-matched DATA they reference

    Each round on one side creates anchors for the other. Only the best mutual
    match is accepted, and each neighbour's weight falls with its popularity: a
    piece of data half the world references identifies nobody.
    """
    A, R = read_dump(alpha_tsv), read_dump(retail_tsv)
    dA, dR = read_data(alpha_tsv), read_data(retail_tsv)
    names = {a: v[1] for a, v in A.items() if v[1]}

    _par, _dpar, _dsc, new_total = _comatch(
        A, R, dA, dR, read_anchors(anchors), rounds, minimum)

    lines = []
    for fr, fa, sc in sorted(new_total):
        nm = names.get(fa)
        if nm:
            lines.append('%08X\t%s\t%.4f\ttoken\t%08X' % (fr, nm, sc, fa))
    io.open(out, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    print('%d new names by token -> %s' % (len(lines), out))
    for l in lines[:10]:
        p = l.split(chr(9))
        print('   %s  %-56s weight %s' % (p[0], p[1][:56], p[2]))


_MAPMOD = None


def _alpha_map():
    """mc3_alpha_map, loaded the same way mc3_ida is - it sits next to us."""
    global _MAPMOD
    if _MAPMOD is None:
        s = importlib.util.spec_from_file_location(
            'mc3_alpha_map', os.path.join(_HERE, 'mc3_alpha_map.py'))
        m = importlib.util.module_from_spec(s)
        s.loader.exec_module(m)
        _MAPMOD = m
    return _MAPMOD


def data(alpha_tsv, retail_tsv, anchors, out, rounds, minimum, mapping=None):
    """Carry the alpha's named GLOBALS over to retail.

    Everything above this line names functions. Nothing names data, and data is
    what the rest of this investigation actually spends its time on: the city,
    shader and texture work all navigate by raw global addresses read out of a
    savestate. The alpha names 3266 of them - `mcCity::s_fCellSize`,
    `g_bNewCityFrustumCulling`, `rmcShader::sm_MicrocodeNames`.

    There is no new machinery here. `_comatch` has always paired alpha data with
    retail data on its way to naming functions, because that is how it reaches
    the silent `datResource` cluster; it just discarded the pairing afterwards.
    This keeps it and joins it to the linker map.

    Two outputs, deliberately separate. A pairing that lands exactly on the
    start of a named symbol goes to `out` and is a name. A pairing that lands
    INSIDE one is written beside it as `<symbol>+0xNN` and is NOT a layer: it is
    only as good as the assumption that the struct did not change shape in the
    year between the builds, which is exactly the assumption this project keeps
    getting burned by. Read it, do not apply it.
    """
    import bisect
    A, R = read_dump(alpha_tsv), read_dump(retail_tsv)
    dA, dR = read_data(alpha_tsv), read_data(retail_tsv)
    _par, dpar, dsc, _nv = _comatch(
        A, R, dA, dR, read_anchors(anchors), rounds, minimum)

    am = _alpha_map()
    syms = [s for s in am.named(am.read_map(mapping or am.MAP)) if am.is_data(s)]
    exact = {}
    for s in syms:
        exact.setdefault(s.addr, s)
    range_ = sorted((s.addr, s.addr + max(s.size, 1), s.name) for s in syms)
    lo = [f[0] for f in range_]
    print('%d named globals in the alpha map' % len(syms))

    lines, interior, silent = [], [], 0
    for da, dr in sorted(dpar.items()):
        sc = dsc.get(da, 0.0)
        s = exact.get(da)
        if s is not None:
            lines.append('%08X\t%s\t%.4f\tdata\t%08X' % (dr, s.name, sc, da))
            continue
        i = bisect.bisect_right(lo, da) - 1
        if i >= 0 and range_[i][0] <= da < range_[i][1]:
            interior.append('%08X\t%s+0x%X\t%.4f\tinterior\t%08X'
                            % (dr, range_[i][2], da - range_[i][0], sc, da))
        else:
            silent += 1

    io.open(out, 'w', encoding='utf-8', newline='\n').write(
        '\n'.join(lines) + '\n')
    outi = os.path.splitext(out)[0] + '_interior.tsv'
    io.open(outi, 'w', encoding='utf-8', newline='\n').write(
        '# NOT a layer: symbol name plus an offset, valid only if the struct did\n'
        '# not change shape between the two builds. For reference, not for apply.\n'
        + '\n'.join(interior) + '\n')
    print('%d data matched: %d with an exact name, %d inside a symbol, '
          '%d with no name in the map' % (len(dpar), len(lines), len(interior), silent))
    print('   %s' % out)
    print('   %s' % outi)
    for l in lines[:12]:
        p = l.split(chr(9))
        print('   %s  %-56s weight %s' % (p[0], p[1][:56], p[2]))


def match(alpha_tsv, retail_tsv, mapping, out, minimum):
    """Pair functions by the strings they reference, then name the retail side.

    A string that dozens of functions use says nothing, so each one is weighted
    by how rare it is; a single shared "$/resources/city/%s_bnd" outweighs ten
    shared "%s". A pair is only accepted when it is the best match for BOTH
    sides, which throws away the ambiguous middle instead of guessing at it.
    """
    A, R = read_dump(alpha_tsv), read_dump(retail_tsv)
    # the name comes from the dump itself; `--map` only backs up what is missing
    names = {a: v[1] for a, v in A.items() if v[1]}
    if mapping:
        for addr, _size, name in read_map(mapping):
            names.setdefault(addr, name)
    print('alpha %d functions (%d named), retail %d'
          % (len(A), len(names), len(R)))

    freq = collections.Counter()
    for _a, (_s, _n, ss, _c) in list(A.items()) + list(R.items()):
        freq.update(ss)
    weight = {s: 1.0 / (1 + freq[s]) for s in freq}

    def score(x, y):
        return sum(weight[s] for s in (x & y))

    by_string = collections.defaultdict(list)
    for ra, (_rs, _rn, rss, _rc) in R.items():
        for s in rss:
            by_string[s].append(ra)

    # SIZE AS A TIE-BREAKER, which this pass lacked and the other two have. Two
    # functions of the SAME .obj often cite the SAME set of strings -
    # `aiRailNetwork::GetExtents` and `aiRailNetwork::LoadRailNetwork` both cite
    # `%s_city` and `city/%s` and nothing else - so the weight TIES and the
    # choice becomes a coin toss. That is how retail 0x503798, 1756 bytes long,
    # got the name of GetExtents, which has 300: the MC2 fork noticed it from the
    # byte count while decompiling. Size changes between builds, but not by an
    # order of magnitude; it is the same 0.4..2.5 range `graph` and `tokens` use.
    def _size_ok(sa, sr):
        return not (sa and sr) or 0.4 <= float(sr) / sa <= 2.5

    best_a = {}
    for aa, (_as_, _an, ass, _ac) in A.items():
        cand = collections.Counter()
        for s in ass:
            for ra in by_string.get(s, ()):
                if _size_ok(A[aa][0], R[ra][0]):
                    cand[ra] += weight[s]
        if cand:
            ra, sc = cand.most_common(1)[0]
            best_a[aa] = (ra, sc)

    best_r = {}
    for aa, (ra, sc) in best_a.items():
        if ra not in best_r or sc > best_r[ra][1]:
            best_r[ra] = (aa, sc)

    lines, accepted = [], 0
    for ra, (aa, sc) in sorted(best_r.items()):
        if best_a.get(aa, (None,))[0] != ra or sc < minimum:
            continue
        name = names.get(aa)
        if not name:
            continue
        common = len(A[aa][2] & R[ra][2])
        lines.append('%08X\t%s\t%.4f\t%d\t%08X' % (ra, name, sc, common, aa))
        accepted += 1
    io.open(out, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    print('%d pairs accepted (mutual, weight >= %.3f) -> %s' % (accepted, minimum, out))
    for l in lines[:12]:
        p = l.split('\t')
        print('   %s  %-56s weight %s, %s strings in common' % (p[0], p[1][:56], p[2], p[3]))


def graph(alpha_tsv, retail_tsv, mapping, anchors_tsv, out, rounds, minimum):
    """Extend the string matches through the call graph.

    A function with no string literals cannot be fingerprinted by what it says,
    but it can be by the company it keeps: who calls it and what it calls. Every
    already-matched neighbour is a constraint, so each round of matching creates
    the anchors the next round needs.

    Popular neighbours carry almost no information - being called by the same
    allocator says nothing - so a neighbour is weighted 1/(1+degree), the same
    rarity idea the string pass uses. Position in the caller's argument list is
    not available here, so a pair must clear both a score floor and a margin over
    the runner-up; a tie means two siblings look alike and we decline to choose.
    """
    A, R = read_dump(alpha_tsv), read_dump(retail_tsv)
    names = {a: v[1] for a, v in A.items() if v[1]}
    if mapping:
        for addr, _size, name in read_map(mapping):
            names.setdefault(addr, name)

    par = {}          # alpha -> retail
    back = {}        # retail -> alpha
    for line in io.open(anchors_tsv, encoding='utf-8'):
        p = line.rstrip().split(chr(9))
        if len(p) >= 5:
            ra, aa = int(p[0], 16), int(p[4], 16)
            par[aa], back[ra] = ra, aa
    print('alpha %d functions, retail %d, %d string anchors'
          % (len(A), len(R), len(par)))

    def callers(D):
        inv = collections.defaultdict(set)
        for a, (_s, _n, _ss, ch) in D.items():
            for c in ch:
                inv[c].add(a)
        return inv

    caA, caR = callers(A), callers(R)

    def degree(D, inv):
        return {a: len(D[a][3]) + len(inv.get(a, ())) for a in D}

    gA, gR = degree(A, caA), degree(R, caR)

    new_total = []
    for round_ in range(1, rounds + 1):
        # a node's fingerprint is the set of ALREADY matched neighbours,
        # translated to the retail side so they can be compared
        def neighbours(a, D, inv, tab):
            out = collections.Counter()
            for c in D[a][3]:
                r = tab.get(c)
                if r is not None:
                    out[('c', r)] += 1
            for c in inv.get(a, ()):
                r = tab.get(c)
                if r is not None:
                    out[('p', r)] += 1
            return out

        idt = {r: r for r in R}
        vA = {a: neighbours(a, A, caA, par) for a in A if a not in par}
        vR = {r: neighbours(r, R, caR, idt) for r in R if r not in back}
        vA = {a: v for a, v in vA.items() if v}
        vR = {r: v for r, v in vR.items() if v}

        # a neighbour's weight by its rarity: being called by the same allocator
        # says nothing, being called by the same mcHood::Load says everything
        neighbour_weight = {r: 1.0 / (1 + gR.get(r, 0)) for r in R}

        by_neighbour = collections.defaultdict(list)
        for r, v in vR.items():
            for k in v:
                by_neighbour[k].append(r)

        candidates_of = {}
        for a, v in vA.items():
            c = collections.Counter()
            for k in v:
                w = neighbour_weight.get(k[1], 0.01)
                for r in by_neighbour.get(k, ()):
                    c[r] += w
            if c:
                candidates_of[a] = c

        best_a = {}
        for a, c in candidates_of.items():
            top = c.most_common(2)
            r, sc = top[0]
            second = top[1][1] if len(top) > 1 else 0.0
            best_a[a] = (r, sc, second)

        best_r = {}
        for a, (r, sc, _sg) in best_a.items():
            if r not in best_r or sc > best_r[r][1]:
                best_r[r] = (a, sc)

        new_items = []
        for r, (a, sc) in best_r.items():
            m = best_a.get(a)
            if not m or m[0] != r or sc < minimum:
                continue
            if m[2] > 0 and sc < m[2] * 1.5:   # tie: two similar siblings
                continue
            # sizes change between builds, but not by an order of magnitude
            sa, sr = A[a][0], R[r][0]
            if sa and sr and not (0.4 <= float(sr) / sa <= 2.5):
                continue
            new_items.append((r, a, sc))

        for r, a, _sc in new_items:
            par[a], back[r] = r, a
        n_nom = sum(1 for _r, a, _s in new_items if a in names)
        print('   round %d: +%d pairs (%d named in the map), total %d'
              % (round_, len(new_items), n_nom, len(par)))
        new_total.extend(new_items)
        if not new_items:
            break

    lines = []
    for r, a, sc in sorted(new_total):
        name = names.get(a)
        if name:
            lines.append('%08X\t%s\t%.4f\tgraph\t%08X' % (r, name, sc, a))
    io.open(out, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    print('%d new names from the graph -> %s' % (len(lines), out))
    for l in lines[:15]:
        p = l.split('\t')
        print('   %s  %-58s weight %s' % (p[0], p[1][:58], p[2]))


def units(mapping=None):
    """{alpha_addr: obj} for every code symbol the linker map places.

    The boundary between two translation units, which is the one thing the
    neighbour pass below cannot see for itself.
    """
    am = _alpha_map()
    out = {}
    for s in am.named(am.read_map(mapping or am.MAP)):
        if am.is_code(s):
            out[s.addr] = am.unit(s.obj)[1]
    return out


def neighbours(alpha_tsv, retail_tsv, anchors, out, tolerance, units_=None):
    """Third pass: extend each anchor along its neighbours, matched by size.

    The two builds compile the same source files, and a compiler emits the
    methods of a class in source order. So a matched pair is not one fact but
    the start of a run: the function before it in the alpha is the function
    before it in retail, and so on, until something contradicts. Sizes are the
    check - they survive relocation, and a whole run of them agreeing is far too
    unlikely to be chance.

    This reaches the cluster the other two passes cannot: `datResource` fixups
    are tiny pointer-relocation routines with no string literals, and their
    neighbours are in the same state, so neither text nor call graph has an
    anchor to grip. But they sit in a row, and one named neighbour anywhere in
    the row is enough.

    A run stops at the first size disagreement rather than trying to resync -
    a wrong alignment would then name every function after it wrongly, and a
    silent shift is the one failure mode worth paying to avoid.
    """
    A, R = read_dump(alpha_tsv), read_dump(retail_tsv)
    names = {a: v[1] for a, v in A.items() if v[1]}

    par, back = {}, {}
    for path in anchors:
        for line in io.open(path, encoding='utf-8'):
            p = line.rstrip('\n').split('\t')
            if len(p) >= 5:
                try:
                    ra, aa = int(p[0], 16), int(p[4], 16)
                except ValueError:
                    continue
                par[aa], back[ra] = ra, aa
    print('alpha %d functions (%d named), retail %d, %d anchors'
          % (len(A), len(names), len(R), len(par)))

    la, lr = sorted(A), sorted(R)
    ia = {a: i for i, a in enumerate(la)}
    ir = {r: i for i, r in enumerate(lr)}

    new_items = []
    for aa, ra in sorted(par.items()):
        if aa not in ia or ra not in ir:
            continue
        for step in (1, -1):
            i, j = ia[aa], ir[ra]
            while True:
                i += step
                j += step
                if not (0 <= i < len(la) and 0 <= j < len(lr)):
                    break
                a2, r2 = la[i], lr[j]
                if a2 in par or r2 in back:
                    # already matched: go on only if they agree, otherwise the run is over
                    if par.get(a2) == r2:
                        continue
                    break
                # The run only holds while it stays inside the SAME .cpp. The
                # compiler emits a class's methods in source order, and that is
                # what supports the step; once the translation unit boundary is
                # crossed the premise ends, and what comes after only matches by
                # coincidence of size - with nothing in the output saying so.
                if units_:
                    u1, u2 = units_.get(aa), units_.get(a2)
                    if u1 and u2 and u1 != u2:
                        break
                sa, sr = A[a2][0], R[r2][0]
                if not sa or not sr or abs(sa - sr) > tolerance:
                    break
                par[a2], back[r2] = r2, a2
                new_items.append((r2, a2, sa, sr))

    lines = []
    for r2, a2, sa, sr in sorted(new_items):
        name = names.get(a2)
        if name:
            lines.append('%08X\t%s\t%d\tneighbour\t%08X' % (r2, name, sa, a2))
    io.open(out, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    print('%d new pairs by neighbourhood, %d named -> %s'
          % (len(new_items), len(lines), out))
    for l in lines[:12]:
        p = l.split('\t')
        print('   %s  %-62s %s bytes' % (p[0], p[1][:62], p[2]))


APPLY = r'''
import ida_auto, ida_name
ida_auto.auto_wait()
PAIRS = %s
n = 0
for ea, name in PAIRS:
    clean = ''.join(c if (c.isalnum() or c == '_') else '_' for c in name)[:120]
    if ida_name.set_name(ea, clean, ida_name.SN_NOCHECK | ida_name.SN_FORCE):
        n += 1
_out.append('named %%d of %%d' %% (n, len(PAIRS)))
'''


def layers(extra=()):
    """Merge the shared store into {retail_addr: (name, origin)}.

    Later layers overwrite earlier ones, so a hand-written name in
    names_manual.tsv survives every re-run of the machine passes.
    """
    tab = {}
    files_ = [os.path.join(SYMBOLS_DIR, c) for c in LAYERS] + list(extra)
    for path in files_:
        if not os.path.isfile(path):
            continue
        origin = os.path.basename(path)
        n = 0
        for line in io.open(path, encoding='utf-8'):
            line = line.rstrip('\n')
            if not line or line.startswith('#'):
                continue
            p = line.split('\t')
            if len(p) >= 2 and p[1].strip():
                try:
                    tab[int(p[0], 16)] = (p[1].strip(), origin)
                except ValueError:
                    continue
                n += 1
        print('   %-20s %d names' % (origin, n))
    return tab


def annotate(ea, name, note):
    """Record a hand-verified name so every other fork picks it up."""
    if not os.path.isdir(SYMBOLS_DIR):
        os.makedirs(SYMBOLS_DIR)
    path = os.path.join(SYMBOLS_DIR, 'names_manual.tsv')
    new = not os.path.isfile(path)
    with io.open(path, 'a', encoding='utf-8') as f:
        if new:
            f.write('# retail_addr\tname\torigin\tnote\n')
        f.write('%08X\t%s\tmanual\t%s\n' % (ea, name, note or ''))
    print('annotated %08X = %s   (%s)' % (ea, name, path))
    print('run `mc3_symbols.py apply` to carry it into the IDA database')


def apply(names_tsv, elf):
    extra = [names_tsv] if names_tsv else []
    print('layers:')
    tab = layers(extra)
    pairs = [(ea, n) for ea, (n, _o) in sorted(tab.items())]
    print('%d unique names after the merge' % len(pairs))
    print(_ida.run(_ida.py(APPLY % repr(pairs)), elf))


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('action', choices=('dump', 'match', 'graph', 'tokens', 'data', 'neighbours', 'apply', 'annotate', 'search'))
    ap.add_argument('args', nargs='*')
    ap.add_argument('--elf')
    ap.add_argument('--map')
    ap.add_argument('--out')
    ap.add_argument('--minimum', type=float, default=0.02)
    ap.add_argument('--rounds', type=int, default=6)
    ap.add_argument('--tolerance', type=int, default=0,
                    help='accepted size difference (0 = exact)')
    # OFF BY DEFAULT, and the measurement decided that. The idea was that a run
    # of neighbours should not cross a .cpp boundary, since source order is what
    # supports the step. Tested against ground truth (the string layer held out
    # of the anchors): 99.4% right without the boundary, 99.5% with it - and
    # 1102 fewer names. It does not pay for itself. It stays as an option.
    ap.add_argument('--units', action='store_true',
                    help='neighbours: stop at the .obj boundary (more conservative, '
                         '-18%% names, no measured gain in precision)')
    a = ap.parse_args()
    if a.action == 'dump':
        dump(a.elf or _ida.DEFAULT_ELF, a.map, a.out or _out_path.path('dump.tsv'))
    elif a.action == 'match':
        match(a.args[0], a.args[1], a.map, a.out or _out_path.path('names.tsv'), a.minimum)
    elif a.action == 'tokens':
        tokens(a.args[0], a.args[1], a.args[2:], a.out or _out_path.path('names_token.tsv'),
               a.rounds, a.minimum)
    elif a.action == 'data':
        data(a.args[0], a.args[1], a.args[2:],
              a.out or os.path.join(SYMBOLS_DIR, 'names_data.tsv'),
              a.rounds, a.minimum, a.map)
    elif a.action == 'graph':
        graph(a.args[0], a.args[1], a.map, a.args[2], a.out or _out_path.path('names_graph.tsv'),
              a.rounds, a.minimum)
    elif a.action == 'neighbours':
        neighbours(a.args[0], a.args[1], a.args[2:], a.out or _out_path.path('names_neighbours.tsv'),
                 a.tolerance, units(a.map) if a.units else None)
    elif a.action == 'annotate':
        annotate(int(a.args[0], 16), a.args[1],
              a.args[2] if len(a.args) > 2 else '')
    elif a.action == 'search':
        target = a.args[0].lower()
        print('layers:')
        tab = layers()
        found = [(ea, n, o) for ea, (n, o) in sorted(tab.items())
                   if target in n.lower() or target in ('%08x' % ea)]
        print('%d results for %r' % (len(found), a.args[0]))
        for ea, n, o in found[:80]:
            print('   %08X  %-68s [%s]' % (ea, n[:68], (o[:-4].replace('names', '').lstrip('_') or 'string') or 'string'))
    else:
        apply(a.args[0] if a.args else None, a.elf or _ida.DEFAULT_ELF)


if __name__ == '__main__':
    main()
