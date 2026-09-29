"""Temporary Git worktree isolation for local repository runs."""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class LocalRepositoryError(RuntimeError):
    """Raised when a local source repository cannot be used safely."""


def _git(repo: Path, *args: str, input_text: str | None = None) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise LocalRepositoryError(f"unable to execute Git: {exc}") from exc
    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip()
        raise LocalRepositoryError(
            f"git {' '.join(args)} failed in {repo}: {details}"
        )
    return completed.stdout


@dataclass(frozen=True)
class RepositoryState:
    root: Path
    head: str
    status: str


def inspect_repository(path: Path) -> RepositoryState:
    candidate = path.expanduser().resolve()
    if not candidate.is_dir():
        raise LocalRepositoryError(f"repository path is not a directory: {candidate}")
    try:
        root_text = _git(candidate, "rev-parse", "--show-toplevel").strip()
        inside = _git(candidate, "rev-parse", "--is-inside-work-tree").strip()
    except LocalRepositoryError as exc:
        raise LocalRepositoryError(
            f"not a local Git repository: {candidate}"
        ) from exc
    if inside != "true":
        raise LocalRepositoryError(f"not a Git working tree: {candidate}")
    root = Path(root_text).resolve()
    return RepositoryState(
        root=root,
        head=_git(root, "rev-parse", "HEAD").strip(),
        status=_git(root, "status", "--porcelain=v1").strip(),
    )


class TemporaryGitWorktree:
    def __init__(self, repository: Path) -> None:
        self.before = inspect_repository(repository)
        if self.before.status:
            raise LocalRepositoryError(
                "source repository must have a clean working tree before RepoFix runs"
            )
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self.path: Path | None = None
        self.after: RepositoryState | None = None

    def __enter__(self) -> "TemporaryGitWorktree":
        self._temporary = tempfile.TemporaryDirectory(prefix="repofix-worktree-")
        self.path = Path(self._temporary.name) / "repository"
        try:
            _git(
                self.before.root,
                "worktree",
                "add",
                "--detach",
                str(self.path),
                self.before.head,
            )
        except Exception:
            self._temporary.cleanup()
            self._temporary = None
            self.path = None
            raise
        return self

    def materialize_patch(self, patch: str) -> None:
        if self.path is None:
            raise LocalRepositoryError("temporary worktree is not active")
        if patch.strip():
            _git(
                self.path,
                "apply",
                "--binary",
                "--whitespace=nowarn",
                "-",
                input_text=patch,
            )

    def diff(self) -> str:
        if self.path is None:
            raise LocalRepositoryError("temporary worktree is not active")
        return _git(self.path, "diff", "--binary", "HEAD", "--")

    def __exit__(self, exc_type, exc, traceback) -> None:
        cleanup_error: Exception | None = None
        try:
            if self.path is not None:
                try:
                    _git(
                        self.before.root,
                        "worktree",
                        "remove",
                        "--force",
                        str(self.path),
                    )
                except Exception as error:
                    cleanup_error = error
                try:
                    _git(self.before.root, "worktree", "prune")
                except Exception as error:
                    cleanup_error = cleanup_error or error
        finally:
            if self._temporary is not None:
                self._temporary.cleanup()
            self.path = None
            self._temporary = None
            self.after = inspect_repository(self.before.root)

        if self.after.head != self.before.head or self.after.status != self.before.status:
            raise LocalRepositoryError(
                "source repository changed during the isolated RepoFix run"
            )
        if cleanup_error is not None and exc is None:
            raise LocalRepositoryError(
                f"unable to clean temporary Git worktree: {cleanup_error}"
            )
