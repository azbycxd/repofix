"""A minimal native tool-calling coding-agent loop."""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI

from .env import DockerEnv
from .reproduction import ReproductionTelemetry, usable_validation_evidence
from .reviewer import review_patch
from .search import (
    BM25Index,
    SEARCH_DEFAULT_TOP_K,
    SEARCH_MAX_TOP_K,
    format_search_results,
)


DEEPSEEK_PRICING_URL = "https://api-docs.deepseek.com/quick_start/pricing/"
TOOL_OUTPUT_MAX_CHARS = 12_000
TOOL_OUTPUT_HEAD_CHARS = 6_000
TOOL_OUTPUT_TAIL_CHARS = 6_000

SYSTEM_PROMPT = """You are a coding agent working on one public SWE-bench issue.
The repository is at /testbed. Use bash to inspect, edit, and test the repository.
The container has no network access. Keep working until the issue is fixed, then call
submit. Do not merely describe a patch and do not ask the user questions.

Before modifying implementation code, first create the smallest executable
reproduction you can and observe the issue fail. Prefer a temporary script such as
/tmp/repofix_repro.py or a focused existing test command. After the implementation
change, run the same reproduction command again and confirm it passes. Keep temporary
reproduction files outside the final patch. Do not search the network or Git history
for an official fix. Do not modify existing tests merely to make your implementation
pass."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a bash command in /testbed and return its complete output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to run in /testbed.",
                    }
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view",
            "description": "Read a line range from a UTF-8 file in /testbed with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                },
                "required": ["path", "start_line", "end_line"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "str_replace",
            "description": (
                "Replace exactly one occurrence in a UTF-8 file in /testbed. "
                "The edit is rolled back if python -m py_compile fails."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_str": {"type": "string"},
                    "new_str": {"type": "string"},
                },
                "required": ["path", "old_str", "new_str"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": (
                "Search the current /testbed repository with BM25 and return "
                "ranked file, symbol, line, and score metadata."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": SEARCH_MAX_TOP_K,
                        "default": SEARCH_DEFAULT_TOP_K,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit",
            "description": "Submit the current repository changes as the final answer.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
]


@dataclass(frozen=True)
class AgentConfig:
    model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com"
    thinking: str = "disabled"
    temperature: float = 0.0
    retrieval_mode: str = "bm25"
    reviewer_enabled: bool = False
    max_steps: int = 50
    max_cost_usd: float = 0.5
    tool_timeout_seconds: int = 60
    tool_output_max_chars: int = TOOL_OUTPUT_MAX_CHARS
    tool_output_head_chars: int = TOOL_OUTPUT_HEAD_CHARS
    tool_output_tail_chars: int = TOOL_OUTPUT_TAIL_CHARS
    provider_timeout_seconds: int = 300
    max_completion_tokens: int = 8192
    # Conservative peak prices in USD per 1M tokens, fetched 2026-09-28.
    cache_hit_input_price: float = 0.006
    cache_miss_input_price: float = 0.30
    output_price: float = 1.20
    pricing_source: str = DEEPSEEK_PRICING_URL


@dataclass(frozen=True)
class AgentResult:
    status: str
    submitted: bool
    patch: str
    steps: int
    provider_calls: int
    tool_calls: int
    view_calls: int
    str_replace_calls: int
    str_replace_failures: int
    syntax_rollbacks: int
    search_calls: int
    truncations: int
    index_file_count: int
    index_chunk_count: int
    index_build_seconds: float
    dense_build_seconds: float
    dense_cache_hit: bool
    dense_cache_hit_count: int
    dense_embedded_count: int
    pre_fix_reproduced: bool
    post_fix_repro_passed: bool
    repro_flipped: bool
    first_production_edit_step: int | None
    existing_test_modified: bool
    git_history_search_count: int
    network_attempt_count: int
    reviewer_verdict: str | None
    reviewer_reject_reason: str | None
    reviewer_returned: bool
    patch_changed_after_reject: bool
    reviewer_calls: int
    reviewer_prompt_tokens: int
    reviewer_cache_hit_tokens: int | None
    reviewer_completion_tokens: int
    reviewer_cost_usd: float
    reviewer_latency_seconds: float
    reviewer_initial_patch: str
    prompt_tokens: int
    cache_hit_tokens: int | None
    completion_tokens: int
    max_estimated_cost_usd: float
    wall_time_seconds: float
    trajectory_path: str


@dataclass(frozen=True)
class ToolObservation:
    content: str
    truncated: bool
    original_chars: int
    original_lines: int
    returned_chars: int
    full_output_path: str | None


def create_deepseek_client(api_key: str, config: AgentConfig) -> OpenAI:
    return OpenAI(
        api_key=api_key,
        base_url=config.base_url,
        timeout=config.provider_timeout_seconds,
        max_retries=2,
    )


def request_chat_completion(
    client: Any, messages: list[dict[str, Any]], config: AgentConfig
) -> Any:
    return client.chat.completions.create(
        model=config.model,
        temperature=config.temperature,
        messages=messages,
        tools=TOOLS,
        reasoning_effort="none",
        extra_body={"thinking": {"type": "disabled"}},
        max_completion_tokens=config.max_completion_tokens,
        timeout=config.provider_timeout_seconds,
    )


def parse_tool_arguments(arguments_text: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        arguments = json.loads(arguments_text)
    except json.JSONDecodeError as exc:
        return None, f"Invalid tool arguments: malformed JSON ({exc})"
    if not isinstance(arguments, dict):
        return None, (
            "Invalid tool arguments: expected a JSON object, "
            f"got {type(arguments).__name__}"
        )
    return arguments, None


def format_tool_observation(
    output: str,
    status_suffix: str,
    full_output_path: str | None,
    max_chars: int = TOOL_OUTPUT_MAX_CHARS,
    head_chars: int = TOOL_OUTPUT_HEAD_CHARS,
    tail_chars: int = TOOL_OUTPUT_TAIL_CHARS,
) -> ToolObservation:
    """Return an unchanged short observation or a clearly marked head/tail view."""
    if min(max_chars, head_chars, tail_chars) <= 0:
        raise ValueError("tool output limits must be positive")
    if head_chars + tail_chars > max_chars:
        raise ValueError("tool output head and tail exceed the maximum")

    original_chars = len(output)
    original_lines = len(output.splitlines())
    if original_chars <= max_chars:
        content = output + status_suffix
        return ToolObservation(
            content=content,
            truncated=False,
            original_chars=original_chars,
            original_lines=original_lines,
            returned_chars=len(content),
            full_output_path=None,
        )

    if not full_output_path:
        raise ValueError("a full output path is required for truncated output")
    content = (
        "[RepoFix: tool output truncated]\n"
        f"Original output: {original_chars} characters, {original_lines} lines.\n"
        f"Showing first {head_chars} and last {tail_chars} characters.\n"
        f"Full output saved at {full_output_path}; use bash to inspect it.\n"
        "--- HEAD ---\n"
        f"{output[:head_chars]}\n"
        "--- TAIL ---\n"
        f"{output[-tail_chars:]}"
        f"{status_suffix}"
    )
    return ToolObservation(
        content=content,
        truncated=True,
        original_chars=original_chars,
        original_lines=original_lines,
        returned_chars=len(content),
        full_output_path=full_output_path,
    )


class TrajectoryWriter:
    def __init__(self, path: Path, secrets: list[str] | None = None) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.secrets = [value for value in (secrets or []) if value]

    def _redact_text(self, value: str) -> str:
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(
            r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+", r"\1[REDACTED]", value
        )
        value = re.sub(
            r"(?i)((?:api[_-]?key|secret)[\"']?\s*[:=]\s*[\"'])[^\"']+",
            r"\1[REDACTED]",
            value,
        )
        return value

    def _redact(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._redact_text(value)
        if isinstance(value, dict):
            return {key: self._redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        return value

    def write(self, record: dict[str, Any]) -> None:
        safe = self._redact(record)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(safe, ensure_ascii=False) + "\n")


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

    @staticmethod
    def _usage_value(usage: Any, name: str) -> int | None:
        if usage is None:
            return None
        value = getattr(usage, name, None)
        if value is None and hasattr(usage, "model_dump"):
            value = usage.model_dump().get(name)
        return int(value) if value is not None else None

    def run(self) -> AgentResult:
        from .harness.loop import run_agent
        return run_agent(self)
