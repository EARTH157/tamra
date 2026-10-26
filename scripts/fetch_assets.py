"""Download pinned dev/build assets. Usage: uv run python scripts/fetch_assets.py [name ...]

"llama" means this platform's llama.cpp build; no names means every asset for this platform.
"""

import json
import sys
import tarfile
import zipfile
from pathlib import Path

import truststore

from tamra.download import download_verified

ROOT = Path(__file__).resolve().parents[1]
LLAMA = {"win32": "llama-windows", "linux": "llama-linux"}


def select(assets: dict, names: list[str], platform: str) -> list[str]:
    """The asset names to fetch on this platform; KeyError for an unknown name."""
    if not names:
        return [n for n, spec in assets.items() if spec.get("platform", platform) == platform]
    chosen = [LLAMA.get(platform, "llama-linux") if n == "llama" else n for n in names]
    for name in chosen:
        if name not in assets:
            raise KeyError(name)
    return chosen


def extract(archive: Path, target: Path) -> None:
    """Unpack a .zip or .tar.gz into target. A tar whose entries all sit in one top-level folder
    (llama.cpp's Linux builds) is unpacked without it. tarfile's "data" filter keeps the
    executable bits and the .so symlinks, and refuses absolute paths and paths outside target."""
    if not archive.name.endswith((".tar.gz", ".tgz")):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(target)
        return
    with tarfile.open(archive) as tf:
        members = tf.getmembers()
        tops = {m.name.split("/", 1)[0] for m in members}
        top = tops.pop() if len(tops) == 1 else None
        if top is not None and any(m.name == top and m.isdir() for m in members):
            members = [
                m.replace(name=m.name[len(top) + 1 :], deep=False)
                for m in members
                if m.name.startswith(top + "/")
            ]
        target.mkdir(parents=True, exist_ok=True)
        tf.extractall(target, members=members, filter="data")


def main(names: list[str]) -> int:
    truststore.inject_into_ssl()
    assets = json.loads((ROOT / "scripts" / "assets.json").read_text(encoding="utf-8"))
    try:
        chosen = select(assets, names, sys.platform)
    except KeyError as e:
        print(f"unknown asset: {e.args[0]}; known: llama, {', '.join(assets)}")
        return 2
    for name in chosen:
        spec = assets[name]
        dest = ROOT / spec["dest"]
        print(f"{name}: {dest}")
        download_verified(spec["url"], dest, spec["sha256"])
        if "extract_to" in spec:
            extract(dest, ROOT / spec["extract_to"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
