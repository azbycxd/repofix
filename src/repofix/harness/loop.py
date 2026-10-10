"""Profile routing; v1 preserves its frozen ordering and observations."""

import json
import time
from dataclasses import asdict
from threading import RLock

from repofix.reproduction import ReproductionTelemetry
from repofix.tasks.prompts import task_messages

from .checkpoint import atomic_write
from .checkpoint_v4 import checkpoint_store, save_boundary
from .config import serialize_config
from .context import ContextManager
from .deadline import DeadlineModel, TaskDeadline
from .model import OpenAICompatibleClient
from .progress import ProgressMonitor
from .runtime import Runtime
from .state import RunState
from .telemetry import finish, record_usage
from .tools.registry import schedule
from .v1 import V1Loop


def run_agent(agent):
    if getattr(agent.config, "profile", "v1") == "v1":
        return V1Loop(agent).run()
    return run_v3(agent)


def run_v3(agent):
    if not hasattr(agent, "budget_lock"):
        agent.budget_lock = RLock()

    state = getattr(agent, "state", None) or RunState(
        messages=task_messages(agent.config.task_kind, agent.issue)
    )
    agent.state = state
    behavior_data = dict(state.metadata.get("reproduction", {}))
    if "_failed_reproduction_keys" in behavior_data:
        behavior_data["_failed_reproduction_keys"] = set(behavior_data["_failed_reproduction_keys"])
    behavior = ReproductionTelemetry(**behavior_data)
    runtime = Runtime(agent, state)
    if agent.config.profile != "v4":
        return finish(agent, state, runtime, drive(agent, state, runtime, behavior))
    started = time.monotonic()
    try:
        wall = drive(agent, state, runtime, behavior)
    except KeyboardInterrupt:
        state.termination = "interrupted"
        wall = time.monotonic() - started
    except TaskDeadline as exc:
        state.termination = "task_deadline"
        agent.trace.write({"type": "deadline", "producer": "harness", "error": str(exc)})
        wall = time.monotonic() - started
    except Exception as exc:
        state.termination = "runtime_error"
        agent.trace.write({"type": "runtime_error", "producer": "harness", "error": str(exc)})
        wall = time.monotonic() - started
    finally:
        runtime.cleanup()
    return finish(agent, state, runtime, wall)


def drive(agent, state, runtime, behavior):
    progress = (
        ProgressMonitor(agent.config, agent.env, state) if agent.config.progress_monitor else None
    )
    model = (
        agent.client if hasattr(agent.client, "complete") else OpenAICompatibleClient(agent.client)
    )
    if runtime.deadline:
        model = DeadlineModel(model, runtime.deadline)
    context = ContextManager(
        agent.config, model, agent.env, agent.trace.path.parent, agent.trace._redact_text
    )
    checkpoints = (
        checkpoint_store(
            agent.config, agent.trace.path.parent, agent.trace._redact, agent.trace.secrets
        )
        if agent.config.checkpointing
        else None
    )
    if checkpoints:
        manifest = {
            "python_hooks_present": any(agent.python_hooks.values()),
            "config": serialize_config(agent.config),
            "issue": agent.issue,
            "git_commit": agent.git_commit,
            "trajectory": agent.trace.path.name,
            **getattr(
                agent,
                "resume_metadata",
                {
                    "kind": "docker",
                    "image": getattr(agent.env, "image", ""),
                    "instance_id": getattr(agent.env, "instance_id", ""),
                },
            ),
        }
        atomic_write(
            agent.trace.path.parent / "resume.json",
            json.dumps(agent.trace._redact(manifest)).encode(),
        )
    started = time.monotonic()
    agent.trace.write(
        {
            "type": "config",
            "git_commit": agent.git_commit,
            "config": serialize_config(agent.config),
            "problem_statement": agent.issue,
        }
    )
    event_cursor = len(state.events)
    while state.step < agent.config.max_steps and not state.termination:
        if runtime.deadline:
            runtime.deadline.check()
        if agent.config.context_management:
            with agent.budget_lock:
                event = (
                    context.maybe_compact(state)
                    if state.budget.estimated_cost < agent.config.max_cost_usd
                    else None
                )
            if event:
                agent.trace.write(event)
            if state.termination:
                break
        if state.budget.estimated_cost >= agent.config.max_cost_usd:
            state.termination = "max_cost"
            break
        state.step += 1
        call_started = time.monotonic()
        state.count("model_requests")
        try:
            with agent.budget_lock:
                if state.budget.estimated_cost >= agent.config.max_cost_usd:
                    state.termination = "max_cost"
                    break
                response = model.complete(state.messages, runtime.registry.schemas, agent.config)
                record_usage(state.budget, response.usage, agent.config)
        except TaskDeadline as exc:
            state.termination = "task_deadline"
            agent.trace.write({"type": "deadline", "producer": "harness", "error": str(exc)})
            break
        except KeyboardInterrupt:
            state.termination = "interrupted"
            break
        except Exception as exc:
            state.termination = "provider_error"
            agent.trace.write({"type": "provider_error", "error": str(exc), "step": state.step})
            break
        state.metadata["usage_anchor"] = {
            "tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
            "messages": len(state.messages),
        }
        model_latency = time.monotonic() - call_started
        choice = response.choices[0]
        message = choice.message
        calls = [
            {
                "id": call.id,
                "name": call.function.name,
                "arguments": call.function.arguments or "{}",
            }
            for call in (message.tool_calls or [])
        ]
        payload = {"role": "assistant", "content": message.content}
        if calls:
            payload["tool_calls"] = [
                {
                    "id": c["id"],
                    "type": "function",
                    "function": {"name": c["name"], "arguments": c["arguments"]},
                }
                for c in calls
            ]
        state.messages.append(payload)
        state.pending_calls = list(calls)
        # Persist the assistant/tool-call protocol before any tool can interrupt
        # or mutate the workspace. Resume marks pending calls, never replays them.
        if not save_boundary(checkpoints, state, runtime, "pre_dispatch"):
            break
        if state.budget.estimated_cost >= agent.config.max_cost_usd:
            state.termination = "max_cost"
            for call in calls:
                state.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": "[not executed: max_cost]",
                    }
                )
            state.pending_calls = []
            calls = []
        observations = []
        for call, result, batch_size in schedule(
            calls,
            runtime.registry,
            runtime.execute,
            agent.config.parallel_readonly,
            agent.config.readonly_workers,
        ):
            state.count("tool_calls")
            state.count(call["name"] + "_calls")
            state.count("truncations", int(result.metadata.get("truncated", False)))
            for counter in ("str_replace_failures", "syntax_rollbacks"):
                state.count(counter, result.metadata.get(counter, 0))
            try:
                behavior.observe_changes(state.step, agent.env.get_tracked_changes())
            except Exception:
                behavior.telemetry_errors += 1
            if call["name"] in {"bash", "job_output"} and result.metadata.get("command"):
                behavior.observe_shell(
                    state.step, result.metadata["command"], result.exit_code, result.content
                )
            data = asdict(behavior)
            data["_failed_reproduction_keys"] = sorted(data["_failed_reproduction_keys"])
            state.metadata["reproduction"] = data
            state.messages.append(
                {"role": "tool", "tool_call_id": call["id"], "content": result.content}
            )
            state.pending_calls = state.pending_calls[1:]
            observations.append(
                {
                    "tool_call_id": call["id"],
                    "name": call["name"],
                    "content": result.content,
                    "parallel_batch_size": batch_size,
                    **result.metadata,
                }
            )
            if checkpoints and (
                call["name"] in {"bash", "str_replace", "apply_patch", "verify"}
                or agent.config.checkpoint_limits
                and call["name"] == "run_tests"
            ):
                if not save_boundary(checkpoints, state, runtime, "post_mutation"):
                    break
            if state.termination:
                for pending in state.pending_calls:
                    state.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": pending["id"],
                            "content": "[not executed: run terminated]",
                        }
                    )
                state.pending_calls = []
                break
        if progress:
            sample = progress.observe(calls, observations)
            state.metadata["v4"]["last_progress_signal"] = sample["signal"]
            agent.trace.write(sample)
        agent.trace.write(
            {
                "type": "step",
                "step": state.step,
                "tool_calls": calls,
                "observation": observations,
                "finish_reason": choice.finish_reason,
                "latency_seconds": time.monotonic() - call_started,
                "model_latency_seconds": model_latency,
                "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
                "completion_tokens": getattr(response.usage, "completion_tokens", None),
                "cache_hit_tokens": getattr(response.usage, "prompt_cache_hit_tokens", None),
                "budget": asdict(state.budget),
                "behavior": behavior.metrics(),
                "events": state.events[event_cursor:],
            }
        )
        event_cursor = len(state.events)
        if not calls and not state.termination:
            if state.metadata.get("nudge_used"):
                state.termination = "no_tool_call"
            else:
                state.messages.append(
                    {"role": "user", "content": "Continue using tools, or submit when done."}
                )
                state.metadata["nudge_used"] = True
        if not save_boundary(checkpoints, state, runtime, "end_step"):
            break
    state.termination = state.termination or "max_steps"
    return time.monotonic() - started
