# PyInstaller recipe for the macOS app bundle.
# Build:  pyinstaller packaging/TileMapper.spec --noconfirm
import os

block_cipher = None
here = os.path.abspath(SPECPATH)
root = os.path.dirname(here)
version = {}
exec(open(os.path.join(root, "tilemapper", "__init__.py")).read(), version)

EXCLUDES = [
    "tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtQuick", "PySide6.QtQml",
    "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtPdf",
]

a = Analysis(
    [os.path.join(root, "packaging", "launch.py")],
    pathex=[root],
    binaries=[],
    datas=[],
    hiddenimports=["reportlab.graphics.barcode.common", "reportlab.pdfbase._fontdata_enc_winansi",
                   "reportlab.pdfbase._fontdata_widths_helvetica",
                   "reportlab.pdfbase._fontdata_widths_helveticabold"],
    excludes=EXCLUDES,
    noarchive=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Tile Mapper",
          debug=False, strip=False, upx=False, console=False,
          argv_emulation=False)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="Tile Mapper")
app = BUNDLE(
    coll,
    name="Tile Mapper.app",
    icon=os.path.join(here, "icon.icns"),
    bundle_identifier="local.tilemapper.app",
    version=version["__version__"],
    info_plist={
        "CFBundleShortVersionString": version["__version__"],
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "CFBundleDocumentTypes": [{
            "CFBundleTypeName": "Tile Mapper Map",
            "CFBundleTypeRole": "Editor",
            "LSHandlerRank": "Owner",
            "CFBundleTypeExtensions": ["tmap"],
        }],
    },
)
