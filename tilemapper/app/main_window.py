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
from ..engine import MapDocument, Placement, TileLibrary, TileRenderer, snap, BACKGROUND_DEFAULTS
from .browser import TileBrowser
from .canvas import MapScene, MapView, rotate, snap_step, SCENE_PER_IN
from .export_dialog import run_export
from .background_panel import BackgroundPanel
from .qtutil import app_data_dir

MAP_FILTER = "Tile Mapper maps (*.tmap)"
TILES_MIME = "application/x-tilemapper-tiles"
MAX_UNDO = 200
HELP_TEXT = """<h3>Building a map</h3>
<ul>
<li><b>Add a tile:</b> drag it from the list onto the map, or double-click it.</li>
<li><b>Move:</b> drag. Tiles snap one grid square (1 inch) at a time, so tiles from
different sets can be lined up however you like.</li>
<li><b>Select several:</b> drag a box around them, or ⌘-click.</li>
<li><b>Turn:</b> R (clockwise) or ⇧R (counter-clockwise).</li>
<li><b>Mirror</b> left-right: H.  <b>Flip</b> top-bottom: V.</li>
<li>With several tiles selected, Turn, Mirror, and Flip work on the <b>whole section as one
block</b>, keeping the tiles joined up.</li>
<li><b>Copy / Cut / Paste:</b> ⌘C, ⌘X, ⌘V. Pasted tiles appear under the mouse pointer (or in the
middle of the view), already selected so you can drag or turn them. Copied sections can be
pasted into another map.</li>
<li><b>Layering:</b> tiles overlap freely; the newest one starts on top. Use the toolbar
buttons <b>To Top</b> (⌘⇧]) and <b>To Bottom</b> (⌘⇧[).
White background around the edge of any tile is see-through, so lower tiles show.</li>
<li><b>Delete:</b> ⌫.  <b>Duplicate:</b> ⌘D.  <b>Undo / Redo:</b> ⌘Z / ⇧⌘Z.</li>
<li><b>Pan:</b> two-finger scroll on the trackpad, or hold Space and drag.</li>
<li><b>Zoom:</b> pinch on the trackpad, turn a mouse scroll wheel, ⌘ + scroll, ⌘+ / ⌘−, or the
<b>Zoom In</b> / <b>Zoom Out</b> toolbar buttons. Zooming centres on the pointer.
<b>Fit the whole map:</b> ⌘0 or <b>Fit Map</b>.</li>
</ul>
<h3>Background image (for tracing real places)</h3>
<ul>
<li><b>View ▸ Choose Background Image…</b> (or the Background Image panel on the right) puts a
photo or plan behind your map. It moves and zooms with the tiles.</li>
<li><b>Set Scale from a Known Distance…:</b> click both ends of something whose length you
know, then type that length in feet. The image is resized so 5 ft = 1 square (1 inch).
You can also type the <b>Image width</b> directly.</li>
<li><b>Move Image:</b> turn on, drag the image into line, then click <b>Done Moving</b>.</li>
<li><b>Opacity</b> fades it. <b>Over tiles / Under tiles</b> (or press <b>B</b>) puts it on top
for tracing or underneath for checking. ⇧B hides or shows it. Clicks always reach the tiles.</li>
<li>The image is saved with the map but is <b>never printed or exported</b>. If you move the
image file, put it in the same folder as the map and it will be found.</li>
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

        self.bg_panel = BackgroundPanel(self)
        self.bg_dock = QDockWidget("Background Image", self)
        self.bg_dock.setObjectName("background_dock")
        self.bg_dock.setWidget(self.bg_panel)
        self.bg_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                                 | QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.bg_dock)
        self.bg_panel.choose_requested.connect(self.choose_background)
        self.bg_panel.remove_requested.connect(self.remove_background)
        self.bg_panel.changed.connect(self._bg_set)
        self.bg_panel.width_changed.connect(self._bg_width)
        self.bg_panel.measure_requested.connect(self._bg_measure)
        self.bg_panel.move_toggled.connect(self._bg_move)
        self.scene.background_changed.connect(self._bg_after_change)
        self.view.measure_done.connect(self._bg_measured)
        self.view.measure_cancelled.connect(lambda: self.statusBar().showMessage("Set Scale cancelled.", 3000))

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
        self._bg_refresh_panel()
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
        self.a_cut = self._act("Cut", self.cut_tiles, S.Cut)
        self.a_copy = self._act("Copy", self.copy_tiles, S.Copy)
        self.a_paste = self._act("Paste", self.paste_tiles, S.Paste)
        for a in (self.a_cut, self.a_copy, self.a_paste):
            m.addAction(a)
        self.a_dup = self._act("Duplicate", self.scene.duplicate_selected, "Ctrl+D")
        m.addAction(self.a_dup)
        m.addAction(self._act("Select All", self._select_all, S.SelectAll))

        # Tile
        m = mb.addMenu("&Tile")
        self.a_rot_cw = self._act("Turn Clockwise", lambda: self.scene.transform_selected("cw", lambda p: rotate(p, 90), "Turn"),
                                  "R", "Turn the selected tiles 90° clockwise (R)")
        self.a_rot_ccw = self._act("Turn Counter-clockwise",
                                   lambda: self.scene.transform_selected("ccw", lambda p: rotate(p, 270), "Turn"), "Shift+R",
                                   "Turn the selected tiles 90° counter-clockwise (⇧R)")
        self.a_mirror = self._act("Mirror Left–Right", lambda: self.scene.transform_selected("mirror", self._mirror, "Mirror"), "H",
                                  "Mirror the selected tiles left to right (H)")
        self.a_flip = self._act("Flip Top–Bottom", lambda: self.scene.transform_selected("flip", self._flip, "Flip"), "V",
                                "Flip the selected tiles top to bottom (V)")
        self.a_front = self._act("Bring to Front", self.scene.bring_to_front, "Ctrl+Shift+]",
                                 "Put the selected tiles on top of everything (⌘⇧])")
        self.a_back = self._act("Send to Back", self.scene.send_to_back, "Ctrl+Shift+[",
                                "Put the selected tiles under everything around them (⌘⇧[)")
        for a in (self.a_rot_cw, self.a_rot_ccw, self.a_mirror, self.a_flip):
            m.addAction(a)
        m.addSeparator()
        for a in (self.a_front, self.a_back):
            m.addAction(a)

        # View
        m = mb.addMenu("&View")
        m.addAction(self._act("Zoom In", lambda: self.view.zoom_by(1.25, False), S.ZoomIn))
        m.addAction(self._act("Zoom Out", lambda: self.view.zoom_by(0.8, False), S.ZoomOut))
        m.addAction(self._act("Fit Whole Map", self.view.zoom_to_fit, "Ctrl+0"))
        m.addAction(self._act("Actual Size on Screen (approx.)", lambda: self.view.set_zoom(0.96), "Ctrl+1"))
        m.addSeparator()
        m.addAction(self._act("Choose Background Image…", self.choose_background))
        self.a_bg_layer = self._act("Background Over / Under Tiles", self._bg_flip_layer, "B",
                                    "Flip the background image between over and under the tiles (B)")
        m.addAction(self.a_bg_layer)
        self.a_bg_show = self._act("Show Background Image", self._bg_toggle_visible, "Shift+B",
                                   "Hide or show the background image (⇧B)", checkable=True)
        m.addAction(self.a_bg_show)
        self.a_bg_panel = self.bg_dock.toggleViewAction()
        self.a_bg_panel.setText("Background Image Panel")
        m.addAction(self.a_bg_panel)

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
        for a, label in ((self.a_front, "⤒ To Top"), (self.a_back, "⤓ To Bottom")):
            b = QAction(label, self)
            b.setToolTip(a.toolTip())
            b.triggered.connect(a.trigger)
            tb.addAction(b)
            a._tb = b
        tb.addSeparator()
        d = QAction("⌫ Delete", self)
        d.triggered.connect(self.scene.delete_selected)
        tb.addAction(d)
        self._tb_delete = d
        tb.addSeparator()
        zo = QAction("− Zoom Out", self)
        zo.setToolTip("Zoom out (⌘−)")
        zo.triggered.connect(lambda: self.view.zoom_by(0.8, False))
        tb.addAction(zo)
        zi = QAction("Zoom In +", self)
        zi.setToolTip("Zoom in (⌘+)")
        zi.triggered.connect(lambda: self.view.zoom_by(1.25, False))
        tb.addAction(zi)
        fit = QAction("Fit Map", self)
        fit.setToolTip("Show the whole map (⌘0)")
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
            if not any(q.rect == p.rect for q in self.doc.placements):
                break
            p.x += max(p.footprint[0], 1)
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

    def _select_all(self):
        for item in self.scene.items_by_uid.values():
            item.setSelected(True)

    # ========================================================= copy & paste
    def _selected_placements(self) -> List[Placement]:
        # keep the map's layer order so pasted tiles stack the same way
        sel = {i.placement.uid for i in self.scene.selected_items()}
        return [p for p in self.doc.placements if p.uid in sel]

    def copy_tiles(self):
        ps = self._selected_placements()
        if not ps:
            return
        from dataclasses import asdict
        from PySide6.QtCore import QMimeData
        data = json.dumps({"format": "tilemapper-tiles", "placements": [asdict(p) for p in ps]})
        mime = QMimeData()
        mime.setData(TILES_MIME, data.encode("utf-8"))
        mime.setText(f"{len(ps)} Tile Mapper tile(s): " + ", ".join(p.code for p in ps[:20]))
        QApplication.clipboard().setMimeData(mime)
        self._clip = data            # fallback if the system clipboard is unavailable
        self.statusBar().showMessage(f"Copied {len(ps)} tile(s).", 3000)

    def cut_tiles(self):
        if self._selected_placements():
            self.copy_tiles()
            self.scene.delete_selected()

    def _clipboard_placements(self) -> List[Placement]:
        mime = QApplication.clipboard().mimeData()
        data = None
        if mime is not None and mime.hasFormat(TILES_MIME):
            data = bytes(mime.data(TILES_MIME)).decode("utf-8")
        data = data or getattr(self, "_clip", None)
        if not data:
            return []
        try:
            d = json.loads(data)
            ps = []
            for pd in d.get("placements", []):
                pd = dict(pd)
                pd.pop("uid", None)
                pd["rotation"] = int(pd.get("rotation", 0)) % 360
                ps.append(Placement(**pd))
            return ps
        except (ValueError, TypeError, KeyError):
            return []

    def paste_tiles(self):
        """Paste centred under the mouse pointer (or the middle of the view)."""
        ps = self._clipboard_placements()
        if not ps:
            return
        from PySide6.QtGui import QCursor
        vp = self.view.viewport()
        pos = vp.mapFromGlobal(QCursor.pos())
        if vp.rect().contains(pos):
            sp = self.view.mapToScene(pos)
            tx, ty = sp.x() / SCENE_PER_IN, sp.y() / SCENE_PER_IN
        else:
            tx, ty = self.view.visible_center_in()
        X0 = min(p.rect[0] for p in ps); Y0 = min(p.rect[1] for p in ps)
        X1 = max(p.rect[2] for p in ps); Y1 = max(p.rect[3] for p in ps)
        dx = round(tx - (X0 + X1) / 2)
        dy = round(ty - (Y0 + Y1) / 2)
        for p in ps:
            p.x += dx
            p.y += dy
        self.scene.add_placements(ps, "Paste")
        self.view.setFocus()
        self.statusBar().showMessage(f"Pasted {len(ps)} tile(s) — drag them into place.", 4000)

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
        self.view.stop_measure()
        self.scene._bg_moving = False
        self.bg_panel.set_moving(False)
        self._resolve_background_path(path)
        self.scene.rebuild()
        self.view.zoom_to_fit()
        self._bg_refresh_panel()
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
        if doc.background and self.scene.bg_item is None:
            QMessageBox.warning(self, "Background image not found",
                                f"This map's background image couldn't be found:\n"
                                f"{doc.background['path']}\n\nChoose it again with "
                                f"View ▸ Choose Background Image….")
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

    # ===================================================== background image
    def _resolve_background_path(self, map_path: Optional[str]):
        """If the image has moved, look for it next to the map file."""
        bg = self.doc.background
        if not bg or os.path.exists(bg["path"]) or not map_path:
            return
        alt = os.path.join(os.path.dirname(map_path), os.path.basename(bg["path"]))
        if os.path.exists(alt):
            bg["path"] = alt

    def _bg_mark_dirty(self):
        self.dirty = True
        self._update_title()

    def _bg_refresh_panel(self):
        self.bg_panel.set_state(self.doc.background, self.scene.background_size_px())
        bg = self.doc.background
        self.a_bg_layer.setEnabled(bool(bg))
        self.a_bg_show.setEnabled(bool(bg))
        self.a_bg_show.setChecked(bool(bg and bg.get("visible", True)))

    def _bg_after_change(self):
        self._bg_mark_dirty()
        self._bg_refresh_panel()

    def choose_background(self):
        start = self.settings.value("bg_dir", os.path.expanduser("~/Desktop"))
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a background image", start,
            "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.webp)")
        if not path:
            return
        self.settings.setValue("bg_dir", os.path.dirname(path))
        old = self.doc.background
        bg = dict(BACKGROUND_DEFAULTS, path=path)
        if old:   # keep the look the user had set up
            bg.update(opacity=old["opacity"], on_top=old["on_top"], visible=True)
        pm = self.scene.background_pixmap(path)
        if pm is None:
            QMessageBox.warning(self, "Couldn't open image", f"This file couldn't be read as an image:\n{path}")
            return
        # start it filling most of what's on screen; Set Scale makes it exact
        r = self.view.mapToScene(self.view.viewport().rect()).boundingRect()
        vis_w = r.width() / SCENE_PER_IN
        vis_h = r.height() / SCENE_PER_IN
        bg["in_per_px"] = 0.9 * min(vis_w / pm.width(), vis_h / pm.height())
        bg["x"] = round(r.center().x() / SCENE_PER_IN - pm.width() * bg["in_per_px"] / 2, 4)
        bg["y"] = round(r.center().y() / SCENE_PER_IN - pm.height() * bg["in_per_px"] / 2, 4)
        self.doc.background = bg
        self.scene.refresh_background()
        self.bg_dock.show()
        self._bg_after_change()
        self.statusBar().showMessage("Background added. Use “Set Scale from a Known Distance…” "
                                     "to size it so 5 ft = 1 square.", 8000)

    def remove_background(self):
        if not self.doc.background:
            return
        self._bg_move(False)
        self.doc.background = None
        self.scene.refresh_background()
        self._bg_after_change()

    def _bg_set(self, changes: dict):
        if not self.doc.background:
            return
        self.doc.background.update(changes)
        self.scene.refresh_background()
        self._bg_after_change()

    def _bg_flip_layer(self):
        if self.doc.background:
            self._bg_set({"on_top": not self.doc.background.get("on_top", False)})

    def _bg_toggle_visible(self):
        if self.doc.background:
            self._bg_set({"visible": not self.doc.background.get("visible", True)})

    def _bg_width(self, width_in: float):
        bg = self.doc.background
        w_px, h_px = self.scene.background_size_px()
        if not bg or not w_px:
            return
        old = float(bg["in_per_px"])
        new = width_in / w_px
        cx = bg["x"] + w_px * old / 2
        cy = bg["y"] + h_px * old / 2
        bg.update(in_per_px=new, x=round(cx - w_px * new / 2, 4), y=round(cy - h_px * new / 2, 4))
        self.scene.refresh_background()
        self._bg_after_change()

    def _bg_move(self, on: bool):
        self.scene.set_background_moving(on)
        self.bg_panel.set_moving(on)
        if on:
            self.statusBar().showMessage("Drag the background image to line it up. "
                                         "Click “Done Moving” when finished.")
        else:
            self.statusBar().clearMessage()

    def _bg_measure(self):
        if not self.doc.background:
            return
        self._bg_move(False)
        self.view.start_measure()
        self.statusBar().showMessage("Set Scale: click one end of something you know the length "
                                     "of, then the other end. Esc cancels.")

    def _bg_measured(self, a, b):
        bg = self.doc.background
        self.statusBar().clearMessage()
        if not bg:
            self.view.stop_measure()
            return
        old = float(bg["in_per_px"])
        dist_in = ((b.x() - a.x()) ** 2 + (b.y() - a.y()) ** 2) ** 0.5 / SCENE_PER_IN
        px = dist_in / old
        if px < 2:
            self.view.stop_measure()
            QMessageBox.information(self, "Set Scale", "Those two points are too close together. "
                                    "Try again with points farther apart.")
            return
        feet, ok = QInputDialog.getDouble(
            self, "Set Scale", "How far apart are those two points in real life (feet)?",
            float(self.settings.value("bg_last_feet", 50.0)), 0.1, 1_000_000.0, 1)
        self.view.stop_measure()
        if not ok:
            return
        self.settings.setValue("bg_last_feet", feet)
        new = (feet / 5.0) / px                     # 5 ft = 1 square = 1 inch
        ax, ay = a.x() / SCENE_PER_IN, a.y() / SCENE_PER_IN
        # keep the first point where it was so the image doesn't jump away
        lx, ly = (ax - bg["x"]) / old, (ay - bg["y"]) / old
        bg.update(in_per_px=new, x=round(ax - lx * new, 4), y=round(ay - ly * new, 4))
        self.scene.refresh_background()
        self._bg_after_change()
        self.statusBar().showMessage(f"Scale set: {feet:g} ft = {feet / 5:g} squares.", 6000)

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
        for a in (self.a_rot_cw, self.a_rot_ccw, self.a_mirror, self.a_flip,
                  self.a_delete, self.a_dup, self.a_front, self.a_back, self.a_cut, self.a_copy):
            a.setEnabled(has)
            if hasattr(a, "_tb"):
                a._tb.setEnabled(has)
        self._tb_delete.setEnabled(has)
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
