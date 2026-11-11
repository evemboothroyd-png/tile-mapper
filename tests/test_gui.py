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
