"""Two-stage compaction with protocol-safe recent history and program facts."""
import hashlib
import json
import math
from pathlib import Path

from repofix.evaluation import changed_paths
from .telemetry import record_usage

SUMMARY_FIELDS = ("goal", "constraints", "done", "verified_facts", "failed_attempts", "next_steps")


def text_tokens(value):
    # No optional tokenizer download at runtime. Actual provider usage anchors
    # subsequent estimates; the offline fallback is explicitly chars / 3.
    return math.ceil(len(json.dumps(value, ensure_ascii=False)) / 3)


def estimate(state):
    anchor = state.metadata.get("usage_anchor")
    if anchor:
        return anchor["tokens"] + text_tokens(state.messages[anchor["messages"]:])
    return text_tokens(state.messages)


def recent_complete_messages(messages, count):
    start = max(2, len(messages) - count)
    while start > 2 and messages[start].get("role") == "tool":
        start -= 1
    return messages[start:] if start < len(messages) else []


class ContextManager:
    def __init__(self, config, model, env, artifact_dir, redact=lambda x: x):
        self.config, self.model, self.env = config, model, env
        self.artifact_dir = Path(artifact_dir) / "context"
        self.redact = redact

    def facts(self, state):
        patch = self.env.get_diff()
        stat = self.env.execute("git diff --stat HEAD --").output
        validation = dict(state.last_validation) if state.last_validation else None
        if validation and isinstance(validation.get("output"), str):
            validation["output"] = validation["output"][-2000:]
        return {"task": state.messages[1]["content"], "plan": state.plan,
                "files_changed": list(changed_paths(patch)), "diff_stat": stat,
                "last_validation": validation}

    def maybe_compact(self, state):
        threshold = self.config.context_window * self.config.compact_threshold
        before = estimate(state)
        if before <= threshold:
            state.metadata["failed_compactions"] = 0
            return None
        previous = state.metadata.get("last_compact_step", -100)
        if state.step <= previous + 1:
            return None
        state.metadata["last_compact_step"] = state.step
        tool_indices = [i for i, m in enumerate(state.messages) if m["role"] == "tool"]
        eligible = tool_indices[:-self.config.keep_recent_tool_results] if self.config.keep_recent_tool_results else tool_indices
        masked = 0
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        for i in eligible:
            msg = state.messages[i]
            content = msg.get("content") or ""
            if content.startswith("[RepoFix: 已省略"):
                continue
            key = hashlib.sha256((str(i) + content).encode()).hexdigest()[:20]
            path = self.artifact_dir / f"tool-{key}.txt"
            path.write_text(self.redact(content), encoding="utf-8")
            msg["content"] = f"[RepoFix: 已省略 {len(content)} 字符的旧输出，全文见 {path}]"
            masked += 1
        state.metadata.pop("usage_anchor", None)
        after_mask = estimate(state)
        event = {"type": "compaction", "step": state.step, "before_tokens": before,
                 "masked_results": masked, "after_mask_tokens": after_mask, "summary_called": False}
        if after_mask > threshold:
            event["summary_called"] = True
            prompt = {"role": "user", "content":
                "Produce only a JSON handoff with goal, constraints, done, verified_facts, "
                "failed_attempts (including reasons), and next_steps. Do not invent facts. "
                "Files changed and last test evidence are filled by the harness."}
            try:
                response = self.model.complete([*state.messages, prompt], [], self.config)
                record_usage(state.budget, response.usage, self.config)
                state.count("summary_calls")
                data = json.loads(response.choices[0].message.content)
                if not isinstance(data, dict) or any(k not in data for k in SUMMARY_FIELDS):
                    raise ValueError("summary missing required fields")
                if any(not isinstance(data[k], (str, list)) for k in SUMMARY_FIELDS):
                    raise ValueError("summary fields must be text or lists")
                summary = {key: data[key] for key in SUMMARY_FIELDS}
                summary.update(self.facts(state))
                suffix = recent_complete_messages(state.messages, max(1, self.config.keep_recent_tool_results))
                state.messages = [*state.messages[:2], {"role": "user", "content":
                    "[RepoFix compacted handoff]\n" + json.dumps(summary, ensure_ascii=False)}, *suffix]
            except Exception as exc:
                event["summary_error"] = str(exc)
        else:
            # Mask-only compaction also refreshes authoritative task/plan facts.
            state.messages.append({"role": "user", "content": "[RepoFix context facts]\n" + json.dumps(self.facts(state), ensure_ascii=False)})
        after = estimate(state)
        event["after_tokens"] = after
        state.count("compactions")
        state.events.append(event)
        if after > threshold:
            failures = state.metadata.get("failed_compactions", 0) + 1
            state.metadata["failed_compactions"] = failures
            if failures >= 3:
                state.termination = "context_exhausted"
        else:
            state.metadata["failed_compactions"] = 0
        return event
