"""Codex-style text patch parser with all-files prepare before any write."""
from dataclasses import dataclass, field
import unicodedata


@dataclass
class Edit:
    kind: str
    path: str
    target: str | None = None
    lines: list[str] = field(default_factory=list)
    hunks: list[list[str]] = field(default_factory=list)


def parse_patch(patch):
    if not isinstance(patch, str):
        raise ValueError("patch must be text")
    lines = patch.splitlines()
    if len(lines) < 3 or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ValueError("patch needs Begin Patch and End Patch markers")
    edits, current = [], None
    for line in lines[1:-1]:
        kinds = {"*** Add File: ": "add", "*** Update File: ": "update", "*** Delete File: ": "delete"}
        marker = next((prefix for prefix in kinds if line.startswith(prefix)), None)
        if marker:
            current = Edit(kinds[marker], line[len(marker):])
            if not current.path:
                raise ValueError("empty file path")
            edits.append(current)
        elif line.startswith("*** Move to: ") and current and current.kind == "update" and not current.hunks and current.target is None:
            current.target = line[len("*** Move to: "):]
        elif line.startswith("@@") and current and current.kind == "update":
            current.hunks.append([])
        elif current and current.kind == "add" and line.startswith("+"):
            current.lines.append(line[1:])
        elif current and current.kind == "update" and current.hunks and (line[:1] in {" ", "+", "-"} or line == "*** End of File"):
            current.hunks[-1].append(line)
        else:
            raise ValueError(f"invalid patch line: {line[:100]}")
    if not edits:
        raise ValueError("empty patch")
    for edit in edits:
        if edit.kind == "update" and not edit.hunks:
            raise ValueError(f"{edit.path}: update has no hunks")
    return edits


def normalized(line, level):
    if level == 0:
        return line
    if level == 1:
        return line.rstrip()
    if level == 2:
        return line.strip()
    return unicodedata.normalize("NFKC", line).translate(str.maketrans({
        "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "−": "-", "…": "..."
    })).strip()


def update_content(path, original, hunks):
    lines = original.splitlines()
    cursor = 0
    for hunk in hunks:
        eof = bool(hunk and hunk[-1] == "*** End of File")
        hunk = [line for line in hunk if line != "*** End of File"]
        old = [line[1:] for line in hunk if line.startswith((" ", "-"))]
        new = [line[1:] for line in hunk if line.startswith((" ", "+"))]
        if not hunk or not old:
            raise ValueError(f"{path}: update hunk needs old context")
        found = None
        for level in range(4):
            candidates = [i for i in range(cursor, len(lines) - len(old) + 1)
                if (not eof or i + len(old) == len(lines)) and
                [normalized(s, level) for s in lines[i:i + len(old)]] == [normalized(s, level) for s in old]]
            if candidates:
                if len(candidates) > 1:
                    raise ValueError(f"{path}: ambiguous context: {old[:3]!r}")
                found = candidates[0]
                break
        if found is None:
            raise ValueError(f"{path}: context not found: {old[:3]!r}")
        lines[found:found + len(old)] = new
        cursor = found + len(new)
    return "\n".join(lines) + ("\n" if original.endswith("\n") and lines else "")


def prepare_patch(patch, files, read_guard=None):
    updates, touched = {}, set()
    for edit in parse_patch(patch):
        path = files.resolve(edit.path)
        target = files.resolve(edit.target) if edit.target else path
        if path in touched or (target != path and target in touched):
            raise ValueError(f"{path}: duplicate/overlapping patch targets")
        touched.update((path, target))
        if edit.kind == "add":
            if files.exists(path):
                raise ValueError(f"{path}: already exists")
            updates[path] = "\n".join(edit.lines) + ("\n" if edit.lines else "")
        else:
            original = files.read(path)
            if read_guard:
                read_guard(path, original)
            if edit.kind == "delete":
                updates[path] = None
            else:
                if target != path and files.exists(target):
                    raise ValueError(f"{target}: move target exists")
                updated = update_content(path, original, edit.hunks)
                updates[path] = None if target != path else updated
                updates[target] = updated
    return updates
