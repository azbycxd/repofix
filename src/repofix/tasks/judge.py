"""Judge-only hidden tests; never share this environment/context with Agent."""

import re
import shlex
from dataclasses import dataclass

from repofix.evaluation import build_evaluation_patch


@dataclass(frozen=True)
class JudgeResult:
    resolved: bool
    tests: dict[str, bool]
    error: str | None = None


def apply_diff(env, patch, name):
    if not patch.strip():
        return
    path = f"/tmp/repofix_{name}.patch"
    env.write_text_file(path, patch)
    result = env.execute(f"git apply --binary --whitespace=nowarn {shlex.quote(path)}")
    if result.exit_code != 0 or result.timed_out:
        raise ValueError(f"{name} patch failed: {result.output}")


def run_test(env, node):
    result = env.execute("python -m pytest -q " + shlex.quote(node), timeout=600)
    # Exit 0 alone could mean the test was skipped. Require pytest pass evidence.
    passed = (
        result.exit_code == 0
        and not result.timed_out
        and bool(re.search(r"\b[1-9][0-9]* passed\b", result.output))
    )
    return passed


def judge(task, production_patch, env_factory):
    outcomes = {}
    try:
        with env_factory(task) as env:
            if env.get_diff().strip():
                raise ValueError("judge requires a clean base environment")
            apply_diff(env, build_evaluation_patch(production_patch).patch, "production")
            apply_diff(env, task.hidden_test_patch, "hidden_tests")
            for node in dict.fromkeys([*task.fail_to_pass, *task.pass_to_pass]):
                outcomes[node] = run_test(env, node)
            return JudgeResult(
                bool(task.fail_to_pass) and bool(outcomes) and all(outcomes.values()), outcomes
            )
    except Exception as exc:
        return JudgeResult(False, outcomes, str(exc))
