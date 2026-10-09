"""Execute container helper source offline in a disposable local Git fixture.

Only the fixed /testbed and /tmp helper paths are redirected into tmp_path.
No model, Docker daemon, network or user's repository is touched.
"""

import os
import shlex
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from repofix.agent import RepoFixAgent
from repofix.env import ExecutionResult
from repofix.harness.checkpoint import capture_workspace, restore_workspace
from repofix.harness.config import HarnessConfig
from repofix.harness.context import ContextManager
from repofix.harness.model import FakeModelClient
from repofix.harness.runtime import Runtime
from repofix.harness.state import RunState
from repofix.harness.tools.files import grep
from repofix.harness.tools.patch import prepare_patch
from repofix.harness.tools.shell import JobManager
from repofix.harness.workspace import RepoFiles, fingerprint


class LocalHelperFixture:
    def __init__(self, root):
        self.root = root / "repository"
        self.root.mkdir()
        self.artifacts = root / "artifacts"
        self.artifacts.mkdir()
        self.environment = {
            **os.environ,
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
        }
        for args in [
            ("init", "-q"),
            ("config", "user.name", "Offline Test"),
            ("config", "user.email", "offline@example.invalid"),
        ]:
            self.git(*args)
        (self.root / "a.py").write_text("VALUE = 1\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True)

    def execute(self, command, timeout=60):
        args = shlex.split(command)
        assert all(len(arg.encode()) < 100_000 for arg in args)

        def redirect(text):
            return text.replace("/testbed", str(self.root)).replace(
                "/tmp/repofix_", str(self.artifacts / "repofix_")
            )

        argv = (
            [sys.executable, "-c", redirect(args[2])]
            if args[:2] == ["python", "-c"]
            else ["/bin/bash", "-c", redirect(command)]
        )
        result = subprocess.run(
            argv,
            cwd=self.root,
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return ExecutionResult(result.stdout + result.stderr, result.returncode, False, 0)

    def write_text_file(self, path, content):
        assert path.startswith("/tmp/repofix_")
        (self.artifacts / Path(path).name).write_text(content)

    @contextmanager
    def temporary_patch_file(self, path, content):
        self.write_text_file(path, content)
        try:
            yield path
        finally:
            (self.artifacts / Path(path).name).unlink(missing_ok=True)

    def read_repository_text_files(self, max_file_bytes=1_000_000):
        return {
            p.relative_to(self.root).as_posix(): p.read_text()
            for p in self.root.rglob("*.py")
            if p.stat().st_size <= max_file_bytes
        }

    def get_diff(self):
        return self.git("diff", "HEAD")


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
    with pytest.raises(RuntimeError):
        files.read("outside/file")


def test_real_fingerprint_detects_repeated_dirty_write(tmp_path):
    env = LocalHelperFixture(tmp_path)
    (env.root / "a.py").write_text("VALUE = 2\n")
    before = fingerprint(env)
    (env.root / "a.py").write_text("VALUE = 3\n")
    after = fingerprint(env)
    assert before["a.py"] != after["a.py"]


def test_large_file_replacement_patch_and_atomic_rollback(tmp_path):
    env = LocalHelperFixture(tmp_path)
    files = RepoFiles(env)
    original = "# padding\n" * 31_000 + "VALUE = 1\n"
    (env.root / "large.py").write_text(original)
    files.apply({"large.py": files.read("large.py").replace("VALUE = 1", "VALUE = 2")})
    patch = (
        "*** Begin Patch\n*** Update File: large.py\n@@\n-VALUE = 2\n+VALUE = 3\n"
        "*** Update File: a.py\n@@\n-VALUE = 1\n+VALUE = 2\n*** End Patch"
    )
    files.apply(prepare_patch(patch, files))
    assert files.read("large.py") == original.replace("VALUE = 1", "VALUE = 3")
    assert files.read("a.py") == "VALUE = 2\n"
    with pytest.raises(RuntimeError):
        files.apply({"large.py": original, "a.py": "VALUE = ("})
    assert files.read("large.py").endswith("VALUE = 3\n")
    assert files.read("a.py") == "VALUE = 2\n"
    assert not list(env.artifacts.glob("repofix_apply_*.json"))


def test_real_grep_fallback_without_rg(tmp_path):
    env = LocalHelperFixture(tmp_path)
    minimal_path = tmp_path / "bin"
    minimal_path.mkdir()
    (minimal_path / "grep").symlink_to("/usr/bin/grep")
    env.environment["PATH"] = str(minimal_path)
    (env.root / "notes.txt").write_text("VALUE found\n")
    assert "a.py:1:VALUE = 1" in grep(env, "VALUE", path_glob="*.py")
    assert "notes.txt" not in grep(env, "VALUE", path_glob="*.py")
    assert grep(env, "not_present") == "No matches."
    assert len(grep(env, "VALUE", max_results=1).splitlines()) == 1


def test_real_background_timeout_poll_and_kill(tmp_path):
    env = LocalHelperFixture(tmp_path)
    jobs = JobManager(env)
    done = jobs.start("sleep 0.3; printf 'first\\nlast\\n'; echo $PAGER")
    try:
        assert jobs.wait(done, 0.01)["still_running"]
        result = jobs.wait(done, 5)
        assert result["exit_code"] == 0 and "last\ncat" in result["output"]
        assert jobs.output(done, 1)["output"] == "cat"
    finally:
        if jobs.output(done)["still_running"]:
            jobs.kill(done)
    running = jobs.start("sleep 30")
    try:
        assert jobs.wait(running, 0.01)["still_running"]
        assert jobs.output(running)["still_running"]
    finally:
        assert jobs.kill(running)["exit_code"] == 137


def test_real_runtime_large_edit_and_read_guard(tmp_path):
    env = LocalHelperFixture(tmp_path)
    (env.root / "a.py").write_text("# padding\n" * 31_000 + "VALUE = 1\n")
    runtime = Runtime(
        RepoFixAgent(
            env,
            "fix",
            tmp_path / "t",
            "",
            "test",
            HarnessConfig.for_profile("v3"),
            FakeModelClient([]),
        ),
        RunState(),
    )
    runtime.view({"path": "a.py", "start_line": 1, "end_line": 1})
    assert (
        "successful"
        in runtime.replace({"path": "a.py", "old_str": "VALUE = 1", "new_str": "VALUE = 2"}).content
    )
    patch = "*** Begin Patch\n*** Update File: a.py\n@@\n-VALUE = 2\n+VALUE = 3\n*** Add File: b.py\n+B = 1\n*** End Patch"
    assert "Patch applied" in runtime.apply_patch({"patch": patch}).content
    env.execute("printf '\\nEXTERNAL = 1\\n' >> a.py")
    with pytest.raises(ValueError, match="content changed"):
        runtime.replace({"path": "a.py", "old_str": "VALUE = 3", "new_str": "VALUE = 4"})


def test_masked_artifact_is_readable_through_real_shell(tmp_path):
    env = LocalHelperFixture(tmp_path)
    content = "evidence " * 4000 + " sentinel-secret"
    state = RunState(
        messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "task"},
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": "x", "type": "function", "function": {"name": "bash", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "x", "content": content},
        ]
    )
    config = HarnessConfig.for_profile("v3", context_window=2000, keep_recent_tool_results=0)
    manager = ContextManager(
        config,
        FakeModelClient([]),
        env,
        tmp_path / "run",
        lambda text: text.replace("sentinel-secret", "[REDACTED]"),
    )
    assert not manager.maybe_compact(state)["summary_called"]
    (host_file,) = manager.artifact_dir.glob("*.txt")
    result = env.execute("cat " + shlex.quote(manager.container_path(host_file)))
    assert result.exit_code == 0 and result.output == host_file.read_text()
    assert "sentinel-secret" not in result.output and "[REDACTED]" in result.output
