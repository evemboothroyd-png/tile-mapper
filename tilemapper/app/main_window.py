"""Main application window."""
from __future__ import annotations

import json
import os
from typing import List, Optional

from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtGui import QAction, QKeySequence, QActionGroup
from PySide6.QtWidgets import (QMainWindow, QDockWidget, QFileDialog, QMessageBox, QToolBar,
                               QLabel, QProgressDialog, QApplication, QInputDialog, QWidget)

from .. import APP_NAME, __version__
from ..engine import MapDocument, Placement, TileLibrary, TileRenderer, snap
from .browser import TileBrowser
from .canvas import MapScene, MapView, rotate, snap_step, SCENE_PER_IN
from .export_dialog import run_export
from .qtutil import app_data_dir

MAP_FILTER = "Tile Mapper maps (*.tmap)"
MAX_UNDO = 200
HELP_TEXT = """<h3>Building a map</h3>
<ul>
<li><b>Add a tile:</b> drag it from the list onto the map, or double-click it.</li>
<li><b>Move:</b> drag. Tiles snap one grid square (1 inch) at a time, so tiles from
different sets can be lined up however you like.</li>
<li><b>Select several:</b> drag a box around them, or ⌘-click.</li>
<li><b>Turn:</b> R (clockwise) or ⇧R (counter-clockwise).</li>
<li><b>Mirror</b> left-right: H.  <b>Flip</b> top-bottom: V.</li>
<li><b>Float on top:</b> F, or tick the box in the toolbar. Floating pieces (like boats) sit
above ordinary tiles, and white background around them becomes see-through.</li>
<li><b>Delete:</b> ⌫.  <b>Duplicate:</b> ⌘D.  <b>Undo / Redo:</b> ⌘Z / ⇧⌘Z.</li>
<li><b>Pan:</b> two-finger scroll, or hold Space and drag.  <b>Zoom:</b> pinch, or ⌘ + scroll,
or ⌘+ / ⌘−.  <b>Fit the whole map:</b> ⌘0.</li>
</ul>
<h3>Printing to scale</h3>
<p>Choose <b>File ▸ Print / Export…</b> and pick <i>Full size — split into printer pages</i>.
Open the PDF and print with <b>Scale 100%</b> (Preview: untick “Scale to fit”, set Scale to
100%). Measure the 1-inch square in the corner of a page — it should be exactly 1 inch.</p>
<h3>Adding new tiles</h3>
<p>Put new image files in your tile folder (any sub-folder becomes a category) and choose
<b>Tiles ▸ Rescan Tile Folder</b>. Thumbnails are made automatically. File names should start
with the tile code, e.g. <i>DV328 Bonus Intersection.jpg</i>.</p>
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings()
        self.cache_dir = os.path.join(app_data_dir(), "cache")
        self.library: Optional[TileLibrary] = None
        self.doc = MapDocument()
        self.path: Optional[str] = None
        self.dirty = False
        self.undo_stack: List[dict] = []
        self.redo_stack: List[dict] = []
        self._last_state = self.doc.to_dict()

        self.renderer = TileRenderer(TileLibrary(os.getcwd()), cache_items=400)
        self.scene = MapScene(self.doc, self.renderer, self)
        self.scene.changed_doc.connect(self._on_doc_changed)
        self.scene.selectionChanged.connect(self._on_selection)
        self.view = MapView(self.scene, self)
        self.view.tile_dropped.connect(self._drop_tile)
        self.view.zoom_changed.connect(self._on_zoom)
        self.setCentralWidget(self.view)

        self.browser = TileBrowser(self)
        self.browser.tile_activated.connect(self._place_center)
        dock = QDockWidget("Tiles", self)
        dock.setObjectName("tiles_dock")
        dock.setWidget(self.browser)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

        self.status_map = QLabel()
        self.status_sel = QLabel()
        self.status_zoom = QLabel()
        self.statusBar().addWidget(self.status_map)
        self.statusBar().addWidget(self.status_sel, 1)
        self.statusBar().addPermanentWidget(self.status_zoom)

        self._build_actions()
        self.resize(1400, 900)
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        self._update_title()
        self._update_status()
        self._on_selection()
        QTimer.singleShot(0, self._startup)

    # ================================================================ actions
    def _act(self, text, slot, shortcut=None, tip=None, checkable=False):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setStatusTip(tip)
            a.setToolTip(tip)
        a.setCheckable(checkable)
        a.triggered.connect(slot)
        return a

    def _build_actions(self):
        mb = self.menuBar()
        S = QKeySequence.StandardKey
        # File
        m = mb.addMenu("&File")
        m.addAction(self._act("New Map", self.new_map, S.New))
        m.addAction(self._act("Open Map…", self.open_map, S.Open))
        self.recent_menu = m.addMenu("Open Recent")
        m.addSeparator()
        m.addAction(self._act("Save", self.save, S.Save))
        m.addAction(self._act("Save As…", self.save_as, S.SaveAs))
        m.addAction(self._act("Rename Map…", self.rename_map))
        m.addSeparator()
        self.a_export = self._act("Print / Export…", lambda: self.export("pages_pdf"), "Ctrl+P",
                                  "Overview prints, full-size page sets, and single big files")
        m.addAction(self.a_export)
        m.addAction(self._act("Export Overview…", lambda: self.export("overview_pdf"), "Ctrl+Shift+P"))
        self._refresh_recent()

        # Edit
        m = mb.addMenu("&Edit")
        self.a_undo = self._act("Undo", self.undo, S.Undo)
        self.a_redo = self._act("Redo", self.redo, S.Redo)
        m.addAction(self.a_undo)
        m.addAction(self.a_redo)
        m.addSeparator()
        self.a_delete = self._act("Delete", self.scene.delete_selected, QKeySequence(Qt.Key.Key_Backspace))
        self.a_delete.setShortcuts([QKeySequence(Qt.Key.Key_Backspace), QKeySequence(Qt.Key.Key_Delete)])
        m.addAction(self.a_delete)
        self.a_dup = self._act("Duplicate", self.scene.duplicate_selected, "Ctrl+D")
        m.addAction(self.a_dup)
        m.addAction(self._act("Select All", self._select_all, S.SelectAll))

        # Tile
        m = mb.addMenu("&Tile")
        self.a_rot_cw = self._act("Turn Clockwise", lambda: self._modify(lambda p: rotate(p, 90), "Turn"),
                                  "R", "Turn the selected tiles 90° clockwise (R)")
        self.a_rot_ccw = self._act("Turn Counter-clockwise",
                                   lambda: self._modify(lambda p: rotate(p, 270), "Turn"), "Shift+R",
                                   "Turn the selected tiles 90° counter-clockwise (⇧R)")
        self.a_mirror = self._act("Mirror Left–Right", lambda: self._modify(self._mirror, "Mirror"), "H",
                                  "Mirror the selected tiles left to right (H)")
        self.a_flip = self._act("Flip Top–Bottom", lambda: self._modify(self._flip, "Flip"), "V",
                                "Flip the selected tiles top to bottom (V)")
        self.a_float = self._act("Float on Top", self._toggle_float, "F",
                                 "Float the selected pieces above ordinary tiles (F)", checkable=True)
        self.a_front = self._act("Bring to Front", self.scene.bring_to_front, "Ctrl+]")
        for a in (self.a_rot_cw, self.a_rot_ccw, self.a_mirror, self.a_flip):
            m.addAction(a)
        m.addSeparator()
        m.addAction(self.a_float)
        m.addAction(self.a_front)

        # View
        m = mb.addMenu("&View")
        m.addAction(self._act("Zoom In", lambda: self.view.zoom_by(1.25, False), S.ZoomIn))
        m.addAction(self._act("Zoom Out", lambda: self.view.zoom_by(0.8, False), S.ZoomOut))
        m.addAction(self._act("Fit Whole Map", self.view.zoom_to_fit, "Ctrl+0"))
        m.addAction(self._act("Actual Size on Screen (approx.)", lambda: self.view.set_zoom(0.96), "Ctrl+1"))

        # Tiles (library)
        m = mb.addMenu("Ti&les")
        m.addAction(self._act("Choose Tile Folder…", self.choose_library))
        m.addAction(self._act("Rescan Tile Folder", self.rescan, "Ctrl+R"))
        m.addAction(self._act("Make Missing Thumbnails in Tile Folder", self.write_thumbs))
        m.addAction(self._act("Show Tile Folder in Finder", self.reveal_library))

        # Help
        m = mb.addMenu("&Help")
        m.addAction(self._act(f"How to Use {APP_NAME}", self.show_help))
        m.addAction(self._act(f"About {APP_NAME}", self.show_about))

        # Toolbar
        tb = QToolBar("Tools", self)
        tb.setObjectName("tools")
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.addToolBar(tb)
        for a, label in ((self.a_rot_ccw, "↺ Turn"), (self.a_rot_cw, "Turn ↻"),
                         (self.a_mirror, "⇋ Mirror"), (self.a_flip, "⇵ Flip")):
            b = QAction(label, self)
            b.setToolTip(a.toolTip())
            b.triggered.connect(a.trigger)
            tb.addAction(b)
            a._tb = b
        tb.addSeparator()
        from PySide6.QtWidgets import QCheckBox
        self.float_box = QCheckBox("Float on top")
        self.float_box.setToolTip("Floating pieces sit above ordinary tiles and their white "
                                  "surround becomes see-through (F)")
        self.float_box.clicked.connect(self._float_box_clicked)
        tb.addWidget(self.float_box)
        tb.addSeparator()
        d = QAction("⌫ Delete", self)
        d.triggered.connect(self.scene.delete_selected)
        tb.addAction(d)
        self._tb_delete = d
        tb.addSeparator()
        fit = QAction("Fit Map", self)
        fit.triggered.connect(self.view.zoom_to_fit)
        tb.addAction(fit)
        spacer = QWidget()
        from PySide6.QtWidgets import QSizePolicy
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        pr = QAction("Print / Export…", self)
        pr.triggered.connect(self.a_export.trigger)
        tb.addAction(pr)

    # ============================================================== startup
    def _startup(self):
        lib = self.settings.value("library_root", "")
        if lib and os.path.isdir(lib):
            self.load_library(lib)
        else:
            QMessageBox.information(
                self, f"Welcome to {APP_NAME}",
                "First, choose the folder that holds your map tiles.\n\n"
                "Sub-folders (Dungeon, Town, Wilderness…) become categories. "
                "A folder named “Tile Thumbnails” inside it is used for thumbnails if present.")
            self.choose_library()

    # ============================================================= library
    def choose_library(self):
        start = self.settings.value("library_root", os.path.expanduser("~/Desktop"))
        d = QFileDialog.getExistingDirectory(self, "Choose your tile folder", start)
        if d:
            self.load_library(d)

    def load_library(self, root: str):
        lib = TileLibrary(root, cache_dir=self.cache_dir)
        prog = QProgressDialog("Reading tiles…", "", 0, 0, self)
        prog.setCancelButton(None)
        prog.setWindowTitle("Tile folder")
        prog.setWindowModality(Qt.WindowModality.WindowModal)
        prog.setMinimumDuration(400)

        def cb(a, b):
            prog.setMaximum(b)
            prog.setValue(a)
            QApplication.processEvents()

        lib.scan(cb)
        prog.close()
        self.library = lib
        self.renderer.library = lib
        self.renderer._cache.clear()
        self.settings.setValue("library_root", root)
        self.browser.set_library(lib)
        self.scene.rebuild()
        if not lib.tiles:
            QMessageBox.warning(self, "No tiles found",
                                f"No .jpg or .png files were found in:\n{root}")
        self._update_status()

    def rescan(self):
        if self.library:
            self.load_library(self.library.root)
        else:
            self.choose_library()

    def write_thumbs(self):
        if not self.library:
            return
        missing = [t for t in self.library.tiles.values() if not t.thumb_path]
        if not missing:
            QMessageBox.information(self, "Thumbnails", "Every tile already has a thumbnail.")
            return
        if QMessageBox.question(self, "Make thumbnails",
                                f"Make {len(missing)} thumbnail(s) in the “Tile Thumbnails” folder?") \
                != QMessageBox.StandardButton.Yes:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            written = self.library.write_missing_thumbnails()
        finally:
            QApplication.restoreOverrideCursor()
        QMessageBox.information(self, "Thumbnails", f"Made {len(written)} thumbnail(s).")

    def reveal_library(self):
        if self.library:
            import subprocess
            subprocess.Popen(["open", self.library.root])

    # ============================================================== placing
    def _make_placement(self, tile_id: str, cx: float, cy: float) -> Optional[Placement]:
        info = self.library.get(tile_id) if self.library else None
        if not info:
            return None
        p = Placement(tile_id, 0, 0, info.w_in, info.h_in, code=info.code)
        step = snap_step(p)
        p.x = snap(cx - info.w_in / 2, step)
        p.y = snap(cy - info.h_in / 2, step)
        return p

    def _drop_tile(self, tile_id: str, x_in: float, y_in: float):
        p = self._make_placement(tile_id, x_in, y_in)
        if p:
            self.scene.place(p)
            self.view.setFocus()

    def _place_center(self, tile_id: str):
        cx, cy = self.view.visible_center_in()
        p = self._make_placement(tile_id, cx, cy)
        if not p:
            return
        # don't land exactly on top of an existing tile: step right until free
        for _ in range(200):
            if not any(q.rect == p.rect and not q.floating for q in self.doc.placements):
                break
            p.x += snap_step(p) if p.floating else max(p.footprint[0], 1)
        self.scene.place(p)
        self.view.setFocus()

    # ================================================================ edits
    @staticmethod
    def _mirror(p: Placement):
        # mirroring in screen space: for turned tiles swap which axis we flip
        if p.rotation in (90, 270):
            p.flip_v = not p.flip_v
        else:
            p.flip_h = not p.flip_h

    @staticmethod
    def _flip(p: Placement):
        if p.rotation in (90, 270):
            p.flip_h = not p.flip_h
        else:
            p.flip_v = not p.flip_v

    def _modify(self, fn, label):
        self.scene.modify_selected(fn, label)

    def _toggle_float(self):
        sel = self.scene.selected_items()
        if not sel:
            self.a_float.setChecked(False)
            return
        target = not all(i.placement.floating for i in sel)
        self._set_float(target)

    def _float_box_clicked(self, checked):
        self._set_float(bool(checked))

    def _set_float(self, value: bool):
        def f(p):
            p.floating = value
        self.scene.modify_selected(f, "Float on top" if value else "Stop floating")
        self._on_selection()

    def _select_all(self):
        for item in self.scene.items_by_uid.values():
            item.setSelected(True)

    # ============================================================ undo/redo
    def _on_doc_changed(self, label: str):
        self.undo_stack.append(self._last_state)
        if len(self.undo_stack) > MAX_UNDO:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self._last_state = self.doc.to_dict()
        self.dirty = True
        self._update_title()
        self._update_status()
        self._on_selection()

    def _restore(self, state: dict):
        restored = MapDocument.from_dict(state)
        self.doc.placements = restored.placements
        self.doc.title = restored.title
        self._last_state = state
        self.scene.rebuild()
        self.dirty = True
        self._update_title()
        self._update_status()
        self._on_selection()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self.doc.to_dict())
            self._restore(self.undo_stack.pop())

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.doc.to_dict())
            self._restore(self.redo_stack.pop())

    # =============================================================== files
    def _confirm_discard(self) -> bool:
        if not self.dirty or not self.doc.placements:
            return True
        r = QMessageBox.question(
            self, "Save changes?", f"Save changes to “{self.doc.title}” first?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Save:
            return self.save()
        return r == QMessageBox.StandardButton.Discard

    def _set_doc(self, doc: MapDocument, path: Optional[str]):
        self.doc = doc
        self.scene.doc = doc
        self.path = path
        self.dirty = False
        self.undo_stack.clear()
        self.redo_stack.clear()
        self._last_state = doc.to_dict()
        self.scene.rebuild()
        self.view.zoom_to_fit()
        self._update_title()
        self._update_status()

    def new_map(self):
        if self._confirm_discard():
            self._set_doc(MapDocument(), None)

    def open_map(self, path: Optional[str] = None):
        if not self._confirm_discard():
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open map", self._map_dir(), MAP_FILTER)
        if not path:
            return
        try:
            doc = MapDocument.load(path)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            QMessageBox.warning(self, "Couldn't open map", str(e))
            return
        self._set_doc(doc, path)
        self._add_recent(path)
        missing = [p.code for p in doc.placements if not (self.library and self.library.get(p.tile_id))]
        if missing:
            QMessageBox.warning(self, "Some tiles are missing",
                                f"{len(missing)} tile(s) in this map aren't in your tile folder "
                                f"(shown as grey boxes): {', '.join(sorted(set(missing))[:20])}")

    def save(self) -> bool:
        if not self.path:
            return self.save_as()
        return self._write(self.path)

    def save_as(self) -> bool:
        start = os.path.join(self._map_dir(), (self.doc.title or "Untitled map") + ".tmap")
        path, _ = QFileDialog.getSaveFileName(self, "Save map", start, MAP_FILTER)
        if not path:
            return False
        if not path.endswith(".tmap"):
            path += ".tmap"
        if self.doc.title in ("", "Untitled map"):
            self.doc.title = os.path.splitext(os.path.basename(path))[0]
        return self._write(path)

    def _write(self, path: str) -> bool:
        if self.library:
            self.doc.library_hint = self.library.root
        try:
            self.doc.save(path)
        except OSError as e:
            QMessageBox.warning(self, "Couldn't save", str(e))
            return False
        self.path = path
        self.dirty = False
        self.settings.setValue("map_dir", os.path.dirname(path))
        self._add_recent(path)
        self._update_title()
        return True

    def rename_map(self):
        t, ok = QInputDialog.getText(self, "Rename map", "Map name (used on printouts):",
                                     text=self.doc.title)
        if ok and t.strip():
            self.doc.title = t.strip()
            self._on_doc_changed("Rename")

    def _map_dir(self) -> str:
        return self.settings.value("map_dir", os.path.expanduser("~/Documents"))

    def _recent(self) -> List[str]:
        v = self.settings.value("recent", [])
        if isinstance(v, str):
            v = [v]
        return [p for p in (v or []) if os.path.exists(p)]

    def _add_recent(self, path):
        r = [path] + [p for p in self._recent() if p != path]
        self.settings.setValue("recent", r[:10])
        self._refresh_recent()

    def _refresh_recent(self):
        self.recent_menu.clear()
        for p in self._recent():
            a = QAction(os.path.basename(p), self)
            a.triggered.connect(lambda _=False, p=p: self.open_map(p))
            self.recent_menu.addAction(a)
        self.recent_menu.setEnabled(bool(self._recent()))

    def export(self, kind: str):
        if not self.library:
            return
        run_export(self, self.doc, self.library, kind, self.settings.value("export_dir", self._map_dir()))

    # ============================================================== status
    def _update_title(self):
        name = os.path.basename(self.path) if self.path else self.doc.title
        self.setWindowTitle(f"{name}{' •' if self.dirty else ''} — {APP_NAME}")
        self.setWindowModified(self.dirty)
        self.a_undo.setEnabled(bool(self.undo_stack))
        self.a_redo.setEnabled(bool(self.redo_stack))

    def _update_status(self):
        b = self.doc.bounds()
        if b:
            w, h = b[2] - b[0], b[3] - b[1]
            self.status_map.setText(f"Map: {w:g}\" × {h:g}\" ({w / 12:.1f} × {h / 12:.1f} ft), "
                                    f"{len(self.doc.placements)} tiles")
        else:
            lib = f" — {len(self.library.tiles)} tiles in folder" if self.library else ""
            self.status_map.setText("Empty map — drag tiles from the left" + lib)

    def _on_selection(self):
        try:
            sel = self.scene.selected_items()
        except RuntimeError:
            return
        has = bool(sel)
        for a in (self.a_rot_cw, self.a_rot_ccw, self.a_mirror, self.a_flip, self.a_float,
                  self.a_delete, self.a_dup, self.a_front):
            a.setEnabled(has)
            if hasattr(a, "_tb"):
                a._tb.setEnabled(has)
        self._tb_delete.setEnabled(has)
        self.float_box.setEnabled(has)
        all_float = has and all(i.placement.floating for i in sel)
        any_float = has and any(i.placement.floating for i in sel)
        self.float_box.blockSignals(True)
        if has and any_float and not all_float:
            self.float_box.setTristate(True)
            self.float_box.setCheckState(Qt.CheckState.PartiallyChecked)
        else:
            self.float_box.setTristate(False)
            self.float_box.setChecked(all_float)
        self.float_box.blockSignals(False)
        self.a_float.setChecked(all_float)
        if len(sel) == 1:
            p = sel[0].placement
            info = self.library.get(p.tile_id) if self.library else None
            fw, fh = p.footprint
            self.status_sel.setText(f"{p.code} {info.title if info else ''} — {fw:g}\" × {fh:g}\""
                                    f" at ({p.x:g}, {p.y:g})")
        elif sel:
            self.status_sel.setText(f"{len(sel)} tiles selected")
        else:
            self.status_sel.setText("")

    def _on_zoom(self, z: float):
        self.status_zoom.setText(f"Zoom {z * 100:.0f}%")

    # ================================================================ help
    def show_help(self):
        box = QMessageBox(self)
        box.setWindowTitle(f"How to use {APP_NAME}")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(HELP_TEXT)
        box.exec()

    def show_about(self):
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<b>{APP_NAME}</b> {__version__}<br>Build battle maps from tile art "
                          f"and print them at true 1-inch scale.")

    # =============================================================== close
    def closeEvent(self, e):
        if self._confirm_discard():
            self.settings.setValue("geometry", self.saveGeometry())
            e.accept()
        else:
            e.ignore()
