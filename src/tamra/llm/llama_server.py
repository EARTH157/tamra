import logging
import subprocess
import time
from pathlib import Path
from typing import IO

import httpx

from tamra.net import free_port
from tamra.winjob import KillOnCloseJob

log = logging.getLogger(__name__)


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
        api_key: str | None = None,
    ):
        self.exe, self.model, self.log_file = exe, model, log_file
        self.ctx_size, self.gpu, self.device = ctx_size, gpu, device
        self.api_key = api_key
        self.port = free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.gpu_used: bool | None = None
        self._proc: subprocess.Popen | None = None
        self._log: IO[bytes] | None = None
        self._job: KillOnCloseJob | None = None

    def args(self, gpu: bool) -> list[str]:
        args = [str(self.exe), "-m", str(self.model), "-c", str(self.ctx_size)]
        args += ["--host", "127.0.0.1", "--port", str(self.port)]
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
                    self._log = self.log_file.open("ab")
                except OSError as e:
                    raise LlamaServerError(f"cannot write the log at {self.log_file}: {e}") from e
                try:
                    self._proc = subprocess.Popen(
                        self.args(gpu),
                        stdout=self._log,
                        stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                except OSError as e:
                    raise LlamaServerError(f"cannot launch {self.exe}: {e}") from e
                self._job = _kill_on_close(self._proc)
                if self._wait_healthy(budget):
                    self.gpu_used = gpu
                    return self
                self.stop()
            except BaseException:
                self.stop()
                raise
        raise LlamaServerError(f"llama-server failed to start; see {self.log_file}")

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


def _kill_on_close(proc: subprocess.Popen) -> KillOnCloseJob | None:
    """Tie proc's lifetime to Tamra's. If Windows refuses, keep running without the job."""
    job = None
    try:
        job = KillOnCloseJob()
        job.add(proc)
        return job
    except OSError as e:
        if job is not None:
            job.close()
        log.warning("llama-server was not placed in a kill-on-close job: %s", e)
        return None
