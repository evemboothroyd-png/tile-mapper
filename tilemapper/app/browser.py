"""Left-hand tile browser: folder tree, search box, and thumbnail grid."""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, QSize, QTimer, QMimeData, Signal, QByteArray
from PySide6.QtGui import QIcon, QPixmap, QDrag, QColor, QPainter
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QLineEdit, QTreeWidget, QTreeWidgetItem,
                               QListWidget, QListWidgetItem, QListView, QSplitter, QLabel,
                               QAbstractItemView)

from ..engine import TileLibrary, TileInfo
from .canvas import MIME_TILE

ALL = "\u0000all"
ROLE_ID = Qt.ItemDataRole.UserRole


class TileList(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setIconSize(QSize(96, 96))
        self.setGridSize(QSize(112, 132))
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setWordWrap(True)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)

    def startDrag(self, actions):
        item = self.currentItem()
        if not item:
            return
        md = QMimeData()
        md.setData(MIME_TILE, QByteArray(item.data(ROLE_ID).encode("utf-8")))
        drag = QDrag(self)
        drag.setMimeData(md)
        icon = item.icon()
        if not icon.isNull():
            drag.setPixmap(icon.pixmap(QSize(72, 72)))
        drag.exec(Qt.DropAction.CopyAction)


class TileBrowser(QWidget):
    tile_activated = Signal(str)          # double-click -> place in view centre

    def __init__(self, parent=None):
        super().__init__(parent)
        self.library: Optional[TileLibrary] = None
        self._pending: List[QListWidgetItem] = []
        self._icon_timer = QTimer(self)
        self._icon_timer.setInterval(0)
        self._icon_timer.timeout.connect(self._load_some_icons)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search code or name…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        lay.addWidget(self.search)

        split = QSplitter(Qt.Orientation.Vertical)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(lambda *_: self.refresh())
        split.addWidget(self.tree)
        self.list = TileList()
        self.list.itemDoubleClicked.connect(lambda it: self.tile_activated.emit(it.data(ROLE_ID)))
        split.addWidget(self.list)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 4)
        lay.addWidget(split, 1)
        self.count = QLabel("")
        self.count.setStyleSheet("color: gray")
        lay.addWidget(self.count)

    # ------------------------------------------------------------ library
    def set_library(self, library: Optional[TileLibrary]):
        self.library = library
        self.tree.blockSignals(True)
        self.tree.clear()
        top = QTreeWidgetItem(["All tiles"])
        top.setData(0, ROLE_ID, ALL)
        self.tree.addTopLevelItem(top)
        nodes = {}
        if library:
            for cat in library.categories():
                if not cat:
                    continue
                parent = top
                path = []
                for part in cat.split("/"):
                    path.append(part)
                    key = "/".join(path)
                    if key not in nodes:
                        n = QTreeWidgetItem([part])
                        n.setData(0, ROLE_ID, key)
                        parent.addChild(n)
                        nodes[key] = n
                    parent = nodes[key]
        top.setExpanded(True)
        self.tree.setCurrentItem(top)
        self.tree.blockSignals(False)
        self.refresh()

    def current_category(self) -> Optional[str]:
        it = self.tree.currentItem()
        if not it:
            return None
        v = it.data(0, ROLE_ID)
        return None if v == ALL else v

    def refresh(self):
        self.list.clear()
        self._pending = []
        if not self.library:
            self.count.setText("No tile folder chosen")
            return
        tiles = self.library.search(self.search.text(), self.current_category())
        placeholder = QPixmap(96, 96)
        placeholder.fill(QColor(70, 74, 80))
        ph_icon = QIcon(placeholder)
        for t in tiles:
            it = QListWidgetItem(ph_icon, t.code)
            it.setData(ROLE_ID, t.tile_id)
            it.setToolTip(f"{t.code} — {t.title}\n{t.size_label}   ({t.category or 'top level'})")
            self.list.addItem(it)
            self._pending.append(it)
        self.count.setText(f"{len(tiles)} tiles")
        self._icon_timer.start()

    def _load_some_icons(self):
        if not self._pending or not self.library:
            self._icon_timer.stop()
            return
        batch, self._pending = self._pending[:24], self._pending[24:]
        for it in batch:
            try:
                info = self.library.get(it.data(ROLE_ID))
            except RuntimeError:      # item deleted by a refresh
                continue
            if not info:
                continue
            try:
                path = self.library.thumbnail_path(info)
                pm = QPixmap(path)
                if not pm.isNull():
                    it.setIcon(QIcon(pm))
            except Exception:
                pass
