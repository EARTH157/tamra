import subprocess
from pathlib import Path

import pytest

from tamra.childproc import NO_WINDOW
from tamra.models import hardware
from tamra.models.catalog import Catalog, load_catalog
from tamra.models.gguf import ModelShape
from tamra.models.hardware import (
    MIB,
    Gpu,
    Hardware,
    context_size,
    detect,
    memory_budget,
    parse_devices,
    recommend,
)

SAMPLE = """\
Available devices:
  Vulkan0: AMD Radeon(TM) 780M Graphics (8094 MiB, 7689 MiB free)
  Vulkan1: NVIDIA GeForce RTX 5060 Laptop GPU (7899 MiB, 7131 MiB free)
"""


def test_parse_devices_reads_every_gpu():
    assert parse_devices(SAMPLE) == [
        Gpu("AMD Radeon(TM) 780M Graphics", 8094),
        Gpu("NVIDIA GeForce RTX 5060 Laptop GPU", 7899),
    ]


def test_parse_devices_without_devices():
    assert parse_devices("Available devices:\n") == []
    assert parse_devices("") == []


def test_parse_devices_ignores_garbage():
    assert parse_devices("error: unknown argument\n\x00\xff junk (12 MiB)\nVulkan0 nope\n") == []


@pytest.mark.parametrize(
    "line, expected",
    [
        (
            "  Vulkan0: NVIDIA GeForce RTX 4060 Laptop GPU (Special) (8188 MiB, 7000 MiB free)",
            [Gpu("NVIDIA GeForce RTX 4060 Laptop GPU (Special)", 8188)],
        ),
        ("  CUDA0: NVIDIA GeForce RTX 4090 (24564 MiB)", [Gpu("NVIDIA GeForce RTX 4090", 24564)]),
        ("  Vulkan0: Some GPU (no memory info)", []),
        ("  Vulkan0: Some GPU", []),
        ("ggml_vulkan: Found 2 Vulkan devices:", []),
        ("load_backend: loaded Vulkan backend from C:/x/ggml-vulkan.dll (v1)", []),
        ("ggml_vulkan: 0 = AMD Radeon(TM) 780M Graphics (AMD proprietary) | uma: 1", []),
    ],
)
def test_parse_devices_lines(line, expected):
    assert parse_devices(f"Available devices:\n{line}\n") == expected


def gpu(name: str, vram_gb: int) -> Gpu:
    return Gpu(name, vram_gb * 1024)


def hw(*gpus: Gpu) -> Hardware:
    return Hardware(ram_gb=16.0, gpus=list(gpus))


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def test_recommend_8gb_discrete_gives_medium(catalog):
    assert recommend(hw(gpu("NVIDIA GeForce RTX 4060", 8)), catalog) == "medium"


def test_recommend_12gb_discrete_gives_large(catalog):
    assert recommend(hw(gpu("NVIDIA GeForce RTX 3060", 12)), catalog) == "large"


def test_recommend_small_discrete_gives_small(catalog):
    assert recommend(hw(gpu("NVIDIA GeForce GTX 1650", 4)), catalog) == "small"


def test_recommend_igpu_only_gives_small(catalog):
    assert recommend(hw(gpu("AMD Radeon(TM) Graphics", 16)), catalog) == "small"
    assert recommend(hw(gpu("Intel(R) Arc(TM) Graphics", 16)), catalog) == "small"


def test_recommend_ignores_igpu_next_to_discrete(catalog):
    gpus = [gpu("AMD Radeon(TM) 780M Graphics", 16), gpu("NVIDIA GeForce RTX 4060", 8)]
    assert recommend(hw(*gpus), catalog) == "medium"


def test_recommend_uses_largest_discrete_gpu(catalog):
    gpus = [gpu("NVIDIA GeForce GTX 1650", 4), gpu("NVIDIA GeForce RTX 3060", 12)]
    assert recommend(hw(*gpus), catalog) == "large"


def test_recommend_discrete_amd_without_tm_counts(catalog):
    assert recommend(hw(gpu("AMD Radeon RX 7800 XT", 16)), catalog) == "large"


@pytest.mark.parametrize(
    "name, vram_gb, tier",
    [
        ("Intel(R) Arc(TM) A770 Graphics", 16, "large"),
        ("Intel(R) Arc(TM) A750 Graphics", 8, "medium"),
        ("Intel(R) Arc(TM) B580 Graphics", 12, "large"),
        ("AMD Radeon(TM) Pro W7800 Graphics", 32, "large"),
        ("AMD Radeon RX 7600", 8, "medium"),
        ("AMD Radeon(TM) RX 7600 Graphics", 8, "medium"),
        ("AMD Radeon(TM) 780M Graphics", 16, "small"),
        ("Intel(R) Arc(TM) Graphics", 16, "small"),
        ("Intel(R) Iris(R) Xe Graphics", 16, "small"),
        ("Intel(R) UHD Graphics", 16, "small"),
    ],
)
def test_recommend_classifies_discrete_and_integrated_names(catalog, name, vram_gb, tier):
    assert recommend(hw(gpu(name, vram_gb)), catalog) == tier


def test_recommend_with_no_llm_in_the_catalog_gives_small():
    assert recommend(hw(gpu("NVIDIA GeForce RTX 3060", 12)), Catalog([])) == "small"


def test_recommend_no_gpu_gives_small(catalog):
    assert recommend(hw(), catalog) == "small"


def test_detect_parses_the_command_output(monkeypatch):
    calls = {}

    def fake_run(args, **kwargs):
        calls["args"], calls["kwargs"] = args, kwargs
        return subprocess.CompletedProcess(args, 0, stdout=SAMPLE, stderr="")

    monkeypatch.setattr(hardware.subprocess, "run", fake_run)
    result = detect(Path("llama-server.exe"))
    assert [g.vram_mb for g in result.gpus] == [8094, 7899]
    assert result.ram_gb > 0
    assert calls["args"] == [str(Path("llama-server.exe")), "--list-devices"]
    assert calls["kwargs"]["timeout"] == 15
    assert calls["kwargs"]["creationflags"] == NO_WINDOW


@pytest.mark.parametrize("exc", [OSError("missing"), subprocess.TimeoutExpired("x", 15)])
def test_detect_returns_no_gpus_on_error(monkeypatch, exc):
    def boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(hardware.subprocess, "run", boom)
    result = detect(Path("llama-server.exe"))
    assert result.gpus == []
    assert result.ram_gb > 0


@pytest.mark.assets
def test_detect_with_the_real_llama_server(llama_exe):
    result = detect(llama_exe)
    assert result.ram_gb > 0
    assert result.gpus, "llama-server --list-devices reported no GPU on this machine"
    assert all(g.name and g.vram_mb > 0 for g in result.gpus)
    assert recommend(result, load_catalog()) in {"small", "medium", "large"}


# Context size (spec §9) ------------------------------------------------------------------------

QWEN3_8B = ModelShape(context_length=40960, kv_bytes_per_token=78336)
QWEN3_8B_BYTES = 4795 * MIB


def _gpu(vram_gb: float, name="NVIDIA GeForce RTX 5060 Laptop GPU") -> Hardware:
    return Hardware(ram_gb=32, gpus=[Gpu(name, int(vram_gb * 1024))])


def test_auto_context_on_an_8gb_card_fits_16k_beside_qwen3_8b():
    # 8 GB - 1.5 GB of margin and buffers - 4.7 GB of model leaves ~1.8 GB: 16k needs 1.2 GB
    assert context_size("auto", QWEN3_8B, QWEN3_8B_BYTES, _gpu(8)) == 16384


def test_auto_context_on_a_large_card_is_32k_and_never_more():
    assert context_size("auto", QWEN3_8B, QWEN3_8B_BYTES, _gpu(24)) == 32768


def test_auto_context_falls_back_to_8k_when_nothing_larger_fits():
    assert context_size("auto", QWEN3_8B, QWEN3_8B_BYTES, _gpu(6)) == 8192
    assert context_size("auto", None, 0, _gpu(24)) == 8192  # a file that does not tell


def test_auto_context_without_a_discrete_gpu_uses_half_the_ram():
    igpu = Gpu("AMD Radeon(TM) 780M Graphics", 8094)
    small = ModelShape(32768, 6528)  # qwen2.5-0.5b
    assert context_size("auto", small, 469 * MIB, Hardware(ram_gb=16, gpus=[igpu])) == 32768
    assert context_size("auto", QWEN3_8B, QWEN3_8B_BYTES, Hardware(ram_gb=8)) == 8192


def test_a_chosen_context_is_kept_but_never_past_the_models_limit():
    assert context_size("32768", QWEN3_8B, QWEN3_8B_BYTES, _gpu(6)) == 32768
    assert context_size("65536", QWEN3_8B, QWEN3_8B_BYTES, _gpu(24)) == 40960
    tiny = ModelShape(4096, 6528)
    assert context_size("auto", tiny, 0, _gpu(24)) == 4096


def test_auto_context_counts_a_sliding_window_cache_once():
    gemma4 = ModelShape(131072, 8704, 53 * MIB)  # Gemma 4 E4B, as read from its file
    assert context_size("auto", gemma4, 5088 * MIB, _gpu(8)) == 32768
    windowed = ModelShape(131072, 8704, 2000 * MIB)  # a window cache that leaves no room
    assert context_size("auto", windowed, 5088 * MIB, _gpu(8)) == 8192


def test_memory_budget_counts_the_largest_discrete_gpu():
    hw = Hardware(ram_gb=64, gpus=[Gpu("Intel(R) UHD Graphics", 32000), Gpu("NVIDIA RTX", 8192)])
    assert memory_budget(hw, 0) == (8192 - 1536) * MIB
