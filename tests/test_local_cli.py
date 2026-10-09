from __future__ import annotations

import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from repofix.agent import AgentConfig
from repofix.cli import main
from repofix.python_sandbox import (
    PYTHON_BASE_IMAGE,
    dependency_install_commands,
    render_dockerfile,
)
from repofix.worktree import LocalRepositoryError, TemporaryGitWorktree, inspect_repository


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


class LocalCliTests(unittest.TestCase):
    def make_repository(self, directory: Path) -> Path:
        repo = directory / "source"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "RepoFix Test"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@localhost"],
            check=True,
        )
        (repo / "example.py").write_text("VALUE = 1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "example.py"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "initial"],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        return repo

    def test_temporary_worktree_isolated_and_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.make_repository(Path(directory))
            before = inspect_repository(repo)
            worktree_path = None
            with TemporaryGitWorktree(repo) as worktree:
                worktree_path = worktree.path
                assert worktree_path is not None
                (worktree_path / "example.py").write_text("VALUE = 2\n", encoding="utf-8")
                self.assertEqual(
                    (repo / "example.py").read_text(encoding="utf-8"),
                    "VALUE = 1\n",
                )
            self.assertIsNotNone(worktree_path)
            self.assertFalse(worktree_path.exists())
            after = inspect_repository(repo)
            self.assertEqual(before.head, after.head)
            self.assertEqual(before.status, after.status)

    def test_dirty_repository_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = self.make_repository(Path(directory))
            (repo / "example.py").write_text("VALUE = 2\n", encoding="utf-8")
            with self.assertRaisesRegex(LocalRepositoryError, "clean working tree"):
                TemporaryGitWorktree(repo)

    def test_python_sandbox_strategy_is_small_and_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "requirements.txt").write_text("example==1\n", encoding="utf-8")
            (repo / "pyproject.toml").write_text("[build-system]\n", encoding="utf-8")
            commands = dependency_install_commands(repo)
            dockerfile, rendered_commands = render_dockerfile(repo)

        self.assertEqual(commands, rendered_commands)
        self.assertEqual(len(commands), 3)
        self.assertIn(f"FROM {PYTHON_BASE_IMAGE}", dockerfile)
        self.assertIn("pip install --no-cache-dir -r requirements.txt", dockerfile)
        self.assertIn("pip install --no-cache-dir -e .", dockerfile)
        self.assertNotIn("pip install --no-cache-dir .\n", dockerfile)
        self.assertIn("git commit -m baseline", dockerfile)

    def test_cli_reports_clear_nonzero_error(self) -> None:
        error = io.StringIO()
        with (
            mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": "fake"}),
            mock.patch(
                "repofix.cli.run_local_repository",
                side_effect=LocalRepositoryError("not a local Git repository"),
            ),
            contextlib.redirect_stderr(error),
        ):
            exit_code = main(["--repo", "/missing", "--issue", "broken"])

        self.assertEqual(exit_code, 1)
        self.assertIn("RepoFix error: not a local Git repository", error.getvalue())

    def test_frozen_optional_features_remain_disabled(self) -> None:
        config = AgentConfig()
        self.assertEqual(config.retrieval_mode, "bm25")
        self.assertFalse(config.reviewer_enabled)


if __name__ == "__main__":
    unittest.main()
