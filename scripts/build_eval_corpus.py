"""Render eval/corpus into a folder of real documents for the retrieval eval (spec §12).

    uv run python scripts/build_eval_corpus.py OUT_DIR

Plain .md and .txt files are copied. A "<name>.json" spec becomes "<name>":
  .docx  {"blocks": [[heading_level, text], ...]}            level 0 is a body paragraph
  .pdf   {"font": "latin_thai" | "cjk", "pages": [[line, ...], ...]}
  .txt   {"encoding": "cp874", "text": "..."}                 a legacy-encoded text file
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "eval" / "corpus"
sys.path.insert(0, str(ROOT / "tests"))  # docgen builds the PDF and DOCX files

from docgen import CJK, LATIN_THAI, make_docx, make_pdf  # noqa: E402

FONTS = {"latin_thai": LATIN_THAI, "cjk": CJK}


def targets(corpus: Path = CORPUS) -> list[str]:
    """The names of the documents the corpus renders to, sorted."""
    return sorted(p.name.removesuffix(".json") for p in corpus.iterdir() if p.is_file())


def build(out_dir: Path, corpus: Path = CORPUS) -> list[Path]:
    """Render every corpus entry into out_dir and return the written paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for source in sorted(p for p in corpus.iterdir() if p.is_file()):
        if source.suffix != ".json":
            written.append(Path(shutil.copy2(source, out_dir / source.name)))
            continue
        target = out_dir / source.name.removesuffix(".json")
        spec = json.loads(source.read_text(encoding="utf-8"))
        if target.suffix == ".docx":
            make_docx(target, [(level, text) for level, text in spec["blocks"]])
        elif target.suffix == ".pdf":
            make_pdf(target, spec["pages"], FONTS[spec["font"]])
        elif target.suffix == ".txt":
            target.write_bytes(spec["text"].encode(spec["encoding"]))
        else:
            raise ValueError(f"no renderer for {source.name}")
        written.append(target)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the eval corpus into a folder")
    parser.add_argument("out_dir", type=Path)
    for path in build(parser.parse_args().out_dir):
        print(path)


if __name__ == "__main__":
    main()
