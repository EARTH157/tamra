"""Detect RAM and GPUs, and recommend a model tier."""

import ctypes
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from tamra.models.catalog import Catalog

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
            creationflags=subprocess.CREATE_NO_WINDOW,
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
