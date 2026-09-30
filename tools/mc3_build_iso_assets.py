"""Build the console ASSETS.DAT without compressing the losangeles PCK/PPF files.

The ported city loads those packages as raw data. Compressing them with the
normal dave.py profile saves space, but freezes the game while the city loads
from the ISO. Every other file keeps using the normal compact profile.

The default root comes from MC3_ISO_SOURCE (the folder holding assets/ and
dave.py).
"""
import argparse
import importlib.util
import os
import struct

DEFAULT_ROOT = os.environ.get('MC3_ISO_SOURCE', 'mod iso - build source')
RAW_PREFIXES = (
    'resources/city/losangeles_',
    'resources/prop/losangeles_',
)

LOSANGELES_FLASH = (
    'loading_generic_m.pck',
    'loading_network_m.pck',
    *(f'loading_network_m{i:02d}.pck' for i in range(1, 11)),
    'loadscreen_arcade_m.pck',
    *(f'loadscreen_arcade_m{i:02d}.pck' for i in range(1, 6)),
    'loadscreen_career_filler_m.pck',
    *(f'loadscreen_career_filler_m{i:02d}.pck' for i in range(1, 11)),
    'loadscreen_garage_m.pck',
)


def load_dave(path):
    spec = importlib.util.spec_from_file_location('mc3_dave_builder', path)
    if spec is None or spec.loader is None:
        raise SystemExit(f'could not load {path}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--source', default=os.path.join(DEFAULT_ROOT, 'assets'))
    ap.add_argument('--output', default=os.path.join(DEFAULT_ROOT, 'ASSETS.NEW'))
    ap.add_argument('--dave', default=os.path.join(DEFAULT_ROOT, 'dave.py'))
    args = ap.parse_args()

    missing = [name for name in LOSANGELES_FLASH
               if not os.path.isfile(os.path.join(args.source, 'flash', name))]
    if missing:
        raise SystemExit('missing losangeles flash: ' + ', '.join(missing))

    dave = load_dave(args.dave)
    dave.COMP_DIR_BLOCKLIST = tuple(dict.fromkeys(
        (*dave.COMP_DIR_BLOCKLIST, *RAW_PREFIXES)
    ))
    print('PCK/PPF left uncompressed:')
    for prefix in RAW_PREFIXES:
        print('  ' + prefix)
    dave.build_dave(
        args.source,
        args.output,
        compfiles=True,
        forcecomp=1,
        complevel=9,
        compnames=True,
        dirs=False,
        align=128,
        compalign=False,
    )

    if not os.path.isfile(args.output):
        raise SystemExit('the output file was not created')
    with open(args.output, 'rb') as archive:
        magic = archive.read(4)
        entries = struct.unpack('<I', archive.read(4))[0]
    size = os.path.getsize(args.output)
    if magic != b'Dave':
        raise SystemExit(f'unexpected magic: {magic!r}')
    if size >= 0x80000000:
        raise SystemExit(
            f'ASSETS went over the safe 2 GiB limit: {size} bytes')
    print(f'ASSETS verified: {entries} entries, {size} bytes')


if __name__ == '__main__':
    main()
