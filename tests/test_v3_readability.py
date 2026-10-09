"""Keep the interview-facing structure legible without changing behavior."""

import ast
from pathlib import Path


def test_source_line_bounds_and_no_function_imports():
    root = Path(__file__).resolve().parents[1] / "src"
    for path in root.rglob("*.py"):
        text = path.read_text()
        assert all(len(line) <= 120 for line in text.splitlines()), path
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert not any(
                    isinstance(child, (ast.Import, ast.ImportFrom)) for child in ast.walk(node)
                ), (path, node.name)
        assert "__import__(" not in text


def test_explicit_compatibility_loop_and_tool_modules():
    root = Path(__file__).resolve().parents[1] / "src/repofix/harness"
    loop = (root / "loop.py").read_text()
    assert "V1Loop(agent).run()" in loop and "agent._execute_tool =" not in loop
    legacy = (root / "v1.py").read_text()
    assert "LegacyState" not in legacy and "for s.tool_call" not in legacy
    for name in ("shell", "files", "search", "plan", "submit", "agents"):
        assert (root / "tools" / f"{name}.py").is_file()
