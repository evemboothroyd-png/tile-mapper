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
BACKGROUND_DEFAULTS = {"x": 0.0, "y": 0.0, "in_per_px": 0.1, "opacity": 0.5,
                       "on_top": False, "visible": True}


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
    floating: bool = False   # legacy flag: white surround drawn see-through
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
        # On-screen reference image (never printed or exported). Keys: path, x, y (inches,
        # top-left), in_per_px (inches per image pixel), opacity (0-1), on_top, visible.
        self.background: Optional[dict] = None

    # ----------------------------------------------------------- ordering
    def draw_order(self) -> List[Placement]:
        """Bottom to top: the order of the placement list."""
        return list(self.placements)

    # ------------------------------------------------------------ editing
    def add(self, p: Placement) -> Placement:
        """New tiles go on top. Nothing underneath is replaced."""
        self.placements.append(p)
        return p

    def remove(self, uid: str):
        self.placements = [p for p in self.placements if p.uid != uid]

    def find(self, uid: str) -> Optional[Placement]:
        for p in self.placements:
            if p.uid == uid:
                return p
        return None

    # ------------------------------------------------------------ layering
    def _split(self, uids):
        uids = set(uids)
        chosen = [p for p in self.placements if p.uid in uids]
        rest = [p for p in self.placements if p.uid not in uids]
        return chosen, rest

    def bring_to_front(self, uids):
        """Put the chosen tiles above everything (keeping their own order)."""
        if isinstance(uids, str):
            uids = [uids]
        chosen, rest = self._split(uids)
        self.placements = rest + chosen

    def send_to_back(self, uids):
        """Put the chosen tiles under everything (keeping their own order)."""
        if isinstance(uids, str):
            uids = [uids]
        chosen, rest = self._split(uids)
        self.placements = chosen + rest

    @staticmethod
    def overlaps(a: "Placement", b: "Placement") -> bool:
        ax, ay, aw, ah = a.rect
        bx, by, bw, bh = b.rect
        return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah

    def bring_forward(self, uids) -> bool:
        """Lift each chosen tile just above the next tile above it that it overlaps."""
        if isinstance(uids, str):
            uids = [uids]
        uids = set(uids)
        changed = False
        for p in [q for q in reversed(self.placements) if q.uid in uids]:
            i = self.placements.index(p)
            for j in range(i + 1, len(self.placements)):
                q = self.placements[j]
                if q.uid not in uids and self.overlaps(p, q):
                    self.placements.pop(i)
                    self.placements.insert(j, p)
                    changed = True
                    break
        return changed

    def send_backward(self, uids) -> bool:
        """Drop each chosen tile just under the next tile below it that it overlaps."""
        if isinstance(uids, str):
            uids = [uids]
        uids = set(uids)
        changed = False
        for p in [q for q in self.placements if q.uid in uids]:
            i = self.placements.index(p)
            for j in range(i - 1, -1, -1):
                q = self.placements[j]
                if q.uid not in uids and self.overlaps(p, q):
                    self.placements.pop(i)
                    self.placements.insert(j, p)
                    changed = True
                    break
        return changed

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
            **({"background": dict(self.background)} if self.background else {}),
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
        bg = d.get("background")
        if isinstance(bg, dict) and bg.get("path"):
            doc.background = dict(BACKGROUND_DEFAULTS, **bg)
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
