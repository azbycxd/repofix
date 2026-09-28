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


DEEPSEEK_PRICING_URL = "https://api-docs.deepseek.com/quick_start/pricing/"

SYSTEM_PROMPT = """You are a coding agent working on one public SWE-bench issue.
The repository is at /testbed. Use bash to inspect, edit, and test the repository.
The container has no network access. Keep working until the issue is fixed, then call
submit. Do not merely describe a patch and do not ask the user questions."""

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
    max_steps: int = 50
    max_cost_usd: float = 0.5
    tool_timeout_seconds: int = 60
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
    prompt_tokens: int
    cache_hit_tokens: int | None
    completion_tokens: int
    max_estimated_cost_usd: float
    wall_time_seconds: float
    trajectory_path: str


def create_deepseek_client(api_key: str, config: AgentConfig) -> OpenAI:
    return OpenAI(
        api_key=api_key,
        base_url=config.base_url,
        timeout=config.provider_timeout_seconds,
        max_retries=0,
    )


def request_chat_completion(
    client: Any, messages: list[dict[str, Any]], config: AgentConfig
) -> Any:
    return client.chat.completions.create(
        model=config.model,
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
        config: AgentConfig | None = None,
        client: Any | None = None,
    ) -> None:
        self.env = env
        self.issue = issue
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
                "config": asdict(self.config),
                "system_prompt": SYSTEM_PROMPT,
                "problem_statement": self.issue,
            }
        )

        provider_calls = 0
        tool_call_count = 0
        prompt_tokens = 0
        completion_tokens = 0
        cache_hit_total = 0
        cache_hit_available = True
        max_cost = 0.0
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
                    arguments_text = tool_call.function.arguments or "{}"
                    arguments, argument_error = parse_tool_arguments(arguments_text)
                    if argument_error is not None:
                        observation = argument_error
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
                                fatal_runtime_error = True
                                status = "fatal_runtime_error"
                            else:
                                observation = result.output
                                if result.timed_out:
                                    observation += "\n[command timed out]"
                                else:
                                    observation += f"\n[exit_code={result.exit_code}]"
                        elif name == "submit":
                            observation = "Submission accepted."
                            submitted = True
                            status = "submitted"
                        else:
                            observation = f"Unknown or invalid tool call: {name}"

                    observations.append(
                        {
                            "tool_call_id": tool_call.id,
                            "name": name,
                            "content": observation,
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
        result = AgentResult(
            status=status,
            submitted=submitted,
            patch=patch,
            steps=provider_calls,
            provider_calls=provider_calls,
            tool_calls=tool_call_count,
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
                **{key: value for key, value in asdict(result).items() if key != "patch"},
                "patch_nonempty": bool(patch.strip()),
                "patch": patch,
            }
        )
        return result
