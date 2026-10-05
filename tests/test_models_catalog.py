import hashlib
import json
from pathlib import Path

import pytest

from tamra.models.catalog import Catalog, ModelEntry, ModelFile, load_catalog

ROOT = Path(__file__).resolve().parents[1]


def _fake_catalog(files: dict[str, bytes]) -> Catalog:
    """A small catalog: one llm per file name, sized and hashed from the given bytes."""
    entries = []
    for i, (name, data) in enumerate(files.items()):
        entries.append(
            ModelEntry(
                id=f"fake-{i}",
                role="llm",
                name=name,
                files=(
                    ModelFile(
                        path=name,
                        size=len(data),
                        sha256=hashlib.sha256(data).hexdigest(),
                        url=f"https://example.invalid/{name}",
                    ),
                ),
                tier="small",
            )
        )
    return Catalog(entries)


def _two_file_catalog(tokenizer_bytes: bytes = b"xy") -> Catalog:
    return Catalog(
        [
            ModelEntry(
                id="emb",
                role="embedding",
                name="emb",
                files=(
                    ModelFile("emb/model.onnx", 3, "0" * 64, "https://example.invalid/m"),
                    ModelFile(
                        "emb/tokenizer.json",
                        len(tokenizer_bytes),
                        hashlib.sha256(tokenizer_bytes).hexdigest(),
                        "https://example.invalid/t",
                    ),
                ),
            )
        ]
    )


def test_packaged_catalog_lists_three_llms_and_the_embedding():
    catalog = load_catalog()
    assert [m.id for m in catalog.llms()] == ["qwen3-4b", "qwen3-8b", "qwen3-14b"]
    assert [m.tier for m in catalog.llms()] == ["small", "medium", "large"]
    assert [m.min_vram_gb for m in catalog.llms()] == [0, 6, 10]
    assert catalog.embedding().id == "bge-m3-int8"
    for m in catalog.llms():
        assert m.license == "apache-2.0"
        assert m.languages == ("th", "en", "zh")
        assert m.context_length == 32768
        assert m.thinking is True
        assert len(m.files) == 1


def test_packaged_catalog_pins_match_the_brief():
    (f,) = load_catalog().get("qwen3-8b").files
    assert f.path == "Qwen3-8B-Q4_K_M.gguf"
    assert f.size == 5027783488
    assert f.sha256 == "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785"
    assert f.url.startswith("https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/7c41481f57cb")


def test_embedding_entry_matches_scripts_assets_json():
    assets = json.loads((ROOT / "scripts" / "assets.json").read_text(encoding="utf-8"))
    files = {f.path: f for f in load_catalog().embedding().files}
    for key, rel in (
        ("bge-m3-model", "bge-m3/model.onnx"),
        ("bge-m3-tokenizer", "bge-m3/tokenizer.json"),
    ):
        assert files[rel].url == assets[key]["url"]
        assert files[rel].sha256 == assets[key]["sha256"]
        assert assets[key]["dest"] == f".models/{rel}"


def test_dev_model_is_not_catalogued():
    names = {f.path for m in load_catalog().models for f in m.files}
    assert not any("qwen2.5" in n for n in names)


def test_get_unknown_id_raises():
    with pytest.raises(KeyError):
        load_catalog().get("nope")


def test_installed_requires_present_file_with_matching_size(tmp_path):
    catalog = _fake_catalog({"a.gguf": b"aaaa", "b.gguf": b"bbbbbb", "c.gguf": b"cc"})
    (tmp_path / "a.gguf").write_bytes(b"aaaa")  # right size
    (tmp_path / "b.gguf").write_bytes(b"bb")  # wrong size (partial)
    # c.gguf is missing
    assert catalog.installed(tmp_path) == {"fake-0": tmp_path / "a.gguf"}


def test_installed_on_missing_directory_is_empty(tmp_path):
    assert _fake_catalog({"a.gguf": b"aaaa"}).installed(tmp_path / "nope") == {}


def test_installed_multi_file_entry_needs_every_file(tmp_path):
    catalog = _two_file_catalog()
    (tmp_path / "emb").mkdir()
    (tmp_path / "emb" / "model.onnx").write_bytes(b"abc")
    assert catalog.installed(tmp_path) == {}
    (tmp_path / "emb" / "tokenizer.json").write_bytes(b"xy")
    assert catalog.installed(tmp_path) == {"emb": tmp_path / "emb" / "model.onnx"}


def test_uncatalogued_lists_gguf_files_matching_no_entry(tmp_path):
    catalog = _fake_catalog({"a.gguf": b"aaaa"})
    (tmp_path / "a.gguf").write_bytes(b"aaaa")
    (tmp_path / "mine.gguf").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("x")
    assert catalog.uncatalogued(tmp_path) == [tmp_path / "mine.gguf"]


def test_uncatalogued_ignores_a_wrong_sized_catalogued_name(tmp_path):
    # A partial file under a catalogued name is not an import; it is not listed.
    catalog = _fake_catalog({"a.gguf": b"aaaa"})
    (tmp_path / "a.gguf").write_bytes(b"aa")
    assert catalog.uncatalogued(tmp_path) == []


def test_uncatalogued_on_missing_directory_is_empty(tmp_path):
    assert _fake_catalog({"a.gguf": b"a"}).uncatalogued(tmp_path / "nope") == []


def test_verify_checks_sha256(tmp_path):
    catalog = _fake_catalog({"a.gguf": b"aaaa"})
    good = tmp_path / "a.gguf"
    good.write_bytes(b"aaaa")
    assert catalog.verify("fake-0", good) is True
    bad = tmp_path / "bad.gguf"
    bad.write_bytes(b"aaab")  # same size, different content
    assert catalog.verify("fake-0", bad) is False


def test_verify_multi_file_entry_picks_the_file_by_name(tmp_path):
    catalog = _two_file_catalog(b"tok")
    p = tmp_path / "tokenizer.json"
    p.write_bytes(b"tok")
    assert catalog.verify("emb", p) is True
    stranger = tmp_path / "other.bin"
    stranger.write_bytes(b"tok")
    with pytest.raises(KeyError):
        catalog.verify("emb", stranger)
