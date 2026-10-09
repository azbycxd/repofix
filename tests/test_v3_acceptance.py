"""Cross-milestone checks for the complete public V3 configuration."""
import json
import subprocess
from pathlib import Path

from repofix.agent import RepoFixAgent
from repofix.harness.config import HarnessConfig
from repofix.harness.fake import FakeEnv
from repofix.harness.model import FakeModelClient
from repofix.evaluation import build_evaluation_patch


def test_feature_new_files_full_loop_and_production_filter(tmp_path):
    patch = "*** Begin Patch\n*** Add File: feature.py\n+VALUE = 2\n*** Add File: tests/test_feature.py\n+from feature import VALUE\n+def test_value():\n+    assert VALUE == 2\n*** End Patch"
    script = [{"calls": [("update_plan", {"steps": [{"step": "feature", "status": "in_progress"}]})]},
              {"calls": [("apply_patch", {"patch": patch})]},
              {"calls": [("bash", {"command": "python -m pytest"})]}, {"calls": [("submit", {})]}]
    env = FakeEnv(commands={"python -m pytest": ("1 passed", 0, False, 0)})
    agent = RepoFixAgent(env, "Add a value feature.", tmp_path / "t.jsonl", "", "test",
                        HarnessConfig.for_profile("v3", task_kind="feature"), FakeModelClient(script))
    result = agent.run()
    assert result.submitted
    assert "tests/test_feature.py" in result.patch and "feature.py" in result.patch
    assert "tests/test_feature.py" not in build_evaluation_patch(result.patch).patch
    assert (tmp_path / "checkpoints/step-0004.json").exists()


def test_verify_submit_only_two_rounds_then_forced(tmp_path):
    main = FakeModelClient([{"calls": [("submit", {})]}] * 3)
    child = lambda mode: FakeModelClient([
        {"calls": [("bash", {"command": "python -m pytest"})]},
        {"calls": [("report", {"verdict": "FAIL", "evidence": "test failed"})]}])
    env = FakeEnv(commands={"python -m pytest": ("1 failed", 1, False, 0)})
    agent = RepoFixAgent(env, "fix", tmp_path / "t", "", "test",
                        HarnessConfig.for_profile("v3", subagents="both", verify_on_submit=True), main)
    agent.subagent_client_factory = child
    result = agent.run()
    assert result.status == "submit_forced" and agent.state.counters["subagent_calls"] == 2
    assert len(main.requests) == 3


def test_new_file_materialization_keeps_original_repo_clean(tmp_path):
    from repofix.worktree import TemporaryGitWorktree, inspect_repository
    repo = tmp_path / "repo"; repo.mkdir()
    for args in [("init", "-q"), ("config", "user.name", "Test"), ("config", "user.email", "offline@example.invalid")]:
        subprocess.run(["git", "-C", str(repo), *args], check=True)
    (repo / "base.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    before = inspect_repository(repo)
    patch = "diff --git a/new.py b/new.py\nnew file mode 100644\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+VALUE = 2\n"
    with TemporaryGitWorktree(repo) as worktree:
        worktree.materialize_patch(patch, stage_new=True)
        assert "new.py" in worktree.diff()
    assert inspect_repository(repo) == before


def test_all_v3_switches_can_be_disabled_and_v1_stays_exact():
    from repofix.agent import AgentConfig
    from dataclasses import asdict
    flags = ("parallel_readonly", "hooks_enabled", "apply_patch_enabled", "read_before_edit", "background_shell",
             "context_management", "plan_tool", "checkpointing", "permissions_enabled", "sandbox_hardening")
    config = HarnessConfig.for_profile("v3", **{flag: False for flag in flags})
    assert all(not getattr(config, flag) for flag in flags)
    assert asdict(HarnessConfig.for_profile("v1").legacy_config()) == asdict(AgentConfig())
