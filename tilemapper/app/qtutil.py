"""Small helpers shared by the window code."""
from __future__ import annotations

import os
import sys

from PIL import Image
from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QImage, QPixmap


def pil_to_qimage(im: Image.Image) -> QImage:
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    data = im.tobytes("raw", "RGBA")
    qimg = QImage(data, im.width, im.height, im.width * 4, QImage.Format.Format_RGBA8888)
    return qimg.copy()   # detach from the Python buffer


def pil_to_qpixmap(im: Image.Image) -> QPixmap:
    return QPixmap.fromImage(pil_to_qimage(im))


def app_data_dir() -> str:
    d = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    if not d:
        d = os.path.join(os.path.expanduser("~"), ".tilemapper")
    os.makedirs(d, exist_ok=True)
    return d


def resource_path(*parts) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, *parts)
