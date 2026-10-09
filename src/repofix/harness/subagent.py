"""Independent child state, shared budget, depth-one restricted tool loops."""
import json
import re
import time
from dataclasses import asdict, replace

from repofix.agent import RepoFixAgent
from .state import RunState
from .checkpoint import capture_workspace, restore_workspace

EXPLORE_STEPS = {"quick": 8, "medium": 15, "thorough": 25}


def clean_report(text):
    lines = [line for line in str(text).splitlines() if not re.search(
        r"(?i)(<\|(?:system|assistant|developer)|</?(?:system|developer)>|^\s*(?:system|developer|assistant)\s*:|ignore (?:all|previous)|override .*instructions)", line)]
    return "[subagent report]\n" + "\n".join(lines)[:1500]


def run_subagent(runtime, mode, question="", thoroughness="medium"):
    parent, state = runtime.agent, runtime.state
    if state.depth >= 1:
        raise ValueError("subagent depth limit is 1")
    if mode not in {"explore", "verify"}:
        raise ValueError("unknown subagent mode")
    if thoroughness not in EXPLORE_STEPS:
        raise ValueError("thoroughness must be quick, medium or thorough")
    state.count("subagent_calls")
    number = state.counters["subagent_calls"]
    config = replace(parent.config, subagents="none", max_steps=EXPLORE_STEPS[thoroughness] if mode == "explore" else 15,
        checkpointing=False, hooks_enabled=False, background_shell=False, plan_tool=False,
        apply_patch_enabled=False, verify_on_submit=False)
    system = ("Explore the repository read-only. Answer the question with evidence. Finish with report(summary), at most 1500 characters."
              if mode == "explore" else
              "Verify the current diff using relevant tests. Do not change /testbed. You may write temporary tests under /tmp. "
              "Run tests and finish with report(verdict, evidence). State PASS only with successful test evidence.")
    task = question if mode == "explore" else question + "\nCurrent full diff:\n" + runtime.env.get_diff()
    child_state = RunState(messages=[{"role": "system", "content": system}, {"role": "user", "content": task}],
                           budget=state.budget, depth=1)
    path = parent.trace.path.parent / "subagents" / f"{mode}-{number}" / "trajectory.jsonl"
    factory = getattr(parent, "subagent_client_factory", None)
    client = factory(mode) if factory else parent.client
    child = RepoFixAgent(runtime.env, task, path, "", parent.git_commit, config, client)
    child.trace.secrets = list(parent.trace.secrets)
    child.state, child.subagent_mode = child_state, mode
    child.budget_lock = parent.budget_lock
    child.allowed_tools = {"view", "grep", "search_code", "report"} | ({"bash"} if mode == "verify" else set())
    snapshot = capture_workspace(runtime.env) if mode == "verify" else None
    before_budget = asdict(state.budget)
    started = time.monotonic()
    state.events.append({"type": "subagent_start", "mode": mode, "number": number})
    changed = False
    try:
        child.run()
    finally:
        if snapshot is not None:
            changed = capture_workspace(runtime.env) != snapshot
            if changed:
                restore_workspace(runtime.env, snapshot)
        event = {"type": "subagent_end", "mode": mode, "number": number,
                 "workspace_restored": changed, "latency_seconds": time.monotonic() - started,
                 "usage": {key: value - before_budget[key] for key, value in asdict(state.budget).items() if key != "cache_hit_available"}}
        state.events.append(event)
        parent.trace.write(event)
    report = child_state.metadata.get("report")
    if mode == "explore":
        return clean_report(report or "No report produced before the child stopped.")
    commands = child_state.metadata.get("verification_commands", [])
    verdict = report.get("verdict", "FAIL") if isinstance(report, dict) else "FAIL"
    if not commands or not any(item["exit_code"] == 0 and item["verification"] for item in commands):
        verdict = "FAIL"
    evidence = report.get("evidence", "No final report") if isinstance(report, dict) else "No final report"
    if changed:
        evidence += "\nChild modified /testbed; all workspace changes were restored."
        verdict = "FAIL"  # A pass measured on reverted code is not final evidence.
    result = {"verdict": verdict, "commands_run": commands, "evidence": clean_report(evidence), "workspace_restored": changed}
    return result
