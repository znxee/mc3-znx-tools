"""
mc2_port.py - bring Midnight Club 2 geometry into Midnight Club 3

MC3 ships a TEXT mesh reader and still uses it: `rmcModel::Create` (0x2A8CA0)
does `strrchr(name, '.')` and sends `.mod` to `mshMesh::LoadMod`, anything else
to the binary `mesh` deserialiser. The text side is `gfxModelBase::LoadMod`
(0x1EBFC0) plus `gfxGeometry::LoadMod` (0x1EEC50).

MC2's `.xmod` is the same `version: 1.10` dialect. So the geometry does not have
to be converted at all - it has to be RENAMED, and its textures have to exist
under the names the material blocks ask for.

    python mc2_port.py textures <dir> [--textures DIR]
    python mc2_port.py check <file|dir>
    python mc2_port.py mod <file|dir>

`check` is the part that earns its keep: it applies the grammar read out of the
ELF, not a guess. What the reader requires, in order:

    version: 1.10
    verts: normals: colors: tex1s: tex2s: tangents: materials: adjuncts:
    primitives: matrices: reskins:

then the data lines (`v n c t1 t2 adj`), the primitive lines (`tri str stp`,
all three known to `gfxGeometry::LoadMod`), the `packet`/`mtx`/`reskin` blocks,
and one `mtl NAME { ... }` block per declared material holding

    packets: primitives: textures: illum: ambient: diffuse: specular:
    texture: <n> "NAME"        (repeated `textures:` times)
    attributes:

A texture name that resolves to nothing is NOT fatal: the reader defaults
`$tex0` to "none", and `rmcTextureFactoryPS2::Create` (0x2B9BD8) builds an empty
texture and caches it rather than failing. The mesh draws untextured.
"""

import argparse
import collections
import os
import shutil
import sys

import mc3_output

# The eleven header counts, in the order gfxModelBase::LoadMod reads them.
HEADER = [
    'verts:', 'normals:', 'colors:', 'tex1s:', 'tex2s:', 'tangents:',
    'materials:', 'adjuncts:', 'primitives:', 'matrices:', 'reskins:',
]

# Header count -> the data line that carries one of them. `adjuncts:` and
# `matrices:` are counted separately because they head blocks rather than
# appearing once per item.
LINE_OF = {
    'verts:': 'v', 'normals:': 'n', 'colors:': 'c',
    'tex1s:': 't1', 'tex2s:': 't2',
}

PRIMITIVES = ('tri', 'str', 'stp')

# Keys gfxModelBase::LoadMod reads inside an `mtl` block.
MTL_KEYS = ['packets:', 'primitives:', 'textures:', 'illum:',
              'ambient:', 'diffuse:', 'specular:']

VERSION = '1.10'


def files(target, exts=('.xmod', '.mod')):
    """Every mesh under `target`, which may be one file or a directory."""
    if os.path.isfile(target):
        return [target]
    found = []
    for root, _, names in os.walk(target):
        for n in sorted(names):
            if n.lower().endswith(exts):
                found.append(os.path.join(root, n))
    return found


def le(path):
    with open(path, 'r', errors='replace') as f:
        return f.read().splitlines()


def texture_names(lines):
    """The texture names a mesh's material blocks ask for, in file order."""
    names = []
    for l in lines:
        s = l.strip()
        if s.startswith('texture:') and '"' in s:
            names.append(s.split('"')[1])
    return names


def check(path):
    """Validate one mesh against the reader's grammar.

    Returns a list of complaints; an empty list means the MC3 reader should get
    through the file.
    """
    lines = le(path)
    if not lines:
        return ['empty file']
    errors = []

    first = lines[0].strip()
    if not first.startswith('version:'):
        errors.append('first line is not `version:` but %r' % first[:40])
    elif first.split()[-1] != VERSION:
        errors.append('version %s, the MC3 reader expects %s'
                     % (first.split()[-1], VERSION))

    # Header counts, in order.
    declared_n = {}
    seen = []
    for l in lines[:40]:
        field = (l.split(':')[0] + ':') if ':' in l else ''
        if field in HEADER and field not in declared_n:
            seen.append(field)
            try:
                declared_n[field] = int(l.split(':')[1].strip())
            except (IndexError, ValueError):
                errors.append('unreadable count in %r' % l.strip()[:40])
    missing_n = [c for c in HEADER if c not in declared_n]
    if missing_n:
        errors.append('header without %s' % ' '.join(missing_n))
    elif seen != HEADER:
        errors.append('header out of order')

    # Line tokens.
    count = {}
    n_primitives = 0
    open_mtls = 0
    for l in lines:
        fields = l.split()
        tok = fields[0] if fields else ''
        count[tok] = count.get(tok, 0) + 1
        if tok in PRIMITIVES:
            n_primitives += 1
        if tok == 'mtl':
            open_mtls += 1

    for field, tok in LINE_OF.items():
        if field in declared_n and count.get(tok, 0) != declared_n[field]:
            errors.append('%s says %d, there are %d `%s` lines'
                         % (field, declared_n[field], count.get(tok, 0), tok))

    if declared_n.get('adjuncts:') and not count.get('adj'):
        errors.append('declares %d adjuncts and there is no `adj` line'
                     % declared_n['adjuncts:'])
    if not n_primitives and declared_n.get('primitives:'):
        errors.append('declares %d primitives and there is no tri/str/stp line'
                     % declared_n['primitives:'])
    if 'materials:' in declared_n and open_mtls != declared_n['materials:']:
        errors.append('materials: says %d, there are %d `mtl` blocks'
                     % (declared_n['materials:'], open_mtls))

    # Tokens the reader has no case for.
    known = set(['v', 'n', 'c', 't1', 't2', 'tan', 'adj', 'mtx', 'mtxn',
                      'mtxv', 'packet', 'reskin', 'mtl', '}', '{', ''] +
                     list(PRIMITIVES) + HEADER + MTL_KEYS +
                     ['version:', 'texture:', 'attributes:'] +
                     # attribute types, read as strings inside `attributes:`
                     ['bool', 'int', 'float', 'vector'])
    unknown_tokens = sorted(t for t in count if t not in known)
    if unknown_tokens:
        errors.append('tokens the reader does not handle: %s' % ' '.join(unknown_tokens[:8]))

    # Material blocks.
    inside = False
    keys = set()
    textures = 0
    expected_list = None
    prim_sum = 0
    for l in lines:
        s = l.strip()
        if s.split()[:1] == ['mtl']:
            inside, keys, textures, expected_list = True, set(), 0, None
        elif inside and s == '}':
            missing_n = [c for c in MTL_KEYS if c not in keys]
            if missing_n:
                errors.append('mtl block without %s' % ' '.join(missing_n))
            if expected_list is not None and textures != expected_list:
                errors.append('mtl block says textures: %d and carries %d `texture:` lines'
                             % (expected_list, textures))
            inside = False
        elif inside:
            field = (s.split(':')[0] + ':') if ':' in s else ''
            if field in MTL_KEYS:
                keys.add(field)
            if field == 'textures:':
                try:
                    expected_list = int(s.split(':')[1].strip())
                except (IndexError, ValueError):
                    pass
            if field == 'primitives:':
                try:
                    prim_sum += int(s.split(':')[1].strip())
                except (IndexError, ValueError):
                    pass
            if s.startswith('texture:'):
                textures += 1

    # The header count is the total over the materials - measured on both
    # games: heli.mod 4+550+6+18 = 578, sky_0.xmod 40*4+20 = 180.
    if 'primitives:' in declared_n and prim_sum != declared_n['primitives:']:
        errors.append('primitives: says %d and the mtl blocks add up to %d'
                     % (declared_n['primitives:'], prim_sum))

    for n in texture_names(lines):
        if len(n) >= 64:
            errors.append('texture name with %d characters, the reader reads it into a '
                         '64-byte buffer: %s' % (len(n), n))
    return errors


def find_texture(folder, name):
    """The MC2 file backing a texture name, or None."""
    if not folder:
        return None
    for ext in ('.tex', '.tga', '.movie'):
        p = os.path.join(folder, name + ext)
        if os.path.isfile(p):
            return p
    return None


def cmd_textures(args):
    targets = files(args.target)
    if not targets:
        print('no .xmod/.mod in %s' % args.target)
        return 1
    usage = {}
    for f in targets:
        for n in texture_names(le(f)):
            usage[n] = usage.get(n, 0) + 1
    print('%d meshes, %d distinct texture names' % (len(targets), len(usage)))

    fname = dict((n, find_texture(args.textures, n)) for n in usage)
    if args.textures:
        missing_n = sorted(n for n in usage if not fname[n])
        print('  with a file in %s: %d' % (args.textures, len(usage) - len(missing_n)))
        print('  without a file:   %d' % len(missing_n))
        for n in missing_n[:12]:
            print('     %s' % n)

    dest = mc3_output.path(args.out_path or 'mc2_textures.tsv')
    with open(dest, 'w') as f:
        f.write('name\tmeshes\tfile\n')
        for n in sorted(usage):
            f.write('%s\t%d\t%s\n' % (n, usage[n], fname[n] or ''))
    print('manifest: %s' % dest)
    return 0


# mcInstBucket::Draw (0x1C0178) sends the model's expanded vertex list with a
# DMA tag whose qwc is `2 * adjuncts` as a 16-bit field, but writes the VIF
# UNPACK count as `(unsigned char)(2 * adjuncts)`. Past 127 adjuncts the two
# disagree: the VIF is handed more quadwords than it will consume as data and
# reads the remainder as VIF COMMANDS, which hangs VIF1. The retail chunks are
# 12 to 31 adjuncts, so the game never guards it. Measured on hardware: a
# 148-adjunct model froze the game the moment debris spawned.
MAX_ADJ_INST = 127


def adjuncts(lines):
    return sum(1 for l in lines if l.split()[:1] == ['adj'])


def fits_in_bucket(lines):
    """A complaint if this mesh would overflow the instanced (chunk) path."""
    n = adjuncts(lines)
    if n > MAX_ADJ_INST:
        return ('%d adjuncts: the instanced path sends qwc=%d and VIF NUM=%d, '
                'and the VIF reads the difference as commands. Limit %d.'
                % (n, 2 * n, (2 * n) & 0xFF, MAX_ADJ_INST))
    return None


def box(lines):
    """(min, max) of the `v` lines, or None if there are none."""
    vs = []
    for l in lines:
        fields = l.split()
        if fields[:1] == ['v'] and len(fields) >= 4:
            try:
                vs.append([float(fields[1]), float(fields[2]), float(fields[3])])
            except ValueError:
                pass
    if not vs:
        return None
    mn = [min(p[i] for p in vs) for i in range(3)]
    mx = [max(p[i] for p in vs) for i in range(3)]
    return mn, mx


def rescale(lines, scale, shift):
    """The same file with every `v` line scaled about the origin and moved."""
    out_path = []
    for l in lines:
        fields = l.split()
        if fields[:1] == ['v'] and len(fields) >= 4:
            try:
                p = [float(fields[i]) for i in (1, 2, 3)]
            except ValueError:
                out_path.append(l)
                continue
            out_path.append('v\t%f\t%f\t%f'
                         % tuple(p[i] * scale + shift[i] for i in range(3)))
        else:
            out_path.append(l)
    return out_path


def cmd_fit(args):
    """Rewrite a mesh to sit where another one sat.

    The point is to replace a model the game already loads and draws, so the
    swap is only visible if the geometry lands at the same size in the same
    place. Uniform scale, so nothing is squashed.
    """
    origin = le(args.target)
    dest = le(args.how)
    ca, cb = box(origin), box(dest)
    if not ca or not cb:
        print('one of the two has no `v` lines')
        return 1
    da = [ca[1][i] - ca[0][i] for i in range(3)]
    db = [cb[1][i] - cb[0][i] for i in range(3)]
    if max(da) <= 0:
        print('%s has no extent' % args.target)
        return 1
    scale = args.scale if args.scale else max(db) / max(da)
    centre_a = [(ca[1][i] + ca[0][i]) / 2 for i in range(3)]
    centre_b = [(cb[1][i] + cb[0][i]) / 2 for i in range(3)]
    shift = [centre_b[i] - centre_a[i] * scale for i in range(3)]

    new = rescale(origin, scale, shift)
    name = args.out_path or (os.path.splitext(os.path.basename(args.how))[0] + '.mod')
    path = name if os.path.isabs(name) or os.path.dirname(name) \
        else mc3_output.path(os.path.join('mc2_mod', name))
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    with open(path, 'w') as f:
        f.write('\n'.join(new) + '\n')

    conf = box(new)
    print('%s  ->  %s' % (os.path.basename(args.target), os.path.basename(args.how)))
    print('  source   %.3f x %.3f x %.3f' % tuple(da))
    print('  target   %.3f x %.3f x %.3f' % tuple(db))
    print('  scale    %.4f' % scale)
    print('  result   %.3f x %.3f x %.3f  centre %.2f %.2f %.2f'
          % tuple([conf[1][i] - conf[0][i] for i in range(3)] +
                  [(conf[1][i] + conf[0][i]) / 2 for i in range(3)]))
    errors = check(path)
    print('  check    %s' % ('ok' if not errors else errors[0]))
    if os.path.basename(args.how).startswith('chunk'):
        complaint = fits_in_bucket(new)
        print('  bucket   %s' % (complaint or
                                 '%d adjuncts, within the limit of %d'
                                 % (adjuncts(new), MAX_ADJ_INST)))
    print('%s' % path)
    return 0


def cmd_retex(args):
    """Point every material at one texture name.

    A name nothing answers is safe on the general model path, but the instanced
    chunk path feeds the texture object's own bytes to the GIF as GS register
    writes (mcInstBucket::Draw 0x1C0178, the `!*(tex+76)` branch), so an
    unresolved name there is not obviously harmless. Borrowing a name the game
    already has is one line of text and removes the variable.
    """
    lines = le(args.target)
    before = sorted(set(texture_names(lines)))
    out_path = []
    for l in lines:
        s = l.strip()
        if s.startswith('texture:') and '"' in s:
            prefix = l[:len(l) - len(l.lstrip())]
            n = s.split()[1]
            out_path.append('%stexture: %s "%s"' % (prefix, n, args.texture))
        else:
            out_path.append(l)
    name = args.out_path or args.target
    path = name if os.path.isabs(name) or os.path.dirname(name) \
        else mc3_output.path(name)
    with open(path, 'w') as f:
        f.write('\n'.join(out_path) + '\n')
    print('%s: %s -> %s' % (os.path.basename(path),
                            ', '.join(before) or '(none)', args.texture))
    errors = check(path)
    print('  check %s' % ('ok' if not errors else errors[0]))
    return 0


def cmd_check(args):
    targets = files(args.target)
    if not targets:
        print('no .xmod/.mod in %s' % args.target)
        return 1
    bad_tags = 0
    complaints = {}
    for f in targets:
        errors = check(f)
        if not errors:
            continue
        bad_tags += 1
        for e in errors:
            key = e.split(' says ')[0].split(':')[0][:58]
            complaints[key] = complaints.get(key, 0) + 1
        if args.verbose or len(targets) == 1:
            print('%s' % f)
            for e in errors:
                print('   %s' % e)
    print('%d meshes, %d pass, %d with complaints'
          % (len(targets), len(targets) - bad_tags, bad_tags))
    for q, c in sorted(complaints.items(), key=lambda kv: -kv[1])[:12]:
        print('   %5d  %s' % (c, q))
    return 0


def cmd_mod(args):
    targets = files(args.target, exts=('.xmod',))
    if not targets:
        print('no .xmod in %s' % args.target)
        return 1
    dest = args.out_path or mc3_output.path('mc2_mod')
    if not os.path.isdir(dest):
        os.makedirs(dest)
    written, rejected = 0, 0
    for f in targets:
        errors = check(f)
        if errors and not args.force:
            rejected += 1
            if args.verbose:
                print('rejected %s: %s' % (os.path.basename(f), errors[0]))
            continue
        name = os.path.splitext(os.path.basename(f))[0] + '.mod'
        shutil.copyfile(f, os.path.join(dest, name))
        written += 1
    print('%d written to %s' % (written, dest))
    if rejected:
        print('%d rejected by the check (--force writes them anyway)' % rejected)
    return 0


# --------------------------------------------------------------------------
# a whole city
# --------------------------------------------------------------------------
#
# An MC2 city is `<name>.lvl` (the hood list and the world extents) plus one
# `.hood` per neighbourhood. A hood holds two kinds of thing:
#
#   unique_component   a mesh that exists once, ALREADY IN WORLD COORDINATES.
#                      Verified: the AABB in the hood equals the mesh's own
#                      bounding box to the last digit, so it draws at identity.
#   instance_component a named type placed by a 3x4 matrix (three rows of
#                      rotation, then the translation).
#
# Each name has a `.cc` beside the meshes saying which LOD/part files exist
# (`lod 0 { main refl }`), and the file is `<name>_<lod>_<part>.xmod`.
#
# This writes the three things a consumer needs and nothing else: the meshes
# renamed to `.mod`, the collision, and one flat placement table.


def read_cc(path):
    """(lods, sphere) from a `.cc`: {lod index: [part, ...]}."""
    lods, current, sphere = {}, None, None
    for l in le(path):
        s = l.strip()
        if s.startswith('bounding_sphere:'):
            sphere = s.split(':', 1)[1].split()
        elif s.startswith('lod '):
            try:
                current = int(s.split()[1])
            except (IndexError, ValueError):
                current = None
            if current is not None:
                lods[current] = []
        elif s == '}':
            current = None
        elif current is not None and s and s != '{':
            lods[current].append(s)
    return lods, sphere


def read_hood(path):
    """(unique components, instances) of one `.hood`."""
    lines = le(path)
    comps, insts = [], []
    i = 0
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith('unique_component'):
            name = emin = emax = None
            while i < len(lines) and lines[i].strip() != '}':
                t = lines[i].strip()
                if t.startswith('name:'):
                    name = t.split(None, 1)[1]
                elif t.startswith('emin'):
                    emin = t.split()[1:4]
                elif t.startswith('emax'):
                    emax = t.split()[1:4]
                i += 1
            if name:
                comps.append((name, emin, emax))
        elif s.startswith('instance_component'):
            kind = emin = emax = None
            lines_m = []
            while i < len(lines) and lines[i].strip() != '}':
                t = lines[i].strip()
                if t.startswith('type:'):
                    kind = t.split(None, 1)[1]
                elif t.startswith('emin'):
                    emin = t.split()[1:4]
                elif t.startswith('emax'):
                    emax = t.split()[1:4]
                elif t and t[0] in '-0123456789':
                    fields = t.split()
                    if len(fields) == 3:
                        lines_m.append(fields)
                i += 1
            if kind and len(lines_m) >= 4:
                insts.append((kind, lines_m[:4], emin, emax))
        i += 1
    return comps, insts


def cmd_city(args):
    city = args.target.rstrip('\\/')
    city_name = os.path.basename(city)
    models_dir = os.path.join(city, 'models')
    if not os.path.isdir(models_dir):
        print('%s has no models/ folder' % city)
        return 1
    bounds = args.bounds or os.path.normpath(
        os.path.join(city, '..', '..', 'bound'))
    textures = args.textures or os.path.normpath(
        os.path.join(city, '..', '..', 'texture_x'))

    dest = args.out_path or mc3_output.path('mc2_%s' % city_name)
    for sub in ('model', 'bound'):
        p = os.path.join(dest, sub)
        if not os.path.isdir(p):
            os.makedirs(p)

    # The level file: hood order and world extents.
    lvl = os.path.join(city, city_name + '.lvl')
    extents = []
    hoods_lvl = []
    if os.path.isfile(lvl):
        lines = le(lvl)
        for j, l in enumerate(lines):
            s = l.strip()
            if s.startswith('extents_min') or s.startswith('extents_max'):
                extents.append(lines[j + 1].split())
            if s.startswith('name:'):
                hoods_lvl.append(s.split(None, 1)[1])

    # The hood spells some names with capitals the files do not have
    # (`l_inst_WhereHouse_Type04_x` on disc is all lower case), so every
    # lookup goes through a case-folded index. 34 types and 543 placements
    # were being dropped before this.
    available = dict((n.lower(), n) for n in os.listdir(models_dir))
    rows = []          # (kind, name, lod, part, matrix12, aabb6, hood)
    needs = set()     # mesh basenames without extension
    bnd_kinds = set()
    without_mesh = collections.Counter()

    for h in sorted(os.listdir(city)):
        if not h.endswith('.hood'):
            continue
        hood = h[:-5]
        comps, insts = read_hood(os.path.join(city, h))
        targets = ([('comp', n, None, emin, emax) for n, emin, emax in comps] +
                 [('inst', t, m, emin, emax) for t, m, emin, emax in insts])
        for kind, name, m, emin, emax in targets:
            # ONE lod per model, and ALL the parts of that lod.
            #
            # The previous version stopped at the FIRST part found (`if parts:
            # break` after scanning main/refl/emissive), and so came out with one
            # piece per model when the `.cc` declares two or three. Measured on
            # 12/09 over the 729 models LA places: the `.cc` files declare 1376
            # lod/part pairs and 642 were not being extracted - 242 of `lod1
            # main`, 208 of `lod0 refl` and 192 of `lod1 refl` - and all 642
            # exist as `.xmod` in the source. In MC2 the active lod draws ALL the
            # parts it lists, and at the crossings it is precisely `refl` that
            # carries the road surface.
            #
            # The lod is chosen per MODEL, never per part: MC3 treats each
            # (name, lod, part) as a kind of its own and draws them all at the
            # same time, with no distance switching, so emitting two lods of the
            # same model puts the stub over the good mesh and creates duplicated,
            # almost coplanar surfaces. The LOWEST usable lod wins, which is the
            # most detailed: an empty `lod 0 { }` simply falls to the next lod.
            real_cc = available.get((name + '.cc').lower())
            cc = os.path.join(models_dir, real_cc) if real_cc else ''
            parts = []
            if cc and os.path.isfile(cc):
                lods, _ = read_cc(cc)
                allowed_parts = ([args.part] if args.part
                              else ['main', 'refl', 'emissive'])
                # `--lod N` asks for a SPECIFIC lod: it restricts the search to
                # it instead of always falling to the most detailed one. Without
                # the option, ascending order still applies and the first usable
                # lod wins.
                # PREFER THE LOD THAT HAS `main`. Five LA models declare
                # `lod 0 { refl }` and only carry the main geometry at lod 1
                # (the l_beverlyhills_int_01/02/03 and l_santamonica_int_04/12
                # crossings). With "first usable lod" the port imported only the
                # reflection and the piece vanished from the map - silently,
                # because a lod WITH content had been found.
                if args.lod is not None:
                    order = [args.lod]
                else:
                    order = sorted(lods)
                    with_main = [l for l in order
                                if 'main' in lods[l]
                                and available.get(
                                    ('%s_%d_main.xmod' % (name, l)).lower())]
                    if with_main:
                        order = with_main + [l for l in order
                                            if l not in with_main]
                for lod in order:
                    if lod not in lods:
                        continue
                    found_parts = []
                    for wanted in lods[lod]:
                        if wanted not in allowed_parts:
                            continue
                        fname = available.get(
                            ('%s_%d_%s.xmod' % (name, lod, wanted)).lower())
                        if fname:
                            found_parts.append((lod, wanted, fname))
                    if found_parts:
                        parts = found_parts
                        break
            if not parts:
                # props carry no part suffix: <name>_<lod>.xmod
                for lod in range(4):
                    fname = available.get(('%s_%d.xmod' % (name, lod)).lower())
                    if fname:
                        parts.append((lod, '-', fname))
                        break
            if not parts:
                without_mesh[name] += 1
                continue
            for lod, part, fname in parts:
                needs.add(fname)
                mat = (['1', '0', '0', '0', '1', '0', '0', '0', '1',
                        '0', '0', '0'] if m is None
                       else [c for line in m for c in line])
                rows.append((kind, name, lod, part, mat,
                              (emin or ['0'] * 3) + (emax or ['0'] * 3), hood))
            if kind == 'inst':
                bnd_kinds.add(name)

    # Meshes, renamed. The check runs on each so nothing unreadable ships.
    written, rejected = 0, []
    tex_usage = collections.Counter()
    for fname in sorted(needs):
        origin = os.path.join(models_dir, fname)
        lines = le(origin)
        errors = check(origin)
        if errors and not args.force:
            rejected.append((fname, errors[0]))
            continue
        for n in texture_names(lines):
            tex_usage[n] += 1
        with open(os.path.join(dest, 'model',
                               fname[:-5] + '.mod'), 'w') as f:
            f.write('\n'.join(lines) + '\n')
        written += 1

    # Collision. The whole city is ONE text otgrid - `<city>.bnd`, 4.2 MB for
    # Los Angeles - and the props that can be hit carry their own.
    bnds = 0
    city_bnd = None
    if os.path.isdir(bounds):
        present = set(os.listdir(bounds))
        present = dict((x.lower(), x) for x in present)
        if (city_name + '.bnd').lower() in present:
            city_bnd = os.path.join(dest, 'bound', city_name + '.bnd')
            shutil.copyfile(
                os.path.join(bounds, present[(city_name + '.bnd').lower()]),
                city_bnd)
            bnds += 1
        for t in sorted(bnd_kinds):
            real = present.get((t + '.bnd').lower())
            if real:
                shutil.copyfile(os.path.join(bounds, real),
                                os.path.join(dest, 'bound', real))
                bnds += 1

    table = os.path.join(dest, '%s_place.tsv' % city_name)
    with open(table, 'w') as f:
        f.write('# %s: %d hoods\n' % (city_name, len(hoods_lvl)))
        if len(extents) >= 2:
            f.write('# extents_min\t%s\n# extents_max\t%s\n'
                    % ('\t'.join(extents[0]), '\t'.join(extents[1])))
        f.write('kind\tname\tlod\tpart\tm00\tm01\tm02\tm10\tm11\tm12\t'
                'm20\tm21\tm22\ttx\tty\ttz\t'
                'eminx\teminy\teminz\temaxx\temaxy\temaxz\thood\n')
        for kind, name, lod, part, mat, aabb, hood in rows:
            f.write('%s\t%s\t%d\t%s\t%s\t%s\t%s\n'
                    % (kind, name, lod, part, '\t'.join(mat),
                       '\t'.join(aabb), hood))

    # Textures. The PS2 build of MC2 stores them in the SAME 14-byte-header
    # `.tex` MC3 uses - formats 1 and 15 are both on MC3's list - so they are
    # copied, not converted. The PC build stores format 22 and would need one.
    manifest = os.path.join(dest, '%s_tex.tsv' % city_name)
    missing_n = 0
    copied = 0
    if args.with_textures:
        pt = os.path.join(dest, 'texture')
        if not os.path.isdir(pt):
            os.makedirs(pt)
    with open(manifest, 'w') as f:
        f.write('name\tmeshes\tfile\n')
        for n in sorted(tex_usage):
            a = find_texture(textures, n) or ''
            if not a:
                missing_n += 1
            elif args.with_textures and a.lower().endswith('.tex'):
                shutil.copyfile(a, os.path.join(dest, 'texture',
                                                os.path.basename(a)))
                copied += 1
            f.write('%s\t%d\t%s\n' % (n, tex_usage[n], a))

    print('%s: %d hoods' % (city_name, len(hoods_lvl)))
    print('  placement lines       %d  (%d components, %d instances)'
          % (len(rows),
             sum(1 for x in rows if x[0] == 'comp'),
             sum(1 for x in rows if x[0] == 'inst')))
    print('  distinct meshes       %d written' % written)
    if rejected:
        print('  REJECTED by check     %d  (the first: %s -- %s)'
              % (len(rejected), rejected[0][0], rejected[0][1]))
    if without_mesh:
        print('  no mesh on disc       %d names, %d placements'
              % (len(without_mesh), sum(without_mesh.values())))
        for n, c in without_mesh.most_common(5):
            print('     %-44s x%d' % (n, c))
    print('  collision             %d .bnd%s' %
          (bnds, '  (includes the whole-city otgrid)' if city_bnd else ''))
    print('  textures              %d names, %d without a file%s'
          % (len(tex_usage), missing_n,
             ', %d copied' % copied if copied else ''))
    print('  %s' % table)
    print('  %s' % manifest)

    # The package explains itself. Written here rather than by hand so a
    # re-export never leaves a stale README behind.
    with open(os.path.join(dest, 'README.md'), 'w') as f:
        f.write(CITY_README % dict(
            city=city_name, hoods=len(hoods_lvl), meshes=written,
            placements=len(rows),
            comps=sum(1 for x in rows if x[0] == 'comp'),
            insts=sum(1 for x in rows if x[0] == 'inst'),
            textures=len(tex_usage), copied=copied, missing_n=missing_n,
            bnds=bnds))
    return 0


CITY_README = """\
# Midnight Club 2 - %(city)s, exported for Midnight Club 3

Produced by `mc2_port.py city`. Everything here is text or already in a format
MC3 reads as it stands. `rmcModel::Create` (0x2A8CA0) sends any name ending in
`.mod` to `mshMesh::LoadMod`, and MC2's `.xmod` is the same `version: 1.10`
grammar, so the meshes are renamed, not converted.

| | |
|---|---|
| `model/` | **%(meshes)d** meshes, `.xmod` renamed to `.mod`, nothing else changed |
| `bound/` | %(bnds)d file(s), text collision. The city's own is one `otgrid` covering all of it |
| `texture/` | **%(copied)d** `.tex`, copied with no conversion |
| `%(city)s_place.tsv` | **%(placements)d** placements - %(comps)d components, %(insts)d instances |
| `%(city)s_tex.tsv` | **%(textures)d** texture names and the file behind each (%(missing_n)d have none) |

Every mesh passed `mc2_port.py check`, which applies the grammar read out of the
ELF rather than a guess.

## Two builds, on purpose

Geometry and placement come from the **PC** build, textures from the **PS2**
build, and they are not interchangeable:

- The PS2 build ships **no loose city geometry**. Its `city/<name>` has the
  `.hood` and `.pdef` but no `models/`; `ASSETS/model` holds no city meshes; and
  `resource/<name>/<name>.rsc` opens `RSC0 MIP0 PAL0 TEX0` - a texture pack.
- The PS2 build ships **the right textures**: the same 14-byte header MC3 uses,
  formats 1 and 15, already swizzled with their CLUT. `mc3_tex.py` reads them as
  they are. The PC build stores format 22 and would need a converter.
- The two builds' `.hood` files **differ** - 10 of 13, with different totals -
  so the placement table has to come from the same build as the meshes it names.

## The placement table

Tab separated. Two comment lines carry the world extents from the `.lvl`, then a
header row, then one row per thing to draw.

    kind  name  lod  part  m00..m22  tx ty tz  eminx..emaxz  hood

- **kind** - `comp` or `inst`.
- **name** - the mesh without its `_<lod>_<part>` suffix. The file is
  `model/<name>_<lod>_<part>.mod`; a prop has no part, `<name>_<lod>.mod`.
- **m00..m22** rotation, row major. **tx ty tz** translation.
- **emin/emax** - the AABB straight from the `.hood`.

Two things to know before writing a consumer:

**`comp` rows are already in world coordinates.** Their identity matrix is not a
placeholder - the AABB in the `.hood` equals the mesh's own bounding box to the
last decimal, checked across the set.

**One mesh per name, at the lowest LOD that has the requested part.** Filtering
on a fixed LOD loses whole types: `l_inst_freeway_wall_01x` exists only at LOD 1
and is placed 314 times. `--part` / `--lod` override the default.

## Drawing one row

    gfxModel* m = gfxGetModel(name, 0, flags);      // 0x1ED340, prefix "model"
    gfxState::SetWorld(matrix34);                   // 0x2ADB48
    gfxModel::Draw(m, *(gfxShader***)(m + 4), 0);   // 0x1EB998

`*(m + 4)` is the model's own shader array, so a model from `gfxGetModel` is
self contained - no city shader group, no `.pck`.

**Culling is a requirement, not an optimisation.** %(placements)d draws per frame will
not run. The AABB on each row is there so a consumer can cull by frustum and
distance without reversing MC3's own PVS.

## What is not here

- **Placement has no reader in MC3.** The whole MC2 grammar is in the retail
  string pool (0x641E3F-0x641EB1) but **zero instructions reference it**, while
  the live `.mod` tokens beside it do. Those readers were compiled out. This
  table exists because the `.hood` cannot simply be handed to the game.
- **A texture name nothing answers** is harmless on the ordinary model path -
  `rmcTextureFactoryPS2::Create` (0x2B9BD8) builds an empty texture and carries
  on - but **fatal on the instanced path**, where `mcInstBucket::Draw` feeds the
  texture object's own bytes to the GIF as GS register writes.
- **The 72-adjunct ceiling** measured in `mcInstBucket::Draw` belongs to the
  instanced debris path only. It does not apply to anything here.
"""


def main(argv=None):
    p = argparse.ArgumentParser(
        description='bring Midnight Club 2 geometry into Midnight Club 3')
    sub = p.add_subparsers(dest='cmd')

    t = sub.add_parser('textures', help='what textures the meshes ask for')
    t.add_argument('target')
    t.add_argument('--textures', help='folder holding the MC2 .tex files')
    t.add_argument('--out-path')
    t.set_defaults(func=cmd_textures)

    c = sub.add_parser('check', help='validate against the MC3 text reader')
    c.add_argument('target')
    c.add_argument('-v', '--verbose', action='store_true')
    c.set_defaults(func=cmd_check)

    f = sub.add_parser('fit', help='resize a mesh onto a model the game already draws')
    f.add_argument('target', help='the MC2 mesh')
    f.add_argument('--how', required=True, help='the retail .mod it replaces')
    f.add_argument('--scale', type=float, help='force a factor instead of fitting')
    f.add_argument('--out-path')
    f.set_defaults(func=cmd_fit)

    r = sub.add_parser('retex', help='point every material at one texture name')
    r.add_argument('target')
    r.add_argument('--texture', required=True)
    r.add_argument('--out-path')
    r.set_defaults(func=cmd_retex)

    ci = sub.add_parser('city', help='export a whole MC2 city: meshes, bounds, placement')
    ci.add_argument('target', help='the city folder, e.g. .../city/losangeles')
    ci.add_argument('--out-path')
    ci.add_argument('--bounds', help='MC2 bound/ folder (default: ../../bound)')
    ci.add_argument('--textures', help='MC2 texture folder to resolve names against')
    ci.add_argument('--with-textures', action='store_true',
                    help='copy the .tex files into the package (PS2 build only: '
                         'those are already in the format MC3 reads)')
    ci.add_argument('--part', help='keep only this part (main, refl, emissive...)')
    ci.add_argument('--lod', type=int, help='keep only this LOD')
    ci.add_argument('--force', action='store_true')
    ci.set_defaults(func=cmd_city)

    m = sub.add_parser('mod', help='write .mod copies MC3 can load')
    m.add_argument('target')
    m.add_argument('--out-path')
    m.add_argument('--force', action='store_true')
    m.add_argument('-v', '--verbose', action='store_true')
    m.set_defaults(func=cmd_mod)

    a = p.parse_args(argv)
    if not getattr(a, 'func', None):
        p.print_help()
        return 1
    return a.func(a)


if __name__ == '__main__':
    sys.exit(main())
