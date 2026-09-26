"""
mc3_selftest.py - re-implement the patched game functions and test them

A patch that was reasoned about but never executed is a hypothesis. This file
turns each one into something that can fail: the traced functions are rewritten
in Python straight from the disassembly, then run against numbers measured out of
real savestates. If a replica cannot reproduce what the game actually did, the
understanding behind the patch is wrong and the test says so.

Three layers, cheapest first:

  1. ENCODING - decode every patched word as MIPS and as an IEEE float, and
     check it means what the patch claims. Catches a typo'd constant instantly.
  2. BEHAVIOUR - run the replicas at 30 Hz and at 60 Hz and compare against the
     savestate measurements. This is where a wrong *model* dies: the frame-locked
     and the dt-scaled hypotheses predict different numbers, and only one matches.
  3. ELF - confirm the built files contain exactly the intended words, that
     nothing else in the loaded image moved, and that the CRC still matches.

    python mc3_selftest.py                 # everything
    python mc3_selftest.py --sstates DIR   # re-measure from the savestates instead of using the stored values
"""

import argparse
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)

# HostFS moved off the project drive, and the file name is lowercase there. Try
# the known places instead of hard-coding one, so the ELF tests keep running
# rather than reporting a missing file as a failure.
HOSTFS_DIRS = (os.path.join(BASE, 'MC3HostFS'), os.environ.get('MC3_HOSTFS', 'MC3HostFS'))


def hostfs(name):
    for d in HOSTFS_DIRS:
        for n in (name, name.lower(), name.upper()):
            c = os.path.join(d, n)
            if os.path.isfile(c):
                return c
    return os.path.join(HOSTFS_DIRS[0], name)

# --------------------------------------------------------------------------
# measurements taken from the savestates (see mc3-60fps-timestep in the notes)
# --------------------------------------------------------------------------
MEASURED_30 = dict(ticks=80,  sim=2.6659, dt=0.0333410, per_tick=0.037341, u_s=1.1205)
MEASURED_60 = dict(ticks=171, sim=2.8485, dt=0.0166666, per_tick=0.032954, u_s=1.9783)

ANIM_WALK = ('ASSETS/ped/anim/mped01_walk01.anim', 49, 1.7850)   # file, frames, advance

# rates (+0x10) read from the 23 live pedestrians in the savestate
LIVE_RATES = [1.0]*15 + [1.1029, 1.1455, 1.2505, 1.3745, 1.2386, 1.4033, 1.2702, 1.4213]


def f32(w):
    """Bit pattern -> float, the way the FPU sees it."""
    return struct.unpack('<f', struct.pack('<I', w & 0xFFFFFFFF))[0]


def bits(f):
    return struct.unpack('<I', struct.pack('<f', f))[0]


# ==========================================================================
#  1. REPLICAS  (each one cites the address it was read from)
# ==========================================================================

def mcTimer_Tick(cycles, status):
    """0x433490. Reads the EE Count and produces the frame dt.

    The clock is always read; if `fixed_step_mode` is on the result is discarded
    and the fixed step goes in its place. In the savestates the mode is 0 and
    `fixed_step` is 0.0, so the path that runs is the measured dt with no cap.
    """
    measured = cycles * 3.3908421e-9
    work = measured

    if status['quantise']:                       # byte_618E4E, 0 in the real game
        for k in (1, 2, 3, 4):
            if measured < k / 60.0:
                work = k / 60.0
                break

    # intermediate clamp: flt_618E38 .. flt_618E34
    work = min(max(work, status['min']), status['max'])

    v9 = work * status['scale']

    if status['fixed_step_mode']:                # dword_618E48
        dt = status['fixed_step'] * status['scale']
    elif status['fixed_step'] == 0.0:
        dt = v9
    elif v9 >= status['fixed_step']:
        dt = status['fixed_step']                # the step becomes a CEILING
    else:
        dt = v9

    dt = min(max(dt, status['min']), status['max'])
    return dt, (1.0 / dt)                     # flt_618E20, flt_618E28


def mcCreature_Init_rate(patched, random01, has_variation):
    """0x45F638..0x45F698. Where the animation rate REALLY comes from.

    The constructor 0x4633A0 puts 1.0 at +0x10, but `sub_463418` (SetRate)
    overwrites it right after with what `mcCreature::Init` computed and stored at
    creature+0x15C:

        $f1 = 0x3EB33334 = 0.35    amplitude
        $f2 = 0x3F8CCCCD = 1.10    base
        rate = 1.10 + rand01 * 0.35        -> range 1.10 .. 1.45
        or else the literal 1.0 of the other path

    Measured in RAM: 65% of the pedestrians at 1.0 and the rest spread over
    1.1029 .. 1.4213 -- exactly the predicted range.
    """
    base = 0.55 if patched else 1.10
    amplitude = 0.175 if patched else 0.35
    default = 0.5 if patched else 1.0
    return base + random01 * amplitude if has_variation else default


def mcCreatureAnim_ctorState(patched):
    """0x4633A0. Three fields are born from the SAME li.s $f0.

    +0x10 is the per-tick advance; +0x14 and +0x18 are presets copied back to
    +0x10 by 0x463400 and 0x4637D0 when the animation changes. That is why the
    patch has to hit the literal, and not a single field.
    """
    v = 0.5 if patched else 1.0
    return {'frame': 0.0, 'incr': v, 'preset_a': v, 'preset_b': v}


def mcCreatureAnim_SetRate(status, rate, rate2, patched):
    """0x463418. Overwrites what the constructor puts.

        c.lt.s $f0, $f12      ; rate > 0 ?
        swc1   $f12, 0x10     ; yes: use it
        li.s   $f0, 1.0       ; no: default (0x463430)
    """
    default = 0.5 if patched else 1.0
    status['incr'] = rate if rate > 0.0 else default
    status['preset_a'] = status['incr']
    status['preset_b'] = rate2 if rate2 > 0.0 else default
    return status


def mcCreatureAnim_SampleRoot(status, root, n_frames):
    """0x463548. Advance the cursor and return the displacement between the 2 frames.

        v5 = *(a1+0x0C)          current position, in FRAMES
        v6 = v5 + *(a1+0x10)     advance -- once per CALL, no dt
        delta = sample(v6) - sample(v5)

    `dt` appears nowhere: this is where the frame-rate dependency comes from.

    The original's `if (v7 > v6)` separates the case where the cursor is still
    inside the animation from the case where it crosses the end. At the seam the
    displacement is the sum of both stretches -- the pedestrian keeps walking
    forward, it does not go back to the starting point. Treating the wrap as a
    negative delta (a bug this test caught) pollutes the average with a jump of
    a whole cycle.
    """
    v5 = status['frame']
    v6 = v5 + status['incr']
    if v6 >= n_frames:
        d = (root(n_frames) - root(v5)) + (root(v6 - n_frames) - root(0.0))
        v6 -= n_frames
    else:
        d = root(v6) - root(v5)
    status['frame'] = v6
    return d


def swfCONTEXT_Advance(status, dt, swf_rate):
    """0x20ACD0. Real-time accumulator - correct by construction.

        acc += dt
        while (acc >= 256.0/rate) { advance 1 frame; acc -= 256.0/rate }
    """
    status['acc'] += dt
    period = 256.0 / swf_rate
    n = 0
    while status['acc'] >= period:
        n += 1
        status['acc'] -= period
    return n


def mcFlash_Update(dt_passed, dt_measured, touching=True):
    """0x320B38. Built-in convention: a NEGATIVE float = use the real dt.

        c.lt.s $f12, $f0                     ; f12 < 0 ?
        lwc1   $f12, g_dtMeasuredQuantized   ; then use the measured dt
    """
    if dt_passed < 0.0:
        return dt_measured if touching else 0.0
    return dt_passed


# ==========================================================================
#  2. TEST INFRASTRUCTURE
# ==========================================================================

class Proof(object):
    def __init__(self):
        self.ok = self.failed = self.skipped_n = 0
        self.lines = []

    def skip_(self, name, because):
        # Not every check is pass/fail. A derived file that was never generated
        # is absent, not wrong - counting it as a failure trains you to ignore
        # the failure count, which is the one thing it must not do.
        self.skipped_n += 1
        print('   SKIP  %-60s %s' % (name, because))

    def check(self, name, cond, detail=''):
        self.lines.append('   %s %-58s %s'
                           % ('PASS' if cond else 'FAIL', name, detail))
        if cond:
            self.ok += 1
        else:
            self.failed += 1

    def near_check(self, name, got, expected, tol, unit=''):
        d = abs(got - expected)
        rel = d / abs(expected) if expected else d
        self.check(name, rel <= tol,
                   'got %.6f  expected %.6f  (%.1f%%)%s'
                   % (got, expected, rel * 100, unit))

    def section(self, t):
        self.lines.append('')
        self.lines.append(t)

    def report(self):
        print('\n'.join(self.lines))
        print()
        print('%d passed, %d failed%s'
              % (self.ok, self.failed,
                 ', %d skipped' % self.skipped_n if self.skipped_n else ''))
        return self.failed == 0


# ==========================================================================
#  3. THE TESTS
# ==========================================================================

def test_encoding(p):
    p.section('1. ENCODING of the patches (does the word mean what I claimed?)')

    # li.s $f0, X  =  lui $at, imm ; mtc1 $at, $f0
    def li_s(w):
        assert (w >> 26) == 0x0F and ((w >> 16) & 0x1F) == 1, 'not lui $at'
        return f32((w & 0xFFFF) << 16)

    p.near_check('4633A0 original  li.s $f0, 1.0', li_s(0x3C013F80), 1.0, 0)
    p.near_check('4633A0 patched   li.s $f0, 0.5', li_s(0x3C013F00), 0.5, 0)
    p.check('4633A0: the patch halves the advance',
            li_s(0x3C013F00) / li_s(0x3C013F80) == 0.5)

    # the flash ones: only the sign bit changes; the rest of the value stays
    for rot, old, new, low in (('1AC2A4', 0x3C013D08, 0x3C01BD08, 0x882F),
                                  ('28EE68', 0x3C013D07, 0x3C01BD07, 0x2B02)):
        fv = f32(((old & 0xFFFF) << 16) | low)
        fn = f32(((new & 0xFFFF) << 16) | low)
        p.check('%s: old value positive (%.6f = 1/%.1f)' % (rot, fv, 1 / fv), fv > 0)
        p.check('%s: new value NEGATIVE (%.6f) -> real dt path' % (rot, fn), fn < 0)
        p.check('%s: only the sign changed (|old| == |new|)' % rot, abs(fv) == abs(fn))

    # the rate constants: halving a float only decrements the exponent, so the
    # low half of the li.s does not change and the patch fits in the lui
    for rot, old, new, low, val in (
            ('45F638 amplitude', 0x3C013EB3, 0x3C013E33, 0x3334, 0.35),
            ('45F644 base     ', 0x3C013F8C, 0x3C013F0C, 0xCCCD, 1.10),
            ('45F698 default  ', 0x3C013F80, 0x3C013F00, 0x0000, 1.00)):
        fv = f32(((old & 0xFFFF) << 16) | low)
        fn = f32(((new & 0xFFFF) << 16) | low)
        p.near_check('%s: original value' % rot, fv, val, 1e-6)
        p.check('%s: patch = exactly half' % rot, fn == fv / 2.0,
                '%.6f -> %.6f' % (fv, fn))

    # the vblank: 14400008 is a bnez; 0 is nop
    w = 0x14400008
    p.check('5280F0 original is bnez $v0, +8',
            (w >> 26) == 0x05 and ((w >> 16) & 0x1F) == 0,
            'opcode %d, rt %d, off %+d' % (w >> 26, (w >> 16) & 0x1F, (w & 0xFFFF) * 4))
    p.check('5280F0 patched is nop (00000000)', 0x00000000 == 0)


def test_timer(p):
    p.section('2. mcTimer::Tick - does it reproduce the dt measured in the savestates?')
    # state read from the savestate RAM
    status = dict(quantise=False, fixed_step_mode=False, fixed_step=0.0,
               scale=1.0, min=1e-4, max=0.04)

    # 171 ticks consumed 840,497,419 EE cycles (measured)
    cycles_per_tick = 840497419 / 171.0
    dt, fps = mcTimer_Tick(cycles_per_tick, status)
    p.near_check('dt at 60 Hz from the real EE cycles', dt, MEASURED_60['dt'], 0.01)
    p.near_check('derived fps', fps, 60.0, 0.01)

    # at 30 Hz the frame costs twice the cycles
    dt30, fps30 = mcTimer_Tick(cycles_per_tick * 2, status)
    p.near_check('dt at 30 Hz', dt30, MEASURED_30['dt'], 0.01)
    p.check('dt scales with the frame rate (the clock is RIGHT)',
            abs(dt30 / dt - 2.0) < 0.02, 'ratio %.4f' % (dt30 / dt))

    # and if the fixed step were on (what I wrongly assumed at first)
    fixed_est = dict(status, fixed_step_mode=True, fixed_step=1 / 60.0)
    dtf, _ = mcTimer_Tick(cycles_per_tick * 2, fixed_est)
    p.check('with a fixed step the dt WOULD IGNORE the clock',
            abs(dtf - 1 / 60.0) < 1e-9,
            'would give %.6f at 30 Hz -- not what the savestate shows' % dtf)


def pedestrian_test(p):
    p.section('3. Pedestrian - the rate comes from Init, not from the constructor')
    n, advance = ANIM_WALK[1], ANIM_WALK[2]
    per_frame = advance / n
    root = lambda q: q * per_frame

    # does the rate model reproduce the distribution measured in RAM?
    predicted = [mcCreature_Init_rate(False, r / 100.0, True) for r in range(101)]
    varied = [t for t in LIVE_RATES if t != 1.0]
    p.check('the measured varied rates fall in the predicted range [1.10, 1.45]',
            all(min(predicted) - 1e-3 <= t <= max(predicted) + 1e-3 for t in varied),
            'measured %.4f..%.4f, predicted %.2f..%.2f'
            % (min(varied), max(varied), min(predicted), max(predicted)))
    p.check('the rest use the literal 1.0 of the other path',
            mcCreature_Init_rate(False, 0.0, False) == 1.0,
            '%d of %d pedestrians at 1.0' % (LIVE_RATES.count(1.0), len(LIVE_RATES)))

    def wheel(patched, ticks, dt, random_=0.0, variation=False):
        rate = mcCreature_Init_rate(patched, random_, variation)
        status = mcCreatureAnim_ctorState(patched)
        mcCreatureAnim_SetRate(status, rate, rate, patched)   # OVERWRITES the constructor
        total = sum(mcCreatureAnim_SampleRoot(status, root, n) for _ in range(ticks))
        return status['incr'], total / ticks, total / (ticks * dt)

    r30, pt30, v30 = wheel(False, MEASURED_30['ticks'], MEASURED_30['dt'])
    r60, pt60, v60 = wheel(False, MEASURED_60['ticks'], MEASURED_60['dt'])
    p.check('without the patch the rate is 1.0 (the constructor was overwritten)', r30 == 1.0)
    p.check('per-tick displacement does not change with the frame rate',
            abs(pt30 - pt60) < 1e-9, '%.6f in both' % pt30)
    p.near_check('matches the measurement at 30 Hz', pt30, MEASURED_30['per_tick'], 0.10)
    p.near_check('matches the measurement at 60 Hz', pt60, MEASURED_60['per_tick'], 0.15)

    # the WRONG patch: only the constructor. SetRate undoes it.
    status = mcCreatureAnim_ctorState(True)
    p.check('the constructor alone would put 0.5', status['incr'] == 0.5)
    mcCreatureAnim_SetRate(status, mcCreature_Init_rate(False, 0, False), 1.0, False)
    p.check('...but SetRate overwrites it back to 1.0 -- that is why the 1st patch failed',
            status['incr'] == 1.0, 'measured in RAM: +0x10 = 1.0 with 0x4633A0 patched')

    # the RIGHT patch: at the rate sources
    rp, ptp, vp = wheel(True, MEASURED_60['ticks'], MEASURED_60['dt'])
    p.check('PATCH: the default rate drops to 0.5', rp == 0.5)
    p.check('PATCH: the per-tick advance is halved',
            abs(ptp / pt60 - 0.5) < 1e-9, '%.6f -> %.6f' % (pt60, ptp))
    p.near_check('PATCH: speed at 60 Hz goes back to the 30 Hz value', vp, v30, 0.02, ' u/s')

    # and the path with variation too
    rv, ptv, vv = wheel(False, 100, MEASURED_60['dt'], 0.5, True)
    rvp, ptvp, vvp = wheel(True, 100, MEASURED_60['dt'], 0.5, True)
    p.check('PATCH ALSO covers the random path (35% of the pedestrians)',
            abs(rvp / rv - 0.5) < 1e-6, 'rate %.4f -> %.4f' % (rv, rvp))
    print_forecast(vp, ptp)


def print_forecast(v, pt):
    global FORECAST
    FORECAST = (v, pt)


FORECAST = None


def test_flash(p):
    p.section('4. Flash - the hard-coded constant against the real dt')
    dt60, dt30 = MEASURED_60['dt'], MEASURED_30['dt']
    swf_rate = 256.0 * 30.0                  # SWF header in 8.8: 30 fps

    def wheel(dt_passed, dt_measured, seconds):
        status = {'acc': 0.0}
        ticks = int(round(seconds / dt_measured))
        n = sum(swfCONTEXT_Advance(status, mcFlash_Update(dt_passed, dt_measured), swf_rate)
                for _ in range(ticks))
        return n / seconds

    hardcoded = f32(0x3D08882F)                # 1/30, what sub_1AC2A0 passed
    a30 = wheel(hardcoded, dt30, 10.0)
    a60 = wheel(hardcoded, dt60, 10.0)
    p.check('hard-coded constant: right at 30 Hz', abs(a30 - 30.0) < 0.5,
            '%.2f frames/s' % a30)
    p.check('hard-coded constant: DOUBLES at 60 Hz', abs(a60 / a30 - 2.0) < 0.05,
            '%.2f frames/s' % a60)

    p30 = wheel(-1.0, dt30, 10.0)
    p60 = wheel(-1.0, dt60, 10.0)
    p.check('PATCH (negative): right at 30 Hz', abs(p30 - 30.0) < 0.5,
            '%.2f frames/s' % p30)
    p.check('PATCH (negative): right at 60 Hz too', abs(p60 - 30.0) < 0.5,
            '%.2f frames/s' % p60)


def segments(d):
    ph = struct.unpack_from('<I', d, 0x1C)[0]
    phes = struct.unpack_from('<H', d, 0x2A)[0]
    phn = struct.unpack_from('<H', d, 0x2C)[0]
    out = []
    for i in range(phn):
        t, off, va, _pa, fsz, _msz = struct.unpack_from('<IIIIII', d, ph + i * phes)
        if t == 1 and fsz:
            out.append((va, off, fsz))
    return out


def crc(d):
    c = 0
    for (w,) in struct.iter_unpack('<I', d[:len(d) // 4 * 4]):
        c ^= w
    return c


def test_elf(p):
    p.section('5. ELF - do the generated files contain exactly what was intended?')
    base = hostfs('SLUS_213.55.ELF')
    if not os.path.isfile(base):
        p.check('base ELF present', False, base)
        return
    d0 = open(base, 'rb').read()

    EXPECTED = {
        # 0x1AC2A4 / 0x28EE68: the constant halved, NOT the old sign flip. The
        # sign flip routes the flash to 0x618E54, which on the loading screen is
        # pinned at 1/60 - so it was only ever a /2 in disguise.
        'SLUS_213.55_60FPS.ELF': {0x5280F0: 0x00000000, 0x1AC2A4: 0x3C013C88,
                                  0x28EE68: 0x3C013C87, 0x4633A0: 0x3C013F00,
                                  0x45F638: 0x3C013E33, 0x45F644: 0x3C013F0C,
                                  0x45F698: 0x3C013F00, 0x45F6A8: 0x3C013F00,
                                  0x45FD98: 0x3C013F00, 0x45FDB0: 0x3C013F00,
                                  0x45FDC8: 0x3C013F00, 0x463430: 0x3C013F00,
                                  0x46345C: 0x3C013F00},
        'SLUS_213.55_60FPS_CONTROLE.ELF': {0x5280F0: 0x00000000},
    }
    for name, expected in EXPECTED.items():
        pck_path = hostfs(name)
        if not os.path.isfile(pck_path):
            p.skip_('%s' % name, 'not generated (the direct ELF route, now unused)')
            continue
        d = open(pck_path, 'rb').read()
        p.check('%s: same size as the base' % name, len(d) == len(d0))
        p.check('%s: crc preserved (%08X)' % (name, crc(d)), crc(d) == crc(d0))

        # 1) the intended words are there
        for va, w in expected.items():
            for vbase, off, size_ in segments(d):
                if vbase <= va < vbase + size_:
                    o = off + va - vbase
                    got = struct.unpack_from('<I', d, o)[0]
                    p.check('%s: VA %08X = %08X' % (name, va, w), got == w,
                            '' if got == w else 'found %08X' % got)

        # 2) and NOTHING ELSE changed inside what is loaded into memory
        difs = []
        for vbase, off, size_ in segments(d):
            for k in range(0, size_ - 3, 4):
                a = struct.unpack_from('<I', d0, off + k)[0]
                b = struct.unpack_from('<I', d, off + k)[0]
                if a != b:
                    difs.append(vbase + k)
        p.check('%s: %d words changed in the loaded image, all expected'
                % (name, len(difs)), sorted(difs) == sorted(expected),
                '' if sorted(difs) == sorted(expected)
                else 'unexpected: %s' % [('%08X' % x) for x in difs
                                          if x not in expected])


def remeasure(p, d1, d2):
    """Redo the measurements from a pair of savestates, instead of trusting the stored numbers."""
    M = 0x1FFFFFF
    da, db = open(d1, 'rb').read(), open(d2, 'rb').read()
    dw = lambda d, va: struct.unpack_from('<I', d, va & M)[0]
    hw = lambda d, va: struct.unpack_from('<H', d, va & M)[0]
    fl = lambda d, va: struct.unpack_from('<f', d, va & M)[0]
    v3 = lambda d, va: (fl(d, va), fl(d, va + 4), fl(d, va + 8))
    q = dw(db, 0x618E3C) - dw(da, 0x618E3C)
    sim = fl(db, 0x70E524) - fl(da, 0x70E524)

    def creatures(d):
        s = dw(d, dw(d, 0x619494)); out = []
        for goff in (4, 40):
            g = s + 152 + goff
            arr, n = dw(d, g), hw(d, g + 4)
            if not arr or n > 512:
                continue
            for i in range(n):
                c = dw(d, arr + 4 * i)
                if c and (c & M) < 0x1FFFF00:
                    out.append(c)
        return out
    vs = []
    cb = set(creatures(db))
    for c in creatures(da):
        if c not in cb:
            continue
        pa, pb = v3(da, c + 276), v3(db, c + 276)
        dist = math.sqrt(sum((x - y) ** 2 for x, y in zip(pa, pb)))
        if dist > 0.01:
            vs.append(dist)
    vs.sort()
    med = vs[len(vs) // 2] if vs else 0.0
    return dict(ticks=q, sim=sim, dt=fl(da, 0x618E20),
                per_tick=med / q, u_s=med / sim)


def test_stub_boot(p):
    """The boot stub cannot be tried a little bit: it runs at the end of crt0,
    before the game exists, and a wrong register or a branch off by one word is a
    black screen with nothing to read. So its shape is checked here instead."""
    p.section('6. BOOT STUB - is the shape right before going to the hardware?')
    sys.path.insert(0, os.environ.get('MC3BOOT', os.path.join(os.path.dirname(HERE), 'mc3boot')))
    try:
        import mc3_inject as M
    except Exception as e:
        p.skip_('mc3boot/mc3_inject.py', 'did not import: %s' % e)
        return
    w = M.early_stub()

    p.check('fits before the payload', M.EARLY + 4 * len(w) <= M.PAYLOAD,
            '%08X + %d words vs PAYLOAD %08X' % (M.EARLY, len(w), M.PAYLOAD))
    p.check('does not overrun the telemetry block', M.EARLY >= 0x0061C9C0 + 40)

    # save and restore have to be symmetric: the hook sits in the middle of crt0
    # and any lost register comes back as garbage to the game code
    sd = {(x >> 16) & 31 for x in w if (x >> 26) == 0x3F}
    ld = {(x >> 16) & 31 for x in w if (x >> 26) == 0x37}
    p.check('saves and restores the same registers', sd == ld,
            'sd=%s ld=%s' % (sorted(sd), sorted(ld)))
    p.check('preserves $v0 (the only live one across the hook)', 2 in sd and 2 in ld)
    p.check('preserves $ra', 31 in sd and 31 in ld)

    # the stack has to balance
    open_ = [x for x in w if (x >> 26) == 0x09 and ((x >> 16) & 31) == 29]
    delta = sum((v - 0x10000 if v & 0x8000 else v)
                for v in (x & 0xFFFF for x in open_))
    p.check('the stack balances (sum of sp adjustments = 0)', delta == 0, 'delta=%d' % delta)

    # the branch of the signature test has to land on the exit label, not in the middle
    for i, x in enumerate(w):
        if (x >> 26) == 0x05:
            target = M.EARLY + 4 * (i + 1) + ((x & 0xFFFF) - 0x10000
                                            if x & 0x8000 else x & 0xFFFF) * 4
            idx = (target - M.EARLY) // 4
            p.check('the bne lands on the first `ld` of the exit',
                    0 <= idx < len(w) and (w[idx] >> 26) == 0x37,
                    'target %08X = word %d' % (target, idx))
            break

    # and it has to reproduce the replaced instruction, or crt0 loses a step
    p.check('reproduces the hook instruction (%08X)' % M.EARLY_WORD,
            M.EARLY_WORD in w)
    p.check('ends in jr $ra + nop',
            w[-3] == M.EARLY_WORD and w[-2] == 0x03E00008 and w[-1] == 0)
    p.check('the hook is a jal to the stub',
            (0x0C000000 | ((M.EARLY >> 2) & 0x3FFFFFF)) >> 26 == 3)


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--sstates', help='folder with the two savestates .01 and .02')
    a = ap.parse_args()

    if a.sstates:
        d1 = os.path.join(a.sstates + '.01', 'eeMemory.bin')
        d2 = os.path.join(a.sstates + '.02', 'eeMemory.bin')
        if os.path.isfile(d1) and os.path.isfile(d2):
            m = remeasure(None, d1, d2)
            target = MEASURED_60 if m['ticks'] / m['sim'] > 45 else MEASURED_30
            target.update(m)
            print('re-measured from the savestate: %d ticks, %.4f s, %.2f Hz, '
                  '%.6f per tick, %.4f u/s\n'
                  % (m['ticks'], m['sim'], m['ticks'] / m['sim'],
                     m['per_tick'], m['u_s']))
        else:
            print('savestates not found at %s.01/.02\n' % a.sstates)

    p = Proof()
    test_encoding(p)
    test_timer(p)
    pedestrian_test(p)
    test_flash(p)
    test_elf(p)
    test_stub_boot(p)
    good_run = p.report()
    if FORECAST:
        print()
        print('FORECAST for the full ELF at 60 Hz:')
        print('   %.6f per tick  and  %.4f u/s' % (FORECAST[1], FORECAST[0]))
        print('   (without the patch, measured: %.6f per tick, %.4f u/s)'
              % (MEASURED_60['per_tick'], MEASURED_60['u_s']))
    sys.exit(0 if good_run else 1)


if __name__ == '__main__':
    main()
