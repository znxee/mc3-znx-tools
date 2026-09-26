"""PS-ADPCM helpers translated from vgmstream's src/coding/psx_decoder.c
(ps_find_loop_offsets / ps_bytes_to_samples), used to recover loop points and sample
counts that some BNK versions don't store explicitly in the table (see bnk_sony.c)."""


def ps_bytes_to_samples(num_bytes: int, channels: int) -> int:
    if channels <= 0:
        return 0
    return num_bytes // channels // 0x10 * 28


def ps_find_loop_offsets(data: bytes, start_offset: int, data_size: int, channels: int, interleave: int):
    """Returns (loop_flag, loop_start_sample, loop_end_sample)."""
    if data_size == 0 or channels == 0 or (channels > 1 and interleave == 0):
        return False, 0, 0

    num_samples = 0
    loop_start = 0
    loop_end = 0
    loop_start_found = False
    loop_end_found = False
    offset = start_offset
    max_offset = start_offset + data_size
    interleave_consumed = 0
    n = len(data)

    while offset < max_offset:
        if offset + 2 > n:
            break
        flag = data[offset + 1] & 0x0F  # low nibble of the 2nd byte (BE u16 header & 0xF)

        if flag == 0x06 and not loop_start_found:
            loop_start = num_samples
            loop_start_found = True

        if flag == 0x03 and not loop_end:
            loop_end = num_samples + 28
            loop_end_found = True

            if (channels == 1 and offset + 0x10 < max_offset and offset + 0x12 <= n
                    and (data[offset + 0x11] & 0x0F) == 0x06):
                loop_end = 0
                loop_end_found = False

            if loop_start_found and loop_end_found:
                break

        num_samples += 28
        offset += 0x10
        interleave_consumed += 0x10
        if interleave_consumed == interleave:
            interleave_consumed = 0
            offset += interleave * (channels - 1)

    if loop_start_found and loop_end_found:
        return True, loop_start, loop_end
    return False, 0, 0


def _iter_frames(data_size: int, channels: int, interleave: int):
    """Yields (sample_position, frame_byte_offset) for channel-0 frames only, following
    the exact same stepping ps_find_loop_offsets uses (loop points are channel-0-relative)."""
    num_samples = 0
    offset = 0
    interleave_consumed = 0
    while offset < data_size:
        yield num_samples, offset
        num_samples += 28
        offset += 0x10
        interleave_consumed += 0x10
        if channels > 1 and interleave and interleave_consumed == interleave:
            interleave_consumed = 0
            offset += interleave * (channels - 1)


def set_ps_adpcm_loop(payload: bytes, channels: int, interleave: int,
                       loop_start_sample: int, loop_end_sample: int):
    """Bakes loop-start (flag 0x06) / loop-end (flag 0x03) markers directly into a raw
    PS-ADPCM stream's frame flag nibbles, clearing any pre-existing ones first. This is
    metadata living *inside* the audio bitstream itself (not a table field), so this is
    still "no re-encode": only 1-nibble flags are touched, sample data is untouched.
    Loop points must land on a 28-sample frame boundary (PS-ADPCM's frame size).
    Returns (new_payload, ok) - ok is False if the requested points don't land on a frame.
    """
    buf = bytearray(payload)
    n = len(buf)
    target_end_sample = max(loop_end_sample - 28, 0)
    start_off = None
    end_off = None

    for sample_pos, off in _iter_frames(n, channels, interleave):
        if off + 2 > n:
            break
        flag = buf[off + 1] & 0x0F
        if flag in (0x06, 0x03):
            buf[off + 1] &= 0xF0
        if sample_pos == loop_start_sample:
            start_off = off
        if sample_pos == target_end_sample:
            end_off = off

    ok = start_off is not None and end_off is not None
    if start_off is not None:
        buf[start_off + 1] = (buf[start_off + 1] & 0xF0) | 0x06
    if end_off is not None:
        buf[end_off + 1] = (buf[end_off + 1] & 0xF0) | 0x03

    return bytes(buf), ok


def clear_ps_adpcm_loop(payload: bytes, channels: int, interleave: int) -> bytes:
    """Clears any existing loop-start/loop-end flag nibbles, without setting new ones."""
    buf = bytearray(payload)
    n = len(buf)
    for _, off in _iter_frames(n, channels, interleave):
        if off + 2 > n:
            break
        if (buf[off + 1] & 0x0F) in (0x06, 0x03):
            buf[off + 1] &= 0xF0
    return bytes(buf)


# ---------------------------------------------------------------------------
# Decoder (translated from vgmstream's decode_psx, float-math version)
# ---------------------------------------------------------------------------

_PS_ADPCM_COEFS = (
    (0.0, 0.0),
    (0.9375, 0.0),
    (1.796875, -0.8125),
    (1.53125, -0.859375),
    (1.90625, -0.9375),
)


def decode_ps_adpcm(payload: bytes):
    """Decodes mono PS-ADPCM to a list of int16 samples (28 per 0x10 frame).
    Faithful to vgmstream's decode_psx (float-math version): every frame is decoded,
    frames flagged 0x07 ('end + don't decode') output silence, history carries through."""
    out = []
    hist1 = 0
    hist2 = 0
    n = len(payload)

    for off in range(0, n - 0x0F, 0x10):
        header = payload[off]
        coef_index = (header >> 4) & 0xF
        shift_factor = header & 0xF
        flag = payload[off + 1] & 0x0F

        if coef_index > 4:
            coef_index = 0
        if shift_factor > 12:
            shift_factor = 9

        c0, c1 = _PS_ADPCM_COEFS[coef_index]
        shift = 20 - shift_factor
        decode_frame = flag < 0x07

        for i in range(28):
            if decode_frame:
                b = payload[off + 2 + (i >> 1)]
                nibble = (b >> 4) if (i & 1) else (b & 0x0F)
                if nibble >= 8:
                    nibble -= 16
                sample = nibble << shift
                sample += int((c0 * hist1 + c1 * hist2) * 256.0)
                sample >>= 8
            else:
                sample = 0
            if sample > 32767:
                out.append(32767)
            elif sample < -32768:
                out.append(-32768)
            else:
                out.append(sample)
            hist2 = hist1
            hist1 = sample

    return out
