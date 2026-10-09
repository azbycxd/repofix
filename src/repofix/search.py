"""Dependency-free BM25 search over repository source chunks."""

from __future__ import annotations

import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Mapping, Sequence

from .chunking import Chunk, chunk_source

if TYPE_CHECKING:
    from .env import DockerEnv


BM25_K1 = 1.5
BM25_B = 0.75
SEARCH_DEFAULT_TOP_K = 5
SEARCH_MAX_TOP_K = 20

CODE_SUFFIXES = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".css",
        ".go",
        ".h",
        ".hpp",
        ".html",
        ".java",
        ".js",
        ".jsx",
        ".php",
        ".py",
        ".pyi",
        ".rb",
        ".rs",
        ".scss",
        ".sh",
        ".sql",
        ".toml",
        ".ts",
        ".tsx",
        ".yaml",
        ".yml",
    }
)
CODE_FILENAMES = frozenset({"Dockerfile", "Makefile", "SConstruct"})

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\b)|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


@dataclass(frozen=True)
class SearchResult:
    chunk: Chunk
    score: float


@dataclass(frozen=True)
class IndexStats:
    file_count: int
    chunk_count: int
    build_seconds: float


def tokenize(text: str) -> list[str]:
    """Tokenize prose, paths, snake_case, and CamelCase identifiers."""

    tokens: list[str] = []
    for match in _WORD_RE.finditer(text):
        raw = match.group(0)
        lowered = raw.lower()
        tokens.append(lowered)
        for part in raw.split("_"):
            part_lower = part.lower()
            if part_lower and part_lower != lowered:
                tokens.append(part_lower)
            camel_parts = [item.lower() for item in _CAMEL_RE.findall(part)]
            if len(camel_parts) > 1:
                tokens.extend(camel_parts)
    return tokens


def is_code_path(path: str) -> bool:
    candidate = PurePosixPath(path)
    return candidate.suffix.lower() in CODE_SUFFIXES or candidate.name in CODE_FILENAMES


class BM25Index:
    """An immutable in-memory BM25 index over source chunks."""

    def __init__(
        self,
        chunks: Sequence[Chunk],
        *,
        k1: float = BM25_K1,
        b: float = BM25_B,
    ) -> None:
        if k1 <= 0 or not 0 <= b <= 1:
            raise ValueError("BM25 requires k1 > 0 and 0 <= b <= 1")
        self.chunks = tuple(chunks)
        self.k1 = k1
        self.b = b
        self._term_frequencies: list[Counter[str]] = []
        self._document_lengths: list[int] = []
        document_frequency: Counter[str] = Counter()

        for chunk in self.chunks:
            path_tokens = tokenize(chunk.file_path)
            symbol_tokens = tokenize(chunk.symbol)
            tokens = (
                path_tokens
                + path_tokens
                + symbol_tokens
                + symbol_tokens
                + tokenize(chunk.chunk_type)
                + tokenize(chunk.source_text)
            )
            frequencies = Counter(tokens)
            self._term_frequencies.append(frequencies)
            self._document_lengths.append(len(tokens))
            document_frequency.update(frequencies.keys())

        count = len(self.chunks)
        self._average_document_length = sum(self._document_lengths) / count if count else 0.0
        self._idf = {
            term: math.log(1.0 + (count - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequency.items()
        }

    @classmethod
    def from_files(cls, files: Mapping[str, str]) -> tuple[BM25Index, IndexStats]:
        started = time.monotonic()
        chunks: list[Chunk] = []
        indexed_files = 0
        for path in sorted(files):
            if not is_code_path(path):
                continue
            indexed_files += 1
            chunks.extend(
                chunk_source(
                    files[path],
                    file_path=path,
                    is_python=PurePosixPath(path).suffix.lower() == ".py",
                )
            )
        index = cls(chunks)
        stats = IndexStats(
            file_count=indexed_files,
            chunk_count=len(chunks),
            build_seconds=time.monotonic() - started,
        )
        return index, stats

    @classmethod
    def from_repository(cls, env: DockerEnv) -> tuple[BM25Index, IndexStats]:
        return cls.from_files(env.read_repository_text_files())

    def search(self, query: str, top_k: int = SEARCH_DEFAULT_TOP_K) -> list[SearchResult]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
            raise ValueError("top_k must be a positive integer")
        if not self.chunks:
            return []

        query_terms = set(tokenize(query))
        scored: list[SearchResult] = []
        average_length = self._average_document_length or 1.0
        for chunk, frequencies, document_length in zip(
            self.chunks, self._term_frequencies, self._document_lengths
        ):
            score = 0.0
            normalization = self.k1 * (1.0 - self.b + self.b * document_length / average_length)
            for term in query_terms:
                term_frequency = frequencies.get(term, 0)
                if not term_frequency:
                    continue
                score += self._idf.get(term, 0.0) * (
                    term_frequency * (self.k1 + 1.0) / (term_frequency + normalization)
                )
            if score > 0.0:
                scored.append(SearchResult(chunk=chunk, score=score))

        scored.sort(
            key=lambda result: (
                -result.score,
                result.chunk.file_path,
                result.chunk.start_line,
                result.chunk.symbol,
            )
        )
        return scored[:top_k]


def format_search_results(results: Sequence[SearchResult]) -> str:
    if not results:
        return "No matching code chunks found."
    lines: list[str] = []
    for rank, result in enumerate(results, start=1):
        chunk = result.chunk
        lines.append(
            f"[{rank}] {chunk.file_path}:{chunk.start_line}-{chunk.end_line}  "
            f"{chunk.symbol}  type={chunk.chunk_type}  score={result.score:.6f}"
        )
    return "\n".join(lines)
