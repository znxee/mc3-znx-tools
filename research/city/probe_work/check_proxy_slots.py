from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_la_texture_proxy import CityPck
for value in sys.argv[1:]:
    path = Path(value)
    try:
        slots = CityPck(path).basic_textures_by_slot()
        print(path, "slots", len(slots), "range", min(slots), max(slots))
    except Exception as exc:
        print(path, type(exc).__name__, exc)
