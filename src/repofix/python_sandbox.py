"""Build a small Python repository image for offline Agent execution."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

import docker


PYTHON_BASE_IMAGE = "python:3.12-slim"
PYTEST_VERSION = "8.3.5"


class PythonSandboxError(RuntimeError):
    """Raised when the local Python repository image cannot be built."""


def dependency_install_commands(repository: Path) -> tuple[str, ...]:
    commands = [
        f"python -m pip install --no-cache-dir pytest=={PYTEST_VERSION}",
    ]
    if (repository / "requirements.txt").is_file():
        commands.append(
            "python -m pip install --no-cache-dir -r requirements.txt"
        )
    if any(
        (repository / name).is_file()
        for name in ("pyproject.toml", "setup.py", "setup.cfg")
    ):
        commands.append("python -m pip install --no-cache-dir -e .")
    return tuple(commands)


def render_dockerfile(repository: Path) -> tuple[str, tuple[str, ...]]:
    install_commands = dependency_install_commands(repository)
    install_layers = "\n".join(f"RUN {command}" for command in install_commands)
    dockerfile = f"""FROM {PYTHON_BASE_IMAGE}
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update \\
    && apt-get install -y --no-install-recommends git \\
    && rm -rf /var/lib/apt/lists/*
WORKDIR /testbed
COPY . /testbed
RUN rm -rf /testbed/.git /testbed/.repofix.Dockerfile \\
    && git init \\
    && git config user.name RepoFix \\
    && git config user.email repofix@localhost \\
    && git add -A \\
    && git commit -m baseline
{install_layers}
CMD ["tail", "-f", "/dev/null"]
"""
    return dockerfile, install_commands


@dataclass(frozen=True)
class SandboxBuild:
    image: str
    build_seconds: float
    install_commands: tuple[str, ...]


class PythonSandboxImage:
    def __init__(self, repository: Path, run_id: str) -> None:
        self.repository = repository.resolve()
        safe_run = re.sub(r"[^a-z0-9_.-]", "-", run_id.lower())[:80]
        self.tag = f"repofix-local:{safe_run}"
        self.client: docker.DockerClient | None = None
        self.build: SandboxBuild | None = None

    def __enter__(self) -> SandboxBuild:
        dockerfile_path = self.repository / ".repofix.Dockerfile"
        if dockerfile_path.exists():
            raise PythonSandboxError(
                "temporary worktree already contains .repofix.Dockerfile"
            )
        dockerfile, install_commands = render_dockerfile(self.repository)
        dockerfile_path.write_text(dockerfile, encoding="utf-8")
        started = time.monotonic()
        self.client = docker.from_env()
        try:
            image, _ = self.client.images.build(
                path=str(self.repository),
                dockerfile=dockerfile_path.name,
                tag=self.tag,
                rm=True,
                forcerm=True,
                labels={"repofix.kind": "local-python-sandbox"},
            )
            image.reload()
        except docker.errors.BuildError as exc:
            details = []
            for item in exc.build_log or []:
                text = item.get("stream") or item.get("error")
                if text:
                    details.append(text.strip())
            tail = "\n".join(details[-12:]) or str(exc)
            self._cleanup_client()
            raise PythonSandboxError(f"Python sandbox build failed:\n{tail}") from exc
        except Exception as exc:
            self._cleanup_client()
            raise PythonSandboxError(f"Python sandbox build failed: {exc}") from exc
        finally:
            dockerfile_path.unlink(missing_ok=True)

        self.build = SandboxBuild(
            image=self.tag,
            build_seconds=time.monotonic() - started,
            install_commands=install_commands,
        )
        return self.build

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._cleanup_client(remove_image=True)

    def _cleanup_client(self, remove_image: bool = False) -> None:
        if self.client is not None:
            try:
                if remove_image:
                    self.client.images.remove(self.tag, force=True, noprune=False)
            except docker.errors.ImageNotFound:
                pass
            finally:
                self.client.close()
        self.client = None
