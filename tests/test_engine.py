"""Engine tests. Uses synthetic tiles so no copyrighted art is needed."""
import os
import struct
import zlib

import pytest
from PIL import Image, ImageDraw

from tilemapper.engine import (TileLibrary, MapDocument, Placement, TileRenderer,
                               parse_code_title, measure, render_region)
from tilemapper.engine import export


def grid_tile(path, inches_w, inches_h, ppi=150, bleed=0, color=(200, 180, 140), white_bg=False):
    w, h = round(inches_w * ppi) + 2 * bleed, round(inches_h * ppi) + 2 * bleed
    im = Image.new("RGB", (w, h), (255, 255, 255) if white_bg else color)
    d = ImageDraw.Draw(im)
    if white_bg:
        inset = 30
        d.rectangle([bleed + inset, bleed + inset, w - bleed - inset, h - bleed - inset], fill=color)
    for i in range(int(inches_w) + 1):
        x = bleed + i * ppi
        d.line([x, 0, x, h], fill=(0, 0, 0), width=2)
    for j in range(int(inches_h) + 1):
        y = bleed + j * ppi
        d.line([0, y, w, y], fill=(0, 0, 0), width=2)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im.save(path, quality=95)


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "RPG Map Tiles"
    grid_tile(str(root / "Dungeon" / "DV328 Bonus Intersection.jpg"), 6, 6)
    grid_tile(str(root / "Dungeon" / "IT12 Ice Temple.jpg"), 6, 6, bleed=19)
    grid_tile(str(root / "Town" / "Inn" / "OSS39 Oriental Sword School.jpg"), 6, 6, ppi=300)
    grid_tile(str(root / "Wilderness" / "DW17 Forest.jpg"), 6, 9)
    grid_tile(str(root / "Town" / "Warf" / "ATCB01 Adventure Town Canals Boat.jpg"), 5, 1,
              color=(120, 80, 40), white_bg=True)
    grid_tile(str(root / "Ships" / "HSWS Deck1 A01 Main Deck.jpg"), 6, 6)
    os.makedirs(root / "Tile Thumbnails" / "Dungeon")
    Image.new("RGB", (201, 201), (1, 2, 3)).save(root / "Tile Thumbnails" / "Dungeon" / "T-DV328.jpg")
    L = TileLibrary(str(root), cache_dir=str(tmp_path / "cache"))
    L.scan()
    return L


def test_parse_codes():
    assert parse_code_title("DV328 Bonus Intersection") == ("DV328", "Bonus Intersection")
    assert parse_code_title("HSWS Deck1 A01 Main Deck") == ("HSWS Deck1 A01", "Main Deck")
    assert parse_code_title("Starships36 Starships") == ("Starships36", "Starships")
    assert parse_code_title("cart00201a Cartophile No. 2")[0] == "cart00201a"


@pytest.mark.parametrize("px,expect", [
    ((903, 903), (150, 6, 6)), ((938, 938), (150, 6, 6)), ((896, 896), (150, 6, 6)),
    ((1800, 1800), (300, 6, 6)), ((1807, 1807), (300, 6, 6)), ((900, 1350), (150, 6, 9)),
    ((1050, 1425), (150, 7, 9.5)), ((1125, 1500), (150, 7.5, 10)), ((750, 150), (150, 5, 1)),
    ((753, 753), (150, 5, 5)),
])
def test_measure(px, expect):
    ppi, w, h, crop = measure(*px)
    assert (ppi, w, h) == expect
    cw, ch = crop[2] - crop[0], crop[3] - crop[1]
    assert cw <= px[0] and ch <= px[1]
    assert cw >= round(w * ppi) - 6 and ch >= round(h * ppi) - 6


def test_overrides(tmp_path):
    ppi, w, h, _ = measure(1800, 1800, {"ppi": 150})
    assert (ppi, w, h) == (150, 12, 12)
    assert measure(900, 900, {"w_in": 5, "h_in": 5})[1:3] == (5, 5)


def test_scan_and_thumbs(lib):
    assert len(lib.tiles) == 6
    assert "Town/Inn" in lib.categories()
    t = lib.get("Dungeon/DV328 Bonus Intersection.jpg")
    assert t.thumb_path and t.thumb_path.endswith("T-DV328.jpg")
    o = lib.get("Town/Inn/OSS39 Oriental Sword School.jpg")
    assert o.ppi == 300 and o.w_in == 6
    p = lib.thumbnail_path(o)
    assert os.path.exists(p)
    written = lib.write_missing_thumbnails()
    assert len(written) == 5
    assert all(os.path.exists(w) for w in written)
    assert [x.code for x in lib.search("ice")] == ["IT12"]
    assert len(lib.search(category="Town")) == 2


def place(lib, tile_id, x, y, **kw):
    t = lib.get(tile_id)
    return Placement(tile_id, x, y, t.w_in, t.h_in, code=t.code, **kw)


def sample_map(lib):
    doc = MapDocument()
    doc.title = "Test"
    doc.add(place(lib, "Dungeon/DV328 Bonus Intersection.jpg", 0, 0))
    doc.add(place(lib, "Dungeon/IT12 Ice Temple.jpg", 6, 0, rotation=90))
    doc.add(place(lib, "Town/Inn/OSS39 Oriental Sword School.jpg", 0, 6, flip_h=True))
    doc.add(place(lib, "Wilderness/DW17 Forest.jpg", 6, 6, rotation=90))   # 9 wide x 6 tall
    doc.add(place(lib, "Town/Warf/ATCB01 Adventure Town Canals Boat.jpg", 1, 2, floating=True))
    return doc


def test_doc_roundtrip(lib, tmp_path):
    doc = sample_map(lib)
    assert doc.bounds() == (0, 0, 15, 12)
    p = tmp_path / "m.tmap"
    doc.save(str(p))
    d2 = MapDocument.load(str(p))
    assert [q.rect for q in d2.placements] == [q.rect for q in doc.placements]
    assert d2.draw_order()[-1].floating          # legacy flag survives a round trip
    # a tile dropped in the same spot stacks on top; nothing is replaced
    doc.add(place(lib, "Dungeon/IT12 Ice Temple.jpg", 0, 0))
    assert sum(1 for q in doc.placements if q.rect == (0, 0, 6, 6)) == 2
    assert doc.draw_order()[-1].code.startswith("IT12")


def test_layering(lib):
    doc = sample_map(lib)
    order = lambda: [q.code.split()[0] for q in doc.placements]
    boat = doc.placements[-1].uid
    dv = doc.placements[0].uid
    doc.send_to_back(boat)
    assert order()[0] == "ATCB01"
    doc.bring_to_front(boat)
    assert order()[-1] == "ATCB01"
    # one step down: just under the nearest overlapping tile, skipping ones it doesn't touch
    b = doc.find(boat)
    below = [q for q in doc.placements[:-1] if doc.overlaps(b, q)]
    assert below and doc.send_backward([boat])
    i = doc.placements.index(b)
    assert doc.placements[i + 1] is below[-1]
    assert doc.bring_forward([boat])
    assert doc.placements.index(b) == doc.placements.index(below[-1]) + 1
    # several at once keep their own order
    doc.bring_to_front([dv, boat])
    assert order()[-2:] == ["DV328", "ATCB01"]
    doc.send_to_back([dv, boat])
    assert order()[:2] == ["DV328", "ATCB01"]
    # nothing overlapping -> no change
    far = place(lib, "Dungeon/IT12 Ice Temple.jpg", 100, 100)
    doc.add(far)
    assert not doc.send_backward([far.uid]) and not doc.bring_forward([far.uid])


def test_render_scale_and_transforms(lib):
    doc = sample_map(lib)
    r = TileRenderer(lib)
    img = render_region(doc, r, 0, 0, 15, 12, 50)
    assert img.size == (750, 600)
    # floating boat: white surround cleared -> underlying tile shows at the boat's corner
    under = img.getpixel((round(1.1 * 50), round(2.1 * 50)))
    assert under != (255, 255, 255)
    # rotated 9x6 forest occupies x 6..15, y 6..12
    assert img.getpixel((round(14.5 * 50), round(11.5 * 50))) != (255, 255, 255)


def test_missing_tile_box(lib):
    doc = MapDocument()
    doc.add(Placement("Nope/X99 Gone.jpg", 0, 0, 6, 6, code="X99"))
    img = render_region(doc, TileRenderer(lib), 0, 0, 6, 6, 20)
    assert img.getpixel((60, 10)) == (225, 225, 225)


def png_info(path):
    with open(path, "rb") as f:
        data = f.read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", data[16:24])
    i = data.find(b"pHYs")
    ppx = struct.unpack(">I", data[i + 4:i + 8])[0]
    return w, h, ppx


def test_fullsize_png_streaming(lib, tmp_path):
    doc = sample_map(lib)
    out = tmp_path / "full.png"
    info = export.fullsize_png(doc, TileRenderer(lib), str(out), ppi=150)
    w, h, ppm = png_info(out)
    assert (w, h) == (15 * 150, 12 * 150)
    assert abs(ppm * 0.0254 - 150) < 0.1
    im = Image.open(out)
    im.load()
    assert im.size == (2250, 1800)


def pdf_page_sizes(path):
    import re
    data = open(path, "rb").read()
    return [tuple(float(v) for v in m.groups())
            for m in re.finditer(rb"/MediaBox \[\s*0 0 ([\d.]+) ([\d.]+)\s*\]", data)]


def test_paged_pdf_layout(lib, tmp_path):
    doc = sample_map(lib)
    out = tmp_path / "pages.pdf"
    setup = export.PageSetup(paper="US Letter (8.5 × 11 in)", orientation="auto")
    res = export.paged_pdf(doc, TileRenderer(lib), str(out), setup, overlap=0.25, ppi=100)
    L = res["layout"]
    # every map inch is covered
    assert L["cols"] * (L["content_w"] - 0.25) + 0.25 >= 15
    assert L["rows"] * (L["content_h"] - 0.25) + 0.25 >= 12
    sizes = pdf_page_sizes(out)
    assert len(sizes) == res["pages"]
    for w, h in sizes:
        assert {round(w), round(h)} == {612, 792}


@pytest.mark.parametrize("paper", list(export.PAPER_SIZES))
def test_all_papers(lib, tmp_path, paper):
    doc = sample_map(lib)
    out = tmp_path / "p.pdf"
    export.paged_pdf(doc, TileRenderer(lib), str(out), export.PageSetup(paper=paper), ppi=60)
    export.overview_pdf(doc, TileRenderer(lib), str(tmp_path / "o.pdf"),
                        export.PageSetup(paper=paper), labels=True)
    pw, ph = export.PAPER_SIZES[paper]
    for w, h in pdf_page_sizes(out):
        assert {round(w, 1), round(h, 1)} == {round(pw * 72, 1), round(ph * 72, 1)}


def test_single_pdf_true_size(lib, tmp_path):
    doc = sample_map(lib)
    out = tmp_path / "one.pdf"
    export.fullsize_pdf_single(doc, TileRenderer(lib), str(out), ppi=60)
    assert pdf_page_sizes(out) == [(15 * 72.0, 12 * 72.0)]


def test_overview_png_labels(lib, tmp_path):
    doc = sample_map(lib)
    export.overview_png(doc, TileRenderer(lib), str(tmp_path / "a.png"), labels=False, long_side_px=600)
    export.overview_png(doc, TileRenderer(lib), str(tmp_path / "b.png"), labels=True, long_side_px=600)
    a = Image.open(tmp_path / "a.png")
    b = Image.open(tmp_path / "b.png")
    assert a.size == b.size and list(a.getdata()) != list(b.getdata())


def test_empty_map_errors(lib, tmp_path):
    with pytest.raises(export.ExportError):
        export.fullsize_png(MapDocument(), TileRenderer(lib), str(tmp_path / "x.png"))


def test_white_surround_is_clear_but_inner_white_kept():
    from PIL import Image, ImageDraw
    from tilemapper.engine.render import clear_white_surround
    im = Image.new("RGB", (900, 900), "white")
    d = ImageDraw.Draw(im)
    d.rectangle((100, 100, 800, 800), outline="black", width=1)   # thin outline, white inside
    a = clear_white_surround(im).getchannel("A")
    assert a.getpixel((10, 10)) == 0 and a.getpixel((99, 450)) == 0       # outside: clear
    assert a.getpixel((450, 450)) == 255 and a.getpixel((101, 450)) == 255  # inside: kept
    assert a.getpixel((100, 450)) == 255                                    # the line itself


def test_every_tile_gets_clear_white_edges(lib):
    from PIL import Image
    doc = MapDocument()
    p = place(lib, "Town/Warf/ATCB01 Adventure Town Canals Boat.jpg", 0, 0)
    assert not p.floating
    doc.add(p)
    img = TileRenderer(lib).tile_image(p, 50)
    assert img.mode == "RGBA" and img.getpixel((1, 1))[3] == 0
