from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from repofix.retrieval import (
    HybridCodeIndex,
    format_fused_results,
    short_query_proxy,
)


class FakeEmbeddingBackend:
    model_id = "test/fake-embedding"
    dimension = 3

    @staticmethod
    def _vector(text: str) -> np.ndarray:
        lowered = text.lower()
        return np.asarray(
            [
                lowered.count("query") + lowered.count("lookup"),
                lowered.count("cache"),
                lowered.count("widget"),
            ],
            dtype=np.float32,
        )

    def embed_documents(self, texts):
        vectors = np.vstack([self._vector(text) for text in texts])
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return vectors / norms

    def embed_query(self, text):
        vector = self._vector(text)
        norm = np.linalg.norm(vector)
        return vector / (norm or 1)


class HybridRetrievalTests(unittest.TestCase):
    def test_short_query_proxy_uses_first_nonempty_issue_line(self) -> None:
        issue = "\n## Fix QueryCompiler lookup\n\nLonger details mentioning gold."
        self.assertEqual(short_query_proxy(issue), "Fix QueryCompiler lookup")

    def test_dense_cache_and_rrf_position_only_output(self) -> None:
        files = {
            "pkg/query.py": "def resolve_lookup(query):\n    return query.resolve()\n",
            "pkg/cache.py": "def clear_cache(cache):\n    cache.clear()\n",
            "web/widget.js": "function renderWidget() { return 'widget'; }\n",
        }
        backend = FakeEmbeddingBackend()
        with tempfile.TemporaryDirectory() as directory:
            cache_root = Path(directory)
            first, first_stats = HybridCodeIndex.from_files(
                files, cache_root=cache_root, backend=backend
            )
            second, second_stats = HybridCodeIndex.from_files(
                files, cache_root=cache_root, backend=backend
            )

        self.assertFalse(first_stats.dense_cache_hit)
        self.assertTrue(second_stats.dense_cache_hit)
        self.assertEqual(first_stats.dense_cache_key, second_stats.dense_cache_key)
        results = second.search("query lookup", top_k=2)
        self.assertEqual(results[0].chunk.file_path, "pkg/query.py")
        self.assertIsNotNone(results[0].bm25_rank)
        self.assertIsNotNone(results[0].dense_rank)

        rendered = format_fused_results(results)
        self.assertIn("rrf=", rendered)
        self.assertIn("bm25_rank=", rendered)
        self.assertIn("dense_rank=", rendered)
        self.assertNotIn("return query.resolve()", rendered)
        self.assertEqual(len(rendered.splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
