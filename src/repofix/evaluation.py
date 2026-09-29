"""Build a production-only patch for official SWE-bench evaluation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .reproduction import is_test_path


_DIFF_START_RE = re.compile(r"(?m)(?=^diff --git )")


@dataclass(frozen=True)
class EvaluationPatch:
    patch: str
    filtered_test_paths: tuple[str, ...]


def _section_paths(section: str) -> tuple[str, ...]:
    paths: list[str] = []
    for line in section.splitlines():
        if line.startswith("--- a/") or line.startswith("+++ b/"):
            paths.append(line[6:])
        elif line.startswith("rename from "):
            paths.append(line.removeprefix("rename from "))
        elif line.startswith("rename to "):
            paths.append(line.removeprefix("rename to "))
        if line.startswith("@@"):
            break

    if not paths:
        header = section.splitlines()[0]
        if " b/" in header:
            paths.append(header.rsplit(" b/", 1)[1])
    return tuple(dict.fromkeys(paths))


def build_evaluation_patch(full_patch: str) -> EvaluationPatch:
    """Remove test-file diff sections while preserving the full Agent patch."""
    if not full_patch.strip():
        return EvaluationPatch(patch=full_patch, filtered_test_paths=())

    kept: list[str] = []
    filtered: list[str] = []
    for section in _DIFF_START_RE.split(full_patch):
        if not section:
            continue
        paths = _section_paths(section)
        test_paths = [path for path in paths if is_test_path(path)]
        if test_paths:
            filtered.extend(test_paths)
        else:
            kept.append(section)
    return EvaluationPatch(
        patch="".join(kept),
        filtered_test_paths=tuple(dict.fromkeys(filtered)),
    )
