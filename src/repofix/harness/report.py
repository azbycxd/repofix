import math
import statistics
from collections import Counter


def percentile(values, quantile):
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * quantile) - 1)] if values else 0


def render_report(rows):
    fake = bool(rows) and all(row.get("fake") for row in rows)
    title = "# RepoFix experiment report" + (" — FAKE OFFLINE FIXTURE, NOT MODEL RESULTS" if fake else "")
    lines = [title, "", "| Variant | Resolved / judged | Mean steps | P50/P95 steps | Mean wall (s) | P50/P95 wall (s) | Total cost ($) | Mean cost ($) |",
             "| --- | --- | ---: | --- | ---: | --- | ---: | ---: |"]
    variants = list(dict.fromkeys(row["variant"] for row in rows))
    for variant in variants:
        group = [row for row in rows if row["variant"] == variant]
        judged = [r for r in group if r["resolved"] is not None]
        resolved = sum(bool(r["resolved"]) for r in judged)
        rate = f"{resolved}/{len(judged)} ({resolved / len(judged):.1%})" if judged else "not judged"
        steps, walls, costs = ([r[key] for r in group] for key in ("steps", "wall_seconds", "estimated_cost"))
        lines.append(f"| {variant} | {rate} | {statistics.mean(steps):.2f} | {percentile(steps,.5)}/{percentile(steps,.95)} | "
                     f"{statistics.mean(walls):.3f} | {percentile(walls,.5):.3f}/{percentile(walls,.95):.3f} | {sum(costs):.9f} | {statistics.mean(costs):.9f} |")
    lines += ["", "## Task result matrix", "", "| Task / repeat | " + " | ".join(variants) + " |", "| --- | " + " | ".join("---" for _ in variants) + " |"]
    for task, repeat in dict.fromkeys((r["task"], r["repeat"]) for r in rows):
        cells = []
        for variant in variants:
            row = next((r for r in rows if (r["task"], r["repeat"], r["variant"]) == (task, repeat, variant)), None)
            cells.append("ERROR" if row and row.get("error") else "RESOLVED" if row and row["resolved"] else "UNRESOLVED" if row and row["resolved"] is False else "NOT_JUDGED")
        lines.append(f"| {task} / {repeat} | " + " | ".join(cells) + " |")
    lines += ["", "## Termination reasons", "", "| Reason | Count |", "| --- | ---: |"]
    for reason, count in sorted(Counter(r.get("termination_reason", "unknown") for r in rows).items()):
        lines.append(f"| {reason} | {count} |")
    return "\n".join(lines) + "\n"
