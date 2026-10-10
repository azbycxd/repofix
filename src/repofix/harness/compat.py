"""Explicit V4 container-helper compatibility. No interpreter monkey patches."""

PY36_PATH_HELPER = """from pathlib import Path
import os
root = Path('/testbed').resolve()
def within(path, parent):
    try: path.relative_to(parent); return True
    except ValueError: return False
def unlink_if_present(path):
    try: path.unlink()
    except FileNotFoundError: pass
def resolve(value):
    if not isinstance(value, str) or not value or '\\0' in value or '..' in Path(value).parts:
        raise ValueError('invalid path or traversal')
    path = Path(value)
    path = (path if path.is_absolute() else root / path).resolve()
    if not within(path, root) or path == root or '.git' in path.relative_to(root).parts:
        raise ValueError('path must stay in /testbed outside .git')
    return path
"""


def compatible_source(source, legacy_path_helper):
    """Only rewrite the known host-owned templates, never repository source."""
    source = source.replace(legacy_path_helper, PY36_PATH_HELPER)
    source = source.replace("text=True", "universal_newlines=True")
    source = source.replace("str(p.readlink())", "os.readlink(str(p))")
    legacy_check = "p.resolve().is_relative_to(pathlib.Path('/testbed').resolve())"
    if legacy_check in source:
        source = PY36_PATH_HELPER + source.replace(legacy_check, "within(p.resolve(), root)")
    for name in ("p", "index_path"):
        source = source.replace(
            name + ".unlink(missing_ok=True)", "unlink_if_present(" + name + ")"
        )
    if "os.readlink" in source and "import os" not in source:
        source = "import os\n" + source
    return source
