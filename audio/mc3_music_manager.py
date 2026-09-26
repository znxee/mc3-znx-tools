#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MC3 Music Manager - Tkinter frontend (backed by mc3_music_core.py).

This is the main/maintained GUI.  A Dear PyGui build is kept alongside as
mc3_music_manager_dpg.py.  All backend logic (streams.dat / .play / .strtbl)
lives in mc3_music_core.py, so both frontends share the same validated code.

Panels:
  Left   : converted music library (RSTM output) - add tracks / add folder / replace
  Middle : insertion plan (multi-select, Select all) + edit form, including
           strtbl display-text formatting (custom multi-line text, font, size,
           X/Y scale, PS2 button glyphs)
  Right  : current in-game library (streams.dat + strtbl names)

Usage:  python mc3_music_manager.py
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import winsound
except ImportError:
    winsound = None

import mc3_music_core as core
from mc3_music_core import (
    CITIES, MAX_NAME_LEN, RSTM_OUT, SCRATCH, VGMSTREAM, PROJECT_FILE,
    STREAMS_DAT, FONTS_DIR, GLYPHS, DEFAULT_FONT, DEFAULT_SIZE, DEFAULT_SCALE,
    DEFAULT_TARGETS, LIMIT_GIB, fmt_dur, rsm_info, sanitize_name, sanitize_genre,
    targets_summary, default_fmt, display_text,
)


class MusicManager:
    BG = "#15161A"
    PANEL = "#1D1F26"
    FG = "#D7DAE0"
    MUT = "#8B8F98"
    ACC = "#7EC8FF"

    def __init__(self, root):
        self.root = root
        root.title("MC3 Music Manager")
        root.geometry("1600x900")
        root.configure(bg=self.BG)
        self._style()

        SCRATCH.mkdir(parents=True, exist_ok=True)

        self.lib = core.GameLibrary()
        self.plan = []
        self.disabled = set()
        self.genres = list(core.GENRES)
        self.fonts = core.list_fonts()
        self._busy = False
        self._log_lines = []
        self._log_lock = threading.Lock()

        self._build_ui()
        self.root.after(50, self._startup)
        self.root.after(200, self._drain_log)

    # ------------------------------------------------------------------ UI --
    def _style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=self.BG, foreground=self.FG,
                     fieldbackground=self.PANEL, bordercolor="#2A2D35")
        st.configure("Panel.TFrame", background=self.PANEL)
        st.configure("TFrame", background=self.BG)
        st.configure("TLabel", background=self.BG, foreground=self.FG)
        st.configure("Muted.TLabel", background=self.BG, foreground=self.MUT)
        st.configure("Panel.TLabel", background=self.PANEL, foreground=self.FG)
        st.configure("PanelMuted.TLabel", background=self.PANEL, foreground=self.MUT)
        st.configure("Accent.TLabel", background=self.PANEL, foreground=self.ACC,
                     font=("Segoe UI", 10, "bold"))
        st.configure("Treeview", background=self.PANEL, foreground=self.FG,
                     fieldbackground=self.PANEL, rowheight=21)
        st.configure("Treeview.Heading", background="#242730", foreground=self.FG)
        st.map("Treeview", background=[("selected", "#2F5A78")])
        st.configure("Panel.TCheckbutton", background=self.PANEL, foreground=self.FG)
        st.map("Panel.TCheckbutton", background=[("active", self.PANEL)])
        # readonly comboboxes render their value with the *selection* colors under
        # the clam theme; without this the text is invisible on the dark field.
        st.configure("TCombobox", foreground=self.FG, background=self.PANEL,
                     fieldbackground=self.PANEL, arrowcolor=self.FG,
                     selectbackground=self.PANEL, selectforeground=self.FG)
        st.map("TCombobox",
               fieldbackground=[("readonly", self.PANEL)],
               foreground=[("readonly", self.FG)],
               selectbackground=[("readonly", self.PANEL)],
               selectforeground=[("readonly", self.FG)])
        st.configure("TButton", padding=4)

    def _build_ui(self):
        # ---- toolbar
        bar = ttk.Frame(self.root, style="Panel.TFrame")
        bar.pack(fill=tk.X)
        ttk.Button(bar, text="Open project", command=self.cb_open_project).pack(side=tk.LEFT, padx=4, pady=4)
        ttk.Button(bar, text="Save project", command=self.cb_save_project).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="Export final files...", command=self.cb_export).pack(side=tk.LEFT, padx=16)
        ttk.Button(bar, text="Restore backups (.bak)", command=self.cb_restore).pack(side=tk.LEFT, padx=4)
        self.drop_disabled_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Drop disabled music (reclaim space)",
                        variable=self.drop_disabled_var, style="Panel.TCheckbutton",
                        command=self._update_budget).pack(side=tk.LEFT, padx=12)
        ttk.Button(bar, text="Stop audio", command=self.stop_audio).pack(side=tk.RIGHT, padx=4)
        self.budget_lbl = ttk.Label(bar, text="", style="Accent.TLabel")
        self.budget_lbl.pack(side=tk.RIGHT, padx=10)

        main = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # ---- left: converted
        left = ttk.Frame(main, style="Panel.TFrame")
        main.add(left, weight=3)
        ttk.Label(left, text="Converted music (RSTM output)", style="Accent.TLabel").pack(anchor="w", padx=8, pady=(8, 2))
        srch = ttk.Frame(left, style="Panel.TFrame"); srch.pack(fill=tk.X, padx=8)
        ttk.Label(srch, text="Filter:", style="Panel.TLabel").pack(side=tk.LEFT)
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *a: self.refresh_converted())
        ttk.Entry(srch, textvariable=self.filter_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        body = ttk.Frame(left, style="Panel.TFrame"); body.pack(fill=tk.BOTH, expand=True)
        # reserve the button column on the right first so it never gets clipped
        lb = ttk.Frame(body, style="Panel.TFrame"); lb.pack(side=tk.RIGHT, fill=tk.Y, padx=6, pady=4)
        self.conv_tree = ttk.Treeview(body, columns=("dur",), show="tree headings", selectmode="extended")
        self.conv_tree.heading("#0", text="File"); self.conv_tree.heading("dur", text="Dur")
        self.conv_tree.column("#0", width=250); self.conv_tree.column("dur", width=50, anchor="e", stretch=False)
        sb1 = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.conv_tree.yview)
        self.conv_tree.configure(yscrollcommand=sb1.set)
        sb1.pack(side=tk.RIGHT, fill=tk.Y, pady=4)
        self.conv_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=4)
        ttk.Button(lb, text="Preview", command=self.cb_play_converted).pack(fill=tk.X, pady=2)
        ttk.Button(lb, text="Add tracks >", command=self.cb_add_tracks).pack(fill=tk.X, pady=2)
        ttk.Button(lb, text="Add folder >", command=self.cb_add_folder).pack(fill=tk.X, pady=2)
        ttk.Button(lb, text="Replace >", command=self.cb_replace).pack(fill=tk.X, pady=2)
        ttk.Label(lb, text="Add folder: adds every\ntrack of the selected\ngroup with one genre\n(new genres allowed).",
                  style="PanelMuted.TLabel", justify=tk.LEFT).pack(pady=6)

        # ---- middle: plan + form
        mid = ttk.Frame(main, style="Panel.TFrame")
        main.add(mid, weight=5)
        ttk.Label(mid, text="Insertion plan", style="Accent.TLabel").pack(anchor="w", padx=8, pady=(8, 2))
        pbar = ttk.Frame(mid, style="Panel.TFrame"); pbar.pack(fill=tk.X, padx=8)
        ttk.Button(pbar, text="Select all", command=self.cb_select_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(pbar, text="Clear selection", command=self.cb_clear_sel).pack(side=tk.LEFT, padx=2)
        ttk.Button(pbar, text="Remove selected", command=self.cb_remove_items).pack(side=tk.LEFT, padx=2)
        ttk.Button(pbar, text="Preview", command=self.cb_play_plan).pack(side=tk.LEFT, padx=8)
        self.plan_tree = ttk.Treeview(mid, columns=("mode", "genre", "title", "artist", "where"),
                                      show="tree headings", selectmode="extended", height=11)
        for c, txt, w in [("#0", "Internal name", 170), ("mode", "Mode", 70), ("genre", "Genre", 90),
                          ("title", "Title", 150), ("artist", "Artist", 120), ("where", "Playlists", 150)]:
            self.plan_tree.heading(c, text=txt)
            self.plan_tree.column(c, width=w, stretch=(c in ("title", "where")))
        self.plan_tree.bind("<<TreeviewSelect>>", lambda e: self.load_form())
        self.plan_tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=2)

        self._build_form(mid)

        # ---- right: game
        right = ttk.Frame(main, style="Panel.TFrame")
        main.add(right, weight=4)
        ttk.Label(right, text="In-game library (streams.dat)", style="Accent.TLabel").pack(anchor="w", padx=8, pady=(8, 2))
        rbody = ttk.Frame(right, style="Panel.TFrame"); rbody.pack(fill=tk.BOTH, expand=True)
        self.game_tree = ttk.Treeview(rbody, columns=("name", "dur"), show="tree headings")
        self.game_tree.heading("#0", text="File"); self.game_tree.heading("name", text="In-game name")
        self.game_tree.heading("dur", text="Dur")
        self.game_tree.column("#0", width=240); self.game_tree.column("name", width=250)
        self.game_tree.column("dur", width=50, anchor="e", stretch=False)
        self.game_tree.tag_configure("replaced", foreground="#E6C85C")
        self.game_tree.tag_configure("disabled", foreground="#666A72")
        self.game_tree.tag_configure("new", foreground="#9DFF8F")
        sb3 = ttk.Scrollbar(rbody, orient=tk.VERTICAL, command=self.game_tree.yview)
        self.game_tree.configure(yscrollcommand=sb3.set)
        self.game_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=4)
        sb3.pack(side=tk.LEFT, fill=tk.Y, pady=4)
        gb = ttk.Frame(rbody, style="Panel.TFrame"); gb.pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=4)
        ttk.Button(gb, text="Preview", command=self.cb_play_game).pack(fill=tk.X, pady=2)
        ttk.Button(gb, text="Enable/Disable\nin playlists", command=self.cb_toggle_disabled).pack(fill=tk.X, pady=2)

        # ---- log
        logf = ttk.Frame(self.root, style="Panel.TFrame"); logf.pack(fill=tk.X, side=tk.BOTTOM)
        self.log_text = tk.Text(logf, height=6, bg="#101114", fg=self.MUT,
                                insertbackground=self.FG, relief=tk.FLAT, wrap="none")
        self.log_text.pack(fill=tk.X, padx=4, pady=2)

    def _build_form(self, parent):
        form = ttk.Frame(parent, style="Panel.TFrame"); form.pack(fill=tk.X, padx=8, pady=4)
        self.f_name = tk.StringVar(); self.f_title = tk.StringVar(); self.f_artist = tk.StringVar()
        self.f_genre = tk.StringVar(value="Rock"); self.f_genre_new = tk.StringVar()
        self.f_winlose = tk.StringVar(value="win")
        self.f_cities = {c: tk.BooleanVar(value=True) for c in CITIES}
        self.f_t = {k: tk.BooleanVar(value=v) for k, v in DEFAULT_TARGETS.items()}
        # formatting vars
        self.f_fmt_custom = tk.BooleanVar(value=False)
        self.f_fmt_font = tk.StringVar(value=DEFAULT_FONT)
        self.f_fmt_size = tk.IntVar(value=DEFAULT_SIZE)
        self.f_fmt_sx = tk.DoubleVar(value=DEFAULT_SCALE[0])
        self.f_fmt_sy = tk.DoubleVar(value=DEFAULT_SCALE[1])

        r0 = ttk.Frame(form, style="Panel.TFrame"); r0.pack(fill=tk.X, pady=1)
        ttk.Label(r0, text=f"Internal name (max {MAX_NAME_LEN}):", style="Panel.TLabel").pack(side=tk.LEFT)
        ttk.Entry(r0, textvariable=self.f_name, width=26).pack(side=tk.LEFT, padx=4)
        self.name_count = ttk.Label(r0, text="", style="PanelMuted.TLabel"); self.name_count.pack(side=tk.LEFT)
        self.f_name.trace_add("write", lambda *a: self._name_count())

        r1 = ttk.Frame(form, style="Panel.TFrame"); r1.pack(fill=tk.X, pady=1)
        ttk.Label(r1, text="Genre:", style="Panel.TLabel").pack(side=tk.LEFT)
        self.genre_cb = ttk.Combobox(r1, textvariable=self.f_genre, values=self.genres, width=14)
        self.genre_cb.pack(side=tk.LEFT, padx=4)
        ttk.Entry(r1, textvariable=self.f_genre_new, width=16).pack(side=tk.LEFT, padx=2)
        ttk.Label(r1, text="(or type new)", style="PanelMuted.TLabel").pack(side=tk.LEFT)
        ttk.Label(r1, text="Win/Lose:", style="Panel.TLabel").pack(side=tk.LEFT, padx=(10, 0))
        ttk.Combobox(r1, textvariable=self.f_winlose, values=["win", "lose"], width=6,
                     state="readonly").pack(side=tk.LEFT, padx=4)

        r2 = ttk.Frame(form, style="Panel.TFrame"); r2.pack(fill=tk.X, pady=1)
        ttk.Label(r2, text="Title:", style="Panel.TLabel").pack(side=tk.LEFT)
        ttk.Entry(r2, textvariable=self.f_title, width=34).pack(side=tk.LEFT, padx=4)
        ttk.Label(r2, text="Artist:", style="Panel.TLabel").pack(side=tk.LEFT)
        ttk.Entry(r2, textvariable=self.f_artist, width=26).pack(side=tk.LEFT, padx=4)

        r3 = ttk.Frame(form, style="Panel.TFrame"); r3.pack(fill=tk.X, pady=1)
        ttk.Label(r3, text="Cities:", style="Panel.TLabel").pack(side=tk.LEFT)
        for c in CITIES:
            ttk.Checkbutton(r3, text=c, variable=self.f_cities[c], style="Panel.TCheckbutton").pack(side=tk.LEFT, padx=2)

        r4 = ttk.Frame(form, style="Panel.TFrame"); r4.pack(fill=tk.X, pady=1)
        ttk.Label(r4, text="Playlists:", style="Panel.TLabel").pack(side=tk.LEFT)
        ttk.Checkbutton(r4, text="city (used)", variable=self.f_t["city"], style="Panel.TCheckbutton").pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(r4, text="garage (used)", variable=self.f_t["garage"], style="Panel.TCheckbutton").pack(side=tk.LEFT, padx=2)
        ttk.Checkbutton(r4, text="genre race (rare)", variable=self.f_t["race"], style="Panel.TCheckbutton").pack(side=tk.LEFT, padx=8)
        ttk.Checkbutton(r4, text="race editor (rare)", variable=self.f_t["raceeditor"], style="Panel.TCheckbutton").pack(side=tk.LEFT, padx=2)

        # ---- display text formatting (strtbl) ----
        fmt = ttk.LabelFrame(form, text="Display text formatting (strtbl)", style="Panel.TFrame")
        fmt.pack(fill=tk.X, pady=(6, 2))
        fr0 = ttk.Frame(fmt, style="Panel.TFrame"); fr0.pack(fill=tk.X, pady=1)
        ttk.Checkbutton(fr0, text='Custom text  (otherwise: "Title" \\n by Artist)',
                        variable=self.f_fmt_custom, style="Panel.TCheckbutton").pack(side=tk.LEFT)
        self.fmt_text = tk.Text(fmt, height=3, width=70, bg="#101114", fg=self.FG,
                                insertbackground=self.FG, relief=tk.FLAT, wrap="word")
        self.fmt_text.pack(fill=tk.X, padx=4, pady=2)
        fr1 = ttk.Frame(fmt, style="Panel.TFrame"); fr1.pack(fill=tk.X, pady=1)
        ttk.Label(fr1, text="Insert:", style="Panel.TLabel").pack(side=tk.LEFT)
        for label, ch in GLYPHS:
            ttk.Button(fr1, text=label, width=4, command=lambda c=ch: self._insert_glyph(c)).pack(side=tk.LEFT, padx=1)
        fr2 = ttk.Frame(fmt, style="Panel.TFrame"); fr2.pack(fill=tk.X, pady=1)
        ttk.Label(fr2, text="Font:", style="Panel.TLabel").pack(side=tk.LEFT)
        ttk.Combobox(fr2, textvariable=self.f_fmt_font, values=self.fonts, width=16, state="readonly").pack(side=tk.LEFT, padx=4)
        ttk.Label(fr2, text="Size:", style="Panel.TLabel").pack(side=tk.LEFT)
        ttk.Spinbox(fr2, from_=0, to=255, textvariable=self.f_fmt_size, width=5).pack(side=tk.LEFT, padx=4)
        ttk.Label(fr2, text="Scale X/Y:", style="Panel.TLabel").pack(side=tk.LEFT)
        ttk.Entry(fr2, textvariable=self.f_fmt_sx, width=6).pack(side=tk.LEFT, padx=2)
        ttk.Entry(fr2, textvariable=self.f_fmt_sy, width=6).pack(side=tk.LEFT, padx=2)
        ttk.Label(fmt, text="Retail music uses smallspace / size 15 / scale 1.0. "
                            "Other values are experimental - test in game.",
                  style="PanelMuted.TLabel", wraplength=620).pack(anchor="w", padx=4)

        r5 = ttk.Frame(form, style="Panel.TFrame"); r5.pack(fill=tk.X, pady=4)
        ttk.Button(r5, text="Apply to selected", command=self.cb_apply_form).pack(side=tk.LEFT, padx=2)
        ttk.Label(r5, text="(name/title/artist apply to one item; genre/cities/playlists/format apply to all selected)",
                  style="PanelMuted.TLabel").pack(side=tk.LEFT, padx=6)

    # ----------------------------------------------------------------- log --
    def log(self, msg):
        with self._log_lock:
            self._log_lines.append(str(msg))

    def _drain_log(self):
        with self._log_lock:
            lines, self._log_lines = self._log_lines, []
        if lines:
            self.log_text.insert(tk.END, "\n".join(lines) + "\n")
            self.log_text.see(tk.END)
        self.root.after(200, self._drain_log)

    def _refresh_genres(self):
        self.genre_cb.configure(values=self.genres)

    def _update_budget(self):
        """Refresh the streams.dat size meter against the game's size limit."""
        if not self.lib.dat_index:
            return
        try:
            rep = core.budget_report(self.plan, self.disabled, self.lib)
        except Exception:
            return
        drop = self.drop_disabled_var.get()
        size = rep["size_dropped"] if drop else rep["size"]
        over = rep["over_dropped"] if drop else rep["over"]
        gib = size / 2**30
        txt = f"streams.dat: {gib:.2f} / {LIMIT_GIB:.2f} GiB"
        if over > 0:
            txt += f"  (OVER by {over/2**20:.0f} MiB!)"
            if not drop and rep["drop_savings"] > 0:
                txt += f"  drop-disabled saves {rep['drop_savings']/2**20:.0f} MiB"
            color = "#FF7E7E"
        elif gib > LIMIT_GIB * 0.95:
            color = "#E6C85C"
        else:
            color = self.ACC
        self.budget_lbl.configure(text=txt, foreground=color)

    def _name_count(self):
        n = len(self.f_name.get())
        over = n > MAX_NAME_LEN
        self.name_count.configure(text=f" {n}/{MAX_NAME_LEN}" + ("  too long!" if over else ""),
                                  foreground="#FF7E7E" if over else self.MUT)

    def _insert_glyph(self, ch):
        self.fmt_text.insert(tk.INSERT, ch)
        self.f_fmt_custom.set(True)

    # ------------------------------------------------------------- startup --
    def _startup(self):
        try:
            self.lib.load(log=self.log)
            self.genres = list(self.lib.genres)
            self._refresh_genres()
        except Exception as e:
            messagebox.showerror("Error", f"Failed reading streams.dat/.lst:\n{e}")
        self.refresh_converted(); self.refresh_game()
        if PROJECT_FILE.exists():
            try:
                self.plan, self.disabled = core.load_project()
                for it in self.plan:
                    if it["genre"] not in self.genres:
                        self.genres.append(it["genre"])
                self._refresh_genres()
                self.refresh_plan(); self.refresh_game()
                self.log(f"Project loaded: {PROJECT_FILE.name}")
            except Exception as e:
                self.log(f"Warning: project not loaded ({e})")
        self._update_budget()
        self.log("Ready.")

    # ----------------------------------------------------- left panel --
    def refresh_converted(self):
        flt = self.filter_var.get().lower() if hasattr(self, "filter_var") else ""
        t = self.conv_tree; t.delete(*t.get_children())
        if not RSTM_OUT.exists():
            return
        groups = {}
        for rsm in sorted(RSTM_OUT.rglob("*.rsm")):
            rel = rsm.relative_to(RSTM_OUT)
            if flt and flt not in str(rel).lower():
                continue
            groups.setdefault(str(rel.parent), []).append(rsm)
        for gname in sorted(groups):
            node = t.insert("", tk.END, text=f"{gname}  ({len(groups[gname])})", open=bool(flt))
            for rsm in groups[gname]:
                _, _, _, dur = rsm_info(rsm)
                t.insert(node, tk.END, text=rsm.stem, values=(fmt_dur(dur),), iid=str(rsm))

    def _sel_converted(self):
        for iid in self.conv_tree.selection():
            p = Path(iid)
            if p.suffix.lower() == ".rsm" and p.exists():
                yield p

    def _sel_group_files(self):
        files = []
        for iid in self.conv_tree.selection():
            p = Path(iid)
            if p.suffix.lower() == ".rsm" and p.exists():
                files.append(p)
            else:
                for child in self.conv_tree.get_children(iid):
                    cp = Path(child)
                    if cp.suffix.lower() == ".rsm" and cp.exists():
                        files.append(cp)
        return files

    # -------------------------------------------------------------- plan --
    def cb_add_tracks(self):
        srcs = list(self._sel_converted())
        if not srcs:
            messagebox.showinfo("Add tracks", "Select one or more .rsm files on the left.")
            return
        for rsm in srcs:
            self.plan.append(core.make_item(rsm, self.plan))
        self.refresh_plan(); self.refresh_game()
        self.log(f"{len(srcs)} track(s) added to the plan.")

    def cb_add_folder(self):
        files = self._sel_group_files()
        if not files:
            messagebox.showinfo("Add folder", "Select a folder (group) on the left first.")
            return
        rels = {str(Path(f).parent.relative_to(RSTM_OUT)) for f in files}
        suggest = sanitize_genre(Path(next(iter(rels))).name) if len(rels) == 1 else "Custom"
        genre = self._ask_genre(f"Genre for these {len(files)} track(s):", suggest)
        if not genre:
            return
        genre = sanitize_genre(genre)
        if genre not in self.genres:
            self.genres.append(genre); self._refresh_genres()
        for rsm in files:
            self.plan.append(core.make_item(rsm, self.plan, genre=genre))
        self.refresh_plan(); self.refresh_game()
        self.log(f"Added folder: {len(files)} track(s) as genre '{genre}'.")

    def _ask_genre(self, prompt, default):
        dlg = tk.Toplevel(self.root); dlg.title("Genre"); dlg.configure(bg=self.PANEL)
        dlg.transient(self.root); dlg.grab_set()
        ttk.Label(dlg, text=prompt, style="Panel.TLabel").pack(padx=14, pady=(14, 4))
        var = tk.StringVar(value=default)
        cb = ttk.Combobox(dlg, textvariable=var, values=self.genres, width=28); cb.pack(padx=14, pady=4); cb.focus_set()
        result = {"value": None}
        row = ttk.Frame(dlg, style="Panel.TFrame"); row.pack(pady=10)
        def ok(*_): result["value"] = var.get().strip(); dlg.destroy()
        def cancel(*_): dlg.destroy()
        ttk.Button(row, text="OK", command=ok).pack(side=tk.LEFT, padx=6)
        ttk.Button(row, text="Cancel", command=cancel).pack(side=tk.LEFT, padx=6)
        dlg.bind("<Return>", ok); dlg.bind("<Escape>", cancel)
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_width()) // 2
        dlg.geometry(f"+{x}+{self.root.winfo_rooty() + 160}")
        self.root.wait_window(dlg)
        return result["value"]

    def cb_replace(self):
        srcs = list(self._sel_converted()); tgt = self._sel_game_track()
        if not srcs or not tgt:
            messagebox.showinfo("Replace", "Select a converted track (left) and the "
                                           "in-game track to replace (right).")
            return
        info = self.lib.game_tracks[tgt]
        item = core.make_item(srcs[0], self.plan, mode="replace", genre=info["genre"], target=tgt)
        item["name"] = Path(tgt).stem
        self.plan.append(item)
        self.refresh_plan(); self.refresh_game()
        self.log(f"Replacement planned: {tgt} <- {srcs[0].name}")

    def refresh_plan(self):
        t = self.plan_tree; t.delete(*t.get_children())
        for i, it in enumerate(self.plan):
            mode = "ADD" if it["mode"] == "add" else "REPLACE"
            t.insert("", tk.END, iid=str(i), text=it["name"],
                     values=(mode, it["genre"], it["title"], it["artist"], targets_summary(it)))
        self._update_budget()

    def _plan_sel(self):
        return [int(i) for i in self.plan_tree.selection()]

    def cb_select_all(self):
        self.plan_tree.selection_set([str(i) for i in range(len(self.plan))])

    def cb_clear_sel(self):
        self.plan_tree.selection_remove(self.plan_tree.selection())

    def load_form(self):
        idx = self._plan_sel()
        if not idx:
            return
        it = self.plan[idx[0]]
        self.f_name.set(it["name"]); self._name_count()
        self.f_title.set(it["title"]); self.f_artist.set(it["artist"])
        self.f_genre.set(it["genre"]); self.f_genre_new.set("")
        self.f_winlose.set(it.get("winlose", "win"))
        for c in CITIES:
            self.f_cities[c].set(c in it["cities"])
        tg = it.get("targets", DEFAULT_TARGETS)
        for k in self.f_t:
            self.f_t[k].set(tg.get(k, DEFAULT_TARGETS[k]))
        f = it.get("fmt") or default_fmt()
        self.f_fmt_custom.set(f.get("custom", False))
        self.fmt_text.delete("1.0", tk.END)
        self.fmt_text.insert("1.0", f.get("text", "") or display_text(it))
        self.f_fmt_font.set(f.get("font", DEFAULT_FONT))
        self.f_fmt_size.set(int(f.get("size", DEFAULT_SIZE)))
        sc = f.get("scale", DEFAULT_SCALE)
        self.f_fmt_sx.set(sc[0]); self.f_fmt_sy.set(sc[1])
        self.genre_cb.configure(state="disabled" if it["mode"] == "replace" else "normal")

    def cb_apply_form(self):
        idx = self._plan_sel()
        if not idx:
            messagebox.showinfo("Apply", "Select one or more plan items first.")
            return
        new_g = sanitize_genre(self.f_genre_new.get()) if self.f_genre_new.get().strip() else ""
        genre = new_g or self.f_genre.get() or "Rock"
        if genre not in self.genres:
            self.genres.append(genre); self._refresh_genres()
        cities = [c for c in CITIES if self.f_cities[c].get()]
        targets = {k: self.f_t[k].get() for k in self.f_t}
        try:
            sx, sy = float(self.f_fmt_sx.get()), float(self.f_fmt_sy.get())
        except (tk.TclError, ValueError):
            sx, sy = 1.0, 1.0
        fmt = {"custom": self.f_fmt_custom.get(),
               "text": self.fmt_text.get("1.0", "end-1c"),
               "font": self.f_fmt_font.get() or DEFAULT_FONT,
               "size": int(self.f_fmt_size.get()),
               "scale": [round(sx, 3), round(sy, 3)]}
        single = len(idx) == 1
        for i in idx:
            it = self.plan[i]
            if single:
                clean = sanitize_name(self.f_name.get().strip(), limit=10_000) or "Track"
                it["name"] = core.unique_name(clean[:MAX_NAME_LEN], self.plan, exclude_idx=i)
                if len(clean) > MAX_NAME_LEN:
                    self.log(f"Name truncated to {MAX_NAME_LEN} chars: {it['name']}")
                it["title"] = self.f_title.get().strip() or it["name"]
                it["artist"] = self.f_artist.get().strip() or "?"
            if it["mode"] == "add":
                it["genre"] = genre
            it["winlose"] = self.f_winlose.get()
            it["cities"] = cities
            it["targets"] = dict(targets)
            it["fmt"] = dict(fmt)
        self.refresh_plan(); self.refresh_game()
        self.log(f"Applied to {len(idx)} item(s).")

    def cb_remove_items(self):
        idx = self._plan_sel()
        for i in sorted(idx, reverse=True):
            self.plan.pop(i)
        if idx:
            self.refresh_plan(); self.refresh_game()
            self.log(f"Removed {len(idx)} item(s) from the plan.")

    # ------------------------------------------------------ right panel --
    def refresh_game(self):
        t = self.game_tree; t.delete(*t.get_children())
        replaced = {it["target"] for it in self.plan if it["mode"] == "replace"}
        by_genre = {}
        for name, info in self.lib.game_tracks.items():
            by_genre.setdefault(info["genre"], []).append(name)
        for it in self.plan:
            if it["mode"] == "add":
                by_genre.setdefault(it["genre"], []).append(("__new__", it))
        for genre in sorted(by_genre):
            node = t.insert("", tk.END, text=genre, open=False)
            for entry in sorted(by_genre[genre],
                                key=lambda e: e[1]["name"] if isinstance(e, tuple) else e):
                if isinstance(entry, tuple):
                    it = entry[1]
                    t.insert(node, tk.END, text=f"{it['name']}.rsm",
                             values=(f'"{it["title"]}" by {it["artist"]}', "-"), tags=("new",))
                    continue
                name = entry; info = self.lib.game_tracks[name]; tags = []
                if name in replaced:
                    tags.append("replaced")
                if info["play_path"].lower() in self.disabled:
                    tags.append("disabled")
                t.insert(node, tk.END, iid=name, text=Path(name).name,
                         values=(info["display"], fmt_dur(info["dur"])), tags=tuple(tags))

    def _sel_game_track(self):
        for iid in self.game_tree.selection():
            if iid in self.lib.game_tracks:
                return iid
        return None

    def cb_toggle_disabled(self):
        name = self._sel_game_track()
        if not name:
            return
        pp = self.lib.game_tracks[name]["play_path"].lower()
        if pp in self.disabled:
            self.disabled.discard(pp); self.log(f"Re-enabled in playlists: {name}")
        else:
            self.disabled.add(pp); self.log(f"Disabled in playlists: {name}")
        self.refresh_game(); self._update_budget()

    # -------------------------------------------------------------- audio --
    def _play_file(self, path, label):
        if winsound is None:
            self.log("winsound unavailable."); return
        def work():
            wav = SCRATCH / "preview.wav"
            try:
                subprocess.run([str(VGMSTREAM), "-o", str(wav), str(path)], check=True, capture_output=True)
                winsound.PlaySound(str(wav), winsound.SND_FILENAME | winsound.SND_ASYNC)
                self.log(f"Playing: {label}")
            except Exception as e:
                self.log(f"Preview error: {e}")
        threading.Thread(target=work, daemon=True).start()

    def cb_play_converted(self):
        for rsm in self._sel_converted():
            self._play_file(rsm, rsm.name); return

    def cb_play_plan(self):
        idx = self._plan_sel()
        if idx:
            self._play_file(Path(self.plan[idx[0]]["src"]), self.plan[idx[0]]["name"])

    def cb_play_game(self):
        name = self._sel_game_track()
        if name:
            tmp = SCRATCH / "game_preview.rsm"
            self.lib.extract_track(name, tmp)
            self._play_file(tmp, name)

    def stop_audio(self):
        if winsound:
            winsound.PlaySound(None, winsound.SND_PURGE)

    # ------------------------------------------------------------ project --
    def cb_save_project(self):
        core.save_project(self.plan, self.disabled)
        self.log(f"Project saved to {PROJECT_FILE.name}")

    def cb_open_project(self):
        p = filedialog.askopenfilename(initialdir=core.ROOT, filetypes=[("Project JSON", "*.json")])
        if not p:
            return
        self.plan, self.disabled = core.load_project(p)
        for it in self.plan:
            if it["genre"] not in self.genres:
                self.genres.append(it["genre"])
        self._refresh_genres()
        self.refresh_plan(); self.refresh_game()
        self.log(f"Project loaded: {p}")

    def cb_restore(self):
        n = core.restore_backups()
        self.log(f"{n} backup(s) restored from .bak.")
        messagebox.showinfo("Backups", f"{n} file(s) restored from .bak.")

    # ------------------------------------------------------------- export --
    def cb_export(self):
        if self._busy:
            return
        if not self.plan and not self.disabled:
            messagebox.showinfo("Export", "The plan is empty.")
            return
        errors = core.validate_plan(self.plan, self.lib)
        if errors:
            messagebox.showerror("Cannot export", "\n".join(errors[:12]))
            return
        drop = self.drop_disabled_var.get()
        rep = core.budget_report(self.plan, self.disabled, self.lib)
        over = rep["over_dropped"] if drop else rep["over"]
        if over > 0:
            msg = (f"streams.dat would be {(rep['size_dropped'] if drop else rep['size'])/2**30:.2f} GiB, "
                   f"over the {LIMIT_GIB:.2f} GiB limit by {over/2**20:.0f} MiB.\n\n"
                   f"Remove or disable tracks to free {over/2**20:.0f} MiB.")
            if not drop and rep["drop_savings"] > 0:
                msg += (f"\n\nTip: enabling 'Drop disabled music' would reclaim "
                        f"{rep['drop_savings']/2**20:.0f} MiB.")
            messagebox.showerror(f"Over {LIMIT_GIB:.2f} GiB limit", msg)
            return
        out_dir = filedialog.askdirectory(initialdir=core.ROOT, title="Output folder for the new streams.dat")
        if not out_dir:
            return
        out_dir = Path(out_dir)
        if (out_dir / "streams.dat").resolve() == STREAMS_DAT.resolve():
            messagebox.showerror("Cannot export",
                                 "That folder contains the ORIGINAL streams.dat - exporting "
                                 "there would overwrite it.\n\nPick a different folder "
                                 "(e.g. create a 'build' subfolder).")
            return
        adds = [it for it in self.plan if it["mode"] == "add"]
        reps = [it for it in self.plan if it["mode"] == "replace"]
        size_gb = (rep["size_dropped"] if drop else rep["size"]) / 2**30
        if not messagebox.askyesno("Export",
                f"Output: {out_dir}\n\n"
                f"streams.dat  (~{size_gb:.2f} / {LIMIT_GIB:.2f} GiB)  +  MC3_PS2_Streams.lst\n"
                f"updated .play files under assets/ (.bak backup)\n"
                f"patched mcstrings*.strtbl (.bak backup)\n\n"
                f"Adds: {len(adds)}   Replaces: {len(reps)}   Disabled: {len(self.disabled)}"
                + (f"   (dropping disabled music)" if drop else "")
                + "\n\nContinue?"):
            return
        self._busy = True
        threading.Thread(target=self._export, args=(out_dir, drop), daemon=True).start()

    def _export(self, out_dir, drop_disabled=False):
        try:
            core.export_plan(self.plan, self.disabled, self.lib, out_dir,
                             log=self.log, drop_disabled=drop_disabled)
            self.log("EXPORT COMPLETE.")
            self.root.after(0, lambda: messagebox.showinfo("Export", "Export complete."))
        except Exception:
            import traceback
            tb = traceback.format_exc()
            self.log("EXPORT ERROR:\n" + tb)
            self.root.after(0, lambda: messagebox.showerror("Export failed", tb))
        finally:
            self._busy = False


def main():
    root = tk.Tk()
    MusicManager(root)
    root.mainloop()


if __name__ == "__main__":
    main()
