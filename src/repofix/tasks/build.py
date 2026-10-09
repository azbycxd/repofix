"""Generate feature specs using real Git history and an injectable evaluator."""
import subprocess
import tempfile
import re
from pathlib import Path

from repofix.reproduction import is_test_path
from .spec import TaskSpec


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True)


def build_task(entry, evaluator):
    """evaluator(checkout, setup_commands, nodes=None) -> {pytest_node: passed_bool}."""
    required = ("id", "repo_url", "base_commit", "merged_commit", "description")
    if any(not entry.get(key) for key in required):
        raise ValueError("seed needs " + ", ".join(required))
    if any(not re.fullmatch(r"[0-9a-fA-F]{40}", entry[key]) for key in ("base_commit", "merged_commit")):
        raise ValueError("seed commits must be actual full Git SHAs; replace all placeholders")
    with tempfile.TemporaryDirectory(prefix="repofix-feature-") as directory:
        repo = Path(directory) / "repository"
        subprocess.run(["git", "clone", "--quiet", "--", entry["repo_url"], str(repo)], check=True)
        base = git(repo, "rev-parse", entry["base_commit"] + "^{commit}").strip()
        merged = git(repo, "rev-parse", entry["merged_commit"] + "^{commit}").strip()
        paths = git(repo, "diff", "--name-only", "-z", base, merged).split("\0")
        tests = [path for path in paths if path and is_test_path(path)]
        if not tests:
            raise ValueError("no changed test paths in the commit range")
        patch = git(repo, "diff", "--binary", base, merged, "--", *tests)
        git(repo, "checkout", "--detach", "--quiet", merged)
        after = evaluator(repo, entry.get("setup_commands", []), None)
        if not after or not all(after.values()):
            raise ValueError("merged revision must have collected and passed every selected test")
        # The temporary clone is owned by this builder; never reset input repo.
        git(repo, "reset", "--hard", merged)
        git(repo, "clean", "-fd")
        git(repo, "checkout", "--detach", "--quiet", base)
        subprocess.run(["git", "-C", str(repo), "apply", "--binary", "-"], input=patch, text=True, check=True)
        git(repo, "add", "--", *tests)
        before = evaluator(repo, entry.get("setup_commands", []), list(after))
        f2p = sorted(node for node, passed in after.items() if passed and not before.get(node, False))
        p2p = sorted(node for node, passed in after.items() if passed and before.get(node, False))
        if not f2p:
            raise ValueError("no fail-to-pass cases found")
        return TaskSpec(entry["id"], "feature", entry["repo_url"], base,
                        entry["description"], patch, f2p, p2p, entry.get("setup_commands", []))
