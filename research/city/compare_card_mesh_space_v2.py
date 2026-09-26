import importlib.util
import os
import sys

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools')
SRC = os.path.join(os.environ.get('MC2_PC_ASSETS', 'mc2_pc/assets_p'), 'city/losangeles/models')
ORIG = TOOLS + r"\output\la729_pck"
LOCAL = TOOLS + r"\output\city_v7_vif_relocation_20260906\la729_local_components_20260906"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


conv = load("mc2_to_mc3", TOOLS + r"\experimental\mc2_to_mc3.py")
vif = load("mc3_vifpos_cards", TOOLS + r"\mc3_vifpos.py")
build = load("mc3_city_build_cards", TOOLS + r"\mc3_city_build.py")


def box(points):
    return tuple(min(p[a] for p in points) for a in range(3)), tuple(max(p[a] for p in points) for a in range(3))


def pck_box(path):
    points = []
    for group in build.read_pck_groups(path):
        for payload, _qw, _nv in group:
            points.extend(vif.decode_positions(payload))
    return box(points)


for stem in (
    "l_inst_downtowncardblk05_01x_0_emissive",
    "l_inst_industrialcardblk06_01x_0_emissive",
):
    positions, _uv, _adj, _strip = conv.read_xmod(os.path.join(SRC, stem + ".xmod"))
    print(stem)
    print(" source      ", box(positions))
    print(" pck-original", pck_box(os.path.join(ORIG, stem + ".mesh.pck")))
    print(" pck-local   ", pck_box(os.path.join(LOCAL, stem + ".mesh.pck")))
