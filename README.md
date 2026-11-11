# Tile Mapper

A Mac app for building tabletop battle maps from tile art and printing them at true
1-inch-grid scale.

* Browse your tile folder by category, with search and thumbnails.
* Drag tiles onto an endless map. Tiles snap to whole-tile (6") spaces; odd-sized and floating
  pieces snap to 1". Hold ⌥ Option to snap to 1".
* Turn (R / ⇧R), mirror (H), flip (V), float on top (F), duplicate (⌘D), undo (⌘Z).
* Print / Export:
  * Overview of the whole map on one page (PDF or PNG), with or without tile codes.
  * Full size split over printer pages (US Letter, Legal, Tabloid, A4, A3) with an assembly
    guide, overlap lines, crop marks, and a 1-inch check square on every page.
  * Full size as one huge PNG (tagged with its DPI) or one huge PDF page.

Maps are saved as small `.tmap` files that refer to tiles by their path inside the tile folder.

## Tile folder

```
My Tiles/
  Dungeon/DV328 Bonus Intersection.jpg
  Town/Taverns/ATTV101 Adventure Town Taverns Vol. 1.jpg
  Tile Thumbnails/Dungeon/T-DV328.jpg      (optional)
  tile_overrides.json                       (optional)
```

File names start with the tile code. Sub-folders become categories. Missing thumbnails are
generated automatically (and **Tiles ▸ Make Missing Thumbnails** writes them into the folder).

### Scale rules

Tiles are drawn on a 1-inch grid at 150 pixels per inch (300 for files 1500 px or larger).
Sizes round to whole or half inches; extra pixels are treated as print bleed and trimmed evenly.
To correct a tile, add it to `tile_overrides.json`:

```json
{ "OSS39": {"ppi": 300}, "Dungeon/XX01 Odd Tile.jpg": {"w_in": 4, "h_in": 6} }
```

## Building

Every push to GitHub builds `.dmg` installers for Apple Silicon and Intel Macs
(Actions tab ▸ latest run ▸ Artifacts). Locally on a Mac: `bash packaging/build_mac.sh`.

Run from source: `pip install -r requirements.txt && python -m tilemapper`.
Tests: `pip install pytest && python -m pytest`.
