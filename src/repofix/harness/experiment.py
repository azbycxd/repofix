"""Sequential variant runner. Fake mode cannot construct a provider or Docker."""
import json
import subprocess
import time
from pathlib import Path

from repofix.agent import RepoFixAgent, TrajectoryWriter
from repofix.tasks.spec import load_tasks
from repofix.tasks.judge import judge
from .config import HarnessConfig
from .fake import FakeEnv
from .model import FakeModelClient

VARIANTS = ("v1", "v3-single", "v3-multi", "v3-nocompact")


def variant_config(variant, kind):
    if variant == "v1":
        return HarnessConfig.for_profile("v1")
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant: {variant}")
    return HarnessConfig.for_profile("v3", task_kind=kind,
        subagents="both" if variant == "v3-multi" else "none",
        verify_on_submit=variant == "v3-multi", context_management=variant != "v3-nocompact")


def fake_env():
    def tests(env):
        passed = env.files.get("example.py") == "VALUE = 2\n"
        return ("1 passed" if passed else "AssertionError: VALUE is wrong", 0 if passed else 1, False, 0)
    return FakeEnv(commands={"python -m pytest": tests})


def fake_script(variant):
    calls = [("bash", {"command": "python -m pytest"})]
    if variant != "v1":
        calls.append(("update_plan", {"steps": [{"step": "implement and test", "status": "in_progress"}]}))
    if variant == "v3-multi":
        calls.append(("explore", {"question": "Locate VALUE", "thoroughness": "quick"}))
    calls += [("view", {"path": "example.py", "start_line": 1, "end_line": 1}),
              ("str_replace", {"path": "example.py", "old_str": "1", "new_str": "2"}),
              ("bash", {"command": "python -m pytest"}), ("submit", {})]
    return [{"calls": [call]} for call in calls]


def fake_child(mode):
    if mode == "explore":
        return FakeModelClient([{"calls": [("grep", {"pattern": "VALUE"})]},
                                {"calls": [("report", {"summary": "VALUE is defined in example.py."})]}])
    return FakeModelClient([{"calls": [("bash", {"command": "python -m pytest"})]},
                            {"calls": [("report", {"verdict": "PASS", "evidence": "pytest passed after edit"})]}])


def run_experiment(tasks_path, variants, repeats, output, fake=False, api_key="", holdout_path=None):
    import contextlib
    from dataclasses import asdict
    tasks = load_tasks(tasks_path)
    root = Path(__file__).resolve().parents[3]
    holdouts = set(Path(holdout_path or root / "holdout.txt").read_text().split())
    if any(task.id in holdouts for task in tasks):
        raise ValueError("Refusing to run HOLDOUT tasks")
    if not tasks or repeats < 1 or not variants or any(v not in VARIANTS for v in variants):
        raise ValueError("nonempty tasks/valid variants and repeats >= 1 required")
    if not fake and not api_key:
        raise ValueError("real experiment requires DEEPSEEK_API_KEY")
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("experiment output must be empty (no accidental reruns/overwrites)")
    output.mkdir(parents=True, exist_ok=True)
    commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    rows = []
    writer = TrajectoryWriter(output / "results.jsonl", secrets=[api_key])
    for task in tasks:
        for variant in variants:
            for repeat in range(1, repeats + 1):
                config = variant_config(variant, task.kind)
                run_dir = output / task.id / variant / str(repeat)
                run_dir.mkdir(parents=True)
                started = time.monotonic()
                row = dict(task=task.id, variant=variant, repeat=repeat, fake=fake,
                    resolved=None, submitted=False, termination_reason="interrupted", steps=0,
                    tool_calls=0, subagent_calls=0, compactions=0, hook_blocks=0,
                    prompt_tokens=0, completion_tokens=0, cache_hit_tokens=None,
                    estimated_cost=0.0, wall_seconds=0.0)
                try:
                    if fake:
                        environment = contextlib.nullcontext(fake_env())
                        client = FakeModelClient(fake_script(variant))
                    else:
                        from repofix.tasks.docker import task_environment
                        environment, client = task_environment(task, config), None
                    with environment as env:
                        agent = RepoFixAgent(env, task.prompt, run_dir / "trajectory.jsonl", api_key,
                                             commit, config, client)
                        if not fake:
                            agent.resume_metadata = {"kind": "task", "repo_url": task.repo_url,
                                "base_commit": task.base_commit, "instance_id": task.id,
                                "setup_commands": task.setup_commands}
                        if fake:
                            agent.subagent_client_factory = fake_child
                        result = agent.run()
                        (run_dir / "full.patch").write_text(agent.trace._redact_text(result.patch))
                        from repofix.evaluation import build_evaluation_patch
                        (run_dir / "production.patch").write_text(agent.trace._redact_text(build_evaluation_patch(result.patch).patch))
                    if fake:
                        # Exercise judge ordering in a fresh in-memory environment.
                        def judge_factory(spec):
                            target = fake_env()
                            for node in [*spec.fail_to_pass, *spec.pass_to_pass]:
                                target.commands["python -m pytest -q " + __import__('shlex').quote(node)] = target.commands["python -m pytest"]
                            return contextlib.nullcontext(target)
                    else:
                        from repofix.tasks.docker import task_environment
                        judge_factory = task_environment
                    verdict = judge(task, result.patch, judge_factory) if task.fail_to_pass else None
                    counters = getattr(getattr(agent, "state", None), "counters", {})
                    row.update(resolved=verdict.resolved if verdict else None, submitted=result.submitted,
                        termination_reason=result.status, steps=result.steps, tool_calls=result.tool_calls,
                        subagent_calls=counters.get("subagent_calls", 0), compactions=counters.get("compactions", 0),
                        hook_blocks=counters.get("hook_blocks", 0), prompt_tokens=result.prompt_tokens,
                        completion_tokens=result.completion_tokens, cache_hit_tokens=result.cache_hit_tokens,
                        estimated_cost=result.max_estimated_cost_usd, trajectory=str(run_dir / "trajectory.jsonl"))
                    if verdict:
                        (run_dir / "judge.json").write_text(json.dumps(agent.trace._redact(asdict(verdict)), indent=2))
                except Exception as exc:
                    row["error"] = writer._redact_text(str(exc))
                row["wall_seconds"] = time.monotonic() - started
                writer.write(row)
                rows.append(row)
    return rows
