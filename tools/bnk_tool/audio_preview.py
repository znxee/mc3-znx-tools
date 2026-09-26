"""Engine-audio preview: loads a bank (.bnk + .td), decodes its PS-ADPCM samples and
renders an audible approximation of what an AudioBlockFull will sound like at a given
RPM (or across an RPM sweep), applying the range crossfades and the volume curve.

The mixing model is an approximation of the game engine (the exact runtime mix code
isn't public): per range, a triangular envelope (0 at min_rpm, 1 at mid_rpm, 0 at
max_rpm; edge ranges hold 1 outward), normalized across active ranges, each sample
pitch-shifted by current_rpm / mid_rpm, then scaled by the block's volume curve
evaluated at the normalized RPM position. Good enough to hear how range boundaries,
crossfades and volume-curve edits will feel - not a bit-exact recreation.
"""
import math
import os
from typing import Dict, List, Optional, Tuple  # noqa: F401 (Tuple used in annotations)

from .parser import parse_bnk
from .ps_adpcm import decode_ps_adpcm
from .wav import build_wav_bytes, clamp16

PREVIEW_RATE = 32000  # output sample rate for previews


class PreviewError(Exception):
    pass


class BankSamples:
    """Decoded PCM cache for one bank: name -> (pcm list[int16], native_rate, loop)"""

    def __init__(self, bnk_path: str, td_path: Optional[str] = None):
        self.bnk_path = bnk_path
        self.td_path = td_path
        self.samples: Dict[str, dict] = {}
        self._load()

    def _load(self):
        h = parse_bnk(self.bnk_path)
        with open(self.bnk_path, "rb") as f:
            data = f.read()

        names_by_sound = {}
        if self.td_path and os.path.exists(self.td_path):
            from .td import parse_td
            td = parse_td(self.td_path)
            names_by_sound = {s["index"]: s["name"] for s in td["sounds"]}

        # group streams by sound_index; a sound may have several pitch-layered grains -
        # keep the first as the representative sample for preview
        for s in h.streams:
            if s.codec != "PSX":
                continue
            name = names_by_sound.get(s.sound_index)
            if name is None:
                name = s.name or f"STREAM_{s.index}"
            payload = data[s.start_offset:s.start_offset + s.stream_size]
            entry = {
                "pcm": None,          # decoded lazily
                "payload": payload,
                "rate": s.sample_rate,
                "loop": s.loop_flag,
                "loop_start": s.loop_start,
                "loop_end": s.loop_end,
                "stream_index": s.index,
            }
            self.samples.setdefault(name.upper(), entry)

    def names(self) -> List[str]:
        return list(self.samples.keys())

    def get_pcm(self, name: str) -> Optional[dict]:
        entry = self.samples.get(name.upper())
        if entry is None:
            return None
        if entry["pcm"] is None:
            entry["pcm"] = decode_ps_adpcm(entry["payload"])
        return entry


def sample_wav(bank: BankSamples, name: str) -> bytes:
    entry = bank.get_pcm(name)
    if entry is None:
        raise PreviewError(f"sample '{name}' not found in the bank")
    return build_wav_bytes(entry["pcm"], entry["rate"])


# ---------------------------------------------------------------------------
# engine mix simulation
# ---------------------------------------------------------------------------

def _range_envelope(rpm: float, rng, is_first: bool, is_last: bool) -> float:
    """Triangular crossfade envelope for one range at a given rpm."""
    lo, mid, hi = rng.min_rpm, rng.mid_rpm, rng.max_rpm
    if hi <= lo:
        return 0.0
    mid = min(max(mid, lo + 1e-3), hi - 1e-3)
    if rpm < lo:
        return 1.0 if (is_first and rpm < lo) else 0.0
    if rpm > hi:
        return 1.0 if (is_last and rpm > hi) else 0.0
    if rpm <= mid:
        if is_first:
            return 1.0 if rpm <= mid else 0.0  # first range: full until mid, then fades
        return (rpm - lo) / (mid - lo)
    return 1.0 - (rpm - mid) / (hi - mid) if not is_last else 1.0


def evaluate_curve_segments(segments, x: float) -> Optional[float]:
    """Evaluates the piecewise quadratic curve at x (uses the 3 stored points per
    segment to refit y = A x^2 + B x + C, same math the game data carries)."""
    if not segments:
        return None
    from .pck import fit_quadratic
    best = None
    for seg in segments:
        x0, y0, cx, cy, x1, y1 = seg[:6]
        lo, hi = (x0, x1) if x0 <= x1 else (x1, x0)
        if lo <= x <= hi:
            a, b, c = fit_quadratic((x0, y0), (cx, cy), (x1, y1))
            return a * x * x + b * x + c
        # remember closest segment ends for clamping
        if best is None or abs(x - lo) < best[0]:
            best = (abs(x - lo), y0 if x0 <= x1 else y1)
        if abs(x - hi) < best[0]:
            best = (abs(x - hi), y1 if x0 <= x1 else y0)
    return best[1] if best else None


class EngineMixer:
    """Renders preview audio for one AudioBlock using a loaded BankSamples."""

    def __init__(self, block, bank: BankSamples):
        self.block = block
        self.bank = bank
        # set by render_gear_drive to map engine RPM onto the recorded audio range
        self.perf_idle = None
        self.perf_max = None

    def _active_ranges(self):
        out = []
        for r in self.block.ranges[: self.block.active_range_count]:
            name = r.sample_name.split(":", 1)[1] if ":" in r.sample_name else r.sample_name
            if not name or name.upper() == "EMPTY":
                continue
            entry = self.bank.get_pcm(name)
            if entry is None or not entry["pcm"]:
                continue
            out.append((r, name, entry))
        return out

    def volume_at(self, rpm: float) -> float:
        blk = self.block
        span = blk.max_rpm_rec - blk.min_rpm_rec
        x = (rpm - blk.min_rpm_rec) / span if span > 0 else 0.0
        x = min(max(x, 0.0), 1.0)
        vol_curve = blk.curves.get("volume_curve")
        y = evaluate_curve_segments(vol_curve.segments if vol_curve else [], x)
        return min(max(y, 0.0), 1.5) if y is not None else 1.0

    def render(self, rpm_fn, duration: float, out_rate: int = PREVIEW_RATE,
               vol_fn=None) -> bytes:
        """rpm_fn(t_seconds) -> rpm; vol_fn(t) -> extra volume multiplier (optional,
        used by the gear simulator for the throttle cut during a shift).
        Returns WAV bytes at out_rate."""
        actives = self._active_ranges()
        if not actives:
            raise PreviewError("no active range with a sample found in the bank")

        n_out = int(duration * out_rate)
        out = [0.0] * n_out
        n_ranges = len(actives)
        # per-range playback positions (fractional sample cursors into looping pcm)
        cursors = [0.0] * n_ranges

        for i in range(n_out):
            t = i / out_rate
            rpm = rpm_fn(t)
            master = self.volume_at(rpm)
            if vol_fn is not None:
                master *= vol_fn(t)

            weights = []
            for k, (rng, _name, _entry) in enumerate(actives):
                w = _range_envelope(rpm, rng, k == 0, k == n_ranges - 1)
                weights.append(w)
            total_w = sum(weights)
            if total_w <= 1e-6:
                continue

            acc = 0.0
            for k, (rng, _name, entry) in enumerate(actives):
                w = weights[k] / total_w
                if w <= 0.001:
                    continue
                pcm = entry["pcm"]
                n = len(pcm)
                if n < 2:
                    continue
                mid = rng.mid_rpm if rng.mid_rpm > 0 else max(rng.min_rpm, 1.0)
                pitch_ratio = max(0.25, min(4.0, rpm / mid))
                step = pitch_ratio * entry["rate"] / out_rate

                pos = cursors[k]
                i0 = int(pos)
                frac = pos - i0
                # loop region: use baked loop points when present, else whole sample
                if entry["loop"] and 0 <= entry["loop_start"] < entry["loop_end"] <= n:
                    lo, hi = entry["loop_start"], entry["loop_end"]
                else:
                    lo, hi = 0, n
                if i0 + 1 >= hi:
                    pos = lo + ((pos - lo) % max(1, (hi - lo)))
                    i0 = int(pos)
                    frac = pos - i0
                s0 = pcm[i0]
                s1 = pcm[i0 + 1] if i0 + 1 < n else s0
                sample = s0 + (s1 - s0) * frac
                acc += sample * w
                cursors[k] = pos + step

            out[i] = acc * master

        pcm16 = [clamp16(int(v)) for v in out]
        return build_wav_bytes(pcm16, out_rate)

    def render_constant(self, rpm: float, duration: float = 2.0) -> bytes:
        return self.render(lambda t: rpm, duration)

    def render_sweep(self, rpm_from: float = None, rpm_to: float = None,
                     duration: float = 6.0) -> bytes:
        blk = self.block
        lo = rpm_from if rpm_from is not None else blk.min_rpm_rec
        hi = rpm_to if rpm_to is not None else blk.max_rpm_rec
        half = duration / 2.0

        def rpm_fn(t):
            if t <= half:
                return lo + (hi - lo) * (t / half)
            return hi - (hi - lo) * ((t - half) / half)

        return self.render(rpm_fn, duration)

    def _engine_to_audio_rpm(self, engine_rpm: float) -> float:
        """Maps an engine-scale RPM (idle..MaxRPM) onto the block's recorded audio RPM
        range (min_rpm_rec..max_rpm_rec). The recorded samples only span the audio
        range (e.g. 1000-4200 for the 350Z) while the real engine revs higher
        (up to 6800); without this mapping the mixer would pitch the samples far above
        their recorded range and sound shrill."""
        blk = self.block
        lo_e = self.perf_idle if self.perf_idle is not None else blk.min_rpm_rec
        hi_e = self.perf_max if self.perf_max is not None else blk.max_rpm_rec
        lo_a, hi_a = blk.min_rpm_rec, blk.max_rpm_rec
        if hi_e <= lo_e or hi_a <= lo_a:
            return engine_rpm
        frac = (engine_rpm - lo_e) / (hi_e - lo_e)
        frac = min(max(frac, 0.0), 1.0)
        return lo_a + frac * (hi_a - lo_a)

    def render_gear_drive(self, perf, duration: float = 12.0,
                          shift_dip_gain: float = 3.0) -> bytes:
        """Full-throttle acceleration through every gear using the vehicle's real
        performance data (bnk_tool.vehsim.VehiclePerf):

        - per-gear top speeds interpolated Low..High (GearBias spacing);
        - RPM climbs to the upshift point (OptRPM, the peak-power RPM where the game
          upshifts), then drops by the gear-ratio step on each shift (wheel speed is
          continuous);
        - engine RPM is mapped onto the block's recorded audio RPM range so the samples
          play across the range they were recorded for (no shrill over-pitching);
        - per-gear time from a constant-power model, scaled to `duration`;
        - during GearChangeTime the block's upshift_curve is applied as a volume
          dip envelope (the audible throttle cut) while RPM eases down.

        Approximation of the runtime mixer, but every number comes from the pck.
        """
        eng = perf.engine
        trans = perf.trans
        # expose idle/max to the engine->audio mapping used inside render()
        self.perf_idle = max(eng.idle_rpm, eng.engage_rpm, 1.0)
        self.perf_max = eng.max_rpm if eng.max_rpm > self.perf_idle else self.block.max_rpm_rec

        speeds = trans.gear_speeds()
        n_gears = len(speeds)
        # upshift at OptRPM (peak power) when plausible, else near the rev limit
        if eng.idle_rpm < eng.opt_rpm <= eng.max_rpm:
            rpm_up = eng.opt_rpm
        else:
            rpm_up = eng.max_rpm * 0.9
        rpm_start = self.perf_idle
        shift_time = max(0.15, min(2.0, trans.gear_change_time))

        # timeline: [(t_start, t_end, kind, (rpm_a, rpm_b))]  -- all in engine RPM
        # kind 'pull': rpm ramps rpm_a -> rpm_b in gear; 'shift': rpm eases down
        pulls = []
        rpm_in = rpm_start
        for g in range(n_gears):
            # constant-power: dt proportional to v2^2 - v1^2 (v = speed at this rpm)
            v1 = speeds[g] * rpm_in / max(1.0, eng.max_rpm)
            v2 = speeds[g] * rpm_up / max(1.0, eng.max_rpm)
            weight = max(0.05, v2 * v2 - v1 * v1)
            pulls.append((rpm_in, rpm_up, weight))
            if g + 1 < n_gears:
                rpm_in = rpm_up * speeds[g] / speeds[g + 1]

        total_shift = shift_time * (n_gears - 1)
        pull_budget = max(1.0, duration - total_shift)
        total_weight = sum(w for _a, _b, w in pulls)

        timeline = []
        t = 0.0
        for g, (ra, rb, w) in enumerate(pulls):
            dt = pull_budget * w / total_weight
            timeline.append((t, t + dt, "pull", (ra, rb)))
            t += dt
            if g + 1 < n_gears:
                rpm_next = rb * speeds[g] / speeds[g + 1]
                timeline.append((t, t + shift_time, "shift", (rb, rpm_next)))
                t += shift_time
        total = t

        upshift = self.block.curves.get("upshift_curve")
        upshift_segments = upshift.segments if upshift else []
        # normalize the envelope's own domain so any authoring range works
        env_x0, env_x1 = 0.0, 1.0
        if upshift_segments:
            xs = [s[0] for s in upshift_segments] + [s[4] for s in upshift_segments]
            env_x0, env_x1 = min(xs), max(xs)
            if env_x1 - env_x0 < 1e-6:
                env_x1 = env_x0 + 1.0

        def shift_env(frac: float) -> float:
            if not upshift_segments:
                return 0.6 * math.sin(math.pi * min(max(frac, 0.0), 1.0))  # fallback dip
            x = env_x0 + (env_x1 - env_x0) * min(max(frac, 0.0), 1.0)
            y = evaluate_curve_segments(upshift_segments, x)
            return max(0.0, y or 0.0) * shift_dip_gain

        def rpm_fn(t_now):
            engine_rpm = rpm_up
            for t0, t1, kind, (ra, rb) in timeline:
                if t0 <= t_now <= t1:
                    frac = (t_now - t0) / max(1e-9, t1 - t0)
                    if kind == "pull":
                        engine_rpm = ra + (rb - ra) * frac
                    else:
                        engine_rpm = ra + (rb - ra) * min(1.0, frac * 1.6)  # rpm falls early
                    break
            return self._engine_to_audio_rpm(engine_rpm)

        def vol_fn(t_now):
            for t0, t1, kind, _ in timeline:
                if kind == "shift" and t0 <= t_now <= t1:
                    frac = (t_now - t0) / max(1e-9, t1 - t0)
                    return max(0.15, 1.0 - shift_env(frac))
            return 1.0

        try:
            return self.render(rpm_fn, total, vol_fn=vol_fn)
        finally:
            self.perf_idle = self.perf_max = None
