import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass(frozen=True)
class TaskSpec:
    id: str
    kind: str
    repo_url: str
    base_commit: str
    prompt: str
    hidden_test_patch: str = ""
    fail_to_pass: list[str] = field(default_factory=list)
    pass_to_pass: list[str] = field(default_factory=list)
    setup_commands: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", self.id) or self.id in {".", ".."}:
            raise ValueError("invalid task id")
        if self.kind not in {"bugfix", "feature"}:
            raise ValueError("kind must be bugfix or feature")
        if not isinstance(self.repo_url, str) or not self.repo_url.strip():
            raise ValueError("repo_url is required")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", self.base_commit):
            raise ValueError("base_commit must be a full 40-character Git SHA")
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ValueError("prompt is required")
        if not isinstance(self.hidden_test_patch, str):
            raise ValueError("hidden_test_patch must be text")
        for name in ("fail_to_pass", "pass_to_pass", "setup_commands"):
            value = getattr(self, name)
            if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
                raise ValueError(f"{name} must be a list of nonempty strings")
        if set(self.fail_to_pass) & set(self.pass_to_pass):
            raise ValueError("fail_to_pass and pass_to_pass overlap")

    def public_task(self):
        # This is the only task payload supplied to an Agent.
        return {"id": self.id, "kind": self.kind, "repo_url": self.repo_url,
                "base_commit": self.base_commit, "prompt": self.prompt}


def load_document(path):
    text = Path(path).read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except ValueError:
        # PyYAML is already an installed SWE-bench dependency, not a V3 addition.
        try:
            import yaml
        except ImportError as exc:
            raise ValueError("YAML input needs the existing SWE-bench dependencies; JSON is also accepted") from exc
        return yaml.safe_load(text)


def load_tasks(path):
    data = load_document(path)
    if not isinstance(data, list):
        raise ValueError("tasks file must contain a list")
    tasks = [TaskSpec(**item) for item in data]
    if len({task.id for task in tasks}) != len(tasks):
        raise ValueError("duplicate task IDs")
    return tasks
