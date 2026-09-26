"""
mc3_traffic_gui.py - replace the meshes inside a city *_traffic.pck.

Deliberately a separate app from mc3_pck_manager: a traffic container has no
LOD tables and no performance structs, and it holds several vehicles at once
instead of one, so mixing them would only make both harder to reason about.

Drag a *_traffic.pck onto this .py, or run it and use "Open".

What it does:
  * lists every piece: vehicle, part, piece id, group count, size;
  * EXTRACTS a piece to .mesh.pck / .obj / .mesh so you can edit it;
  * REPLACES a piece from .mesh.pck, .obj, .mesh, .psppck or .xbck -- anything
    the converter reads is converted on the way in;
  * previews a piece in the 3D viewer;
  * TEXTURES: lists and exports them (same descriptor chain as a vehicle .pck,
    only the vtable values differ -- info 0x007A2320, node 0x007A20E0);
  * SKELETON: one per vehicle, 19 bones. The piece id IS the bone id, which is
    where a piece's name comes from.

Nothing already in the file ever moves: the new body is appended at the end and
only the slot pointer is rewritten. A replacement touches 5 bytes of the original
region -- the body size in the header and the slot pointer -- which is what keeps
the strings the container hides inside its padding from breaking.
"""
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import importlib.util

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)
    spec.loader.exec_module(mod)
    return mod


traffic = _load("mc3_traffic")


def _optional(name):
    try:
        return _load(name)
    except Exception:
        return None


convert = _optional("mc3_mesh_convert")
view = _optional("mc3_view")
tex = _optional("mc3_tex")

# what the converter can turn into a .mesh.pck on the way in
IMPORT_EXT = (".mesh.pck", ".pck", ".obj", ".mesh", ".psppck", ".xbck")

# Index 2 is "ambient_body" in all four cities, so it is the safe pick for a body
# mesh; index 1 is "ambient_shadow" on atlanta/detroit but "ambient_wheel" on
# sd/tokyo. Anything at or above the container's shader count hangs the game.
DEFAULT_MATERIAL = 1


def human(n):
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%.1f KB" % (n / 1024.0)
    return "%.2f MB" % (n / (1024.0 * 1024))


class App:
    def __init__(self, path=None):
        self.tp = None
        self.path = None
        self.dirty = False
        self.root = tk.Tk()
        self.root.title("MC3 Traffic Mesh Replacer")
        self.root.geometry("1020x620")
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
        ttk.Button(top, text="Replace mesh...", command=self.on_replace).pack(side="left")
        ttk.Button(top, text="Extract...", command=self.on_extract).pack(side="left", padx=4)
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(top, text="View 3D", command=self.on_view).pack(side="left")

        self.info = ttk.Label(self.root, text="Open a city *_traffic.pck",
                              padding=(10, 2))
        self.info.pack(fill="x")

        ttk.Label(self.root, padding=(10, 0),
                  text="A replacement appends the new mesh at the end and only rewrites the "
                       "slot pointer, so nothing already in the file moves.",
                  foreground="#666").pack(fill="x")

        # ---- shader index forced on whatever gets imported ----
        shbar = ttk.Frame(self.root, padding=(10, 4, 10, 0))
        shbar.pack(fill="x")
        self.force_mat = tk.BooleanVar(value=True)
        ttk.Checkbutton(shbar, text="Force shader index on import:",
                        variable=self.force_mat).pack(side="left")
        self.mat_var = tk.StringVar(value=str(DEFAULT_MATERIAL))
        ttk.Spinbox(shbar, from_=0, to=63, width=4, textvariable=self.mat_var
                    ).pack(side="left", padx=6)
        self.shader_lbl = ttk.Label(shbar, text="", foreground="#666")
        self.shader_lbl.pack(side="left", padx=8)
        self.traffic_blocks = tk.BooleanVar(value=True)
        ttk.Checkbutton(shbar, text="re-encode packets in traffic style",
                        variable=self.traffic_blocks).pack(side="left", padx=16)
        ttk.Label(self.root, padding=(10, 0),
                  text="A traffic container only carries 6-7 shaders, but a vehicle mesh "
                       "usually refers to a much higher index (the 350z hood uses 14) -- and "
                       "an index past the end HANGS THE GAME, so imports are clamped to a low one. "
                       "Traffic packets also carry no per-packet culling bounds (tag 98000260 instead "
                       "of 9800026C), so imports are re-encoded to match.",
                  foreground="#a60", wraplength=980, justify="left").pack(fill="x")

        self.nb = ttk.Notebook(self.root, padding=(8, 4))
        self.nb.pack(fill="both", expand=True)
        mid = ttk.Frame(self.nb)
        self.nb.add(mid, text="Meshes")
        cols = ("idx", "vehicle", "part", "id", "groups", "size", "mesh", "slot")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        for c, t, w in (("idx", "#", 40), ("vehicle", "vehicle", 190),
                        ("part", "part", 70), ("id", "piece id", 60),
                        ("groups", "groups", 70), ("size", "size", 90),
                        ("mesh", "mesh offset", 110), ("slot", "slot offset", 110)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w" if c == "vehicle" else "center",
                             stretch=(c == "vehicle"))
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.tag_configure("new", foreground="#0a6")
        self.tree.bind("<Double-1>", lambda e: self.on_replace())

        self._build_textures()
        self._build_skel()

        bot = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        bot.pack(fill="both")
        self.log = tk.Text(bot, height=9, wrap="none")
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")

    # ------------------------------------------------------------ textures
    def _build_textures(self):
        page = ttk.Frame(self.nb)
        self.nb.add(page, text="Textures")
        bar = ttk.Frame(page, padding=(0, 4))
        bar.pack(fill="x")
        ttk.Button(bar, text="Export PNG...", command=self.on_tex_export).pack(side="left")
        ttk.Button(bar, text="Export all...", command=self.on_tex_export_all
                   ).pack(side="left", padx=4)
        self.tex_lbl = ttk.Label(bar, text="", foreground="#666")
        self.tex_lbl.pack(side="left", padx=10)

        body = ttk.Frame(page)
        body.pack(fill="both", expand=True)
        cols = ("name", "size", "type", "px")
        self.texv = ttk.Treeview(body, columns=cols, show="headings", selectmode="browse")
        for c, t, w in (("name", "name", 230), ("size", "size", 80),
                        ("type", "type", 150), ("px", "pixel offset", 110)):
            self.texv.heading(c, text=t)
            self.texv.column(c, width=w, anchor="w" if c in ("name", "type") else "center",
                             stretch=(c == "name"))
        sb = ttk.Scrollbar(body, orient="vertical", command=self.texv.yview)
        self.texv.configure(yscrollcommand=sb.set)
        self.texv.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

        side = ttk.Frame(body, width=280)
        side.pack(side="left", fill="y", padx=(8, 0))
        side.pack_propagate(False)
        self.tex_canvas = ttk.Label(side, anchor="center")
        self.tex_canvas.pack(fill="both", expand=True)
        self.tex_info = ttk.Label(side, text="(select a texture)", anchor="center",
                                  foreground="#666")
        self.tex_info.pack(fill="x")
        self._tex_items = {}
        self._tex_photo = None
        self.texv.bind("<<TreeviewSelect>>", lambda e: self.on_tex_select())

    def _tex_list(self):
        if tex is None or not self.tp:
            return []
        try:
            return tex.textures(bytes(self.tp.data), base=self.tp.pck.va)
        except Exception as exc:
            self.say("textures: %s" % exc)
            return []

    def refresh_textures(self):
        self.texv.delete(*self.texv.get_children())
        self._tex_items = {}
        self.tex_canvas.configure(image="")
        self._tex_photo = None
        self.tex_info.configure(text="(select a texture)")
        if tex is None:
            self.tex_lbl.configure(text="(mc3_tex.py not found)")
            return
        items = self._tex_list()
        named = sum(1 for n, _p, _d, _c in items if n)
        self.tex_lbl.configure(
            text="%d texture(s), %d named" % (len(items), named) if items
            else "(no textures found)")
        for nm, px, dim, desc in items:
            if dim:
                w, h = dim
                kind = "4bpp placeholder" if (w, h) == (8, 8) else "8bpp swizzled"
                size = "%dx%d" % (w, h)
            else:
                w = h = 0
                size, kind = "?", "unknown"
            iid = self.texv.insert("", "end",
                                   values=(nm or "(no name)", size, kind, "0x%X" % px))
            self._tex_items[iid] = dict(name=nm, px=px, w=w, h=h)

    def _selected_tex(self):
        sel = self.texv.selection()
        return self._tex_items.get(sel[0]) if sel else None

    def on_tex_select(self):
        e = self._selected_tex()
        if not e or not e["w"]:
            return
        try:
            img = tex.decode_texture(bytes(self.tp.data), e["px"], e["w"], e["h"])
        except Exception as exc:
            self.tex_info.configure(text="decode failed: %s" % exc)
            return
        self.tex_info.configure(text="%s\n%dx%d  %d colors"
                                % (e["name"] or "(no name)", e["w"], e["h"], img.colors))
        try:
            self._tex_photo = tk.PhotoImage(data=self._png_bytes(img))
            z = max(1, min(260 // max(e["w"], 1), 260 // max(e["h"], 1)))
            if z > 1:
                self._tex_photo = self._tex_photo.zoom(z, z)
            self.tex_canvas.configure(image=self._tex_photo)
        except Exception:
            self.tex_canvas.configure(image="")

    @staticmethod
    def _png_bytes(img):
        import base64
        import io
        import tempfile
        fd, p = tempfile.mkstemp(suffix=".png", prefix="mc3tex_")
        os.close(fd)
        try:
            img.save_png(p)
            return base64.b64encode(open(p, "rb").read())
        finally:
            try:
                os.remove(p)
            except OSError:
                pass

    def on_tex_export(self):
        if not self.need():
            return
        e = self._selected_tex()
        if not e or not e["w"]:
            messagebox.showinfo("Select", "Pick a texture from the list.")
            return
        stem = (e["name"] or "tex_%X" % e["px"]).replace(".tex", "")
        p = filedialog.asksaveasfilename(defaultextension=".png",
                                         initialfile="%s.png" % stem,
                                         filetypes=[("PNG", "*.png")])
        if not p:
            return
        tex.decode_texture(bytes(self.tp.data), e["px"], e["w"], e["h"]).save_png(p)
        self.say("texture -> %s" % p)

    def on_tex_export_all(self):
        if not self.need():
            return
        d = filedialog.askdirectory(title="Folder for the PNGs")
        if not d:
            return
        n = 0
        for nm, px, dim, desc in self._tex_list():
            if not dim:
                continue
            stem = (nm or "tex_%X" % px).replace(".tex", "")
            out = os.path.join(d, "%s_%dx%d.png" % (stem, dim[0], dim[1]))
            try:
                tex.decode_texture(bytes(self.tp.data), px, dim[0], dim[1]).save_png(out)
                n += 1
            except Exception as exc:
                self.say("  %s: %s" % (stem, exc))
        self.say("%d texture(s) -> %s" % (n, d))

    # ------------------------------------------------------------ skeleton
    def _build_skel(self):
        page = ttk.Frame(self.nb)
        self.nb.add(page, text="Skel (bones)")
        self.skel_lbl = ttk.Label(page, padding=(0, 4), foreground="#666",
                                  text="One skeleton per vehicle. A piece's id IS its bone id, "
                                       "which is where the part name comes from.")
        self.skel_lbl.pack(fill="x")
        body = ttk.Frame(page)
        body.pack(fill="both", expand=True)
        cols = ("bone", "anchor", "off")
        self.skelv = ttk.Treeview(body, columns=cols, selectmode="browse")
        self.skelv.heading("#0", text="vehicle / bone")
        self.skelv.column("#0", width=280, stretch=True)
        for c, t, w in (("bone", "bone id", 80), ("anchor", "anchor", 220),
                        ("off", "offset", 110)):
            self.skelv.heading(c, text=t)
            self.skelv.column(c, width=w, anchor="center")
        sb = ttk.Scrollbar(body, orient="vertical", command=self.skelv.yview)
        self.skelv.configure(yscrollcommand=sb.set)
        self.skelv.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

    def refresh_skel(self):
        self.skelv.delete(*self.skelv.get_children())
        if not self.tp:
            return
        try:
            sk = self.tp.skeletons()
        except Exception as exc:
            self.skel_lbl.configure(text="skeleton: %s" % exc)
            return
        used = {}
        for pc in self.tp.pieces():
            used.setdefault(pc["vehicle"], set()).add(pc["piece_id"])
        for i, (name, nodes) in enumerate(sk):
            top = self.skelv.insert("", "end", text=name, open=(i == 0),
                                    values=("%d bones" % len(nodes), "", ""))
            for n in nodes:
                mark = " <- piece" if n["bone"] in used.get(name, ()) else ""
                self.skelv.insert(top, "end", text="  %s%s" % (n["name"] or "?", mark),
                                  values=(n["bone"],
                                          "%.2f, %.2f, %.2f" % n["anchor"],
                                          "0x%X" % n["off"]))
        self.skel_lbl.configure(
            text="%d skeleton(s), %d bones each. A piece's id IS its bone id -- that is where "
                 "the part name comes from." % (len(sk), len(sk[0][1]) if sk else 0))

    def say(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        self.root.update_idletasks()

    # ----------------------------------------------------------------- data
    def on_open(self):
        p = filedialog.askopenfilename(
            title="Open a traffic container",
            filetypes=(("Traffic container", "*traffic*.pck"), ("PCK", "*.pck"),
                       ("All", "*.*")))
        if p:
            self.open_file(p)

    def open_file(self, path):
        try:
            tp = traffic.TrafficPck(path)
            pieces = tp.pieces()
        except Exception as exc:
            messagebox.showerror("Error", "Could not open:\n%s" % exc)
            return
        if not pieces:
            messagebox.showwarning(
                "No pieces",
                "No traffic meshes found in this file.\n"
                "Is it a city *_traffic.pck?")
        self.tp = tp
        self.path = path
        self.dirty = False
        self._replaced = set()
        self.say("opened: %s  (VA %#x, %s, %d pieces)"
                 % (os.path.basename(path), tp.pck.va, human(len(tp.data)), len(pieces)))
        self._show_shaders()
        self.refresh()

    def _show_shaders(self):
        """List the container's shaders so the index to force is an informed
        choice rather than a guess."""
        names = []
        try:
            shading = _load("mc3_shadinggroup")
            names = shading.extract_shadinggroup(self.path)[1]
        except Exception:
            pass
        n = len(names)
        self.shader_lbl.configure(
            text=("this file has %d shader(s): valid indices 0..%d" % (n, n - 1))
            if n else "(could not read the shader table)")
        if names:
            self.say("shaders in this container (%d):" % n)
            for i, nm in enumerate(names):
                self.say("    %2d = %s" % (i, nm.replace(".shadert", "")))

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        if not self.tp:
            return
        pieces = self.tp.pieces(refresh=True)
        for pc in pieces:
            tag = ("new",) if pc["index"] in getattr(self, "_replaced", ()) else ()
            self.tree.insert("", "end", values=(
                pc["index"], pc["vehicle"], pc["part"], pc["piece_id"], pc["groups"],
                human(pc["size"]), "0x%06X" % pc["off"], "0x%06X" % pc["slot"]),
                tags=tag)
        self.refresh_textures()
        self.refresh_skel()
        self.info.configure(text="%s   |   %s   |   %d pieces   |   %s" % (
            os.path.basename(self.path), human(len(self.tp.data)), len(pieces),
            "MODIFIED" if self.dirty else "saved"))

    def selected(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return int(self.tree.item(sel[0], "values")[0])

    def need(self):
        if not self.tp:
            messagebox.showinfo("Open a file", "Open a traffic container first.")
            return False
        return True

    # -------------------------------------------------------------- actions
    def _as_mesh_pck(self, path):
        """Return a .mesh.pck path for `path`, converting when needed.

        With "traffic style" on, even a .mesh.pck is re-encoded, because a
        vehicle packet carries 3 culling floats that a traffic packet does not
        have -- retail traffic uses tag 98000260 and puts the vertex count at
        +0x08, a vehicle uses 9800026C and puts it at +0x14.
        """
        low = path.lower()
        style = "traffic" if self.traffic_blocks.get() else "vehicle"
        native = low.endswith(".mesh.pck") or (low.endswith(".pck")
                                               and "traffic" not in low)
        if native and style == "vehicle":
            return path, None
        if convert is None:
            raise RuntimeError("mc3_mesh_convert.py is needed to import %s"
                               % os.path.splitext(path)[1])
        import tempfile
        m = convert.load(path)
        fd, tmp = tempfile.mkstemp(suffix=".mesh.pck", prefix="mc3traffic_")
        os.close(fd)
        convert.write_pck(m, tmp, block_style=style)
        return tmp, tmp

    def on_replace(self):
        if not self.need():
            return
        i = self.selected()
        if i is None:
            messagebox.showinfo("Select", "Pick a piece from the list.")
            return
        pc = self.tp.pieces()[i]
        f = filedialog.askopenfilename(
            title="Mesh to put in place of '%s'" % pc["label"],
            filetypes=(("Any mesh the converter reads",
                        "*.mesh.pck *.pck *.obj *.mesh *.psppck *.xbck"),
                       ("All", "*.*")))
        if not f:
            return
        tmp = None
        try:
            src, tmp = self._as_mesh_pck(f)
            if tmp:
                    self.say("re-encoded %s -> %s-style packets"
                         % (os.path.basename(f),
                            "traffic" if self.traffic_blocks.get() else "vehicle"))
            mat = None
            if self.force_mat.get():
                try:
                    mat = int(self.mat_var.get(), 0)
                except ValueError:
                    raise ValueError("shader index must be a number")
                nsh = self.tp.shader_count()
                if nsh and mat >= nsh:
                    raise ValueError(
                        "shader index %d is past the end: this container has %d "
                        "(valid 0..%d), and the game hangs on an out-of-range "
                        "index" % (mat, nsh, nsh - 1))
            before = len(self.tp.data)
            va, nfix, blen = self.tp.replace(i, src, material=mat)
        except Exception as exc:
            messagebox.showerror("Replace failed", str(exc))
            return
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        self.dirty = True
        self._replaced.add(i)
        self.say("[%d] %s <- %s   appended %s at VA %#x (%d pointers rebased); "
                 "file %s -> %s" % (i, pc["label"], os.path.basename(f),
                                    human(blen), va, nfix,
                                    human(before), human(len(self.tp.data))))
        self.say("    kept piece id %d; only the slot pointer "
                 "and the header size changed" % pc["piece_id"])
        if self.force_mat.get():
            self.say("    every group forced to shader index %s" % self.mat_var.get())
        else:
            self.say("    WARNING: donor shader indices kept as-is (%s) -- an index "
                     "past this container's shader count hangs the game"
                     % self.tp.materials(self.tp.pck.F(va)))
        self.refresh()

    def on_extract(self):
        if not self.need():
            return
        i = self.selected()
        if i is None:
            messagebox.showinfo("Select", "Pick a piece from the list.")
            return
        pc = self.tp.pieces()[i]
        stem = "%s_part%s" % (pc["vehicle"].strip("()").replace(" ", "_"),
                              pc["part_index"] if pc["part_index"] is not None else i)
        p = filedialog.asksaveasfilename(
            title="Extract piece", initialfile=stem + ".mesh.pck",
            defaultextension=".mesh.pck",
            filetypes=(("MC3 mesh", "*.mesh.pck"), ("OBJ", "*.obj"),
                       ("Angel Studios mesh", "*.mesh"), ("All", "*.*")))
        if not p:
            return
        try:
            import tempfile
            fd, tmp = tempfile.mkstemp(suffix=".mesh.pck")
            os.close(fd)
            self.tp.extract(i, tmp)
            low = p.lower()
            if low.endswith(".mesh.pck") or low.endswith(".pck"):
                import shutil
                shutil.move(tmp, p)
            else:
                if convert is None:
                    raise RuntimeError("mc3_mesh_convert.py is needed for that format")
                convert.save(convert.load(tmp), p)
                os.remove(tmp)
        except Exception as exc:
            messagebox.showerror("Extract failed", str(exc))
            return
        self.say("extracted [%d] %s -> %s" % (i, stem, p))
        self.say("    note: the material table can live in the container's shared "
                 "area, so an extracted piece may come back as a single group")

    def on_view(self):
        if not self.need():
            return
        if view is None:
            messagebox.showinfo("Viewer unavailable",
                                "mc3_view.py could not be loaded (it needs matplotlib).")
            return
        i = self.selected()
        if i is None:
            messagebox.showinfo("Select", "Pick a piece from the list.")
            return
        pc = self.tp.pieces()[i]
        try:
            import tempfile
            fd, tmp = tempfile.mkstemp(suffix=".mesh.pck", prefix="mc3tv_")
            os.close(fd)
            self.tp.extract(i, tmp)
            groups, plat = view.parse_any(tmp)
        except Exception as exc:
            messagebox.showerror("Viewer error", str(exc))
            return
        if not groups:
            messagebox.showinfo("Nothing to show", "No geometry in that piece.")
            return
        self.say("3D view: [%d] %s (%d group(s))" % (i, pc["label"], len(groups)))
        try:
            view.show_native(groups, pc["label"], plat)
        except Exception as exc:
            messagebox.showerror("Viewer error", str(exc))

    # ---------------------------------------------------------------- save
    def on_save(self):
        if not self.need():
            return
        if not messagebox.askyesno("Save", "Overwrite\n%s ?" % self.path):
            return
        self._write(self.path)

    def on_save_as(self):
        if not self.need():
            return
        p = filedialog.asksaveasfilename(
            defaultextension=".pck", initialfile=os.path.basename(self.path),
            filetypes=(("PCK", "*.pck"), ("All", "*.*")))
        if p:
            self._write(p)

    def _write(self, path):
        try:
            n = self.tp.save(path)
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
