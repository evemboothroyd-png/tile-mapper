"""Side panel for the on-screen reference image (a photo or plan to trace tiles over).

The image is for viewing only: it is saved with the map so it comes back when the map
is reopened, but it is never printed or exported.
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QCheckBox,
                               QSlider, QLabel, QDoubleSpinBox, QRadioButton, QButtonGroup,
                               QFormLayout, QSizePolicy)

FEET_PER_SQUARE = 5.0


class BackgroundPanel(QWidget):
    choose_requested = Signal()
    remove_requested = Signal()
    measure_requested = Signal()
    move_toggled = Signal(bool)
    changed = Signal(dict)          # partial update: keys of doc.background to change
    width_changed = Signal(float)   # new image width in inches (keeps the centre fixed)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._updating = False
        lay = QVBoxLayout(self)

        row = QHBoxLayout()
        self.choose = QPushButton("Choose Image…")
        self.choose.clicked.connect(self.choose_requested)
        self.remove = QPushButton("Remove")
        self.remove.clicked.connect(self.remove_requested)
        row.addWidget(self.choose)
        row.addWidget(self.remove)
        lay.addLayout(row)

        self.name = QLabel("No image")
        self.name.setWordWrap(True)
        self.name.setStyleSheet("color: #666")
        lay.addWidget(self.name)

        self.visible = QCheckBox("Show image")
        self.visible.toggled.connect(lambda v: self._emit({"visible": v}))
        lay.addWidget(self.visible)

        form = QFormLayout()
        orow = QHBoxLayout()
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(5, 100)
        self.opacity_lbl = QLabel()
        self.opacity_lbl.setMinimumWidth(36)
        self.opacity.valueChanged.connect(self._on_opacity)
        orow.addWidget(self.opacity)
        orow.addWidget(self.opacity_lbl)
        form.addRow("Opacity:", orow)

        prow = QHBoxLayout()
        self.under = QRadioButton("Under tiles")
        self.over = QRadioButton("Over tiles")
        self.place_group = QButtonGroup(self)
        self.place_group.addButton(self.under)
        self.place_group.addButton(self.over)
        self.over.toggled.connect(lambda v: self._emit({"on_top": v}))
        prow.addWidget(self.under)
        prow.addWidget(self.over)
        form.addRow("Layer:", prow)

        self.width = QDoubleSpinBox()
        self.width.setRange(1.0, 5000.0)
        self.width.setDecimals(1)
        self.width.setSingleStep(1.0)
        self.width.setSuffix(" in")
        self.width.setKeyboardTracking(False)
        self.width.valueChanged.connect(self._on_width)
        form.addRow("Image width:", self.width)
        self.width_ft = QLabel()
        self.width_ft.setStyleSheet("color: #666")
        form.addRow("", self.width_ft)
        lay.addLayout(form)

        self.measure = QPushButton("Set Scale from a Known Distance…")
        self.measure.setToolTip("Click two points on the image, then type the real distance "
                                "between them. The image is resized so 5 ft = 1 square.")
        self.measure.clicked.connect(self.measure_requested)
        lay.addWidget(self.measure)

        self.move = QPushButton("Move Image")
        self.move.setCheckable(True)
        self.move.setToolTip("While this is on, drag the image to line it up. "
                             "Tiles can't be selected until you turn it off.")
        self.move.toggled.connect(self._on_move)
        lay.addWidget(self.move)

        help_ = QLabel("For viewing only — the image is saved with the map but never "
                       "printed or exported. <b>B</b> flips it over / under the tiles.")
        help_.setWordWrap(True)
        help_.setStyleSheet("color: #666")
        lay.addWidget(help_)
        lay.addStretch(1)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.set_state(None, (0, 0))

    # ------------------------------------------------------------ helpers
    def _emit(self, d: dict):
        if not self._updating:
            self.changed.emit(d)

    def _on_opacity(self, v: int):
        self.opacity_lbl.setText(f"{v}%")
        self._emit({"opacity": v / 100.0})

    def _on_width(self, v: float):
        self.width_ft.setText(f"= {v * FEET_PER_SQUARE:,.0f} ft across (5 ft per square)")
        if not self._updating:
            self.width_changed.emit(v)

    def _on_move(self, on: bool):
        self.move.setText("Done Moving" if on else "Move Image")
        if not self._updating:
            self.move_toggled.emit(on)

    def set_moving(self, on: bool):
        self._updating = True
        self.move.setChecked(on)
        self.move.setText("Done Moving" if on else "Move Image")
        self._updating = False

    def set_state(self, bg, size_px):
        """Show the current background settings (bg may be None)."""
        self._updating = True
        has = bool(bg)
        for w in (self.remove, self.visible, self.opacity, self.under, self.over, self.width,
                  self.measure, self.move):
            w.setEnabled(has)
        if has:
            self.name.setText(os.path.basename(bg["path"]))
            self.name.setToolTip(bg["path"])
            self.visible.setChecked(bool(bg.get("visible", True)))
            self.opacity.setValue(int(round(float(bg["opacity"]) * 100)))
            (self.over if bg.get("on_top") else self.under).setChecked(True)
            w_in = size_px[0] * float(bg["in_per_px"])
            self.width.setValue(w_in)
            self.width_ft.setText(f"= {w_in * FEET_PER_SQUARE:,.0f} ft across (5 ft per square)")
        else:
            self.name.setText("No image. Choose a photo or plan to trace your tiles over.")
            self.name.setToolTip("")
            self.visible.setChecked(True)
            self.opacity.setValue(50)
            self.under.setChecked(True)
            self.width_ft.setText("")
            self.move.setChecked(False)
            self.move.setText("Move Image")
        self.opacity_lbl.setText(f"{self.opacity.value()}%")
        self._updating = False
