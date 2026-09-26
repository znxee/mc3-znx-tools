"""Pass a UTF-8 patch file to Codex's apply-patch entry point."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: apply_patch_file.py CODEX_EXE PATCH_FILE")
    executable, patch_path = sys.argv[1], Path(sys.argv[2])
    result = subprocess.run(
        [executable, "--codex-run-as-apply-patch",
         patch_path.read_text(encoding="utf-8")],
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
