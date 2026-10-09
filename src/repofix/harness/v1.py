"""Frozen compatibility loop with ordinary local variables, golden tested."""

import time
from dataclasses import asdict
from importlib import import_module
from typing import Any

from repofix.core import (
    SYSTEM_PROMPT,
    AgentResult,
    format_tool_observation,
    parse_tool_arguments,
    request_chat_completion,
)
from repofix.reproduction import ReproductionTelemetry, usable_validation_evidence
from repofix.reviewer import review_patch
from repofix.search import SEARCH_DEFAULT_TOP_K, SEARCH_MAX_TOP_K, BM25Index, format_search_results


class V1Loop:
    def __init__(self, agent):
        self.env = agent.env
        self.issue = agent.issue
        self.git_commit = agent.git_commit
        self.config = agent.config
        self.client = agent.client
        self.trace = agent.trace
        self.usage_value = agent._usage_value

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
            dense = import_module("repofix.retrieval")

            code_index, hybrid_stats = dense.HybridCodeIndex.from_repository(self.env)
            index_metadata = {
                "mode": "dense_rrf",
                **asdict(hybrid_stats),
            }
            search_formatter = dense.format_fused_results
            index_file_count = hybrid_stats.file_count
            index_chunk_count = hybrid_stats.chunk_count
            index_build_seconds = hybrid_stats.total_build_seconds
            dense_build_seconds = hybrid_stats.dense_build_seconds
            dense_cache_hit = hybrid_stats.dense_cache_hit
            dense_cache_hit_count = hybrid_stats.dense_cache_hit_count
            dense_embedded_count = hybrid_stats.dense_embedded_count
        else:
            raise ValueError(f"unsupported retrieval mode: {self.config.retrieval_mode}")
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
                response = request_chat_completion(self.client, messages, self.config)
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
            call_prompt = self.usage_value(usage, "prompt_tokens") or 0
            call_completion = self.usage_value(usage, "completion_tokens") or 0
            call_cache_hit = self.usage_value(usage, "prompt_cache_hit_tokens")
            if call_cache_hit is None:
                cache_hit_available = False
                call_cache_hit_for_cost = 0
            else:
                cache_hit_total += call_cache_hit
                call_cache_hit_for_cost = call_cache_hit
            call_cache_miss = self.usage_value(usage, "prompt_cache_miss_tokens")
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
                                    behavior.observe_changes(step, self.env.get_tracked_changes())
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
                                        result.output + f"\n[exit_code={result.exit_code}]"
                                    )
                                if result.timed_out:
                                    status_suffix = "\n[command timed out]"
                                else:
                                    status_suffix = f"\n[exit_code={result.exit_code}]"
                                full_output_path = None
                                if len(result.output) > self.config.tool_output_max_chars:
                                    full_output_path = f"/tmp/repofix_out_{tool_call_count}.txt"
                                    try:
                                        self.env.write_text_file(full_output_path, result.output)
                                    except Exception as exc:
                                        observation = (
                                            "Fatal runtime error while preserving "
                                            f"full tool output: {exc}"
                                        )
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
                                    view_output = self.env.view_file(path, start_line, end_line)
                                    full_output_path = None
                                    if len(view_output) > self.config.tool_output_max_chars:
                                        full_output_path = f"/tmp/repofix_out_{tool_call_count}.txt"
                                        self.env.write_text_file(full_output_path, view_output)
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
                                        "original_lines": len(observation.splitlines()),
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
                                isinstance(value, str) for value in (path, old_str, new_str)
                            ):
                                observation = (
                                    "str_replace error: path, old_str, and new_str must be strings"
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
                                    edit = self.env.str_replace_file(path, old_str, new_str)
                                    try:
                                        behavior.observe_changes(
                                            step, self.env.get_tracked_changes()
                                        )
                                    except Exception:
                                        behavior.telemetry_errors += 1
                                    if not edit.success:
                                        str_replace_failure_count += 1
                                    syntax_rollback_count += int(edit.syntax_rollback)
                                    full_output_path = None
                                    if len(edit.output) > self.config.tool_output_max_chars:
                                        full_output_path = f"/tmp/repofix_out_{tool_call_count}.txt"
                                        self.env.write_text_file(full_output_path, edit.output)
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
                                        "original_lines": len(observation.splitlines()),
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
                                    if len(search_output) > self.config.tool_output_max_chars:
                                        full_output_path = f"/tmp/repofix_out_{tool_call_count}.txt"
                                        self.env.write_text_file(full_output_path, search_output)
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
                                        "original_lines": len(observation.splitlines()),
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
                                    reviewer_cache_hit_tokens = review.cache_hit_tokens
                                    reviewer_completion_tokens = review.completion_tokens
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
                                                "last_validation_output": (last_validation_output),
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
        patch_changed_after_reject = bool(reviewer_returned and patch != reviewer_initial_patch)
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
