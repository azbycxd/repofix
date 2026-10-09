"""Small, dependency-free source chunking helpers."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

FALLBACK_CHUNK_LINES = 50


@dataclass(frozen=True)
class Chunk:
    """A source range that can be indexed or shown to an agent."""

    file_path: str
    symbol: str
    chunk_type: str
    start_line: int
    end_line: int
    source_text: str


def chunk_file(path: str | Path) -> list[Chunk]:
    """Read *path* and split it using Python AST or the line fallback."""

    file_path = Path(path)
    source = file_path.read_text(encoding="utf-8")
    return chunk_source(
        source,
        file_path=file_path.as_posix(),
        is_python=file_path.suffix.lower() == ".py",
    )


def chunk_source(
    source: str,
    *,
    file_path: str,
    is_python: bool | None = None,
) -> list[Chunk]:
    """Chunk source text while preserving exact one-based source ranges.

    Python files are split into top-level functions, top-level classes, and
    direct methods of each top-level class. Non-Python files and Python files
    that fail AST parsing use fixed 50-line blocks.
    """

    if is_python is None:
        is_python = Path(file_path).suffix.lower() == ".py"

    if not is_python:
        return _line_chunks(source, file_path)

    try:
        tree = ast.parse(source, filename=file_path)
    except (SyntaxError, ValueError):
        return _line_chunks(source, file_path)

    source_lines = source.splitlines(keepends=True)
    chunks: list[Chunk] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chunks.append(
                _ast_chunk(
                    node,
                    file_path=file_path,
                    symbol=node.name,
                    chunk_type="top_level_function",
                    source_lines=source_lines,
                )
            )
            continue

        if not isinstance(node, ast.ClassDef):
            continue

        chunks.append(
            _ast_chunk(
                node,
                file_path=file_path,
                symbol=node.name,
                chunk_type="top_level_class",
                source_lines=source_lines,
            )
        )
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                chunks.append(
                    _ast_chunk(
                        child,
                        file_path=file_path,
                        symbol=f"{node.name}.{child.name}",
                        chunk_type="class_method",
                        source_lines=source_lines,
                    )
                )

    return chunks


def _ast_chunk(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    *,
    file_path: str,
    symbol: str,
    chunk_type: str,
    source_lines: list[str],
) -> Chunk:
    start_line = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
    end_line = node.end_lineno or node.lineno
    return Chunk(
        file_path=file_path,
        symbol=symbol,
        chunk_type=chunk_type,
        start_line=start_line,
        end_line=end_line,
        source_text="".join(source_lines[start_line - 1 : end_line]),
    )


def _line_chunks(source: str, file_path: str) -> list[Chunk]:
    source_lines = source.splitlines(keepends=True)
    chunks: list[Chunk] = []
    for offset in range(0, len(source_lines), FALLBACK_CHUNK_LINES):
        start_line = offset + 1
        end_line = min(offset + FALLBACK_CHUNK_LINES, len(source_lines))
        chunks.append(
            Chunk(
                file_path=file_path,
                symbol=f"lines_{start_line}_{end_line}",
                chunk_type="line_block",
                start_line=start_line,
                end_line=end_line,
                source_text="".join(source_lines[offset:end_line]),
            )
        )
    return chunks
