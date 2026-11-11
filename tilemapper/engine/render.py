"""Draws a map (or any rectangle of it) into an image at a chosen resolution.

Everything here works in inches; `ppi` decides how many pixels an inch gets.
Used by every export, so the printed size always comes from the same math.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from .library import TileLibrary
from .mapdoc import MapDocument, Placement

WHITE = (255, 255, 255)


def font(size_px: int):
    size_px = max(6, int(size_px))
    for name in ("Arial Bold.ttf", "Arial.ttf", "Helvetica.ttc", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size_px)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size_px)
    except TypeError:
        return ImageFont.load_default()


def clear_white_surround(img: Image.Image, threshold: int = 28) -> Image.Image:
    """Make the white area that touches the edges transparent (RGBA result).

    White inside the artwork (not connected to the border) is kept.

    Fast two-stage method: flood-fill the border-connected white on a reduced
    copy (only blocks that are entirely white count, so the fill can't cross a
    line), then grow that region back out at full size through white pixels to
    get crisp edges.
    """
    from PIL import ImageChops, ImageFilter
    rgb = img.convert("RGB")
    w, h = rgb.size
    r, g, b = rgb.split()
    lo = ImageChops.darker(ImageChops.darker(r, g), b)          # min channel
    white = lo.point(lambda v: 255 if v >= 255 - threshold else 0)
    if white.getbbox() is None:                                  # no white at all
        out = rgb.convert("RGBA")
        return out

    step = max(1, -(-max(w, h) // 500))                          # ceil(max/500)
    small = white.reduce(step) if step > 1 else white.copy()
    small = small.point(lambda v: 255 if v == 255 else 0)        # fully white blocks
    sw, sh = small.size
    seeds = [(x, 0) for x in range(sw)] + [(x, sh - 1) for x in range(sw)] + \
            [(0, y) for y in range(sh)] + [(sw - 1, y) for y in range(sh)]
    for sd in seeds:
        if small.getpixel(sd) == 255:
            ImageDraw.floodfill(small, sd, 128, thresh=0)
    region = small.point(lambda v: 255 if v == 128 else 0)
    if step > 1:
        region = region.resize((sw * step, sh * step), Image.NEAREST).crop((0, 0, w, h))
    cur = ImageChops.darker(region, white)
    # grow through white pixels to fill the partly white blocks along the edges
    grow = ImageFilter.MaxFilter(3)
    for _ in range(2 * step + 2):
        nxt = ImageChops.darker(cur.filter(grow), white)
        if ImageChops.difference(nxt, cur).getbbox() is None:
            break
        cur = nxt
    out = rgb.convert("RGBA")
    out.putalpha(ImageChops.invert(cur))
    return out


class TileRenderer:
    """Loads tiles, applies crop / size / mirror / rotation, with a cache."""

    def __init__(self, library: TileLibrary, cache_items: int = 48):
        self.library = library
        self._cache: "OrderedDict[tuple, Image.Image]" = OrderedDict()
        self._cache_items = cache_items

    def tile_image(self, p: Placement, ppi: float) -> Optional[Image.Image]:
        key = (p.tile_id, round(ppi, 3), p.rotation, p.flip_h, p.flip_v)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        info = self.library.get(p.tile_id)
        if info is None:
            return None
        try:
            with Image.open(info.path) as src:
                im = src.convert("RGB").crop(info.crop)
        except Exception:
            return None
        size = (max(1, round(p.w_in * ppi)), max(1, round(p.h_in * ppi)))
        if im.size != size:
            im = im.resize(size, Image.LANCZOS)
        im = clear_white_surround(im)          # white edges are see-through on every tile
        im = transform(im, p.rotation, p.flip_h, p.flip_v)
        self._cache[key] = im
        if len(self._cache) > self._cache_items:
            self._cache.popitem(last=False)
        return im


def transform(im: Image.Image, rotation: int, flip_h: bool, flip_v: bool) -> Image.Image:
    """Mirror/flip first, then rotate clockwise. Same order as the canvas."""
    if flip_h:
        im = im.transpose(Image.FLIP_LEFT_RIGHT)
    if flip_v:
        im = im.transpose(Image.FLIP_TOP_BOTTOM)
    r = rotation % 360
    if r == 90:
        im = im.transpose(Image.ROTATE_270)
    elif r == 180:
        im = im.transpose(Image.ROTATE_180)
    elif r == 270:
        im = im.transpose(Image.ROTATE_90)
    return im


def render_region(doc: MapDocument, renderer: TileRenderer,
                  x0: float, y0: float, x1: float, y1: float, ppi: float,
                  labels: bool = False, background=WHITE,
                  missing_box: bool = True) -> Image.Image:
    """Render the map area between (x0, y0) and (x1, y1) inches."""
    W = max(1, round((x1 - x0) * ppi))
    H = max(1, round((y1 - y0) * ppi))
    canvas = Image.new("RGB", (W, H), background)
    draw = ImageDraw.Draw(canvas)
    order = doc.draw_order()
    for p in order:
        rx0, ry0, rx1, ry1 = p.rect
        if rx1 <= x0 or ry1 <= y0 or rx0 >= x1 or ry0 >= y1:
            continue
        px = round((rx0 - x0) * ppi)
        py = round((ry0 - y0) * ppi)
        im = renderer.tile_image(p, ppi)
        if im is None:
            if missing_box:
                bw, bh = round((rx1 - rx0) * ppi), round((ry1 - ry0) * ppi)
                draw.rectangle([px, py, px + bw - 1, py + bh - 1], fill=(225, 225, 225),
                               outline=(150, 150, 150), width=max(1, round(ppi / 50)))
                f = font(max(8, ppi * 0.4))
                draw.text((px + bw / 2, py + bh / 2), f"Missing\n{p.code}", fill=(90, 90, 90),
                          font=f, anchor="mm", align="center")
            continue
        if im.mode == "RGBA":
            canvas.paste(im, (px, py), im)
        else:
            canvas.paste(im, (px, py))
    if labels:
        draw_labels(canvas, doc, x0, y0, ppi)
    return canvas


def draw_labels(canvas: Image.Image, doc: MapDocument, x0: float, y0: float, ppi: float):
    """Write each tile's code in a white box at its centre."""
    draw = ImageDraw.Draw(canvas, "RGBA")
    for p in doc.draw_order():
        rx0, ry0, rx1, ry1 = p.rect
        cx = ((rx0 + rx1) / 2 - x0) * ppi
        cy = ((ry0 + ry1) / 2 - y0) * ppi
        short = min(rx1 - rx0, ry1 - ry0)
        size = max(9, min(ppi * 0.55, ppi * short * 0.16))
        f = font(size)
        text = p.code or "?"
        bbox = draw.textbbox((cx, cy), text, font=f, anchor="mm")
        pad = size * 0.25
        draw.rounded_rectangle([bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad],
                               radius=pad, fill=(255, 255, 255, 215), outline=(0, 0, 0, 255),
                               width=max(1, round(size / 14)))
        draw.text((cx, cy), text, font=f, fill=(0, 0, 0, 255), anchor="mm")
