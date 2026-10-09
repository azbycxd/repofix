import contextlib
import subprocess
import sys
from dataclasses import asdict

import pytest

from repofix.agent import SYSTEM_PROMPT
from repofix.cli import build_parser
from repofix.harness.fake import FakeEnv
from repofix.tasks.build import build_task, git
from repofix.tasks.judge import judge
from repofix.tasks.prompts import task_messages
from repofix.tasks.spec import TaskSpec


def test_spec_prompts_and_hidden_boundary():
    task = TaskSpec(
        "example",
        "feature",
        "/local/repo",
        "a" * 40,
        "add behavior",
        "SECRET_HIDDEN_TEST",
        ["test.py::test_new"],
    )
    assert "hidden" not in str(task.public_task()).lower()
    assert "SECRET_HIDDEN_TEST" not in str(task_messages(task.kind, task.prompt))
    assert "acceptance tests" in task_messages("feature", "task")[0]["content"]
    assert task_messages("bugfix", "issue")[0]["content"] == SYSTEM_PROMPT
    for overrides in [{"id": "../escape"}, {"kind": "other"}, {"base_commit": "fake"}]:
        with pytest.raises(ValueError):
            TaskSpec(**{**asdict(task), **overrides})


def test_judge_only_passes_all_cases_and_applies_hidden_separately():
    task = TaskSpec(
        "id",
        "feature",
        "/repo",
        "a" * 40,
        "task",
        "hidden patch",
        ["test.py::new"],
        ["test.py::old"],
    )
    env = FakeEnv(
        commands={
            "python -m pytest -q test.py::new": ("1 passed", 0, False, 0),
            "python -m pytest -q test.py::old": ("1 passed", 0, False, 0),
        }
    )
    assert judge(task, "", lambda task: contextlib.nullcontext(env)).resolved
    assert env.output_files["/tmp/repofix_hidden_tests.patch"] == "hidden patch"
    env.commands["python -m pytest -q test.py::old"] = ("1 skipped", 0, False, 0)
    assert not judge(task, "", lambda task: contextlib.nullcontext(env)).resolved


def test_build_task_real_local_git_commits(tmp_path):
    repo = tmp_path / "upstream"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "offline@example.invalid")
    git(repo, "config", "user.name", "Offline")
    (repo / "calc.py").write_text("def double(x):\n    return x\n")
    (repo / "test_calc.py").write_text(
        "from calc import double\ndef test_zero():\n    assert double(0) == 0\n"
    )
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD").strip()
    (repo / "calc.py").write_text("def double(x):\n    return x * 2\n")
    (repo / "test_calc.py").write_text(
        "from calc import double\ndef test_zero():\n    assert double(0) == 0\ndef test_double():\n    assert double(3) == 6\n"
    )
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "feature")
    merged = git(repo, "rev-parse", "HEAD").strip()

    def evaluate(checkout, setup, nodes):
        nodes = nodes or ["test_calc.py::test_zero", "test_calc.py::test_double"]
        return {
            node: subprocess.run(
                [sys.executable, "-B", "-m", "pytest", "-q", node],
                cwd=checkout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).returncode
            == 0
            for node in nodes
        }

    task = build_task(
        {
            "id": "double",
            "repo_url": str(repo),
            "base_commit": base,
            "merged_commit": merged,
            "description": "Support doubling numeric values.",
        },
        evaluate,
    )
    assert task.fail_to_pass == ["test_calc.py::test_double"]
    assert task.pass_to_pass == ["test_calc.py::test_zero"]
    assert "diff --git a/calc.py" not in task.hidden_test_patch
    assert git(repo, "rev-parse", "HEAD").strip() == merged


def test_cli_feature_alias():
    args = build_parser().parse_args(
        ["--repo", "/repo", "--task", "feature", "--kind", "feature", "--profile", "v3"]
    )
    assert args.issue == "feature" and args.kind == "feature"
