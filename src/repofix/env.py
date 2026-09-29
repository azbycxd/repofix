"""Minimal Docker execution environment for one SWE-bench instance."""

from __future__ import annotations

import io
import posixpath
import re
import shlex
import tarfile
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


@dataclass(frozen=True)
class StrReplaceResult:
    output: str
    success: bool
    syntax_rollback: bool


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
        result = self.execute("git diff --binary HEAD --", timeout=60)
        if result.timed_out or result.exit_code != 0:
            raise RuntimeError("Unable to collect final git diff")
        return result.output

    def get_tracked_changes(self) -> list[tuple[str, str]]:
        """Return modified/staged tracked paths without including untracked files."""
        result = self.execute("git diff --name-status HEAD --", timeout=60)
        if result.timed_out or result.exit_code != 0:
            raise RuntimeError("Unable to inspect tracked repository changes")
        changes: list[tuple[str, str]] = []
        for line in result.output.splitlines():
            fields = line.split("\t")
            if len(fields) < 2:
                continue
            status = fields[0]
            path = fields[-1]
            changes.append((status, path))
        return changes

    def read_repository_text_files(
        self, max_file_bytes: int = 1_000_000
    ) -> dict[str, str]:
        """Read UTF-8 tracked files from the current /testbed Git revision."""
        if self.container is None:
            raise RuntimeError("DockerEnv has not been started")
        if max_file_bytes <= 0:
            raise ValueError("max_file_bytes must be positive")

        archived = self.container.exec_run(
            ["git", "archive", "--format=tar", "HEAD"],
            workdir=self.workdir,
            user="root",
        )
        if archived.exit_code != 0:
            details = archived.output.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"Unable to archive /testbed repository: {details}")

        files: dict[str, str] = {}
        with tarfile.open(fileobj=io.BytesIO(archived.output), mode="r:") as tar:
            for member in tar.getmembers():
                if not member.isfile() or member.size > max_file_bytes:
                    continue
                path = posixpath.normpath(member.name)
                if path.startswith("../") or posixpath.isabs(path):
                    continue
                extracted = tar.extractfile(member)
                if extracted is None:
                    continue
                payload = extracted.read()
                if b"\x00" in payload:
                    continue
                try:
                    files[path] = payload.decode("utf-8")
                except UnicodeDecodeError:
                    continue
        return files

    def write_text_file(self, path: str, content: str) -> None:
        """Write UTF-8 text inside the container without shell interpolation."""
        if self.container is None or self.client is None:
            raise RuntimeError("DockerEnv has not been started")
        directory, filename = posixpath.split(path)
        if directory != "/tmp" or not filename or posixpath.basename(filename) != filename:
            raise ValueError("tool output files must be direct children of /tmp")

        self._put_text_file(directory, filename, content, 0o600)

    def _put_text_file(
        self, directory: str, filename: str, content: str, mode: int
    ) -> None:
        if self.container is None:
            raise RuntimeError("DockerEnv has not been started")
        payload = content.encode("utf-8")
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            info = tarfile.TarInfo(name=filename)
            info.size = len(payload)
            info.mode = mode
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(payload))
        if not self.container.put_archive(directory, archive.getvalue()):
            raise RuntimeError(f"Unable to write {posixpath.join(directory, filename)}")

    def _resolve_testbed_file(self, path: str) -> str:
        if self.container is None or self.client is None:
            raise RuntimeError("DockerEnv has not been started")
        if not path or "\x00" in path:
            raise ValueError("path must be a non-empty file path")
        candidate = posixpath.normpath(
            path if posixpath.isabs(path) else posixpath.join(self.workdir, path)
        )
        try:
            if posixpath.commonpath([self.workdir, candidate]) != self.workdir:
                raise ValueError("path must stay inside /testbed")
        except ValueError as exc:
            raise ValueError("path must stay inside /testbed") from exc

        result = self.container.exec_run(
            ["readlink", "-f", "--", candidate],
            workdir=self.workdir,
            user="root",
        )
        if result.exit_code != 0:
            raise ValueError(f"file does not exist: {path}")
        resolved = result.output.decode("utf-8", errors="replace").strip()
        try:
            if posixpath.commonpath([self.workdir, resolved]) != self.workdir:
                raise ValueError("resolved path must stay inside /testbed")
        except ValueError as exc:
            raise ValueError("resolved path must stay inside /testbed") from exc
        is_file = self.container.exec_run(
            ["test", "-f", resolved], workdir=self.workdir, user="root"
        )
        if is_file.exit_code != 0:
            raise ValueError(f"not a regular file: {path}")
        return resolved

    def _read_text_file(self, path: str) -> tuple[str, str, int]:
        if self.container is None:
            raise RuntimeError("DockerEnv has not been started")
        resolved = self._resolve_testbed_file(path)
        stream, _ = self.container.get_archive(resolved)
        archive = io.BytesIO(b"".join(stream))
        with tarfile.open(fileobj=archive, mode="r:*") as tar:
            members = [member for member in tar.getmembers() if member.isfile()]
            if len(members) != 1:
                raise RuntimeError(f"Unable to read regular file: {path}")
            extracted = tar.extractfile(members[0])
            if extracted is None:
                raise RuntimeError(f"Unable to read regular file: {path}")
            try:
                content = extracted.read().decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"file is not valid UTF-8: {path}") from exc
            return resolved, content, members[0].mode

    @staticmethod
    def _numbered_lines(content: str, start_line: int, end_line: int) -> str:
        lines = content.splitlines()
        if start_line < 1 or end_line < start_line:
            raise ValueError("line range must satisfy 1 <= start_line <= end_line")
        if start_line > len(lines):
            raise ValueError(
                f"start_line {start_line} exceeds file length {len(lines)}"
            )
        selected = lines[start_line - 1 : min(end_line, len(lines))]
        return "\n".join(
            f"{line_number:6d}\t{line}"
            for line_number, line in enumerate(selected, start=start_line)
        )

    def view_file(self, path: str, start_line: int, end_line: int) -> str:
        resolved, content, _ = self._read_text_file(path)
        numbered = self._numbered_lines(content, start_line, end_line)
        return f"{resolved}\n{numbered}"

    def str_replace_file(
        self, path: str, old_str: str, new_str: str
    ) -> StrReplaceResult:
        if not old_str:
            return StrReplaceResult(
                "str_replace error: old_str must not be empty", False, False
            )
        resolved, original, mode = self._read_text_file(path)
        occurrences = original.count(old_str)
        if occurrences == 0:
            return StrReplaceResult(
                "str_replace error: old_str was not found; file was not modified",
                False,
                False,
            )
        if occurrences != 1:
            return StrReplaceResult(
                f"str_replace error: old_str occurs {occurrences} times; "
                "expected exactly 1; file was not modified",
                False,
                False,
            )

        updated = original.replace(old_str, new_str, 1)
        directory, filename = posixpath.split(resolved)
        self._put_text_file(directory, filename, updated, mode)
        syntax_message = "Syntax check skipped for non-Python file."
        if resolved.endswith(".py"):
            compile_result = self.execute(
                f"python -m py_compile {shlex.quote(resolved)}", timeout=60
            )
            if compile_result.timed_out or compile_result.exit_code != 0:
                self._put_text_file(directory, filename, original, mode)
                details = (
                    compile_result.output.strip()
                    or "py_compile failed without output"
                )
                return StrReplaceResult(
                    "str_replace syntax check failed; original file restored.\n"
                    f"{details}",
                    False,
                    True,
                )
            syntax_message = "py_compile passed."

        replacement_offset = original.index(old_str)
        start_line = updated.count("\n", 0, replacement_offset) + 1
        replacement_lines = max(1, new_str.count("\n") + 1)
        total_lines = len(updated.splitlines())
        context_start = max(1, start_line - 3)
        context_end = min(total_lines, start_line + replacement_lines + 2)
        context = self._numbered_lines(updated, context_start, context_end)
        return StrReplaceResult(
            f"Replaced exactly one occurrence. {syntax_message}\n"
            f"{resolved}\n{context}",
            True,
            False,
        )

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
