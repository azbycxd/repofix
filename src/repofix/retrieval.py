"""Cached dense retrieval and reciprocal-rank fusion for code chunks."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Protocol, Sequence

import numpy as np
from fastembed import TextEmbedding

from .chunking import Chunk
from .search import BM25Index, IndexStats, SearchResult

if TYPE_CHECKING:
    from .env import DockerEnv


EMBEDDING_LIBRARY = "fastembed"
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIMENSION = 384
EMBEDDING_BATCH_SIZE = 256
EMBEDDING_THREADS = 4
DENSE_SOURCE_MAX_CHARS = 512
RRF_K = 60
RRF_RANK_WINDOW = 60
CACHE_FORMAT_VERSION = "repofix-dense-chunk-v1"
CACHE_LOOKUP_BATCH_SIZE = 500
DEFAULT_CACHE_ROOT = Path(__file__).resolve().parents[2] / ".cache" / "repofix"


class EmbeddingBackend(Protocol):
    model_id: str
    dimension: int

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class FastEmbedBackend:
    model_id = EMBEDDING_MODEL
    dimension = EMBEDDING_DIMENSION

    def __init__(self, model_cache: Path) -> None:
        model_cache.mkdir(parents=True, exist_ok=True)
        self.model = TextEmbedding(
            model_name=self.model_id,
            cache_dir=str(model_cache),
            threads=EMBEDDING_THREADS,
        )

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        vectors = np.asarray(
            list(self.model.embed(texts, batch_size=EMBEDDING_BATCH_SIZE)),
            dtype=np.float32,
        )
        return _normalize_rows(vectors)

    def embed_query(self, text: str) -> np.ndarray:
        vectors = np.asarray(list(self.model.query_embed(text)), dtype=np.float32)
        return _normalize_rows(vectors)[0]


_BACKENDS: dict[str, FastEmbedBackend] = {}


def get_embedding_backend(cache_root: Path = DEFAULT_CACHE_ROOT) -> FastEmbedBackend:
    key = str(cache_root.resolve())
    if key not in _BACKENDS:
        _BACKENDS[key] = FastEmbedBackend(cache_root / "models")
    return _BACKENDS[key]


@dataclass(frozen=True)
class DenseIndexStats:
    build_seconds: float
    cache_hit: bool
    requested_chunk_count: int
    unique_chunk_count: int
    cache_hit_count: int
    embedded_count: int
    cache_path: str


@dataclass(frozen=True)
class HybridIndexStats:
    file_count: int
    chunk_count: int
    bm25_build_seconds: float
    dense_build_seconds: float
    total_build_seconds: float
    dense_cache_hit: bool
    dense_requested_chunk_count: int
    dense_unique_chunk_count: int
    dense_cache_hit_count: int
    dense_embedded_count: int
    dense_cache_path: str
    embedding_library: str
    embedding_model: str
    embedding_dimension: int
    rrf_k: int
    rrf_rank_window: int


@dataclass(frozen=True)
class FusedSearchResult:
    chunk: Chunk
    score: float
    bm25_rank: int | None
    dense_rank: int | None


def _normalize_rows(vectors: np.ndarray) -> np.ndarray:
    if vectors.ndim != 2:
        raise ValueError("embedding matrix must have two dimensions")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def _document_text(chunk: Chunk) -> str:
    source = chunk.source_text[:DENSE_SOURCE_MAX_CHARS]
    return (
        f"passage: file {chunk.file_path}\n"
        f"symbol {chunk.symbol}\n"
        f"type {chunk.chunk_type}\n"
        f"{source}"
    )


def short_query_proxy(problem_statement: str) -> str:
    """Use the first non-empty issue line as a deterministic short query."""
    for line in problem_statement.splitlines():
        cleaned = re.sub(r"^[#>*\-\s]+", "", line).strip()
        if cleaned:
            return cleaned[:500]
    return problem_statement.strip()[:500]


def _chunk_cache_key(document: str, model_id: str) -> str:
    digest = hashlib.sha256()
    digest.update(CACHE_FORMAT_VERSION.encode())
    digest.update(model_id.encode())
    payload = document.encode("utf-8")
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)
    return digest.hexdigest()


def _batched(values: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def _prepare_chunk_cache(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS embeddings (
            cache_key TEXT PRIMARY KEY,
            dimension INTEGER NOT NULL,
            vector BLOB NOT NULL
        ) WITHOUT ROWID
        """
    )
    connection.commit()


class DenseIndex:
    def __init__(
        self,
        chunks: Sequence[Chunk],
        vectors: np.ndarray,
        backend: EmbeddingBackend,
    ) -> None:
        if vectors.shape != (len(chunks), backend.dimension):
            raise ValueError(
                f"dense matrix shape {vectors.shape} does not match "
                f"({len(chunks)}, {backend.dimension})"
            )
        self.chunks = tuple(chunks)
        self.vectors = np.asarray(vectors, dtype=np.float32)
        self.backend = backend

    @classmethod
    def from_chunks(
        cls,
        chunks: Sequence[Chunk],
        *,
        cache_root: Path = DEFAULT_CACHE_ROOT,
        backend: EmbeddingBackend | None = None,
    ) -> tuple[DenseIndex, DenseIndexStats]:
        started = time.monotonic()
        backend = backend or get_embedding_backend(cache_root)
        cache_dir = (
            cache_root
            / "dense_chunks"
            / backend.model_id.replace("/", "--")
        )
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{CACHE_FORMAT_VERSION}.sqlite3"

        documents_by_key: dict[str, str] = {}
        positions_by_key: dict[str, list[int]] = {}
        for position, chunk in enumerate(chunks):
            document = _document_text(chunk)
            cache_key = _chunk_cache_key(document, backend.model_id)
            documents_by_key.setdefault(cache_key, document)
            positions_by_key.setdefault(cache_key, []).append(position)

        unique_keys = list(documents_by_key)
        vectors = np.empty((len(chunks), backend.dimension), dtype=np.float32)
        cached_keys: set[str] = set()
        embedded_count = 0

        with sqlite3.connect(cache_path, timeout=60) as connection:
            _prepare_chunk_cache(connection)
            for key_batch in _batched(unique_keys, CACHE_LOOKUP_BATCH_SIZE):
                placeholders = ",".join("?" for _ in key_batch)
                rows = connection.execute(
                    f"SELECT cache_key, dimension, vector FROM embeddings "
                    f"WHERE cache_key IN ({placeholders})",
                    tuple(key_batch),
                ).fetchall()
                invalid_keys: list[str] = []
                for cache_key, dimension, vector_blob in rows:
                    vector = np.frombuffer(vector_blob, dtype=np.float32)
                    if dimension != backend.dimension or vector.size != backend.dimension:
                        invalid_keys.append(cache_key)
                        continue
                    for position in positions_by_key[cache_key]:
                        vectors[position] = vector
                    cached_keys.add(cache_key)
                if invalid_keys:
                    connection.executemany(
                        "DELETE FROM embeddings WHERE cache_key = ?",
                        ((cache_key,) for cache_key in invalid_keys),
                    )
                    connection.commit()

            missing_keys = [
                cache_key for cache_key in unique_keys if cache_key not in cached_keys
            ]
            for key_batch in _batched(missing_keys, EMBEDDING_BATCH_SIZE):
                documents = [documents_by_key[cache_key] for cache_key in key_batch]
                embedded = np.asarray(
                    backend.embed_documents(documents), dtype=np.float32
                )
                expected_shape = (len(key_batch), backend.dimension)
                if embedded.shape != expected_shape:
                    raise ValueError(
                        f"embedding batch shape {embedded.shape} does not match "
                        f"{expected_shape}"
                    )
                connection.executemany(
                    "INSERT OR REPLACE INTO embeddings "
                    "(cache_key, dimension, vector) VALUES (?, ?, ?)",
                    (
                        (
                            cache_key,
                            backend.dimension,
                            sqlite3.Binary(embedded[index].tobytes()),
                        )
                        for index, cache_key in enumerate(key_batch)
                    ),
                )
                connection.commit()
                for index, cache_key in enumerate(key_batch):
                    for position in positions_by_key[cache_key]:
                        vectors[position] = embedded[index]
                embedded_count += len(key_batch)

        index = cls(chunks, vectors, backend)
        return index, DenseIndexStats(
            build_seconds=time.monotonic() - started,
            cache_hit=embedded_count == 0,
            requested_chunk_count=len(chunks),
            unique_chunk_count=len(unique_keys),
            cache_hit_count=len(cached_keys),
            embedded_count=embedded_count,
            cache_path=str(cache_path),
        )

    def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
            raise ValueError("top_k must be a positive integer")
        if not self.chunks:
            return []
        query_vector = self.backend.embed_query(query)
        scores = self.vectors @ query_vector
        count = min(top_k, len(self.chunks))
        positions = np.argsort(-scores, kind="stable")[:count]
        return [
            SearchResult(chunk=self.chunks[int(position)], score=float(scores[position]))
            for position in positions
        ]


class HybridCodeIndex:
    def __init__(self, bm25: BM25Index, dense: DenseIndex) -> None:
        if bm25.chunks != dense.chunks:
            raise ValueError("BM25 and dense indexes must use identical chunks")
        self.bm25 = bm25
        self.dense = dense
        self.chunks = bm25.chunks

    @classmethod
    def from_files(
        cls,
        files: dict[str, str],
        *,
        cache_root: Path = DEFAULT_CACHE_ROOT,
        backend: EmbeddingBackend | None = None,
    ) -> tuple[HybridCodeIndex, HybridIndexStats]:
        started = time.monotonic()
        bm25, bm25_stats = BM25Index.from_files(files)
        dense, dense_stats = DenseIndex.from_chunks(
            bm25.chunks, cache_root=cache_root, backend=backend
        )
        index = cls(bm25, dense)
        return index, HybridIndexStats(
            file_count=bm25_stats.file_count,
            chunk_count=bm25_stats.chunk_count,
            bm25_build_seconds=bm25_stats.build_seconds,
            dense_build_seconds=dense_stats.build_seconds,
            total_build_seconds=time.monotonic() - started,
            dense_cache_hit=dense_stats.cache_hit,
            dense_requested_chunk_count=dense_stats.requested_chunk_count,
            dense_unique_chunk_count=dense_stats.unique_chunk_count,
            dense_cache_hit_count=dense_stats.cache_hit_count,
            dense_embedded_count=dense_stats.embedded_count,
            dense_cache_path=dense_stats.cache_path,
            embedding_library=EMBEDDING_LIBRARY,
            embedding_model=dense.backend.model_id,
            embedding_dimension=dense.backend.dimension,
            rrf_k=RRF_K,
            rrf_rank_window=RRF_RANK_WINDOW,
        )

    @classmethod
    def from_repository(
        cls,
        env: DockerEnv,
        *,
        cache_root: Path = DEFAULT_CACHE_ROOT,
        backend: EmbeddingBackend | None = None,
    ) -> tuple[HybridCodeIndex, HybridIndexStats]:
        return cls.from_files(
            env.read_repository_text_files(),
            cache_root=cache_root,
            backend=backend,
        )

    def search(self, query: str, top_k: int = 5) -> list[FusedSearchResult]:
        window = min(RRF_RANK_WINDOW, len(self.chunks))
        bm25_results = self.bm25.search(query, top_k=window)
        dense_results = self.dense.search(query, top_k=window)
        ranks: dict[Chunk, dict[str, int]] = {}
        for rank, result in enumerate(bm25_results, start=1):
            ranks.setdefault(result.chunk, {})["bm25"] = rank
        for rank, result in enumerate(dense_results, start=1):
            ranks.setdefault(result.chunk, {})["dense"] = rank

        fused: list[FusedSearchResult] = []
        for chunk, chunk_ranks in ranks.items():
            bm25_rank = chunk_ranks.get("bm25")
            dense_rank = chunk_ranks.get("dense")
            score = 0.0
            if bm25_rank is not None:
                score += 1.0 / (RRF_K + bm25_rank)
            if dense_rank is not None:
                score += 1.0 / (RRF_K + dense_rank)
            fused.append(
                FusedSearchResult(
                    chunk=chunk,
                    score=score,
                    bm25_rank=bm25_rank,
                    dense_rank=dense_rank,
                )
            )
        fused.sort(
            key=lambda result: (
                -result.score,
                result.chunk.file_path,
                result.chunk.start_line,
                result.chunk.symbol,
            )
        )
        return fused[:top_k]


def format_fused_results(results: Sequence[FusedSearchResult]) -> str:
    if not results:
        return "No matching code chunks found."
    lines: list[str] = []
    for rank, result in enumerate(results, start=1):
        chunk = result.chunk
        bm25_rank = result.bm25_rank if result.bm25_rank is not None else "-"
        dense_rank = result.dense_rank if result.dense_rank is not None else "-"
        lines.append(
            f"[{rank}] {chunk.file_path}:{chunk.start_line}-{chunk.end_line}  "
            f"{chunk.symbol}  type={chunk.chunk_type}  rrf={result.score:.6f}  "
            f"bm25_rank={bm25_rank}  dense_rank={dense_rank}"
        )
    return "\n".join(lines)
