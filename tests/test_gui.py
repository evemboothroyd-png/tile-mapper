"""Smoke test for the window. Runs headless on the build machine."""
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.test_engine import grid_tile  # noqa: E402


@pytest.fixture(scope="module")
def app():
    QCoreApplication.setOrganizationName("TileMapperTest")
    QCoreApplication.setApplicationName("TileMapperTest")
    a = QApplication.instance() or QApplication([])
    yield a


def test_window_workflow(app, tmp_path):
    root = tmp_path / "tiles"
    grid_tile(str(root / "Dungeon" / "DV328 Bonus Intersection.jpg"), 6, 6)
    grid_tile(str(root / "Dungeon" / "IT12 Ice Temple.jpg"), 6, 6, bleed=19)
    grid_tile(str(root / "Wilderness" / "DW17 Forest.jpg"), 6, 9)
    grid_tile(str(root / "Town" / "Warf" / "ATCB01 Canal Boat.jpg"), 5, 1, white_bg=True)
    QSettings().setValue("library_root", str(root))

    from tilemapper.app.main_window import MainWindow
    from tilemapper.engine import MapDocument
    w = MainWindow()
    w.show()
    w.load_library(str(root))
    app.processEvents()
    assert len(w.library.tiles) == 4
    assert w.browser.list.count() == 4

    ids = sorted(w.library.tiles)
    w._drop_tile("Dungeon/DV328 Bonus Intersection.jpg", 3, 3)
    w._drop_tile("Wilderness/DW17 Forest.jpg", 9, 4.5)
    w._place_center("Town/Warf/ATCB01 Canal Boat.jpg")
    app.processEvents()
    assert len(w.doc.placements) == 3
    assert w.doc.placements[0].x == 0 and w.doc.placements[0].y == 0
    # every tile snaps one square (1") at a time
    w._drop_tile("Dungeon/IT12 Ice Temple.jpg", 30.4, 3.6)
    it12 = w.doc.placements[-1]
    assert (it12.x, it12.y) == (27, 1)
    w.scene.delete_selected()
    w.scene.items_by_uid[w.doc.placements[-1].uid].setSelected(True)   # the boat again

    # rotate / mirror the boat
    w.a_rot_cw.trigger()
    w.a_mirror.trigger()
    boat = w.doc.placements[-1]
    assert boat.rotation == 90 and (boat.flip_v or boat.flip_h)
    assert not hasattr(w, "float_box")

    # layering: send the boat to the bottom, then back to the top
    boat_uid = boat.uid
    w.a_back.trigger()
    assert w.doc.placements[0].uid == boat_uid
    item = w.scene.items_by_uid[boat_uid]
    assert item.zValue() < min(i.zValue() for u, i in w.scene.items_by_uid.items() if u != boat_uid)

    # undo / redo the layering
    w.undo()
    assert w.doc.placements[-1].uid == boat_uid
    w.redo()
    assert w.doc.placements[0].uid == boat_uid
    w.scene.items_by_uid[boat_uid].setSelected(True)
    w.a_front.trigger()
    assert w.doc.placements[-1].uid == boat_uid

    # delete and duplicate
    w.scene.clearSelection()
    w.scene.items_by_uid[w.doc.placements[0].uid].setSelected(True)
    w.scene.duplicate_selected()
    assert len(w.doc.placements) == 4
    w.scene.delete_selected()
    assert len(w.doc.placements) == 3

    # save / reopen
    path = tmp_path / "m.tmap"
    assert w._write(str(path))
    d = MapDocument.load(str(path))
    assert len(d.placements) == 3
    w.dirty = False
    w.open_map(str(path))
    assert len(w.scene.items_by_uid) == 3

    # export through the same engine the dialog uses
    from tilemapper.app.export_dialog import ExportWorker
    from tilemapper.engine import export
    for kind in ("overview_pdf", "overview_png", "pages_pdf", "single_png", "single_pdf"):
        out = tmp_path / f"o_{kind}.{'png' if kind.endswith('png') else 'pdf'}"
        wk = ExportWorker(w.doc, w.library, str(out), {
            "kind": kind, "setup": export.PageSetup(), "overlap": 0.25, "labels": True,
            "ppi": 150, "skip_blank": True})
        errors = []
        wk.failed.connect(errors.append)
        wk.run()
        assert not errors, errors
        assert out.exists() and out.stat().st_size > 1000

    w.dirty = False
    w.close()


def test_zoom(app):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from tilemapper.app.main_window import MainWindow
    w = MainWindow()
    w.show()
    app.processEvents()
    v = w.view
    z0 = v._zoom
    v.zoom_by(1.25, False)
    assert abs(v._zoom - z0 * 1.25) < 1e-9
    v.zoom_by(0.8, False)
    assert abs(v._zoom - z0) < 1e-9

    def wheel(dy, phase, pixel=QPoint()):
        pos = QPointF(v.viewport().rect().center())
        return QWheelEvent(pos, v.viewport().mapToGlobal(pos), pixel, QPoint(0, dy),
                           Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, phase, False)

    # a mouse wheel (no scroll phase) zooms
    z = v._zoom
    app.sendEvent(v.viewport(), wheel(120, Qt.ScrollPhase.NoScrollPhase))
    assert v._zoom > z
    # a trackpad two-finger scroll pans and leaves the zoom alone
    z = v._zoom
    app.sendEvent(v.viewport(), wheel(-40, Qt.ScrollPhase.ScrollUpdate, QPoint(0, -40)))
    assert v._zoom == z
    w.close()


def test_background_image(app, tmp_path, monkeypatch):
    from PIL import Image as PILImage
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtWidgets import QInputDialog
    from tilemapper.app.canvas import BG_Z_OVER, BG_Z_UNDER, SCENE_PER_IN
    from tilemapper.app.main_window import MainWindow
    from tilemapper.engine import BACKGROUND_DEFAULTS, MapDocument

    root = tmp_path / "tiles"
    grid_tile(str(root / "Dungeon" / "DV328 Bonus Intersection.jpg"), 6, 6)
    img = tmp_path / "photos" / "mews.png"
    img.parent.mkdir()
    PILImage.new("RGB", (400, 200), (90, 140, 200)).save(img)

    w = MainWindow()
    w.show()
    w.load_library(str(root))
    w._drop_tile("Dungeon/DV328 Bonus Intersection.jpg", 3, 3)
    app.processEvents()
    tile_item = next(iter(w.scene.items_by_uid.values()))

    # add the image (same steps as Choose Background Image, minus the file dialog)
    w.doc.background = dict(BACKGROUND_DEFAULTS, path=str(img), in_per_px=0.1)
    assert w.scene.refresh_background()
    w._bg_after_change()
    bgi = w.scene.bg_item
    assert bgi is not None and bgi.zValue() == BG_Z_UNDER
    assert abs(bgi.sceneBoundingRect().width() - 40 * SCENE_PER_IN) < 1e-6   # 400 px * 0.1 in
    assert bgi.acceptedMouseButtons() == Qt.MouseButton.NoButton            # clicks reach tiles

    # opacity and over/under
    w.bg_panel.opacity.setValue(30)
    assert abs(bgi.opacity() - 0.30) < 1e-6
    w.a_bg_layer.trigger()
    assert w.doc.background["on_top"] and w.scene.bg_item.zValue() == BG_Z_OVER
    assert w.bg_panel.over.isChecked()
    w.a_bg_layer.trigger()
    assert not w.doc.background["on_top"] and w.scene.bg_item.zValue() == BG_Z_UNDER
    w.a_bg_show.trigger()
    assert not w.scene.bg_item.isVisible()
    w.a_bg_show.trigger()
    assert w.scene.bg_item.isVisible()

    # image width box: 80 in wide, centre stays put
    cx = w.doc.background["x"] + 20
    w.bg_panel.width.setValue(80.0)
    assert abs(w.doc.background["in_per_px"] - 0.2) < 1e-9
    assert abs(w.doc.background["x"] + 40 - cx) < 1e-6

    # Set Scale: two points 100 image pixels apart are 50 ft -> 10 squares -> 0.1 in/px
    monkeypatch.setattr(QInputDialog, "getDouble", staticmethod(lambda *a, **k: (50.0, True)))
    bx, by = w.doc.background["x"], w.doc.background["y"]
    a = QPointF((bx + 10 * 0.2) * SCENE_PER_IN, (by + 10 * 0.2) * SCENE_PER_IN)
    b = QPointF((bx + 110 * 0.2) * SCENE_PER_IN, (by + 10 * 0.2) * SCENE_PER_IN)
    w._bg_measure()
    w._bg_measured(a, b)
    bg = w.doc.background
    assert abs(bg["in_per_px"] - 0.1) < 1e-9
    # the first clicked point stays on the same spot of the image
    assert abs(bg["x"] + 10 * 0.1 - a.x() / SCENE_PER_IN) < 1e-6

    # move mode: tiles ignore the mouse, the image can be dragged; then back to normal
    w._bg_move(True)
    assert tile_item.acceptedMouseButtons() == Qt.MouseButton.NoButton
    assert w.scene.bg_item.acceptedMouseButtons() == Qt.MouseButton.LeftButton
    x_before = w.doc.background["x"]
    w.scene.bg_item.setPos(w.scene.bg_item.pos() + QPointF(300, 0))
    w.scene.background_moved()
    assert abs(w.doc.background["x"] - (x_before + 3)) < 1e-6
    w._bg_move(False)
    assert tile_item.acceptedMouseButtons() == Qt.MouseButton.LeftButton

    # saved with the map; found next to the map if the image was moved
    mp = tmp_path / "maps" / "mews.tmap"
    mp.parent.mkdir()
    assert w._write(str(mp))
    saved = MapDocument.load(str(mp))
    assert saved.background["path"] == str(img)
    moved = mp.parent / "mews.png"
    img.rename(moved)
    w.open_map(str(mp))
    assert w.doc.background["path"] == str(moved)
    assert w.scene.bg_item is not None
    assert w.bg_panel.width.value() > 0

    # undo keeps the image; remove takes it away
    w.scene.items_by_uid[w.doc.placements[0].uid].setSelected(True)
    w.a_rot_cw.trigger()
    w.undo()
    assert w.scene.bg_item is not None
    w.remove_background()
    assert w.doc.background is None and w.scene.bg_item is None
    w.dirty = False
    w.close()
