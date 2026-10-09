import fnmatch
import re
import shlex


def grep(env, pattern, path_glob=None, max_results=50):
    if not isinstance(pattern, str) or not pattern or not isinstance(max_results, int) or isinstance(max_results, bool) or not 1 <= max_results <= 1000:
        raise ValueError("grep requires a pattern and max_results in 1..1000")
    if path_glob is not None and (not isinstance(path_glob, str) or path_glob.startswith("/") or ".." in path_glob.split("/")):
        raise ValueError("path_glob must stay inside /testbed")
    if hasattr(env, "files"):
        matches = []
        for path, text in sorted(env.files.items()):
            if path_glob and not fnmatch.fnmatch(path, path_glob):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if re.search(pattern, line):
                    matches.append(f"{path}:{number}:{line}")
        return "\n".join(matches[:max_results]) or "No matches."
    glob = f" --glob {shlex.quote(path_glob)}" if path_glob else ""
    include = f" --include={shlex.quote(path_glob)}" if path_glob else ""
    command = (f"if command -v rg >/dev/null 2>&1; then rg -n --no-heading --color never{glob} -- {shlex.quote(pattern)} .; "
               f"else grep -rnE{include} -- {shlex.quote(pattern)} .; fi")
    result = env.execute(command)
    if result.exit_code not in (0, 1):
        raise ValueError(result.output or "grep failed")
    return "\n".join(result.output.splitlines()[:max_results]) or "No matches."
