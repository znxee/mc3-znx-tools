"""Translated from vgmstream's src/util/spu_utils.c (spu2_note_to_pitch / ps_note_to_pitch /
spu2_pitch_to_sample_rate), used by bnk_sony.c to turn center_note/center_fine into a sample rate.
bnk_sony.c always calls this with note=60, fine=0 (the stream itself carries no note/fine, only
a "center" calibration point), so those are exposed as optional args defaulting to that."""

_NOTE_PITCH_TABLE = [
    0x8000, 0x879C, 0x8FAC, 0x9837, 0xA145, 0xAADC,
    0xB504, 0xBFC8, 0xCB2F, 0xD744, 0xE411, 0xF1A1,
]

_FINE_PITCH_TABLE = [
    0x8000, 0x800E, 0x801D, 0x802C, 0x803B, 0x804A, 0x8058, 0x8067,
    0x8076, 0x8085, 0x8094, 0x80A3, 0x80B1, 0x80C0, 0x80CF, 0x80DE,
    0x80ED, 0x80FC, 0x810B, 0x811A, 0x8129, 0x8138, 0x8146, 0x8155,
    0x8164, 0x8173, 0x8182, 0x8191, 0x81A0, 0x81AF, 0x81BE, 0x81CD,
    0x81DC, 0x81EB, 0x81FA, 0x8209, 0x8218, 0x8227, 0x8236, 0x8245,
    0x8254, 0x8263, 0x8272, 0x8282, 0x8291, 0x82A0, 0x82AF, 0x82BE,
    0x82CD, 0x82DC, 0x82EB, 0x82FA, 0x830A, 0x8319, 0x8328, 0x8337,
    0x8346, 0x8355, 0x8364, 0x8374, 0x8383, 0x8392, 0x83A1, 0x83B0,
    0x83C0, 0x83CF, 0x83DE, 0x83ED, 0x83FD, 0x840C, 0x841B, 0x842A,
    0x843A, 0x8449, 0x8458, 0x8468, 0x8477, 0x8486, 0x8495, 0x84A5,
    0x84B4, 0x84C3, 0x84D3, 0x84E2, 0x84F1, 0x8501, 0x8510, 0x8520,
    0x852F, 0x853E, 0x854E, 0x855D, 0x856D, 0x857C, 0x858B, 0x859B,
    0x85AA, 0x85BA, 0x85C9, 0x85D9, 0x85E8, 0x85F8, 0x8607, 0x8617,
    0x8626, 0x8636, 0x8645, 0x8655, 0x8664, 0x8674, 0x8683, 0x8693,
    0x86A2, 0x86B2, 0x86C1, 0x86D1, 0x86E0, 0x86F0, 0x8700, 0x870F,
    0x871F, 0x872E, 0x873E, 0x874E, 0x875D, 0x876D, 0x877D, 0x878C,
]


def _c_div(a: int, b: int) -> int:
    """C-style truncating integer division (toward zero)."""
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def _ps_note_to_pitch(center_note: int, center_fine: int, note: int, fine: int) -> int:
    fine_idx = fine + center_fine

    fine_adjust = fine_idx
    if fine_idx < 0:
        fine_adjust = fine_idx + 0x7F
    fine_adjust = _c_div(fine_adjust, 128)

    note_adjust = note + fine_adjust - center_note
    unk3 = _c_div(note_adjust, 6)
    if note_adjust < 0:
        unk3 -= 1

    fine_idx -= fine_adjust * 128

    unk2 = -1 if note_adjust < 0 else 0
    if unk3 < 0:
        unk3 -= 1

    unk2 = _c_div(unk3, 2) - unk2
    unk1 = unk2 - 2
    note_idx = note_adjust - (unk2 * 12)

    if note_idx < 0 or (note_idx == 0 and fine_idx < 0):
        note_idx += 12
        unk1 = unk2 - 3

    if fine_idx < 0:
        note_idx = (note_idx - 1) + fine_adjust
        fine_idx += (fine_adjust + 1) * 128

    pitch = (_NOTE_PITCH_TABLE[note_idx] * _FINE_PITCH_TABLE[fine_idx]) >> 16

    if unk1 < 0:
        pitch = (pitch + (1 << (-unk1 - 1))) >> -unk1

    return pitch & 0xFFFF


def spu2_note_to_pitch(center_note: int, center_fine: int, note: int = 60, fine: int = 0) -> int:
    is_negative = bool(center_note & 0x80)
    cn = (0x100 - center_note) if is_negative else center_note

    pitch = _ps_note_to_pitch(cn, center_fine, note, fine)
    if pitch > 0x4000:
        pitch = 0x4000

    if not is_negative:
        pitch = _c_div(pitch * 44100, 48000)

    return pitch


def spu2_pitch_to_sample_rate(pitch: int) -> int:
    return _c_div(48000 * pitch, 4096)


def center_to_sample_rate(center_note: int, center_fine: int) -> int:
    pitch = spu2_note_to_pitch(center_note, center_fine)
    return spu2_pitch_to_sample_rate(pitch)


_ALL_RATES = None  # lazy {(center_note, center_fine): sample_rate} cache, 65536 entries


def _all_rates():
    global _ALL_RATES
    if _ALL_RATES is None:
        table = {}
        for cn in range(256):
            for cf in range(256):
                table[(cn, cf)] = center_to_sample_rate(cn, cf)
        _ALL_RATES = table
    return _ALL_RATES


def sample_rate_to_center(target_sample_rate: int):
    """Finds the (center_note, center_fine) byte pair whose resulting sample rate is
    closest to target_sample_rate (the format only supports ~4096 quantized pitch steps
    per octave range, so an exact match isn't always possible for arbitrary rates)."""
    table = _all_rates()
    best_key = min(table, key=lambda k: abs(table[k] - target_sample_rate))
    return best_key[0], best_key[1], table[best_key]
