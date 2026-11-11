#!/bin/bash
# Builds "Tile Mapper.app" and a .dmg installer. Run on a Mac from the repo root:
#   bash packaging/build_mac.sh
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt pyinstaller

# icon: PNG -> .icns
rm -rf build/icon.iconset && mkdir -p build/icon.iconset
for s in 16 32 128 256 512; do
  sips -z $s $s packaging/icon.png --out build/icon.iconset/icon_${s}x${s}.png >/dev/null
  d=$((s*2)); sips -z $d $d packaging/icon.png --out build/icon.iconset/icon_${s}x${s}@2x.png >/dev/null
done
iconutil -c icns build/icon.iconset -o packaging/icon.icns

pyinstaller packaging/TileMapper.spec --noconfirm --distpath dist --workpath build/pyi

VERSION=$(python3 -c "import tilemapper; print(tilemapper.__version__)")
ARCH=$(uname -m)
DMG="dist/Tile-Mapper-${VERSION}-mac-${ARCH}.dmg"
rm -rf build/dmg && mkdir -p build/dmg
cp -R "dist/Tile Mapper.app" build/dmg/
ln -s /Applications build/dmg/Applications
cp packaging/READ_ME_FIRST.txt build/dmg/
hdiutil create -volname "Tile Mapper" -srcfolder build/dmg -ov -format UDZO "$DMG"
echo "Built $DMG"
