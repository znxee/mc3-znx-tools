"""
mc3_docs.py - builds the Midnight Club 3 format documentation as a PDF.

Everything documented here was measured against the clean assets, and the counts
in the text are the actual validation numbers. To add a finding, edit CONTENT
below and run the script again:

    python mc3_docs.py                  -> output/MC3_Format_Documentation.pdf
    python mc3_docs.py -o other.pdf
"""
from __future__ import annotations

import datetime
import os
import struct
import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageBreak,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)

VERSION = "5.1"
TITLE = "Midnight Club 3 - File Format Documentation"
SUBTITLE = "PS2 .pck / PSP .psppck / Xbox .xbck, and the executable"

INK = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#666666")
ACCENT = colors.HexColor("#1f5fa8")
WARN = colors.HexColor("#a05000")
RULE = colors.HexColor("#cccccc")
CODEBG = colors.HexColor("#f4f4f4")


# ---------------------------------------------------------------------------
# content
#
# Each section is (heading, [blocks]). A block is one of:
#   ("p",  text)                 paragraph (supports <b> <i> <font>)
#   ("code", text)               fixed-width block
#   ("table", [rows], [widths])  first row is the header
#   ("note", text)               highlighted caveat
#   ("h3", text)                 sub-heading
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------- examples
# The hex in this document is not transcribed, it is read out of the clean
# assets while the PDF is built. If a file moves the example says so instead of
# quietly going stale.
ASSETS = os.path.join(os.environ.get('MC3_ASSETS', 'ASSETS'), 'resources')


def _bytes(rel, off, n):
    try:
        with open(os.path.join(ASSETS, rel), "rb") as f:
            f.seek(off)
            b = f.read(n)
        return b if len(b) == n else None
    except OSError:
        return None


def _hex(b):
    return " ".join("%02X" % x for x in b)


def fields(rel, off, spec, title=""):
    """A struct laid out field by field, straight from the file.

    spec is [(size, label)]; sizes of 1, 2 and 4 also print the little-endian
    value, which is usually the whole point of the example.
    """
    n = sum(sz for sz, _ in spec)
    b = _bytes(rel, off, n)
    head = "%s  @ 0x%06X%s" % (os.path.basename(rel), off, "   " + title if title else "")
    if b is None:
        return ("code", head + "\n  (asset not found - example not generated)")
    out, at = [head], 0
    for sz, label in spec:
        chunk = b[at:at + sz]
        val = ""
        if label.startswith("f:"):           # float
            label = label[2:]
            val = "= %-10.3f" % struct.unpack("<f", chunk)[0]
        elif label.startswith("h:"):         # nao vale mostrar valor
            label = label[2:]
        elif sz in (1, 2, 4):
            v = int.from_bytes(chunk, "little")
            val = "= %-10s" % (("0x%08X" % v) if (sz == 4 and v > 0xFFFF) else str(v))
        out.append("  +%02X  %-23s %-13s %s" % (at, _hex(chunk), val, label))
        at += sz
    return ("code", "\n".join(out))


def hexrows(rel, rows, title=""):
    """One labelled row of raw bytes per entry: [(offset, count, label)]."""
    out = [title] if title else []
    w = max(len(l) for _o, _n, l in rows)
    for off, n, label in rows:
        b = _bytes(rel, off, n)
        out.append("  %-*s  0x%06X   %s" % (w, label, off, _hex(b) if b else "(not found)"))
    return ("code", "\n".join(out))


CONTENT = [
("Scope and method", [
 ("p", "This documents the resource containers of <b>Midnight Club 3</b> across its three "
       "builds: PS2 <font face=Courier>.pck</font>, PSP <font face=Courier>.psppck</font> "
       "(both MC3 PSP and MC:LA Remix) and Xbox <font face=Courier>.xbck</font>. "
       "It covers the container itself, the vehicle and traffic scene graphs, meshes, "
       "skeletons, performance structures and textures."),
 ("p", "Every claim here was measured against the clean assets, not inferred. Where a number "
       "appears (\"783/783\", \"4135 of 4135\") it is the count from an actual sweep over the "
       "game files. Where something is still unknown it says so explicitly - see "
       "<i>Open questions</i> at the end."),
 ("note", "Sources used as ground truth, with credit where it is due. The Xbox "
          "<b>\"leftover\"</b> text files are the uncompiled sources and name almost every "
          "field. On the community side: <b>AlgumCorrupto, Thiekus, ZNX and offlbruno</b> for "
          "the flash texture logic and the palette work; <b>Victor (Mid Engine) and RibeiroG</b> "
          "for the rim.ppf work; <b>offlbruno</b> for the .ppf and city ImHex patterns and "
          "<b>AlgumCorrupto</b> for the city C header and the RAM dump; "
          "<b>TahmidAlam-git's</b> mesh_to_obj.py "
          "(github.com/TahmidAlam-git/MC3_extraction_tools) for the Xbox mesh layout; and "
          "<b>zKiwi's</b> io_AngelStudios_mesh Blender addon for the .mesh text format."),
]),

("The container", [
 ("p", "All three platforms share one container. It is not a parsed archive: it is a "
       "<b>load-in-place dump of C++ objects</b>. The game reads the block whole and only "
       "fixes up pointers using the base in the header."),
 ("h3", "Header and pointer convention"),
 ("code",
  "0x00  u32  virtual address of file offset 0x80\n"
  "0x04  u32  resource type (22 = single mesh, 60 = traffic, ...)\n"
  "0x08  u32  1\n"
  "0x0C  u32  body size  ( = filesize - 0x80 )\n"
  "0x10..0x7F  zero\n"
  "0x80        body starts here\n"
  "\n"
  "file_offset = pointer - virtual_base + 0x80\n"
  "virtual_base = u32[0x00] - 0x80"),
 ("p", "Typical virtual bases: PS2 <font face=Courier>0x06800000</font>, MC3 PSP "
       "<font face=Courier>0x0A0D1F00</font>, MC:LA <font face=Courier>0x0109DF80</font>, "
       "Xbox <font face=Courier>0x82B87900</font>."),
 ("note", "<b>Xbox is little-endian.</b> The decisive test: u32 at 0x0C read little-endian "
          "equals filesize - 0x80 exactly (353744 and 1708912 on the two files checked); "
          "big-endian gives garbage. That is expected - the original Xbox is x86; the "
          "big-endian console is the Xbox 360."),
 ("h3", "The first u32 of a struct is a vtable pointer, not a magic number"),
 ("p", "Values like <font face=Courier>98 0F 7A 00</font> or "
       "<font face=Courier>20 94 7A 00</font> sitting at the start of every serialized "
       "struct are <b>vtable pointers</b>, left behind because the object was saved with "
       "memcpy. Three pieces of evidence:"),
 ("p", "1. Zeroing the value in the file changes nothing - the game writes it back, and the "
       "value differs on every run. 2. In a PCSX2 save state the same struct holds "
       "<font face=Courier>0x006278C8</font>, which is inside the retail ELF's .data "
       "(0x614400-0x636824) and is byte-identical between ELF and RAM: a real vtable, in the "
       "Itanium C++ ABI layout (offset-to-top, typeinfo, then the virtual functions). "
       "3. The values stored in the files (0x007Axxxx) appear nowhere in the retail ELF, "
       "because they are residue from the build that serialized them."),
 ("note", "Practical consequence: these values are <b>safe to ignore or zero</b> when "
          "generating structures, and <b>useless as an anchor</b> - they change per build. "
          "The real type comes from the slot POSITION. This bit twice: the shader table scan "
          "and the texture descriptor scan both had to stop keying on them."),
]),

("Finding pointers", [
 ("h3", "Forward: the VA-range technique"),
 ("p", "A pointer is any 4-byte-aligned u32 whose value falls inside "
       "[virtual_address, virtual_address + body_size]. Almost nothing else lands in that "
       "window, which is likely why every file gets its own virtual address."),
 ("note", "<b>Vertex data forms false pointers constantly.</b> Concrete case: in "
          "vp_350z_04_g.pck, offset 0x80794 holds <font face=Courier>E8 16 87 06</font> = "
          "0x068716E8 - inside the range and even 4-aligned, but it is a vertex coordinate. "
          "Excluding the packet data regions removes <b>1322 false positives (24%)</b> in "
          "that one file. Without the filter, relocation would rewrite geometry."),
 ("note", "A writer hit that failure for real while embedding the MC:LA C6 shell. Two "
          "cross-vertex u32 values, <font face=Courier>0x01720C00</font> at mesh body "
          "+0x898C and <font face=Courier>0x01720E41</font> at +0x11A14, happened to land "
          "inside the standalone mesh VA range. Blanket rebasing changed z=3072 into "
          "z=29520 in trim and produced the matching roof spike. The shell had exactly "
          "26 real structural pointers but the range scan found 28. Typed traversal fixes "
          "26 and preserves every geometry byte."),
 ("h3", "Reverse: who points here"),
 ("p", "Because everything is reachable from the root through pointers, from any address you "
       "can walk up the owner chain. Search the address as a u32; if nobody points exactly at "
       "it, step back 4 bytes (the real target usually starts after padding) and repeat. When "
       "more than one place points at the same target, those are the several owners - several "
       "materials referencing one texture, for instance."),
 ("p", "Implemented as <font face=Courier>PointerGraph</font>: forward and reverse indices, "
       "<font face=Courier>sources_of</font>, <font face=Courier>rollback</font>, "
       "<font face=Courier>trace_up</font> (BFS to the root) and "
       "<font face=Courier>next_target</font>, which sizes a block without any padding "
       "heuristic. The root pointer on PS2 lives at <b>VA 0x06800128</b>."),
 ("note", "<b>0x06800128 is a VA, not a file offset.</b> In the file it sits at 0x1A8 "
          "(0x128 + 0x80). Treating it as an offset shifts root detection by 0x80 and makes "
          "it succeed by accident on header garbage."),
]),

("Vehicle container", [
 ("p", "Per LOD the container holds three parallel tables, reached through pointers in the "
       "table header - <b>not</b> at a fixed offset:"),
 ("code",
  "LOD table header\n"
  "  +0x00 u16 unk | +0x02 u16 count   (MC:LA puts the count at +0x06)\n"
  "  +0x04 tag / vtable ptr\n"
  "  +0x08 -> mesh table   (u32 pointer per slot; ZERO = load external file)\n"
  "  +0x0C -> name table   (pointer to \"<...>.mesh\")\n"
  "  +0x10 -> ID table     (u32 per slot, NOT u16)\n"
  "  +0x14 u16 skel items | +0x18 tag EE020100 | +0x1C -> skel"),
 ("p", "When a mesh pointer is zero the game loads "
       "<font face=Courier>&lt;name&gt;.mesh.pck</font> from the same folder, and the slot ID "
       "must be non-zero for that to happen. Embedding means writing a pointer into that slot."),
 ("note", "In HLOD the three tables happen to sit at +0x20, but that is coincidence - the "
          "LLOD tables are far away. Assuming +0x20 made the LLOD read strings as pointers "
          "(the bogus \"VA 0x6F6F7276\" is the ASCII \"vroo\")."),
 ("h3", "Embedding without breaking anything"),
 ("p", "The container uses 0xCD as padding but sometimes stores strings and pieces of other "
       "structures in that space. Inserting bytes in the middle would shift everything and "
       "break whatever is hidden there. So a new mesh body is <b>appended at the end</b>, its "
       "internal pointers rebased to where it landed (16-byte aligned - MC3 never points at "
       "+4/+8/+12), and only the chosen slot is repointed. Nothing else moves."),
 ("p", "Rebasing the appended body must be <b>typed</b>. For PS2, visit "
       "mesh+0x0C (materials), mesh+0x10 (main table), each group entry+0x00 "
       "(descriptor array), and each descriptor+0x00 (VIF region). Never range-scan "
       "VIF bytes: the C6 changes 26 pointer slots and zero geometry bytes."),
 ("p", "Measured: embedding one part into the 350z changes <b>6 bytes</b> (body size + the "
       "slot). Filling all 106 slots of vp_supra_98 changed only two regions, both header or "
       "table - zero bytes outside. Embed followed by remove returns the file "
       "<b>byte-for-byte identical</b> to the original."),
 ("h3", "Skeleton"),
 ("p", "Same structure everywhere, but the node grew on PSP because the anchor is padded to "
       "16 bytes:"),
 ("table", [
    ["field", "PS2 (node 0x44)", "PSP (node 0x60)"],
    ["anchor 1 (3 floats)", "+0x00", "+0x00"],
    ["name pointer", "+0x0C", "+0x10"],
    ["next", "+0x14", "+0x18"],
    ["child", "+0x18", "+0x1C"],
    ["parent", "+0x1C", "+0x20"],
    ["anchor 2", "+0x20", "+0x30"],
    ["ID (u16)", "+0x38", "+0x50"],
  ], [58*mm, 45*mm, 45*mm]),
 ("p", "The bone ID is the same value the mesh ID table stores - that is how a part attaches "
       "to a bone. The header (u16 count, u16 unk, tag, list pointer) is reached from file "
       "0x1AC on PS2 with tag <font face=Courier>EE020100</font>, from 0x1C4 on MC3 PSP with "
       "the same tag, and on MC:LA with tag <font face=Courier>00010000</font>. Because "
       "neither the offset nor the tag is universal, detection scores candidates by how many "
       "nodes carry a readable name, with a bonus when the first is <font face=Courier>vroot"
       "</font>."),
 ("note", "That scoring was necessary: with only a 2-node check, MC:LA matched a block of "
          "shader parameters (\"tintA\", \"WinFresnelMin\") before the real skeleton. "
          "Result now: PS2 250 bones, MC3 PSP 250, MC:LA 120, all named."),
 ("h3", "One RAM tree for all proven vehicle readers"),
 ("p", "<font face=Courier>mc3_vehicle_ram.hexpat</font> now follows the same structures "
       "already used by the Manager and command-line tools instead of stopping at their "
       "pointers: LOD/name/ID tables, skeleton, mesh groups and blocks, shader group, "
       "resident texture data, performance and vehicle audio. The disk-only 0x80-byte PCK "
       "header and PSP/Xbox layouts are deliberately excluded because the pattern starts at "
       "the loaded PS2 body in <font face=Courier>eeMemory.bin</font>."),
 ("note", "The 350Z's embedded vehicle meshes all have <font face=Courier>slot_count = 0" 
          "</font>, so their <font face=Courier>mesh+0x14</font> pointer remains visible but "
          "is not expanded. The populated per-block attribute chain is a city-mesh feature "
          "in the files measured so far; copying that interpretation here would create a "
          "convincing but false subtree."),
 ("h3", "The same tree directly on a raw vp_*.pck"),
 ("p", "<font face=Courier>mc3_vehicle_pck.hexpat</font> applies the mapped graph without a "
       "savestate. It validates the 0x80-byte header as main vehicle type "
       "<font face=Courier>0x0038E030</font>, version 1, places the package root at file "
       "+0x80, and translates every serialized pointer with "
       "<font face=Courier>file = VA - base + 0x80</font>. The accepted VA interval comes "
       "from the header body size, so null, CD padding, runtime-only fields and external "
       "references remain raw words instead of opening false children."),
 ("p", "Raw polymorphic words are deliberately labelled <b>serialized class token</b>, "
       "not vtable. They are build residue overwritten by Load(). Shaders use the stable "
       "discriminator used by the retail loader, <font face=Courier>shader_flags &amp; "
       "0x7F</font>: zero selects the Basic layout, while the other known types select the "
       "common complex pass and proven derived tails. This removes the last dependency on "
       "the vtable addresses of one RAM capture."),
 ("note", "Validated by the ImHex Pattern Language CLI on all <b>117</b> available main "
          "vehicle packages: base cars, vp_d_* Remix vehicles, motorcycles, police and DLC "
          "slots, with zero failures. A type-22 *.mesh.pck shows only its valid header and is "
          "not mistaken for a main vehicle package."),
 ("h3", "Vehicle phBoundGeometry in RAM"),
 ("p", "The requested RAM range <font face=Courier>0x017B1AF8..0x017B1D97</font> is not a "
       "standalone object. It starts at +0x48 of a <font face=Courier>phBoundGeometry</font> "
       "whose 0x80-byte header begins at <font face=Courier>0x017B1AB0</font>, then contains "
       "14 records of <font face=Courier>phPolygon</font> (0x20 bytes each) and 14 Vec3 "
       "vertices. The pointer to it is <font face=Courier>drwShaderModel+0x78</font>."),
 ("table", [
    ["field", "meaning"],
    ["+0x08, +0x14", "AABB minimum and maximum"],
    ["+0x20, +0x2C", "placement offset and centre-of-gravity offset"],
    ["+0x38, +0x3C", "sphere radius and radius from origin"],
    ["+0x48", "u16 runtime flags - zero in the PCK, mutated after loading"],
    ["+0x4C, +0x50", "u32 vertex and polygon counts"],
    ["+0x68, +0x6C", "vertex and polygon pointers"],
    ["+0x70, +0x74", "material-index table and default material override"],
 ], [90, 300]),
 ("p", "A vehicle package carries <b>two distinct phBoundGeometry objects</b>: the model "
       "bound at <font face=Courier>drwShaderModel+0x78</font> and a second bound at package "
       "root+0x0C. A census of 352 PS2 vehicle packages found 704 valid bounds, zero invalid "
       "indices or broken reciprocal neighbours, and no package where the two pointers were "
       "the same. Their serialized geometry is byte-identical in all 352 packages, but the "
       "loaded 350Z package bound has different vertices, planes, AABB, centre of gravity and "
       "material values. It is therefore a mutable live copy, not an alias."),
 ("note", "A writer must preserve both objects and their separate material tables. Editing "
          "only the model bound does not update the live physical copy. After changing "
          "vertices, rebuild extents, radii, unit normals, areas and reciprocal neighbours. "
          "The serialized vtable value is module-specific residue and must never be copied "
          "as a universal class tag."),
]),

("Performance structures", [
 ("p", "Pointers in the PCK header, identical on all three platforms:"),
 ("code",
  "0x98 VehInput   0xA0 VehDamage   0xA4 VehModel   0xA8 VehAudio\n"
  "0xB4 VehSimBase 0xB8 VehSimMods  0xBC VehGyroBase 0xC0 VehGyroMods\n"
  "0xC4 VehZone    0xC8 VehMods\n"
  "\n"
  "Base = stock car, Mods = fully upgraded."),
 ("p", "Each struct starts with the vtable pointer and usually a pointer to the config name, "
       "so the useful fields begin at +0x0C (most), +0x04 (Aero, AIInfo) or +0x00 (Engine, "
       "Transmission, Drivetrain, Axle, Fluid, Nitro, SSTurbo, Hydraulics). From VehSim "
       "onward come 16 subsystem pointers: WheelFront, WheelBack, Engine, Transmission, "
       "Drivetrain, Freetrain, AxleFront, AxleBack, Aero, Fluid, -, Nitro, SSTurbo, -, "
       "Hydraulics, AIInfo."),
 ("h3", "The filler problem"),
 ("p", "The gaps between fields hold different things per console, and reading the PS2 layout "
       "straight on another build walks off by one field per group:"),
 ("table", [
    ["console", "what fills the gaps", "consequence"],
    ["PS2", "0xCDCDCDCD, only to align a colour vec4 to 16 bytes", "baseline"],
    ["PSP", "0xCDCDCDCD on EVERY member (a vec3 takes 16 bytes, so does Mass)", "fields shift, filler shows as -4.31602e+08"],
    ["Xbox", "a vtable pointer at +0x00 of Fluid", "reads as a denormal, ~8.7e-39"],
  ], [26*mm, 66*mm, 56*mm]),
 ("p", "Both kinds read back as an absurd float - 0xCDCDCDCD is -4.3e+08, a small code "
       "address is a denormal - while a real physics value is neither. So the reader skips "
       "filler before each <i>float</i> field, and the schema's fixed skips became adaptive: "
       "they only jump what is actually filler."),
 ("note", "Two traps in that fix. The denormal test must apply to float fields only - "
          "Transmission starts with ManualNumGears = 8, which is a denormal as a float but a "
          "legitimate integer. And sub-word skips must stay unconditional: Nitro has a 2-byte "
          "skip after a u16, and making it adaptive broke it."),
 ("p", "Result: <b>36 sections / 512 fields</b> on all platforms. The same 350z reads "
       "<b>512/512 identical</b> between PS2 and Xbox, and <b>510/512</b> between PS2 and "
       "MC3 PSP - the two that differ are Aero/AngVel2DampZ (0.825 vs 0.45), a real platform "
       "tuning change, since every field around it matches."),
 ("h3", "VehZone and VehMods"),
 ("p", "VehZone is <b>18 floats</b> at +0x0C, proven complete: comparing the 42 leftover "
       "<font face=Courier>.vehzone</font> sources against the binaries gives <b>508/508 "
       "fields identical</b>. The bikes_Lean* fields are zero on every car and populated only "
       "on bikes, which is what confirms the alignment. The four camera fields present in the "
       "text source (FOV, DistScale, InTime, OutTime) are <b>not compiled into the PS2 PCK</b> "
       "- FOV -20 and DistScale 1.32 appear nowhere in the file."),
 ("p", "VehMods holds six upgrade-category pointers. Their target begins with the real "
       "category ID: 0 Engine, 1 Trans, 2 Brake, 3 Suspension, 4 Tire, 5 Hydraulics. "
       "<b>The pointer slots are not category order</b> - the 350Z stores Suspension before "
       "Brake. Each record is "
       "<font face=Courier>u16 category | u16 num_levels | u16 progression[4] | u16 fields[]"
       "</font>. The category lists are adjacent with no separator, so a parser must stop at "
       "the known sub-mod count or it reads into the next category."),
 ("h3", "Vehicle audio in the same PCK"),
 ("p", "Header slot 0xA8 reaches the vehicle-audio root. Its named levels split into raw "
       "auxiliary objects, full TurboBlower/Engine/Exhaust blocks, and five Common profiles. "
       "A full block stores a 0x20-byte bank name, eight allocated sample/RPM ranges, "
       "near/far distance, warble and boost scalars, EngineRevSound and five curve pointers. "
       "Each curve reserves eight rows of nine floats: three control points plus the "
       "quadratic coefficients A/B/C."),
 ("p", "On <font face=Courier>vp_350z_04.pck</font>, the shared parser and the RAM pattern "
       "reach 13 full audio blocks, five Common profiles and 17 auxiliary pointers. Set "
       "<font face=Courier>EXPAND_AUDIO_CURVES = true</font> only when those rows are needed. "
       "Raw auxiliary internals remain unlabelled: the Audio Studio currently finds their "
       "strings and candidate floats heuristically, so no unstable offset was promoted to a "
       "named binary field."),
]),

("Meshes", [
 ("p", "All formats share one geometry model: triangle strips where the triangle closing at "
       "index i is (i-2,i-1,i) for even i and (i-1,i-2,i) for odd i."),
 ("table", [
    ["", "PS2 .pck", "PSP .psppck", "Xbox .xbck", ".mesh text"],
    ["positions", "s16 fixed point", "s16 fixed point", "float", "float"],
    ["normals", "signed bytes", "signed bytes", "NORMPACKED3", "float"],
    ["hides a triangle with", "ADC flag (bit 0 of normal.x)", "degenerate vertex", "n/a", "n/a"],
    ["geometry container", "VIF/GS packets", "14-byte records", "D3D buffer", "adjunct list"],
  ], [34*mm, 32*mm, 30*mm, 28*mm, 26*mm]),
 ("note", "<b>The ADC flag is the single most repeated trap in this project.</b> It hides "
          "bridge triangles on PS2, and no other format has it. Writing a PS2 strip verbatim "
          "into .mesh, .obj or .xbck draws those hidden triangles: a 128-triangle hood came "
          "out with 146 - three separate times, in three different writers."),
 ("h3", "A vehicle VIF packet, now visible in the RAM pattern"),
 ("code", "BlockEntry: u32 packet | u16 qwc | u16 vertex_count\n"
          "vehicle packet +00 tag 9800026C/C501026C | +04 scale\n"
          "               +08 centerY | +0C centerZ | +10 radius\n"
          "               +14 vertex count | +24 position UNPACK\n"
          "then: V3-16 position | pad4 | V2-16 UV | V4-8 colour | V3-8 normal/ADC\n"
          "traffic packet +00 tag 98000260 | +04 scale | +08 count | +0C position UNPACK"),
 ("p", "The HexPat calculates every stream address from the block's real vertex count and "
       "the VIF four-byte padding rule. It therefore displays positions, UVs, colours and "
       "normals separately without scanning the payload for byte patterns. In "
       "<font face=Courier>normal_adc.x_with_adc</font>, bit 0 suppresses the triangle ending "
       "at that vertex; the remaining signed bits are the X normal component."),
 ("h3", "Xbox .mesh.xbck"),
 ("p", "Mapped from the smallest file in the game (368 bytes, one section, four vertices). "
       "The header carries real pointers:"),
 ("code",
  "0x80 +0x00 vtable ptr\n"
  "     +0x06 PIECE ID   |  +0x07 VERTEX STRIDE (28)\n"
  "     +0x08 u16 face_sections | +0x0A u16 ff_sections\n"
  "     +0x0C -> material table (u16 per section)\n"
  "     +0x10 -> sets           +0x14 zero\n"
  "     +0x18 -> tail           +0x1C -> index list\n"
  "then: material table (pad 16) | u32 set_count + 24-byte sets |\n"
  "      12-byte tail | vertices | face header (12B/section) | u16 indices\n"
  "\n"
  "vertex (28 bytes):\n"
  "  +0x00  3 floats position\n"
  "  +0x0C  NORMPACKED3 normal   (11/11/10 signed bits in one u32)\n"
  "  +0x10  4 bytes, probably a tangent - NOT decoded\n"
  "  +0x14  2 floats UV"),
 ("p", "The format stores <b>no vertex count</b>; the stream ends where the face header "
       "starts, recognised by u32 == 1 followed by a zero u32. The tail holds a pointer to the "
       "vertex block <b>with the top bit cleared</b> (0x829E9A60 is written 0x029E9A60), and "
       "each 24-byte set points at its (start,count) pair inside the face header."),
 ("p", "Two confirmations: decoding +0x0C as NORMPACKED3 agrees with the geometric normal on "
       "<b>60 of 60</b> sampled hood vertices; and the byte at +0x06 is the <b>same piece id</b> "
       "the PS2 stores as a u16 at 0x86 - <b>4135 of 4135</b> meshes present in both builds "
       "match, and no piece id in the assets exceeds 255. The Xbox u16 at 0x86 is simply "
       "(stride &lt;&lt; 8) | piece_id."),
 ("p", "Validation: 107/107 Xbox meshes of the 350z read with triangles; the hood gives "
       "<b>exactly 128 triangles, the same as the PS2 version</b>, and renders identically. "
       "Writing is implemented for single meshes and checked with TahmidAlam-git's parser as "
       "an independent verifier - <b>60/60</b> PS2 meshes converted to .xbck read back with "
       "the exact triangle count."),
]),

("Traffic containers", [
 ("p", "A city <font face=Courier>*_traffic.pck</font> (type 60) packs every traffic vehicle "
       "of that city. It has no LOD tables and no performance structs, but it does carry a "
       "skeleton per vehicle. Each piece is the same structure as a standalone .mesh.pck, and "
       "the pieces are reached from tables of consecutive pointers."),
 ("h3", "A piece has no name of its own"),
 ("p", "It is named by where it sits. The container keeps a <b>vehicle directory</b> at file "
       "offset 0xB0 - object list, count, name table of 104-byte entries - and from each entry "
       "<font face=Courier>+0x2A4</font> reaches the root, <font face=Courier>+0x10</font> the "
       "mesh group, whose array of slot pointers holds the pieces. That is where "
       "<font face=Courier>va_deliverytruck_a</font> comes from, and the slot it hands out is "
       "the same one the structural scan finds on <b>103 of 103</b> pieces it covers."),
 ("p", "The part name then comes from the vehicle's own skeleton at "
       "<font face=Courier>root+0x68</font> (count, tag 0x00010039, node array): <b>the piece "
       "id is a bone id</b>. Id 0 is the body at <font face=Courier>vroot</font>, 4..7 are the "
       "wheels, and the odd small piece is the shadow - and the sizes agree, the piece landing "
       "on \"shadow\" being the 256-byte one while each wheel is 512. Every vehicle in the four "
       "cities resolves; the only pieces left unnamed are the <b>12 belonging to the city "
       "train</b>, which is not in the vehicle list at all."),
 ("note", "<b>The printable bytes at piece+0x18 are NOT a name</b>, though they were read as "
          "one for a while. A piece uses +0x00..+0x14 and +0x20, leaving +0x18 free, and the "
          "exporter packs <b>skeleton node names</b> into that gap - each one is pointed at "
          "from a node's name field at +0x0C, with a 3-float anchor and a bone id at +0x38. "
          "Three things gave it away: the atlanta delivery truck's 3-group body came out "
          "called \"wheels\" and the detroit one \"hlight0\"; the same bus reads \"whl3\" in "
          "atlanta but \"hlight1\" in detroit; and the bone id of the node owning the string "
          "never matches the piece id. Adjacency, not association."),
 ("table", [
    ["file", "mesh tag", "pieces", "shaders"],
    ["atlanta_traffic.pck", "0x007A1E18", "30", "6"],
    ["detroit_traffic.pck", "0x007A1E18", "32", "6"],
    ["sd_traffic.pck", "0x007A1E18", "29", "7"],
    ["tokyo_traffic.pck", "0x00BC8628", "24", "7"],
  ], [58*mm, 40*mm, 25*mm, 25*mm]),
 ("h3", "Replacing a piece safely"),
 ("p", "Editing in place is dangerous for the same reason as in vehicles, and here there is a "
       "concrete example: in atlanta the slot pointer table sits at 0x24A50 and the string "
       "\"shadow\" is squeezed between the last pointer and the mesh at 0x24A70. So the same "
       "append-and-repoint method is used, carrying over the piece id and the inline name. "
       "Measured across all four cities: a replacement changes <b>5 bytes</b> of the original "
       "region - the body size and the slot pointer - and every piece still parses."),
 ("note", "<b>A shader index at or above the container's shader count hangs the game.</b> A "
          "traffic file has 6 or 7 shaders while a vehicle mesh routinely refers to index 14 "
          "(the 350z hood does), so imported meshes get their index forced. Index <b>2 is "
          "ambient_body in all four cities</b>; index 1 is ambient_shadow on atlanta/detroit "
          "but ambient_wheel on sd/tokyo."),
 ("h3", "Traffic packets have no culling bounds"),
 ("p", "Measured over every block of the four containers and 40 vehicle meshes:"),
 ("table", [
    ["", "vehicle", "traffic"],
    ["packet tag", "9800026C (315/315)", "98000260 (161/161)"],
    ["layout", "tag | scale | 3 culling floats | count", "tag | scale | count"],
    ["vertex count sits at", "+0x14", "+0x08"],
    ["odd-parity variant", "C501026C, used", "never used"],
  ], [40*mm, 55*mm, 53*mm]),
 ("p", "The recurring <font face=Courier>2A 00 00 00</font> in both is not a marker - it is "
       "the vertex count (42), just at different offsets because of the 12 bytes of culling "
       "data. Emitting traffic-style packets is a writer variant; no vtable simulation is "
       "involved, since the packet header is VIF/GS data, not a C++ object."),
]),

("City containers", [
 ("p", "A city is a <b>kit of parts placed by transforms</b>, not hand-authored geometry: "
       "atlanta holds <b>231 models</b> and <b>3608 instances</b> of them. The container is the "
       "same type 0x43 on PS2, PSP and MC:LA, and the class counts line up across platforms "
       "for the same city - instances 3608 vs 3610, meshes 1353 vs 1354, one more class 1151 "
       "vs 1151 - so only the vtable values differ, as always."),
 ("h3", "Hoods: the top of the hierarchy"),
 ("code",
  "MapRoot, at file 0x80\n"
  "  +08 subresource_count   +0C -> subresources\n"
  "  +14 HOOD COUNT          +18 -> HOODS\n"
  "  +1C -> global aggregate (the per-hood counts, summed)\n"
  "\n"
  "Hood -- 0x14 bytes\n"
  "  +00 -> name        +04 num_unique    +08 -> unique[]\n"
  "  +0C num_instance   +10 -> instance[]"),
 ("p", "It resolves on all four cities, names included: atlanta has <b>7</b> hoods (bh, dt, "
       "fwy, lf, mt, ug, we) totalling 651 unique and 3608 instance components; detroit 6 "
       "(717/5156); sd 6 (628/5008); tokyo 6 spelled out - asakusa, ginza, imperialpalace, "
       "shibuya, shinjuku, wharf - totalling 650/7688. This is exactly the model MC2 writes in "
       "text: <font face=Courier>numhoods</font> in the .lvl, then per-hood "
       "<font face=Courier>num_unique_components</font> and "
       "<font face=Courier>num_instance_components</font>."),
 ("p", "A <b>unique component</b> is 0x1C bytes and carries no transform - it is placed once, "
       "with its position baked into the geometry. An <b>instance component</b> is the 0x60 "
       "record below. Both put their vtable at +0x10 and share the same head: next, prev, "
       "flags, resource pointer."),
 ("h3", "The instance, and where its vtable hides"),
 ("code",
  "instance -- 0x60 bytes, and the VTABLE IS AT +0x10, not at +0x00\n"
  "  +00  -> next            (linked list)\n"
  "  +04  -> prev\n"
  "  +08  flags\n"
  "  +0C  -> model (0x7A0090)\n"
  "  +10  vtable 0x7A00B8\n"
  "  +14  4 bytes, cell / LOD\n"
  "  +18  float radius (copied from the model)\n"
  "  +1C  u32 running index\n"
  "  +20  float[9] 3x3 matrix, row-major, WITH SCALE BAKED IN\n"
  "  +44  float[3] position\n"
  "  +50  float[3] world-space bound centre"),
 ("p", "The three matrix rows are mutually orthogonal on <b>3608 of 3608</b>. They are not "
       "orthoNORMAL though: 1952 instances carry a uniform scale and <b>1656 a non-uniform "
       "one</b> - stretching a road piece along its length is exactly what makes a modular kit "
       "work. Testing for orthonormality instead reports 1431 bogus failures."),
 ("h3", "The model, which is an MC2 .pdef in binary"),
 ("code",
  "model 0x7A0090 -- 231 of them, one per a_inst_* name\n"
  "  +04  float[3] sphere centre      +10  float radius\n"
  "  +14  -> mesh LOD0                +20  -> mesh LOD1\n"
  "  +3C  u32 LOD count               +48  name, inline"),
 ("p", "Midnight Club 2 on PC ships the same data as <b>plain text</b>, and it matches field "
       "for field: <font face=Courier>name</font> / <font face=Courier>lods: 2 : 0 2</font> / "
       "<font face=Courier>sphere: x y z radius</font>. Its "
       "<font face=Courier>.lvl</font> gives world extents and a grid, its "
       "<font face=Courier>.hood</font> files give per-component AABBs and instance counts, and "
       "its <font face=Courier>.occlude</font> gives occluder quads as literal vertex lists."),
 ("note", "<b>MC:LA's cities are ported from MC2.</b> Of the 475 object names in MC:LA's LA "
          "container, <b>461 also exist in MC2</b> - 97%; Tokyo shares 280 of 368. So for those "
          "two cities the text sources double as documentation of what the binary means."),
 ("h3", "City geometry: planar, not interleaved"),
 ("p", "This is why a vehicle decoder fails on a city block. A vehicle packs one record per "
       "vertex; a city splits the attributes into separate arrays:"),
 ("code",
  "+0x00  tag ?? ?? 02 60     <- the first TWO bytes vary (VIF code)\n"
  "+0x04  float scale         (always a power of two, 1/64 .. 1/2048)\n"
  "+0x08  u32 vertex count\n"
  "+0x0C  sub-chunk: (00 01 | 27 02), count_lo, 0x69\n"
  "+0x10  POSITIONS  count x 6 bytes (s16 x3)\n"
  "       4-byte sub-chunk ending in 0x65\n"
  "       UVs        count x 4 bytes (s16 x2, over 4096)"),
 ("p", "There is <b>no normal and no ADC flag</b> here - the strip simply ends with the block. "
       "And <font face=Courier>0001</font> versus <font face=Courier>2702</font> is not two "
       "formats, it is the strip <b>parity</b>, the same role "
       "<font face=Courier>ee00</font>/<font face=Courier>1b02</font> play in a vehicle."),
 ("p", "Measured on atlanta: <b>9794 blocks, 328083 vertices, 308495 triangles</b>. Rendering "
       "six models gives recognisable low-rise buildings, 120 to 269 triangles each. Path from "
       "a model to its blocks: model+0x14 -> mesh, mesh+0x10 -> table of (pointer, count), each "
       "pointer -> a list of (pointer, u16, u16), and that last pointer is the block."),
 ("note", "The layout came from the user's own ImHex bookmarks. Converting them needs the "
          "project's baseAddress: <b>file offset = VA - 0x67FFF80</b>."),
 ("h3", "What is still closed"),
 ("p", "The PSP side of the same geometry is still undecoded, though atlanta now serves as a "
       "free reference pair - the same city exists on both consoles. City textures are open on "
       "PSP too: MC:LA ships no city texture pack at all, while the PS2 side expects a 55 MB "
       "<font face=Courier>.ppf</font>, which is at least readable now."),
 ("note", "The loose <font face=Courier>.occlude</font>, <font face=Courier>.graph</font> and "
          "<font face=Courier>.ait</font> next to a city are <b>authoring leftovers</b>, not "
          "runtime data - they are plain text, and the occlude file even carries editor "
          "outliner paths. The exception is <font face=Courier>.aib</font>, which is compiled "
          "(magic <font face=Courier>TSV1</font>) and loaded loose. MC3 PS2 and MC:LA PSP use "
          "the <b>same .aib version</b> (header 0C 41 18), so the AI path data is already "
          "compatible between them."),
]),
("Textures", [
 ("h3", "The .tex source format"),
 ("p", "A 14-byte header of seven u16 - width, height, format, mipmaps, and three more - then "
       "the palette (16 or 256 RGBA entries, one shared by all levels), then the mip levels "
       "back to back. The size formula closes <b>exactly on all 1718 clean .tex files</b>."),
 ("table", [
    ["format code", "meaning"],
    ["1", "8bpp / 256 colours, no alpha"],
    ["14", "8bpp / 256 colours, with alpha"],
    ["15", "4bpp / 16 colours, no alpha"],
    ["16", "4bpp / 16 colours, with alpha"],
  ], [30*mm, 108*mm]),
 ("p", "The bpp half is proven by the size formula. The alpha half is an <b>exporter choice, "
       "not derived from content</b>: format 1 never appears with alpha (163/163 opaque) and a "
       "proxy inherits from its parent, but 14 and 16 are also used on opaque textures. The "
       ".tex is <b>not swizzled</b> and uses 0..255 alpha; swizzling and the 0..128 alpha "
       "range only appear once compiled."),
 ("h3", "PS2 swizzle"),
 ("p", "Three texture types inside a compiled .pck: type 1 is 8bpp with indices and palette "
       "swizzled, type 5 is 8bpp with only the palette swizzled, type 6 is 4bpp linear. A "
       "16-colour palette never goes through the CLUT swizzle. The CLUT swizzle swaps the two "
       "middle groups of 8 inside every block of 32 colours; the texture swizzle is the "
       "standard PSMT8 pattern."),
 ("h3", "Vehicle textures"),
 ("p", "Vehicle .pck files do carry textures, but not as the flash structs - the texture is a "
       "raw block, and the right mental model is Console Texture Explorer's: graphics offset, "
       "w, h, bpp, mipmap count, swizzling, with palette = offset + texture size + mip size. "
       "In practice cars have <b>mipmap count 0</b>, so the palette sits immediately after the "
       "pixels."),
 ("p", "Above the pixels sits a descriptor at <b>pixels - 0x90</b>, whose +0x08 points at an "
       "owner node that points back at the descriptor (validated 261/261). Walking node -> "
       "info -> descriptor -> pixels also gives the texture names: "
       "<font face=Courier>&lt;car&gt;_trim</font> (the badge/light atlas) and "
       "<font face=Courier>window</font>."),
 ("p", "The loaded-RAM form is now reachable from each shader texture slot in "
       "<font face=Courier>mc3_vehicle_ram.hexpat</font>: four MipDesc/TEX0 entries, four "
       "MipData pointers, TEX1, GS TBP/CBP, clamp and mip count. With "
       "<font face=Courier>EXPAND_TEXTURE_PIXELS = true</font>, a resident MipData opens the "
       "same 0x90-byte allocation header used by <font face=Courier>mc3_tex.py</font>, the "
       "indexed pixels and the 16- or 256-entry CLUT after the final mip. Pixel expansion is "
       "off by default because it dominates the ImHex pattern count."),
 ("h3", "The +0x0C field is the byte size, plus 0x100"),
 ("p", "That is all it is. It closes on every size in the game: 256x256 gives "
       "65536+1024+256 = 66816, 128x128 gives 17664, 128x64 gives 9472, 64x64 gives 5376, "
       "32x32 gives 2304, 16x16 gives 1536 - and the 4bpp 8x8 placeholder gives 32+64+256 = "
       "352. It still carries only a size, so 128x64 and 64x128 cannot be told apart here; "
       "the shape comes from the pixels."),
 ("note", "This replaced an earlier reading of the same field as "
          "<b>(w*h &gt;&gt; 8) + 5</b>. That was an <b>algebraic coincidence</b>: for an 8bpp "
          "texture the size is w*h + 1024, so (size + 0x100) &gt;&gt; 8 does come out as "
          "w*h/256 + 5. It looked like a clean formula and matched six sizes - but it broke on "
          "the 4bpp placeholder, which the size reading handles without a special case. The "
          "PSP pack stores the same field with <b>no 0x100 bias</b>."),
 ("h3", "pf05 texture packs (.ppf on PS2, .pspppf on PSP)"),
 ("code",
  "+00  magic \"pf05\"\n"
  "+04  u32 count\n"
  "+08  u32 page size\n"
  "+0C  count x { u16 block_index ; u16 size }\n"
  "       offset = block_index * 0x800\n"
  "       bytes  = size * 0x80"),
 ("p", "The second u16 is a size, not flags. The check that settled it: the PSP ships a "
       "<b>plain-text CSV</b> next to its pack (<font face=Courier>.pspppmap</font>, "
       "<font face=Courier># pageno,offset,name,type,size</font>), and all 10 pages of "
       "hudmap hold exactly what the CSV declares - the one short page reads 0x0030, and "
       "0x1800 bytes is the only value its 0x1440 of content fits. Across PS2 and PSP, "
       "<b>835 of 835</b> atlanta pages and 782 of 782 decal pages land inside the file."),
 ("p", "A pack page carries the <b>same descriptor as a .pck</b>, so the vehicle reader works "
       "on it unchanged: atlanta's city pack holds <b>3145 textures</b>, detroit 3332, sd 3031 "
       "and tokyo 4097, around 90% of them sized. That makes the PS2 city textures readable "
       "today. The starting point for the format was <b>offlbruno's</b> ImHex pattern; "
       "<b>Victor (Mid Engine) and RibeiroG</b> had the same layout, size unit included, in "
       "their Rim Importer & Explorer, which reads rim.ppf directly."),
 ("h3", "Traffic containers use the same chain"),
 ("p", "A <font face=Courier>*_traffic.pck</font> holds its textures exactly as a vehicle "
       "does - same descriptor, same anchor, same node -&gt; info -&gt; descriptor -&gt; pixels "
       "walk. Only the vtable values differ, as they always do between builds: the info sits "
       "behind <font face=Courier>0x007A2320</font> and the node behind "
       "<font face=Courier>0x007A20E0</font>. That gives <b>45 textures across the four "
       "cities, all 45 sized and 36 named</b> - <font face=Courier>va_whl_sedan_01.tex</font>, "
       "<font face=Courier>va_civic_share.tex</font>, <font face=Courier>vp_shadow_ambient.tex"
       "</font>. The nine left over simply have no name node in the container."),
 ("p", "Traffic is where the two non-square sizes show up, <b>128x64</b> and 64x64. The aspect "
       "is not a guess: at 128x64 a wheel texture decodes as a circle, and the coherence of "
       "the decode beats 64x128 on 8 of the 8 textures that use the code."),
 ("note", "Two traps in the name lookup, both found here. A resource name can contain a "
          "<b>dot</b> - traffic names its textures <font face=Courier>*.tex</font> - and a "
          "filter of <font face=Courier>[A-Za-z0-9_]</font> left every one of them anonymous. "
          "But loosening it to <font face=Courier>str.isalnum()</font> is worse: that method "
          "is <b>Unicode-aware</b>, so a latin1-decoded 0xEB reads as a letter and pure "
          "garbage passes as a name. The check has to be an explicit ASCII set - and it has "
          "to admit a leading underscore, or <font face=Courier>__envmap__</font> drops out."),
 ("p", "Some textures still come back anonymous, because <b>nothing references their info "
       "through a node</b> - sd_traffic has <font face=Courier>va_whl_truck_drk.tex</font> "
       "sitting right there in the file with no node linking it. For those there is a "
       "fallback: the last <font face=Courier>*.tex</font> string in the 0x90 bytes before "
       "the descriptor. It runs only when the node walk found nothing, so it can never "
       "override a real name, and it takes the traffic containers from 33 named to <b>41 of "
       "45</b>."),
 ("note", "That fallback is adjacency, which this format usually punishes - the plain "
          "\"nearest preceding string\" agrees with the node-resolved name 15 times against "
          "<b>69 disagreements</b> (it picks up bone names like "
          "<font face=Courier>glow_side1</font>). Two restrictions rescue it, and both were "
          "measured: requiring the .tex suffix, and bounding the window. Swept over every "
          "file, 0x90 to 0xC0 all score <b>30 agreements and zero disagreements</b>; the "
          "first failure appears at 0xE0, where the __envmap__ placeholder starts stealing "
          "the name of the bus."),
 ("p", "Sizes come from the descriptor field at +0x0C: 00 05 01 00 = 256x256, 00 45 00 00 = "
       "128x128 (the <font face=Courier>_o</font> opponent files, half resolution), "
       "00 09 00 00 = 32x32, 00 06 00 00 = 16x16. The code 60 01 00 00 marks the 8x8 flat "
       "placeholders - 32 bytes of 4bpp pixels plus a 16-colour palette of one repeated colour, "
       "either transparent or opaque white. Across 94 cars x base/_o/_g that resolves "
       "<b>783/783 textures</b>."),
]),

("The city", [
 ("p", "The city is the one container whose format was read out of the <b>executable</b> "
       "rather than measured. Everything below was traced in IDA, headless, from the string "
       "<font face=Courier>$/resources/city/%s_midnight_%s</font> outward - see "
       "<i>Reading the ELF</i> at the end of this chapter. Where a count appears it is still "
       "a sweep over the real files, used to confirm what the code said."),

 ("h3", "The 0x80 header is checked, and failure is silent"),
 ("p", "The loader reads the header, validates two fields, allocates and reads the body. "
       "It is a handful of instructions and it decides whether a container loads at all:"),
 ("code", "read(f, hdr, 128);\n"
          "if (hdr[2] == 1) {                 // +0x08 = version, must be 1\n"
          "    *out_base = hdr[0];            // +0x00 = original base address\n"
          "    ok = (hdr[1] == wanted_type);  // +0x04 = TYPE ID, checked\n"
          "    *out_size = hdr[3];            // +0x0C = body size\n"
          "    if (ok) { p = alloc(hdr[3], 128); read(f, p, hdr[3]); return p; }\n"
          "}\n"
          "return 0;                          // no crash - it just does not load"),
 fields("city/atlanta_midnight_clear.pck", 0, [
    (4, "base address the file was built for"),
    (4, "type id - 67 is city geometry"),
    (4, "version, and 1 is the only accepted value"),
    (4, "body size, always filesize minus 128"),
 ], "the header the loader validates"),
 ("note", "This is the cheapest way to lose an afternoon. A wrong type id or version does "
          "not fault, does not log and does not crash: the loader returns null and the "
          "content is simply absent. The body is allocated <b>128-byte aligned</b>, not 16."),
 ("p", "The type id names the content class. Swept over every container in the clean assets, "
       "with version 1 and <font face=Courier>size == filesize - 128</font> holding in all "
       "of them:"),
 ("table", [
    ["type", "content", "count"],
    ["67", "city geometry (atlanta_dawn_clear.pck)", "36"],
    ["4", "city _fog variant", "36"],
    ["9", "*_bnd.pck (bounds)", "4"],
    ["33", "*_peds.pck", "4"],
    ["44", "hudmap.pck", "1"],
    ["60", "*_traffic.pck", "4"],
    ["64", "prop", "702"],
    ["22", "vehicle *.mesh.pck", "many"],
    ["37", "vehicle decal.pck", "2"],
    ["0x38E030", "main vehicle vp_*.pck", "117"],
 ], [60, 282, 48]),
 ("p", "The <font face=Courier>vp_*.pck</font> vehicle packages carry 0x38E030 in that field. "
       "Unlike the small resource ids, it is a build-specific class descriptor value and "
       "must be matched exactly by this retail loader. The "
       "<font face=Courier>.ppf</font> texture packs do not use this header at all."),

 ("h3", "Loading and relocation"),
 ("p", "Load-in-place, with hand-written relocation per class. The caller builds a small "
       "context on the stack and every relocator adds <font face=Courier>ctx[1]</font> to "
       "each pointer slot it knows about:"),
 ("code", "v7 = load_pack(path, 67, &amp;orig_base, &amp;size);\n"
          "ctx[0] = v7;                 // where it landed\n"
          "ctx[1] = v7 - orig_base;     // THE DELTA every relocator adds\n"
          "ctx[2] = size;\n"
          "g_ctx  = ctx;                // global stack, saved and restored\n"
          "relocate_root(v7, ctx);"),
 ("note", "The relocators are blind: they add the delta to whatever sits in a pointer slot, "
          "including the 0xCDCDCDCD padding the compiler left behind. A stale 0xCDCDCDCD in a "
          "slot the code will visit becomes a wild pointer - that was the very first city "
          "crash here, visible in the RAM dump as 0xC7CC24CD."),

 ("h3", "The scene graph"),
 ("p", "Element strides come from the relocation loops, which is what settles the two "
       "arrays a Hood holds - 28 bytes is <font face=Courier>sizeof(UniqueComponent)</font> "
       "and 96 is <font face=Courier>sizeof(InstanceComponent)</font>:"),
 ("code", "0x80 header\n"
          "  MapRoot                       vtable 0x625228 written at load\n"
          "    +0x08 count / +0x0C ptr     array of 16-byte elements\n"
          "    +0x10 ptr                   one 16-byte element\n"
          "    +0x14 count / +0x18 ptr     Hood[20 bytes]\n"
          "    +0x1D4, +0x1D8 .. +0x228    21 further resource pointers\n"
          "      Hood\n"
          "        +0x00 name\n"
          "        +0x04 count / +0x08 ptr   UniqueComponent  [28 bytes]\n"
          "        +0x0C count / +0x10 ptr   InstanceComponent [96 bytes]\n"
          "          ComponentData\n"
          "            +0x08 name   +0x28 vtable   +0x3C buffers[2][5]\n"
          "              buffers[0][0] -&gt; mesh"),
 ("p", "Only <font face=Courier>buffers[0][0]</font> is ever populated; the other nine slots "
       "are zero in every city model. The name pointer at +0x08 points back into the object "
       "itself, at +0x70. <font face=Courier>UniqueComponent+0x14</font> is not a pointer but "
       "four bytes of <b>tile AABB</b> (x0,x1,z0,z1), precomputed for culling, and +0x18 is a "
       "float radius."),

 ("h3", "The mesh"),
 ("p", "The tag <font face=Courier>0x007A1E18</font> at mesh+0x00 is <b>dead residue</b> from "
       "the build that produced the file - the loader overwrites it with the real vtable, "
       "0x626D98. It is still the most convenient way to find meshes in a file, but nothing "
       "reads it."),
 ("table", [
    ["field", "meaning"],
    ["+0x00", "overwritten with 0x626D98 at load"],
    ["+0x04", "flags - the loader forces bit 9 and preserves bit 8"],
    ["+0x08", "u16 group count"],
    ["+0x0A", "u16 slot count of the +0x14 chain"],
    ["+0x0C", "material table, one u16 shader index per group"],
    ["+0x10", "group table, group count x 8"],
    ["+0x14", "per-block chain (see below)"],
 ], [58, 332]),
 fields("city/atlanta_midnight_clear.pck", 0x18F4F0, [
    (4, "tag - overwritten with the real vtable at load"),
    (4, "flags"),
    (2, "group count"),
    (2, "slot count of the +0x14 chain"),
    (4, "material table"),
    (4, "group table"),
    (4, "per-block chain"),
 ], "the mesh of a_inst_low_ghto_06x"),
 ("p", "A group table entry is <font face=Courier>[ptr u32][count u16][pad u16]</font>, and "
       "<b>the count the game walks the block list with comes from here</b>, not from the "
       "16-byte array header in front of the list - both must agree. Each block list entry is "
       "<font face=Courier>[ptr u32][qwords u16][vertices u16]</font>. Retail keeps each group "
       "as one contiguous region: header, entries, then the blocks butted together."),
 fields("city/atlanta_midnight_clear.pck", 0x18F830, [
    (4, "block pointer"),
    (2, "size in quadwords"),
    (2, "vertex count"),
    (4, "block pointer"),
    (2, "size in quadwords"),
    (2, "vertex count"),
 ], "first two entries of group 2, which holds three blocks of 48, 46 and 4"),
 ("p", "Measured ceilings in atlanta: <b>48 vertices per block</b>, 21 blocks in one group, "
       "46 blocks in one mesh. City packets are <b>planar</b> - separate position and UV "
       "arrays - where vehicle packets interleave."),

 ("h3", "The VU1 double buffer"),
 ("p", "Four fields in a block header look like a triangle-parity flag and are not. They are "
       "<b>VU1 memory addresses</b>, and the two halves are always 295 quadwords apart. The "
       "field at +0x0C is a plain VIFcode, <font face=Courier>[imm u16][num u8][cmd u8]</font>: "
       "cmd 0x69 is UNPACK V3-16 (position), 0x65 is UNPACK V2-16 (UV), and num is the vertex "
       "count that also sits at +0x08 and +0x0E."),
 ("table", [
    ["field", "buffer A", "buffer B", "what it is"],
    ["+0x00", "imm 158", "imm 453", "UNPACK S-32, num 2"],
    ["+0x0C", "imm 256", "imm 551", "UNPACK V3-16, position"],
    ["after XYZ", "imm 208", "imm 503", "UNPACK V2-16, UV"],
    ["tail", "MSCAL 158", "MSCAL 453", "microprogram entry"],
 ], [66, 74, 74, 176]),
 hexrows("city/atlanta_midnight_clear.pck", [
    (0x18F850, 16, "block 0 - buffer A"),
    (0x18FA60, 16, "block 1 - buffer B"),
 ], "the same two blocks, first sixteen bytes - only the addresses differ"),
 ("p", "Read the last four bytes of each as a VIFcode. Block 0 ends "
       "<font face=Courier>00 01 30 69</font>: imm 0x0100 = 256, num 0x30 = 48 "
       "vertices, cmd 0x69 = UNPACK V3-16. Block 1 ends "
       "<font face=Courier>27 02 2E 69</font>: imm 0x0227 = 551, num 0x2E = 46. Same "
       "command, same geometry role, different half of the buffer - and the first "
       "four bytes carry the matching pair 0x409E and 0x41C5, which are 158 and 453."),
 ("note", "The half is chosen by the block's <b>position in its group's list</b>, and it "
          "resets at each group: <font face=Courier>odd = (index % 2 == 1)</font>. Measured: "
          "<b>785 of the 786 groups</b> in atlanta alternate A,B,A,B from block 0, the single "
          "exception being a degenerate group with imm 0. Copy the value from a source block "
          "instead and the alternation breaks the moment a list changes length - two blocks "
          "land in the same half and DMA overwrites what the VU is still running. It hangs."),

 ("h3", "The +0x14 chain is per block"),
 ("p", "This is the structure that makes a city mesh hard to edit, and it is invisible from "
       "the outside - it only shows up in the relocator:"),
 ("code", "mesh+0x14 -&gt; array of (mesh+0x0A) slot pointers\n"
          "  each slot -&gt; one 8-byte entry per group: [ptrA][ptrB]\n"
          "     ptrA -&gt; [u32 count][u32 vertices of each block]\n"
          "             and the payloads butted on right after\n"
          "     ptrB -&gt; array of `count` pointers, one per block"),
 ("p", "The count in every entry equals the number of blocks in that group: <b>6893 of 6893 "
       "entries</b> across atlanta, with no exception. Each payload is <b>one byte per "
       "vertex</b>, 16-aligned - a 48-vertex block gets 48 bytes, a 4-vertex block gets four "
       "bytes and zero padding. The slot count varies per model (2, 3, 4, 5, 6, 7, 8, 12 ...), "
       "so these are parallel sets of the same per-vertex attribute. What the attribute means "
       "is still unknown: high entropy, values clustered near +-127, so a normal or baked "
       "per-vertex lighting are the obvious guesses."),
 fields("city/atlanta_midnight_clear.pck", 0x1907E0, [
    (4, "block count of this group"),
    (4, "vertices of block 0"),
    (4, "vertices of block 1"),
    (4, "vertices of block 2"),
 ], "ptrA of group 2 - the payloads start right after, at 0x1907F0"),
 ("note", "Reusing the original chain while changing a block count is fatal, and asymmetric. "
          "Removing a block leaves a spare entry and nothing breaks; adding one makes the game "
          "index past the end. That asymmetry - 10 blocks fine, 11 fine, 12 hangs - is the "
          "signature to look for. Because the payload is per vertex, splitting a block only "
          "needs the same vertex slice used for position and UV."),

 ("h3", "Winding"),
 ("p", "The winding rule is fixed and carries no flag: triangle <i>i</i> is "
       "<font face=Courier>(i-2, i-1, i)</font> when <i>i</i> is even and "
       "<font face=Courier>(i-1, i-2, i)</font> when odd. The VU1 microprogram is the same in "
       "both buffer halves, so nothing per-block can invert it. Checking face normals against "
       "the model centroid over all 156 atlanta models gives <b>64.8% outward</b> - median 68% "
       "per model, 120 of 156 above 60% - and the opposite rule would give exactly the "
       "complement, so the convention is settled: even index is the front face."),
 ("note", "Deriving winding from the +0x0C field, which is a VU1 address, is the trap. Since "
          "that value alternates with list position, the facing of every triangle becomes a "
          "coin flip and roughly half the surface vanishes into back-face culling. To place a "
          "strip with the opposite winding, shift it by one index - prepend a duplicate "
          "vertex. Retail is full of these: 7.4% of its triangles are degenerate."),

 ("h3", "Reading the ELF"),
 ("p", "The PS2 executable loads into IDA with no coaxing - processor "
       "<font face=Courier>r5900l</font>, about 12500 functions - and "
       "<font face=Courier>mc3_ida.py</font> drives it headless, so a format question can be "
       "answered from the code instead of guessed from bytes. The MIPS decompiler is "
       "<font face=Courier>hexmips.dll</font> and it only loads under the <b>32-bit</b> "
       "<font face=Courier>idat.exe</font>; IDAPython needs a Python 3.9 or older. IDC "
       "scripting always works and needs no Python at all."),
 ("table", [
    ["address", "function"],
    ["0x1A8EC0", "city load: builds the path, loads type 67, relocates the root"],
    ["0x430CF8", "pack loader: validates the 0x80 header"],
    ["0x259160", "MapRoot relocator"],
    ["0x25D4F0", "Hood relocator"],
    ["0x2A9D18", "mesh relocator: group table and the +0x14 chain"],
    ["0x2AD148", "one group entry and its block list"],
    ["0x2A9E40", "one +0x14 slot (takes the group count)"],
    ["0x2AA0E0", "mesh destructor"],
    ["0x626D98", "real mesh vtable"],
 ], [72, 318]),
 ("note", "Several of these are not functions in a fresh database - they are only reached "
          "through vtables, so IDA never creates them. Scanning back for the "
          "<font face=Courier>addiu $sp, -N</font> prologue and calling "
          "<font face=Courier>add_func</font> recovers them."),

 ("h3", "What is still open"),
 ("p", "The meaning of the per-vertex byte in the +0x14 chain. The 21 resource pointers at "
       "MapRoot+0x1D4, the natural place for compiled occluders and the path graph. The "
       "0x38E7B0 type id on <font face=Courier>vp_*.pck</font>. Textures for converted "
       "geometry, which still sample the host city's atlas. And collision, which lives in the "
       "untouched <font face=Courier>_bnd.pck</font>."),
]),

("Collision and roads", [
 ("p", "The PS2 build has no loose <font face=Courier>.occlude</font>, "
       "<font face=Courier>.graph</font> or <font face=Courier>.aib</font> beside the city - "
       "the ones in the Xbox tree are uncompiled leftovers. On PS2 all of it is compiled into "
       "<font face=Courier>&lt;city&gt;_bnd.pck</font>, container type 9."),
 ("note", "The field names of the source format survive in the executable as dead strings the "
          "linker kept, around 0x647F32: <font face=Courier>bound  bndmax  maxOtEdges:  "
          "maxOtPolys:  maxOtVerts:  vertex  %s_quad_0  %s_roads  numroads:</font>. "
          "<b>\"Ot\" is octree</b>, and roads and quads sit right beside it - which is the first "
          "hint that the road network lives in the bound file, not in the geometry."),

 ("h3", "One octree and one quadtree"),
 ("p", "The root holds two trees of the same generic class, told apart by a type byte at "
       "node+0x04. A dispatch at <font face=Courier>0x46CB78</font> knows nine types "
       "(0, 1, 2, 3, 9, 10, 11, 13, 16); a city uses two of them, and the recursive relocator "
       "gives away what they are - one loop walks <b>8</b> children, the other <b>4</b>:"),
 ("table", [
    ["type", "children", "payload at", "role"],
    ["9", "8 (octree)", "+0x94", "world collision"],
    ["10", "4 (quadtree)", "+0x78", "road network"],
 ], [42, 78, 72, 150]),
 ("p", "The bounding boxes confirm it without ambiguity. The quadtree is flat - detroit's is "
       "exactly 0 units tall - while the octree reaches 233 in atlanta and 357 in tokyo, where "
       "the skyscrapers are:"),
 ("table", [
    ["city", "quadtree height", "octree height", "vertices", "polygons"],
    ["atlanta", "10", "233", "25395", "21617"],
    ["detroit", "0", "117", "21842", "17840"],
    ["san diego", "14", "112", "24314", "20297"],
    ["tokyo", "10", "357", "19475", "14549"],
 ], [70, 88, 78, 66, 66]),
 ("p", "The low bit of a node pointer marks a leaf: if it is set the relocator neither "
       "descends nor relocates. Each payload also carries a flat list of u16 primitive indices "
       "that the leaves slice into - 56573 of them for the atlanta octree against 21617 "
       "polygons, since one polygon belongs to several leaves. The quadtree indexes far less "
       "(153 in atlanta, 554 in detroit), which is the right order of magnitude for "
       "<font face=Courier>numroads:</font>."),

 ("h3", "The geometry"),
 fields("city/atlanta_bnd.pck", 0x80, [
    (4, "vertex count"),
    (4, "polygon count"),
    (4, "h:unused"),
    (4, "h:the structure that holds both trees"),
 ], "the bound root"),
 ("p", "The type 9 node repeats those two counts and points at the arrays:"),
 ("table", [
    ["field", "meaning"],
    ["+0x08 .. +0x1F", "AABB, min corner then max"],
    ["+0x20 .. +0x37", "centre, stored twice"],
    ["+0x38, +0x3C", "two radii"],
    ["+0x4C", "vertex count"],
    ["+0x50", "polygon count"],
    ["+0x68", "vertices, float3, 12 bytes each"],
    ["+0x6C", "polygons, 32 bytes each"],
    ["+0x70", "material id table, count at +0x74"],
 ], [110, 280]),

 ("h3", "The 32-byte polygon"),
 fields("city/atlanta_bnd.pck", 0x1A20, [
    (4, "f:plane normal x"),
    (4, "f:plane normal y"),
    (4, "f:plane normal z"),
    (4, "f:polygon area"),
    (2, "vertex 0"), (2, "vertex 1"), (2, "vertex 2"), (2, "vertex 3"),
    (2, "neighbour 0"), (2, "neighbour 1"), (2, "neighbour 2"), (2, "neighbour 3"),
 ], "the first polygon of atlanta"),
 ("p", "A polygon is a <b>triangle when v3 == 0 and n3 == 0xFFFF</b>, and those four bytes go "
       "unused; otherwise it is a quad. 0xFFFF in a neighbour slot means the edge is a "
       "boundary. Nothing here identifies a material - all 32 bytes are accounted for, and how "
       "the id table at +0x70 is reached is still unresolved."),
 ("note", "Verified over the four shipped cities, <b>74907 polygons</b>: every area recomputed "
          "from the vertices matches the stored one, every normal is unit length, and all "
          "<b>195240 neighbour references are mutual with none broken</b>. That mutual face "
          "graph is worth noticing - it means collision walks from polygon to polygon rather "
          "than querying the tree every frame."),
 ("p", "<font face=Courier>mc3_bound.py</font> parses all of this and exports either tree as "
       "Wavefront OBJ."),

 ("h3", "MC2's BND0 and a bound built at load time"),
 ("p", "MC2 PS2 keeps a city's collision inside <font face=Courier>&lt;city&gt;.rsc</font> as "
       "BND0 blocks. Los Angeles has two that matter: <font face=Courier>losangeles</font>, "
       "type byte 0x0B (otgrid: +0x5C cells in x, +0x68 in z, +0x6C first cell, cells 0x90 "
       "apart, an empty one has +0x84 = 0xFFFFFFFF), and <font face=Courier>losangeles_quad_0"
       "</font>, type 0x0A. Each cell and the quad are nodes with <b>MC3's node layout</b>: "
       "+0x4C verts, +0x50 polys, +0x74/+0x78 vertex/polygon offsets from the start of the "
       "block, +0x80/+0x84 a table of PMT0 handles (entry = (h &amp; 0x3FFF) - 1, name at "
       "PMT0+8). The 32-byte polygon is MC3's, with the cell's material slot in the low byte "
       "of the area."),
 ("note", "Two differences. Indices are local to the cell. And <b>a triangle is index 3 == 0 "
          "alone</b>: MC2 writes 0 in neighbour 3, and its neighbours are edge indices, not "
          "polygons. Read with MC3's v3 == 0 and n3 == 0xFFFF rule, ~7800 triangles turn into "
          "quads that reach vertex 0 of their cell."),
 ("p", "<font face=Courier>losangeles_bound.mod</font> hooks the jal to "
       "<font face=Courier>datRscBuilder::LoadBuild</font> (0x42E810) at 0x1A937C in "
       "<font face=Courier>mcLayerCity::InitPhys</font>. The loader returns a body allocated "
       "with <font face=Courier>aligned_new(size, 128)</font>, *out1 = the virtual base "
       "(0x06800000) and *out2 = the size, and <font face=Courier>mcPhysics</font> relocates "
       "by the difference - so a body built in the heap in that form is accepted as if it were "
       "the file. For Los Angeles it takes 0x330 bytes of non-geometry objects from "
       "<font face=Courier>tokyo_bnd.pck</font>, the otgrid as type 9 and the quad as type 10 "
       "(140 verts / 176 polys), and builds both trees on the EE. The quadtree's quadrant bits "
       "are bit0 = x, bit1 = z (222 of 222 tokyo references in the right cell). LA: 20379 "
       "polygons, 45726 references, 617 blocks; 1.08 MB against 1.65 MB for the converted "
       "<font face=Courier>losangeles_bnd.pck</font>, which is no longer installed."),
 ("p", "The same runtime builder now selects MC2 data by MC3 city index. Paris is city 6: "
       "its type 0x0B grid is 7 x 7 and builds 26,858 vertices, 18,299 polygons, "
       "33,822 references and 237 type-9 blocks at depth 7. Its type 0x0A quad has "
       "340 vertices and 412 polygons (775 references, 16 blocks). The returned body is "
       "0xF5DC0 bytes. A physical sample of 451 points spread over the Paris grid produced "
       "451 expected rests and zero falls, respawns or false high surfaces."),

 ("h3", "The traffic network (_city.aib)"),
 ("p", "<font face=Courier>ASSETS/city/&lt;city&gt;/&lt;city&gt;_city.aib</font> is a TaggedStream: "
       "\"TSV1\", then records of a u16 tag and a u32 length. The loader (aiRailNetwork, "
       "0x503C00) is versioned: 0x4100 counts, 0x4101 nodes, 0x4103/0x4107 road v1/v2, "
       "0x4105 traffic control, 0x4106 router (0x4200/0x4205 inside), 0x4108 name, "
       "0x410A/0x410B/0x410D traffic light v2/v3/v4, 0x410C optional extents. MC2's "
       "<font face=Courier>losangeles.aib</font> uses 0x4107 and 0x410A and no 0x410C, and "
       "loads unchanged as <font face=Courier>losangeles_city.aib</font> (the one it replaced "
       "was tokyo's, byte for byte)."),
 ("p", "The router ends with an n x n u16 next-hop table indexed by road: from road a to road b "
       "go to road table[a*n + b], 0xFFFF = no way. The route search 0x5059B0 walks it for the "
       "battle modes' AI only (capture_the_flag, tag, keepaway, bomb_tag, paint...); point-to-point "
       "races never call it. MC3 stores it as 0x4202 (u32 n) + 0x4204 (one block, Tokyo n = 277 of "
       "717 roads); MC2 as 0x4206 (u16 n) + n records 0x4207 (u16 n + one row), n = every road. "
       "MC3's loader skips MC2's tags, so n and the table pointer (router+68/+72) stayed 0 and the "
       "search read 2*road from address 0: TLB miss at 0x505A30, read as 0 by PCSX2's recompiler, "
       "fatal on a PS2. mc2_aib_compat builds the table from MC2's rows at load time, only for a "
       "battle race type (+0x18 of the current mcRaceConfig): it costs 2*n*n bytes (LA 211 KB, "
       "Paris 220 KB, Tokyo 331 KB) of the heap the props' physics load from next. Otherwise the "
       "router gets n = 0 and a 2 KB all-0xFFFF table from the same heap."),
 ("p", "The battle route planner (sub_41B440) asks sub_41B120 for the nodes shared by a road and "
       "the next one; a path ending on a road of type 0 or 4 has no next road and the call reads "
       "address 4 (TLB miss 0x41B150/0x41B160) - with MC2's files on every battle map. The three "
       "calls (0x41C148, 0x41C164, 0x41C23C) answer 0 for a null road, which is what the "
       "emulator's read of 0 produced."),
 ("h3", "MC2 CPVS: an instance's chain is not its whole model"),
 ("p", "In MC2's <font face=Courier>&lt;time&gt;_cpvs.rsc</font> each placed instance has one "
       "<font face=Courier>..._&lt;type&gt;&lt;extension&gt;_0_main</font> PCP0 - the DMA chain that "
       "draws it with per-vertex colour by <font face=Courier>ref</font>-ing into the PMD0s of the "
       "model. In 100 of LA's instanced types (1265 of 7045 instances) that chain references only "
       "the model's first near PMD0; the others have no PCP0 at all and are drawn straight from the "
       "PMD0, flat. They are the model's ground: freeway and road decks, warehouse yards, office "
       "parking. A renderer that draws the PCP0 instead of the model loses them - the background "
       "shows through. Draw every near PMD0 the PCP0 does not reference after it."),
 ("p", "Paris uses the same scheme with <font face=Courier>_p_inst_</font> rather than LA's "
       "<font face=Courier>_l_inst_</font> token. Its original resource contains 1,493 PMD0 and "
       "699 CMI0 records (270 components plus 429 types), 8,195 placements and 11,921 PCP "
       "references. The final dawn/clear runtime resolved 178 component chains and 7,460 "
       "instance chains; 4,460 of those instances needed one or more uncovered PMD0 pieces."),
 ("p", "The same file names a second LOD 0 chain per model, <font face=Courier>_0_refl</font>. "
       "It is not a reflection pass over the same geometry: it colours the model's OTHER near pieces - "
       "road deck, sidewalks, gutters - that no <font face=Courier>_main</font> references (LA 309 "
       "pieces, Paris 343; same format and VU program). Without it that ground is drawn flat, a bright "
       "seam against the lit asphalt beside it."),
 ("h3", "Occluders: why a car's lights show through a wall"),
 ("p", "MC3 hides a car behind a building by testing it against box occluders from "
       "<font face=Courier>ASSETS/city/&lt;city&gt;/&lt;city&gt;.occlude</font> (text, format 3: per box 8 "
       "vertices from y -100 to the roof, 12 edges, 6 faces; atlanta 340, detroit 455). The local player "
       "goes through rmcCarModel::CullUpdate (0x2F6278, IsAABoxOccluded 0x39B890, result at model+64); "
       "opponents, traffic and props through mcOccluderSystem::DualFrustumVisCheck. A culled car loses its "
       "body, its glows, its light reflections on the road and the light it throws on walls. Without "
       "boxes that fit the city, only the body is hidden (by the Z buffer) and the lights show through "
       "the building. The viewer and the cull frustum are the LOCAL PLAYER's camera (mcGame::PreDraw), "
       "so a debug camera placed elsewhere sees cars vanish whenever they leave the player's frustum."),
 ("p", "MC2's own .occlude holds a few tunnel quads, so the boxes of an MC2 city are generated "
       "(mc2_occluders.py) from MC2's collision and drawn geometry: a flood fill from the aib's lanes over "
       "floor, bounded by collision walls, leaves the building interiors; drawn facades and roofs give the "
       "heights; the void behind a facade counts as that building; no box may touch a lane."),
 ("h3", "A MC2 .aib in MC3: traffic controls and rail flags"),
 ("p", "MC3's aiRailNetwork loader reads MC2's <font face=Courier>&lt;city&gt;.aib</font> tag by tag, but "
       "two records differ. A traffic control (tag 0x4105) is, in MC3, u16 count + ids + two floats "
       "(10.0, 2.5); MC2 stops after the ids. The reader takes the floats anyway, and "
       "TaggedStream::ReadTag (0x5B86F8) ends the whole stream when a reader went past its record - so "
       "nothing after the first control loaded, including the aiRouter (0x4106: box, 50 m cells, cell to "
       "road lists). And bit 1 of a rail's flags (+18 of the 20-byte 0x4109 record) means 'no traffic "
       "here' to MC3 (set only with bit 5 in retail) but is on most MC2 rails. MC2 also stores "
       "pedestrian paths in the same AIB and marks them with bit 3. Result without adaptation: "
       "zero ambient traffic; clearing bit 1 indiscriminately puts cars on sidewalks. The "
       "mc2_aib_compat shim reads short controls, clears bit 1 only on car rails, and gives every "
       "bit-3 pedestrian rail the native MC3 closed pair (bits 1+5). Paris measured 2,113 car rails "
       "opened and all 1,305 pedestrian rails closed out of 3,805. A live scan of every active "
       "ambient car found zero on pedestrian rails; a cubic-Hermite rail-versus-collision scan found "
       "zero car-rail centers inside sidewalk polygons. Both systems still use the same original AIB; "
       "the runtime flag mapping separates their rail subsets."),
 ("h3", "HUD map of an added city"),
 ("p", "The minimap image comes from the streamed build <font face=Courier>resources/city/hudmap</font>: "
       "a table of rmcTexturePS2 pointers indexed by city (+3 for the locked variant) - 0 sd, 1 atlanta, "
       "2 detroit, 3 tokyo, 4 atlanta_locked, 5 detroit_locked, then per-race maps. The world-to-map "
       "scale comes from hudMap::SharedDataAllTypes at 0x6BDE20, 32 bytes per city "
       "(min x, max x, span, 1/span, then the same for z), filled by sub_285438 for cities 0..3 only; "
       "its readers index it through sub_285508(table, city). A city 5 therefore draws detroit_locked "
       "with garbage extents. mcTextureFactory::Create(name) (vtable +12 of *0x618954) makes an "
       "rmcTexturePS2 that loads a loose <font face=Courier>ASSETS/texture/&lt;name&gt;.tex</font> "
       "(+ _proxy), and sub_285540(entry, city) fills an entry from mcCity::GetCityExtendsForMap."),
 ("p", "For Paris the loose image is MC2's original 65,614-byte "
       "<font face=Courier>hud_map_paris.tex</font>. The synthetic MC3 shell carries only one "
       "empty-cookie hood per source hood and a harmless cube, but keeps Paris's measured map "
       "extents: x -1880..1520 and z -1640..1360. The city geometry itself remains in the "
       "original MC2 files and is drawn by the runtime renderer."),
 ("h3", "Registering a seventh city"),
  ("p", "The visible Arcade cycle is not the only city-count limit. "
       "<font face=Courier>mc::LoadCityData</font> at 0x4B79D8 contains "
       "<font face=Courier>slti v0,s3,6</font>; changing the Arcade modulo alone leaves city 6 "
       "with no races. The Paris slot patch changes that guarded instruction to a limit of 7 "
        "and flushes the instruction cache. With a valid <font face=Courier>paris.loc</font>, the "
        "runtime reported 24 entries: one standard Cruise plus 23 converted MC2 races."),
  ("p", "The converter also removes MC2's nested <font face=Courier>PreRacePath</font> "
        "from Opponent blocks. MC3 does not consume that grammar; it loses token alignment "
        "and supplies a null CarType to the following aiOpponentFactory. This was isolated "
        "from the only two Paris races that stopped at loading. After the removal, all 22 "
        "Paris races with opponents reached the city with their declared opponent counts."),
 ("h3", "The AI floor (aiRouter box)"),
 ("p", "aiOpponent::Update (0x4034A0) starts with a fell-out-of-the-world test: when the "
       "car's y is below the aiRouter box's min y - 10 it calls the brain (vtable +64, puts "
       "the car back on its path) and skips the rest of the AI, every frame. The router is "
       "<font face=Courier>*(aiRailNetwork::sm_pinstance 0x61B170 + 88)</font>, box min at "
       "+0 and max at +12. Retail AIBs load it, and MC2 AIBs load it after the short-control "
       "shim above; the earlier claim that it was never loaded was wrong. A city-specific "
       "floor is still required for roads below the router margin: in LA, "
       "the PCH under the Santa Monica bluffs (y -16) held all eight cars at "
       "(-826, -16, -1170). Los Angeles sets min y to MC2's own value (-16.31, router tag "
       "0x4205 of losangeles.aib), which puts the floor at -26.3. Paris uses its own router "
       "minimum -17.301518, producing a threshold of -27.3."),
 ("note", "Not the distance to the player: with the player's car kept 30 m above the "
          "watched opponent every frame (moved with aiBrain::TeleportCar 0x3FC600, which only "
          "reads brain+12 -> opponent and opponent+36 -> car) the cars stacked the same, and "
          "aiStuck (opponent+116: state +8, count +20, really-stuck +92) stayed 0."),
]),

("Vector unit microcode", [
 ("p", "The executable was linked <b>without stripping</b> and still carries the whole "
       "DVP toolchain layout: <font face=Courier>.vutext</font> holds 175568 bytes of "
       "microcode (21946 instructions), <font face=Courier>.DVP.ovlytab</font> is 97 "
       "records of 12 bytes and <font face=Courier>.DVP.ovlystrtab</font> names them. A "
       "record is [name offset][EE address of the chunk][destination in VU micro memory], "
       "and the section name spells out the rest: "
       "<font face=Courier>.DVP.overlay..&lt;vma&gt;.&lt;program&gt;.&lt;line&gt;.&lt;chunk&gt;</font>."),
 ("p", "That yields <b>21 microprograms of 103 to 1677 instructions</b>. Each image holds "
       "several routines - the E bit ends one - which is how the engine MSCALs a different "
       "entry point without re-uploading. Do not derive the destination from the chunk "
       "index: chunks are 2048 bytes and usually land on 0x000, 0x800, 0x1000, but program "
       "12963 has a 256-byte preamble at 0 and jumps to 0x1F40."),
 ("note", "There are no MPG VIFcodes to find. Scanning the ELF and 400 .pck for command "
          "0x4A, requiring the E bit on exactly the last instruction of the run, finds "
          "nothing - the upload packet is built at runtime. And `ps2microcode` is a keyword "
          "of the .shadert grammar, not a pointer."),
 ("h3", "How the city packet is consumed"),
 ("p", "The 16-bit value that looked like a per-strip parity flag is the immediate of a VIF "
       "<b>ITOP</b>, sitting right before the MSCAL that ends each batch. The VU program "
       "opens with <font face=Courier>XITOP</font> and reads everything relative to it. "
       "MSCAL 6 lands in a jump table and branches to instruction 264 of program 14508803."),
 ("table", [["relative to ITOP", "UNPACK", "what it is"],
            ["+0", "S-32 num=2", "2-qword header; the per-block scale, and the count in .w"],
            ["+2", "V4-8 / V3-8", "normal in s8, bit 0 of x is the ADC flag"],
            ["+50", "V2-16 / V4-16", "UV, converted with ITOF12"],
            ["+98", "V3-16", "position, scaled by 1/128"]], [120, 110, 250]),
 ("p", "Measured over 3003 blocks. Only <b>7.3%</b> of city blocks carry the +2 stream (759 "
       "of atlanta's 10338), and which shader path reads it is chosen by a flag word at VU "
       "absolute 34 - so emitting it for a mesh whose material does not read it only dirties "
       "a region shared between blocks."),
 ("p", "The fourth byte of that stream is a <b>palette index</b>: "
       "<font face=Courier>MTIR</font> takes it (fsf is bits 22..21, not 24..23) and "
       "<font face=Courier>LQ 896(VI)</font> reads a colour from a 256-entry table at "
       "768..1023. So the city has <b>no angle-dependent lighting</b>; it is baked per "
       "vertex. The index is not reconstructible - its correlation with the normal is "
       "+0.045/-0.001/+0.059 and with height +0.051."),
 ("h3", "Trains and pedestrian graphs are compiled into the city packs"),
 ("p", "A city's trains are not read from <font face=Courier>tune/city/&lt;city&gt;.train</font> at run "
       "time: they are compiled into <font face=Courier>&lt;city&gt;_traffic.pck</font> (header type 0x3C). "
       "Its root object is at file offset 0x80 and root+0x11C points at the train manager, whose first "
       "field is an atArray of mcTrain (pointer, u16 count, u16 capacity): retail San Diego 2, Tokyo 4, "
       "Detroit 1, Atlanta 0. The tracks are built at load time from every road of type 3 in the city's "
       ".aib (mcTrainBuilder::BuildTrainTracks 0x232D48); an MC2 .aib uses type 3 for ordinary roads, so a "
       "traffic pack copied from another city runs its trams through them. Setting the count to 0 removes them."),
 ("p", "Likewise the pedestrians do not walk on <font face=Courier>city/&lt;city&gt;/&lt;city&gt;.graph</font>: "
       "retail keeps only mcBaGraph(datResource&amp;), and the graph is the first member of the root of "
       "<font face=Courier>&lt;city&gt;_peds.pck</font> (type 0x21, root at 0x80)."),
 ("table", [
    ["root field", "array", "element"],
    ["+0x00 / +0x04", "vertices, u16 count + capacity", "Vector3, 12 bytes"],
    ["+0x08 / +0x0C", "nodes", "40 bytes: u16 vertex[8], u16 connection[8] (0xCDCD unused), u8 vertex count, u8 connection count, u8 type (0 sidewalk, 2 corner), u8 1, float"],
    ["+0x10 / +0x14", "connections", "u16 vertex1, vertex2, node on that side (6 bytes)"],
    ["+0x1C / +0x20", "node user data", "3 bytes: 00 01 01 sidewalk, 00 00 00 corner"],
  ], [30*mm, 45*mm, 95*mm]),
 ("p", "mcFeedSurfaceManager builds its spawn list from the graph at run time, so appending new arrays "
       "and repointing the root is enough; a round trip of Tokyo's text graph reproduces the retail node and "
       "connection bytes exactly."),
 ("h3", "Prop particles: compiled rules, text .ptx and the shared atlas"),
 ("p", "A retail prop's particles (hydrant spray, newspapers, splinters, steam) are "
       "mcPropParticleBirthRule objects (0x190 bytes) compiled into "
       "<font face=Courier>resources/prop/&lt;city&gt;_&lt;tod&gt;_&lt;weather&gt;_props.pck</font>, each followed "
       "by its name <font face=Courier>&lt;type&gt;_&lt;part&gt;</font> (+4 points at it). The same object loads "
       "from text: LoadWithHash 0x390070 / parFileIO::Load 0x55F3D8 read "
       "<font face=Courier>tune/effects/&lt;name&gt;.ptx</font> (vtable 0x62CD38: directory tune/effects, "
       "extension and block ptx). mc3_prop_ptx.py writes the compiled rules as text; they reload byte-identical."),
 ("table", [
    ["offset", "field (swPtxBirth::FileIO 0x1EFE7C)", "offset", "field (rule, 0x3900F8)"],
    ["+80 / +84", "Life, LifeVar", "+0x160", "m_inheritMatrix (u8)"],
    ["+88", "PositionVar", "+0x161", "m_sprayAfterBroken"],
    ["+100 / +112", "Velocity, VelocityVar", "+0x162", "m_inheritCollisionVelocity"],
    ["+124", "VelocityDamping", "+0x163", "m_bRecieveAmbientLighting"],
    ["+136 / +138", "Start/EndTextureTile (s16)", "+0x164", "m_bImpactStrengthAffectsSpawnRate"],
    ["+152 / +160", "RadiusBirth, RadiusDeathPercent", "+0x168", "m_minForceToSpawn"],
    ["+172", "Gravity", "+0x16C", "m_emissive"],
    ["+176 / +192", "ColorBirth, ColorDeath", "+0x170", "m_alwaysOn"],
    ["+208 / +252", "RotateSpeed, RotateVar", "+0x178", "m_position"],
    ["+248 / +272", "RadiusVar, NumInitialParticles", "+0x184 / +0x188", "m_spewTimeLimit, m_emitRate"],
  ], [22*mm, 58*mm, 22*mm, 68*mm]),
 ("p", "They are thrown by mcPropManager::s_pSwPtx (0x617F6C), a swPropPtxSystem of 256 particles that "
       "mcLayerCity::Load creates in every city and mcSwPtx::Draw (from mcPlayer::Draw) draws with ONE "
       "texture, s_pPropParticleTex: <font face=Courier>&lt;x&gt;_shared_particle</font> from the city's texture "
       "folder, x = s (city 0), a (1), t (3), d (any other, so also cities 5 and 6). The tiles are an 8x8 grid "
       "of that atlas, so a rule only fits its own city's atlas. mcPropFixed::EmitParticles 0x390B38: count = "
       "AdjustPropEmissionCount(m_emitRate), position = m_position through the prop matrix, "
       "BlastTransformed 0x1F42D8; colours times the city ambient (mcCity +0xE0) and the nearest light when "
       "m_bRecieveAmbientLighting. The system's byte +52 (set for hydrants, copied into each particle) is "
       "built as 0xCD and only EmitParticles clears it; set, Update 0x1F3850 stops every particle within "
       "3 m of a car."),
 ("h3", "Sky and prop lights of an MC2 city"),
 ("p", "The city's sky is mcSkyHatClass (mcCity+0x190): eight layer models at +0x34..+0x50 (rmcModel*, "
       "freed by its destructor 0x25BB98, drawn by Draw 0x25C238 with the sky shader group mcCity+0x10) and the "
       "tune file skyhat_&lt;city&gt;_&lt;tod&gt;_&lt;weather&gt; (SetupTuneData 0x25BC90). A built city pack with null "
       "layers and an empty group shows only m_clearColor. MC2 keeps its sky in "
       "<font face=Courier>resource/&lt;city&gt;/&lt;time&gt;_&lt;weather&gt;.rsc</font>: MOD0 sky_0 (dome, radius 100) "
       "and sky_1..6 (rain clouds), handles 0xC0xx = this container."),
 ("p", "An MC2 PRP0 carries its lights: +0x18 count, 0xC0-byte records from +0x20 = MC2's light_data "
       "(+0x0C type, +0x10 position, +0x20 color, +0x30 intensity, +0x34 decay, +0x38 direction, +0x44 spot "
       "angle, +0x4C draw_glow, +0x50 glow_offset, +0x60 glow_color, +0x70 draw_cone, +0x74..+0x7C cone size/"
       "intensity/offset, +0x80 cone_color, +0x90 draw_flare, +0x94 flare_offset, +0xA0 flare_color, +0xB0 "
       "reflection_color). MC3's mcLightData (176 bytes, ctor 0x5958E8, FileIO 0x595B18, dir tune//lightdata) "
       "has the same fields: +0x0C type, +0x10 intensity, +0x18 local position, +0x24 direction, +0x30 color, "
       "+0x40 glow, +0x50 cone, +0x60 flare, +0x70 reflection colours, +0x80 flags (1 flashing, 2 directional), "
       "+0x81..+0x83 draw glow/cone/flare, +0x84 decay, +0x88 spot angle, +0x8C dropoff, +0x90 glow offset, "
       "+0x94..+0x9C cone, +0xA0 flare offset, +0xA4 flare size. mcLight (36 bytes): +0 world position, +12 next "
       "in cell, +28 data, +32 s16 cell, +34 flags (4 = on); init 0x259988(light, Matrix34, data); grid "
       "mcLightManager 0x70FA4C (mcCity+616 cells), AddLightToGrid 0x259658. Drawing is per prop: "
       "mcPropType::Render calls 0x25A1F8 (reflection, $f12 = prop ground height) in pass 4 and 0x259FF0 "
       "(glow/cone/flare) in pass 512; both only add to mcGlow's buffers (350 per frame)."),
 ("h3", "MC2 ground textures: the alpha is wetness"),
 ("p", "In MC2's shared texture banks (<font face=Courier>textures_&lt;time&gt;_&lt;weather&gt;.rsc</font>) the "
       "ground textures' CLUT alpha is not opacity but how wet each texel is, per weather. LA, max / mean: "
       "clear asphalt 0 / 0, cloudy 29 / 7, rainy 104 / 25, rainy sidewalks 128 / 30. MC2 pastes its "
       "reflections into the frame with ALPHA 0x58 (Cs * Ad + Cd) and 0x54 ((Cs - Cd) * Ad + Cd), alpha "
       "write masked and depth test ALWAYS (read from the DMA list of an MC2 frame), so the frame alpha "
       "the ground leaves is the reflectivity, exactly as in MC3."),
 ("p", "The <font face=Courier>*cardblk*</font> instance models (68 in LA, 37 in Paris, texture "
       "<font face=Courier>fx_reflection_01</font>) are vertical glow cards 1.4 to 20 m below the ground "
       "under shop windows: the windows mirrored, meant to be seen only through that alpha."),
 ("h3", "The palette alpha is used by whoever draws next"),
 ("p", "The two palettes are inline in the MapRoot (file offsets 0x310 and 0x1310): +0x30 is "
       "the source and +0x2C the live copy that <font face=Courier>mcCity::ChangePaletteSaturation</font> "
       "(0x257770) rebuilds through HSV with the condition's multipliers. Every retail city, in "
       "every condition, has <b>alpha 0 in entries 0..7, alpha 1.0 in 8..255, and entry 255 "
       "opaque white</b> - the one <font face=Courier>rmcCpvPalette::Lookup</font> tests first."),
 ("p", "The alpha matters outside the city geometry. <font face=Courier>mcCityModelClass::SetRenderStates</font> "
       "downloads the live palette to VU1 and it stays resident; <font face=Courier>mcCheckpoint::Draw</font> "
       "(0x3CB738) then calls <font face=Courier>rmcSetCpvMode(1, 0)</font> without a palette of its own. "
       "The <font face=Courier>cp_flare*</font> shaders are modulate + blendset normal, so the checkpoint "
       "smoke column takes its alpha from the city palette. A synthetic city with alpha 0 in all 256 "
       "entries draws the column fully transparent - measured, and fixed by giving the palette the "
       "retail alpha."),
 ("h3", "Walking the stream without desyncing"),
 ("p", "Three things must be right. UNPACK data pads to <b>four</b> bytes, not sixteen. "
       "STMASK carries 4 bytes of its own and STROW/STCOL carry 16. And a <b>masked</b> "
       "UNPACK only reads the components whose two mask bits are 00 - city blocks issue a "
       "V4-8 masked under STMASK 0x55555555, every field ROW, which reads nothing at all; "
       "assuming num*4 bytes there skips 144 bytes that do not exist and loses the rest of "
       "the packet. The UNPACK immediate is [FLG:1][USN:1][ADDR:14]."),
]),

("The file layer", [
 ("p", "Path resolution has a <b>single global root</b> at 0x711C20 and no search chain. "
       "BuildPath copies the root, skips two characters when the name starts with "
       "<font face=Courier>$</font>, appends the top of an 8-deep directory stack otherwise, "
       "and appends <font face=Courier>.&lt;ext&gt;</font> when the name has no dot - which "
       "is why the literal string \".pck\" does not appear anywhere. The 49 "
       "<font face=Courier>$/...</font> literals are the entire logical namespace."),
 ("p", "When assets.dat mounts, the read device is <b>replaced, not chained</b>: there is no "
       "loose-file fallback behind an archive."),
 ("h3", "Kinds with a raw, non-pck reader"),
 ("p", "Geometry reads <font face=Courier>.mod</font> and <font face=Courier>.mesh</font> as "
       "<b>ASCII</b>, and the vehicle mesh loader falls back to it when the .pck fails. "
       "Collision reads <font face=Courier>.bbnd</font> binary falling back to "
       "<font face=Courier>.bnd</font> ASCII. Textures are never pck - .tex, .xtex, .tga, "
       ".ipu, .bmp, .spr. <font face=Courier>.occlude</font> is live and is text, and "
       "<font face=Courier>.aib</font> (the AI road network) is read from the loose file. "
       "Only <font face=Courier>.graph</font> is genuinely dead. What is pck-only is the "
       "city-scale aggregate: city, bnd, fog, traffic, peds, hudmap, prop, decal."),
 ("h3", "Why a bad file gives a black screen"),
 ("p", "The retail build nulled its Errorf/fatal calls and left the "
       "<font face=Courier>while(1);</font> - <b>179 instances</b> of six nops followed by a "
       "branch back six. Eleven of the thirteen .pck consumers sit on one. The loader gives "
       "up silently three ways (file missing, version != 1, wrong type id) and the caller "
       "spins forever. If the PC lands on 0x1A9080 the city was rejected; 0x1A93D8 is the "
       "_bnd."),
 ("h3", "A ZIP fallback that shipped disabled"),
 ("p", "When a loose file fails to open, the resource open replaces the last "
       "<font face=Courier>/</font> of the path with <font face=Courier>.zip</font> and looks "
       "for the basename inside, walking local headers and reading uncompressed-size bytes "
       "raw - <b>stored entries only, there is no inflate</b>. It is gated on a byte at VA "
       "0x618D00 (file 0x478D80) that nothing in the game ever writes. Setting it works: "
       "confirmed in game by removing atlanta_bnd.pck and serving it from "
       "resources/city.zip."),
 ("note", "The loose file wins and the zip is only consulted on failure, so the natural "
          "modloader arrangement is the inverse of the obvious one: vanilla inside zips, "
          "mods loose on top."),
]),

("Hoods and how a mesh is placed", [
 ("p", "A city is seven neighbourhoods. The table hangs off MapRoot+0x14 (count) and "
       "+0x18 (pointer), and each 20-byte entry is [record][unique count][unique array]"
       "[instance count][instance array]. The record is 176 bytes and starts with the "
       "hood NAME inline, which matches the prefix on that hood's meshes exactly."),
 ("table", [["hood", "name", "unique", "instance"],
            ["0", "bh", "51", "505"], ["1", "dt (downtown)", "183", "747"],
            ["2", "fwy", "75", "233"], ["3", "lf", "90", "523"],
            ["4", "mt", "155", "902"], ["5", "ug (underground)", "43", "147"],
            ["6", "we", "54", "551"]], [70, 150, 90, 90]),
 ("p", "The <font face=Courier>a_inst_*</font> models are instanced props SHARED "
       "between hoods, so touching those changes the whole city rather than one "
       "neighbourhood. A hood's own geometry is its UNIQUE components."),
 ("h3", "Two kinds of component"),
 ("p", "An <b>instance</b> component is 96 bytes and carries a 3x4 transform at +0x20: "
       "three unit rotation rows and the translation at +0x44. Using the translation "
       "alone draws every rotated building facing the wrong way. The convention is "
       "row-major - solving the same model's local centre through two differently "
       "rotated instances of it gives the same answer both times."),
 ("p", "A <b>unique</b> component is only 28 bytes and has NO transform. What places "
       "it is the bound sphere on its ComponentData: <b>+0x2C is the centre in world "
       "coordinates</b> and +0x38 the radius, while the vertices are local. The "
       "component itself carries the tile box at +0x14 and a copy of the radius at "
       "+0x18."),
 ("note", "That +0x2C is really the placement was settled by experiment, not by "
          "reading: writing the SAME value into every mesh of a hood made the objects "
          "collapse onto one point in game. Neither the radius convention nor the tile "
          "box could decide it - a tile-box test over all 479 unique meshes scored the "
          "two candidate readings at 2.65 and 2.43 units of error, far too close to "
          "call."),
 ("h3", "Ten LOD slots, and some have nothing in slot 0"),
 ("p", "<font face=Courier>ComponentData+0x3C</font> is ten slots and each one is a "
       "DISTINCT mesh - the LOD variants. Of the 43 unique components of atlanta's "
       "`ug`, 23 have one slot filled, 13 have two, 6 have three and one has five. "
       "<b>Some have nothing in slot 0 at all.</b> Replacing only slot 0 therefore "
       "skips those components entirely and leaves every other component's distant LOD "
       "drawing the ORIGINAL geometry at the ORIGINAL place, which in game looks like "
       "pieces of the old location following the camera around."),
 ("h3", "Culling has to be resized too"),
 ("p", "Replacing geometry without touching the radius leaves a 52-unit tunnel piece's "
       "culling around a 195-unit building, and the game drops it before the camera "
       "gets close. The first whole-hood fill loaded perfectly and showed nothing for "
       "exactly that reason. The radius at cd+0x38 and comp+0x18 and the tile box at "
       "comp+0x14 all have to follow the new geometry."),
]),

("The Midnight Club 2 side", [
 ("p", "MC3's city is a compiled form of MC2's, and MC2 ships the sources as TEXT. "
       "Anything unclear on the PS2 side is usually spelled out there, so the MC2 "
       "assets are worth checking before assuming anything."),
 ("table", [["file", "what it holds"],
            ["&lt;city&gt;.lvl", "numhoods, extents_min/max, dimensions, hood names"],
            ["&lt;hood&gt;.hood", "every component: name, world AABB, and for instances a 3x4"],
            ["*.pdef", "prop definitions"],
            ["models/*.xmod", "geometry, ASCII, in WORLD coordinates"],
            ["*_cpvs.dat", "baked per-vertex colour, one per time and weather"],
            ["*.occlude, *.aib", "occluders and the AI road network"]], [150, 300]),
 ("p", "A <font face=Courier>unique_component</font> in a .hood is <b>name plus emin/"
       "emax and nothing else</b> - no parent, no transform, no chain. The geometry is "
       "already in world coordinates: the wfargo tower's .xmod spans 640.000..800.000 "
       "in x, byte for byte the emin/emax its .hood declares. So there is no hierarchy "
       "to find, and a whole neighbourhood can be moved as one rigid block because the "
       "models already know where they stand relative to each other."),
 ("note", "Read that from the executable, not by guessing - but not from mc2.exe, which "
          "is PACKED: its .text scores 8.00 entropy and carries a .bind section, so no "
          "string is ever referenced and static analysis finds nothing. "
          "<b>testapp.exe is the same build unprotected</b> - same .text and .rdata "
          "sizes, 6.55 entropy - and it still names 142 source files in its asserts: "
          "hood.cpp, citymodel.cpp, instcitymodel.cpp, boundoctree.cpp and so on. The "
          ".hood parser is sub_5248D0 there."),
]),

("Shaders and materials", [
 ("p", "A shader is named by a <font face=Courier>.shadert</font> text file. Sweeping every "
       "<font face=Courier>.pck</font> in the clean assets finds <b>77 distinct names across "
       "11515 references</b> - the most used being "
       "<font face=Courier>garage_lightmap_subtract</font> (990), "
       "<font face=Courier>lit_textured</font> (873), <font face=Courier>chrome</font> (702), "
       "<font face=Courier>carpaint</font> (489) and <font face=Courier>road_reflector</font> "
       "(360)."),

 ("h3", "A registry of 32 slots"),
 ("p", "<font face=Courier>sub_2B4438(index, name)</font> loads the file, allocates a 20-byte "
       "shader object, stores the name at +12 and writes the pointer to "
       "<font face=Courier>0x70FB60 + 4*index</font>. The lookup "
       "<font face=Courier>sub_2B43C0(name)</font> walks that array <b>up to 32</b> comparing "
       "names, so the registry holds 32 shaders. The city registers nine of them, by index, in "
       "<font face=Courier>sub_3B34C0</font>:"),
 ("code", "0 city_facade          3 city_window_cutout   6 doublesided\n"
          "1 city_window          4 city_road            7 double_sided_hdr_object\n"
          "2 city_window_daytime  5 hdr_object           8 doublesided_prop"),

 ("h3", "The bitfield at material+0x04"),
 ("p", "One packed word says which shader a material uses, which pass it belongs to and what "
       "its parameters are. <font face=Courier>sub_2B3908</font> assembles it:"),
 ("table", [
    ["bits", "meaning"],
    ["0 .. 6", "set by the shader setup wrappers"],
    ["7 .. 11", "<b>drawbucket</b> - the render pass"],
    ["15 .. 21", "shader index into the 32-slot registry"],
    ["22 .. 24", "number of parameters"],
    ["25 .. 31", "mask: bit i set means parameter i is a texture, else a float"],
 ], [70, 320]),
 ("p", "Parameters follow at +0x08, one word each. Decoded against the real file the signature "
       "checks out with <b>no failures</b> in 400 sampled objects per class:"),
 ("table", [
    ["vtable", "count", "flags", "shader", "params"],
    ["0x7B2318", "1151", "0x07808010", "1 city_window", "6: two textures then the RGBA"],
    ["0x7B22B8", "434", "0x07020011", "4 city_road", "4: two textures, two floats"],
    ["0x7A1FC8", "305", "0x03428002", "5 hdr_object", "5"],
    ["0x7A1FC8", "207", "0x02430002", "6 doublesided", "1"],
    ["0x7A1EF8", "1961", "0x00000000", "0 city_facade", "0"],
 ], [68, 40, 76, 118, 148]),
 ("note", "<b>The vtable does not identify the shader.</b> 0x7A1FC8 alone carries four "
          "different ones - it groups by parameter layout, and the bitfield is what names the "
          "shader. Any tool that tries to swap a shader by editing the vtable is in the wrong "
          "field; it is +0x04."),

 ("h3", "Drawing, and the material sets"),
 ("p", "The draw call resolves a material per group and filters by pass:"),
 ("code", "for group in 0 .. mesh[0x08]:                       // group count\n"
          "    m = table-&gt;array[ mesh[0x0C] [group] ];          // the u16 per group\n"
          "    if ((m-&gt;flags &gt;&gt; 7) &amp; 0x1F) == drawbucket:\n"
          "        m-&gt;vtable[10](m, mesh, ..., group, ...)"),
 ("p", "The table is not global. It comes from the caller as "
       "<font face=Courier>MapRoot+0x0C + 16 * component[0x0A]</font> - so <b>every component "
       "carries a byte saying which material set it uses</b>. A city ships several complete "
       "sets: 18 in atlanta, detroit and tokyo, 15 in san diego, each an array as long as the "
       "material count (1329, 1154, 1720, 1516)."),
 ("note", "The sets are <b>copies</b>, not variants. Of the 623 materials that appear in two or "
          "more, all 623 have byte-identical material objects, and their texture descriptors "
          "differ only in the u16 at +6 - the reference count, the field the destructor "
          "decrements. Separate refcounts only make sense if each set is its own ownership "
          "unit. They are not regions either: measured against the real instance positions, "
          "every set spreads over the whole city with an RMS radius within 7% of the global "
          "one."),

 ("h3", "The .shadert grammar"),
 ("p", "<font face=Courier>sub_2B06B8</font> is the parser. These are the 87 keywords it "
       "compares, which is the whole language:"),
 ("code", "local  default  PS2  nowarnings  warnings\n"
          "lod          high med low vlow\n"
          "texgroup  texframe  user0\n"
          "texcombine   window modulate decal modulate2x add\n"
          "texgen       passthru reflect distort\n"
          "texsrc    drawbucket\n"
          "cull         none front back\n"
          "lighting     none dir point\n"
          "fogblend  alphablend  smoothshade\n"
          "blendset     overwrite destalphainvdestalpha matte normal add sub lightmap\n"
          "ps2blend     Cs Cd As Ad FIX\n"
          "d3dblend\n"
          "alphafunc    greater greaterequal lessequal always notequal never less equal\n"
          "alpharef  destalphatest  disable zero one\n"
          "depthfunc    closerequal closer always never\n"
          "depthwrite\n"
          "rotate  slides  slidet  scales  scalet\n"
          "colorwrite  failalpha keep  rgbaonly depthonly rgbonly\n"
          "nextpass  nextstage\n"
          "d3dvertexshader  ps2microcode  basecolor"),
 ("p", "<font face=Courier>drawbucket</font> is the name of the bits 7..11 field, which closes "
       "that loop. <font face=Courier>ps2blend Cs Cd As Ad FIX</font> are literally the "
       "operands of the GS ALPHA register, so the format exposes PS2 blending directly. "
       "<font face=Courier>local</font> / <font face=Courier>default</font> / "
       "<font face=Courier>PS2</font> are platform sections and "
       "<font face=Courier>d3dvertexshader</font> / <font face=Courier>ps2microcode</font> pick "
       "the program per platform - one file serves both builds."),

 ("h3", "The city bakes materials, the vehicle does not"),
 ("p", "Searching for the material signature - a vtable followed by a coherent bitfield whose "
       "texture mask matches which parameters are pointers - finds every known class in the "
       "city file and <b>nothing at all</b> in "
       "<font face=Courier>vp_jetta_03.pck</font> or "
       "<font face=Courier>vp_gto_68.pck</font>. The city file is the control that proves the "
       "search works, so the zero is a result."),
 ("table", [
    ["", "city", "vehicle"],
    ["registration", "sub_2B4438, 9 calls, baked index", "sub_2B0388, 50 calls, by name"],
    ["where the name lives", "only in the executable", "also in the .pck, in a name table"],
    ["resolved", "index already in the bitfield", "by name at load, via sub_2B43C0"],
    ["material object", "written into the file", "built in memory by the parser"],
 ], [96, 152, 152]),
 ("p", "So changing a city shader means editing a <b>number</b> - bits 15..21 of the bitfield. "
       "Changing a vehicle shader means editing a <b>string</b>, or the "
       "<font face=Courier>.shadert</font> itself. The 50 runtime calls and the 9 baked ones "
       "both end at the same 32-slot registry."),

 ("h3", "A taillight casing owns three real child shaders"),
 ("p", "The 350Z RAM address <font face=Courier>0x0178C110</font> is not a texture or an "
       "isolated allocation. The parent <font face=Courier>drwShaderTaillightCasing</font> at "
       "0x0178C0C0 points from +0x30 to a three-entry vector at 0x0178C100: "
       "<font face=Courier>CarpaintNoVinyl</font> at 0x0178C110, CarbonFiber at 0x0178C150, "
       "and Chrome at 0x0178C190. The explicit vector establishes ownership; physical "
       "adjacency alone does not."),
 ("code", "drwShaderCarpaintNoVinyl  size 0x34  runtime vtable 0062E910\n"
          "+00..+2F  rmcShaderComplex (inline ShaderPass, name, state mask)\n"
          "+30 u8     CarPaintEnvScale local index\n"
          "+31 u8     CarPaintFresnelMin local index\n"
          "+32 u8     CarPaintFresnelMax local index\n"
          "+33 u8     CarPaintFresnelScale local index"),
 ("p", "Retail <font face=Courier>Load</font> at 0x003B6EF8 resolves those four names. "
       "<font face=Courier>Draw</font> at 0x003B7040 reads the four floats through the stored "
       "indices and uploads one Fresnel vector. On the 350Z the indices are 14,15,16,17, "
       "the type is 0x3E and the state mask is 0xB0A. A HostFS census found <b>66 casing-owned "
       "children</b>; all 66 agree on type and mask, while the indices vary with each group's "
       "local-list order. The two serialized tokens, 0x007A98C0 and 0x007AABC8, are therefore "
       "still module residue rather than class IDs."),
 ("note", "The same factory corrected an old label: runtime vtable "
          "<font face=Courier>0x0062F8E8</font> is selected for "
          "<font face=Courier>licenseplate.shadert</font>, not CarpaintNoVinyl. The HexPat now "
          "opens the complete Casing -&gt; vector -&gt; three child-shader path and recognizes "
          "CarpaintNoVinyl by 0x0062E910."),

 ("h3", "What a shader cannot reach"),
 ("p", "The known PCSX2 patch "
       "<font face=Courier>patch=1,EE,005613CC,word,240C0000</font> forces high quality "
       "reflections, and there is no way to do the same from an asset. The address sits inside "
       "a <b>constructor</b>, where the instruction is a plain "
       "<font face=Courier>li $t4, 1</font> feeding "
       "<font face=Courier>this-&gt;byte_0C = 1</font>. Of the six writes to that field in the "
       "render code only the constructor stores 1; the other five store zero. Nothing in any "
       "file feeds it, and at draw time it is copied into a global that the pipeline reads."),
 ("note", "The grammar has <font face=Courier>texgen reflect</font>, but that governs how a "
          "material <i>samples</i> a reflection map - not how the map is <i>produced</i>. "
          "Those are different layers, which is exactly why shader edits reach one and never "
          "the other."),
]),

("The executable: names", [
 ("p", "The retail ELF ships stripped. The October 2004 alpha does not: it carries "
       "<b>16943 named functions</b>. The two are different builds, so neither addresses nor "
       "bytes line up - every relocation differs. What carries over is <i>identity</i>, and "
       "there are three independent ways to establish it."),
 ("table", [
   ["Pass", "What it uses", "Pairs", "Precision"],
   ["match", "what a function SAYS: the string literals it references, each weighted by rarity", "1224", "98.9%"],
   ["grafo", "the company it keeps: callers and callees already matched, weighted 1/(1+degree)", "3219", "98.2%"],
   ["vizinhos", "where it SITS: a compiler emits a class's methods in source order, so a matched pair starts a run that sizes confirm", "6304", "99.3%"],
  ], [24*mm, 76*mm, 18*mm, 20*mm]),
 ("p", "Precision is measured, not asserted: a blind hold-out hides 25% of the anchors and "
       "checks whether the pass rediscovers the <b>same address</b>. Total applied: "
       "<b>10766 names</b>."),
 ("note", "The third pass cracked the cluster the other two cannot reach. "
          "<font face=Courier>datResource</font> fixups are tiny pointer-relocation routines "
          "with no string literals, and their neighbours are in the same state, so neither text "
          "nor call graph has an anchor. But they sit in a row, and one named neighbour anywhere "
          "in the row is enough. Coverage went from 58 to 262, finally naming "
          "<font face=Courier>mcHood::mcHood(datResource&amp;)</font> and "
          "<font face=Courier>mcCityModel::mcCityModel(datResource&amp;)</font>."),
 ("h3", "Why a run must stop rather than resync"),
 ("p", "A misaligned run would name every function after it wrongly, and a silent shift is the "
       "one failure mode worth paying to avoid. The pass halts at the first size disagreement. "
       "All 5 errors in 730 recoveries were one-slot slides inside rows of SDK stubs with "
       "identical sizes."),
]),

("The executable: time", [
 ("p", "The engine does <b>not</b> run on a fixed timestep, though the code for one is there. "
       "<font face=Courier>datTimeManager::Update</font> reads the EE Count register and "
       "converts with <font face=Courier>cycles x 3.3908421e-9</font>. Of the two modes, the "
       "one selected is <font face=Courier>RealTime(0)</font>: measured time, no cap."),
 ("table", [
   ["Address", "Name", "Role"],
   ["0x433490", "datTimeManager::Update", "reads Count, produces the frame dt"],
   ["0x433A08", "datTimeManager::RealTime(float)", "rate 0 = measured time. THIS is what runs"],
   ["0x433A50", "datTimeManager::FixedFrame(float, uint)", "fixed step: exists, never selected"],
   ["0x618E20", "g_dtQuadro", "the dt the whole game reads (290 references)"],
   ["0x618E24", "raw measured dt", "written, never read: a mirror of the measurement"],
   ["0x618E28", "1/dt", "read by 48 places"],
   ["0x618E54", "UI dt", "what mcFlash::Update reads on the negative-delta path"],
  ], [24*mm, 62*mm, 62*mm]),
 ("p", "<b>There is more than one dt, and they do not always agree.</b> On the loading "
       "screen 0x618E20 and 0x618E54 both sit pinned at exactly 1/60 while 0x618E24 shows "
       "the loop really turning at 120.5 Hz. So a patch that routes something to \"the dt\" "
       "there is not adaptive at all - it is a division by two wearing a disguise. Always "
       "check WHICH global a function loads."),
 ("p", "Measured in savestates: <font face=Courier>dt = 0.0167239</font> at 60 Hz and "
       "<font face=Courier>0.0083009</font> at 120 Hz. The clock is right at any rate, so "
       "anything that scales by dt is already frame-rate independent."),
 ("h3", "The loading screen"),
 ("p", "Two callers on the loading thread pass a hard-coded delta instead of the frame's: "
       "<font face=Courier>mcLoading::UpdateFlashFixo30</font> at 0x1AC2A4 with 1/30, and "
       "<font face=Courier>mcLoadingThread::UpdateFlash</font> at 0x28EE68 with 0.033. Both "
       "are sized for 30 fps, so unlocking the frame rate speeds the animation by the same "
       "factor. Both are built as <font face=Courier>lui</font> + <font face=Courier>ori</font>, "
       "so decrementing the exponent in the lui halves the value exactly - one word each, no "
       "cave needed."),
 ("table", [
   ["", "0x1AC2A4", "0x28EE68"],
   ["original (30 fps)", "3C013D08", "3C013D07"],
   ["/2, for 60 fps", "3C013C88", "3C013C87"],
   ["/4, for 120 fps", "3C013C08", "3C013C07"],
  ], [46*mm, 51*mm, 51*mm]),
 ("p", "The constant has to match the real field rate; there is no way to make this one "
       "adaptive from a patch, because the dt visible at that point is pinned. A payload can "
       "do better, since it can read the raw measurement."),
 ("h3", "60 fps, and what it breaks"),
 ("p", "The vblank handler counts down the fields <font face=Courier>gfxPipeline::BeginFrame"
       "</font> asked for and only then signals the semaphore the game thread waits on. "
       "Removing the still-waiting branch at <font face=Courier>0x5280F0</font> makes it signal "
       "every field: 29.97 to 59.94 Hz, in one word."),
 ("p", "What then runs wrong is whatever does <b>not</b> scale by dt. Two cases were found, "
       "both measured rather than guessed:"),
 ("table", [
   ["System", "The defect", "Fix"],
   ["Flash and loading", "two callers pass a hard-coded 1/30 to mcFlash::Update, which already accepts a NEGATIVE delta meaning use-the-measured-dt", "flip the sign bit of the lui at 0x1AC2A4 and 0x28EE68"],
   ["Pedestrians", "mcCreatureAnimator advances by a rate in FRAMES PER TICK with no dt anywhere; the walk cycle root motion is what moves them", "halve the rate sources, or compute it live from a payload"],
  ], [26*mm, 66*mm, 56*mm]),
 ("p", "The pedestrian numbers, from savestate pairs: 0.037341 units per tick at 30 Hz and "
       "0.032954 at 60 Hz. <b>Per tick essentially unchanged</b>, so it does not scale with dt. "
       "It matches the root advance per frame of "
       "<font face=Courier>mped01_walk01</font>: 1.7850/49 = 0.036429."),
 ("h3", "Telling the city what rate it is running at"),
 ("p", "<font face=Courier>mcGame::ApplyFrameRateConfig</font> passes a literal to "
       "<font face=Courier>mcCity::SetIntendedFrameRate</font> at "
       "<font face=Courier>0x1A70F0</font>, and that number becomes a PER-FRAME budget "
       "inside <font face=Courier>mcPVS</font>: <font face=Courier>60/rate x 17</font> and "
       "<font face=Courier>x 20</font>. The game passes 30 and never revisits it, so with the "
       "vblank patch running at 60 or 120 the city is still spending a budget sized for a "
       "33 ms frame inside an 8 ms one - the wrong value actively helps CAUSE the drops."),
 ("table", [
   ["rate", "word at 0x1A70F0", "thresholds"],
   ["30", "2405001E", "34.0 / 40.0  (original)"],
   ["60", "2405003C", "17.0 / 20.0"],
   ["90", "2405005A", "11.3 / 13.3"],
   ["120", "24050078", "8.5 / 10.0"],
  ], [20*mm, 44*mm, 60*mm]),
 ("p", "The adaptive machinery already exists and was simply calibrated for the wrong number: "
       "<font face=Courier>mcCity::IsFrameRateDying()</font> is consulted by "
       "<font face=Courier>mcPropType::AdjustPropEmissionCount</font> and "
       "<font face=Courier>mcPickUpManager::Draw</font> to shed load."),
 ("h3", "Why an unlocked frame rate is not reachable from inside"),
 ("p", "The game's only clock is the EE <font face=Courier>Count</font> register, which counts "
       "EMULATED time. PCSX2 paces emulated time to its NTSC setting, so a host that cannot "
       "keep up leaves emulated time behind real time - and that IS the slow motion. Nothing "
       "inside the PS2 can see past it. The patched game is already rate independent, proven "
       "at 120.47 Hz; what is locked is the emulator's pacing, not the game."),
 ("note", "The rate is NOT set where it is initialised. The "
          "<font face=Courier>mcCreatureAnimator</font> constructor writes 1.0 and "
          "<font face=Courier>SetPlayBackRate</font> overwrites it right after with what "
          "<font face=Courier>mcCreature::Init</font> computed as "
          "<font face=Courier>1.10 + rand * 0.35</font>. Patching the constructor does nothing, "
          "confirmed by reading the live object, which still held 1.0."),
]),

("Injecting code", [
 ("p", "A pnach can only change constants. Anything depending on a value that exists only at "
       "run time needs real code. The route that works needs no ELF edit: the pnach carries a "
       "<b>61-instruction loader</b> and everything else lives in a loose file the game reads at "
       "run time through its own Stream API."),
 ("table", [
   ["Address", "What", "Applied as"],
   ["0x61C858", "loader, 244 bytes", "patch=2"],
   ["0x61C960", "the file path", "patch=2"],
   ["0x61C980", "state and trace, 16 bytes", "patch=0 ONLY"],
   ["0x61D000", "payload, up to 10240 bytes", "read from the file"],
  ], [24*mm, 62*mm, 40*mm]),
 ("p", "The cave sits <b>inside the ELF image</b>: a 12288-byte run of zeros in a PROGBITS "
       "section, confirmed still untouched in a gameplay savestate. Free RAM outside the image "
       "does not work, because the boot zeroes those ranges after "
       "<font face=Courier>patch=0</font> writes; the hook then jumps into NOPs and falls into "
       "garbage."),
 ("p", "The hook retargets the four <font face=Courier>jal datTimeManager::Update</font> in "
       "<font face=Courier>mcGame</font>. The loader calls the real timer first, so the game is "
       "unchanged, and the payload gets a per-frame tick. Hooking all four matters: the in-game "
       "one alone would put the file open at the end of city loading, with the streamer busy."),
 ("note", "The state word must be <font face=Courier>patch=0</font>. With "
          "<font face=Courier>patch=1</font> or <font face=Courier>2</font> the pnach would zero "
          "the flag every frame and the payload would reload forever."),
 ("h3", "Three rules for a payload"),
 ("p", "<b>Idempotent</b>: more than one hook fires per frame, so assigning a value is safe and "
       "accumulating counts several times. <b>Position dependent</b>: the generated code "
       "references its own constants by absolute address, so the linker script must use the "
       "cave address. <b>No runtime</b>: no new, delete, stdlib or exceptions, unless calling "
       "the game's own."),
 ("h3", "Three wrappings, one loader"),
 ("p", "The same 61 instructions reach three targets. What differs is only how the payload "
       "travels, which is what makes comparing emulator against hardware worth anything: a "
       "behaviour difference cannot be blamed on the injected code differing."),
 ("table", [
   ["route", "payload", "needs"],
   ["pnach", "loose file read at run time through Stream::Open", "PCSX2 + HostFS"],
   ["pnach --embed", "carried inside the patch file", "PCSX2, any boot medium"],
   ["cht --embed", "same, in OPL's raw code format", "a real PS2 with OPL"],
  ], [26*mm, 74*mm, 46*mm]),
 ("p", "The <font face=Courier>cht</font> route REQUIRES the embedded form, by construction: "
       "OPL reapplies every code every frame and has no equivalent of a write-once patch. "
       "Embedded that is harmless, since loader, payload and state are always the same values; "
       "with an external file the state would be reset to zero every frame and the payload "
       "would reload forever."),
 ("p", "Result, measured at 120.47 Hz with a payload computing "
       "<font face=Courier>rate = base * dt * 30</font>: <b>14 of 14 pedestrians</b> at exactly "
       "the expected ratio of 0.2490, each one's own variation preserved. A constant patch would "
       "have given 0.5 there, twice too fast."),
]),

("Turning patches into code", [
 ("p", "A pnach and a payload are not rivals. Anything a payload can do to memory, a pnach "
       "line can do too - what a pnach cannot do is compute. So the useful split is: let the "
       "payload carry BOTH, and generate the constant part from the pnach files rather than "
       "copying addresses by hand."),
 ("p", "<font face=Courier>mc3_pnach.py --to-cpp</font> emits a table of every group, every "
       "write and its width, with a compile-time error for any two groups that write the same "
       "address. Groups default to off, so regenerating never disturbs a choice. The result: "
       "one binary, the same 50 writes under PCSX2 and on hardware."),
 ("p", "Two things the emulator lets you get away with and a console does not:"),
 ("p", "<b>The instruction cache.</b> These addresses are code. PCSX2's recompiler sees the "
       "store and invalidates; a real R5900 keeps running the old words out of its 16 KB "
       "I-cache. The payload issues <font face=Courier>FlushCache(0)</font> then "
       "<font face=Courier>FlushCache(2)</font> - EE syscall 100 - and only on the frames "
       "where a value actually changed, which is normally just the first."),
 ("p", "<b>Write-once.</b> OPL reapplies every code every frame and has no equivalent of "
       "patch=0. The way around it is not to embed everything but to emit no code for the "
       "state word at all: boot zeroes EE RAM, so the flag already starts at 0 and the loader "
       "is the only thing that ever sets it. That turns 716 codes into 71."),
 ("p", "Two things stay in the pnach by construction: the HostFS bootstrap, which runs before "
       "the game opens any file - and the payload is a file - and the loader itself, which "
       "cannot load itself."),
]),

("Booting the game yourself", [
 ("p", "A patch file can only write memory, and only where the emulator lets it. Both "
       "limits bite: OPL reapplies every code every frame with no equivalent of "
       "patch=0, and PCSX2 matches patch files by executable checksum. The way past "
       "both is to stop being a patch and become the thing that boots the game."),
 ("p", "<font face=Courier>mc3boot</font> runs INSTEAD of the game: it reads the mod "
       "files, reads the game image into place, writes what it wants, and hands over. "
       "The order is the whole point - by the time the game runs its first instruction "
       "everything is already in RAM. At the end of the game's own crt0 there is no "
       "file system yet, so an external mod file cannot be read there; here it can, "
       "because we are still a normal program with the IOP up."),
 ("p", "This game makes it easy. One <font face=Courier>PT_LOAD</font>, so loading is a "
       "copy and a memset - no relocation, no symbol table:"),
 ("table", [
   ["field", "value"],
   ["entry", "0x001A0008"],
   ["segment", "file +0x80 -> VA 0x001A0000"],
   ["filesz / memsz", "0x004D7074 / 0x00575D3C"],
   ["loader lives at", "0x01000000, above the game, freed to the heap after the jump"],
  ], [40*mm, 108*mm]),
 ("p", "Hand-over follows ps2sdk's own elf-loader: drop RPC, "
       "<font face=Courier>FlushCache(0)</font>, <font face=Courier>FlushCache(2)</font>, "
       "<font face=Courier>ExecPS2</font>. No IOP reset - the reference does not do one, "
       "and the game reboots the IOP itself anyway."),
 ("h3", "The bootstrap that could finally move"),
 ("p", "The HostFS redirection was documented here as unmovable: as a patch file it had "
       "to run before the game opened any file, and the mod payload IS a file. The chain "
       "loader dissolves that - it reads with its own file system and writes into the "
       "image before the game starts, so those are ordinary memory writes. It is applied "
       "only when the game itself came from <font face=Courier>host0:</font>, decided at "
       "run time from the path that actually opened."),
 ("h3", "Relocatable modules"),
 ("p", "A mod that must own a fixed address cannot coexist with another mod that wants "
       "the same one, and requiring a compiler to add one rules out most people. So the "
       "linker keeps its relocations and the loader replays them wherever it puts the "
       "module. Only four types occur in practice - R_MIPS_32, HI16, LO16, 26 - because "
       "the modules are freestanding and never call outside themselves."),
 ("p", "HI16/LO16 is the pair worth care: the value is split across two instructions and "
       "the low half is sign extended, so the high half carries a correction. That pairing "
       "is done at BUILD time, where it can be checked; the runtime only receives \"these "
       "two instructions form one address, add the delta\". The check that makes it "
       "trustworthy is that relocating a module to the original address reproduces the "
       "directly-linked binary byte for byte."),
 ("p", "The in-image loader always jumps to one fixed address, so that address holds a "
       "dispatcher walking a null-terminated table of entry points. One jump, any number "
       "of mods, and the older patch-file routes keep working unchanged."),
]),

("Toolkit", [
 ("p", "Everything lives in <font face=Courier>tools/</font>, plain Python 3 "
       "with no required dependency (Pillow only accelerates palette work). Start with "
       "<font face=Courier>mc3_hub.py</font>, which lists the tools and launches them."),
 ("table", [
    ["file", "what it does"],
    ["mc3_hub.py", "front door: lists every tool and opens it"],
    ["mc3_pck_manager.py", "main GUI: meshes, bones, structures, performance, textures, shaders"],
    ["mc3_traffic_gui.py", "separate GUI for *_traffic.pck: meshes, textures, bones"],
    ["mc3_pck_embed.py", "container format, embed/remove, skeleton, vehicle structs"],
    ["mc3_mesh_convert.py", "converter: pck / psppck / xbck / .mesh / obj"],
    ["mc3_tex.py", "textures: .tex, flash images, vehicle atlases, swizzle, PNG"],
    ["mc3_pointers.py", "PointerGraph: reverse lookup, walk to root, size a block"],
    ["mc3_perf.py", "physics and performance values"],
    ["mc3_ida.py", "drives IDA Pro headless against the ELF: decompile, disasm, xrefs"],
    ["mc3_bound.py", "collision and road trees from *_bnd.pck, exports OBJ"],
    ["mc3_view.py", "3D viewer, flipped faces in red"],
    ["mc3_shadinggroup.py", "index -> shader table of a vehicle"],
    ["mc3_audio_studio.py", "audio: curve editing, .bnk mux/demux, audible preview"],
    ["mc3_traffic.py", "traffic container backend"],
    ["mc3_symbols.py", "carries the alpha build names to retail: 10946 names, four passes"],
    ["mc3_elfpatch.py", "verified word patches into the ELF, PCSX2 CRC preserved"],
    ["mc3_pnach.py", "audits the pnach set; --to-cpp turns the groups into a payload table"],
    ["mc3_selftest.py", "replicas of patched functions, tested against savestate numbers"],
    ["modloader/", "runs your own C++ inside the game; chain loader, .mod modules, .ini"],
    ["mc3_city_build.py", "builds a city .pck from scratch; --enxerta grafts one generated piece"],
    ["mc3_place_sim.py", "runs the loader Place walk on the PC: says what would hang, before booting"],
    ["mc3_city_strip.py", "empties a city, keeping what matches a name or a radius"],
    ["mc3_hood_fill.py", "replaces every unique mesh of one hood; ports a whole MC2 neighbourhood"],
    ["mc3_saida.py", "one place for generated files: everything lands in output/"],
    ["mc3_docs.py", "generates this document"],
  ], [45*mm, 93*mm]),
]),

("Loading a city: the Place walk", [
 ("p", "A <font face=Courier>.pck</font> is a memory image, but loading it is not "
       "one blanket relocation. Each class has a constructor taking "
       "<font face=Courier>datResource&amp;</font> that fixes the pointers IT declares, in "
       "the order it declares them, and calls the constructor of whatever it owns. "
       "<font face=Courier>mcCity::Place</font> only calls "
       "<font face=Courier>mcCity::mcCity(datResource&amp;)</font> at 0x259160, and that "
       "function is the whole reader."),
 ("p", "<b>The relocation delta is <font face=Courier>*(datResource+4)</font></b>. Every "
       "pointer fix in the chain is that one addition, and every field is guarded by "
       "<font face=Courier>if (ptr)</font> before being followed."),
 ("table", [
    ["MapRoot field", "what it holds", "who walks it"],
    ["+0x08 / +0x0C", "shader group descriptors, 16 bytes each", "rmcShaderGroup::rmcShaderGroup"],
    ["+0x10", "another shader array", "same"],
    ["+0x14 / +0x18", "hood table, 20 bytes per entry", "mcHood::mcHood"],
    ["+0x1C", "flat index: every component and instance", "0x25B9D8"],
    ["+0x2C / +0x30", "two 256-entry RGBA palettes (live / source)", "ChangePaletteSaturation"],
    ["+0x190 / +0x194 / +0x198", "cullable classes (city model, instance)", "mcCityModelClass etc."],
    ["+0x228", "texture proxy", "rmcTextureProxyPS2"],
    ["+0x268", "sizes the two scratch arrays of +0x1C", "read, not walked"],
  ], [40*mm, 55*mm, 43*mm]),
 ("p", "<b>The MapRoot is 0x290 bytes, not 0x80.</b> The constructor reaches +0x228 and "
       "loops over twenty pointers from +0x1D8. Allocating less leaves those fields "
       "sitting on whatever was appended next, and the loader reads geometry as a pointer."),
 ("p", "<b>The infinite loop.</b> "
       "<font face=Courier>rmcShaderFactoryStandard::ResourcePageIn</font> (0x2AF9E8) walks "
       "the shader array and dispatches on <font face=Courier>*(obj+4) &amp; 0x7F</font>: 0 is "
       "rmcShaderBasic, 1 rmcShaderComplex, 2 rmcShaderInstance. Anything else falls into "
       "loc_2AFA88, which is five NOPs and a branch to itself - a stripped assert. That is "
       "what 'loading forever' actually is, and it is why a wrong byte gives no message."),
 ("p", "The walk has <b>no already-placed guard</b>: the handler runs once per SLOT. In the "
       "retail file every object appears in exactly one slot (4117 objects, 4117 slots, "
       "19817 nulls in atlanta). Pointing many slots at one object makes its handler re-add "
       "the delta to its internal pointers once per slot."),
 ("p", "<b>Vtables in the file are tokens, not addresses.</b> 0x007A1E18 becomes 0x00626D98 "
       "at load; 0x0079FF88 becomes 0x00625098. The game translates them by walking lists, so "
       "an object YOU create is never translated and must be written with the final address. "
       "Objects the constructor stamps itself (MapRoot, shader, group descriptor) are exempt - "
       "whatever the file holds is overwritten."),
 ("p", "<b>'Empty' has two shapes.</b> A field dereferenced without a guard needs a valid "
       "object with a zero count; a field walked by <font face=Courier>while (ptr)</font> needs "
       "a plain NULL. CullableTypeFixup (0x24AE58) is a linked-list walk - giving it a zeroed "
       "block instead of NULL runs a constructor once over zeros."),
 ("p", "<font face=Courier>mc3_place_sim.py</font> replays all of this on the PC and reports "
       "what the game would hit, which turns a console boot into a command. Its "
       "<font face=Courier>--cobertura</font> mode also says which structures it does NOT "
       "verify - 5 of the 14 the constructor touches are modelled today."),
]),

("The text mesh reader, and what it opens", [
 ("p", "Everything else in this document is about binary containers, because that is "
       "what the game ships. It is not what the game can only read. "
       "<font face=Courier>rmcModel::Create(const char* name, int)</font> at 0x2A8CA0 "
       "calls <font face=Courier>strrchr(name, '.')</font> and dispatches on the "
       "extension:"),
 ("code", "  .mod            -> mshMesh::LoadMod            (text)\n"
          "  anything else   -> SerializeFromFile&lt;mshMesh&gt;(name, m, \"mesh\")"),
 ("p", "The text side is <font face=Courier>gfxModelBase::LoadMod</font> (0x1EBFC0) and "
       "<font face=Courier>gfxGeometry::LoadMod</font> (0x1EEC50). This is not dead "
       "code left in the build: <font face=Courier>ASSETS/model/</font> holds 39 "
       "<font face=Courier>.mod</font> files the retail game loads &mdash; the "
       "helicopter, the checkpoint flag, the debris chunks, the HUD arrows."),
 ("h3", "Midnight Club 2 writes the same dialect"),
 ("p", "MC2's <font face=Courier>.xmod</font> is <font face=Courier>version: 1.10</font> "
       "with the same eleven header counts in the same order, the same data lines and "
       "the same <font face=Courier>mtl</font> block. The geometry does not need "
       "converting; it needs renaming. Measured with "
       "<font face=Courier>mc2_port.py check</font>, which applies the grammar read "
       "out of the ELF:"),
 ("table", [
    ["corpus", "files", "pass"],
    ["MC3 retail ASSETS/model/*.mod", "39", "39"],
    ["MC2 all three cities, *.xmod", "9902", "9902"],
  ], [80*mm, 28*mm, 28*mm]),
 ("p", "One invariant is easy to get wrong and holds in both games: the header's "
       "<font face=Courier>primitives:</font> is the <b>sum of the per-material "
       "<font face=Courier>primitives:</font></b>, not the number of "
       "<font face=Courier>tri</font>/<font face=Courier>str</font>/"
       "<font face=Courier>stp</font> lines. heli.mod declares 578 and has 138 such "
       "lines; its four materials declare 4 + 550 + 6 + 18."),
 ("h3", "Textures arrive by name, and a miss is not fatal"),
 ("p", "The material's <font face=Courier>texture: 0 \"NAME\"</font> becomes a shader "
       "string parameter <font face=Courier>$tex0</font>, defaulting to "
       "<font face=Courier>\"none\"</font>. Resolution walks by name, never by path:"),
 ("code", "mcCityTextureFactory::Create              0x596278\n"
          "  a name holding \"_sky_\" that does not match the current\n"
          "  time-of-day + weather pair is replaced by \"none\"\n"
          "rmcTextureFactoryPS2::Create             0x2B9BD8\n"
          "  rmcTextureFactory::AttachToExistingTexture   0x2B67D8\n"
          "    \"none\"    -> the global null texture\n"
          "    HashTable unk_6BE790  by name\n"
          "    HashTable unk_6BE778  by name (dictionary-backed)\n"
          "  no hit -> builds an EMPTY rmcTexturePS2, caches it, carries on"),
 ("note", "A texture nothing answers for does not hang the loader and does not crash "
          "the draw. The mesh appears untextured. That makes geometry testable before "
          "a single texture has been converted."),
 ("p", "The injection point is "
       "<font face=Courier>rmcTextureFactory::RegisterTextureReference(name, tex)</font> "
       "at 0x2B66F0, which inserts straight into that by-name table. Supplying files "
       "would not work anyway: 12 of the 19 names the retail "
       "<font face=Courier>.mod</font> files ask for do not exist as "
       "<font face=Courier>.tex</font> on disc &mdash; they come from a mounted "
       "dictionary."),
 ("p", "Inside a <font face=Courier>.pck</font> the same texture is reached by index "
       "instead. <font face=Courier>rmcDictionaryReference</font>, vtable 0x626FC8, is "
       "20 bytes carrying a u16 dictionary index at +0x10 and a u16 texture index at "
       "+0x12; <font face=Courier>LookupDictionaryReference</font> (0x2B5E70) is just "
       "<font face=Courier>dict[d].textures[i]</font>, with the array of "
       "<font face=Courier>char*</font> names at "
       "<font face=Courier>unk_6BE6F8 + 12*d + 4</font> and the count at +8."),
 ("h3", "Drawing one, and the ceiling on the instanced path"),
 ("p", "A model from <font face=Courier>gfxGetModel</font> is self contained: it carries "
       "its own shader array, so no city shader group and no "
       "<font face=Courier>.pck</font> are involved. The recipe is the tail of "
       "<font face=Courier>hudArrow::Display</font>:"),
 ("code", "gfxModel* m = gfxGetModel(name, 0, flags);      // 0x1ED340\n"
          "gfxState::SetWorld(matrix34);                   // 0x2ADB48\n"
          "gfxModel::Draw(m, *(gfxShader***)(m + 4), 0);   // 0x1EB998"),
 ("p", "The instanced debris path is a different animal and it has a hard ceiling. "
       "<font face=Courier>mcInstBucket::Draw</font> (0x1C0178) uploads the model to VU1 "
       "address 0x6F and 127 instance transforms to 0x300, then runs microprogram 428787. "
       "That program writes its GS packet into one of two output buffers, at VU 328 and "
       "547 &mdash; <b>219 quadwords each</b> &mdash; and the loop emits <b>three "
       "quadwords per vertex</b> (the parameter word the EE writes is a GIFtag whose "
       "REGS field is 0x512: ST, RGBAQ, XYZ2, NREG=3). So:"),
 ("code", "1 + 3*N <= 219   ->   N <= 72 adjuncts"),
 ("note", "Measured on hardware: three models of 24 adjuncts run; 100/78/24 and 100/24/36 "
          "freeze after about four collisions, with texture corruption alongside &mdash; "
          "the overflow lands in the second buffer and the instance array. The retail "
          "chunks are 12, 27 and 18. A looser second limit exists at 127 (the VIF UNPACK "
          "count is truncated to 8 bits while the DMA tag keeps 16), but 72 binds first. "
          "Neither applies to the ordinary model path above."),
 ("h3", "What this does not solve"),
 ("p", "Placement. A shared mesh format says nothing about where a piece goes, and the "
       "obvious shortcut is closed: <b>the text "
       "<font face=Courier>.lvl</font>/<font face=Courier>.hood</font> readers are not in "
       "the retail build</b>. The whole MC2 grammar sits in the string pool "
       "(<font face=Courier>num_unique_components:</font>, "
       "<font face=Courier>instance_component</font>, <font face=Courier>emin</font>, "
       "<font face=Courier>owner:</font> ... at 0x641E3F&ndash;0x641EB1), but a scan of "
       "every instruction that could form those addresses finds <b>none</b> &mdash; while "
       "the same scan finds the live <font face=Courier>.mod</font> tokens beside them "
       "(<font face=Courier>verts:</font> twice, once inside "
       "<font face=Courier>gfxModelBase::LoadMod</font>). Those readers were compiled out."),
 ("note", "A string in the pool proves the code once existed, not that it still runs. The "
          "cheap test is to look for the instruction that loads its address &mdash; and to "
          "validate the test against a string known to be live before trusting a negative."),
 ("p", "So placement has to come from the binary city <font face=Courier>.pck</font> or "
       "from a consumer reading a table of its own. "
       "<font face=Courier>mc2_port.py cidade</font> exports the latter: for Los Angeles, "
       "729 meshes, one 4.2 MB text <font face=Courier>otgrid</font> holding the whole "
       "city's collision, 512 texture names, and 8809 placements &mdash; 298 components "
       "and 8511 instances, with the AABB of each, because culling 8809 draws is a "
       "requirement and not an optimisation. Components are already in world coordinates: "
       "their AABB equals the mesh's own bounding box to the last decimal."),
]),

("Open questions", [
 ("p", "Stated plainly so nobody re-derives a dead end:"),
 ("table", [
    ["topic", "status"],
    ["PSP textures", "not in the .psppck. They live in the .pspppf packs (magic 'pf05' + entry table), a separate format not yet reversed."],
    ["Xbox textures", "reported to be DXT5. Neither a 'DXT' string nor an obvious block was found in _g.xbck; .xbpf / .xbpmap in the tree are the likely containers."],
    ["Xbox shaders", "the vehicle .xbck simply does not store the names (zero .shadert). They must come from a shared file or by ID."],
    ["Xbox vertex +0x10", "perpendicular to the normal on 80 of 116 hood vertices, so probably a tangent, but not decoded. Passed through verbatim on a round-trip."],
    ["Xbox set constants", "0x00805EC1, 0x59, 0x4A02 in the 24-byte set are copied literally; their meaning is unknown."],
    ["Writing .xbck", "single meshes only, and NEVER TESTED IN GAME. It is structurally valid and passes an independent parser, but nobody has loaded one on hardware."],
    ["Traffic extract", "the material table can live in the container's shared area, outside the footprint, so an extracted piece may come back as a single group."],
    ["MC2 placement", "the mesh format is shared, the placement is not. .lvl/.hood instances have no MC3 equivalent yet, and mcPropBase::Init wants a registered pdef resource the game ships none of."],
  ], [38*mm, 100*mm]),
]),
]


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------
def build_styles():
    ss = getSampleStyleSheet()
    s = {}
    s["title"] = ParagraphStyle("t", parent=ss["Title"], fontName="Helvetica-Bold",
                                fontSize=23, leading=28, textColor=INK, spaceAfter=6)
    s["sub"] = ParagraphStyle("s", parent=ss["Normal"], fontName="Helvetica",
                              fontSize=11.5, leading=16, textColor=MUTED, alignment=TA_LEFT)
    s["h2"] = ParagraphStyle("h2", parent=ss["Heading1"], fontName="Helvetica-Bold",
                             fontSize=15, leading=19, textColor=ACCENT,
                             spaceBefore=16, spaceAfter=7)
    s["h3"] = ParagraphStyle("h3", parent=ss["Heading2"], fontName="Helvetica-Bold",
                             fontSize=11.5, leading=15, textColor=INK,
                             spaceBefore=11, spaceAfter=4)
    s["p"] = ParagraphStyle("p", parent=ss["BodyText"], fontName="Helvetica",
                            fontSize=9.7, leading=14.2, textColor=INK, spaceAfter=7)
    s["code"] = ParagraphStyle("c", parent=ss["Code"], fontName="Courier",
                               fontSize=8.1, leading=10.6, textColor=INK,
                               backColor=CODEBG, borderPadding=6,
                               leftIndent=2, spaceBefore=3, spaceAfter=9)
    s["note"] = ParagraphStyle("n", parent=ss["BodyText"], fontName="Helvetica",
                               fontSize=9.4, leading=13.4, textColor=WARN,
                               leftIndent=8, borderPadding=6, spaceBefore=3, spaceAfter=9)
    s["cell"] = ParagraphStyle("cell", parent=ss["BodyText"], fontName="Helvetica",
                               fontSize=8.4, leading=11.2, textColor=INK, spaceAfter=0)
    s["cellh"] = ParagraphStyle("cellh", parent=s["cell"], fontName="Helvetica-Bold")
    return s


def make_table(rows, widths, s):
    data = [[Paragraph(str(c), s["cellh"] if i == 0 else s["cell"]) for c in row]
            for i, row in enumerate(rows)]
    # Long tables are allowed to break across pages, repeating the header row;
    # forcing them whole leaves a page half empty.
    t = Table(data, colWidths=widths, hAlign="LEFT", repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, ACCENT),
        ("GRID", (0, 0), (-1, -1), 0.3, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafbfc")]),
    ]))
    return t


def build(out_path):
    s = build_styles()
    today = datetime.date.today().isoformat()

    doc = BaseDocTemplate(out_path, pagesize=A4,
                          leftMargin=22*mm, rightMargin=22*mm,
                          topMargin=20*mm, bottomMargin=18*mm,
                          title=TITLE, author="MC3 reverse engineering notes")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="n")

    def decorate(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(doc.leftMargin, 11*mm, "%s  -  v%s, %s" % (TITLE, VERSION, today))
        canvas.drawRightString(A4[0] - doc.rightMargin, 11*mm, "%d" % canvas.getPageNumber())
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.4)
        canvas.line(doc.leftMargin, 14*mm, A4[0] - doc.rightMargin, 14*mm)
        canvas.restoreState()

    doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=decorate)])

    story = [Spacer(1, 26*mm),
             Paragraph(TITLE, s["title"]),
             Paragraph(SUBTITLE, s["sub"]),
             Spacer(1, 6*mm)]
    story.append(Paragraph(
        "Version %s &nbsp;&nbsp;|&nbsp;&nbsp; %s" % (VERSION, today), s["sub"]))
    story.append(Spacer(1, 10*mm))
    story.append(Paragraph("Contents", s["h3"]))
    # Keep the contents on the title page.  A single 25-entry column used to
    # spill only three entries onto an almost-empty second page.
    half = (len(CONTENT) + 1) // 2
    toc = []
    for row in range(half):
        cells = []
        for index in (row, row + half):
            if index < len(CONTENT):
                cells.append(Paragraph(
                    "%d. &nbsp;%s" % (index + 1, CONTENT[index][0]), s["p"]))
            else:
                cells.append("")
        toc.append(cells)
    contents_table = Table(toc, colWidths=[doc.width / 2.0, doc.width / 2.0],
                           hAlign="LEFT")
    contents_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(contents_table)
    story.append(PageBreak())

    for i, (head, blocks) in enumerate(CONTENT, 1):
        story.append(Paragraph("%d. %s" % (i, head), s["h2"]))
        for kind, *rest in blocks:
            if kind == "p":
                story.append(Paragraph(rest[0], s["p"]))
            elif kind == "h3":
                story.append(Paragraph(rest[0], s["h3"]))
            elif kind == "code":
                txt = rest[0].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                txt = txt.replace("\n", "<br/>").replace(" ", "&nbsp;")
                story.append(Paragraph(txt, s["code"]))
            elif kind == "note":
                story.append(Paragraph(rest[0], s["note"]))
            elif kind == "table":
                t = make_table(rest[0], rest[1], s)
                story.append(t if len(rest[0]) > 6 else KeepTogether(t))
                story.append(Spacer(1, 7))
    doc.build(story)
    return out_path


# The document is a deliverable, not a build artefact, so it lives in
# Documentation/ rather than with the throwaway output.
OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Documentation")


def main():
    out = None
    a = sys.argv[1:]
    if "-o" in a:
        out = a[a.index("-o") + 1]
    elif a:
        out = a[0]
    if out is None:
        if not os.path.isdir(OUTDIR):
            os.makedirs(OUTDIR)
        out = os.path.join(OUTDIR, "MC3_Format_Documentation.pdf")
    p = build(out)
    print("wrote %s (%d bytes)" % (p, os.path.getsize(p)))


if __name__ == "__main__":
    main()
