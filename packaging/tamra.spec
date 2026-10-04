# PyInstaller spec — build with: uv run pyinstaller packaging/tamra.spec --noconfirm
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=collect_dynamic_libs("sqlite_vec"),
    datas=[
        (str(ROOT / "ui" / "dist"), "ui/dist"),
        (str(ROOT / "vendor" / "llama"), "vendor/llama"),
    ],
    hiddenimports=collect_submodules("uvicorn")
    # PyInstaller's hook-keyring already covers this; kept as belt-and-braces for the
    # Windows Credential Manager backend.
    + collect_submodules("keyring.backends")
    + ["tamra.app", "tamra.selfcheck", "tamra.embedder"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Tamra", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Tamra")
