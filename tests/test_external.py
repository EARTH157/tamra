"""The scanner for GGUF models that other local AI tools downloaded."""

import hashlib
import json
import os
import sys

import pytest

from tamra.models import external
from tamra.models.external import ExternalModel, scan

HEX_A = "a" * 64
HEX_B = "b" * 64
GGUF = b"GGUF" + b"\0" * 12


def ollama_model(root, name, tag, digest_hex, *, projector=False, blob=GGUF, runner=None):
    """A v1 manifest under root/manifests/registry.ollama.ai/library/<name>/<tag>, and its blob."""
    manifest = root / "manifests" / "registry.ollama.ai" / "library" / name / tag
    manifest.parent.mkdir(parents=True, exist_ok=True)
    layers = [{"mediaType": "application/vnd.ollama.image.model", "digest": f"sha256:{digest_hex}"}]
    if projector:
        layers.insert(
            0,
            {"mediaType": "application/vnd.ollama.image.projector", "digest": "sha256:" + "c" * 64},
        )
    doc = {"schemaVersion": 2, "layers": layers}
    if runner is not None:
        doc |= {"runner": runner[0], "format": runner[1]}
    manifest.write_text(json.dumps(doc), encoding="utf-8")
    (root / "blobs").mkdir(parents=True, exist_ok=True)
    if blob is not None:
        (root / "blobs" / f"sha256-{digest_hex}").write_bytes(blob)
    return root / "blobs" / f"sha256-{digest_hex}"


def gguf(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(GGUF)
    return path


@pytest.fixture(autouse=True)
def _no_ollama_service(monkeypatch, tmp_path):
    """Keep this machine's Ollama service folder (Linux) out of every test."""
    monkeypatch.setattr(external, "_OLLAMA_SERVICE", tmp_path / "no-service")


@pytest.mark.skipif(sys.platform != "linux", reason="Ollama's Linux service folder")
def test_ollama_installed_as_a_service_is_found_too(tmp_path, monkeypatch):
    service = tmp_path / "service"
    blob = ollama_model(service, "qwen3", "4b", HEX_A)
    monkeypatch.setattr(external, "_OLLAMA_SERVICE", service)
    assert scan(tmp_path, {}) == [ExternalModel("ollama", "qwen3:4b", blob, HEX_A)]


@pytest.mark.skipif(sys.platform != "linux", reason="Ollama's Linux service folder")
def test_ollama_models_set_by_the_user_replace_the_service_folder(tmp_path, monkeypatch):
    ollama_model(tmp_path / "service", "qwen3", "4b", HEX_A)
    monkeypatch.setattr(external, "_OLLAMA_SERVICE", tmp_path / "service")
    assert scan(tmp_path, {"OLLAMA_MODELS": str(tmp_path / "elsewhere")}) == []


def test_nothing_installed_gives_nothing(tmp_path):
    assert scan(tmp_path, {}) == []


def test_an_ollama_model_is_its_blob_named_by_tag(tmp_path):
    root = tmp_path / ".ollama" / "models"
    blob = ollama_model(root, "qwen3", "8b", HEX_A)
    assert scan(tmp_path, {}) == [ExternalModel("ollama", "qwen3:8b", blob, HEX_A)]


def test_ollama_models_honour_OLLAMA_MODELS(tmp_path):
    root = tmp_path / "elsewhere"
    blob = ollama_model(root, "qwen3", "8b", HEX_A)
    assert scan(tmp_path, {"OLLAMA_MODELS": str(root)}) == [
        ExternalModel("ollama", "qwen3:8b", blob, HEX_A)
    ]


def test_tags_that_share_a_blob_are_listed_once_under_the_tag_that_is_not_latest(tmp_path):
    root = tmp_path / ".ollama" / "models"
    ollama_model(root, "qwen3", "latest", HEX_A)
    blob = ollama_model(root, "qwen3", "8b", HEX_A)
    assert scan(tmp_path, {}) == [ExternalModel("ollama", "qwen3:8b", blob, HEX_A)]


def test_a_vision_model_is_listed_by_its_text_model_without_the_projector(tmp_path):
    # Ollama keeps a vision model's mmproj in its own layer; llama-server runs the text model alone.
    root = tmp_path / ".ollama" / "models"
    blob = ollama_model(root, "qwen3.5", "4b", HEX_A, projector=True, runner=("llamacpp", "gguf"))
    assert scan(tmp_path, {}) == [ExternalModel("ollama", "qwen3.5:4b", blob, HEX_A)]


def test_models_for_another_runner_bad_manifests_and_non_gguf_blobs_are_skipped(tmp_path):
    root = tmp_path / ".ollama" / "models"
    ollama_model(root, "mlx-only", "4b", HEX_A, runner=("mlx", "safetensors"))
    ollama_model(root, "broken", "1b", HEX_B, blob=b"not a model")
    bad = root / "manifests" / "registry.ollama.ai" / "library" / "bad" / "1b"
    bad.parent.mkdir(parents=True)
    bad.write_text("{not json", encoding="utf-8")
    missing = ollama_model(root, "gone", "1b", "d" * 64, blob=None)
    assert not missing.exists()
    assert scan(tmp_path, {}) == []


def test_a_v2_manifest_list_is_followed_to_its_llama_cpp_manifest(tmp_path):
    root = tmp_path / ".ollama" / "models"
    (root / "blobs").mkdir(parents=True)
    model = root / "blobs" / f"sha256-{HEX_A}"
    model.write_bytes(GGUF)
    inner = json.dumps(
        {
            "schemaVersion": 2,
            "layers": [
                {"mediaType": "application/vnd.ollama.image.model", "digest": f"sha256:{HEX_A}"}
            ],
        }
    ).encode()
    inner_hex = hashlib.sha256(inner).hexdigest()
    (root / "blobs" / f"sha256-{inner_hex}").write_bytes(inner)
    listing = root / "manifests-v2" / "ollama.com" / "library" / "qwen3.5" / "4b"
    listing.parent.mkdir(parents=True)
    entries = [
        {"digest": "sha256:" + "e" * 64, "runner": "mlx", "format": "safetensors"},
        {"digest": f"sha256:{inner_hex}", "runner": "llamacpp", "format": "gguf"},
    ]
    listing.write_text(json.dumps({"schemaVersion": 2, "manifests": entries}), encoding="utf-8")
    assert scan(tmp_path, {}) == [ExternalModel("ollama", "qwen3.5:4b", model, HEX_A)]


@pytest.mark.parametrize(
    "folder", [(".lmstudio", "models"), (".cache", "lm-studio", "models"), ("portable", "models")]
)
def test_lm_studio_models_are_found_in_every_known_folder(tmp_path, folder):
    (tmp_path / ".lmstudio-home-pointer").write_text(str(tmp_path / "portable"), encoding="utf-8")
    model = gguf(
        tmp_path.joinpath(*folder, "lmstudio-community", "Qwen3-8B-GGUF", "Qwen3-8B-Q4_K_M.gguf")
    )
    assert scan(tmp_path, {}) == [ExternalModel("lmstudio", "Qwen3-8B-Q4_K_M", model, None)]


def test_lm_studio_models_are_found_in_its_downloads_folder_setting(tmp_path):
    downloads = tmp_path / "D-drive" / "LLMs"
    settings = tmp_path / ".lmstudio" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"downloadsFolder": str(downloads)}), encoding="utf-8")
    model = gguf(downloads / "pub" / "repo" / "m.gguf")
    assert scan(tmp_path, {}) == [ExternalModel("lmstudio", "m", model, None)]


def test_a_folder_named_twice_is_scanned_once(tmp_path):
    (tmp_path / ".lmstudio-home-pointer").write_text(str(tmp_path / ".lmstudio"), encoding="utf-8")
    model = gguf(tmp_path / ".lmstudio" / "models" / "pub" / "repo" / "m.gguf")
    assert scan(tmp_path, {}) == [ExternalModel("lmstudio", "m", model, None)]


def test_projectors_later_split_parts_and_non_gguf_files_are_skipped(tmp_path):
    repo = tmp_path / ".lmstudio" / "models" / "pub" / "repo"
    gguf(repo / "mmproj-model-f16.gguf")
    first = gguf(repo / "Big-Q4_K_M-00001-of-00002.gguf")
    gguf(repo / "Big-Q4_K_M-00002-of-00002.gguf")
    (repo / "fake.gguf").write_bytes(b"nope")
    (repo / "README.md").write_text("hi", encoding="utf-8")
    assert scan(tmp_path, {}) == [ExternalModel("lmstudio", first.stem, first, None)]


def test_lm_studio_files_deeper_than_three_folders_are_not_listed(tmp_path):
    gguf(tmp_path / ".lmstudio" / "models" / "a" / "b" / "c" / "d" / "deep.gguf")
    assert scan(tmp_path, {}) == []


def test_a_plain_hugging_face_snapshot_file_has_no_known_hash(tmp_path):
    hub = tmp_path / ".cache" / "huggingface" / "hub"
    model = gguf(
        hub / "models--Qwen--Qwen3-8B-GGUF" / "snapshots" / "rev1" / "Qwen3-8B-Q4_K_M.gguf"
    )
    assert scan(tmp_path, {}) == [ExternalModel("huggingface", "Qwen3-8B-Q4_K_M", model, None)]


def test_hugging_face_honours_HF_HUB_CACHE_then_HF_HOME(tmp_path):
    hub = tmp_path / "hub-cache"
    model = gguf(hub / "models--x--y" / "snapshots" / "r" / "y.gguf")
    assert scan(tmp_path, {"HF_HUB_CACHE": str(hub)})[0].path == model
    home_hub = tmp_path / "hf-home" / "hub"
    other = gguf(home_hub / "models--x--z" / "snapshots" / "r" / "z.gguf")
    assert scan(tmp_path, {"HF_HOME": str(tmp_path / "hf-home")})[0].path == other


def test_a_symlinked_hugging_face_file_takes_its_hash_from_the_blob_name(tmp_path):
    repo = tmp_path / ".cache" / "huggingface" / "hub" / "models--x--y"
    blob = repo / "blobs" / HEX_A
    gguf(blob)
    link = repo / "snapshots" / "r" / "y.gguf"
    link.parent.mkdir(parents=True)
    try:
        os.symlink(blob, link)
    except OSError:
        pytest.skip("this OS account cannot create symlinks")
    assert scan(tmp_path, {}) == [ExternalModel("huggingface", "y", link, HEX_A)]
