#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MC3 Music Manager - Dear PyGui (imgui-style) frontend.

Backend logic lives in mc3_music_core.py; this file is only the UI.
The previous Tkinter build is kept as mc3_music_manager_tk.py (fallback).

Panels:
  Left   : converted music library (RSTM output), filter, per-folder add
  Middle : insertion plan + edit form (incl. strtbl display-text formatting:
           custom multi-line text, font, size, X/Y scale, special glyphs)
  Right  : current in-game library (streams.dat + strtbl names)

Usage:  python mc3_music_manager.py
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import dearpygui.dearpygui as dpg

try:
    import winsound
except ImportError:
    winsound = None

import mc3_music_core as core
from mc3_music_core import (
    CITIES, MAX_NAME_LEN, RSTM_OUT, SCRATCH, VGMSTREAM, PROJECT_FILE,
    fmt_dur, rsm_info, sanitize_name, sanitize_genre, targets_summary,
    default_fmt, display_text,
)

ACCENT = (126, 200, 255)
GREEN  = (157, 255, 143)
YELLOW = (230, 200, 92)
GRAY   = (120, 124, 132)
RED    = (255, 126, 126)


class App:
    def __init__(self):
        self.lib = core.GameLibrary()
        self.plan = []
        self.disabled = set()
        self.genres = list(core.GENRES)
        self.fonts = core.list_fonts()
        self._busy = False
        self._log_buf = []
        self._log_lock = threading.Lock()
        # widget bookkeeping (tag -> data), rebuilt with the panes
        self.conv_rows = []      # (selectable_tag, Path)
        self.plan_rows = []      # (selectable_tag, plan_index)
        self.game_rows = []      # (selectable_tag, lst_name)
        self.form_idx = None     # plan index currently loaded in the form

    # ------------------------------------------------------------------ log
    def log(self, msg):
        with self._log_lock:
            self._log_buf.append(str(msg))

    def _drain_log(self):
        with self._log_lock:
            lines, self._log_buf = self._log_buf, []
        for ln in lines:
            dpg.add_text(ln, parent="log_body")
        if lines:
            dpg.set_y_scroll("log_body", -1.0)

    # ---------------------------------------------------------------- fonts
    def _setup_font(self):
        try:
            with dpg.font_registry():
                # DPG 2.x loads glyph ranges automatically from the ttf
                f = dpg.add_font("C:/Windows/Fonts/segoeui.ttf", 17)
                dpg.bind_font(f)
        except Exception:
            pass                                                   # default font

    # ------------------------------------------------------------------ ui
    def build(self):
        self._setup_font()

        with dpg.window(tag="primary"):
            # ---- toolbar
            with dpg.group(horizontal=True):
                dpg.add_button(label="Open project", callback=self.cb_open_project)
                dpg.add_button(label="Save project", callback=self.cb_save_project)
                dpg.add_button(label="Export final files...", tag="btn_export",
                               callback=self.cb_export)
                dpg.add_button(label="Restore backups (.bak)", callback=self.cb_restore)
                dpg.add_button(label="Stop audio", callback=lambda: self.stop_audio())
                dpg.add_text("", tag="status", color=ACCENT)

            with dpg.group(horizontal=True):
                # ---------------- left: converted library
                with dpg.child_window(width=430, height=-160, tag="pane_left"):
                    dpg.add_text("Converted music (RSTM output)", color=ACCENT)
                    dpg.add_input_text(hint="filter...", tag="conv_filter",
                                       callback=lambda: self.rebuild_converted(), width=-1)
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Preview", callback=self.cb_play_converted)
                        dpg.add_button(label="Add tracks >", callback=self.cb_add_tracks)
                        dpg.add_button(label="Replace >", callback=self.cb_replace)
                        dpg.add_button(label="Clear sel", callback=self.cb_clear_conv)
                    dpg.add_child_window(tag="conv_body", border=False)

                # ---------------- middle: plan + form (stretches with the window)
                with dpg.child_window(width=-480, height=-160, tag="pane_mid"):
                    dpg.add_text("Insertion plan", color=ACCENT)
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Apply to selected", callback=self.cb_apply_form)
                        dpg.add_button(label="Remove selected", callback=self.cb_remove_items)
                        dpg.add_button(label="Preview", callback=self.cb_play_plan)
                        dpg.add_button(label="Select all", callback=self.cb_select_all_plan)
                        dpg.add_button(label="Clear sel", callback=self.cb_clear_plan)
                    dpg.add_child_window(tag="plan_body", height=-400, border=False)

                    dpg.add_separator()
                    # ---- form
                    with dpg.group(horizontal=True):
                        dpg.add_text("Internal name")
                        dpg.add_input_text(tag="f_name", width=240,
                                           callback=lambda: self._name_len())
                        dpg.add_text("", tag="f_name_len")
                    with dpg.group(horizontal=True):
                        dpg.add_text("Genre")
                        dpg.add_combo(self.genres, tag="f_genre", width=150)
                        dpg.add_input_text(hint="or type new genre", tag="f_genre_new", width=160)
                        dpg.add_text("Win/Lose")
                        dpg.add_combo(["win", "lose"], tag="f_winlose",
                                      default_value="win", width=80)
                    with dpg.group(horizontal=True):
                        dpg.add_text("Title ")
                        dpg.add_input_text(tag="f_title", width=250)
                        dpg.add_text("Artist")
                        dpg.add_input_text(tag="f_artist", width=220)
                    with dpg.group(horizontal=True):
                        dpg.add_text("Cities")
                        for c in CITIES:
                            dpg.add_checkbox(label=c, tag=f"f_city_{c}", default_value=True)
                    with dpg.group(horizontal=True):
                        dpg.add_text("Playlists")
                        dpg.add_checkbox(label="city (used)", tag="f_t_city", default_value=True)
                        dpg.add_checkbox(label="garage (used)", tag="f_t_garage", default_value=True)
                        dpg.add_checkbox(label="genre race (rare)", tag="f_t_race")
                        dpg.add_checkbox(label="race editor (rare)", tag="f_t_editor")

                    # ---- display text / strtbl formatting
                    with dpg.collapsing_header(label="Display text formatting (strtbl)", default_open=True):
                        dpg.add_checkbox(label="Custom text (otherwise: \"Title\" \\n by Artist)",
                                         tag="f_fmt_custom")
                        dpg.add_input_text(tag="f_fmt_text", multiline=True, width=-1, height=54,
                                           hint='"My Song"\nby My Artist')
                        with dpg.group(horizontal=True):
                            dpg.add_text("Insert:")
                            for label, ch in core.GLYPHS:
                                dpg.add_button(label=label, small=True,
                                               callback=self.cb_glyph, user_data=ch)
                        with dpg.group(horizontal=True):
                            dpg.add_text("Font")
                            dpg.add_combo(self.fonts, tag="f_fmt_font",
                                          default_value=core.DEFAULT_FONT, width=170)
                            dpg.add_text("Size")
                            dpg.add_input_int(tag="f_fmt_size", default_value=core.DEFAULT_SIZE,
                                              width=90, min_value=0, max_value=255, min_clamped=True,
                                              max_clamped=True)
                            dpg.add_text("Scale X/Y")
                            dpg.add_input_floatx(tag="f_fmt_scale", size=2,
                                                 default_value=[1.0, 1.0], width=150, format="%.2f")
                        dpg.add_text("Retail music uses smallspace / size 15 / scale 1.0. "
                                     "Other values are experimental - test in game.",
                                     color=GRAY, wrap=600)

                # ---------------- right: game library
                with dpg.child_window(height=-160, tag="pane_right"):
                    dpg.add_text("In-game library (streams.dat)", color=ACCENT)
                    with dpg.group(horizontal=True):
                        dpg.add_button(label="Preview", callback=self.cb_play_game)
                        dpg.add_button(label="Enable/Disable in playlists",
                                       callback=self.cb_toggle_disabled)
                        dpg.add_button(label="Clear sel", callback=self.cb_clear_game)
                    dpg.add_child_window(tag="game_body", border=False)

            # ---- log
            dpg.add_text("Log", color=ACCENT)
            dpg.add_child_window(tag="log_body", height=120)

        # ---- genre modal (used by per-folder add buttons)
        with dpg.window(label="Genre for folder", modal=True, show=False, tag="genre_modal",
                        no_resize=True, width=420, height=150, pos=(500, 300)):
            dpg.add_text("", tag="genre_modal_info")
            with dpg.group(horizontal=True):
                dpg.add_combo(self.genres, tag="genre_modal_combo", width=170)
                dpg.add_input_text(hint="or type a new genre", tag="genre_modal_new", width=200)
            with dpg.group(horizontal=True):
                dpg.add_button(label="OK", width=80, callback=self.cb_genre_modal_ok)
                dpg.add_button(label="Cancel", width=80,
                               callback=lambda: dpg.configure_item("genre_modal", show=False))
        self._pending_folder = None

        # ---- export dir dialog + confirm modal + error modal
        dpg.add_file_dialog(directory_selector=True, show=False, modal=True,
                            tag="export_dir_dlg", width=760, height=460,
                            default_path=str(core.ROOT),
                            callback=self.cb_export_dir_chosen)
        with dpg.window(label="Confirm export", modal=True, show=False, tag="confirm_modal",
                        no_resize=True, width=560, height=260, pos=(450, 280)):
            dpg.add_text("", tag="confirm_text", wrap=530)
            with dpg.group(horizontal=True):
                dpg.add_button(label="Export", width=100, callback=self.cb_export_go)
                dpg.add_button(label="Cancel", width=100,
                               callback=lambda: dpg.configure_item("confirm_modal", show=False))
        with dpg.window(label="Cannot export", modal=True, show=False, tag="error_modal",
                        no_resize=True, width=620, height=280, pos=(430, 280)):
            dpg.add_text("", tag="error_text", wrap=590, color=RED)
            dpg.add_button(label="OK", width=100,
                           callback=lambda: dpg.configure_item("error_modal", show=False))
        self._export_dir = None

        dpg.set_primary_window("primary", True)

    # ------------------------------------------------------- pane rebuilds
    def rebuild_converted(self):
        flt = dpg.get_value("conv_filter").lower() if dpg.does_item_exist("conv_filter") else ""
        dpg.delete_item("conv_body", children_only=True)
        self.conv_rows = []
        if not RSTM_OUT.exists():
            dpg.add_text("RSTM output/ not found", parent="conv_body", color=RED)
            return
        groups = {}
        for rsm in sorted(RSTM_OUT.rglob("*.rsm")):
            rel = rsm.relative_to(RSTM_OUT)
            if flt and flt not in str(rel).lower():
                continue
            groups.setdefault(str(rel.parent), []).append(rsm)
        for gname in sorted(groups):
            files = groups[gname]
            node = dpg.add_tree_node(label=f"{gname}  ({len(files)})",
                                     parent="conv_body", default_open=bool(flt))
            dpg.add_button(label="Add this folder as genre...", small=True, parent=node,
                           callback=self.cb_add_folder, user_data=gname)
            for rsm in files:
                _, _, _, dur = rsm_info(rsm)
                tag = dpg.add_selectable(label=f"{rsm.stem}   [{fmt_dur(dur)}]",
                                         parent=node)
                self.conv_rows.append((tag, rsm))

    def rebuild_plan(self):
        dpg.delete_item("plan_body", children_only=True)
        self.plan_rows = []
        if not self.plan:
            dpg.add_text("(empty - use 'Add tracks' / 'Add folder' on the left)",
                         parent="plan_body", color=GRAY)
            return
        with dpg.table(parent="plan_body", header_row=True, resizable=True,
                       policy=dpg.mvTable_SizingStretchProp,
                       borders_innerV=True, borders_outerH=True):
            dpg.add_table_column(label="Internal name", init_width_or_weight=0.26)
            dpg.add_table_column(label="Mode", init_width_or_weight=0.10)
            dpg.add_table_column(label="Genre", init_width_or_weight=0.14)
            dpg.add_table_column(label="Title", init_width_or_weight=0.22)
            dpg.add_table_column(label="Artist", init_width_or_weight=0.14)
            dpg.add_table_column(label="Playlists", init_width_or_weight=0.14)
            for i, it in enumerate(self.plan):
                with dpg.table_row():
                    tag = dpg.add_selectable(label=it["name"], span_columns=True,
                                             callback=self.cb_plan_clicked, user_data=i)
                    self.plan_rows.append((tag, i))
                    dpg.add_text("ADD" if it["mode"] == "add" else "REPLACE",
                                 color=GREEN if it["mode"] == "add" else YELLOW)
                    dpg.add_text(it["genre"])
                    dpg.add_text(it["title"])
                    dpg.add_text(it["artist"])
                    dpg.add_text(targets_summary(it))

    def rebuild_game(self):
        dpg.delete_item("game_body", children_only=True)
        self.game_rows = []
        replaced = {it["target"] for it in self.plan if it["mode"] == "replace"}
        by_genre = {}
        for name, info in self.lib.game_tracks.items():
            by_genre.setdefault(info["genre"], []).append(name)
        for it in self.plan:
            if it["mode"] == "add":
                by_genre.setdefault(it["genre"], []).append(("__new__", it))
        for genre in sorted(by_genre):
            node = dpg.add_tree_node(label=genre, parent="game_body")
            for entry in sorted(by_genre[genre],
                                key=lambda e: e[1]["name"] if isinstance(e, tuple) else e):
                if isinstance(entry, tuple):
                    it = entry[1]
                    dpg.add_text(f"+ {it['name']}.rsm   \"{it['title']}\" by {it['artist']}",
                                 parent=node, color=GREEN)
                    continue
                name = entry
                info = self.lib.game_tracks[name]
                label = f"{Path(name).name}   {info['display'] or '-'}   [{fmt_dur(info['dur'])}]"
                color = None
                if name in replaced:
                    label = "[REPLACED] " + label
                    color = YELLOW
                if info["play_path"].lower() in self.disabled:
                    label = "[OFF] " + label
                    color = GRAY
                tag = dpg.add_selectable(label=label, parent=node)
                if color:
                    dpg.configure_item(tag, label=label)
                    with dpg.theme() as t:
                        with dpg.theme_component(dpg.mvSelectable):
                            dpg.add_theme_color(dpg.mvThemeCol_Text, color)
                    dpg.bind_item_theme(tag, t)
                self.game_rows.append((tag, name))

    def rebuild_all(self):
        self.rebuild_converted()
        self.rebuild_plan()
        self.rebuild_game()

    # -------------------------------------------------------- selections
    def _sel_conv(self):
        return [p for tag, p in self.conv_rows if dpg.get_value(tag)]

    def _sel_plan(self):
        return [i for tag, i in self.plan_rows if dpg.get_value(tag)]

    def _sel_game(self):
        return [n for tag, n in self.game_rows if dpg.get_value(tag)]

    def cb_clear_conv(self):
        for tag, _ in self.conv_rows: dpg.set_value(tag, False)

    def cb_clear_plan(self):
        for tag, _ in self.plan_rows: dpg.set_value(tag, False)

    def cb_select_all_plan(self):
        for tag, _ in self.plan_rows: dpg.set_value(tag, True)

    def cb_clear_game(self):
        for tag, _ in self.game_rows: dpg.set_value(tag, False)

    # ------------------------------------------------------------- form
    def _name_len(self):
        n = len(dpg.get_value("f_name"))
        over = n > MAX_NAME_LEN
        dpg.set_value("f_name_len", f"{n}/{MAX_NAME_LEN}" + ("  too long!" if over else ""))
        dpg.configure_item("f_name_len", color=RED if over else GRAY)

    def cb_glyph(self, sender, app_data, user_data):
        dpg.set_value("f_fmt_text", dpg.get_value("f_fmt_text") + user_data)
        dpg.set_value("f_fmt_custom", True)

    def cb_plan_clicked(self, sender, app_data, user_data):
        self.form_idx = user_data
        it = self.plan[user_data]
        dpg.set_value("f_name", it["name"]);   self._name_len()
        dpg.set_value("f_genre", it["genre"]); dpg.set_value("f_genre_new", "")
        dpg.set_value("f_title", it["title"]); dpg.set_value("f_artist", it["artist"])
        dpg.set_value("f_winlose", it.get("winlose", "win"))
        for c in CITIES:
            dpg.set_value(f"f_city_{c}", c in it["cities"])
        tg = it.get("targets", core.DEFAULT_TARGETS)
        dpg.set_value("f_t_city", tg.get("city", True))
        dpg.set_value("f_t_garage", tg.get("garage", True))
        dpg.set_value("f_t_race", tg.get("race", False))
        dpg.set_value("f_t_editor", tg.get("raceeditor", False))
        fmt = it.get("fmt") or default_fmt()
        dpg.set_value("f_fmt_custom", fmt.get("custom", False))
        dpg.set_value("f_fmt_text", fmt.get("text", "") or display_text(it))
        dpg.set_value("f_fmt_font", fmt.get("font", core.DEFAULT_FONT))
        dpg.set_value("f_fmt_size", fmt.get("size", core.DEFAULT_SIZE))
        sc = fmt.get("scale", core.DEFAULT_SCALE)
        dpg.set_value("f_fmt_scale", [sc[0], sc[1], 0.0, 0.0])

    def cb_apply_form(self):
        idx = self._sel_plan()
        if not idx and self.form_idx is not None:
            idx = [self.form_idx]
        if not idx:
            self.log("Apply: no plan item selected.")
            return
        new_genre = sanitize_genre(dpg.get_value("f_genre_new")) if dpg.get_value("f_genre_new").strip() else ""
        genre = new_genre or dpg.get_value("f_genre") or "Rock"
        if genre not in self.genres:
            self.genres.append(genre)
            dpg.configure_item("f_genre", items=self.genres)
            dpg.configure_item("genre_modal_combo", items=self.genres)
        cities = [c for c in CITIES if dpg.get_value(f"f_city_{c}")]
        targets = {"city": dpg.get_value("f_t_city"), "garage": dpg.get_value("f_t_garage"),
                   "race": dpg.get_value("f_t_race"), "raceeditor": dpg.get_value("f_t_editor")}
        sc = dpg.get_value("f_fmt_scale")
        fmt = {"custom": dpg.get_value("f_fmt_custom"),
               "text": dpg.get_value("f_fmt_text"),
               "font": dpg.get_value("f_fmt_font") or core.DEFAULT_FONT,
               "size": int(dpg.get_value("f_fmt_size")),
               "scale": [round(sc[0], 3), round(sc[1], 3)]}
        single = len(idx) == 1
        for i in idx:
            it = self.plan[i]
            if single:
                raw = dpg.get_value("f_name").strip()
                clean = sanitize_name(raw, limit=10_000)      # sanitize, then cap+dedupe
                it["name"] = core.unique_name(clean[:MAX_NAME_LEN], self.plan, exclude_idx=i)
                if len(clean) > MAX_NAME_LEN:
                    self.log(f"Name truncated to {MAX_NAME_LEN} chars: {it['name']}")
                it["title"] = dpg.get_value("f_title").strip() or it["name"]
                it["artist"] = dpg.get_value("f_artist").strip() or "?"
            if it["mode"] == "add":
                it["genre"] = genre
            it["winlose"] = dpg.get_value("f_winlose")
            it["cities"] = cities
            it["targets"] = dict(targets)
            it["fmt"] = dict(fmt)
        self.rebuild_plan(); self.rebuild_game()
        self.log(f"Applied to {len(idx)} item(s).")

    # ------------------------------------------------------------ actions
    def cb_add_tracks(self):
        srcs = self._sel_conv()
        if not srcs:
            self.log("Add tracks: select one or more files on the left.")
            return
        for rsm in srcs:
            self.plan.append(core.make_item(rsm, self.plan))
        self.rebuild_plan(); self.rebuild_game()
        self.log(f"{len(srcs)} track(s) added to the plan.")

    def cb_add_folder(self, sender, app_data, user_data):
        self._pending_folder = user_data
        files = sorted((RSTM_OUT / user_data).glob("*.rsm"))
        dpg.set_value("genre_modal_info", f"{user_data}  ->  {len(files)} track(s)")
        suggest = sanitize_genre(Path(user_data).name)
        dpg.set_value("genre_modal_new", suggest if suggest not in self.genres else "")
        dpg.set_value("genre_modal_combo", suggest if suggest in self.genres else "")
        dpg.configure_item("genre_modal", show=True)

    def cb_genre_modal_ok(self):
        folder = self._pending_folder
        dpg.configure_item("genre_modal", show=False)
        if not folder:
            return
        typed = dpg.get_value("genre_modal_new").strip()
        genre = sanitize_genre(typed) if typed else (dpg.get_value("genre_modal_combo") or "Rock")
        if genre not in self.genres:
            self.genres.append(genre)
            dpg.configure_item("f_genre", items=self.genres)
            dpg.configure_item("genre_modal_combo", items=self.genres)
        files = sorted((RSTM_OUT / folder).glob("*.rsm"))
        for rsm in files:
            self.plan.append(core.make_item(rsm, self.plan, genre=genre))
        self.rebuild_plan(); self.rebuild_game()
        self.log(f"Added folder '{folder}': {len(files)} track(s) as genre '{genre}'.")

    def cb_replace(self):
        srcs = self._sel_conv()
        tgts = self._sel_game()
        if not srcs or not tgts:
            self.log("Replace: select a converted track (left) AND an in-game track (right).")
            return
        tgt = tgts[0]
        info = self.lib.game_tracks[tgt]
        item = core.make_item(srcs[0], self.plan, mode="replace",
                              genre=info["genre"], target=tgt)
        item["name"] = Path(tgt).stem
        self.plan.append(item)
        self.rebuild_plan(); self.rebuild_game()
        self.log(f"Replacement planned: {tgt} <- {Path(srcs[0]).name}")

    def cb_remove_items(self):
        idx = self._sel_plan()
        if not idx and self.form_idx is not None:
            idx = [self.form_idx]
        for i in sorted(set(idx), reverse=True):
            self.plan.pop(i)
        if idx:
            self.form_idx = None
            self.rebuild_plan(); self.rebuild_game()
            self.log(f"Removed {len(set(idx))} item(s).")

    def cb_toggle_disabled(self):
        names = self._sel_game()
        if not names:
            self.log("Enable/Disable: select an in-game track first.")
            return
        for name in names:
            pp = self.lib.game_tracks[name]["play_path"].lower()
            if pp in self.disabled:
                self.disabled.discard(pp)
                self.log(f"Re-enabled in playlists: {name}")
            else:
                self.disabled.add(pp)
                self.log(f"Disabled in playlists: {name}")
        self.rebuild_game()

    # -------------------------------------------------------------- audio
    def _play_file(self, path, label):
        if winsound is None:
            self.log("winsound unavailable.")
            return
        def work():
            wav = SCRATCH / "preview.wav"
            try:
                subprocess.run([str(VGMSTREAM), "-o", str(wav), str(path)],
                               check=True, capture_output=True)
                winsound.PlaySound(str(wav), winsound.SND_FILENAME | winsound.SND_ASYNC)
                self.log(f"Playing: {label}")
            except Exception as e:
                self.log(f"Preview error: {e}")
        threading.Thread(target=work, daemon=True).start()

    def cb_play_converted(self):
        srcs = self._sel_conv()
        if srcs:
            self._play_file(srcs[0], Path(srcs[0]).name)

    def cb_play_plan(self):
        idx = self._sel_plan() or ([self.form_idx] if self.form_idx is not None else [])
        if idx:
            it = self.plan[idx[0]]
            self._play_file(Path(it["src"]), it["name"])

    def cb_play_game(self):
        names = self._sel_game()
        if not names:
            return
        tmp = SCRATCH / "game_preview.rsm"
        self.lib.extract_track(names[0], tmp)
        self._play_file(tmp, names[0])

    def stop_audio(self):
        if winsound:
            winsound.PlaySound(None, winsound.SND_PURGE)

    # ------------------------------------------------------------ project
    def cb_save_project(self):
        core.save_project(self.plan, self.disabled)
        self.log(f"Project saved to {PROJECT_FILE.name}")

    def cb_open_project(self):
        if PROJECT_FILE.exists():
            self.plan, self.disabled = core.load_project()
            for it in self.plan:
                if it["genre"] not in self.genres:
                    self.genres.append(it["genre"])
            dpg.configure_item("f_genre", items=self.genres)
            dpg.configure_item("genre_modal_combo", items=self.genres)
            self.rebuild_plan(); self.rebuild_game()
            self.log(f"Project loaded: {PROJECT_FILE.name}")
        else:
            self.log(f"No project file found ({PROJECT_FILE.name}).")

    def cb_restore(self):
        n = core.restore_backups()
        self.log(f"{n} backup(s) restored from .bak.")

    # ------------------------------------------------------------- export
    def cb_export(self):
        if self._busy:
            return
        if not self.plan and not self.disabled:
            dpg.set_value("error_text", "The plan is empty.\n\nAdd, replace or disable "
                                        "at least one track before exporting.")
            dpg.show_item("error_modal")
            return
        errors = core.validate_plan(self.plan, self.lib)
        if errors:
            dpg.set_value("error_text", "\n".join(errors[:12]))
            dpg.show_item("error_modal")
            for e in errors[:12]:
                self.log("ERROR: " + e)
            return
        dpg.show_item("export_dir_dlg")

    def cb_export_dir_chosen(self, sender, app_data):
        dpg.configure_item("export_dir_dlg", show=False)   # ensure dialog is closed
        # DPG dir selector may return the path in file_path_name or current_path,
        # sometimes with the last folder duplicated - normalize to an existing dir.
        cand = []
        if isinstance(app_data, dict):
            cand = [app_data.get("file_path_name", ""), app_data.get("current_path", "")]
        out_dir = None
        for c in cand:
            if not c:
                continue
            p = Path(c)
            if p.is_dir():
                out_dir = p
                break
            if p.parent.is_dir():          # duplicated-last-component quirk
                out_dir = p.parent
                break
        if out_dir is None:
            self.log(f"Export: could not resolve the chosen folder ({cand}).")
            return
        if (out_dir / "streams.dat").resolve() == core.STREAMS_DAT.resolve():
            dpg.set_value("error_text",
                          "That folder contains the ORIGINAL streams.dat - exporting there "
                          "would overwrite it.\n\nPick a different folder (e.g. create a "
                          "'build' subfolder).")
            dpg.show_item("error_modal")
            return
        self._export_dir = out_dir
        adds = [it for it in self.plan if it["mode"] == "add"]
        reps = [it for it in self.plan if it["mode"] == "replace"]
        extra = sum(Path(it["src"]).stat().st_size for it in adds if Path(it["src"]).exists())
        size_gb = (core.STREAMS_DAT.stat().st_size + extra) / 1e9
        dpg.set_value("confirm_text",
                      f"Output: {out_dir}\n\n"
                      f"streams.dat  (~{size_gb:.2f} GB)  +  MC3_PS2_Streams.lst\n"
                      f"updated .play files under assets/ (.bak backup)\n"
                      f"patched mcstrings*.strtbl (.bak backup)\n\n"
                      f"Adds: {len(adds)}    Replaces: {len(reps)}    "
                      f"Disabled: {len(self.disabled)}")
        dpg.configure_item("confirm_modal", show=True)

    def cb_export_go(self):
        dpg.configure_item("confirm_modal", show=False)
        out_dir = self._export_dir
        if not out_dir:
            return
        self._busy = True
        dpg.configure_item("btn_export", enabled=False)
        dpg.set_value("status", "Exporting...")

        def work():
            try:
                core.export_plan(self.plan, self.disabled, self.lib, out_dir, log=self.log)
                self.log("EXPORT COMPLETE.")
            except Exception:
                import traceback
                self.log("EXPORT ERROR:\n" + traceback.format_exc())
            finally:
                self._busy = False
        threading.Thread(target=work, daemon=True).start()

    # --------------------------------------------------------------- run
    def run(self):
        dpg.create_context()
        dpg.create_viewport(title="MC3 Music Manager", width=1580, height=920)
        self.build()
        dpg.setup_dearpygui()
        dpg.show_viewport()

        self.lib.load(log=self.log)
        self.genres = list(self.lib.genres)
        dpg.configure_item("f_genre", items=self.genres)
        dpg.configure_item("genre_modal_combo", items=self.genres)
        if PROJECT_FILE.exists():
            try:
                self.plan, self.disabled = core.load_project()
                for it in self.plan:
                    if it["genre"] not in self.genres:
                        self.genres.append(it["genre"])
                dpg.configure_item("f_genre", items=self.genres)
                dpg.configure_item("genre_modal_combo", items=self.genres)
                self.log(f"Project loaded: {PROJECT_FILE.name}")
            except Exception as e:
                self.log(f"Warning: project not loaded ({e})")
        self.rebuild_all()
        self.log("Ready.")

        while dpg.is_dearpygui_running():
            self._drain_log()
            if not self._busy and not dpg.is_item_enabled("btn_export"):
                dpg.configure_item("btn_export", enabled=True)
                dpg.set_value("status", "")
            dpg.render_dearpygui_frame()
        dpg.destroy_context()


def main():
    App().run()


if __name__ == "__main__":
    main()
