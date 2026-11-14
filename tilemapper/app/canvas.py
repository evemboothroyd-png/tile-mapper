"""The map canvas: an endless sheet you drop tiles onto."""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Qt, QPointF, QRectF, Signal, QEvent
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QPixmap, QKeyEvent
from PySide6.QtWidgets import (QApplication, QGraphicsItem, QGraphicsPixmapItem,
                               QGraphicsScene, QGraphicsView, QStyleOptionGraphicsItem)

from ..engine import MapDocument, Placement, TileRenderer, TILE_UNIT_IN, FINE_UNIT_IN
from .qtutil import pil_to_qpixmap

SCENE_PER_IN = 100.0          # scene units per inch
PREVIEW_PPI = 75              # pixels per inch used for on-screen tiles
MIME_TILE = "application/x-tilemapper-tile"
WORLD_IN = 2000.0             # the sheet extends this far each way (≈166 feet)


def snap_step(p: Placement) -> float:
    """Every tile moves one grid square (1 inch) at a time."""
    return FINE_UNIT_IN


class TileItem(QGraphicsPixmapItem):
    def __init__(self, placement: Placement, pixmap: Optional[QPixmap], scene: "MapScene"):
        super().__init__()
        self.placement = placement
        self._map_scene = scene
        self._press_pos = None
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                      | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.setShapeMode(QGraphicsPixmapItem.ShapeMode.BoundingRectShape)
        self.set_pixmap(pixmap)

    def set_pixmap(self, pixmap: Optional[QPixmap]):
        p = self.placement
        fw, fh = p.footprint
        if pixmap is None or pixmap.isNull():
            pm = QPixmap(max(1, int(fw * PREVIEW_PPI)), max(1, int(fh * PREVIEW_PPI)))
            pm.fill(QColor(225, 225, 225))
            painter = QPainter(pm)
            painter.setPen(QColor(90, 90, 90))
            painter.drawRect(0, 0, pm.width() - 1, pm.height() - 1)
            painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, f"Missing\n{p.code}")
            painter.end()
            pixmap = pm
        self.setPixmap(pixmap)
        # scale so the pixmap covers exactly the footprint in inches
        self.setScale(1.0)
        sx = fw * SCENE_PER_IN / max(1, pixmap.width())
        sy = fh * SCENE_PER_IN / max(1, pixmap.height())
        from PySide6.QtGui import QTransform
        self.setTransform(QTransform.fromScale(sx, sy))
        self.setPos(p.x * SCENE_PER_IN, p.y * SCENE_PER_IN)
        self.setZValue(self._map_scene.z_for(p))
        info = self._map_scene.renderer.library.get(p.tile_id)
        title = f" — {info.title}" if info and info.title else ""
        extras = []
        if p.rotation:
            extras.append(f"turned {p.rotation}°")
        if p.flip_h:
            extras.append("mirrored")
        if p.flip_v:
            extras.append("flipped")
        self.setToolTip(f"{p.code}{title}" + (f"\n({', '.join(extras)})" if extras else ""))

    # --- snapping while dragging
    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene():
            step = snap_step(self.placement) * SCENE_PER_IN
            x = round(value.x() / step) * step
            y = round(value.y() / step) * step
            return QPointF(x, y)
        return super().itemChange(change, value)

    def mousePressEvent(self, event):
        self._press_pos = QPointF(self.pos())
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self._press_pos is not None and self._press_pos != self.pos():
            self._map_scene.items_moved()
        self._press_pos = None

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget=None):
        # draw the tile without Qt's thin dashed outline, then our own
        from PySide6.QtWidgets import QStyle
        opt = QStyleOptionGraphicsItem(option)
        opt.state &= ~QStyle.StateFlag.State_Selected
        super().paint(painter, opt, widget)
        if self.isSelected():
            pen = QPen(QColor(30, 120, 255), 3)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.boundingRect().adjusted(0.5, 0.5, -0.5, -0.5))


BG_Z_UNDER = -1.0e7
BG_Z_OVER = 1.0e7


class BackgroundItem(QGraphicsPixmapItem):
    """The on-screen reference image. Lives in the scene so it pans and zooms with the
    tiles, but is never part of the map's printing or export."""

    def __init__(self, pixmap: QPixmap, scene: "MapScene"):
        super().__init__(pixmap)
        self._map_scene = scene
        self._press_pos = None
        self.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.setShapeMode(QGraphicsPixmapItem.ShapeMode.BoundingRectShape)
        self.set_movable(False)

    def apply(self, bg: dict):
        from PySide6.QtGui import QTransform
        s = float(bg["in_per_px"]) * SCENE_PER_IN
        self.setTransform(QTransform.fromScale(s, s))
        self.setPos(float(bg["x"]) * SCENE_PER_IN, float(bg["y"]) * SCENE_PER_IN)
        self.setOpacity(max(0.0, min(1.0, float(bg["opacity"]))))
        self.setZValue(BG_Z_OVER if bg.get("on_top") else BG_Z_UNDER)
        self.setVisible(bool(bg.get("visible", True)))

    def set_movable(self, on: bool):
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, on)
        # when not moving, clicks pass straight through to the tiles (even when on top)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton if on else Qt.MouseButton.NoButton)
        self.setCursor(Qt.CursorShape.SizeAllCursor if on else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event):
        self._press_pos = QPointF(self.pos())
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self._press_pos is not None and self._press_pos != self.pos():
            self._map_scene.background_moved()
        self._press_pos = None


class MapScene(QGraphicsScene):
    changed_doc = Signal(str)      # description of the change (for undo)
    background_changed = Signal()  # the reference image was moved / resized / restyled

    def __init__(self, doc: MapDocument, renderer: TileRenderer, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.renderer = renderer
        self.items_by_uid: Dict[str, TileItem] = {}
        self.bg_item: Optional[BackgroundItem] = None
        self._bg_pixmap: Optional[QPixmap] = None
        self._bg_path: Optional[str] = None
        self._bg_moving = False
        w = WORLD_IN * SCENE_PER_IN
        self.setSceneRect(-w, -w, 2 * w, 2 * w)
        self.setBackgroundBrush(QBrush(QColor(52, 56, 62)))
        self.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.NoIndex)

    # -------------------------------------------------------------- items
    def z_for(self, p: Placement) -> float:
        try:
            return float(self.doc.placements.index(p))
        except ValueError:
            return 0.0

    def pixmap_for(self, p: Placement) -> Optional[QPixmap]:
        im = self.renderer.tile_image(p, PREVIEW_PPI)
        return pil_to_qpixmap(im) if im is not None else None

    def rebuild(self):
        self.clear()
        self.items_by_uid = {}
        self.bg_item = None
        for p in self.doc.placements:
            self._add_item(p)
        self.refresh_background()
        self.set_background_moving(self._bg_moving)

    # --------------------------------------------------------- background
    def background_pixmap(self, path: str) -> Optional[QPixmap]:
        if path != self._bg_path:
            pm = QPixmap(path)
            self._bg_pixmap = None if pm.isNull() else pm
            self._bg_path = path
        return self._bg_pixmap

    def refresh_background(self) -> bool:
        """Create, update or remove the reference image to match doc.background.
        Returns False if the image file couldn't be loaded."""
        bg = self.doc.background
        if not bg:
            if self.bg_item is not None:
                self.removeItem(self.bg_item)
                self.bg_item = None
            return True
        pm = self.background_pixmap(bg["path"])
        if pm is None:
            if self.bg_item is not None:
                self.removeItem(self.bg_item)
                self.bg_item = None
            return False
        if self.bg_item is None or self.bg_item.pixmap().cacheKey() != pm.cacheKey():
            if self.bg_item is not None:
                self.removeItem(self.bg_item)
            self.bg_item = BackgroundItem(pm, self)
            self.addItem(self.bg_item)
            self.bg_item.set_movable(self._bg_moving)
        self.bg_item.apply(bg)
        return True

    def background_size_px(self):
        pm = self._bg_pixmap
        return (pm.width(), pm.height()) if pm is not None else (0, 0)

    def set_background_moving(self, on: bool):
        """Move mode: the image can be dragged and tiles ignore the mouse."""
        self._bg_moving = on
        if self.bg_item is not None:
            self.bg_item.set_movable(on)
            if on:
                self.clearSelection()
        buttons = Qt.MouseButton.NoButton if on else Qt.MouseButton.LeftButton
        for item in self.items_by_uid.values():
            item.setAcceptedMouseButtons(buttons)

    def background_moved(self):
        if self.bg_item is not None and self.doc.background:
            self.doc.background["x"] = round(self.bg_item.pos().x() / SCENE_PER_IN, 4)
            self.doc.background["y"] = round(self.bg_item.pos().y() / SCENE_PER_IN, 4)
            self.background_changed.emit()

    def _add_item(self, p: Placement) -> TileItem:
        item = TileItem(p, self.pixmap_for(p), self)
        if self._bg_moving:
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.addItem(item)
        self.items_by_uid[p.uid] = item
        return item

    def refresh_item(self, uid: str):
        item = self.items_by_uid.get(uid)
        if item:
            item.set_pixmap(self.pixmap_for(item.placement))

    def refresh_z(self):
        for item in self.items_by_uid.values():
            p = item.placement
            item.setZValue(self.z_for(p))

    # ------------------------------------------------------------ editing
    def place(self, p: Placement, select: bool = True) -> TileItem:
        self.doc.add(p)
        item = self._add_item(p)
        self.refresh_z()
        if select:
            self.clearSelection()
            item.setSelected(True)
        self.changed_doc.emit(f"Place {p.code}")
        return item

    def selected_items(self) -> List[TileItem]:
        return [i for i in self.selectedItems() if isinstance(i, TileItem)]

    def items_moved(self):
        for item in self.selected_items() or list(self.items_by_uid.values()):
            p = item.placement
            p.x = round(item.pos().x() / SCENE_PER_IN, 4)
            p.y = round(item.pos().y() / SCENE_PER_IN, 4)
        self.changed_doc.emit("Move")

    def delete_selected(self):
        sel = self.selected_items()
        if not sel:
            return
        for item in sel:
            self.doc.remove(item.placement.uid)
            self.items_by_uid.pop(item.placement.uid, None)
            self.removeItem(item)
        self.changed_doc.emit("Delete")

    def modify_selected(self, fn: Callable[[Placement], None], label: str):
        sel = self.selected_items()
        if not sel:
            return
        for item in sel:
            fn(item.placement)
            item.set_pixmap(self.pixmap_for(item.placement))
        self.refresh_z()
        self.changed_doc.emit(label)

    def duplicate_selected(self):
        sel = self.selected_items()
        if not sel:
            return
        from dataclasses import replace
        import uuid
        new_items = []
        for item in sel:
            p = item.placement
            step = snap_step(p)
            q = replace(p, uid=uuid.uuid4().hex[:12], x=p.x + step, y=p.y + step)
            self.doc.placements.append(q)
            new_items.append(self._add_item(q))
        self.refresh_z()
        self.clearSelection()
        for it in new_items:
            it.setSelected(True)
        self.changed_doc.emit("Duplicate")

    def _layer(self, method: str, label: str):
        uids = [i.placement.uid for i in self.selected_items()]
        if not uids:
            return
        before = [p.uid for p in self.doc.placements]
        getattr(self.doc, method)(uids)
        if [p.uid for p in self.doc.placements] != before:
            self.refresh_z()
            self.changed_doc.emit(label)

    def bring_to_front(self):
        self._layer("bring_to_front", "Bring to front")

    def send_to_back(self):
        self._layer("send_to_back", "Send to back")


def rotate(p: Placement, deg: int):
    p.rotation = (p.rotation + deg) % 360


class MapView(QGraphicsView):
    zoom_changed = Signal(float)
    tile_dropped = Signal(str, float, float)     # tile_id, x_in, y_in (centre)
    measure_done = Signal(QPointF, QPointF)      # two scene points picked for "Set Scale"
    measure_cancelled = Signal()

    def __init__(self, scene: MapScene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.BoundingRectViewportUpdate)
        self.setAcceptDrops(True)
        self._space = False
        self._measuring = False
        self._measure_pts: List[QPointF] = []
        self._measure_items = []
        self._zoom = 1.0
        self.set_zoom(0.25)
        self.centerOn(0, 0)

    # ---------------------------------------------------------------- zoom
    def set_zoom(self, z: float, anchor_under_mouse: bool = False):
        z = max(0.01, min(4.0, z))
        if not anchor_under_mouse:
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        factor = z / self._zoom
        self.scale(factor, factor)
        self._zoom = z
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.zoom_changed.emit(z)

    def zoom_by(self, f: float, under_mouse: bool = True):
        self.set_zoom(self._zoom * f, under_mouse)

    def zoom_to_fit(self):
        b = self.scene().doc.bounds()
        bgi = self.scene().bg_item
        if not b and bgi is not None and bgi.isVisible():
            r = bgi.sceneBoundingRect()
            b = (r.left() / SCENE_PER_IN, r.top() / SCENE_PER_IN,
                 r.right() / SCENE_PER_IN, r.bottom() / SCENE_PER_IN)
        if not b:
            self.set_zoom(0.25)
            self.centerOn(0, 0)
            return
        r = QRectF(b[0] * SCENE_PER_IN, b[1] * SCENE_PER_IN,
                   (b[2] - b[0]) * SCENE_PER_IN, (b[3] - b[1]) * SCENE_PER_IN)
        r = r.adjusted(-r.width() * 0.05 - 50, -r.height() * 0.05 - 50, r.width() * 0.05 + 50,
                       r.height() * 0.05 + 50)
        vw, vh = self.viewport().width(), self.viewport().height()
        z = min(vw / r.width(), vh / r.height())
        self.set_zoom(z)
        self.centerOn(r.center())

    def wheelEvent(self, event):
        """Mouse wheel zooms. Trackpad two-finger scroll pans; with ⌘ held it zooms."""
        mods = event.modifiers()
        zoom_mod = mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier)
        from_trackpad = (event.phase() != Qt.ScrollPhase.NoScrollPhase
                         or not event.pixelDelta().isNull())
        if zoom_mod or not from_trackpad:
            dy = event.angleDelta().y() or event.pixelDelta().y()
            if dy:
                self.zoom_by(1.0015 ** dy)
            event.accept()
            return
        super().wheelEvent(event)   # two-finger scroll pans

    def _pinch(self, e) -> bool:
        try:
            if e.type() == QEvent.Type.NativeGesture and \
                    e.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                self.zoom_by(1.0 + e.value())
                e.accept()
                return True
        except AttributeError:
            pass
        return False

    def viewportEvent(self, e):
        # On a Mac the pinch arrives at the viewport (the drawing area), not the view itself.
        if self._pinch(e):
            return True
        return super().viewportEvent(e)

    def event(self, e):
        if self._pinch(e):
            return True
        return super().event(e)

    # ------------------------------------------- picking two points to set scale
    def start_measure(self):
        self._clear_measure()
        self._measuring = True
        self._measure_pts = []
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        self.setFocus()

    def stop_measure(self):
        self._measuring = False
        self._clear_measure()
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.viewport().unsetCursor()

    def _clear_measure(self):
        for it in self._measure_items:
            try:
                if it.scene() is not None:
                    self.scene().removeItem(it)
            except RuntimeError:      # already gone (scene was rebuilt)
                pass
        self._measure_items = []

    def _mark(self, pt: QPointF):
        pen = QPen(QColor(255, 40, 40), 3)
        pen.setCosmetic(True)
        r = 6 / max(self._zoom, 1e-6)
        dot = self.scene().addEllipse(pt.x() - r, pt.y() - r, 2 * r, 2 * r, pen)
        dot.setZValue(2.0e7)
        self._measure_items.append(dot)
        if len(self._measure_pts) == 2:
            a, b = self._measure_pts
            line = self.scene().addLine(a.x(), a.y(), b.x(), b.y(), pen)
            line.setZValue(2.0e7)
            self._measure_items.append(line)

    # ------------------------------------------------- space-bar panning
    def keyPressEvent(self, e: QKeyEvent):
        if self._measuring and e.key() == Qt.Key.Key_Escape:
            self.stop_measure()
            self.measure_cancelled.emit()
            return
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._space = True
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.setInteractive(False)
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e: QKeyEvent):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._space = False
            self.setDragMode(QGraphicsView.DragMode.NoDrag if self._measuring
                             else QGraphicsView.DragMode.RubberBandDrag)
            self.setInteractive(True)
            return
        super().keyReleaseEvent(e)

    def mousePressEvent(self, e):
        if self._measuring and e.button() == Qt.MouseButton.LeftButton and not self._space:
            pt = self.mapToScene(e.position().toPoint())
            self._measure_pts.append(pt)
            self._mark(pt)
            if len(self._measure_pts) == 2:
                a, b = self._measure_pts
                self._measuring = False
                self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
                self.viewport().unsetCursor()
                self.measure_done.emit(a, b)
            e.accept()
            return
        if e.button() == Qt.MouseButton.MiddleButton:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.setInteractive(False)
            fake = type(e)(e.type(), e.position(), e.globalPosition(), Qt.MouseButton.LeftButton,
                           Qt.MouseButton.LeftButton, e.modifiers())
            super().mousePressEvent(fake)
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.MiddleButton:
            fake = type(e)(e.type(), e.position(), e.globalPosition(), Qt.MouseButton.LeftButton,
                           Qt.MouseButton.NoButton, e.modifiers())
            super().mouseReleaseEvent(fake)
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            self.setInteractive(True)
            return
        super().mouseReleaseEvent(e)

    # -------------------------------------------------------- drag & drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME_TILE):
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(MIME_TILE):
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if e.mimeData().hasFormat(MIME_TILE):
            tile_id = bytes(e.mimeData().data(MIME_TILE)).decode("utf-8")
            pt = self.mapToScene(e.position().toPoint())
            self.tile_dropped.emit(tile_id, pt.x() / SCENE_PER_IN, pt.y() / SCENE_PER_IN)
            e.acceptProposedAction()
        else:
            super().dropEvent(e)

    def visible_center_in(self):
        c = self.mapToScene(self.viewport().rect().center())
        return c.x() / SCENE_PER_IN, c.y() / SCENE_PER_IN
