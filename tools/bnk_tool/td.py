"""Parser/writer for .td sidecar files (TD_FILE format) that map a sound name to its
"sound" index inside a .bnk (table1). These appear to be what the game itself uses to
look sounds up by name at runtime - the .bnk's own table4 (when present) is a separate,
optional debug-name table vgmstream can read; our sample files don't even have one.

Format (plain text, tab-indented):
    TD_FILE 3.0
    NUMSOUNDS 8
        HIGH_ENGINE
            0
            1
        HIGH_TAIL
            1
            1
        ...

Each sound has: name, an index (0-based, matches the .bnk's table1/"sound" index),
and a second integer whose exact meaning isn't documented (observed 0 for "_HORN"-type
one-shots and 1 otherwise in sample files - looks like a category/loop-ish flag). We
preserve it verbatim when known and default to 1 for brand new entries.
"""

DEFAULT_FLAG = 1


class TdError(Exception):
    pass


def parse_td(path: str) -> dict:
    with open(path, "r", encoding="latin-1") as f:
        lines = [ln.strip() for ln in f if ln.strip()]

    if not lines or not lines[0].startswith("TD_FILE"):
        raise TdError("does not look like a .td file (expected 'TD_FILE' on the first line)")
    version = lines[0].split(None, 1)[1] if " " in lines[0] else ""

    if len(lines) < 2 or not lines[1].startswith("NUMSOUNDS"):
        raise TdError("expected 'NUMSOUNDS N' on the second line")
    num_sounds = int(lines[1].split()[1])

    sounds = []
    pos = 2
    for _ in range(num_sounds):
        if pos + 2 >= len(lines) + 1 or pos + 1 >= len(lines):
            raise TdError("truncated .td file (fewer entries than NUMSOUNDS declares)")
        name = lines[pos]
        index = int(lines[pos + 1])
        flag = int(lines[pos + 2])
        sounds.append({"name": name, "index": index, "flag": flag})
        pos += 3

    return {"version": version, "sounds": sounds}


def build_td(sounds: list, version: str = "3.0") -> str:
    """sounds: list of {"name": str, "index": int, "flag": int}, in the order to write."""
    lines = [f"TD_FILE {version}", f"NUMSOUNDS {len(sounds)}"]
    for snd in sounds:
        lines.append(f"\t{snd['name']}")
        lines.append(f"\t\t{snd['index']}")
        lines.append(f"\t\t{snd.get('flag', DEFAULT_FLAG)}")
    return "\n".join(lines) + "\n"


def name_to_index(td: dict) -> dict:
    return {s["name"]: s["index"] for s in td["sounds"]}


def index_to_name(td: dict) -> dict:
    return {s["index"]: s["name"] for s in td["sounds"]}


def generate_td_from_manifest(manifest: dict, renames: dict = None) -> str:
    """Rebuilds .td content from a demux() manifest (post-MUX/BUILD), so the game can
    still look sounds up by name even if some were renamed. renames: optional
    {sound_index: new_name} to relabel a sound without touching the .bnk itself."""
    renames = renames or {}
    by_index = {}
    for s in manifest["streams"]:
        idx = s.get("sound_index")
        if idx is None or s.get("is_zlsd"):
            continue
        if idx in by_index:
            continue
        name = renames.get(idx, renames.get(str(idx)))
        if name is None:
            name = s.get("name") or f"SOUND_{idx}"
        flag = s.get("td_flag")
        if flag is None:
            flag = DEFAULT_FLAG
        by_index[idx] = {"name": name, "index": idx, "flag": flag}

    sounds = [by_index[i] for i in sorted(by_index)]
    version = (manifest.get("td") or {}).get("version") or "3.0"
    return build_td(sounds, version)
