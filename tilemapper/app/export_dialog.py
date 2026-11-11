"""Print / export dialog and the background worker that does the rendering."""
from __future__ import annotations

import os
import traceback

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QComboBox, QCheckBox,
                               QDialogButtonBox, QLabel, QDoubleSpinBox, QRadioButton,
                               QButtonGroup, QGroupBox, QFileDialog, QMessageBox,
                               QProgressDialog)

from ..engine import MapDocument, TileRenderer, TileLibrary
from ..engine import export

KINDS = [
    ("overview_pdf", "Overview — whole map on one page (PDF, not to scale)"),
    ("overview_png", "Overview — whole map as an image (PNG, not to scale)"),
    ("pages_pdf", "Full size — split into printer pages (PDF, true scale)"),
    ("single_png", "Full size — one huge image (PNG, true scale)"),
    ("single_pdf", "Full size — one huge page (PDF, true scale, for print shops)"),
]


class ExportDialog(QDialog):
    def __init__(self, doc: MapDocument, parent=None, kind: str = "pages_pdf"):
        super().__init__(parent)
        self.setWindowTitle("Print / Export")
        self.doc = doc
        lay = QVBoxLayout(self)
        b = doc.bounds()
        if b:
            lay.addWidget(QLabel(f"Map size: {b[2] - b[0]:g}\" × {b[3] - b[1]:g}\" "
                                 f"({len(doc.placements)} tiles)"))

        box = QGroupBox("What do you want to make?")
        bl = QVBoxLayout(box)
        self.kind_group = QButtonGroup(self)
        for i, (k, label) in enumerate(KINDS):
            rb = QRadioButton(label)
            rb.setProperty("kind", k)
            self.kind_group.addButton(rb, i)
            bl.addWidget(rb)
            if k == kind:
                rb.setChecked(True)
        self.kind_group.buttonToggled.connect(lambda *_: self._update())
        lay.addWidget(box)

        form = QFormLayout()
        self.paper = QComboBox()
        self.paper.addItems(list(export.PAPER_SIZES))
        form.addRow("Paper:", self.paper)
        self.orient = QComboBox()
        self.orient.addItems(["Automatic (fewest pages)", "Portrait", "Landscape"])
        form.addRow("Orientation:", self.orient)
        self.overlap = QDoubleSpinBox()
        self.overlap.setRange(0.0, 1.0)
        self.overlap.setSingleStep(0.125)
        self.overlap.setValue(0.25)
        self.overlap.setSuffix(" in")
        form.addRow("Page overlap:", self.overlap)
        self.labels = QCheckBox("Print each tile's code on top of it")
        form.addRow("", self.labels)
        self.res = QComboBox()
        self.res.addItems(["150 dots per inch (standard)", "300 dots per inch (sharper, bigger files)"])
        form.addRow("Detail:", self.res)
        self.skip_blank = QCheckBox("Skip pages with nothing on them")
        self.skip_blank.setChecked(True)
        form.addRow("", self.skip_blank)
        lay.addLayout(form)

        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #555")
        lay.addWidget(self.note)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.go = bb.addButton("Save…", QDialogButtonBox.ButtonRole.AcceptRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._update()

    def kind(self) -> str:
        b = self.kind_group.checkedButton()
        return b.property("kind") if b else "pages_pdf"

    def _update(self):
        k = self.kind()
        paged = k in ("pages_pdf", "overview_pdf")
        self.paper.setEnabled(paged)
        self.orient.setEnabled(paged)
        self.overlap.setEnabled(k == "pages_pdf")
        self.skip_blank.setEnabled(k == "pages_pdf")
        self.res.setEnabled(k in ("pages_pdf", "single_png", "single_pdf"))
        self.labels.setEnabled(True)
        notes = {
            "overview_pdf": "Shrinks the whole map to fit one sheet — handy for the GM's overview.",
            "overview_png": "A picture of the whole map for screens, Roll20, or Foundry.",
            "pages_pdf": "Prints at exactly 1 inch per grid square. In the print window choose "
                         "Scale 100% (or 'Actual size') — not 'Fit to page'. Page 1 shows how the "
                         "pages fit together; every page has a 1-inch check square.",
            "single_png": "One image of the whole map at true size, tagged with its DPI. "
                          "Large maps make large files.",
            "single_pdf": "One page as big as the whole map — for a print shop or plotter. "
                          "PDF pages are limited to 200 inches on a side.",
        }
        self.note.setText(notes[k])

    def options(self) -> dict:
        orient = ["auto", "portrait", "landscape"][self.orient.currentIndex()]
        return {
            "kind": self.kind(),
            "setup": export.PageSetup(self.paper.currentText(), orient),
            "overlap": self.overlap.value(),
            "labels": self.labels.isChecked(),
            "ppi": 300 if self.res.currentIndex() == 1 else 150,
            "skip_blank": self.skip_blank.isChecked(),
        }


class ExportWorker(QThread):
    progress = Signal(int, int)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, doc: MapDocument, library: TileLibrary, path: str, opts: dict):
        super().__init__()
        # work on a private copy so editing during export can't interfere
        self.doc = MapDocument.from_dict(doc.to_dict())
        self.renderer = TileRenderer(library, cache_items=32)
        self.path = path
        self.opts = opts

    def run(self):
        o = self.opts
        k = o["kind"]
        cb = lambda a, b: self.progress.emit(a, b)
        try:
            if k == "overview_pdf":
                export.overview_pdf(self.doc, self.renderer, self.path, o["setup"], o["labels"])
            elif k == "overview_png":
                export.overview_png(self.doc, self.renderer, self.path, o["labels"])
            elif k == "pages_pdf":
                export.paged_pdf(self.doc, self.renderer, self.path, o["setup"], o["overlap"],
                                 o["ppi"], o["skip_blank"], o["labels"], cb)
            elif k == "single_png":
                export.fullsize_png(self.doc, self.renderer, self.path, o["ppi"], o["labels"], cb)
            elif k == "single_pdf":
                export.fullsize_pdf_single(self.doc, self.renderer, self.path, o["ppi"], cb)
            self.done.emit(self.path)
        except export.ExportError as e:
            self.failed.emit(str(e))
        except Exception:
            self.failed.emit(traceback.format_exc())


def run_export(parent, doc: MapDocument, library: TileLibrary, kind: str = "pages_pdf",
               default_dir: str = "") -> None:
    if not doc.placements:
        QMessageBox.information(parent, "Nothing to export", "Place some tiles on the map first.")
        return
    dlg = ExportDialog(doc, parent, kind)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return
    opts = dlg.options()
    ext = ".png" if opts["kind"].endswith("png") else ".pdf"
    suffix = {"overview_pdf": " overview", "overview_png": " overview",
              "pages_pdf": " pages", "single_png": " full size", "single_pdf": " full size"}[opts["kind"]]
    if opts["labels"]:
        suffix += " with codes"
    name = (doc.title or "Map") + suffix + ext
    path, _ = QFileDialog.getSaveFileName(parent, "Save as", os.path.join(default_dir, name),
                                          "PNG image (*.png)" if ext == ".png" else "PDF (*.pdf)")
    if not path:
        return
    if not path.lower().endswith(ext):
        path += ext
    prog = QProgressDialog("Rendering…", "", 0, 0, parent)
    prog.setCancelButton(None)
    prog.setWindowTitle("Exporting")
    prog.setWindowModality(Qt.WindowModality.WindowModal)
    prog.setMinimumDuration(0)
    prog.show()
    worker = ExportWorker(doc, library, path, opts)
    parent._export_worker = worker     # keep alive

    def on_progress(a, b):
        prog.setMaximum(b)
        prog.setValue(a)

    def on_done(p):
        prog.close()
        msg = f"Saved:\n{p}"
        if opts["kind"] in ("pages_pdf", "single_pdf", "single_png"):
            msg += "\n\nWhen printing, set Scale to 100% (Actual size) so 1 inch prints as 1 inch."
        box = QMessageBox(QMessageBox.Icon.Information, "Export finished", msg, parent=parent)
        reveal = box.addButton("Show in Finder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is reveal:
            import subprocess
            subprocess.Popen(["open", "-R", p])

    def on_fail(msg):
        prog.close()
        QMessageBox.warning(parent, "Export failed", msg)

    worker.progress.connect(on_progress)
    worker.done.connect(on_done)
    worker.failed.connect(on_fail)
    worker.start()
