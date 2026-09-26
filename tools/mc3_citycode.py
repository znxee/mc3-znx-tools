"""Clone the assets a city is found by, from one short code to another.

The city's SHORT CODE is not a label. It is the key half of a filename: the
loader builds `loading_generic_<code>.pck` and `loadscreen_career_filler_<code>
06.pck` and opens them. A city registered with a code nothing answers to hangs
on the loading screen, which is how this was found - by scanning EE RAM for the
strings the game had built, not by reading code.

    python mc3_citycode.py --donor t --new m            # what it would copy
    python mc3_citycode.py --donor t --new m --write

WHAT IT WILL AND WILL NOT COPY

A file is city-keyed when its name ends in `_<code>` or `_<code><digits>` before
the extension. That alone would also match `foo_t.pck` where the `t` means
something else entirely, so it is not enough: a file is only cloned when THE
SAME SLOT EXISTS FOR AT LEAST TWO OTHER KNOWN CITY CODES. `loading_generic_t`
qualifies because `_a`, `_d` and `_s` are all there beside it; an accidental
`_t` has no such family and is left alone.

That check is the whole reason this is safe to run over a directory of 156
files. It is also why it can be wrong in the other direction - a city asset that
only ever existed for one city will not be recognised. If a boot still hangs,
scan RAM for the name it built rather than trusting this list.

DIRECTORIES TOO, IN NAME MODE

A city owns four directories named after it, and missing them is not cosmetic:
`tune/traffic/<name>/` holds the traffic densities, so a city cloned without
them comes up with no traffic at all.

    city/<name>          tune/ped/<name>
    tune/race/<name>     tune/traffic/<name>

They are cloned whole, and files INSIDE get the same renaming - `tokyo_dawn
.density` becomes `modcity_dawn.density`, while `dawn_clear/` and `fped01.cal`
keep the names they have. A destination that already exists is merged into, not
replaced: hand-made files there survive.
"""
import argparse
import os
import re
import shutil

DEFAULT_ASSETS = os.path.join(os.environ.get('MC3_HOSTFS', 'MC3HostFS'), 'ASSETS')
CODES = ('s', 'a', 'd', 't')          # sd, atlanta, detroit, tokyo
NAMES = ('sd', 'atlanta', 'detroit', 'tokyo')
MIN_FAMILY = 2                          # other cities that must share the slot


def slice_name(name, city):
    """('<city>_', 'rest') - the data assets use the NAME, not the short code.

    Two conventions in one game, and it costs a boot to find out: the loading
    screen builds `loading_generic_<code>.pck` from the short code, while the
    city data builds `city/<name>_<time>_<weather>.ppf` from the full name.
    """
    # Two shapes, and missing the second one cost `tokyo.override` on the first
    # run: `<name>_<rest>` for tokyo_midnight_clear.pck, and `<name>.<ext>` for
    # tokyo.loc / tokyo.locinf / tokyo.override, which have no underscore at all.
    low = name.lower()
    if low.startswith(city + '_'):
        return city + '_', name[len(city) + 1:]
    if low.startswith(city + '.'):
        return city, name[len(city):]        # the rest already starts with the dot
    return None


def slice_(name, code):
    """('prefix', 'suffix') if `name` has the form <pref>_<code><digits><ext>."""
    m = re.match(r'^(.*_)%s(\d*)(\.[^.]+)$' % re.escape(code), name, re.I)
    if not m:
        return None
    return m.group(1), m.group(2) + m.group(3)


def family(files, assemble, donor, universe):
    """How many OTHER cities have exactly the same slot."""
    n = 0
    for c in universe:
        if c == donor:
            continue
        if assemble(c).lower() in files:
            n += 1
    return n


def scan(root, donor, new):
    by_name = donor in NAMES
    universe = NAMES if by_name else CODES
    planes = []
    for base, _dirs, names in os.walk(root):
        present = set(n.lower() for n in names)
        for n in names:
            if by_name:
                cut = slice_name(n, donor)
                if not cut:
                    continue
                _pref, rest = cut
                # `rest` carries its own separator: '_' was consumed above for
                # the underscore shape, and the dotted shape keeps its dot.
                sep = '' if rest.startswith('.') else '_'
                assemble = lambda c, r=rest, j=sep: c + j + r
            else:
                cut = slice_(n, donor)
                if not cut:
                    continue
                prefix, suffix = cut
                assemble = lambda c, p=prefix, s=suffix: p + c + s
            if family(present, assemble, donor, universe) < MIN_FAMILY:
                continue
            planes.append((os.path.join(base, n),
                           os.path.join(base, assemble(new)),
                           family(present, assemble, donor, universe)))
    return sorted(planes)


def scan_dirs(root, donor, new):
    """[(source, target)] of the directories named after the city.

    Only in name mode: `tune/traffic/t` is not a thing, the directories are
    always spelled out. The family check is the same idea as for files - a
    directory only counts when at least two other cities have one beside it.
    """
    planes = []
    for base, dirs, _f in os.walk(root):
        present = set(d.lower() for d in dirs)
        for d in dirs:
            if d.lower() != donor:
                continue
            siblings = sum(1 for c in NAMES if c != donor and c in present)
            if siblings < MIN_FAMILY:
                continue
            planes.append((os.path.join(base, d), os.path.join(base, new)))
    return sorted(planes)


def clone_dir(origin, dest, donor, new, write_):
    """Recursive copy, renaming the files prefixed with the city."""
    n = 0
    for base, _dirs, names in os.walk(origin):
        rel = os.path.relpath(base, origin)
        target = dest if rel == '.' else os.path.join(dest, rel)
        if write_ and not os.path.isdir(target):
            os.makedirs(target)
        for f in names:
            cut = slice_name(f, donor)
            out_path = (new + cut[1] if cut and cut[1].startswith('.')
                     else new + '_' + cut[1] if cut else f)
            full = os.path.join(target, out_path)
            if os.path.exists(full):
                continue
            n += 1
            if write_:
                shutil.copyfile(os.path.join(base, f), full)
    return n


def main():
    p = argparse.ArgumentParser(
        description='clone the assets indexed by the city short code')
    p.add_argument('--assets', default=DEFAULT_ASSETS)
    p.add_argument('--donor', default='t',
                   help='short code (t) for the screen assets, or name (tokyo) '
                        'for the city data - the mode comes from this')
    p.add_argument('--new', required=True, help='short code or name of the new city')
    p.add_argument('--write', action='store_true',
                   help='without it, only list what it would do')
    a = p.parse_args()

    if a.donor.lower() == a.new.lower():
        raise SystemExit('donor and target are the same code')

    donor = a.donor.lower()
    if donor not in CODES and donor not in NAMES:
        raise SystemExit('donor %r is neither a code (%s) nor a name (%s)'
                         % (a.donor, '/'.join(CODES), '/'.join(NAMES)))
    mode = 'name' if donor in NAMES else 'code'
    print('mode: city %s  (%s -> %s)' % (mode, donor, a.new.lower()))
    planes = scan(a.assets, donor, a.new.lower())
    if not planes:
        raise SystemExit('nothing with %s %r in %s' % (mode, a.donor, a.assets))

    new_items = made_n = 0
    for origin, dest, siblings in planes:
        exists = os.path.exists(dest)
        marks = 'already exists' if exists else ('copied' if a.write else 'would copy')
        print('   %-46s -> %-46s [%d siblings] %s'
              % (os.path.basename(origin), os.path.basename(dest),
                 siblings, marks))
        if exists:
            continue
        new_items += 1
        if a.write:
            shutil.copyfile(origin, dest)
            made_n += 1

    if mode == 'name':
        dirs = scan_dirs(a.assets, donor, a.new.lower())
        print()
        for origin, dest in dirs:
            n = clone_dir(origin, dest, donor, a.new.lower(), a.write)
            print('   %-46s -> %-30s %d file(s) %s'
                  % (os.path.relpath(origin, a.assets),
                     os.path.relpath(dest, a.assets), n,
                     'copied' if a.write else 'to copy'))
            made_n += n if a.write else 0
            new_items += n

    print()
    print('%d files with %s %r, %d missing for %r%s'
          % (len(planes), mode, a.donor, new_items, a.new,
             (', %d copied' % made_n) if a.write else ' (use --write)'))


if __name__ == '__main__':
    main()
