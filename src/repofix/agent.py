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
        started = time.monotonic()
        if self.config.retrieval_mode == "bm25":
            code_index, bm25_stats = BM25Index.from_repository(self.env)
            index_metadata = {
                "mode": "bm25",
                **asdict(bm25_stats),
            }
            search_formatter = format_search_results
            index_file_count = bm25_stats.file_count
            index_chunk_count = bm25_stats.chunk_count
            index_build_seconds = bm25_stats.build_seconds
            dense_build_seconds = 0.0
            dense_cache_hit = False
            dense_cache_hit_count = 0
            dense_embedded_count = 0
        elif self.config.retrieval_mode == "dense_rrf":
            from .retrieval import HybridCodeIndex, format_fused_results

            code_index, hybrid_stats = HybridCodeIndex.from_repository(self.env)
            index_metadata = {
                "mode": "dense_rrf",
                **asdict(hybrid_stats),
            }
            search_formatter = format_fused_results
            index_file_count = hybrid_stats.file_count
            index_chunk_count = hybrid_stats.chunk_count
            index_build_seconds = hybrid_stats.total_build_seconds
            dense_build_seconds = hybrid_stats.dense_build_seconds
            dense_cache_hit = hybrid_stats.dense_cache_hit
            dense_cache_hit_count = hybrid_stats.dense_cache_hit_count
            dense_embedded_count = hybrid_stats.dense_embedded_count
        else:
            raise ValueError(
                f"unsupported retrieval mode: {self.config.retrieval_mode}"
            )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Fix this issue in /testbed:\n\n{self.issue}",
            },
        ]
        self.trace.write(
            {
                "type": "config",
                "step": 0,
                "model": self.config.model,
                "git_commit": self.git_commit,
                "config": asdict(self.config),
                "code_index": index_metadata,
                "system_prompt": SYSTEM_PROMPT,
                "problem_statement": self.issue,
            }
        )

        provider_calls = 0
        tool_call_count = 0
        view_call_count = 0
        str_replace_call_count = 0
        str_replace_failure_count = 0
        syntax_rollback_count = 0
        search_call_count = 0
        truncation_count = 0
        prompt_tokens = 0
        completion_tokens = 0
        cache_hit_total = 0
        cache_hit_available = True
        max_cost = 0.0
        behavior = ReproductionTelemetry()
        last_validation_command: str | None = None
        last_validation_output: str | None = None
        reviewer_verdict: str | None = None
        reviewer_reject_reason: str | None = None
        reviewer_returned = False
        reviewer_calls = 0
        reviewer_prompt_tokens = 0
        reviewer_cache_hit_tokens: int | None = None
        reviewer_completion_tokens = 0
        reviewer_cost = 0.0
        reviewer_latency = 0.0
        reviewer_initial_patch = ""
        submitted = False
        status = "max_steps"
        no_tool_nudge_used = False

        for step in range(1, self.config.max_steps + 1):
            call_started = time.monotonic()
            try:
                response = request_chat_completion(
                    self.client, messages, self.config
                )
            except Exception as exc:
                self.trace.write(
                    {
                        "type": "fatal_error",
                        "step": step,
                        "model": self.config.model,
                        "error": str(exc),
                        "latency_seconds": time.monotonic() - call_started,
                    }
                )
                status = "fatal_provider_error"
                break

            provider_calls += 1
            choice = response.choices[0]
            message = choice.message
            usage = response.usage
            call_prompt = self._usage_value(usage, "prompt_tokens") or 0
            call_completion = self._usage_value(usage, "completion_tokens") or 0
            call_cache_hit = self._usage_value(usage, "prompt_cache_hit_tokens")
            if call_cache_hit is None:
                cache_hit_available = False
                call_cache_hit_for_cost = 0
            else:
                cache_hit_total += call_cache_hit
                call_cache_hit_for_cost = call_cache_hit
            call_cache_miss = self._usage_value(usage, "prompt_cache_miss_tokens")
            if call_cache_miss is None:
                call_cache_miss = max(0, call_prompt - call_cache_hit_for_cost)

            prompt_tokens += call_prompt
            completion_tokens += call_completion
            max_cost += (
                call_cache_hit_for_cost * self.config.cache_hit_input_price
                + call_cache_miss * self.config.cache_miss_input_price
                + call_completion * self.config.output_price
            ) / 1_000_000

            raw_tool_calls = message.tool_calls or []
            calls_for_trace = [
                {
                    "id": tool_call.id,
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments or "{}",
                }
                for tool_call in raw_tool_calls
            ]
            assistant_payload: dict[str, Any] = {
                "role": "assistant",
                "content": message.content,
            }
            if calls_for_trace:
                assistant_payload["tool_calls"] = [
                    {
                        "id": item["id"],
                        "type": "function",
                        "function": {
                            "name": item["name"],
                            "arguments": item["arguments"],
                        },
                    }
                    for item in calls_for_trace
                ]
            messages.append(assistant_payload)
            observations: list[dict[str, Any]] = []
            fatal_runtime_error = False

            if max_cost >= self.config.max_cost_usd:
                status = "cost_limit"
            else:
                for tool_call in raw_tool_calls:
                    tool_call_count += 1
                    name = tool_call.function.name
                    if name == "view":
                        view_call_count += 1
                    elif name == "str_replace":
                        str_replace_call_count += 1
                    elif name == "search_code":
                        search_call_count += 1
                    arguments_text = tool_call.function.arguments or "{}"
                    arguments, argument_error = parse_tool_arguments(arguments_text)
                    observation_metadata: dict[str, Any]
                    if argument_error is not None:
                        observation = argument_error
                        if name == "str_replace":
                            str_replace_failure_count += 1
                        observation_metadata = {
                            "truncated": False,
                            "original_chars": len(observation),
                            "original_lines": len(observation.splitlines()),
                            "returned_chars": len(observation),
                            "full_output_path": None,
                        }
                    else:
                        assert arguments is not None
                        if name == "bash" and isinstance(arguments.get("command"), str):
                            try:
                                result = self.env.execute(
                                    arguments["command"],
                                    self.config.tool_timeout_seconds,
                                )
                            except Exception as exc:
                                observation = f"Fatal runtime error: {exc}"
                                observation_metadata = {
                                    "truncated": False,
                                    "original_chars": len(observation),
                                    "original_lines": len(observation.splitlines()),
                                    "returned_chars": len(observation),
                                    "full_output_path": None,
                                }
                                fatal_runtime_error = True
                                status = "fatal_runtime_error"
                            else:
                                try:
                                    behavior.observe_changes(
                                        step, self.env.get_tracked_changes()
                                    )
                                except Exception:
                                    behavior.telemetry_errors += 1
                                behavior.observe_shell(
                                    step,
                                    arguments["command"],
                                    result.exit_code,
                                    result.output,
                                )
                                if usable_validation_evidence(
                                    arguments["command"],
                                    result.output,
                                    result.timed_out,
                                ):
                                    last_validation_command = arguments["command"]
                                    last_validation_output = (
                                        result.output
                                        + f"\n[exit_code={result.exit_code}]"
                                    )
                                if result.timed_out:
                                    status_suffix = "\n[command timed out]"
                                else:
                                    status_suffix = f"\n[exit_code={result.exit_code}]"
                                full_output_path = None
                                if (
                                    len(result.output)
                                    > self.config.tool_output_max_chars
                                ):
                                    full_output_path = (
                                        f"/tmp/repofix_out_{tool_call_count}.txt"
                                    )
                                    try:
                                        self.env.write_text_file(
                                            full_output_path, result.output
                                        )
                                    except Exception as exc:
                                        observation = (
                                            "Fatal runtime error while preserving "
                                            f"full tool output: {exc}"
                                        )
                                        observation_metadata = {
                                            "truncated": False,
                                            "original_chars": len(observation),
                                            "original_lines": len(
                                                observation.splitlines()
                                            ),
                                            "returned_chars": len(observation),
                                            "full_output_path": None,
                                        }
                                        fatal_runtime_error = True
                                        status = "fatal_runtime_error"
                                    else:
                                        view = format_tool_observation(
                                            result.output,
                                            status_suffix,
                                            full_output_path,
                                            self.config.tool_output_max_chars,
                                            self.config.tool_output_head_chars,
                                            self.config.tool_output_tail_chars,
                                        )
                                        observation = view.content
                                        observation_metadata = {
                                            key: value
                                            for key, value in asdict(view).items()
                                            if key != "content"
                                        }
                                        truncation_count += 1
                                else:
                                    view = format_tool_observation(
                                        result.output,
                                        status_suffix,
                                        None,
                                        self.config.tool_output_max_chars,
                                        self.config.tool_output_head_chars,
                                        self.config.tool_output_tail_chars,
                                    )
                                    observation = view.content
                                    observation_metadata = {
                                        key: value
                                        for key, value in asdict(view).items()
                                        if key != "content"
                                    }
                        elif name == "view":
                            path = arguments.get("path")
                            start_line = arguments.get("start_line")
                            end_line = arguments.get("end_line")
                            if not (
                                isinstance(path, str)
                                and isinstance(start_line, int)
                                and not isinstance(start_line, bool)
                                and isinstance(end_line, int)
                                and not isinstance(end_line, bool)
                            ):
                                observation = (
                                    "view error: path must be a string and line "
                                    "bounds must be integers"
                                )
                                observation_metadata = {
                                    "truncated": False,
                                    "original_chars": len(observation),
                                    "original_lines": 1,
                                    "returned_chars": len(observation),
                                    "full_output_path": None,
                                }
                            else:
                                try:
                                    view_output = self.env.view_file(
                                        path, start_line, end_line
                                    )
                                    full_output_path = None
                                    if (
                                        len(view_output)
                                        > self.config.tool_output_max_chars
                                    ):
                                        full_output_path = (
                                            f"/tmp/repofix_out_{tool_call_count}.txt"
                                        )
                                        self.env.write_text_file(
                                            full_output_path, view_output
                                        )
                                    view = format_tool_observation(
                                        view_output,
                                        "",
                                        full_output_path,
                                        self.config.tool_output_max_chars,
                                        self.config.tool_output_head_chars,
                                        self.config.tool_output_tail_chars,
                                    )
                                except Exception as exc:
                                    observation = f"view error: {exc}"
                                    observation_metadata = {
                                        "truncated": False,
                                        "original_chars": len(observation),
                                        "original_lines": len(
                                            observation.splitlines()
                                        ),
                                        "returned_chars": len(observation),
                                        "full_output_path": None,
                                    }
                                else:
                                    observation = view.content
                                    observation_metadata = {
                                        key: value
                                        for key, value in asdict(view).items()
                                        if key != "content"
                                    }
                                    truncation_count += int(view.truncated)
                        elif name == "str_replace":
                            path = arguments.get("path")
                            old_str = arguments.get("old_str")
                            new_str = arguments.get("new_str")
                            if not all(
                                isinstance(value, str)
                                for value in (path, old_str, new_str)
                            ):
                                observation = (
                                    "str_replace error: path, old_str, and new_str "
                                    "must be strings"
                                )
                                str_replace_failure_count += 1
                                observation_metadata = {
                                    "truncated": False,
                                    "original_chars": len(observation),
                                    "original_lines": 1,
                                    "returned_chars": len(observation),
                                    "full_output_path": None,
                                }
                            else:
                                try:
                                    edit = self.env.str_replace_file(
                                        path, old_str, new_str
                                    )
                                    try:
                                        behavior.observe_changes(
                                            step, self.env.get_tracked_changes()
                                        )
                                    except Exception:
                                        behavior.telemetry_errors += 1
                                    if not edit.success:
                                        str_replace_failure_count += 1
                                    syntax_rollback_count += int(
                                        edit.syntax_rollback
                                    )
                                    full_output_path = None
                                    if (
                                        len(edit.output)
                                        > self.config.tool_output_max_chars
                                    ):
                                        full_output_path = (
                                            f"/tmp/repofix_out_{tool_call_count}.txt"
                                        )
                                        self.env.write_text_file(
                                            full_output_path, edit.output
                                        )
                                    view = format_tool_observation(
                                        edit.output,
                                        "",
                                        full_output_path,
                                        self.config.tool_output_max_chars,
                                        self.config.tool_output_head_chars,
                                        self.config.tool_output_tail_chars,
                                    )
                                except Exception as exc:
                                    str_replace_failure_count += 1
                                    observation = f"str_replace error: {exc}"
                                    observation_metadata = {
                                        "truncated": False,
                                        "original_chars": len(observation),
                                        "original_lines": len(
                                            observation.splitlines()
                                        ),
                                        "returned_chars": len(observation),
                                        "full_output_path": None,
                                    }
                                else:
                                    observation = view.content
                                    observation_metadata = {
                                        key: value
                                        for key, value in asdict(view).items()
                                        if key != "content"
                                    }
                                    truncation_count += int(view.truncated)
                        elif name == "search_code":
                            query = arguments.get("query")
                            top_k = arguments.get("top_k", SEARCH_DEFAULT_TOP_K)
                            if not (
                                isinstance(query, str)
                                and query.strip()
                                and isinstance(top_k, int)
                                and not isinstance(top_k, bool)
                                and 1 <= top_k <= SEARCH_MAX_TOP_K
                            ):
                                observation = (
                                    "search_code error: query must be a non-empty "
                                    f"string and top_k must be an integer from 1 to {SEARCH_MAX_TOP_K}"
                                )
                                observation_metadata = {
                                    "truncated": False,
                                    "original_chars": len(observation),
                                    "original_lines": 1,
                                    "returned_chars": len(observation),
                                    "full_output_path": None,
                                }
                            else:
                                try:
                                    search_output = search_formatter(
                                        code_index.search(query, top_k=top_k)
                                    )
                                    full_output_path = None
                                    if (
                                        len(search_output)
                                        > self.config.tool_output_max_chars
                                    ):
                                        full_output_path = (
                                            f"/tmp/repofix_out_{tool_call_count}.txt"
                                        )
                                        self.env.write_text_file(
                                            full_output_path, search_output
                                        )
                                    view = format_tool_observation(
                                        search_output,
                                        "",
                                        full_output_path,
                                        self.config.tool_output_max_chars,
                                        self.config.tool_output_head_chars,
                                        self.config.tool_output_tail_chars,
                                    )
                                except Exception as exc:
                                    observation = f"search_code error: {exc}"
                                    observation_metadata = {
                                        "truncated": False,
                                        "original_chars": len(observation),
                                        "original_lines": len(
                                            observation.splitlines()
                                        ),
                                        "returned_chars": len(observation),
                                        "full_output_path": None,
                                    }
                                else:
                                    observation = view.content
                                    observation_metadata = {
                                        key: value
                                        for key, value in asdict(view).items()
                                        if key != "content"
                                    }
                                    truncation_count += int(view.truncated)
                        elif name == "submit":
                            if self.config.reviewer_enabled and reviewer_calls == 0:
                                reviewer_calls = 1
                                try:
                                    reviewer_initial_patch = self.env.get_diff()
                                    review = review_patch(
                                        self.client,
                                        self.config,
                                        self.issue,
                                        reviewer_initial_patch,
                                        last_validation_command,
                                        last_validation_output,
                                    )
                                except Exception as exc:
                                    reviewer_verdict = "ERROR"
                                    reviewer_reject_reason = str(exc)
                                    observation = (
                                        "Reviewer unavailable; submission accepted "
                                        "without a verdict."
                                    )
                                    submitted = True
                                    status = "submitted"
                                    self.trace.write(
                                        {
                                            "type": "reviewer",
                                            "step": step,
                                            "verdict": reviewer_verdict,
                                            "error": str(exc),
                                            "input_fields": [
                                                "issue",
                                                "full_diff",
                                                "last_validation_command",
                                                "last_validation_output",
                                            ],
                                        }
                                    )
                                else:
                                    reviewer_verdict = review.verdict
                                    reviewer_reject_reason = review.reject_reason
                                    reviewer_prompt_tokens = review.prompt_tokens
                                    reviewer_cache_hit_tokens = (
                                        review.cache_hit_tokens
                                    )
                                    reviewer_completion_tokens = (
                                        review.completion_tokens
                                    )
                                    reviewer_cost = review.estimated_cost_usd
                                    reviewer_latency = review.latency_seconds
                                    self.trace.write(
                                        {
                                            "type": "reviewer",
                                            "step": step,
                                            "model": self.config.model,
                                            "verdict": review.verdict,
                                            "reject_reason": review.reject_reason,
                                            "raw_output": review.raw_output,
                                            "finish_reason": review.finish_reason,
                                            "prompt_tokens": review.prompt_tokens,
                                            "cache_hit_tokens": review.cache_hit_tokens,
                                            "completion_tokens": review.completion_tokens,
                                            "estimated_cost_usd": review.estimated_cost_usd,
                                            "latency_seconds": review.latency_seconds,
                                            "input": {
                                                "issue": self.issue,
                                                "full_diff": reviewer_initial_patch,
                                                "last_validation_command": (
                                                    last_validation_command
                                                ),
                                                "last_validation_output": (
                                                    last_validation_output
                                                ),
                                            },
                                        }
                                    )
                                    if review.verdict == "REJECT":
                                        reviewer_returned = True
                                        observation = (
                                            "Reviewer rejected the first submission: "
                                            f"{review.reject_reason}\n"
                                            "Address this reason, then call submit again."
                                        )
                                    else:
                                        observation = "Submission accepted."
                                        submitted = True
                                        status = "submitted"
                            else:
                                observation = "Submission accepted."
                                submitted = True
                                status = "submitted"
                            observation_metadata = {
                                "truncated": False,
                                "original_chars": len(observation),
                                "original_lines": 1,
                                "returned_chars": len(observation),
                                "full_output_path": None,
                            }
                        else:
                            observation = f"Unknown or invalid tool call: {name}"
                            observation_metadata = {
                                "truncated": False,
                                "original_chars": len(observation),
                                "original_lines": len(observation.splitlines()),
                                "returned_chars": len(observation),
                                "full_output_path": None,
                            }

                    observations.append(
                        {
                            "tool_call_id": tool_call.id,
                            "name": name,
                            "content": observation,
                            **observation_metadata,
                        }
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": observation,
                        }
                    )
                    if submitted or fatal_runtime_error:
                        break

            self.trace.write(
                {
                    "type": "step",
                    "step": step,
                    "model": self.config.model,
                    "config": {"thinking": self.config.thinking},
                    "prompt_tokens": call_prompt,
                    "cache_hit_tokens": call_cache_hit,
                    "completion_tokens": call_completion,
                    "tool_calls": calls_for_trace,
                    "observation": observations,
                    "assistant_content": message.content,
                    "finish_reason": choice.finish_reason,
                    "latency_seconds": time.monotonic() - call_started,
                    "max_estimated_cost_usd": max_cost,
                    "behavior": behavior.metrics(),
                }
            )

            if status in {"submitted", "cost_limit", "fatal_runtime_error"}:
                break
            if not raw_tool_calls:
                if no_tool_nudge_used:
                    status = "no_tool_call"
                    break
                messages.append(
                    {
                        "role": "user",
                        "content": "Continue by calling bash to work on the issue, or submit when done.",
                    }
                )
                no_tool_nudge_used = True

        try:
            patch = self.env.get_diff()
        except Exception as exc:
            patch = ""
            status = "fatal_runtime_error"
            self.trace.write(
                {
                    "type": "fatal_error",
                    "step": provider_calls,
                    "model": self.config.model,
                    "error": str(exc),
                }
            )
        wall_time = time.monotonic() - started
        patch_changed_after_reject = bool(
            reviewer_returned and patch != reviewer_initial_patch
        )
        result = AgentResult(
            status=status,
            submitted=submitted,
            patch=patch,
            steps=provider_calls,
            provider_calls=provider_calls,
            tool_calls=tool_call_count,
            view_calls=view_call_count,
            str_replace_calls=str_replace_call_count,
            str_replace_failures=str_replace_failure_count,
            syntax_rollbacks=syntax_rollback_count,
            search_calls=search_call_count,
            truncations=truncation_count,
            index_file_count=index_file_count,
            index_chunk_count=index_chunk_count,
            index_build_seconds=index_build_seconds,
            dense_build_seconds=dense_build_seconds,
            dense_cache_hit=dense_cache_hit,
            dense_cache_hit_count=dense_cache_hit_count,
            dense_embedded_count=dense_embedded_count,
            pre_fix_reproduced=behavior.pre_fix_reproduced,
            post_fix_repro_passed=behavior.post_fix_repro_passed,
            repro_flipped=behavior.repro_flipped,
            first_production_edit_step=behavior.first_production_edit_step,
            existing_test_modified=behavior.existing_test_modified,
            git_history_search_count=behavior.git_history_search_count,
            network_attempt_count=behavior.network_attempt_count,
            reviewer_verdict=reviewer_verdict,
            reviewer_reject_reason=reviewer_reject_reason,
            reviewer_returned=reviewer_returned,
            patch_changed_after_reject=patch_changed_after_reject,
            reviewer_calls=reviewer_calls,
            reviewer_prompt_tokens=reviewer_prompt_tokens,
            reviewer_cache_hit_tokens=reviewer_cache_hit_tokens,
            reviewer_completion_tokens=reviewer_completion_tokens,
            reviewer_cost_usd=reviewer_cost,
            reviewer_latency_seconds=reviewer_latency,
            reviewer_initial_patch=reviewer_initial_patch,
            prompt_tokens=prompt_tokens,
            cache_hit_tokens=cache_hit_total if cache_hit_available else None,
            completion_tokens=completion_tokens,
            max_estimated_cost_usd=max_cost,
            wall_time_seconds=wall_time,
            trajectory_path=str(self.trace.path),
        )
        self.trace.write(
            {
                "type": "summary",
                **{
                    key: value
                    for key, value in asdict(result).items()
                    if key not in {"patch", "reviewer_initial_patch"}
                },
                "patch_nonempty": bool(patch.strip()),
                "patch": patch,
                "behavior": behavior.metrics(),
            }
        )
        return result
