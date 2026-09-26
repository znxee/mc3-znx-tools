"""
mc3_pck_manager.py - GUI to manage the meshes of a vehicle container
(PS2 vp_*.pck, MC3 PSP and MC:LA vp_*.psppck, Xbox vp_*.xbck).

Drag a container onto this .py, or run it and use "Open".

What you can do:
  * see every slot of each LOD: index, name, ID, whether the mesh is EMBEDDED in
    the container or comes from an external .pck/.psppck/.xbck, and its size;
  * EMBED an external mesh into a slot (appends at the end and repoints the slot
    -- nothing else in the file moves);
  * EMBED ALL slots that have a matching file in the folder;
  * REMOVE embedded meshes to shrink the file (relocates the pointers and zeroes
    the slot, so the game goes back to loading the external file);
  * EDIT the slot ID. The ID indexes the .skel list (referenced from the table
    header itself: +0x14 items, +0x18 tag EE020100, +0x1C pointer). ID 0 is the
    valid root node, ``vroot``; external loading is controlled by name + a zero
    mesh pointer, not by a nonzero ID.

All the format logic comes from mc3_pck_embed.py (same directory).
"""
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import importlib.util

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("mc3_pck_embed", os.path.join(_HERE, "mc3_pck_embed.py"))
mc3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mc3)

_pspec = importlib.util.spec_from_file_location("mc3_perf", os.path.join(_HERE, "mc3_perf.py"))
perf = importlib.util.module_from_spec(_pspec)
_pspec.loader.exec_module(perf)

_tspec = importlib.util.spec_from_file_location("mc3_tex", os.path.join(_HERE, "mc3_tex.py"))
tex = importlib.util.module_from_spec(_tspec)
# register before executing: the @dataclass in mc3_tex resolves annotations
# through sys.modules[cls.__module__], which would be None without this
sys.modules["mc3_tex"] = tex
_tspec.loader.exec_module(tex)


def _optional(name):
    """Load a sibling module, returning None if it is missing or fails to load.
    The viewer and the shader table are extras: the GUI must still open without
    them (mc3_view needs matplotlib for the native window, for instance)."""
    path = os.path.join(_HERE, name + ".py")
    if not os.path.isfile(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


view = _optional("mc3_view")
shading = _optional("mc3_shadinggroup")


def _ext_order(path):
    """Container extensions to try when looking for an external mesh, own
    format first (PS2 .pck, PSP .psppck, Xbox .xbck)."""
    own = os.path.splitext(path)[1].lower()
    order = [".pck", ".psppck", ".xbck"]
    if own in order:
        order.remove(own)
        order.insert(0, own)
    return tuple(order)


def human(n):
    if n < 1024:
        return "%d B" % n
    if n < 1024*1024:
        return "%.1f KB" % (n/1024.0)
    return "%.2f MB" % (n/(1024.0*1024))



# ---------------------------------------------------------------------------
# Customisation categories (the "Parts" tab)
#
# The car PCK has one big object at root+0x08 (the u32 at file 0x88). Inside it
# sits a table of skeleton-node IDs split into 14 categories, plus a parallel
# count per category. Thirteen categories have 24 slots; `body` has exactly 8.
# 0xFFFFFFFF marks an empty slot, which is why a car with no customisation is
# almost entirely 0xFFFFFFFF without anything breaking.
#
# The resolver is sub_302D10 (called from sub_2E2298). Per category it reads
# the count, walks that many entries, and for each ID `i` fetches
# skeleton[i].name (the node table at obj+0xDC, stride 68, name at +0x0C) and
# hands it to a category-specific register function (sub_4B6118, sub_4B6158,
# ...). So the ID is NOT a part - it is a BONE INDEX, and each category is
# registered on its own, which is what makes the options mutually exclusive:
# the game picks one entry per category.
#
# THE INVARIANT: the loop reads `count` entries regardless of how many slots are
# actually filled. Add an ID without raising the count and it never shows; raise
# it too far and the game indexes the node table with 0xFFFFFFFF. Across all 117
# canonical packages, count == filled slots in every one of the 1638 lists.
PARTS_PTR_AT = 0x88          # file offset of the pointer to the object
PARTS_EMPTY = 0xFFFFFFFF
PARTS_SLICES = (0x2AFC, 0x2B5C, 0x2BBC, 0x2C1C, 0x2C7C, 0x2CDC,
                0x2DD4, 0x2E34, 0x2F54, 0x2FB4, 0x2FD4, 0x3034,
                0x3094, 0x30F4)
PARTS_COUNTS = (0x3174, 0x3178, 0x317C, 0x3180, 0x3184, 0x3188,
                0x318C, 0x3190, 0x3198, 0x31A0, 0x31A4, 0x31A8,
                0x31AC, 0x31B0)
PARTS_WIDTHS = (24, 24, 24, 24, 24, 24, 24, 24, 24, 8, 24, 24,
                24, 24)
# Names are exact: rmcCarModelType::AddNamesToPartDatabase dispatches each list
# to the correspondingly named mcCarPartDb::Add*Name function.
PARTS_NAMES = (
    "front bumper",
    "rear bumper",
    "side skirts",
    "spoiler",
    "hood",
    "scoop / blower",
    "headlights",
    "taillights",
    "car-file exhaust",
    "body / chop top",
    "brush guard",
    "front grille / louvers",
    "one-shot kit / mud flaps",
    "wheelie bar",
)


def parts_base(pck):
    """File offset of the object that owns the category tables, or None."""
    try:
        o = pck.F(pck.U(PARTS_PTR_AT))
    except Exception:
        return None
    return o if pck.inside(o) and pck.inside(o + 0x3200) else None


def parts_read(pck):
    """[(category, count, [(slot, id, bone_name), ...])] for every category."""
    base = parts_base(pck)
    if base is None:
        return []
    names = {}
    try:
        for node in pck.skel_nodes():
            names[node[0]] = node[1]          # index -> bone name
    except Exception:
        pass
    out = []
    for k, (aoff, coff, width) in enumerate(zip(PARTS_SLICES, PARTS_COUNTS,
                                                 PARTS_WIDTHS)):
        cnt = pck.U(base + coff)
        rows = []
        for i in range(width):
            v = pck.U(base + aoff + 4 * i)
            if v != PARTS_EMPTY:
                rows.append((i, v, names.get(v, "")))
        out.append((k, cnt, rows))
    return out


def parts_write(pck, cat, slot, value):
    """Set one slot and resync that category's count. Returns the new count."""
    import struct as _s
    base = parts_base(pck)
    if base is None:
        raise ValueError("this file has no category table")
    if not 0 <= cat < len(PARTS_SLICES):
        raise ValueError("invalid category")
    if not 0 <= slot < PARTS_WIDTHS[cat]:
        raise ValueError("slot outside category capacity")
    _s.pack_into("<I", pck.data, base + PARTS_SLICES[cat] + 4 * slot,
                 value & 0xFFFFFFFF)
    n = sum(1 for i in range(PARTS_WIDTHS[cat])
            if pck.U(base + PARTS_SLICES[cat] + 4 * i) != PARTS_EMPTY)
    _s.pack_into("<I", pck.data, base + PARTS_COUNTS[cat], n)
    return n


class App:
    def __init__(self, path=None):
        self.pck = None
        self.path = None
        self.dirty = False

        self.root = tk.Tk()
        self.root.title("MC3 PCK Manager")
        self.root.geometry("1100x650")
        self._build()
        if path:
            self.open_file(path)

    # ---------------------------------------------------------------- layout
    def _build(self):
        top = ttk.Frame(self.root, padding=(8, 8, 8, 4))
        top.pack(fill="x")
        ttk.Button(top, text="Open...", command=self.on_open).pack(side="left")
        ttk.Button(top, text="Save", command=self.on_save).pack(side="left", padx=4)
        ttk.Button(top, text="Save as...", command=self.on_save_as).pack(side="left")
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(top, text="Embed file...", command=self.on_embed_file).pack(side="left")
        ttk.Button(top, text="Embed all from folder", command=self.on_embed_dir).pack(side="left", padx=4)
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(top, text="Remove", command=self.on_remove).pack(side="left")
        ttk.Button(top, text="Remove all", command=self.on_remove_all).pack(side="left", padx=4)
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(top, text="Edit ID...", command=self.on_edit_id).pack(side="left")
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(top, text="View 3D", command=self.on_view3d).pack(side="left")
        ttk.Button(top, text="Audio Studio...", command=self.on_audio_editor).pack(side="left", padx=4)

        self.info = ttk.Label(self.root, text="Open a vp_*.pck, vp_*.psppck or vp_*.xbck", padding=(10, 2))
        self.info.pack(fill="x")

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=8, pady=4)

        mid = ttk.Frame(self.nb, padding=(4, 4))
        self.nb.add(mid, text="Meshes")
        mbar = ttk.Frame(mid)
        mbar.pack(fill="x", pady=(0, 4))
        ttk.Button(mbar, text="Check all", command=lambda: self.check_all(True)).pack(side="left")
        ttk.Button(mbar, text="Uncheck all", command=lambda: self.check_all(False)).pack(side="left", padx=4)
        ttk.Button(mbar, text="Invert", command=self.check_invert).pack(side="left")
        self.chk_lbl = ttk.Label(mbar, text="", foreground="#0a6")
        self.chk_lbl.pack(side="left", padx=12)
        ttk.Label(mbar, text="(click the box to check; the actions use the checked "
                             "rows, or the selection if nothing is checked)",
                  foreground="#888").pack(side="right")
        cols = ("idx", "name", "id", "state", "size")
        self.tree = ttk.Treeview(mid, columns=cols, show="tree headings", selectmode="extended")
        for c, t, w in (("idx", "#", 55), ("name", "name", 470), ("id", "ID", 70),
                        ("state", "state", 110), ("size", "size", 90)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w" if c == "name" else "center",
                             stretch=(c == "name"))
        self.tree.column("#0", width=150, stretch=False)
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("emb", foreground="#0a6")
        self.tree.tag_configure("ext", foreground="#888")
        self.tree.tag_configure("root", foreground="#568")
        # checkboxes: the mark lives in the tree column (#0). Clicking it
        # toggles; clicking a LOD box toggles all of its children.
        self._checked = set()
        self.tree.bind("<Button-1>", self._on_tree_click, add="+")
        self.tree.bind("<space>", self._on_tree_space)

        # ---- .skel tab (the same ID list the meshes use) ----
        skf = ttk.Frame(self.nb, padding=(4, 4))
        self.nb.add(skf, text="Skel (bones)")
        bar = ttk.Frame(skf)
        bar.pack(fill="x", pady=(0, 4))
        ttk.Button(bar, text="Edit bone ID...", command=self.on_edit_bone_id).pack(side="left")
        ttk.Button(bar, text="Edit anchor...", command=self.on_edit_anchor).pack(side="left", padx=4)
        self.skel_lbl = ttk.Label(bar, text="")
        self.skel_lbl.pack(side="left", padx=12)
        scols = ("idx", "name", "id", "parent", "anchor", "uses", "tags")
        self.skel = ttk.Treeview(skf, columns=scols, show="tree headings", selectmode="browse")
        for c, t, w in (("idx", "#", 55), ("name", "bone", 260), ("id", "ID", 60),
                        ("parent", "parent", 70), ("anchor", "anchor (x,y,z)", 220),
                        ("uses", "meshes with this ID", 150),
                        ("tags", "PS2 bone tags", 260)):
            self.skel.heading(c, text=t)
            self.skel.column(c, width=w, anchor="w" if c in ("name", "anchor") else "center",
                             stretch=(c == "name"))
        self.skel.column("#0", width=180, stretch=False)
        sb2 = ttk.Scrollbar(skf, orient="vertical", command=self.skel.yview)
        self.skel.configure(yscrollcommand=sb2.set)
        self.skel.pack(side="left", fill="both", expand=True)
        sb2.pack(side="right", fill="y")

        # ---- vehicle structures tab (VehSim, VehGyro, audio...) ----
        stf = ttk.Frame(self.nb, padding=(4, 4))
        self.nb.add(stf, text="Structures")
        ttk.Label(stf, text="Structures pointed at by the header. The 1st u32 is the VTABLE POINTER, "
                            "a serialization leftover: the game rewrites it on load (you can zero it safely) "
                            "and the value changes on every run. The real type comes from the slot POSITION.",
                  foreground="#666").pack(anchor="w", pady=(0, 4))
        scols2 = ("name", "va", "file", "type", "config")
        self.structs = ttk.Treeview(stf, columns=scols2, show="headings", selectmode="browse")
        for c, t, w in (("name", "structure", 130), ("va", "VA", 100), ("file", "file", 90),
                        ("type", "vtable ptr", 100), ("config", "config name", 240)):
            self.structs.heading(c, text=t)
            self.structs.column(c, width=w, anchor="w" if c in ("name", "config") else "center",
                                stretch=(c == "config"))
        self.structs.pack(fill="both", expand=True)

        # ---- performance tab (VehSim/VehGyro/engine/gearbox/wheels...) ----
        pf = ttk.Frame(self.nb, padding=(4, 4))
        self.nb.add(pf, text="Performance")
        pbar = ttk.Frame(pf)
        pbar.pack(fill="x", pady=(0, 4))
        ttk.Button(pbar, text="Edit value...", command=self.on_edit_perf).pack(side="left")
        self.perf_lbl = ttk.Label(pbar, text="")
        self.perf_lbl.pack(side="left", padx=12)
        pcols = ("field", "value", "type", "off")
        self.perf = ttk.Treeview(pf, columns=pcols, show="tree headings", selectmode="browse")
        for c, t, w in (("field", "field", 240), ("value", "value", 160),
                        ("type", "type", 60), ("off", "file offset", 100)):
            self.perf.heading(c, text=t)
            self.perf.column(c, width=w, anchor="w" if c == "field" else "center",
                             stretch=(c == "field"))
        self.perf.column("#0", width=190, stretch=False)
        sb3 = ttk.Scrollbar(pf, orient="vertical", command=self.perf.yview)
        self.perf.configure(yscrollcommand=sb3.set)
        self.perf.pack(side="left", fill="both", expand=True)
        sb3.pack(side="right", fill="y")
        self.perf.bind("<Double-1>", lambda e: self.on_edit_perf())

        # ---- parts tab (customisation categories) ----
        paf = ttk.Frame(self.nb, padding=(4, 4))
        self.nb.add(paf, text="Parts")
        pabar = ttk.Frame(paf)
        pabar.pack(fill="x", pady=(0, 4))
        ttk.Button(pabar, text="Set ID...", command=self.on_edit_part).pack(side="left")
        ttk.Button(pabar, text="Clear slot", command=self.on_clear_part).pack(side="left", padx=(6, 0))
        self.parts_lbl = ttk.Label(pabar, text="", foreground="#666")
        self.parts_lbl.pack(side="left", padx=(10, 0))
        ttk.Label(paf, text="11 categories of 24 slots. The ID is a BONE INDEX, not a part - the game "
                            "resolves it to the node name and registers one option per category, which "
                            "is why two of the same kind never show together. The count must match the "
                            "filled slots; editing here keeps it in sync.",
                  foreground="#666", wraplength=900, justify="left").pack(anchor="w", pady=(0, 4))
        pcols = ("slot", "id", "bone")
        self.parts = ttk.Treeview(paf, columns=pcols, show="tree headings", selectmode="browse")
        self.parts.heading("#0", text="category")
        self.parts.column("#0", width=230, stretch=False)
        for c, t, w in (("slot", "slot", 60), ("id", "bone id", 80), ("bone", "bone name", 380)):
            self.parts.heading(c, text=t)
            self.parts.column(c, width=w, anchor="w" if c == "bone" else "center",
                              stretch=(c == "bone"))
        self.parts.pack(fill="both", expand=True)

        # ---- textures tab (the car parts atlas) ----
        tf = ttk.Frame(self.nb, padding=(4, 4))
        self.nb.add(tf, text="Textures")
        tbar = ttk.Frame(tf)
        tbar.pack(fill="x", pady=(0, 4))
        ttk.Button(tbar, text="Export PNG...", command=self.on_tex_export).pack(side="left")
        ttk.Button(tbar, text="Export all...", command=self.on_tex_export_all).pack(side="left", padx=4)
        ttk.Button(tbar, text="Import PNG...", command=self.on_tex_import).pack(side="left")
        ttk.Button(tbar, text="Add by offset...", command=self.on_tex_add_offset).pack(side="left", padx=4)
        self.tex_lbl = ttk.Label(tbar, text="")
        self.tex_lbl.pack(side="left", padx=12)

        tbody = ttk.Frame(tf)
        tbody.pack(fill="both", expand=True)
        tcols = ("name", "dim", "bpp", "type", "off")
        self.tex = ttk.Treeview(tbody, columns=tcols, show="headings", selectmode="browse")
        for c, t, w in (("name", "name", 200), ("dim", "size", 90), ("bpp", "bpp", 50),
                        ("type", "type", 150), ("off", "pixel offset", 110)):
            self.tex.heading(c, text=t)
            self.tex.column(c, width=w, anchor="w" if c in ("name", "type") else "center",
                            stretch=(c == "name"))
        sb4 = ttk.Scrollbar(tbody, orient="vertical", command=self.tex.yview)
        self.tex.configure(yscrollcommand=sb4.set)
        self.tex.pack(side="left", fill="both", expand=True)
        sb4.pack(side="left", fill="y")
        # preview panel on the right. The frame has a FIXED size in pixels
        # (pack_propagate(False)); without it a tk.Label with no image would read
        # width/height as characters/lines and blow up the layout.
        prev = ttk.Frame(tbody, padding=(8, 0))
        prev.pack(side="left", fill="y")
        holder = tk.Frame(prev, width=272, height=272, background="#333")
        holder.pack()
        holder.pack_propagate(False)
        self.tex_canvas = tk.Label(holder, background="#333", anchor="center")
        self.tex_canvas.pack(fill="both", expand=True)
        self.tex_info = ttk.Label(prev, text="(select a texture)", justify="center")
        self.tex_info.pack(pady=(4, 0))
        self.tex.bind("<<TreeviewSelect>>", lambda e: self.on_tex_select())
        self._tex_map = {}
        self._manual_tex = []
        self._tex_photo = None

        # ---- shaders tab (the index->shader table a mesh material points into) ----
        shf = ttk.Frame(self.nb, padding=(4, 4))
        self.nb.add(shf, text="Shaders")
        ttk.Label(shf, text="The shadinggroup: the mesh material index points into THIS list, "
                            "and the order changes from car to car -- that is why a converted "
                            "part can come out with the wrong material.",
                  foreground="#666", wraplength=900, justify="left").pack(anchor="w", pady=(0, 4))
        shcols = ("idx", "shader")
        self.shaders = ttk.Treeview(shf, columns=shcols, show="headings", selectmode="browse")
        for c, t, w in (("idx", "index", 70), ("shader", "shader resource", 600)):
            self.shaders.heading(c, text=t)
            self.shaders.column(c, width=w, anchor="w" if c == "shader" else "center",
                                stretch=(c == "shader"))
        sb5 = ttk.Scrollbar(shf, orient="vertical", command=self.shaders.yview)
        self.shaders.configure(yscrollcommand=sb5.set)
        self.shaders.pack(side="left", fill="both", expand=True)
        sb5.pack(side="right", fill="y")

        bot = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        bot.pack(fill="both")
        self.log = tk.Text(bot, height=8, wrap="none")
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")

    # ------------------------------------------------------------- checkboxes
    CHK_ON, CHK_OFF = "☑", "☐"      # checked / empty box

    def _is_slot_row(self, iid):
        """Only mesh rows have a checkbox; LOD nodes are just groupers."""
        return bool(self.tree.parent(iid))

    def _slot_rows(self):
        for lod in self.tree.get_children(""):
            for iid in self.tree.get_children(lod):
                yield iid

    def _paint_check(self, iid):
        mark = self.CHK_ON if iid in self._checked else self.CHK_OFF
        self.tree.item(iid, text=mark)

    def _set_checked(self, iid, on):
        if on:
            self._checked.add(iid)
        else:
            self._checked.discard(iid)
        self._paint_check(iid)

    def _toggle(self, iid):
        self._set_checked(iid, iid not in self._checked)

    def _on_tree_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "tree":
            return None                      # clicked on a data column
        iid = self.tree.identify_row(event.y)
        if not iid:
            return None
        if self._is_slot_row(iid):
            self._toggle(iid)
        else:                                # LOD box: turn everything on/off
            kids = self.tree.get_children(iid)
            want = not all(k in self._checked for k in kids)
            for k in kids:
                self._set_checked(k, want)
        self._update_check_lbl()
        return "break"                       # keep it from becoming a selection drag

    def _on_tree_space(self, _event):
        for iid in self.tree.selection():
            if self._is_slot_row(iid):
                self._toggle(iid)
        self._update_check_lbl()
        return "break"

    def _update_check_lbl(self):
        n = sum(1 for i in self._checked if self.tree.exists(i))
        self.chk_lbl.configure(text=("%d checked" % n) if n else "")

    def check_all(self, on):
        for iid in self._slot_rows():
            self._set_checked(iid, on)
        self._update_check_lbl()

    def check_invert(self):
        for iid in self._slot_rows():
            self._toggle(iid)
        self._update_check_lbl()

    def say(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        self.root.update_idletasks()

    # ----------------------------------------------------------------- dados
    def on_open(self):
        p = filedialog.askopenfilename(
            title="Open container",
            filetypes=(("MC3 containers", "*.pck *.psppck *.xbck"), ("All", "*.*")))
        if p:
            self.open_file(p)

    def open_file(self, path):
        try:
            self.pck = mc3.Pck(path)
        except Exception as exc:
            messagebox.showerror("Error", "Could not open:\n%s" % exc)
            return
        self.path = path
        self.dirty = False
        lods = self.pck.lods()
        if not lods:
            messagebox.showwarning(
                "No tables",
                "No mesh tables found in this file.\n"
                "Is it the main vehicle container (vp_*.pck / .psppck / .xbck)?")
        self.say("opened: %s  (VA %#x, %s)" % (os.path.basename(path), self.pck.va,
                                               human(len(self.pck.data))))
        self.refresh()

    def refresh(self):
        # the iids die on delete, so keep the marks by (LOD, index)
        keep = set()
        for iid in list(self._checked):
            if self.tree.exists(iid) and self.tree.parent(iid):
                lod = self.tree.item(self.tree.parent(iid), "text").split()[0]
                keep.add((lod, self.tree.item(iid, "values")[0]))
        self.tree.delete(*self.tree.get_children())
        self._checked.clear()
        if not self.pck:
            self._update_check_lbl()
            return
        p = self.pck
        total_emb = 0
        for lod, va in p.lods().items():
            sl = p.slots(va)
            emb = [s for s in sl if s[1]]
            sk = p.skel_info(va)
            label = "%s  (%d slots, %d embedded)" % (lod, len(sl), len(emb))
            if sk:
                label += "   skel: %d items" % sk[0]
            node = self.tree.insert("", "end", text=label, open=True,
                                    values=("", "", "", "", ""))
            for (i, mp, nm, idv, off) in sl:
                if mp:
                    fp = p.mesh_footprint(mp)
                    size = human(fp[1]-fp[0]) if fp else "?"
                    state, tag = "embedded", "emb"
                    total_emb += (fp[1]-fp[0]) if fp else 0
                else:
                    size = ""
                    state, tag = ("external @ vroot", "root") if idv == 0 else ("external", "ext")
                iid = self.tree.insert(node, "end",
                                       values=(i, nm or "(no name)", idv, state, size),
                                       tags=(tag,), text=self.CHK_OFF)
                if (lod, str(i)) in keep:
                    self._checked.add(iid)
                    self._paint_check(iid)
        self.info.configure(text="%s   |   %s   |   embedded: %s   |   %s" % (
            os.path.basename(self.path), human(len(p.data)), human(total_emb),
            "MODIFIED" if self.dirty else "saved"))
        self._update_check_lbl()
        self.refresh_skel()
        self.refresh_parts()
        self.refresh_structs()
        self.refresh_perf()
        self.refresh_tex()
        self.refresh_shaders()

    def refresh_perf(self):
        self.perf.delete(*self.perf.get_children())
        self._perf_map = {}
        if not self.pck:
            return
        try:
            secs = perf.sections(self.pck)
        except Exception as exc:
            self.perf_lbl.configure(text="error: %s" % exc)
            return
        if not secs:
            self.perf_lbl.configure(text="(no performance data -- is this a PS2 car PCK?)")
            return
        nf = sum(len(v) for v in secs.values())
        self.perf_lbl.configure(text="%d sections, %d fields" % (len(secs), nf))
        # VehMods: upgrade table (raw u16, editable)
        try:
            mods = perf.vehmods(self.pck)
        except Exception:
            mods = []
        if mods:
            mnode = self.perf.insert("", "end", text="VehMods (upgrades)", open=False,
                                     values=("", "", "", ""))
            for (k, off, vals) in mods:
                cname, recs = perf.vehmods_decode(vals)
                cat = self.perf.insert(mnode, "end", text=cname,
                                       values=("%d sub-mods" % len(recs), "", "",
                                               "0x%X" % off))
                for (sub, fields) in recs:
                    snode = self.perf.insert(cat, "end", text=sub,
                                             values=("", "", "", ""))
                    for (fname, v, idx) in fields:
                        fo = off + 2*idx
                        iid = self.perf.insert(snode, "end", text="",
                                               values=(fname, v, "h", "0x%X" % fo))
                        self._perf_map[iid] = (fname, "h", fo, v)

        for sec in secs:
            node = self.perf.insert("", "end", text=sec, open=False, values=("", "", "", ""))
            for (name, typ, off, val) in secs[sec]:
                if typ == "f":
                    shown = "%.6g" % val
                elif name == "DriveTrainType":
                    shown = "%d  (%s)" % (val, perf.DRIVETRAIN_TYPES.get(val, "?"))
                else:
                    shown = str(val)
                iid = self.perf.insert(node, "end", text="",
                                       values=(name, shown, typ, "0x%X" % off))
                self._perf_map[iid] = (name, typ, off, val)

    # -------------------------------------------------------------- textures
    @staticmethod
    def _tex_spec(w, h):
        """(bpp, colors, type text, editable)."""
        if (w, h) == (8, 8):
            return 4, 16, "4bpp placeholder", False
        return 8, 256, "8bpp swizzled", True

    def refresh_tex(self):
        self.tex.delete(*self.tex.get_children())
        self._tex_map = {}
        self.tex_canvas.configure(image="")
        self._tex_photo = None
        self.tex_info.configure(text="(select a texture)")
        if not self.pck:
            self.tex_lbl.configure(text="")
            return
        try:
            items = tex.textures(self.pck.data, base=self.pck.va)
        except Exception as exc:
            self.tex_lbl.configure(text="error: %s" % exc)
            return
        if not items:
            psp = self.path.lower().endswith(".psppck")
            self.tex_lbl.configure(text=(
                "(no textures here -- on PSP they live in the .pspppf packs, "
                "a separate format this toolkit does not read yet)" if psp else
                "(no textures -- is this a PS2 vehicle vp_*.pck?)"))
            return
        nres = sum(1 for _n, _p, d, _c in items if d)
        self.tex_lbl.configure(text="%d texture(s), %d sized" % (len(items), nres))
        for (nm, px, dim, desc) in items:
            if dim:
                w, h = dim
                bpp, _c, tstr, _e = self._tex_spec(w, h)
                dims, bpps = "%dx%d" % (w, h), str(bpp)
            else:
                w = h = 0
                dims, bpps, tstr = "?", "", "unknown format"
            iid = self.tex.insert("", "end",
                                  values=(nm or "(no name)", dims, bpps, tstr, "0x%X" % px))
            self._tex_map[iid] = {"name": nm, "px": px, "w": w, "h": h}
        # re-add the manually placed textures (they do not come from the scan)
        auto = {(e["px"]) for e in self._tex_map.values()}
        for m in self._manual_tex:
            if m["px"] in auto:
                continue
            iid = self.tex.insert("", "end",
                                  values=(m["name"], "%dx%d" % (m["w"], m["h"]), 8,
                                          "8bpp swizzled (manual)", "0x%X" % m["px"]))
            self._tex_map[iid] = dict(m)

    def _select_tex_by_px(self, px):
        for iid, e in self._tex_map.items():
            if e["px"] == px:
                self.tex.selection_set(iid)
                self.tex.see(iid)
                self.on_tex_select()
                return

    def _selected_tex(self):
        sel = self.tex.selection()
        if not sel or sel[0] not in self._tex_map:
            return None
        return self._tex_map[sel[0]]

    def _make_preview(self, im, box=256):
        """Compose the texture over a checkerboard (to show alpha), scale it up
        with nearest neighbour to ~box px and return a PhotoImage through a
        temporary PNG."""
        import tempfile
        w, h = im.width, im.height
        src = im.to_rgba()               # already with alpha 0..255
        if max(w, h) < box:              # small: scale up by an integer
            k = box // max(w, h)
            tw, th = w * k, h * k
        elif w >= h:
            tw, th = box, max(1, h * box // w)
        else:
            th, tw = box, max(1, w * box // h)
        out = bytearray(tw * th * 4)
        for y in range(th):
            sy = y * h // th
            for x in range(tw):
                sx = x * w // tw
                s = (sy * w + sx) * 4
                a = src[s + 3]
                chk = 150 if ((x // 8 + y // 8) & 1) else 90
                o = (y * tw + x) * 4
                for k in range(3):
                    out[o + k] = (src[s + k] * a + chk * (255 - a)) // 255
                out[o + 3] = 255
        fd, path = tempfile.mkstemp(suffix=".png", prefix="mc3tex_")
        os.close(fd)
        tex.write_png(path, tw, th, out)
        img = tk.PhotoImage(file=path)
        try:
            os.remove(path)
        except OSError:
            pass
        return img

    def on_tex_select(self):
        e = self._selected_tex()
        if not e:
            return
        if not e["w"]:
            self.tex_canvas.configure(image="")
            self._tex_photo = None
            self.tex_info.configure(text="unknown size\n(use 'Add by offset')")
            return
        try:
            im = tex.decode_texture(self.pck.data, e["px"], e["w"], e["h"])
            self._tex_photo = self._make_preview(im)
        except Exception as exc:
            self.tex_canvas.configure(image="")   # do not keep the previous image
            self._tex_photo = None
            self.tex_info.configure(text="preview error:\n%s" % exc)
            return
        self.tex_canvas.configure(image=self._tex_photo)
        self.tex_info.configure(text="%s\n%dx%d  %d colors" % (
            e["name"] or "(no name)", e["w"], e["h"], im.colors))

    def on_tex_export(self):
        if not self.need_file():
            return
        e = self._selected_tex()
        if not e or not e["w"]:
            messagebox.showinfo("Select", "Pick a texture that has a size.")
            return
        p = filedialog.asksaveasfilename(
            defaultextension=".png", initialfile=(e["name"] or "tex_%X" % e["px"]) + ".png",
            filetypes=(("PNG", "*.png"),))
        if not p:
            return
        try:
            tex.decode_texture(self.pck.data, e["px"], e["w"], e["h"]).save_png(p)
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            return
        self.say("texture exported: %s" % p)

    def on_tex_export_all(self):
        if not self.need_file():
            return
        folder = filedialog.askdirectory(title="Folder to export the textures to")
        if not folder:
            return
        n = 0
        used = set()
        for e in self._tex_map.values():
            if not e["w"]:
                continue
            nm = e["name"] or "tex"
            if nm in used:
                nm = "%s_%X" % (nm, e["px"])
            used.add(nm)
            try:
                tex.decode_texture(self.pck.data, e["px"], e["w"], e["h"]).save_png(
                    os.path.join(folder, nm + ".png"))
                n += 1
            except Exception as exc:
                self.say("  [error] %s: %s" % (nm, exc))
        self.say("exported %d texture(s) to %s" % (n, folder))

    def on_tex_import(self):
        if not self.need_file():
            return
        e = self._selected_tex()
        if not e or not e["w"]:
            messagebox.showinfo("Select", "Pick a texture that has a size.")
            return
        if not self._tex_spec(e["w"], e["h"])[3]:
            messagebox.showinfo("Not editable",
                                "8x8 placeholder (flat color resolved in the shader).")
            return
        p = filedialog.askopenfilename(title="PNG to import",
                                       filetypes=(("PNG", "*.png"), ("All", "*.*")))
        if not p:
            return
        try:
            sw, sh, rgba = tex.read_png(p)
            if (sw, sh) != (e["w"], e["h"]) and not messagebox.askyesno(
                    "Different size",
                    "The PNG is %dx%d and the slot is %dx%d.\n"
                    "Rescale with nearest neighbour?" % (sw, sh, e["w"], e["h"])):
                return
            tex.replace_texture(self.pck.data, e["px"], e["w"], e["h"], rgba, sw, sh)
        except Exception as exc:
            messagebox.showerror("Import error", str(exc))
            return
        self.dirty = True
        self.say("imported %s -> %s (%dx%d)" % (
            os.path.basename(p), e["name"] or "0x%X" % e["px"], e["w"], e["h"]))
        px = e["px"]
        self.refresh()
        self._select_tex_by_px(px)     # keep the selection and show the new pixels

    def on_tex_add_offset(self):
        if not self.need_file():
            return
        s = simpledialog.askstring("Manual offset",
                                   "Pixel offset in the file (hex, e.g. 0x6090):",
                                   parent=self.root)
        if not s:
            return
        try:
            px = int(s, 0)
        except ValueError:
            messagebox.showerror("Invalid", "Use hex, e.g. 0x6090.")
            return
        d = simpledialog.askstring("Size", "Width x Height (e.g. 256x256):",
                                   initialvalue="256x256", parent=self.root)
        if not d:
            return
        try:
            w, h = (int(x) for x in d.lower().replace(" ", "").split("x"))
        except Exception:
            messagebox.showerror("Invalid", "Use WIDTHxHEIGHT, e.g. 256x256.")
            return
        entry = {"name": "manual_%X" % px, "px": px, "w": w, "h": h}
        self._manual_tex = [m for m in self._manual_tex if m["px"] != px] + [entry]
        iid = self.tex.insert("", "end",
                              values=(entry["name"], "%dx%d" % (w, h), 8,
                                      "8bpp swizzled (manual)", "0x%X" % px))
        self._tex_map[iid] = dict(entry)
        self.tex.selection_set(iid)
        self.tex.see(iid)
        self.on_tex_select()

    # -------------------------------------------------------------- shaders
    def refresh_shaders(self):
        self.shaders.delete(*self.shaders.get_children())
        if not self.pck or shading is None:
            return
        try:
            _base, names = shading.extract_shadinggroup(self.path)
        except Exception as exc:
            self.shaders.insert("", "end", values=("", "error: %s" % exc))
            return
        if not names:
            self.shaders.insert("", "end", values=("", "(no shadinggroup found)"))
            return
        for i, nm in enumerate(names):
            self.shaders.insert("", "end", values=(i, nm))

    # ------------------------------------------------------------- 3D view
    def on_view3d(self):
        """Open the mesh viewer. Shows the checked/selected slots when they are
        embedded, otherwise the whole container."""
        if not self.need_file():
            return
        if view is None:
            messagebox.showinfo("Viewer unavailable",
                                "mc3_view.py could not be loaded (it needs matplotlib "
                                "for the native window).")
            return
        if self.dirty and not messagebox.askyesno(
                "Unsaved changes",
                "The viewer reads the file from disk.\n"
                "Your changes are not saved yet -- open anyway?"):
            return

        target, label = self.path, os.path.basename(self.path)
        sel = [s for s in self.selection() if s[3]] if self.pck.lods() else []
        if len(sel) == 1:
            # a single embedded mesh: dump just that body so the viewer opens the
            # part instead of the whole car
            va, lod, i, mp, nm, idv, off = sel[0]
            fp = self.pck.mesh_footprint(mp)
            if fp:
                import tempfile
                fd, tmp = tempfile.mkstemp(suffix=".mesh.pck", prefix="mc3view_")
                with os.fdopen(fd, "wb") as f:
                    f.write(bytes(self.pck.data[:0x80]))
                    f.write(bytes(self.pck.data[fp[0]:fp[1]]))
                target, label = tmp, "%s[%d] %s" % (lod, i, nm or "")
        try:
            groups, plat = view.parse_any(target)
        except Exception as exc:
            messagebox.showerror("Viewer error", str(exc))
            return
        if not groups:
            messagebox.showinfo("Nothing to show", "No geometry found in %s." % label)
            return
        self.say("3D view: %s (%d group(s), %s)" % (label, len(groups), plat))
        try:
            view.show_native(groups, label, plat)
        except Exception as exc:
            messagebox.showerror("Viewer error", str(exc))

    # MC3 Audio Studio is a separate, finished app; here we just launch it
    # pointing at the currently open file. It lives in this folder together with
    # its bnk_tool package (it does sys.path.insert on its own directory); the
    # extra candidates only cover the case of someone moving it back out.
    AUDIO_STUDIO_NAME = "mc3_audio_studio.py"
    AUDIO_STUDIO_CANDIDATES = [
        os.path.join(_HERE, AUDIO_STUDIO_NAME),
        os.path.join(os.path.dirname(_HERE), AUDIO_STUDIO_NAME),
    ]

    @classmethod
    def _find_audio_studio(cls):
        for c in cls.AUDIO_STUDIO_CANDIDATES:
            if os.path.isfile(c):
                return c
        return None

    def on_audio_editor(self):
        if not self.need_file():
            return
        if self.dirty and not messagebox.askyesno(
                "Unsaved changes",
                "The Audio Studio opens the file from disk.\n"
                "Your changes have not been saved yet -- open anyway?"):
            return
        script = self._find_audio_studio()
        if not script:
            messagebox.showinfo(
                "Audio Studio not found",
                "Could not find %s.\n\nIt needs the bnk_tool package sitting next "
                "to it, so point at the script in its own folder." % self.AUDIO_STUDIO_NAME)
            script = filedialog.askopenfilename(
                title="Where is mc3_audio_studio.py?",
                filetypes=(("Python", "*.py"), ("All", "*.*")))
            if not script:
                return
        pkg = os.path.join(os.path.dirname(os.path.abspath(script)), "bnk_tool")
        if not os.path.isdir(pkg):
            messagebox.showwarning(
                "bnk_tool missing",
                "The bnk_tool package was not found next to\n%s\n\n"
                "The Audio Studio needs it and will fail to start." % script)
        import subprocess
        try:
            subprocess.Popen([sys.executable, script, os.path.abspath(self.path)],
                             cwd=os.path.dirname(os.path.abspath(script)))
        except Exception as exc:
            messagebox.showerror("Error", "Could not open the Audio Studio:\n%s" % exc)
            return
        self.say("Audio Studio opened on %s" % os.path.basename(self.path))

    def on_edit_perf(self):
        if not self.need_file():
            return
        sel = self.perf.selection()
        if not sel or sel[0] not in getattr(self, "_perf_map", {}):
            messagebox.showinfo("Select", "Pick a field (not the section).")
            return
        name, typ, off, val = self._perf_map[sel[0]]
        cur = ("%.6g" % val) if typ == "f" else str(val)
        v = simpledialog.askstring("Edit %s" % name,
                                   "New value for %s  (%s @ 0x%X):" % (name, typ, off),
                                   initialvalue=cur, parent=self.root)
        if v is None:
            return
        try:
            perf.write(self.pck, off, typ, float(v) if typ == "f" else int(v, 0))
        except Exception as exc:
            messagebox.showerror("Invalid value", str(exc))
            return
        self.dirty = True
        self.say("%s: %s -> %s  (@0x%X)" % (name, cur, v, off))
        self.refresh()

    def refresh_parts(self):
        self.parts.delete(*self.parts.get_children())
        self._parts_map = {}
        if not self.pck:
            return
        data = parts_read(self.pck)
        if not data:
            self.parts_lbl.configure(text="(this file has no category table)")
            return
        used_set = sum(len(r) for _k, _c, r in data)
        self.parts_lbl.configure(text="%d slots filled across %d categories"
                                      % (used_set, len(data)))
        for k, cnt, rows in data:
            ok = "" if cnt == len(rows) else "   COUNT %d != %d FILLED" % (cnt, len(rows))
            top = self.parts.insert("", "end", open=bool(rows),
                                    text="%d - %s  (count %d)%s"
                                         % (k, PARTS_NAMES[k] if k < len(PARTS_NAMES) else "?",
                                            cnt, ok),
                                    values=("", "", ""))
            for slot, idv, nm in rows:
                iid = self.parts.insert(top, "end", text="",
                                        values=(slot, idv, nm or "(no name)"))
                self._parts_map[iid] = (k, slot, idv)

    def _part_selection(self):
        sel = self.parts.selection()
        if not sel or sel[0] not in getattr(self, "_parts_map", {}):
            messagebox.showinfo("Select", "Pick a slot (not the category row).")
            return None
        return self._parts_map[sel[0]]

    def on_edit_part(self):
        if not self.need_file():
            return
        got = self._part_selection()
        if not got:
            return
        cat, slot, idv = got
        v = simpledialog.askstring("Category %d slot %d" % (cat, slot),
                                   "New bone id (or FFFFFFFF to clear):",
                                   initialvalue=str(idv), parent=self.root)
        if v is None:
            return
        try:
            n = parts_write(self.pck, cat, slot, int(v, 0))
        except Exception as exc:
            messagebox.showerror("Invalid value", str(exc))
            return
        self.dirty = True
        self.say("category %d slot %d: %s -> %s  (count now %d)" % (cat, slot, idv, v, n))
        self.refresh_parts()

    def on_clear_part(self):
        if not self.need_file():
            return
        got = self._part_selection()
        if not got:
            return
        cat, slot, idv = got
        n = parts_write(self.pck, cat, slot, PARTS_EMPTY)
        self.dirty = True
        self.say("category %d slot %d cleared (was %s, count now %d)" % (cat, slot, idv, n))
        self.refresh_parts()

    def refresh_structs(self):
        self.structs.delete(*self.structs.get_children())
        if not self.pck:
            return
        # group by type ID to make it visible that the same type repeats the value
        seen = {}
        for (nm, va, tid, s) in self.pck.veh_structs():
            seen.setdefault(tid, []).append(nm)
            self.structs.insert("", "end", values=(nm, "0x%08X" % va,
                                                   "0x%X" % self.pck.F(va),
                                                   "0x%08X" % tid, s))

    def refresh_skel(self):
        self.skel.delete(*self.skel.get_children())
        p = self.pck
        nodes = p.skel_nodes() if p else []
        if not nodes:
            self.skel_lbl.configure(text="(this file has no .skel)")
            return
        # how many meshes use each ID (this is how a part attaches to a bone)
        uses = {}
        for lod, va in p.lods().items():
            for (i, mp, nm, idv, off) in p.slots(va):
                uses[idv] = uses.get(idv, 0) + 1
        tags_by_index = {}
        for tag, index, _stored, _bucket, _off in p.bone_tags():
            if index is not None:
                tags_by_index.setdefault(index, []).append(tag)
        label = "%d bones" % len(nodes)
        if tags_by_index:
            label += ", %d PS2 tags" % sum(map(len, tags_by_index.values()))
        self.skel_lbl.configure(text=label)
        node_of = {}
        for (i, nm, idv, par, ch, nxt, anc, off) in nodes:
            parent_iid = node_of.get(par, "")
            iid = self.skel.insert(parent_iid, "end", open=(par is None),
                                   text=nm or "(no name)",
                                   values=(i, nm or "(no name)", idv,
                                           "-" if par is None else par,
                                           "%.3f, %.3f, %.3f" % anc,
                                           uses.get(idv, 0) or "",
                                           ", ".join(tags_by_index.get(i, ()))))
            node_of[i] = iid

    def skel_selection(self):
        sel = self.skel.selection()
        if not sel:
            return None
        return int(self.skel.item(sel[0], "values")[0])

    def on_edit_bone_id(self):
        if not self.need_file():
            return
        i = self.skel_selection()
        if i is None:
            messagebox.showinfo("Select", "Pick a bone from the list.")
            return
        nodes = {n[0]: n for n in self.pck.skel_nodes()}
        cur = nodes[i][2]
        v = simpledialog.askstring("Bone ID",
                                   "New ID for bone '%s'.\n"
                                   "It is the same value the mesh table uses\n"
                                   "to attach the part to that bone." % (nodes[i][1] or i),
                                   initialvalue=str(cur), parent=self.root)
        if v is None:
            return
        try:
            self.pck.set_skel_id(i, int(v, 0))
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            return
        self.dirty = True
        self.say("bone[%d] %s: ID %d -> %s" % (i, nodes[i][1], cur, v))
        self.refresh()

    def on_edit_anchor(self):
        if not self.need_file():
            return
        i = self.skel_selection()
        if i is None:
            messagebox.showinfo("Select", "Pick a bone from the list.")
            return
        nodes = {n[0]: n for n in self.pck.skel_nodes()}
        cur = nodes[i][6]
        v = simpledialog.askstring("Bone anchor",
                                   "Position (x, y, z) of bone '%s':" % (nodes[i][1] or i),
                                   initialvalue="%.6f, %.6f, %.6f" % cur, parent=self.root)
        if v is None:
            return
        try:
            xyz = [float(x) for x in v.replace(";", ",").split(",")]
            if len(xyz) != 3:
                raise ValueError("give 3 numbers")
            self.pck.set_skel_anchor(i, xyz)
        except Exception as exc:
            messagebox.showerror("Invalid value", str(exc))
            return
        self.dirty = True
        self.say("bone[%d] %s: anchor %s -> %s" % (i, nodes[i][1],
                 "%.3f,%.3f,%.3f" % cur, v))
        self.refresh()

    def selection(self):
        """[(lod_va, lod_name, index, mesh_ptr, name, id, slot_off)].

        Uses the CHECKED rows; if there are none, uses the normal selection --
        that way anyone who never touches the boxes keeps the old behaviour."""
        out = []
        lods = self.pck.lods()
        rows = [i for i in self._checked if self.tree.exists(i)] or list(self.tree.selection())
        for iid in rows:
            parent = self.tree.parent(iid)
            if not parent:
                continue
            lod_label = self.tree.item(parent, "text").split()[0]
            va = lods.get(lod_label)
            if va is None:
                continue
            vals = self.tree.item(iid, "values")
            idx = int(vals[0])
            for (i, mp, nm, idv, off) in self.pck.slots(va):
                if i == idx:
                    out.append((va, lod_label, i, mp, nm, idv, off))
                    break
        return out

    # -------------------------------------------------------------- actions
    def need_file(self):
        if not self.pck:
            messagebox.showinfo("Open a file", "Open a container first.")
            return False
        return True

    def on_embed_file(self):
        if not self.need_file():
            return
        sel = self.selection()
        if len(sel) != 1:
            messagebox.showinfo("Select 1 slot", "Pick exactly one slot from the list.")
            return
        va, lod, i, mp, nm, idv, off = sel[0]
        ext = "*" + os.path.splitext(self.path)[1]
        f = filedialog.askopenfilename(title="Mesh to embed into %s[%d]" % (lod, i),
                                       filetypes=(("Mesh", ext), ("All", "*.*")))
        if not f:
            return
        try:
            mva, nfix, blen = self.pck.append_mesh_body(f)
            self.pck.set_slot(off, mva)
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            return
        self.dirty = True
        self.say("embedded %s (%s) into %s[%d] -> VA %#x, %d pointers rebased"
                 % (os.path.basename(f), human(blen), lod, i, mva, nfix))
        self.refresh()

    def on_embed_dir(self):
        if not self.need_file():
            return
        folder = os.path.dirname(os.path.abspath(self.path))
        is_psp = self.path.lower().endswith(".psppck")
        placed, n = {}, 0
        for lod, va in self.pck.lods().items():
            for (i, mp, nm, idv, off) in self.pck.slots(va):
                if mp or not nm:
                    continue
                cand = None
                for e in _ext_order(self.path):
                    c = os.path.join(folder, nm + e)
                    if os.path.isfile(c):
                        cand = c
                        break
                if not cand:
                    continue
                if cand not in placed:
                    mva, _f, _b = self.pck.append_mesh_body(cand)
                    placed[cand] = mva
                self.pck.set_slot(off, placed[cand])
                n += 1
        if not n:
            messagebox.showinfo("Nothing to do",
                                "No empty slot has a matching file in the folder.")
            return
        self.dirty = True
        self.say("embedded %d slot(s) from %d file(s) in the folder" % (n, len(placed)))
        self.refresh()

    def _remove(self, targets):
        """targets: [(lod_va, lod, i, mesh_ptr, name, id, slot_off)]"""
        p = self.pck
        by_va = {}
        for t in targets:
            if t[3]:
                by_va.setdefault(t[3], []).append(t)
        ranges, zeros, total = [], [], 0
        for mesh_va, grp in sorted(by_va.items()):
            fp = p.mesh_footprint(mesh_va)
            if not fp:
                self.say("  [skip] %s: does not look like a valid mesh" % grp[0][4][:40])
                continue
            lo, hi = fp
            slots_here = [t[6] for t in grp]
            for lod, va in p.lods().items():
                for (i, mp, nm, idv, off) in p.slots(va):
                    if mp == mesh_va and off not in slots_here:
                        slots_here.append(off)
            if p.external_refs(lo, hi, tuple(slots_here)):
                r = p.relocate_blockers(lo, hi, tuple(slots_here))
                if r is None:
                    self.say("  [skip] %s: something outside points inside" % grp[0][4][:40])
                    continue
                if r[0]:
                    self.say("  (moved %d string(s) to free the space)" % r[0])
            ranges.append((lo, hi))
            zeros += slots_here
            total += hi - lo
            if any(t[5] == 0 for t in grp):
                self.say("  %s: ID=0; the external mesh will attach to vroot"
                         % grp[0][4][:44])
        if not ranges:
            self.say("nothing removed.")
            return
        removed, nfix = p.remove_ranges(ranges, zeros)
        self.dirty = True
        self.say("removed %s (%d pointers relocated)" % (human(removed), nfix))
        self.refresh()

    def on_remove(self):
        if not self.need_file():
            return
        sel = [s for s in self.selection() if s[3]]
        if not sel:
            messagebox.showinfo("Select", "Pick EMBEDDED slots to remove.")
            return
        if not messagebox.askyesno("Remove", "Remove %d embedded mesh(es)?\n\n"
                                   "The slots go back to pointing at the external file."
                                   % len(sel)):
            return
        self._remove(sel)

    def on_remove_all(self):
        if not self.need_file():
            return
        alls = []
        for lod, va in self.pck.lods().items():
            for (i, mp, nm, idv, off) in self.pck.slots(va):
                if mp:
                    alls.append((va, lod, i, mp, nm, idv, off))
        if not alls:
            messagebox.showinfo("Nothing", "There are no embedded meshes.")
            return
        if not messagebox.askyesno("Remove all",
                                   "Remove ALL %d embedded meshes?\n\n"
                                   "Only do this if the external .pck files exist in the folder."
                                   % len(alls)):
            return
        self._remove(alls)

    def on_edit_id(self):
        if not self.need_file():
            return
        sel = self.selection()
        if not sel:
            messagebox.showinfo("Select", "Pick one or more slots.")
            return
        cur = sel[0][5]
        v = simpledialog.askstring("Edit ID",
                                   "New ID (decimal or 0x..) for %d slot(s).\n"
                                   "The ID indexes the .skel list; 0 is the valid\n"
                                   "vroot node." % len(sel),
                                   initialvalue=str(cur), parent=self.root)
        if v is None:
            return
        try:
            val = int(v, 0)
        except ValueError:
            messagebox.showerror("Invalid value", "Use a number (e.g. 15 or 0x0F).")
            return
        for (va, lod, i, mp, nm, idv, off) in sel:
            self.pck.set_id(va, i, val)
            self.say("ID of %s[%d] : %d -> %d" % (lod, i, idv, val))
        self.dirty = True
        self.refresh()

    # ----------------------------------------------------------------- save
    def on_save(self):
        if not self.need_file():
            return
        if not messagebox.askyesno("Save", "Overwrite\n%s ?" % self.path):
            return
        self._write(self.path)

    def on_save_as(self):
        if not self.need_file():
            return
        ext = os.path.splitext(self.path)[1] or ".pck"
        p = filedialog.asksaveasfilename(defaultextension=ext,
                                         initialfile=os.path.basename(self.path),
                                         filetypes=(("Container", "*" + ext), ("All", "*.*")))
        if p:
            self._write(p)

    def _write(self, path):
        try:
            n = self.pck.save(path)
        except Exception as exc:
            messagebox.showerror("Save error", str(exc))
            return
        self.path = path
        self.dirty = False
        self.say("saved: %s (%s)" % (path, human(n)))
        self.refresh()

    def run(self):
        self.root.mainloop()


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    App(path).run()


if __name__ == "__main__":
    main()
