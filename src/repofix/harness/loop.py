"""Profile routing; v1 preserves its frozen ordering and observations."""


def run_agent(agent):
    if getattr(agent.config, "profile", "v1") == "v1":
        from .v1 import V1Loop
        # The compatibility loop delegates through the same outer object so its
        # injected model, env, trace and usage reader keep their original APIs.
        agent._execute_tool = lambda state: V1Loop._execute_tool(agent, state)
        return V1Loop.run(agent)
    return run_v3(agent)


def run_v3(agent):
    import time
    from dataclasses import asdict
    from repofix.agent import SYSTEM_PROMPT
    from .state import RunState
    from .runtime import Runtime
    from .model import OpenAICompatibleClient
    from .tools.registry import schedule
    from .telemetry import finish, record_usage

    state = getattr(agent, "state", None) or RunState(messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Fix this issue in /testbed:\n\n{agent.issue}"}])
    agent.state = state
    runtime = Runtime(agent, state)
    model = agent.client if hasattr(agent.client, "complete") else OpenAICompatibleClient(agent.client)
    from .context import ContextManager
    context = ContextManager(agent.config, model, agent.env, agent.trace.path.parent, agent.trace._redact_text)
    from .checkpoint import CheckpointStore, atomic_write
    import json
    checkpoints = CheckpointStore(agent.trace.path.parent, agent.trace._redact, agent.trace.secrets) if agent.config.checkpointing else None
    if checkpoints:
        manifest = {"config": asdict(agent.config), "issue": agent.issue, "git_commit": agent.git_commit,
                    "trajectory": agent.trace.path.name,
                    **getattr(agent, "resume_metadata", {"kind": "docker", "image": getattr(agent.env, "image", ""),
                       "instance_id": getattr(agent.env, "instance_id", "")})}
        atomic_write(agent.trace.path.parent / "resume.json", json.dumps(agent.trace._redact(manifest)).encode())
    started = time.monotonic()
    agent.trace.write({"type": "config", "git_commit": agent.git_commit,
                       "config": asdict(agent.config), "problem_statement": agent.issue})
    while state.step < agent.config.max_steps and not state.termination:
        if agent.config.context_management:
            event = context.maybe_compact(state)
            if event:
                agent.trace.write(event)
            if state.termination:
                break
        if state.budget.estimated_cost >= agent.config.max_cost_usd:
            state.termination = "max_cost"
            break
        state.step += 1
        call_started = time.monotonic()
        try:
            response = model.complete(state.messages, runtime.registry.schemas, agent.config)
        except KeyboardInterrupt:
            state.termination = "interrupted"
            break
        except Exception as exc:
            state.termination = "provider_error"
            agent.trace.write({"type": "provider_error", "error": str(exc), "step": state.step})
            break
        record_usage(state.budget, response.usage, agent.config)
        state.metadata["usage_anchor"] = {"tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
                                           "messages": len(state.messages)}
        choice = response.choices[0]
        message = choice.message
        calls = [{"id": call.id, "name": call.function.name, "arguments": call.function.arguments or "{}"}
                 for call in (message.tool_calls or [])]
        payload = {"role": "assistant", "content": message.content}
        if calls:
            payload["tool_calls"] = [{"id": c["id"], "type": "function", "function":
                {"name": c["name"], "arguments": c["arguments"]}} for c in calls]
        state.messages.append(payload)
        state.pending_calls = list(calls)
        if checkpoints:
            checkpoints.save(state, agent.env)
        observations = []
        for call, result, batch_size in schedule(calls, runtime.registry, runtime.execute,
                agent.config.parallel_readonly, agent.config.readonly_workers):
            state.count("tool_calls")
            state.count(call["name"] + "_calls")
            state.count("truncations", int(result.metadata.get("truncated", False)))
            for counter in ("str_replace_failures", "syntax_rollbacks"):
                state.count(counter, result.metadata.get(counter, 0))
            state.messages.append({"role": "tool", "tool_call_id": call["id"], "content": result.content})
            state.pending_calls = state.pending_calls[1:]
            observations.append({"tool_call_id": call["id"], "name": call["name"], "content": result.content,
                                 "parallel_batch_size": batch_size, **result.metadata})
            if checkpoints:
                checkpoints.save(state, agent.env)
            if state.termination:
                for pending in state.pending_calls:
                    state.messages.append({"role": "tool", "tool_call_id": pending["id"],
                                           "content": "[not executed: run terminated]"})
                state.pending_calls = []
                break
        agent.trace.write({"type": "step", "step": state.step, "tool_calls": calls,
            "observation": observations, "finish_reason": choice.finish_reason,
            "latency_seconds": time.monotonic() - call_started, "budget": asdict(state.budget)})
        if not calls:
            if state.metadata.get("nudge_used"):
                state.termination = "interrupted"
            else:
                state.messages.append({"role": "user", "content": "Continue using tools, or submit when done."})
                state.metadata["nudge_used"] = True
        if checkpoints:
            checkpoints.save(state, agent.env)
    state.termination = state.termination or "max_steps"
    return finish(agent, state, runtime, time.monotonic() - started)
