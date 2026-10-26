# PyInstaller spec — build with: uv run pyinstaller packaging/tamra.spec --noconfirm
# Windows: dist/Tamra/Tamra.exe. Linux: dist/tamra/tamra (scripts/make_deb.py packages it).
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent
WINDOWS = sys.platform == "win32"
NAME = "Tamra" if WINDOWS else "tamra"
ICON = ROOT / "packaging" / ("tamra.ico" if WINDOWS else "tamra.png")

hiddenimports = (
    collect_submodules("uvicorn")
    # PyInstaller's hook-keyring already covers this; kept as belt-and-braces for the OS
    # credential store backend.
    + collect_submodules("keyring.backends")
    + ["anthropic", "truststore"]
    + ["tamra.app", "tamra.selfcheck", "tamra.embedder", "tamra.mcp_server"]
)
excludes = []
if WINDOWS:
    hiddenimports += ["keyring.backends.Windows"]
else:
    hiddenimports += [
        "keyring.backends.SecretService",
        "webview.platforms.qt",  # pywebview imports its backend by name at runtime
        "qtpy",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebChannel",
        "PySide6.QtNetwork",
    ]
    # Other Qt bindings and pywebview backends, never used on Linux.
    excludes += ["PyQt5", "PyQt6", "PySide2", "gi", "webview.platforms.gtk", "webview.platforms.cef"]
    # Qt modules pywebview's Qt backend never imports; QtQml would also pull every QML plugin.
    # Qt WebEngine still gets the native Qt libraries it links against.
    excludes += [f"PySide6.{m}" for m in ("QtQml", "QtQuick", "QtQuickWidgets", "QtPositioning")]

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    # vec0.dll / vec0.so: the Linux default patterns only match lib*.so.
    binaries=collect_dynamic_libs("sqlite_vec", search_patterns=["*.dll", "*.so"]),
    datas=[
        (str(ROOT / "ui" / "dist"), "ui/dist"),
        (str(ROOT / "vendor" / "llama"), "vendor/llama"),
        (str(ROOT / "src" / "tamra" / "models" / "catalog.json"), "tamra/models"),
        (str(ICON), "packaging"),
        (str(ROOT / "LICENSE"), "."),
        (str(ROOT / "NOTICE"), "."),
    ],
    hiddenimports=hiddenimports,
    excludes=excludes,
)
if not WINDOWS:
    # Qt's .qm translations are never loaded (Tamra installs no QTranslator), and Chromium's
    # locale packs fall back to en-US: keep the languages Tamra speaks.
    keep = {"en-US.pak", "th.pak", "zh-CN.pak", "zh-TW.pak"}
    a.datas = [
        d
        for d in a.datas
        if not (
            d[0].startswith("PySide6/Qt/translations/")
            and (d[0].endswith(".qm") or Path(d[0]).name not in keep)
        )
    ]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    console=False,
    icon=str(ICON) if WINDOWS else None,  # Linux takes the icon from the .desktop entry
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME)
