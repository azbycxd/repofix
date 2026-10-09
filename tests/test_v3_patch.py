import pytest

from repofix.agent import RepoFixAgent
from repofix.harness.config import HarnessConfig
from repofix.harness.fake import FakeEnv
from repofix.harness.model import FakeModelClient
from repofix.harness.runtime import Runtime
from repofix.harness.state import RunState
from repofix.harness.tools.patch import parse_patch, prepare_patch, update_content
from repofix.harness.workspace import RepoFiles


@pytest.mark.parametrize("old,context", [("x ", "x "), ("x  ", "x"), ("  x ", "x"), ("“x”", '"x"')])
def test_four_context_matching_levels(old, context):
    assert update_content("f", old + "\n", [["-" + context, "+y"]]) == "y\n"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "*** Begin Patch\n*** End Patch",
        "*** Begin Patch\n*** Update File: x\n+bad\n*** End Patch",
    ],
)
def test_invalid_patch(text):
    with pytest.raises(ValueError):
        parse_patch(text)


def test_atomic_prepare_and_syntax_rollback():
    env = FakeEnv({"a.py": "x = 1\n", "b.py": "y = 2\n"})
    files = RepoFiles(env)
    patch = "*** Begin Patch\n*** Update File: a.py\n@@\n-x = 1\n+x = 3\n*** Update File: b.py\n@@\n-missing\n+y = 3\n*** End Patch"
    with pytest.raises(ValueError, match="b.py: context not found"):
        prepare_patch(patch, files)
    assert env.files == env.baseline
    with pytest.raises(SyntaxError):
        files.apply({"a.py": "x = 3\n", "b.py": "y = (", "new.py": "ok = 1\n"})
    assert env.files == env.baseline


def test_add_move_delete():
    env = FakeEnv({"old.txt": "old\n", "delete.txt": "delete\n"})
    files = RepoFiles(env)
    patch = "*** Begin Patch\n*** Update File: old.txt\n*** Move to: moved.txt\n@@\n-old\n+new\n*** Delete File: delete.txt\n*** Add File: new.py\n+x = 1\n*** End Patch"
    files.apply(prepare_patch(patch, files))
    assert env.files == {"moved.txt": "new\n", "new.py": "x = 1\n"}


def test_read_guard_unseen_read_and_stale(tmp_path):
    env = FakeEnv()
    runtime = Runtime(
        RepoFixAgent(
            env,
            "test",
            tmp_path / "t.jsonl",
            "",
            "test",
            HarnessConfig.for_profile("v3"),
            FakeModelClient([]),
        ),
        RunState(),
    )
    args = {"path": "example.py", "old_str": "1", "new_str": "2"}
    with pytest.raises(ValueError, match="view"):
        runtime.replace(args)
    runtime.view({"path": "example.py", "start_line": 1, "end_line": 1})
    assert "successful" in runtime.replace(args).content
    assert "successful" in runtime.replace({**args, "old_str": "2", "new_str": "3"}).content
    env.files["example.py"] = "VALUE = 4\n"  # external/bash change
    with pytest.raises(ValueError, match="content changed"):
        runtime.replace({**args, "old_str": "4", "new_str": "5"})
    runtime.apply_patch({"patch": "*** Begin Patch\n*** Add File: new.txt\n+hello\n*** End Patch"})
    assert (
        "successful"
        in runtime.replace({"path": "new.txt", "old_str": "hello", "new_str": "world"}).content
    )
    runtime.apply_patch(
        {
            "patch": "*** Begin Patch\n*** Update File: new.txt\n*** Move to: moved.txt\n@@\n-world\n+again\n*** End Patch"
        }
    )
    assert "new.txt" not in runtime.state.file_reads
    assert (
        "successful"
        in runtime.replace({"path": "moved.txt", "old_str": "again", "new_str": "done"}).content
    )


@pytest.mark.parametrize("path", ["../evil", "/etc/passwd", "a/../b", ".git/config", "link/file"])
def test_path_escape(path):
    env = FakeEnv()
    env.symlinks["link"] = "/etc"
    with pytest.raises(ValueError):
        RepoFiles(env).resolve(path)
