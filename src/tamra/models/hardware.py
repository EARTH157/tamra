"""Detect RAM and GPUs, and recommend a model tier and a context size."""

import ctypes
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from tamra.childproc import NO_WINDOW
from tamra.models.catalog import Catalog
from tamra.models.gguf import ModelShape

# "  Vulkan0: AMD Radeon(TM) 780M Graphics (8094 MiB, 7689 MiB free)"
_DEVICE = re.compile(r"^\s*\S+\d:\s+(?P<name>.+?)\s+\((?P<mib>\d+)\s*MiB(?:,[^)]*)?\)\s*$")


@dataclass(frozen=True)
class Gpu:
    name: str
    vram_mb: int


@dataclass
class Hardware:
    ram_gb: float
    gpus: list[Gpu] = field(default_factory=list)


def parse_devices(output: str) -> list[Gpu]:
    """Read the GPUs from `llama-server --list-devices`. Unrecognised lines are skipped."""
    gpus = []
    for line in output.splitlines():
        m = _DEVICE.match(line)
        if m:
            gpus.append(Gpu(m["name"], int(m["mib"])))
    return gpus


def _ram_gb() -> float:
    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return 0.0
    return status.ullTotalPhys / 1024**3


def detect(llama_exe: Path) -> Hardware:
    """Total RAM, plus the GPUs llama-server can see. A failed probe means no GPUs."""
    try:
        done = subprocess.run(
            [str(llama_exe), "--list-devices"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            stdin=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
        )
        gpus = parse_devices(done.stdout)
    except (OSError, subprocess.SubprocessError):
        gpus = []
    return Hardware(ram_gb=_ram_gb(), gpus=gpus)


_INTEL_DISCRETE = re.compile(r"Arc\(TM\) [AB]\d{3}")
_AMD_DISCRETE = re.compile(r"\b(Pro|RX)\b")


def _is_integrated(gpu: Gpu) -> bool:
    """Intel (except Arc A/B cards) and "Radeon(TM) ... Graphics" (except Pro/RX) are iGPUs."""
    name = gpu.name
    if "Intel" in name:
        return not _INTEL_DISCRETE.search(name)
    if "Radeon(TM)" in name and "Graphics" in name:
        return not _AMD_DISCRETE.search(name)
    return False


MIB = 1024 * 1024
# The local model's context window, in tokens (spec §9). "auto" fits it to this computer.
CONTEXT_CHOICES = ("auto", "8192", "16384", "32768", "65536")
CONTEXT_FLOOR = 8192  # auto never goes below what every machine ran before it existed
AUTO_CONTEXTS = (32768, 16384)  # tried largest first; past 32k a long prompt is slow to read
FIT_MARGIN_MB = 1024  # VRAM left free, as llama-server's own --fit-target default
COMPUTE_MB = 512  # llama.cpp's compute buffers for the 4-8B models
RAM_SHARE = 0.5  # without a discrete GPU the model may take half the RAM; the rest is Windows'


def memory_budget(hardware: Hardware, model_bytes: int) -> int:
    """Bytes left for the KV cache once the model is loaded: in the largest discrete GPU's VRAM,
    or, without one, in the share of RAM the model may use. Negative when the model alone does
    not fit."""
    discrete = [g.vram_mb for g in hardware.gpus if not _is_integrated(g)]
    if discrete:
        room = (max(discrete) - FIT_MARGIN_MB - COMPUTE_MB) * MIB
    else:  # an integrated GPU shares the system RAM
        room = int(hardware.ram_gb * 1024 * MIB * RAM_SHARE) - COMPUTE_MB * MIB
    return room - model_bytes


def context_size(
    choice: str, shape: ModelShape | None, model_bytes: int, hardware: Hardware
) -> int:
    """The context window to start llama-server with. "auto" takes the largest of AUTO_CONTEXTS
    whose q8_0 KV cache fits the memory budget, else CONTEXT_FLOOR. A size the user chose is
    taken as it is. Neither goes past what the model was trained for."""
    if choice == "auto":
        size = CONTEXT_FLOOR
        if shape is not None:
            budget = memory_budget(hardware, model_bytes)
            fits = [
                c
                for c in AUTO_CONTEXTS
                if c * shape.kv_bytes_per_token + shape.kv_fixed_bytes <= budget
            ]
            size = fits[0] if fits else CONTEXT_FLOOR
    else:
        size = int(choice)
    if shape is not None and shape.context_length:
        size = min(size, shape.context_length)
    return size


def recommend(hardware: Hardware, catalog: Catalog) -> str:
    """The largest tier whose min_vram_gb fits the largest discrete GPU; else the smallest."""
    tiers = sorted(catalog.llms(), key=lambda m: m.min_vram_gb)
    if not tiers:
        return "small"
    discrete = [g.vram_mb for g in hardware.gpus if not _is_integrated(g)]
    vram_mb = max(discrete, default=0)
    fitting = [m for m in tiers if discrete and m.min_vram_gb * 1024 <= vram_mb]
    chosen = fitting[-1] if fitting else tiers[0]
    return chosen.tier or "small"
