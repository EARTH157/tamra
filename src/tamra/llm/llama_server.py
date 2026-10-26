import re
import subprocess
import time
from pathlib import Path
from typing import IO, TYPE_CHECKING

import httpx

from tamra.childproc import spawn
from tamra.net import free_port

if TYPE_CHECKING:
    from tamra.winjob import KillOnCloseJob


class LlamaServerError(Exception):
    """llama-server could not start. `reason` is a tamra.llm.base.ErrorReason."""

    def __init__(self, message: str, reason: str = "other"):
        super().__init__(message)
        self.reason = reason


LOG_VERBOSITY = "4"
MAX_LOG_BYTES = 1024 * 1024  # a larger log is restarted instead of appended to
_OFFLOADED = re.compile(r"offloaded\s+(\d+)\s*/\s*(\d+)\s+layers\s+to\s+GPU", re.IGNORECASE)
_MODEL_BUFFER = re.compile(r"load_tensors:\s+(\w+)\s+model buffer size\s*=\s*([\d.]+)\s*MiB")
_UNKNOWN_ARCH = re.compile(r"unknown model architecture: '([^']+)'")


def _log_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def parse_gpu_offload(log_text: str) -> bool | None:
    """Whether llama-server put model weights on a GPU. None when the log does not say (yet).

    The "offloaded N/M layers to GPU" line is the signal, but llama.cpp also prints it when it
    was told to use no device and ran on the CPU. So when the log lists model buffers, the weights
    must also sit in a non-CPU buffer (such as Vulkan1) for this to be True.
    """
    found = _OFFLOADED.findall(log_text)
    if not found:
        return None
    if int(found[-1][0]) == 0:
        return False
    buffers = _MODEL_BUFFER.findall(log_text)
    if not buffers:
        return True
    return any(not name.upper().startswith("CPU") and float(size) > 0 for name, size in buffers)


class LlamaServer:
    """Runs llama.cpp's llama-server as a child process bound to 127.0.0.1."""

    def __init__(
        self,
        exe: Path,
        model: Path,
        log_file: Path,
        ctx_size: int = 4096,
        gpu: bool = True,
        device: str | None = None,
        api_key: str | None = None,
    ):
        self.exe, self.model, self.log_file = exe, model, log_file
        self.ctx_size, self.gpu, self.device = ctx_size, gpu, device
        self.api_key = api_key
        self.port = free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.gpu_used: bool | None = None
        self._log_start = 0  # where this run's output begins in the (appended-to) log file
        self._proc: subprocess.Popen | None = None
        self._log: IO[bytes] | None = None
        self._job: KillOnCloseJob | None = None  # Windows only (childproc.spawn)

    def args(self, gpu: bool) -> list[str]:
        args = [str(self.exe), "-m", str(self.model), "-c", str(self.ctx_size)]
        # A q8_0 KV cache takes about half the memory of f16 for no visible loss; llama.cpp turns
        # flash attention on for it, which quantized values need (spec §9).
        args += ["-ctk", "q8_0", "-ctv", "q8_0"]
        args += ["--host", "127.0.0.1", "--port", str(self.port)]
        args += ["-lv", LOG_VERBOSITY]  # the default level hides the layer-offload report
        if self.api_key:
            args += ["--api-key", self.api_key]
        if not gpu:
            return args + ["--device", "none"]
        if self.device:
            args += ["--device", self.device]
        return args + ["-ngl", "99"]

    def start(self, timeout: float = 120.0) -> "LlamaServer":
        """Start llama-server, GPU first and then CPU, within one overall time budget."""
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise LlamaServerError(f"cannot write the log at {self.log_file}: {e}") from e
        deadline = time.monotonic() + timeout
        attempts = [True, False] if self.gpu else [False]
        for gpu in attempts:
            remaining = deadline - time.monotonic()
            budget = remaining * 0.6 if gpu and len(attempts) > 1 else remaining
            try:
                try:
                    self._log = self.log_file.open(
                        "wb" if _log_size(self.log_file) > MAX_LOG_BYTES else "ab"
                    )
                    self._log_start = self._log.tell()
                except OSError as e:
                    raise LlamaServerError(f"cannot write the log at {self.log_file}: {e}") from e
                try:
                    self._proc, self._job = spawn(
                        self.args(gpu),
                        stdout=self._log,
                        stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL,
                    )
                except OSError as e:
                    raise LlamaServerError(f"cannot launch {self.exe}: {e}") from e
                if self._wait_healthy(budget):
                    self.gpu_used = gpu
                    return self
                self.stop()
                unknown = _UNKNOWN_ARCH.search(self._run_log())
                if unknown:  # the CPU would fail the same way
                    raise LlamaServerError(
                        f"This version of llama.cpp cannot run models of type {unknown.group(1)}.",
                        "model_unsupported",
                    )
            except BaseException:
                self.stop()
                raise
        raise LlamaServerError(f"llama-server failed to start; see {self.log_file}")

    def _run_log(self) -> str:
        """This run's part of the log file; empty when it cannot be read."""
        try:
            with self.log_file.open("rb") as f:
                f.seek(self._log_start)
                return f.read().decode("utf-8", errors="replace")
        except OSError:
            return ""

    @property
    def gpu_offload(self) -> bool | None:
        """True when layers were offloaded to the GPU, False when none were (CPU only), None when
        the log says nothing about it."""
        return parse_gpu_offload(self._run_log())

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _wait_healthy(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        client = httpx.Client(trust_env=False, timeout=2.0)
        try:
            while time.monotonic() < deadline:
                if self._proc is None or self._proc.poll() is not None:
                    return False
                try:
                    if client.get(f"{self.base_url}/health").status_code == 200:
                        return True
                except httpx.TransportError:
                    pass
                time.sleep(0.25)
            return False
        finally:
            client.close()

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        self._proc = None
        if self._job is not None:
            self._job.close()
            self._job = None
        if self._log is not None:
            self._log.close()
            self._log = None

    def __enter__(self) -> "LlamaServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
