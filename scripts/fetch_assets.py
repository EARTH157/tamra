"""Download pinned dev/build assets. Usage: uv run python scripts/fetch_assets.py [name ...]"""

import json
import sys
import zipfile
from pathlib import Path

from tamra.download import download_verified

ROOT = Path(__file__).resolve().parents[1]


def main(names: list[str]) -> int:
    assets = json.loads((ROOT / "scripts" / "assets.json").read_text(encoding="utf-8"))
    unknown = set(names) - assets.keys()
    if unknown:
        print(f"unknown asset(s): {', '.join(sorted(unknown))}; known: {', '.join(assets)}")
        return 2
    for name in names or list(assets):
        spec = assets[name]
        dest = ROOT / spec["dest"]
        print(f"{name}: {dest}")
        download_verified(spec["url"], dest, spec["sha256"])
        if "extract_to" in spec:
            with zipfile.ZipFile(dest) as zf:
                zf.extractall(ROOT / spec["extract_to"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
