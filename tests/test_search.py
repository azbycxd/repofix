from __future__ import annotations

import unittest

from repofix.search import BM25Index, format_search_results, tokenize


class BM25SearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.index, self.stats = BM25Index.from_files(
            {
                "pkg/query.py": (
                    "class QueryCompiler:\n"
                    "    def resolve_lookup(self, lookup):\n"
                    "        return lookup.resolve()\n"
                ),
                "pkg/cache.py": (
                    "def clear_cache(cache):\n"
                    "    cache.clear()\n"
                ),
                "web/widget.js": "function renderWidget() { return 'widget'; }\n",
                "docs/guide.txt": "resolve lookup documentation\n",
            }
        )

    def test_index_uses_code_files_and_existing_chunker(self) -> None:
        self.assertEqual(self.stats.file_count, 3)
        self.assertEqual(self.stats.chunk_count, 4)
        self.assertGreaterEqual(self.stats.build_seconds, 0.0)

    def test_search_returns_ranked_chunk_metadata_and_source(self) -> None:
        results = self.index.search("QueryCompiler resolve lookup", top_k=2)

        self.assertEqual(results[0].chunk.file_path, "pkg/query.py")
        method = next(
            result
            for result in results
            if result.chunk.symbol == "QueryCompiler.resolve_lookup"
        )
        self.assertEqual(method.chunk.chunk_type, "class_method")
        self.assertEqual((method.chunk.start_line, method.chunk.end_line), (2, 3))
        self.assertIn("return lookup.resolve()", method.chunk.source_text)
        self.assertGreater(results[0].score, 0.0)

        formatted = format_search_results(results)
        self.assertIn("score=", formatted)
        self.assertIn("pkg/query.py:2-3", formatted)
        self.assertIn("QueryCompiler.resolve_lookup", formatted)
        self.assertNotIn("return lookup.resolve()", formatted)
        self.assertNotIn("--- source ---", formatted)
        self.assertEqual(len(formatted.splitlines()), 2)

    def test_tokenizer_splits_snake_case_and_camel_case(self) -> None:
        tokens = tokenize("QueryCompiler resolve_lookup")
        self.assertIn("query", tokens)
        self.assertIn("compiler", tokens)
        self.assertIn("resolve", tokens)
        self.assertIn("lookup", tokens)

    def test_invalid_query_and_top_k_are_rejected(self) -> None:
        for query in ("", "   "):
            with self.assertRaises(ValueError):
                self.index.search(query)
        for top_k in (0, -1, True):
            with self.assertRaises(ValueError):
                self.index.search("cache", top_k=top_k)


if __name__ == "__main__":
    unittest.main()
