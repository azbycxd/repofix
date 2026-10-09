"""Extracted v1 loop; golden-tested against frozen pre-refactor requests."""
import time
from dataclasses import asdict
from repofix.agent import (AgentResult, SYSTEM_PROMPT, parse_tool_arguments,
    format_tool_observation, request_chat_completion)
from repofix.reproduction import ReproductionTelemetry, usable_validation_evidence
from repofix.reviewer import review_patch
from repofix.search import BM25Index, SEARCH_DEFAULT_TOP_K, SEARCH_MAX_TOP_K, format_search_results
from .state import LegacyState
from .tools.registry import v1_registry

class V1Loop:

    def run(self) -> AgentResult:
        s = LegacyState()
        registry = v1_registry(self._execute_tool)
        fallback = next(iter(registry.specs.values()))
        s.started = time.monotonic()
        if self.config.retrieval_mode == 'bm25':
            s.code_index, s.bm25_stats = BM25Index.from_repository(self.env)
            s.index_metadata = {'mode': 'bm25', **asdict(s.bm25_stats)}
            s.search_formatter = format_search_results
            s.index_file_count = s.bm25_stats.file_count
            s.index_chunk_count = s.bm25_stats.chunk_count
            s.index_build_seconds = s.bm25_stats.build_seconds
            s.dense_build_seconds = 0.0
            s.dense_cache_hit = False
            s.dense_cache_hit_count = 0
            s.dense_embedded_count = 0
        elif self.config.retrieval_mode == 'dense_rrf':
            from repofix.retrieval import HybridCodeIndex, format_fused_results
            s.code_index, s.hybrid_stats = HybridCodeIndex.from_repository(self.env)
            s.index_metadata = {'mode': 'dense_rrf', **asdict(s.hybrid_stats)}
            s.search_formatter = format_fused_results
            s.index_file_count = s.hybrid_stats.file_count
            s.index_chunk_count = s.hybrid_stats.chunk_count
            s.index_build_seconds = s.hybrid_stats.total_build_seconds
            s.dense_build_seconds = s.hybrid_stats.dense_build_seconds
            s.dense_cache_hit = s.hybrid_stats.dense_cache_hit
            s.dense_cache_hit_count = s.hybrid_stats.dense_cache_hit_count
            s.dense_embedded_count = s.hybrid_stats.dense_embedded_count
        else:
            raise ValueError(f'unsupported retrieval mode: {self.config.retrieval_mode}')
        s.messages: list[dict[str, Any]] = [{'role': 'system', 'content': SYSTEM_PROMPT}, {'role': 'user', 'content': f'Fix this issue in /testbed:\n\n{self.issue}'}]
        self.trace.write({'type': 'config', 'step': 0, 'model': self.config.model, 'git_commit': self.git_commit, 'config': asdict(self.config), 'code_index': s.index_metadata, 'system_prompt': SYSTEM_PROMPT, 'problem_statement': self.issue})
        s.provider_calls = 0
        s.tool_call_count = 0
        s.view_call_count = 0
        s.str_replace_call_count = 0
        s.str_replace_failure_count = 0
        s.syntax_rollback_count = 0
        s.search_call_count = 0
        s.truncation_count = 0
        s.prompt_tokens = 0
        s.completion_tokens = 0
        s.cache_hit_total = 0
        s.cache_hit_available = True
        s.max_cost = 0.0
        s.behavior = ReproductionTelemetry()
        s.last_validation_command: str | None = None
        s.last_validation_output: str | None = None
        s.reviewer_verdict: str | None = None
        s.reviewer_reject_reason: str | None = None
        s.reviewer_returned = False
        s.reviewer_calls = 0
        s.reviewer_prompt_tokens = 0
        s.reviewer_cache_hit_tokens: int | None = None
        s.reviewer_completion_tokens = 0
        s.reviewer_cost = 0.0
        s.reviewer_latency = 0.0
        s.reviewer_initial_patch = ''
        s.submitted = False
        s.status = 'max_steps'
        s.no_tool_nudge_used = False
        for s.step in range(1, self.config.max_steps + 1):
            s.call_started = time.monotonic()
            try:
                s.response = request_chat_completion(self.client, s.messages, self.config)
            except Exception as exc:
                self.trace.write({'type': 'fatal_error', 'step': s.step, 'model': self.config.model, 'error': str(exc), 'latency_seconds': time.monotonic() - s.call_started})
                s.status = 'fatal_provider_error'
                break
            s.provider_calls += 1
            s.choice = s.response.choices[0]
            s.message = s.choice.message
            s.usage = s.response.usage
            s.call_prompt = self._usage_value(s.usage, 'prompt_tokens') or 0
            s.call_completion = self._usage_value(s.usage, 'completion_tokens') or 0
            s.call_cache_hit = self._usage_value(s.usage, 'prompt_cache_hit_tokens')
            if s.call_cache_hit is None:
                s.cache_hit_available = False
                s.call_cache_hit_for_cost = 0
            else:
                s.cache_hit_total += s.call_cache_hit
                s.call_cache_hit_for_cost = s.call_cache_hit
            s.call_cache_miss = self._usage_value(s.usage, 'prompt_cache_miss_tokens')
            if s.call_cache_miss is None:
                s.call_cache_miss = max(0, s.call_prompt - s.call_cache_hit_for_cost)
            s.prompt_tokens += s.call_prompt
            s.completion_tokens += s.call_completion
            s.max_cost += (s.call_cache_hit_for_cost * self.config.cache_hit_input_price + s.call_cache_miss * self.config.cache_miss_input_price + s.call_completion * self.config.output_price) / 1000000
            s.raw_tool_calls = s.message.tool_calls or []
            s.calls_for_trace = [{'id': s.tool_call.id, 'name': s.tool_call.function.name, 'arguments': s.tool_call.function.arguments or '{}'} for s.tool_call in s.raw_tool_calls]
            s.assistant_payload: dict[str, Any] = {'role': 'assistant', 'content': s.message.content}
            if s.calls_for_trace:
                s.assistant_payload['tool_calls'] = [{'id': item['id'], 'type': 'function', 'function': {'name': item['name'], 'arguments': item['arguments']}} for item in s.calls_for_trace]
            s.messages.append(s.assistant_payload)
            s.observations: list[dict[str, Any]] = []
            s.fatal_runtime_error = False
            if s.max_cost >= self.config.max_cost_usd:
                s.status = 'cost_limit'
            else:
                for s.tool_call in s.raw_tool_calls:
                    registry.specs.get(s.tool_call.function.name, fallback).handler(s)
                    if s.submitted or s.fatal_runtime_error:
                        break
            self.trace.write({'type': 'step', 'step': s.step, 'model': self.config.model, 'config': {'thinking': self.config.thinking}, 'prompt_tokens': s.call_prompt, 'cache_hit_tokens': s.call_cache_hit, 'completion_tokens': s.call_completion, 'tool_calls': s.calls_for_trace, 'observation': s.observations, 'assistant_content': s.message.content, 'finish_reason': s.choice.finish_reason, 'latency_seconds': time.monotonic() - s.call_started, 'max_estimated_cost_usd': s.max_cost, 'behavior': s.behavior.metrics()})
            if s.status in {'submitted', 'cost_limit', 'fatal_runtime_error'}:
                break
            if not s.raw_tool_calls:
                if s.no_tool_nudge_used:
                    s.status = 'no_tool_call'
                    break
                s.messages.append({'role': 'user', 'content': 'Continue by calling bash to work on the issue, or submit when done.'})
                s.no_tool_nudge_used = True
        try:
            s.patch = self.env.get_diff()
        except Exception as exc:
            s.patch = ''
            s.status = 'fatal_runtime_error'
            self.trace.write({'type': 'fatal_error', 'step': s.provider_calls, 'model': self.config.model, 'error': str(exc)})
        s.wall_time = time.monotonic() - s.started
        s.patch_changed_after_reject = bool(s.reviewer_returned and s.patch != s.reviewer_initial_patch)
        s.result = AgentResult(status=s.status, submitted=s.submitted, patch=s.patch, steps=s.provider_calls, provider_calls=s.provider_calls, tool_calls=s.tool_call_count, view_calls=s.view_call_count, str_replace_calls=s.str_replace_call_count, str_replace_failures=s.str_replace_failure_count, syntax_rollbacks=s.syntax_rollback_count, search_calls=s.search_call_count, truncations=s.truncation_count, index_file_count=s.index_file_count, index_chunk_count=s.index_chunk_count, index_build_seconds=s.index_build_seconds, dense_build_seconds=s.dense_build_seconds, dense_cache_hit=s.dense_cache_hit, dense_cache_hit_count=s.dense_cache_hit_count, dense_embedded_count=s.dense_embedded_count, pre_fix_reproduced=s.behavior.pre_fix_reproduced, post_fix_repro_passed=s.behavior.post_fix_repro_passed, repro_flipped=s.behavior.repro_flipped, first_production_edit_step=s.behavior.first_production_edit_step, existing_test_modified=s.behavior.existing_test_modified, git_history_search_count=s.behavior.git_history_search_count, network_attempt_count=s.behavior.network_attempt_count, reviewer_verdict=s.reviewer_verdict, reviewer_reject_reason=s.reviewer_reject_reason, reviewer_returned=s.reviewer_returned, patch_changed_after_reject=s.patch_changed_after_reject, reviewer_calls=s.reviewer_calls, reviewer_prompt_tokens=s.reviewer_prompt_tokens, reviewer_cache_hit_tokens=s.reviewer_cache_hit_tokens, reviewer_completion_tokens=s.reviewer_completion_tokens, reviewer_cost_usd=s.reviewer_cost, reviewer_latency_seconds=s.reviewer_latency, reviewer_initial_patch=s.reviewer_initial_patch, prompt_tokens=s.prompt_tokens, cache_hit_tokens=s.cache_hit_total if s.cache_hit_available else None, completion_tokens=s.completion_tokens, max_estimated_cost_usd=s.max_cost, wall_time_seconds=s.wall_time, trajectory_path=str(self.trace.path))
        self.trace.write({'type': 'summary', **{key: value for key, value in asdict(s.result).items() if key not in {'patch', 'reviewer_initial_patch'}}, 'patch_nonempty': bool(s.patch.strip()), 'patch': s.patch, 'behavior': s.behavior.metrics()})
        return s.result

    def _execute_tool(self, s):
        s.tool_call_count += 1
        s.name = s.tool_call.function.name
        if s.name == 'view':
            s.view_call_count += 1
        elif s.name == 'str_replace':
            s.str_replace_call_count += 1
        elif s.name == 'search_code':
            s.search_call_count += 1
        s.arguments_text = s.tool_call.function.arguments or '{}'
        s.arguments, s.argument_error = parse_tool_arguments(s.arguments_text)
        s.observation_metadata: dict[str, Any]
        if s.argument_error is not None:
            s.observation = s.argument_error
            if s.name == 'str_replace':
                s.str_replace_failure_count += 1
            s.observation_metadata = {'truncated': False, 'original_chars': len(s.observation), 'original_lines': len(s.observation.splitlines()), 'returned_chars': len(s.observation), 'full_output_path': None}
        else:
            assert s.arguments is not None
            if s.name == 'bash' and isinstance(s.arguments.get('command'), str):
                try:
                    s.result = self.env.execute(s.arguments['command'], self.config.tool_timeout_seconds)
                except Exception as exc:
                    s.observation = f'Fatal runtime error: {exc}'
                    s.observation_metadata = {'truncated': False, 'original_chars': len(s.observation), 'original_lines': len(s.observation.splitlines()), 'returned_chars': len(s.observation), 'full_output_path': None}
                    s.fatal_runtime_error = True
                    s.status = 'fatal_runtime_error'
                else:
                    try:
                        s.behavior.observe_changes(s.step, self.env.get_tracked_changes())
                    except Exception:
                        s.behavior.telemetry_errors += 1
                    s.behavior.observe_shell(s.step, s.arguments['command'], s.result.exit_code, s.result.output)
                    if usable_validation_evidence(s.arguments['command'], s.result.output, s.result.timed_out):
                        s.last_validation_command = s.arguments['command']
                        s.last_validation_output = s.result.output + f'\n[exit_code={s.result.exit_code}]'
                    if s.result.timed_out:
                        s.status_suffix = '\n[command timed out]'
                    else:
                        s.status_suffix = f'\n[exit_code={s.result.exit_code}]'
                    s.full_output_path = None
                    if len(s.result.output) > self.config.tool_output_max_chars:
                        s.full_output_path = f'/tmp/repofix_out_{s.tool_call_count}.txt'
                        try:
                            self.env.write_text_file(s.full_output_path, s.result.output)
                        except Exception as exc:
                            s.observation = f'Fatal runtime error while preserving full tool output: {exc}'
                            s.observation_metadata = {'truncated': False, 'original_chars': len(s.observation), 'original_lines': len(s.observation.splitlines()), 'returned_chars': len(s.observation), 'full_output_path': None}
                            s.fatal_runtime_error = True
                            s.status = 'fatal_runtime_error'
                        else:
                            s.view = format_tool_observation(s.result.output, s.status_suffix, s.full_output_path, self.config.tool_output_max_chars, self.config.tool_output_head_chars, self.config.tool_output_tail_chars)
                            s.observation = s.view.content
                            s.observation_metadata = {key: value for key, value in asdict(s.view).items() if key != 'content'}
                            s.truncation_count += 1
                    else:
                        s.view = format_tool_observation(s.result.output, s.status_suffix, None, self.config.tool_output_max_chars, self.config.tool_output_head_chars, self.config.tool_output_tail_chars)
                        s.observation = s.view.content
                        s.observation_metadata = {key: value for key, value in asdict(s.view).items() if key != 'content'}
            elif s.name == 'view':
                s.path = s.arguments.get('path')
                s.start_line = s.arguments.get('start_line')
                s.end_line = s.arguments.get('end_line')
                if not (isinstance(s.path, str) and isinstance(s.start_line, int) and (not isinstance(s.start_line, bool)) and isinstance(s.end_line, int) and (not isinstance(s.end_line, bool))):
                    s.observation = 'view error: path must be a string and line bounds must be integers'
                    s.observation_metadata = {'truncated': False, 'original_chars': len(s.observation), 'original_lines': 1, 'returned_chars': len(s.observation), 'full_output_path': None}
                else:
                    try:
                        s.view_output = self.env.view_file(s.path, s.start_line, s.end_line)
                        s.full_output_path = None
                        if len(s.view_output) > self.config.tool_output_max_chars:
                            s.full_output_path = f'/tmp/repofix_out_{s.tool_call_count}.txt'
                            self.env.write_text_file(s.full_output_path, s.view_output)
                        s.view = format_tool_observation(s.view_output, '', s.full_output_path, self.config.tool_output_max_chars, self.config.tool_output_head_chars, self.config.tool_output_tail_chars)
                    except Exception as exc:
                        s.observation = f'view error: {exc}'
                        s.observation_metadata = {'truncated': False, 'original_chars': len(s.observation), 'original_lines': len(s.observation.splitlines()), 'returned_chars': len(s.observation), 'full_output_path': None}
                    else:
                        s.observation = s.view.content
                        s.observation_metadata = {key: value for key, value in asdict(s.view).items() if key != 'content'}
                        s.truncation_count += int(s.view.truncated)
            elif s.name == 'str_replace':
                s.path = s.arguments.get('path')
                s.old_str = s.arguments.get('old_str')
                s.new_str = s.arguments.get('new_str')
                if not all((isinstance(value, str) for value in (s.path, s.old_str, s.new_str))):
                    s.observation = 'str_replace error: path, old_str, and new_str must be strings'
                    s.str_replace_failure_count += 1
                    s.observation_metadata = {'truncated': False, 'original_chars': len(s.observation), 'original_lines': 1, 'returned_chars': len(s.observation), 'full_output_path': None}
                else:
                    try:
                        s.edit = self.env.str_replace_file(s.path, s.old_str, s.new_str)
                        try:
                            s.behavior.observe_changes(s.step, self.env.get_tracked_changes())
                        except Exception:
                            s.behavior.telemetry_errors += 1
                        if not s.edit.success:
                            s.str_replace_failure_count += 1
                        s.syntax_rollback_count += int(s.edit.syntax_rollback)
                        s.full_output_path = None
                        if len(s.edit.output) > self.config.tool_output_max_chars:
                            s.full_output_path = f'/tmp/repofix_out_{s.tool_call_count}.txt'
                            self.env.write_text_file(s.full_output_path, s.edit.output)
                        s.view = format_tool_observation(s.edit.output, '', s.full_output_path, self.config.tool_output_max_chars, self.config.tool_output_head_chars, self.config.tool_output_tail_chars)
                    except Exception as exc:
                        s.str_replace_failure_count += 1
                        s.observation = f'str_replace error: {exc}'
                        s.observation_metadata = {'truncated': False, 'original_chars': len(s.observation), 'original_lines': len(s.observation.splitlines()), 'returned_chars': len(s.observation), 'full_output_path': None}
                    else:
                        s.observation = s.view.content
                        s.observation_metadata = {key: value for key, value in asdict(s.view).items() if key != 'content'}
                        s.truncation_count += int(s.view.truncated)
            elif s.name == 'search_code':
                s.query = s.arguments.get('query')
                s.top_k = s.arguments.get('top_k', SEARCH_DEFAULT_TOP_K)
                if not (isinstance(s.query, str) and s.query.strip() and isinstance(s.top_k, int) and (not isinstance(s.top_k, bool)) and (1 <= s.top_k <= SEARCH_MAX_TOP_K)):
                    s.observation = f'search_code error: query must be a non-empty string and top_k must be an integer from 1 to {SEARCH_MAX_TOP_K}'
                    s.observation_metadata = {'truncated': False, 'original_chars': len(s.observation), 'original_lines': 1, 'returned_chars': len(s.observation), 'full_output_path': None}
                else:
                    try:
                        s.search_output = s.search_formatter(s.code_index.search(s.query, top_k=s.top_k))
                        s.full_output_path = None
                        if len(s.search_output) > self.config.tool_output_max_chars:
                            s.full_output_path = f'/tmp/repofix_out_{s.tool_call_count}.txt'
                            self.env.write_text_file(s.full_output_path, s.search_output)
                        s.view = format_tool_observation(s.search_output, '', s.full_output_path, self.config.tool_output_max_chars, self.config.tool_output_head_chars, self.config.tool_output_tail_chars)
                    except Exception as exc:
                        s.observation = f'search_code error: {exc}'
                        s.observation_metadata = {'truncated': False, 'original_chars': len(s.observation), 'original_lines': len(s.observation.splitlines()), 'returned_chars': len(s.observation), 'full_output_path': None}
                    else:
                        s.observation = s.view.content
                        s.observation_metadata = {key: value for key, value in asdict(s.view).items() if key != 'content'}
                        s.truncation_count += int(s.view.truncated)
            elif s.name == 'submit':
                if self.config.reviewer_enabled and s.reviewer_calls == 0:
                    s.reviewer_calls = 1
                    try:
                        s.reviewer_initial_patch = self.env.get_diff()
                        s.review = review_patch(self.client, self.config, self.issue, s.reviewer_initial_patch, s.last_validation_command, s.last_validation_output)
                    except Exception as exc:
                        s.reviewer_verdict = 'ERROR'
                        s.reviewer_reject_reason = str(exc)
                        s.observation = 'Reviewer unavailable; submission accepted without a verdict.'
                        s.submitted = True
                        s.status = 'submitted'
                        self.trace.write({'type': 'reviewer', 'step': s.step, 'verdict': s.reviewer_verdict, 'error': str(exc), 'input_fields': ['issue', 'full_diff', 'last_validation_command', 'last_validation_output']})
                    else:
                        s.reviewer_verdict = s.review.verdict
                        s.reviewer_reject_reason = s.review.reject_reason
                        s.reviewer_prompt_tokens = s.review.prompt_tokens
                        s.reviewer_cache_hit_tokens = s.review.cache_hit_tokens
                        s.reviewer_completion_tokens = s.review.completion_tokens
                        s.reviewer_cost = s.review.estimated_cost_usd
                        s.reviewer_latency = s.review.latency_seconds
                        self.trace.write({'type': 'reviewer', 'step': s.step, 'model': self.config.model, 'verdict': s.review.verdict, 'reject_reason': s.review.reject_reason, 'raw_output': s.review.raw_output, 'finish_reason': s.review.finish_reason, 'prompt_tokens': s.review.prompt_tokens, 'cache_hit_tokens': s.review.cache_hit_tokens, 'completion_tokens': s.review.completion_tokens, 'estimated_cost_usd': s.review.estimated_cost_usd, 'latency_seconds': s.review.latency_seconds, 'input': {'issue': self.issue, 'full_diff': s.reviewer_initial_patch, 'last_validation_command': s.last_validation_command, 'last_validation_output': s.last_validation_output}})
                        if s.review.verdict == 'REJECT':
                            s.reviewer_returned = True
                            s.observation = f'Reviewer rejected the first submission: {s.review.reject_reason}\nAddress this reason, then call submit again.'
                        else:
                            s.observation = 'Submission accepted.'
                            s.submitted = True
                            s.status = 'submitted'
                else:
                    s.observation = 'Submission accepted.'
                    s.submitted = True
                    s.status = 'submitted'
                s.observation_metadata = {'truncated': False, 'original_chars': len(s.observation), 'original_lines': 1, 'returned_chars': len(s.observation), 'full_output_path': None}
            else:
                s.observation = f'Unknown or invalid tool call: {s.name}'
                s.observation_metadata = {'truncated': False, 'original_chars': len(s.observation), 'original_lines': len(s.observation.splitlines()), 'returned_chars': len(s.observation), 'full_output_path': None}
        s.observations.append({'tool_call_id': s.tool_call.id, 'name': s.name, 'content': s.observation, **s.observation_metadata})
        s.messages.append({'role': 'tool', 'tool_call_id': s.tool_call.id, 'content': s.observation})
