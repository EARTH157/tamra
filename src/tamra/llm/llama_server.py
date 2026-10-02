import subprocess
import time
from pathlib import Path
from typing import IO

import httpx

from tamra.net import free_port


class LlamaServerError(Exception):
    pass


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
    ):
        self.exe, self.model, self.log_file = exe, model, log_file
        self.ctx_size, self.gpu, self.device = ctx_size, gpu, device
        self.port = free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.gpu_used: bool | None = None
        self._proc: subprocess.Popen | None = None
        self._log: IO[bytes] | None = None

    def args(self, gpu: bool) -> list[str]:
        args = [str(self.exe), "-m", str(self.model), "-c", str(self.ctx_size)]
        args += ["--host", "127.0.0.1", "--port", str(self.port)]
        if not gpu:
            return args + ["--device", "none"]
        if self.device:
            args += ["--device", self.device]
        return args + ["-ngl", "99"]

    def start(self, timeout: float = 120.0) -> "LlamaServer":
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        for gpu in [True, False] if self.gpu else [False]:
            self._log = self.log_file.open("ab")
            self._proc = subprocess.Popen(
                self.args(gpu),
                stdout=self._log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if self._wait_healthy(timeout):
                self.gpu_used = gpu
                return self
            self.stop()
        raise LlamaServerError(f"llama-server failed to start; see {self.log_file}")

    def _wait_healthy(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._proc is None or self._proc.poll() is not None:
                return False
            try:
                if httpx.get(f"{self.base_url}/health", timeout=2.0).status_code == 200:
                    return True
            except httpx.TransportError:
                pass
            time.sleep(0.25)
        return False

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        self._proc = None
        if self._log is not None:
            self._log.close()
            self._log = None

    def __enter__(self) -> "LlamaServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()
