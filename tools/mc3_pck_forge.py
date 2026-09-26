r"""Turn .mesh files into .pck by letting the game compile them, with no game.

WHAT RUNS WHERE

The engine's own mesh compiler (rmcGeometry::Packet::Init, reached only through
rmcModel::Create's .mesh branch) is the thing that emits VIF. This drives it in
the one state where nothing else is loaded: `pck_forge.mod` hooks the
`jal mcGame::Execute` at 0x001A1024 and never chains, so the executable becomes
a batch program with 25 MB of heap instead of the 1.4 MB the front end leaves.

    python mc3_pck_forge.py meshes/*.mesh --city detroit_dawn_clear --out output/forge

Steps, all of them here so a run is reproducible from one command:

  1. stage each .mesh as HostFS model/pNNN.mesh, and the city's colour-per-
     vertex palette as HostFS cpv.bin (see mc3_cpv_palette.py for why the
     condition matters: the wrong palette changes the packets and reports
     nothing);
  2. write a MINIMAL mc3boot.ini - core plus pck_forge, nothing else - after
     backing up the existing one, so other mods cannot perturb the run, and
     restore it afterwards no matter how the run ends;
  3. boot PCSX2 headless and wait for the module's ENDB line;
  4. rebuild the heap image with mc3_heap_dump.py and cut one .pck per root
     with mc3_heap_to_pck.py --batch.

WHAT IT DOES NOT DO: check the geometry. Compare against the source with
mc3_view.py or mc3_coverage.py before installing anything.
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PCSX2 = os.environ.get('MC3_PCSX2', 'pcsx2-qt.exe')
LOG = os.path.join(os.environ.get('MC3_PCSX2_DATA', os.path.expanduser('~/Documents/PCSX2')),
                   'logs', 'cli_test.txt')
HOSTFS = os.environ.get('MC3_HOSTFS', 'MC3HostFS')
CITIES = os.path.join(HOSTFS, 'ASSETS', 'resources', 'city')

INI = """; written by mc3_pck_forge.py - the original was moved to mc3boot.ini.forge_bak
[mods]
core.mod = bootstrap
pck_forge.mod = shim

[boot]
mdls = %d
spac = %d
"""


def stage(meshes, hostfs):
    # ASSETS/model, not <hostfs>/model. rmcModel::Create reads the .mesh through
    # datAssetManager, whose root mc3boot points at ASSETS - so "model/p000.mesh"
    # resolves under there. Staged in the wrong place, Create does NOT fail: it
    # returns a model with no geometry and allocates the same bytes every time,
    # which is exactly how this was found (identical 30 096-byte dumps for
    # different meshes, and for no mesh at all).
    model_dir = os.path.join(hostfs, 'ASSETS', 'model')
    os.makedirs(model_dir, exist_ok=True)
    for old in glob.glob(os.path.join(model_dir, 'p[0-9][0-9][0-9].mesh')):
        os.remove(old)
    for i, src in enumerate(meshes):
        shutil.copyfile(src, os.path.join(model_dir, 'p%03d.mesh' % i))
    return model_dir


def stage_palette(city, hostfs, palette=None):
    """The palette is inline in the city .pck at MapRoot+0x290; 0 means none."""
    dst = os.path.join(hostfs, 'cpv.bin')
    if palette:                  # raw 4096 bytes, e.g. dumped from another run
        shutil.copyfile(palette, dst)
        return palette
    if not city:
        if os.path.exists(dst):
            os.remove(dst)          # no palette -> the module turns CPV off
        return None
    src = city if os.path.exists(city) else os.path.join(CITIES, city + '.pck')
    if not os.path.exists(src):
        raise SystemExit('city not found: %s' % src)
    sys.path.insert(0, HERE)
    import mc3_cpv_palette
    with open(dst, 'wb') as f:
        f.write(mc3_cpv_palette.read_palette(src))
    return src


def run_game(seconds):
    """Boot and wait for ENDB rather than a fixed time - the dump is the signal."""
    if os.path.exists(LOG):
        os.remove(LOG)
    cmd = [PCSX2, '-batch', '-nogui', '-logfile', LOG, '--',
           os.path.join(HOSTFS, 'mc3boot.elf')]
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    try:
        while time.time() - t0 < seconds:
            time.sleep(1.0)
            if p.poll() is not None:
                break
            try:
                with open(LOG, encoding='utf-8', errors='replace') as f:
                    if 'PFDN' in f.read():
                        return time.time() - t0
            except OSError:
                pass
        return None
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()


def blobs_from_log(path):
    """Yields (base, bytes) for each BLOB..ENDB section in the log."""
    text_line = re.compile(r'^([0-9A-F]{8}):([0-9A-F]+)$')
    base = None
    buf = None
    for raw in open(path, encoding='utf-8', errors='replace'):
        t = raw.strip()
        cut = t.find(']')
        if cut >= 0 and t.startswith('['):
            t = t[cut + 1:].strip()
        if t.startswith('BLOB '):
            p = t.split()
            base, buf = int(p[1], 16), bytearray(int(p[2], 16))
        elif t.startswith('ENDB'):
            if buf is not None:
                yield base, bytes(buf)
            buf = None
        elif buf is not None:
            m = text_line.match(t)
            if m:
                off = int(m.group(1), 16)
                b = bytes.fromhex(m.group(2))
                buf[off:off + len(b)] = b


def roots_from_log(path):
    out = []
    for raw in open(path, encoding='utf-8', errors='replace'):
        line = raw.strip()
        cut = line.find(']')
        if cut >= 0 and line.startswith('['):
            line = line[cut + 1:].strip()
        if line.startswith('ROOT '):
            t = line.split()
            out.append((int(t[1]), int(t[2], 16)))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('mesh', nargs='+', help='.mesh files, in output order')
    ap.add_argument('--palette', help='4096 raw bytes to install instead of --city')
    ap.add_argument('--city', help='city .pck or a name like detroit_dawn_clear; '
                                   'omit to build with no CPV at all')
    ap.add_argument('--out', default='output/forge', help='directory for the .pck files')
    ap.add_argument('--chunk', type=int, default=10, metavar='N',
                    help='models per boot. The rebuilder finds a model by walking '
                         'pointers, so models sharing a heap can weld together when '
                         'vertex data happens to spell a block address - measured: '
                         'clean at 10 per boot, badly inflated at 299. 0 = all at once.')
    ap.add_argument('--spacer', type=int, default=14, metavar='MB',
                    help='burn this much of the low free store first, so the working '
                         'heap lands in a high address range; 0 disables. Low heaps '
                         'make the rebuilder weld neighbouring models together.')
    ap.add_argument('--seconds', type=float, default=300.0)
    ap.add_argument('--hostfs', default=HOSTFS)
    a = ap.parse_args()

    meshes = [m for pat in a.mesh for m in sorted(glob.glob(pat))] or a.mesh
    for m in meshes:
        if not os.path.exists(m):
            raise SystemExit('does not exist: %s' % m)
    os.makedirs(a.out, exist_ok=True)

    city = stage_palette(a.city, a.hostfs, a.palette)
    size = a.chunk if a.chunk > 0 else len(meshes)
    batches_ = [meshes[i:i + size] for i in range(0, len(meshes), size)]
    print('  %d mesh(es) in %d batch(es) of up to %d, palette: %s' % (
        len(meshes), len(batches_), size,
        os.path.basename(city) if city else 'none (CPV off)'))

    ini = os.path.join(a.hostfs, 'mc3boot.ini')
    bak = ini + '.forge_bak'
    shutil.copyfile(ini, bak)
    made_n, empties, written = 0, [], set()
    try:
        for n, batch in enumerate(batches_):
            stage(batch, a.hostfs)
            with open(ini, 'w', encoding='utf-8') as f:
                f.write(INI % (len(batch), a.spacer))
            took = run_game(a.seconds)
            if took is None:
                raise SystemExit('batch %d: the game did not reach the end of the dump' % n)

            blobs = list(blobs_from_log(LOG))
            roots = roots_from_log(LOG)
            if not blobs or not roots:
                raise SystemExit('batch %d: log without BLOB or ROOT' % n)
            base, blob = blobs[-1]
            dump = os.path.join(a.out, 'heap_%03d.bin' % n)
            with open(dump, 'wb') as f:
                f.write(blob)
            tsv = os.path.join(a.out, 'batch_%03d.tsv' % n)
            target = 0
            with open(tsv, 'w', encoding='utf-8') as f:
                for idx, root in roots:
                    if idx >= len(batch):
                        continue
                    name = os.path.splitext(os.path.basename(batch[idx]))[0]
                    out_path = os.path.join(a.out, name + '.mesh.pck')
                    if out_path in written:      # two inputs can share a basename
                        out_path = os.path.join(a.out, '%s_%03d.mesh.pck' % (name, idx))
                    written.add(out_path)
                    if not root:
                        empties.append(name)
                        continue
                    f.write('%08X' % root + chr(9) + out_path + chr(10))
                    target += 1
            if target:
                subprocess.run([sys.executable, os.path.join(HERE, 'mc3_heap_to_pck.py'),
                                dump, '--base', '%08X' % base, '--batch', tsv],
                               check=True, capture_output=True)
            made_n += target
            os.remove(dump)
            print('  batch %3d/%d: %2d model(s), heap %08X, %.0f s'
                  % (n + 1, len(batches_), target, base, took))
    finally:
        shutil.move(bak, ini)       # the .ini is shared with other forks

    print('  %d .pck generated%s' % (made_n,
          (', %d without geometry (%s)' % (len(empties), ', '.join(empties[:3]))) if empties else ''))


if __name__ == '__main__':
    main()
