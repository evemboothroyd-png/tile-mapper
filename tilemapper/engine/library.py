"""Tile library: finds tiles on disk and works out their real-world size.

Folder layout this expects (it matches the Google Drive layout):

    <library root>/
        Dungeon/DV328 Bonus Intersection.jpg
        Town/Taverns/ATTV101 Adventure Town Taverns Vol. 1.jpg
        ...
        Tile Thumbnails/              <- optional; same sub-folders
            Dungeon/T-DV328.jpg

Anything outside "Tile Thumbnails" that is a .jpg/.jpeg/.png is treated as a
tile. The folder a tile sits in (relative to the root) is its category.

Scale rule (measured on the real tiles):
  * Tiles are drawn on a 1-inch grid at either 150 or 300 pixels per inch.
  * Files 1500 px or more on their short side are 300 ppi, the rest 150 ppi.
  * The size in inches is rounded to a whole inch when within 0.3" of one,
    otherwise to the nearest half inch. Any extra pixels are a print bleed
    and are trimmed evenly from both sides.
  * A per-library override file ("tile_overrides.json" in the root) can set
    ppi and/or inches for any tile by code or relative path.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

from PIL import Image

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
THUMB_DIR_NAME = "Tile Thumbnails"
OVERRIDES_NAME = "tile_overrides.json"
THUMB_SCALE = 0.2235          # matches the existing thumbnails (~200 px per 6")

_CODE_PART = re.compile(r"^[A-Za-z]*\d+[A-Za-z]?$")


def parse_code_title(stem: str) -> Tuple[str, str]:
    """Split a file stem like 'DV328 Bonus Intersection' into code and title.

    Handles multi-token codes such as 'HSWS Deck1 A01 Main Deck'.
    """
    parts = stem.split()
    if not parts:
        return stem, ""
    code_parts = [parts[0]]
    if not any(ch.isdigit() for ch in parts[0]):
        # e.g. HSWS Deck1 A01 ... -> keep taking tokens that look like codes
        for p in parts[1:]:
            if _CODE_PART.match(p):
                code_parts.append(p)
            else:
                break
    code = " ".join(code_parts)
    title = " ".join(parts[len(code_parts):])
    return code, title


def snap_inches(raw: float) -> float:
    nearest = round(raw)
    if abs(raw - nearest) <= 0.3 and nearest > 0:
        return float(nearest)
    return max(0.5, round(raw * 2) / 2)


@dataclass
class TileInfo:
    tile_id: str                 # path relative to library root, '/' separated
    path: str                    # absolute path
    code: str
    title: str
    category: str                # relative folder, '/' separated ('' = root)
    px_w: int
    px_h: int
    ppi: int
    w_in: float
    h_in: float
    crop: Tuple[int, int, int, int]   # left, top, right, bottom in pixels
    thumb_path: Optional[str] = None  # library thumbnail, if one exists
    mtime: float = 0.0

    @property
    def size_label(self) -> str:
        def f(v):
            return f"{v:g}"
        return f'{f(self.w_in)}" × {f(self.h_in)}"'

    def to_json(self):
        d = asdict(self)
        d["crop"] = list(self.crop)
        return d

    @staticmethod
    def from_json(d):
        d = dict(d)
        d["crop"] = tuple(d["crop"])
        return TileInfo(**d)


def measure(px_w: int, px_h: int, override: Optional[dict] = None):
    """Return (ppi, w_in, h_in, crop) for an image of the given pixel size."""
    override = override or {}
    ppi = int(override.get("ppi") or (300 if min(px_w, px_h) >= 1500 else 150))
    if "w_in" in override and "h_in" in override:
        w_in, h_in = float(override["w_in"]), float(override["h_in"])
    else:
        w_in, h_in = snap_inches(px_w / ppi), snap_inches(px_h / ppi)
    need_w, need_h = round(w_in * ppi), round(h_in * ppi)
    # Trim bleed evenly if the file is bigger than the snapped size. If it is
    # slightly smaller we keep it whole and it gets stretched a hair to fit.
    extra_w, extra_h = max(0, px_w - need_w), max(0, px_h - need_h)
    left, top = extra_w // 2, extra_h // 2
    crop = (left, top, px_w - (extra_w - left), px_h - (extra_h - top))
    return ppi, w_in, h_in, crop


class TileLibrary:
    """Scans a folder of tiles. Re-scan any time to pick up new files."""

    def __init__(self, root: str, cache_dir: Optional[str] = None):
        self.root = os.path.abspath(root)
        self.cache_dir = cache_dir
        self.tiles: Dict[str, TileInfo] = {}
        self.overrides: Dict[str, dict] = {}

    # ------------------------------------------------------------- scanning
    def _load_overrides(self):
        p = os.path.join(self.root, OVERRIDES_NAME)
        self.overrides = {}
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    self.overrides = json.load(f)
            except (OSError, ValueError):
                self.overrides = {}

    def _index_cache_path(self):
        if not self.cache_dir:
            return None
        os.makedirs(self.cache_dir, exist_ok=True)
        key = re.sub(r"[^A-Za-z0-9]+", "_", self.root)[-80:]
        return os.path.join(self.cache_dir, f"index_{key}.json")

    def scan(self, progress=None) -> List[TileInfo]:
        """Walk the library. `progress(done, total)` is called if given."""
        self._load_overrides()
        old = {}
        cp = self._index_cache_path()
        if cp and os.path.exists(cp):
            try:
                with open(cp, encoding="utf-8") as f:
                    old = {d["tile_id"]: TileInfo.from_json(d) for d in json.load(f)}
            except (OSError, ValueError, KeyError, TypeError):
                old = {}

        found = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            rel_dir = os.path.relpath(dirpath, self.root)
            if rel_dir == ".":
                rel_dir = ""
            top = rel_dir.split(os.sep)[0] if rel_dir else ""
            if top == THUMB_DIR_NAME or top.startswith("."):
                dirnames[:] = []
                continue
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for fn in sorted(filenames):
                if os.path.splitext(fn)[1].lower() in IMAGE_EXTS and not fn.startswith("."):
                    found.append((rel_dir, fn))

        tiles = {}
        total = len(found)
        for i, (rel_dir, fn) in enumerate(found):
            path = os.path.join(self.root, rel_dir, fn)
            tile_id = "/".join([p for p in rel_dir.split(os.sep) if p] + [fn])
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            code, title = parse_code_title(os.path.splitext(fn)[0])
            ov = self.overrides.get(tile_id) or self.overrides.get(code)
            prev = old.get(tile_id)
            if prev and prev.mtime == mtime and not ov:
                info = prev
                info.path = path
            else:
                try:
                    with Image.open(path) as im:
                        px_w, px_h = im.size
                except Exception:
                    continue
                ppi, w_in, h_in, crop = measure(px_w, px_h, ov)
                info = TileInfo(tile_id, path, code, title,
                                "/".join(p for p in rel_dir.split(os.sep) if p),
                                px_w, px_h, ppi, w_in, h_in, crop, None, mtime)
            info.thumb_path = self._find_library_thumb(info)
            tiles[tile_id] = info
            if progress and (i % 25 == 0 or i == total - 1):
                progress(i + 1, total)

        self.tiles = tiles
        if cp:
            try:
                with open(cp, "w", encoding="utf-8") as f:
                    json.dump([t.to_json() for t in tiles.values()], f)
            except OSError:
                pass
        return list(tiles.values())

    # ----------------------------------------------------------- thumbnails
    def _thumb_candidates(self, info: TileInfo):
        code = info.code
        base = os.path.join(self.root, THUMB_DIR_NAME)
        cat = info.category.split("/") if info.category else []
        yield os.path.join(base, *cat, f"T-{code}.jpg")
        yield os.path.join(base, f"T-{code}.jpg")

    def _find_library_thumb(self, info: TileInfo) -> Optional[str]:
        for p in self._thumb_candidates(info):
            if os.path.exists(p):
                return p
        return None

    def thumbnail_path(self, info: TileInfo) -> str:
        """Library thumbnail if present, otherwise a cached generated one."""
        if info.thumb_path and os.path.exists(info.thumb_path):
            return info.thumb_path
        cache = os.path.join(self.cache_dir or os.path.join(self.root, ".tilemapper_cache"), "thumbs")
        os.makedirs(cache, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", info.tile_id) + f"_{int(info.mtime)}.jpg"
        out = os.path.join(cache, name)
        if not os.path.exists(out):
            make_thumbnail(info, out)
        return out

    def write_missing_thumbnails(self, progress=None) -> List[str]:
        """Create T-CODE.jpg in the library's Tile Thumbnails folder for every
        tile that doesn't have one. Returns the paths written."""
        written = []
        todo = [t for t in self.tiles.values() if not t.thumb_path]
        for i, info in enumerate(todo):
            cat = info.category.split("/") if info.category else []
            out = os.path.join(self.root, THUMB_DIR_NAME, *cat, f"T-{info.code}.jpg")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            if os.path.exists(out):
                info.thumb_path = out
                continue
            make_thumbnail(info, out)
            info.thumb_path = out
            written.append(out)
            if progress:
                progress(i + 1, len(todo))
        return written

    # -------------------------------------------------------------- queries
    def categories(self) -> List[str]:
        return sorted({t.category for t in self.tiles.values()})

    def get(self, tile_id: str) -> Optional[TileInfo]:
        return self.tiles.get(tile_id)

    def search(self, text: str = "", category: Optional[str] = None) -> List[TileInfo]:
        text = text.strip().lower()
        out = []
        for t in self.tiles.values():
            if category and not (t.category == category or t.category.startswith(category + "/")):
                continue
            if text and text not in t.code.lower() and text not in t.title.lower():
                continue
            out.append(t)
        out.sort(key=lambda t: (t.category, natural_key(t.code)))
        return out


def natural_key(s: str):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", s)]


def make_thumbnail(info: TileInfo, out_path: str):
    with Image.open(info.path) as im:
        im = im.convert("RGB").crop(info.crop)
        w = max(1, round(info.w_in * 150 * THUMB_SCALE))
        h = max(1, round(info.h_in * 150 * THUMB_SCALE))
        im = im.resize((w, h), Image.LANCZOS)
        im.save(out_path, quality=85)
