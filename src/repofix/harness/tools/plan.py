import json


def update_plan(state, steps):
    if not isinstance(steps, list):
        raise ValueError("steps must be a list")
    normalized = []
    for item in steps:
        if not isinstance(item, dict) or set(item) != {"step", "status"}:
            raise ValueError("each plan item requires step and status only")
        if not isinstance(item["step"], str) or not item["step"].strip():
            raise ValueError("plan step must be nonempty text")
        if item["status"] not in {"pending", "in_progress", "completed"}:
            raise ValueError("invalid plan status")
        normalized.append(dict(item))
    if sum(item["status"] == "in_progress" for item in normalized) > 1:
        raise ValueError("at most one step may be in_progress")
    state.plan = normalized
    return json.dumps(state.plan, ensure_ascii=False)
