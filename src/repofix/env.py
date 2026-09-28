"""Minimal Docker execution environment for one SWE-bench instance."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass

import docker


@dataclass(frozen=True)
class ExecutionResult:
    output: str
    exit_code: int | None
    timed_out: bool
    latency_seconds: float


class DockerEnv:
    """Run shell commands in an isolated, network-disabled SWE-bench image."""

    workdir = "/testbed"

    def __init__(self, instance_id: str, image: str, run_id: str) -> None:
        self.instance_id = instance_id
        self.image = image
        self.run_id = run_id
        self.client: docker.DockerClient | None = None
        self.container = None

    @property
    def name(self) -> str:
        raw = f"repofix.{self.instance_id}.{self.run_id}".lower()
        return re.sub(r"[^a-z0-9_.-]", "-", raw)[:120]

    def start(self) -> "DockerEnv":
        if self.container is not None:
            return self
        self.client = docker.from_env()
        try:
            try:
                self.client.images.get(self.image)
            except docker.errors.ImageNotFound as exc:
                raise RuntimeError(
                    f"SWE-bench image is not available locally: {self.image}"
                ) from exc

            try:
                stale = self.client.containers.get(self.name)
            except docker.errors.NotFound:
                stale = None
            if stale is not None:
                stale.remove(force=True)

            self.container = self.client.containers.create(
                image=self.image,
                name=self.name,
                command="tail -f /dev/null",
                detach=True,
                user="root",
                working_dir=self.workdir,
                network_disabled=True,
                network_mode="none",
                labels={
                    "repofix.run_id": self.run_id,
                    "repofix.instance_id": self.instance_id,
                },
            )
            self.container.start()
            self.container.reload()
            if self.container.status != "running":
                raise RuntimeError("SWE-bench container did not reach running state")
            return self
        except Exception:
            self.close()
            raise

    def execute(self, command: str, timeout: int = 60) -> ExecutionResult:
        if self.container is None or self.client is None:
            raise RuntimeError("DockerEnv has not been started")
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        chunks: list[bytes] = []
        state: dict[str, object] = {"exec_id": None, "error": None}

        def run() -> None:
            try:
                created = self.client.api.exec_create(
                    self.container.id,
                    [
                        "timeout",
                        "--signal=TERM",
                        "--kill-after=5",
                        str(timeout),
                        "bash",
                        "-lc",
                        command,
                    ],
                    workdir=self.workdir,
                    user="root",
                )
                exec_id = created["Id"]
                state["exec_id"] = exec_id
                stream = self.client.api.exec_start(exec_id, stream=True)
                chunks.extend(stream)
            except Exception as exc:  # surfaced on the calling thread
                state["error"] = exc

        started = time.monotonic()
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(timeout + 7)
        host_timeout = thread.is_alive()
        exec_id = state["exec_id"]
        if host_timeout and exec_id:
            inspected = self.client.api.exec_inspect(str(exec_id))
            pid = inspected.get("Pid")
            if pid:
                self.container.exec_run(["kill", "-TERM", str(pid)], detach=True)
            thread.join(5)

        latency = time.monotonic() - started
        if state["error"] is not None:
            raise RuntimeError(str(state["error"])) from state["error"]

        exit_code = None
        if exec_id and not thread.is_alive():
            exit_code = self.client.api.exec_inspect(str(exec_id)).get("ExitCode")
        timed_out = host_timeout or exit_code == 124
        output = b"".join(chunks).decode("utf-8", errors="replace")
        return ExecutionResult(output, exit_code, timed_out, latency)

    def get_diff(self) -> str:
        result = self.execute(
            "git add --intent-to-add -- . && git diff --binary HEAD --", timeout=60
        )
        if result.timed_out or result.exit_code != 0:
            raise RuntimeError("Unable to collect final git diff")
        return result.output

    def close(self) -> None:
        container, client = self.container, self.client
        self.container = None
        self.client = None
        if container is not None:
            try:
                container.stop(timeout=10)
            except Exception:
                try:
                    container.kill()
                except Exception:
                    pass
            try:
                container.remove(force=True)
            except docker.errors.NotFound:
                pass
        if client is not None:
            client.close()

    def __enter__(self) -> "DockerEnv":
        return self.start()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
