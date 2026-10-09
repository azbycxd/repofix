"""Public agent facade with explicit profile dispatch."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .core import (
    DEEPSEEK_PRICING_URL as DEEPSEEK_PRICING_URL,
)
from .core import (
    SYSTEM_PROMPT as SYSTEM_PROMPT,
)
from .core import (
    TOOL_OUTPUT_HEAD_CHARS as TOOL_OUTPUT_HEAD_CHARS,
)
from .core import (
    TOOL_OUTPUT_MAX_CHARS as TOOL_OUTPUT_MAX_CHARS,
)
from .core import (
    TOOL_OUTPUT_TAIL_CHARS as TOOL_OUTPUT_TAIL_CHARS,
)
from .core import (
    TOOLS as TOOLS,
)
from .core import (
    AgentConfig as AgentConfig,
)
from .core import (
    AgentResult as AgentResult,
)
from .core import (
    ToolObservation as ToolObservation,
)
from .core import (
    TrajectoryWriter as TrajectoryWriter,
)
from .core import (
    create_deepseek_client as create_deepseek_client,
)
from .core import (
    format_tool_observation as format_tool_observation,
)
from .core import (
    parse_tool_arguments as parse_tool_arguments,
)
from .core import (
    request_chat_completion as request_chat_completion,
)
from .env import DockerEnv
from .harness.loop import run_agent


class RepoFixAgent:
    def __init__(
        self,
        env: DockerEnv,
        issue: str,
        trajectory_path: Path,
        api_key: str,
        git_commit: str,
        config: AgentConfig | None = None,
        client: Any | None = None,
    ) -> None:
        self.env = env
        self.issue = issue
        self.git_commit = git_commit
        self.config = config or AgentConfig()
        self.client = client or create_deepseek_client(api_key, self.config)
        self.trace = TrajectoryWriter(trajectory_path, secrets=[api_key])
        self.python_hooks = {"PreToolUse": [], "PostToolUse": [], "PreSubmit": []}

    def register_hook(self, event: str, hook) -> None:
        """Register a process-local Python hook before starting a V3 run."""
        if not getattr(self.config, "hooks_enabled", False):
            raise ValueError("Python hooks require v3 with hooks_enabled")
        if event not in self.python_hooks or not callable(hook):
            raise ValueError("expected a callable PreToolUse/PostToolUse/PreSubmit hook")
        self.python_hooks[event].append(hook)

    @staticmethod
    def _usage_value(usage: Any, name: str) -> int | None:
        if usage is None:
            return None
        value = getattr(usage, name, None)
        if value is None and hasattr(usage, "model_dump"):
            value = usage.model_dump().get(name)
        return int(value) if value is not None else None

    def run(self) -> AgentResult:
        return run_agent(self)

    @staticmethod
    def create_child(env, issue, trajectory_path, git_commit, config, client):
        return RepoFixAgent(env, issue, trajectory_path, "", git_commit, config, client)
