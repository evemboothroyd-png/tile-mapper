"""Exports: overview prints, full-size single files, and full-size page sets.

Scale guarantee: full-size output is always placed at exactly 72 PDF points
per inch (or tagged with its true DPI for PNG), so printing at "Actual size"
/ 100% gives 1 inch on paper per 1-inch grid square. Every full-size page
also carries a 1-inch check square and a 3-inch ruler in the margin.
"""
from __future__ import annotations

import io
import math
import struct
import zlib
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from PIL import Image
from reportlab.lib.colors import black, white, Color
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdfcanvas

from .mapdoc import MapDocument
from .render import TileRenderer, render_region

PT = 72.0  # PDF points per inch

PAPER_SIZES = {
    "US Letter (8.5 × 11 in)": (8.5, 11.0),
    "US Legal (8.5 × 14 in)": (8.5, 14.0),
    "Tabloid (11 × 17 in)": (11.0, 17.0),
    "A4 (210 × 297 mm)": (210 / 25.4, 297 / 25.4),
    "A3 (297 × 420 mm)": (297 / 25.4, 420 / 25.4),
}
DEFAULT_PAPER = "US Letter (8.5 × 11 in)"
MAX_PDF_PAGE_IN = 200.0          # PDF viewers' practical page-size limit

Progress = Optional[Callable[[int, int], None]]


class ExportError(Exception):
    pass


@dataclass
class PageSetup:
    paper: str = DEFAULT_PAPER
    orientation: str = "auto"    # "portrait", "landscape", "auto"
    margin_in: float = 0.5

    def size(self, content_w: float = 1, content_h: float = 1) -> Tuple[float, float]:
        w, h = PAPER_SIZES[self.paper]
        if self.orientation == "landscape" or (
                self.orientation == "auto" and content_w > content_h):
            return max(w, h), min(w, h)
        return min(w, h), max(w, h)


def _bounds(doc: MapDocument):
    b = doc.bounds()
    if not b:
        raise ExportError("The map is empty — place some tiles first.")
    return b


def _jpeg_reader(img: Image.Image, quality: int = 92) -> ImageReader:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, subsampling=0)
    buf.seek(0)
    return ImageReader(buf)


# ------------------------------------------------------------------ overview
def overview_pdf(doc: MapDocument, renderer: TileRenderer, out_path: str,
                 setup: PageSetup, labels: bool, title: str = "") -> dict:
    """Whole map shrunk onto one page, optionally with tile codes."""
    x0, y0, x1, y1 = _bounds(doc)
    mw, mh = x1 - x0, y1 - y0
    pw, ph = setup.size(mw, mh)
    m = setup.margin_in
    header = 0.35
    avail_w, avail_h = pw - 2 * m, ph - 2 * m - header
    scale = min(avail_w / mw, avail_h / mh)
    dw, dh = mw * scale, mh * scale
    # render at ~200 dpi of the printed size, capped for memory
    ppi = min(150.0, max(20.0, 200.0 * scale))
    img = render_region(doc, renderer, x0, y0, x1, y1, ppi, labels=False)
    if labels:
        # draw labels at printed size so they stay readable when shrunk
        from .render import draw_labels
        lab_ppi = ppi
        draw_labels_scaled(img, doc, x0, y0, lab_ppi, scale)
    c = pdfcanvas.Canvas(out_path, pagesize=(pw * PT, ph * PT))
    c.setTitle(title or doc.title)
    ox = m + (avail_w - dw) / 2
    oy = m + (avail_h - dh) / 2
    c.drawImage(_jpeg_reader(img), ox * PT, oy * PT, dw * PT, dh * PT)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(m * PT, (ph - m - 0.2) * PT, title or doc.title)
    c.setFont("Helvetica", 8)
    c.drawRightString((pw - m) * PT, (ph - m - 0.2) * PT,
                      f"Overview — {mw:g}\" × {mh:g}\" map shown at {scale * 100:.0f}% (not to scale)")
    c.showPage()
    c.save()
    return {"pages": 1, "scale": scale}


def draw_labels_scaled(img: Image.Image, doc: MapDocument, x0: float, y0: float,
                       ppi: float, print_scale: float):
    """Labels sized for the printed page rather than the full-size map."""
    from PIL import ImageDraw
    from .render import font
    draw = ImageDraw.Draw(img, "RGBA")
    # target ~9 pt text on paper; convert to image pixels
    px_per_printed_inch = ppi / print_scale
    for p in doc.draw_order():
        rx0, ry0, rx1, ry1 = p.rect
        short_printed = min(rx1 - rx0, ry1 - ry0) * print_scale
        pt = max(5.0, min(10.0, short_printed * 72 * 0.18))
        size = pt / 72 * px_per_printed_inch
        f = font(size)
        cx = ((rx0 + rx1) / 2 - x0) * ppi
        cy = ((ry0 + ry1) / 2 - y0) * ppi
        text = p.code or "?"
        bb = draw.textbbox((cx, cy), text, font=f, anchor="mm")
        pad = size * 0.25
        draw.rounded_rectangle([bb[0] - pad, bb[1] - pad, bb[2] + pad, bb[3] + pad],
                               radius=pad, fill=(255, 255, 255, 220), outline=(0, 0, 0, 255),
                               width=max(1, round(size / 14)))
        draw.text((cx, cy), text, font=f, fill=(0, 0, 0, 255), anchor="mm")


def overview_png(doc: MapDocument, renderer: TileRenderer, out_path: str,
                 labels: bool, long_side_px: int = 3000) -> dict:
    x0, y0, x1, y1 = _bounds(doc)
    ppi = min(150.0, long_side_px / max(x1 - x0, y1 - y0))
    img = render_region(doc, renderer, x0, y0, x1, y1, ppi, labels=labels)
    img.save(out_path, dpi=(ppi, ppi))
    return {"size_px": img.size}


# ------------------------------------------------------- full size: one file
class StreamingPNG:
    """Writes a PNG row-band by row-band so huge maps don't need huge RAM."""

    def __init__(self, path: str, width: int, height: int, dpi: float):
        self.f = open(path, "wb")
        self.width, self.height = width, height
        self.rows = 0
        self.z = zlib.compressobj(6)
        self.f.write(b"\x89PNG\r\n\x1a\n")
        self._chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        ppm = int(round(dpi / 0.0254))
        self._chunk(b"pHYs", struct.pack(">IIB", ppm, ppm, 1))
        self._buf = bytearray()

    def _chunk(self, tag: bytes, data: bytes):
        self.f.write(struct.pack(">I", len(data)))
        self.f.write(tag)
        self.f.write(data)
        self.f.write(struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    def write_band(self, img: Image.Image):
        img = img.convert("RGB")
        assert img.width == self.width
        raw = img.tobytes()
        stride = self.width * 3
        out = bytearray()
        for r in range(img.height):
            out += b"\x00" + raw[r * stride:(r + 1) * stride]
        self._buf += self.z.compress(bytes(out))
        if len(self._buf) > 1 << 20:
            self._chunk(b"IDAT", bytes(self._buf))
            self._buf = bytearray()
        self.rows += img.height

    def close(self):
        self._buf += self.z.flush()
        if self._buf:
            self._chunk(b"IDAT", bytes(self._buf))
        self._chunk(b"IEND", b"")
        self.f.close()
        if self.rows != self.height:
            raise ExportError(f"Wrote {self.rows} rows, expected {self.height}.")


def fullsize_png(doc: MapDocument, renderer: TileRenderer, out_path: str,
                 ppi: int = 150, labels: bool = False, progress: Progress = None) -> dict:
    """The whole map at true size in one PNG (tagged with its DPI)."""
    x0, y0, x1, y1 = _bounds(doc)
    W, H = round((x1 - x0) * ppi), round((y1 - y0) * ppi)
    if W > 2 ** 31 - 1 or H > 2 ** 31 - 1:
        raise ExportError("Map too large for a single PNG.")
    band_in = max(1.0, 1024 / ppi)
    png = StreamingPNG(out_path, W, H, ppi)
    y = 0
    done_rows = 0
    while done_rows < H:
        rows = min(round(band_in * ppi), H - done_rows)
        ya = y0 + done_rows / ppi
        yb = y0 + (done_rows + rows) / ppi
        band = render_region(doc, renderer, x0, ya, x1, yb, ppi, labels=labels)
        if band.size != (W, rows):
            band = band.resize((W, rows))
        png.write_band(band)
        done_rows += rows
        if progress:
            progress(done_rows, H)
    png.close()
    return {"size_px": (W, H), "size_in": (x1 - x0, y1 - y0), "ppi": ppi}


def fullsize_pdf_single(doc: MapDocument, renderer: TileRenderer, out_path: str,
                        ppi: int = 150, progress: Progress = None) -> dict:
    """The whole map on one giant PDF page at true size (for print shops)."""
    x0, y0, x1, y1 = _bounds(doc)
    mw, mh = x1 - x0, y1 - y0
    if max(mw, mh) > MAX_PDF_PAGE_IN:
        raise ExportError(f"The map is {mw:g}\" × {mh:g}\"; a single PDF page can't exceed "
                          f"{MAX_PDF_PAGE_IN:g}\". Use the PNG or the page set instead.")
    c = pdfcanvas.Canvas(out_path, pagesize=(mw * PT, mh * PT))
    c.setTitle(doc.title)
    band_in = 6.0
    yy = y0
    n = math.ceil(mh / band_in)
    for i in range(n):
        yb = min(y1, yy + band_in)
        band = render_region(doc, renderer, x0, yy, x1, yb, ppi)
        # PDF origin is bottom-left
        c.drawImage(_jpeg_reader(band), 0, (y1 - yb) * PT, mw * PT, (yb - yy) * PT)
        yy = yb
        if progress:
            progress(i + 1, n)
    c.showPage()
    c.save()
    return {"size_in": (mw, mh)}


# --------------------------------------------------------- full size: pages
def plan_pages(map_w: float, map_h: float, setup: PageSetup, overlap: float):
    """Pick orientation (if auto) to minimise page count. Returns layout."""
    best = None
    orients = ["portrait", "landscape"] if setup.orientation == "auto" else [setup.orientation]
    for o in orients:
        s = PageSetup(setup.paper, o, setup.margin_in)
        pw, ph = s.size()
        cw = pw - 2 * setup.margin_in        # printable content width
        ch = ph - 2 * setup.margin_in - 0.45 # leave room for footer/ruler
        if cw <= overlap or ch <= overlap:
            continue
        cols = max(1, math.ceil((map_w - overlap) / (cw - overlap)))
        rows = max(1, math.ceil((map_h - overlap) / (ch - overlap)))
        cand = (cols * rows, o, pw, ph, cw, ch, cols, rows)
        if best is None or cand[0] < best[0]:
            best = cand
    if best is None:
        raise ExportError("Margins/overlap too large for this paper.")
    _, o, pw, ph, cw, ch, cols, rows = best
    return {"orientation": o, "page_w": pw, "page_h": ph, "content_w": cw,
            "content_h": ch, "cols": cols, "rows": rows}


def _ruler(c, x_in: float, y_in: float):
    """3-inch ruler with ¼" ticks plus a 1-inch check square."""
    c.setStrokeColor(black)
    c.setLineWidth(0.6)
    c.line(x_in * PT, y_in * PT, (x_in + 3) * PT, y_in * PT)
    for i in range(13):
        t = 0.12 if i % 4 == 0 else (0.08 if i % 2 == 0 else 0.05)
        xx = (x_in + i * 0.25) * PT
        c.line(xx, y_in * PT, xx, (y_in + t) * PT)
    c.setFont("Helvetica", 5.5)
    for i in range(4):
        c.drawCentredString((x_in + i) * PT, (y_in - 0.1) * PT, str(i))
    sq_x = x_in + 3.3
    c.rect(sq_x * PT, (y_in - 0.12) * PT, 1 * PT, 0.3 * PT, stroke=1, fill=0)
    c.drawString((sq_x + 0.04) * PT, (y_in - 0.02) * PT, "← exactly 1 inch →")


def paged_pdf(doc: MapDocument, renderer: TileRenderer, out_path: str,
              setup: PageSetup, overlap: float = 0.25, ppi: int = 150,
              skip_blank: bool = True, labels: bool = False,
              progress: Progress = None) -> dict:
    """Full-size map split over printer pages, with overlap and align marks.

    Page 1 is an assembly guide showing where every page goes.
    """
    x0, y0, x1, y1 = _bounds(doc)
    mw, mh = x1 - x0, y1 - y0
    L = plan_pages(mw, mh, setup, overlap)
    pw, ph, cw, ch = L["page_w"], L["page_h"], L["content_w"], L["content_h"]
    cols, rows = L["cols"], L["rows"]
    m = setup.margin_in
    step_x, step_y = cw - overlap, ch - overlap

    cells = []
    for r in range(rows):
        for col in range(cols):
            ax = x0 + col * step_x
            ay = y0 + r * step_y
            bx = min(ax + cw, x1)
            by = min(ay + ch, y1)
            has = any(not (p.rect[2] <= ax or p.rect[0] >= bx or p.rect[3] <= ay or p.rect[1] >= by)
                      for p in doc.placements)
            cells.append((r, col, ax, ay, bx, by, has))
    printed = [c_ for c_ in cells if c_[6] or not skip_blank]

    c = pdfcanvas.Canvas(out_path, pagesize=(pw * PT, ph * PT))
    c.setTitle(doc.title)

    # ---- assembly guide
    gw, gh = pw - 2 * m, ph - 2 * m - 1.3
    sc = min(gw / mw, gh / mh)
    img = render_region(doc, renderer, x0, y0, x1, y1, min(60.0, max(10.0, 150 * sc)))
    gx = m + (gw - mw * sc) / 2
    gy_top = ph - m - 1.0
    c.setFont("Helvetica-Bold", 14)
    c.drawString(m * PT, (ph - m - 0.25) * PT, f"{doc.title} — assembly guide")
    c.setFont("Helvetica", 9)
    c.drawString(m * PT, (ph - m - 0.5) * PT,
                 f"Map {mw:g}\" × {mh:g}\" on {len(printed)} {setup.paper.split(' (')[0]} pages "
                 f"({L['orientation']}), {cols} across × {rows} down, {overlap:g}\" overlap.")
    c.drawString(m * PT, (ph - m - 0.7) * PT,
                 "Print at 100% / Actual size (turn OFF 'Fit to page'). Check the 1-inch square on each page.")
    c.drawImage(_jpeg_reader(img), gx * PT, (gy_top - mh * sc) * PT, mw * sc * PT, mh * sc * PT)
    c.setStrokeColor(Color(0.85, 0.1, 0.1))
    c.setFillColor(Color(0.85, 0.1, 0.1))
    c.setLineWidth(0.8)
    num = {}
    for i, cell in enumerate(printed, start=2):
        r, col, ax, ay, bx, by, _ = cell
        num[(r, col)] = i
        rx = gx + (ax - x0) * sc
        ry = gy_top - (by - y0) * sc
        c.rect(rx * PT, ry * PT, (bx - ax) * sc * PT, (by - ay) * sc * PT, stroke=1, fill=0)
        c.setFont("Helvetica-Bold", max(6, min(14, (bx - ax) * sc * 10)))
        c.drawCentredString((rx + (bx - ax) * sc / 2) * PT, (ry + (by - ay) * sc / 2) * PT, str(i))
    c.setFillColor(black)
    _ruler(c, m, m + 0.15)
    c.showPage()

    # ---- the pages
    total = len(printed)
    for k, cell in enumerate(printed):
        r, col, ax, ay, bx, by, _ = cell
        region = render_region(doc, renderer, ax, ay, bx, by, ppi, labels=labels)
        w_in, h_in = bx - ax, by - ay
        top = ph - m                    # content hangs from the top margin
        c.drawImage(_jpeg_reader(region), m * PT, (top - h_in) * PT, w_in * PT, h_in * PT)
        # overlap guide lines (dashed) where the neighbouring page starts
        c.setStrokeColor(Color(0.4, 0.4, 0.4))
        c.setLineWidth(0.5)
        c.setDash(3, 3)
        if col + 1 < cols and (bx - ax) > overlap:
            xx = m + w_in - overlap
            c.line(xx * PT, (top - h_in) * PT, xx * PT, top * PT)
        if r + 1 < rows and (by - ay) > overlap:
            yy = top - h_in + overlap
            c.line(m * PT, yy * PT, (m + w_in) * PT, yy * PT)
        c.setDash()
        # corner crop marks just outside the image
        c.setStrokeColor(black)
        c.setLineWidth(0.4)
        L_ = 0.15
        for (cx, cy) in [(m, top), (m + w_in, top), (m, top - h_in), (m + w_in, top - h_in)]:
            sx = -1 if cx == m else 1
            sy = 1 if cy == top else -1
            c.line(cx * PT, (cy + sy * 0.04) * PT, cx * PT, (cy + sy * (0.04 + L_)) * PT)
            c.line((cx + sx * 0.04) * PT, cy * PT, (cx + sx * (0.04 + L_)) * PT, cy * PT)
        # footer
        c.setFont("Helvetica", 7.5)
        c.setFillColor(black)
        c.drawRightString((pw - m) * PT, (top + 0.12) * PT,
                          f"Page {num[(r, col)]} — row {r + 1} of {rows}, column {col + 1} of {cols}"
                          f"   ·   map inches {ax - x0:g}–{bx - x0:g} across, {ay - y0:g}–{by - y0:g} down")
        _ruler(c, m, m + 0.15)
        c.showPage()
        if progress:
            progress(k + 1, total)
    c.save()
    return {"pages": total + 1, "layout": L}
