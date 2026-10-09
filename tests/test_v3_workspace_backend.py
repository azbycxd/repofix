"""Execute container helper source offline in a disposable local Git fixture.

Only the fixed /testbed and /tmp helper paths are redirected into tmp_path.
No model, Docker daemon, network or user's repository is touched.
"""
import os
import shlex
import subprocess
import sys
from pathlib import Path
import pytest

from repofix.env import ExecutionResult
from repofix.harness.workspace import RepoFiles, fingerprint
from repofix.harness.checkpoint import capture_workspace, restore_workspace


class LocalHelperFixture:
    def __init__(self, root):
        self.root = root / "repository"
        self.root.mkdir()
        self.artifacts = root / "artifacts"
        self.artifacts.mkdir()
        self.environment = {**os.environ, "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]}
        for args in [("init", "-q"), ("config", "user.name", "Offline Test"), ("config", "user.email", "offline@example.invalid")]:
            self.git(*args)
        (self.root / "a.py").write_text("VALUE = 1\n")
        self.git("add", "."); self.git("commit", "-qm", "base")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True)

    def execute(self, command, timeout=60):
        args = shlex.split(command)
        assert args[:2] == ["python", "-c"], "only generated Python helpers are allowed"
        code = args[2].replace("'/testbed'", repr(str(self.root)))
        code = code.replace("/tmp/repofix_", str(self.artifacts / "repofix_"))
        result = subprocess.run([sys.executable, "-c", code], cwd=self.root, env=self.environment,
                                capture_output=True, text=True, timeout=timeout)
        return ExecutionResult(result.stdout + result.stderr, result.returncode, False, 0)

    def write_text_file(self, path, content):
        assert path.startswith("/tmp/repofix_")
        (self.artifacts / Path(path).name).write_text(content)


def test_real_helper_transaction_snapshot_restore(tmp_path):
    env = LocalHelperFixture(tmp_path)
    files = RepoFiles(env)
    files.apply({"a.py": "VALUE = 2\n", "new.py": "NEW = True\n"})
    assert files.read("a.py") == "VALUE = 2\n"
    assert "new.py" in env.git("diff", "--name-only", "HEAD")
    (env.root / "repro.txt").write_text("untracked evidence\n")
    snapshot = capture_workspace(env)
    files.apply({"a.py": "VALUE = 3\n", "new.py": None})
    (env.root / "other.txt").write_text("remove on restore")
    restore_workspace(env, snapshot)
    assert files.read("a.py") == "VALUE = 2\n"
    assert files.read("new.py") == "NEW = True\n"
    assert (env.root / "repro.txt").read_text() == "untracked evidence\n"
    assert not (env.root / "other.txt").exists()


def test_real_helper_rollback_and_symlink_escape(tmp_path):
    env = LocalHelperFixture(tmp_path)
    files = RepoFiles(env)
    before_index = env.git("diff", "--cached")
    with pytest.raises(RuntimeError):
        files.apply({"a.py": "VALUE = 2\n", "broken.py": "VALUE = ("})
    assert files.read("a.py") == "VALUE = 1\n" and not files.exists("broken.py")
    assert env.git("diff", "--cached") == before_index
    (env.root / "outside").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(RuntimeError): files.read("outside/file")


def test_real_fingerprint_detects_repeated_dirty_write(tmp_path):
    env = LocalHelperFixture(tmp_path)
    (env.root / "a.py").write_text("VALUE = 2\n")
    before = fingerprint(env)
    (env.root / "a.py").write_text("VALUE = 3\n")
    after = fingerprint(env)
    assert before["a.py"] != after["a.py"]
