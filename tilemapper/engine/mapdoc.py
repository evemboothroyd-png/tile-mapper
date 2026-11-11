"""The map document: which tile sits where, and how it's turned.

All positions are in inches. A placement's (x, y) is the top-left corner of
its footprint after rotation. The map has no fixed size; its bounds are just
the box around everything placed on it.

Saved as JSON with the .tmap extension. Tiles are referenced by their path
relative to the tile library root, so a map works on any computer that has
the same tiles in the same folders.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple

FORMAT_VERSION = 1
TILE_UNIT_IN = 6.0           # "whole tile" snap step
FINE_UNIT_IN = 1.0           # snap step for odd-sized and floating pieces


@dataclass
class Placement:
    tile_id: str
    x: float                 # inches, top-left of rotated footprint
    y: float
    w_in: float              # unrotated tile size in inches
    h_in: float
    rotation: int = 0        # 0, 90, 180, 270 clockwise
    flip_h: bool = False     # mirror left-right (applied before rotation)
    flip_v: bool = False     # flip top-bottom (applied before rotation)
    floating: bool = False   # drawn above ordinary tiles; white surround clear
    code: str = ""           # kept so missing tiles can still be identified
    uid: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @property
    def footprint(self) -> Tuple[float, float]:
        if self.rotation in (90, 270):
            return self.h_in, self.w_in
        return self.w_in, self.h_in

    @property
    def rect(self) -> Tuple[float, float, float, float]:
        fw, fh = self.footprint
        return self.x, self.y, self.x + fw, self.y + fh


class MapDocument:
    def __init__(self):
        self.placements: List[Placement] = []
        self.title = "Untitled map"
        self.library_hint = ""     # library root used when saved (informational)

    # ----------------------------------------------------------- ordering
    def draw_order(self) -> List[Placement]:
        """Ordinary tiles first (in placement order), floating pieces on top."""
        base = [p for p in self.placements if not p.floating]
        top = [p for p in self.placements if p.floating]
        return base + top

    # ------------------------------------------------------------ editing
    def add(self, p: Placement, replace_same_spot: bool = True) -> Placement:
        if replace_same_spot and not p.floating:
            self.placements = [q for q in self.placements
                               if q.floating or q.rect != p.rect]
        self.placements.append(p)
        return p

    def remove(self, uid: str):
        self.placements = [p for p in self.placements if p.uid != uid]

    def find(self, uid: str) -> Optional[Placement]:
        for p in self.placements:
            if p.uid == uid:
                return p
        return None

    def bring_to_front(self, uid: str):
        p = self.find(uid)
        if p:
            self.placements.remove(p)
            self.placements.append(p)

    # ------------------------------------------------------------- bounds
    def bounds(self) -> Optional[Tuple[float, float, float, float]]:
        if not self.placements:
            return None
        rs = [p.rect for p in self.placements]
        return (min(r[0] for r in rs), min(r[1] for r in rs),
                max(r[2] for r in rs), max(r[3] for r in rs))

    # ----------------------------------------------------------- file I/O
    def to_dict(self):
        return {
            "format": "tilemapper-map",
            "version": FORMAT_VERSION,
            "title": self.title,
            "library_hint": self.library_hint,
            "placements": [asdict(p) for p in self.placements],
        }

    def save(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=1)

    @staticmethod
    def from_dict(d) -> "MapDocument":
        if d.get("format") != "tilemapper-map":
            raise ValueError("This isn't a Tile Mapper map file.")
        doc = MapDocument()
        doc.title = d.get("title", doc.title)
        doc.library_hint = d.get("library_hint", "")
        for pd in d.get("placements", []):
            pd = dict(pd)
            pd["rotation"] = int(pd.get("rotation", 0)) % 360
            doc.placements.append(Placement(**pd))
        return doc

    @staticmethod
    def load(path: str) -> "MapDocument":
        with open(path, encoding="utf-8") as f:
            return MapDocument.from_dict(json.load(f))


def snap(value: float, step: float) -> float:
    return round(value / step) * step
