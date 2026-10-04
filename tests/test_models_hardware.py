import subprocess
from pathlib import Path

import pytest

from tamra.models import hardware
from tamra.models.catalog import Catalog, load_catalog
from tamra.models.hardware import Gpu, Hardware, detect, parse_devices, recommend

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
    assert calls["kwargs"]["creationflags"] == subprocess.CREATE_NO_WINDOW


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
