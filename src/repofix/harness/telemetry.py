"""Legacy-compatible result plus extensible V3 summary."""
import json
from dataclasses import asdict, fields
from repofix.agent import AgentResult


def record_usage(budget, usage, config):
    prompt = getattr(usage, "prompt_tokens", 0) or 0
    completion = getattr(usage, "completion_tokens", 0) or 0
    hit = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
    miss = getattr(usage, "prompt_cache_miss_tokens", None)
    miss = max(0, prompt - hit) if miss is None else miss
    budget.provider_calls += 1
    budget.prompt_tokens += prompt
    budget.completion_tokens += completion
    budget.cache_hit_tokens += hit
    budget.estimated_cost += (hit * config.cache_hit_input_price + miss * config.cache_miss_input_price
                              + completion * config.output_price) / 1_000_000


def finish(agent, state, runtime, wall):
    values = {}
    for f in fields(AgentResult):
        values[f.name] = False if f.type == "bool" else ("" if f.type == "str" else (None if "None" in str(f.type) else 0))
    try:
        patch = agent.env.get_diff()
    except Exception as exc:
        patch = ""
        state.termination = "interrupted"
        state.events.append({"type": "runtime_error", "error": str(exc)})
    values.update(status=state.termination, submitted=state.submitted, patch=patch,
        steps=state.step, provider_calls=state.budget.provider_calls,
        tool_calls=state.counters.get("tool_calls", 0),
        view_calls=state.counters.get("view_calls", 0),
        str_replace_calls=state.counters.get("str_replace_calls", 0),
        str_replace_failures=state.counters.get("str_replace_failures", 0),
        syntax_rollbacks=state.counters.get("syntax_rollbacks", 0),
        search_calls=state.counters.get("search_code_calls", 0),
        truncations=state.counters.get("truncations", 0),
        index_file_count=runtime.index_stats.file_count, index_chunk_count=runtime.index_stats.chunk_count,
        index_build_seconds=runtime.index_stats.build_seconds,
        prompt_tokens=state.budget.prompt_tokens, completion_tokens=state.budget.completion_tokens,
        cache_hit_tokens=state.budget.cache_hit_tokens, max_estimated_cost_usd=state.budget.estimated_cost,
        wall_time_seconds=wall, trajectory_path=str(agent.trace.path))
    result = AgentResult(**values)
    summary = {**asdict(result), "termination_reason": state.termination,
               "counters": state.counters, "events": state.events}
    agent.trace.write({"type": "summary", **summary})
    (agent.trace.path.parent / "summary.json").write_text(
        json.dumps(agent.trace._redact(summary), ensure_ascii=False, indent=2) + "\n")
    return result
