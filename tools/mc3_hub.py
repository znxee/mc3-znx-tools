"""
mc3_hub.py - the front door of the MC3 toolkit.

Shows every tool, what it does and what it opens, and launches it. Run this
first if you are not sure which tool you need:

    python mc3_hub.py                 GUI hub
    python mc3_hub.py --list          plain text list (no tkinter needed)
    python mc3_hub.py <file>          says which tools accept that file

Each tool stays usable on its own; the hub only points at them.
"""
import importlib.util
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# name, script, one-line summary, what it opens, longer description
TOOLS = [
    dict(
        name="PCK Manager",
        script="mc3_pck_manager.py",
        gui=True,
        summary="The main GUI. Meshes, bones, structures, performance, textures, shaders.",
        opens=[".pck", ".psppck", ".xbck"],
        detail=(
            "Open a vehicle container and work on everything in one window:\n"
            "  - Meshes: every LOD slot with checkboxes; embed an external mesh,\n"
            "    embed all from the folder, remove embedded ones to shrink the\n"
            "    file, edit the slot ID.\n"
            "  - Skel (bones): the bone tree, bone IDs and anchors.\n"
            "  - Structures: the vehicle structs the header points at.\n"
            "  - Performance: engine, gearbox, wheels, VehZone, VehMods -- editable.\n"
            "  - Textures: the car parts atlas, preview with alpha, export/import PNG.\n"
            "  - Shaders: the shadinggroup (index -> shader) of this car.\n"
            "It also has a 'View 3D' button and opens the audio editor."
        ),
    ),
    dict(
        name="Mesh Converter",
        script="mc3_mesh_convert.py",
        gui=False,
        summary="Convert meshes between PS2, PSP, Xbox, Blender .mesh and OBJ.",
        opens=[".pck", ".psppck", ".mesh", ".obj", ".xbck"],
        detail=(
            "Multi-directional converter. Any of these can be input or output:\n"
            "  .pck      PS2 single mesh\n"
            "  .psppck   PSP mesh (MC3 PSP and MC:LA Remix)\n"
            "  .mesh     Angel Studios text mesh -- the ASSETS/vehicle source\n"
            "            format, the same one the io_AngelStudios_mesh Blender\n"
            "            addon reads and writes\n"
            "  .obj      Wavefront OBJ\n\n"
            "Usage:  python mc3_mesh_convert.py input output\n"
            "  python mc3_mesh_convert.py bumper.pck bumper.mesh   # to Blender\n"
            "  python mc3_mesh_convert.py wheel.mesh wheel.pck     # back to PS2\n"
            "Options: --mc3/--mcla (psppck flavor), --id N, --materials a,b,c,\n"
            "--scale F, -v"
        ),
    ),
    dict(
        name="3D Viewer",
        script="mc3_view.py",
        gui=True,
        summary="Look at a mesh. Highlights flipped faces in red.",
        opens=[".pck", ".psppck", ".xbck"],
        detail=(
            "Drag a file onto the .py, or pass it on the command line.\n"
            "Controls: drag = rotate, scroll = zoom, left/right = step through\n"
            "meshes, A = show all, B = flipped faces in RED (the ones you see\n"
            "through in game), G = color by group, W = wireframe, Q/E = roll.\n"
            "Use --html to get the WebGL version instead of the native window.\n"
            "Also reachable from the PCK Manager through 'View 3D'."
        ),
    ),
    dict(
        name="Textures",
        script="mc3_tex.py",
        gui=False,
        summary="Textures: .tex sources, flash images, vehicle atlases. PS2 swizzle.",
        opens=[".tex", ".pck"],
        detail=(
            "python mc3_tex.py list <file>       what is inside\n"
            "python mc3_tex.py extract <file> -o out/\n\n"
            "Handles the three PS2 texture types (8bpp both-swizzled, 8bpp with\n"
            "only the palette swizzled, 4bpp linear), the CLUT swizzle, and the\n"
            "0..128 alpha range. Import/replace is in the PCK Manager, which has\n"
            "the palette quantizer wired up."
        ),
    ),
    dict(
        name="Shading group",
        script="mc3_shadinggroup.py",
        gui=False,
        summary="Print the index -> shader table of a vehicle.",
        opens=[".pck", ".psppck"],
        detail=(
            "python mc3_shadinggroup.py <file.pck>\n\n"
            "The material index stored in a mesh points into this list, and the\n"
            "order changes from car to car -- so the same index means a different\n"
            "shader on another vehicle. Check here before setting --materials in\n"
            "the converter. Also shown in the PCK Manager 'Shaders' tab."
        ),
    ),
    dict(
        name="Embed / remove (CLI)",
        script="mc3_pck_embed.py",
        gui=False,
        summary="Same embed/remove engine as the GUI, for scripting and batches.",
        opens=[".pck", ".psppck"],
        detail=(
            "python mc3_pck_embed.py container.pck --list\n"
            "python mc3_pck_embed.py container.pck part.mesh.pck --name NAME\n"
            "python mc3_pck_embed.py container.pck --auto-dir\n"
            "python mc3_pck_embed.py container.pck --remove-all --dry-run\n\n"
            "Embedding appends at the end and repoints the slot, so nothing else\n"
            "in the file moves. Removing relocates pointers and refuses to run\n"
            "when something outside points into the mesh footprint."
        ),
    ),
    dict(
        name="Traffic Replacer",
        script="mc3_traffic_gui.py",
        gui=True,
        summary="Replace the meshes inside a city *_traffic.pck.",
        opens=[".pck"],
        detail=(
            "A traffic container packs every traffic vehicle of a city. It is not\n"
            "a vehicle container -- no LOD tables, no skeleton, no performance --\n"
            "so it gets its own app instead of being folded into the PCK Manager.\n\n"
            "  - lists every piece: index, piece id, inline name, groups, size;\n"
            "  - EXTRACT a piece to .mesh.pck / .obj / .mesh to edit it;\n"
            "  - REPLACE a piece from .mesh.pck, .obj, .mesh, .psppck or .xbck\n"
            "    (converted on the way in);\n"
            "  - preview a piece in the 3D viewer.\n\n"
            "Nothing already in the file moves: the new body is appended at the\n"
            "end and only the slot pointer is rewritten, so a replacement changes\n"
            "5 bytes of the original region (header size + slot pointer). That is\n"
            "what keeps the strings the container hides in its padding intact."
        ),
    ),
    dict(
        name="Audio Studio",
        script="mc3_audio_studio.py",
        gui=True,
        summary="Vehicle audio: curve editing, .bnk mux/demux and audible preview.",
        opens=[".pck", ".bnk"],
        detail=(
            "Unified audio editor. Three things in one window:\n"
            "  - the .pck curve viewer, now with real EDITING: block fields,\n"
            "    sample/RPM ranges, curve points (drag on the graph or type in\n"
            "    the table) and profile parameters, all written in place with a\n"
            "    .bak backup;\n"
            "  - bnk_tool (MUX/DEMUX of Sony/SCREAM .bnk): finds the bank from\n"
            "    the name the block references (E_350Z -> assets/audio/banks/\n"
            "    E_350Z.bnk + e_350z.td), plays individual samples, extracts and\n"
            "    rebuilds banks;\n"
            "  - audible preview: renders the engine at a fixed RPM or over an\n"
            "    RPM sweep, crossfading the ranges through the volume curve, with\n"
            "    a bit-exact PS-ADPCM decoder.\n\n"
            "Ships as a unit with the bnk_tool package next to it. Also reachable\n"
            "from the PCK Manager through 'Audio Studio...'."
        ),
    ),
    dict(
        name="Performance (module)",
        script="mc3_perf.py",
        gui=False,
        summary="Read/write the physics and performance values of a car PCK.",
        opens=[".pck"],
        detail=(
            "Library used by the PCK Manager 'Performance' tab: VehSim, VehGyro,\n"
            "engine, gearbox, wheels, VehZone and the VehMods upgrade table.\n"
            "Import it from Python, or just use the GUI tab."
        ),
    ),
    dict(
        name="Pointer graph (module)",
        script="mc3_pointers.py",
        gui=False,
        summary="Reverse pointer lookup: who points here, walk up to the root.",
        opens=[".pck", ".psppck", ".xbck"],
        detail=(
            "Library. PointerGraph builds a forward and a reverse pointer index\n"
            "over a container:\n"
            "  sources_of(off)   who points exactly here\n"
            "  rollback(off)     step back until somebody points\n"
            "  trace_up(off)     walk the owner chain up to the root\n"
            "  next_target(off)  where the next pointed-to struct starts, which\n"
            "                    sizes a block without any padding heuristic\n"
            "This is the reverse-search method written as reusable code."
        ),
    ),
    dict(
        name="Rim Importer & Explorer",
        script="Rim Importer & Explorer 2.0.py",
        dirs=[os.environ.get("MC3_RIM_IMPORTER", "")],
        gui=True,
        summary="Rims: browse, preview, import and repaint shaders inside rim.ppf.",
        opens=[".ppf"],
        detail=(
            "By Victor (Mid Engine) and RibeiroG -- not part of this toolkit,\n"
            "launched from wherever it lives (point MC3_RIM_IMPORTER at its folder).\n\n"
            "Reads the pf05 pack straight: header, descriptor table, one entry\n"
            "per rim, geometry packets, shader batches. Browses and names the\n"
            "entries, previews the mesh, exports OBJ, imports a replacement and\n"
            "repaints shader ids in place with a backup.\n\n"
            "It agrees with this toolkit on the pf05 layout (block index * 0x800,\n"
            "size * 0x80), decodes the ADC flag correctly (bit 0 of normal.x,\n"
            "masked out with & 0xFE) and refuses a shader id the entry does not\n"
            "list -- which is the thing that hangs the game.\n\n"
            "Two things it does NOT cover, measured here:\n"
            "  - its packet pattern is nailed to 98/C5 00 02 60, so it sees\n"
            "    nothing in the sibling packs that use the 026C form with\n"
            "    culling bounds: tire.ppf has 844 packets, exhaust 270,\n"
            "    rider 117, shared_tex 85, brake 54, all read as zero;\n"
            "  - its shader table is found by the literal vtable A0 22 7A 00,\n"
            "    which is build residue: 683 hits in rim.ppf, ZERO in\n"
            "    rim.pspppf."
        ),
    ),
    dict(
        name="City Builder",
        script="mc3_city_build.py",
        gui=False,
        summary="Build a city .pck from scratch, or graft one generated piece into a real one.",
        opens=['.pck'],
        detail=(
            "python mc3_city_build.py --donor CITY.pck --out-path new.pck\n"
            "python mc3_city_build.py --donor CITY.pck --xmod M.xmod --out-path new.pck\n"
            "python mc3_city_build.py --base CITY.pck --graft shaders --out-path g.pck\n"
            "python mc3_city_build.py --verify-only new.pck\n"
            "\n"
            "GENERATED: header, MapRoot (0x290 bytes -- mcCity reaches +0x228), hood\n"
            "table and records, components, mcCityModelType, meshes, group and block\n"
            "tables, VIF packets, and the two 256-entry palettes.\n"
            "COPIED from the donor: one shader block (0xB0, sub-object at +0x10) and\n"
            "the cullable classes, with their counts zeroed.\n"
            "\n"
            "--graft is the differential bisection: start from a file that LOADS and\n"
            "swap ONE generated piece, so a boot excludes half the space instead of\n"
            "testing one field. That cleared the shader group and isolated the\n"
            "ComponentData.\n"
            "\n"
            "An object we generate must carry the TRANSLATED vtable, not the file\n"
            "token: the game translates tokens by walking lists a new object never\n"
            "enters. Measured in RAM -- 607 retail meshes became 0x00626D98 while ours\n"
            "kept 0x007A1E18."
        ),
    ),
    dict(
        name="Place simulator",
        script="mc3_place_sim.py",
        gui=False,
        summary="Run the city loader's Place walk on the PC and report what would hang.",
        opens=['.pck'],
        detail=(
            "python mc3_place_sim.py CITY.pck [--detail]\n"
            "python mc3_place_sim.py MINE.pck --coverage RETAIL.pck\n"
            "\n"
            "Replays in Python the same graph the game walks:\n"
            "  mcCity::mcCity(datResource&)              0x259160\n"
            "  rmcShaderGroup::rmcShaderGroup            0x2B3600\n"
            "  rmcShaderFactoryStandard::ResourcePageIn  0x2AF9E8\n"
            "     dispatches on *(obj+4) & 0x7F; anything outside {0,1,2} falls into\n"
            "     loc_2AFA88, which is `nop x5; b self` -- the infinite loop behind\n"
            "     every 'loading forever'.\n"
            "\n"
            "Checks: unhandled type tag, the same object in more than one slot (the\n"
            "walk has no already-placed guard), null slots, out-of-body pointers, the\n"
            "+0x1C index, ComponentData constants, and per-block qwc/nv against the\n"
            "VIF stream.\n"
            "\n"
            "--coverage says which MapRoot fields your file leaves empty AND which\n"
            "ones this simulator does not verify. Knowing the gaps matters more than\n"
            "the list of passes.\n"
            "\n"
            "Always run it on an untouched retail city first."
        ),
    ),
    dict(
        name="MC2 -> MC3 geometry",
        script="mc2_port.py",
        gui=False,
        summary="Validate Midnight Club 2 meshes against MC3's own text mesh reader.",
        opens=['.xmod', '.mod'],
        detail=(
            "python mc2_port.py check    FILE_OR_DIR\n"
            "python mc2_port.py textures DIR --textures MC2/texture_x\n"
            "python mc2_port.py mod      DIR\n"
            "python mc2_port.py fit      MESH --how RETAIL.mod\n"
            "python mc2_port.py retex    MESH --texture NAME\n"
            "python mc2_port.py city     MC2/city/losangeles\n"
            "\n"
            "MC3 still ships a TEXT mesh reader and uses it for 39 of its own models.\n"
            "rmcModel::Create (0x2A8CA0) does strrchr(name, '.'): `.mod` goes to\n"
            "mshMesh::LoadMod, anything else to the binary `mesh` deserialiser.\n"
            "\n"
            "MC2's .xmod is the same `version: 1.10` dialect, so the geometry is not\n"
            "converted -- it is RENAMED. Measured: the 39 retail .mod pass 39/39 and\n"
            "all 9902 MC2 .xmod pass 9902/9902.\n"
            "\n"
            "Textures come by NAME, not by file: the mtl block's name becomes the\n"
            "shader parameter $tex0 and is resolved through two by-name hash tables.\n"
            "A name nothing answers is not fatal -- the mesh draws untextured.\n"
            "\n"
            "The checker applies the grammar read out of the ELF. Validate it on the\n"
            "retail models before trusting it on anything else."
        ),
    ),
    dict(
        name='MC2 races and modes',
        script='mc2_races_to_mc3.py',
        gui=False,
        summary="Install an MC2 city's races into its MC3 slot, plus MC3-only modes built from them.",
        opens=['.rac', '.loc'],
        detail=(
            'python mc2_races_to_mc3.py --mc2-city losangeles\n'
            'python mc2_races_to_mc3.py --mc2-city losangeles --add-types capture_the_flag,bomb_tag\n'
            'python mc2_race_modes.py --city losangeles\n'
            'python mc2_race_shortcut.py --city paris --race NAME --leg A B\n'
            '\n'
            "Reads the user's own MC2 PS2 races and writes MC3 .rac/.rinf, the .loc and\n"
            '.locinf entries and the menu and loading-screen strings (mcstrings01 and\n'
            "mcloadstrings). MC2's bomb_tag is MC3's Detonator. --add-types only APPENDS,\n"
            'so hand-fixed races survive. mc2_race_modes builds Tag and Paint from the\n'
            'Capture the Flag arenas and Frenzy from the longest ordered races.\n'
            'mc2_race_shortcut injects AI shortcuts that follow the road network.'
        ),
    ),
    dict(
        name='MC2 city occluders',
        script='mc2_occluders.py',
        gui=False,
        summary='Generate an MC3 .occlude for an MC2 city, so lights and cars stop showing through walls.',
        opens=['.rsc'],
        detail=(
            'python mc2_occluders.py --city losangeles --out losangeles.occlude --png la.png\n'
            '\n'
            "Boxes from MC2's own collision (BND0) and rendered buildings, flood-filled\n"
            'from the road network; boxes touching a lane are dropped.'
        ),
    ),
    dict(
        name='City trains and pedestrians',
        script='mc3_peds_graph.py',
        gui=False,
        summary="Put an MC2 city's pedestrian sidewalks into its _peds.pck; list or remove compiled trains.",
        opens=['.pck', '.graph', '.aib'],
        detail=(
            'python mc2_ped_graph.py --city losangeles --out losangeles.graph\n'
            'python mc3_peds_graph.py losangeles_peds.pck losangeles.graph\n'
            'python mc3_traffic_trains.py losangeles_traffic.pck --remove\n'
            '\n'
            'Pedestrians walk the graph compiled into <city>_peds.pck, not the text\n'
            '.graph; mc2_ped_graph builds sidewalk strips from the pedestrian rails of\n'
            "the MC2 city's .aib and mc3_peds_graph appends them to the pack. Trains are\n"
            'compiled into <city>_traffic.pck; a copied pack runs its trams on any road\n'
            'of type 3 - mc3_traffic_trains sets their count to 0.'
        ),
    ),
    dict(
        name='Prop particle rules',
        script='mc3_prop_ptx.py',
        gui=False,
        summary="Extract the particle rules compiled into a city's props pack as text .ptx.",
        opens=['.pck'],
        detail=(
            'python mc3_prop_ptx.py detroit_dusk_clear_props.pck --list\n'
            'python mc3_prop_ptx.py detroit_dusk_clear_props.pck --out ASSETS/tune/effects\n'
            '\n'
            'Each rule (mcPropParticleBirthRule, 0x190 bytes) is written field by\n'
            'field; mcPropParticleBirthRule::LoadWithHash reads it back byte-identical.\n'
            "Tiles index the city's <x>_shared_particle atlas (d_ for detroit)."
        ),
    ),
    dict(
        name='Rename an added city',
        script='mc3_rename_city.py',
        gui=False,
        summary='Rename an added MC3 city in paths, text files, mc3boot.ini and string tables.',
        opens=[],
        detail=(
            'python mc3_rename_city.py --old OLD --new NEW --dry-run\n'
            '\n'
            "The city's MC3 name is the key every asset path is built from; no .pck or\n"
            '.ppf stores it. Rebuild the registry module (city_paris_slot7) with NEW.'
        ),
    ),
    dict(
        name="Symbols (alpha -> retail)",
        script="mc3_symbols.py",
        gui=False,
        summary="Carry function names from the 2004 alpha build over to the retail ELF.",
        opens=['.elf', '.i64'],
        detail=(
            "python mc3_symbols.py dump  --elf ALPHA.i64 --out alpha.tsv\n"
            "python mc3_symbols.py dump  --elf RETAIL    --out retail.tsv\n"
            "python mc3_symbols.py match  alpha.tsv retail.tsv\n"
            "python mc3_symbols.py graph  alpha.tsv retail.tsv names.tsv\n"
            "python mc3_symbols.py tokens alpha.tsv retail.tsv names.tsv\n"
            "python mc3_symbols.py apply\n"
            "python mc3_symbols.py search Hood     (query without opening IDA)\n"
            "python mc3_symbols.py annotate EA NAME 'how I confirmed it'\n"
            "\n"
            "Addresses and bytes both change between builds; what survives is what a\n"
            "function TALKS ABOUT, the company it keeps, and where it SITS. Four\n"
            "passes, each reaching what the previous cannot:\n"
            "  match      string literals           1224 pairs\n"
            "  graph      callers and callees       3219 pairs\n"
            "  neighbours source order + size       6304 pairs\n"
            "  tokens     referenced data / vtables  183 pairs\n"
            "10946 names applied, 98%+ precision on a blind hold-out.\n"
            "\n"
            "Results live in tools/symbols/ as layered .tsv files that every fork of\n"
            "the project reads and writes; the manual layer always wins."
        ),
    ),
    dict(
        name="Modloader (run your own C++)",
        script="mc3_inject.py",
        gui=False,
        summary="Inject a compiled C++ payload into the running game (mc3boot repo).",
        opens=['.bin', '.p2s'],
        detail=(
            "python mc3_inject.py all --embed payload/mc3mod.bin\n"
            "python mc3_inject.py state savestate.p2s\n"
            "cd payload && sh build.sh\n"
            "(all three run inside the mc3boot checkout; set MC3BOOT to it)\n"
            "\n"
            "A patch file can only change constants. This runs real code: the\n"
            "patch carries a 61-instruction loader, and the payload is either a\n"
            "loose file the game reads through its own Stream API or carried\n"
            "inside the patch file.\n"
            "\n"
            "Four routes from one loader, all injecting the same bytes:\n"
            "  pnach          PCSX2 + HostFS, payload in a loose file\n"
            "  pnach --embed  PCSX2 from an ISO\n"
            "  cht            real PS2 through OPL, payload in the image (71 codes)\n"
            "  cht   --embed  same with no image change, 716 codes - over most\n"
            "                 OPL limits, so prefer the one above\n"
            "`all` writes all four plus a note saying which is which, so the\n"
            "emulator and hardware can be compared without doubt about what ran.\n"
            "\n"
            "The payload fixes pedestrian animation speed at ANY frame rate\n"
            "(rate = base * dt * 30), which no constant patch can do - verified\n"
            "at 120.47 Hz - and applies the pnach groups itself, with the table\n"
            "generated from the real .pnach files by mc3_pnach.py --to-cpp and\n"
            "the EE instruction cache flushed so hardware behaves like PCSX2.\n"
            "Edit payload/config.h to choose groups. Needs the ps2dev toolchain\n"
            "to build C++; see the mc3boot README, including the MSYS2 traps."
        ),
    ),
    dict(
        name="ELF patcher",
        script="mc3_elfpatch.py",
        gui=False,
        summary="Verified word patches into the ELF, with the PCSX2 CRC preserved.",
        opens=['.elf', '.ELF'],
        detail=(
            "python mc3_elfpatch.py crc   --elf ELF\n"
            "python mc3_elfpatch.py show  VA VA --elf ELF\n"
            "python mc3_elfpatch.py apply --elf IN --out OUT --keep-crc \\\n"
            "       --patch VA:EXPECTED:NEW\n"
            "\n"
            "Every patch declares the value it expects to find and is REFUSED if\n"
            "it does not match, so a stale address or the wrong ELF fails loudly\n"
            "instead of corrupting an instruction.\n"
            "\n"
            "--keep-crc: PCSX2 identifies a game by the ELF's CRC, which is just\n"
            "every 32-bit word XORed together. Because it is a plain XOR, the\n"
            "damage is old^new per word, and XORing that into a slack word past\n"
            "the end of PT_LOAD cancels it - so existing pnach files keep working."
        ),
    ),
    dict(
        name="Fork log",
        script="mc3_forks.py",
        gui=False,
        summary="What the parallel sessions changed, and what it breaks.",
        opens=[],
        detail=(
            "python mc3_forks.py read              the last few entries\n"
            "python mc3_forks.py since 2026-08-18  from a date on\n"
            "python mc3_forks.py add --fork \"...\" --changed \"...\" \\n"
            "                       --affects \"...\" --action \"...\"\n"
            "\n"
            "Work on this game happens in several sessions at once, sharing one\n"
            "filesystem. When one renames a folder, changes a file format or moves\n"
            "a generated artefact, the others find out by breaking - usually much\n"
            "later, and while chasing something unrelated.\n"
            "\n"
            "FORKS.md is the channel for that, in the same spirit as symbols/.\n"
            "Newest first. Every entry answers three things: what changed, who it\n"
            "can break, what they should do.\n"
            "\n"
            "Not a diary: if another session would not change what it does after\n"
            "reading it, it does not belong there.\n"
        ),
    ),
    dict(
        name="Pnach auditor",
        script="mc3_pnach.py",
        gui=False,
        summary="Audit every patch file PCSX2 will load for one game.",
        opens=['.pnach'],
        detail=(
            "python mc3_pnach.py --dir PCSX2/cheats --elf ELF\n"
            "python mc3_pnach.py --dir ... --elf ... --to-cpp patches.h\n"
            "\n"
            "PCSX2 loads EVERY file matching the CRC, and warns about nothing.\n"
            "This reads them all, resolves them the way PCSX2 would, and checks\n"
            "each line against the ELF (labels as mc3boot prints them):\n"
            "  CONFLITO   two files write different values to one address\n"
            "  EXCLUSIVO  alternative groups on one address - tick only one\n"
            "  MORTA      a line a later one overrides\n"
            "  CAVERNA    a contiguous block outside PT_LOAD (injected code)\n"
            "  FORA       a lone address outside PT_LOAD - likely a typo\n"
            "  SEM EFEITO the ELF already holds that value\n"
            "  DESCRICAO  the comment cites a number the word does not encode\n"
            "\n"
            "The last one is why it exists: a comment is the only record of what\n"
            "a patch was FOR, and a copy-pasted line keeps the old text with a new\n"
            "value. It found four such lines in one reflection patch pack."
        ),
    ),
    dict(
        name="Patch self-test",
        script="mc3_selftest.py",
        gui=False,
        summary="Re-implement patched game functions and test them against savestates.",
        opens=['.p2s'],
        detail=(
            "python mc3_selftest.py\n"
            "python mc3_selftest.py --sstates DIR   (re-measure instead of using\n"
            "                                        the recorded numbers)\n"
            "\n"
            "A patch that was reasoned about but never executed is a hypothesis.\n"
            "Three layers, cheapest first:\n"
            "  ENCODING   decode each patched word as MIPS and as an IEEE float\n"
            "             and check it means what the patch claims\n"
            "  BEHAVIOUR  run the replicas at 30 and 60 Hz against measurements;\n"
            "             this is where a wrong MODEL dies\n"
            "  ELF        confirm the built files hold exactly the intended words,\n"
            "             that nothing else moved, and that the CRC matches\n"
            "\n"
            "It caught a real error on its first run - a replica treating the\n"
            "animation loop as negative travel - before the patch ever reached\n"
            "the game."
        ),
    ),
    dict(
        name="Documentation",
        script="mc3_docs.py",
        gui=False,
        summary="Build the PDF documenting every format finding so far.",
        opens=[],
        detail=(
            "python mc3_docs.py            -> output/MC3_Format_Documentation.pdf\n"
            "python mc3_docs.py -o out.pdf\n\n"
            "The container, pointer techniques, vehicle and traffic scene graphs,\n"
            "meshes on all three consoles, skeletons, performance structures and\n"
            "textures -- with the measured counts, the traps that cost time, and\n"
            "an explicit list of what is still unknown.\n\n"
            "The document is generated from the CONTENT list at the top of the\n"
            "script, so a new finding is one edit plus a re-run. Needs reportlab."
        ),
    ),
]


# Everything lives here, but a tool may have been moved out (the Audio Studio
# travels as a unit with its bnk_tool package), so look around before giving up.
EXTRA_DIRS = [
    HERE,
    os.environ.get("MC3BOOT", os.path.join(os.path.dirname(HERE), "mc3boot")),
    os.path.dirname(HERE),
]


def resolve(t):
    # an entry may bring its own directories: not every tool lives in tools/
    # (mc3_inject and mc3_pnach come from the mc3boot repo, the Rim Importer is
    # a separate download)
    for d in list(t.get("dirs", ())) + EXTRA_DIRS:
        p = os.path.join(d, t["script"])
        if os.path.isfile(p):
            return p
    return None


def print_list(target=None):
    print("MC3 toolkit  (%s)" % HERE)
    print("=" * 78)
    for t in TOOLS:
        path = resolve(t)
        mark = " " if path else "!"
        if target:
            ext = os.path.splitext(target)[1].lower()
            if ext == ".pck" and target.lower().endswith(".mesh.pck"):
                ext = ".pck"
            if ext not in t["opens"]:
                continue
        print("%s %-22s %s" % (mark, t["name"], t["summary"]))
        print("%-24s opens: %s%s" % ("", ", ".join(t["opens"]),
                                     "" if path else "   [MISSING FILE]"))
    if target:
        print("\n(filtered for %s)" % os.path.basename(target))
    print("=" * 78)
    print("Details: python mc3_hub.py            (GUI)")


def launch(t, arg=None):
    path = resolve(t)
    if not path:
        raise RuntimeError("%s not found (looked in: %s)"
                           % (t["script"], ", ".join(EXTRA_DIRS)))
    cmd = [sys.executable, path]
    if arg:
        cmd.append(arg)
    # run from the tool's own folder: the Audio Studio imports bnk_tool relative
    # to itself, and others may read sibling files too
    subprocess.Popen(cmd, cwd=os.path.dirname(path))


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
def run_gui(initial=None):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("MC3 Toolkit")
    root.geometry("980x600")

    head = ttk.Frame(root, padding=(10, 8, 10, 4))
    head.pack(fill="x")
    ttk.Label(head, text="MC3 Toolkit", font=("", 15, "bold")).pack(side="left")
    ttk.Label(head, text=HERE, foreground="#888").pack(side="left", padx=12)

    state = {"file": initial}
    filebar = ttk.Frame(root, padding=(10, 0, 10, 6))
    filebar.pack(fill="x")
    ttk.Button(filebar, text="Pick a file...",
               command=lambda: pick()).pack(side="left")
    file_lbl = ttk.Label(filebar, text="(no file -- tools open their own)",
                         foreground="#666")
    file_lbl.pack(side="left", padx=10)

    body = ttk.Frame(root, padding=(10, 0, 10, 6))
    body.pack(fill="both", expand=True)

    cols = ("tool", "summary", "opens")
    tree = ttk.Treeview(body, columns=cols, show="headings", selectmode="browse")
    for c, t, w in (("tool", "tool", 190), ("summary", "what it does", 470),
                    ("opens", "opens", 150)):
        tree.heading(c, text=t)
        tree.column(c, width=w, anchor="w", stretch=(c == "summary"))
    tree.pack(side="left", fill="both", expand=True)
    sb = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    sb.pack(side="left", fill="y")

    detail = tk.Text(root, height=13, wrap="word")
    detail.pack(fill="both", padx=10, pady=(0, 4))
    detail.configure(state="disabled")

    foot = ttk.Frame(root, padding=(10, 0, 10, 10))
    foot.pack(fill="x")
    open_btn = ttk.Button(foot, text="Open tool", command=lambda: do_open())
    open_btn.pack(side="left")
    ttk.Button(foot, text="Open tool with the picked file",
               command=lambda: do_open(with_file=True)).pack(side="left", padx=6)
    status = ttk.Label(foot, text="", foreground="#0a6")
    status.pack(side="left", padx=12)

    rows = {}

    def fill():
        tree.delete(*tree.get_children())
        rows.clear()
        f = state["file"]
        ext = ""
        if f:
            ext = os.path.splitext(f)[1].lower()
        for t in TOOLS:
            ok = resolve(t) is not None
            match = (not ext) or (ext in t["opens"])
            iid = tree.insert("", "end", values=(
                t["name"] + ("" if ok else "  [missing]"),
                t["summary"], ", ".join(t["opens"])),
                tags=("match" if match else "dim",))
            rows[iid] = t
        tree.tag_configure("dim", foreground="#aaa")
        tree.tag_configure("match", foreground="")

    def show_detail(_e=None):
        sel = tree.selection()
        detail.configure(state="normal")
        detail.delete("1.0", "end")
        if sel and sel[0] in rows:
            t = rows[sel[0]]
            detail.insert("end", "%s   (%s)\n\n" % (t["name"], t["script"]))
            detail.insert("end", t["detail"])
        detail.configure(state="disabled")

    def pick():
        p = filedialog.askopenfilename(
            title="Pick a file to work on",
            filetypes=(("MC3 files", "*.pck *.psppck *.mesh *.tex *.obj"),
                       ("All", "*.*")))
        if p:
            state["file"] = p
            file_lbl.configure(text=os.path.basename(p), foreground="")
            fill()

    def do_open(with_file=False):
        sel = tree.selection()
        if not sel or sel[0] not in rows:
            messagebox.showinfo("Select", "Pick a tool from the list.")
            return
        t = rows[sel[0]]
        arg = state["file"] if with_file else None
        if with_file and not arg:
            messagebox.showinfo("No file", "Pick a file first.")
            return
        if with_file and not t["gui"] and t["script"] != "mc3_view.py":
            messagebox.showinfo(
                "Command-line tool",
                "%s is a command-line tool -- it needs arguments the hub does "
                "not know (an output name, options).\n\nSee the details box and "
                "run it in a terminal." % t["name"])
            return
        try:
            launch(t, arg)
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            return
        status.configure(text="opened %s" % t["script"])

    tree.bind("<<TreeviewSelect>>", show_detail)
    tree.bind("<Double-1>", lambda e: do_open())
    fill()
    if initial:
        file_lbl.configure(text=os.path.basename(initial), foreground="")
    kids = tree.get_children()
    if kids:
        tree.selection_set(kids[0])
        show_detail()
    root.mainloop()


def main():
    args = [a for a in sys.argv[1:]]
    if "--list" in args or "-l" in args:
        rest = [a for a in args if not a.startswith("-")]
        print_list(rest[0] if rest else None)
        return
    files = [a for a in args if not a.startswith("-")]
    initial = files[0] if files and os.path.isfile(files[0]) else None
    try:
        run_gui(initial)
    except Exception as exc:
        print("could not open the GUI (%s), falling back to the list:\n" % exc)
        print_list(initial)


if __name__ == "__main__":
    main()
