"""DEMUX: split a .bnk into its raw audio streams (no decoding) + a manifest.json
that records everything needed to rebuild ("MUX") the file later."""
import json
import os
import re

from .model import CODEC_EXT
from .parser import parse_bnk
from .genh import build_genh, is_genh_supported, GenhError
from .td import parse_td

MANIFEST_NAME = "manifest.json"


def _safe_name(s: str) -> str:
    s = s.strip()
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s)
    return s


def _field_ref(fr):
    if fr is None:
        return None
    return {"offset": fr.offset, "size": fr.size}


def demux(bnk_path: str, out_dir: str, use_genh: bool = True, td_path: str = None) -> str:
    """Extracts every stream + writes manifest.json into out_dir. Returns the manifest path.

    use_genh: wrap extracted streams in a .genh header (playable/editable in any
    GENH-aware tool) when the codec has a GENH equivalent (PSX, PCM16, MPEG).
    Codecs GENH can't represent (HEVAG, raw ATRAC9) fall back to a raw dump; codecs
    that are already self-contained (RIFF_ATRAC9, XVAG_ATRAC9) are left as-is.

    td_path: optional matching .td sidecar file. When given, its names take priority
    over whatever came from the .bnk's own table4 (often absent/unreliable), matched
    by "sound index" (table1 index) - this is what the game itself uses to look up
    sounds by name, so it's the more authoritative source.
    """
    h = parse_bnk(bnk_path)
    os.makedirs(out_dir, exist_ok=True)

    with open(bnk_path, "rb") as f:
        data = f.read()

    td_index_to_name = {}
    td_index_to_flag = {}
    td_info = None
    if td_path:
        td = parse_td(td_path)
        td_info = td
        for snd in td["sounds"]:
            td_index_to_name[snd["index"]] = snd["name"]
            td_index_to_flag[snd["index"]] = snd["flag"]

    manifest = {
        "source_file": os.path.basename(bnk_path),
        "source_size": h.file_size,
        "big_endian": h.big_endian,
        "version": h.version,
        "sections": h.sections,
        "sblk_offset": h.sblk_offset,
        "data_offset": h.data_offset,
        "data_size": h.data_size,
        "zlsd_offset": h.zlsd_offset,
        "zlsd_size": h.zlsd_size,
        "sblk_version": h.sblk_version,
        "support_tier": h.support_tier,
        "bank_name": h.bank_name,
        "td": td_info,
        "streams": [],
    }

    used_names = set()

    for s in h.streams:
        entry_data_offset = s.start_offset - s.extradata_size
        entry_total_size = s.extradata_size + s.stream_size + s.postdata_size

        name_source = "table4" if s.name else "none"
        td_flag = None
        if s.sound_index is not None and s.sound_index in td_index_to_name:
            s.name = td_index_to_name[s.sound_index]
            td_flag = td_index_to_flag[s.sound_index]
            name_source = "td"

        base = _safe_name(s.name) if s.name else ""
        fname = f"{s.index:03d}_{base}" if base else f"{s.index:03d}"
        fname = fname.strip("_") or f"{s.index:03d}"

        payload = data[s.start_offset:s.start_offset + s.stream_size]

        container = "raw"
        ext = CODEC_EXT.get(s.codec, ".bin")
        file_bytes = payload
        if use_genh and is_genh_supported(s.codec):
            try:
                genh_header = build_genh(
                    codec=s.codec, channels=s.channels, sample_rate=s.sample_rate,
                    interleave=s.interleave, num_samples=s.num_samples,
                    loop_flag=s.loop_flag, loop_start=s.loop_start, loop_end=s.loop_end,
                    big_endian_pcm=h.big_endian)
                file_bytes = genh_header + payload
                ext = ".genh"
                container = "genh"
            except GenhError as e:
                s.warnings.append(f"could not generate GENH ({e}); extracted as raw")
        elif s.codec in ("RIFF_ATRAC9", "XVAG_ATRAC9"):
            container = "self-contained"

        out_name = fname + ext
        n = 2
        while out_name in used_names:
            out_name = f"{fname}_{n}{ext}"
            n += 1
        used_names.add(out_name)

        with open(os.path.join(out_dir, out_name), "wb") as of:
            of.write(file_bytes)

        extradata_hex = data[entry_data_offset:entry_data_offset + s.extradata_size].hex()

        # stream_flags field location (loop-enable bit), when applicable
        stream_flags_field = None
        if s.sndh_offset:
            if h.sblk_version in (0x01, 0x02, 0x03, 0x04, 0x05, 0x08, 0x09):
                stream_flags_field = {"offset": s.sndh_offset + 0x0e, "size": 2}
            elif h.sblk_version in (0x0c, 0x0d, 0x0e, 0x0f, 0x10):
                stream_flags_field = {"offset": s.sndh_offset + 0x12, "size": 2}

        manifest["streams"].append({
            "index": s.index,
            "name": s.name,
            "name_source": name_source,
            "sound_index": s.sound_index,
            "td_flag": td_flag,
            "codec": s.codec,
            "channels": s.channels,
            "sample_rate": s.sample_rate,
            "loop_flag": s.loop_flag,
            "loop_start": s.loop_start,
            "loop_end": s.loop_end,
            "interleave": s.interleave,
            "encoder_delay": s.encoder_delay,
            "sndh_offset": s.sndh_offset,
            "entry_data_offset": entry_data_offset,
            "entry_total_size": entry_total_size,
            "extradata_size": s.extradata_size,
            "postdata_size": s.postdata_size,
            "extradata_hex": extradata_hex,
            "stream_offset_field": _field_ref(s.stream_offset_field),
            "stream_size_field": _field_ref(s.stream_size_field),
            "stream_size_field_original_value": s.size_field_raw_original,
            "center_note_field": _field_ref(s.center_note_field),
            "center_fine_field": _field_ref(s.center_fine_field),
            "sample_rate_float_field": _field_ref(s.sample_rate_float_field),
            "stream_flags_field": stream_flags_field,
            "embedded_header": s.embedded_header,
            "stream_name_size": s.stream_name_size,
            "file": out_name,
            "container": container,
            "warnings": list(s.warnings),
            "is_zlsd": s.is_zlsd,
        })

    manifest_path = os.path.join(out_dir, MANIFEST_NAME)
    with open(manifest_path, "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, indent=2, ensure_ascii=False)

    return manifest_path
