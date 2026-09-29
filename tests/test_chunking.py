from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from repofix.chunking import FALLBACK_CHUNK_LINES, chunk_file, chunk_source


class ChunkingTests(unittest.TestCase):
    def test_python_ast_chunks_functions_classes_and_methods(self) -> None:
        source = """\
import os

@decorator
def top_level(value):
    def nested():
        return value
    return nested()

class Example:
    field = 1

    def method(self):
        return self.field

    async def async_method(self):
        return self.field

async def async_top_level():
    return os.getcwd()
"""

        chunks = chunk_source(source, file_path="pkg/example.py")
        by_symbol = {chunk.symbol: chunk for chunk in chunks}

        self.assertEqual(FALLBACK_CHUNK_LINES, 50)
        self.assertEqual(
            set(by_symbol),
            {
                "top_level",
                "Example",
                "Example.method",
                "Example.async_method",
                "async_top_level",
            },
        )
        self.assertEqual(by_symbol["top_level"].chunk_type, "top_level_function")
        self.assertEqual((by_symbol["top_level"].start_line, by_symbol["top_level"].end_line), (3, 7))
        self.assertTrue(by_symbol["top_level"].source_text.startswith("@decorator\n"))
        self.assertNotIn("nested", by_symbol)

        self.assertEqual(by_symbol["Example"].chunk_type, "top_level_class")
        self.assertEqual((by_symbol["Example"].start_line, by_symbol["Example"].end_line), (9, 16))
        self.assertEqual(by_symbol["Example.method"].chunk_type, "class_method")
        self.assertEqual((by_symbol["Example.method"].start_line, by_symbol["Example.method"].end_line), (12, 13))
        self.assertIn("async def async_method", by_symbol["Example.async_method"].source_text)
        self.assertEqual(by_symbol["async_top_level"].chunk_type, "top_level_function")
        self.assertTrue(all(chunk.file_path == "pkg/example.py" for chunk in chunks))

    def test_invalid_python_uses_fixed_line_fallback(self) -> None:
        source = "".join(f"line {number}\n" for number in range(1, 121))

        chunks = chunk_source(source, file_path="broken.py")

        self.assertEqual(
            [(chunk.start_line, chunk.end_line) for chunk in chunks],
            [(1, 50), (51, 100), (101, 120)],
        )
        self.assertTrue(all(chunk.chunk_type == "line_block" for chunk in chunks))
        self.assertTrue(chunks[0].source_text.startswith("line 1\n"))
        self.assertTrue(chunks[0].source_text.endswith("line 50\n"))
        self.assertTrue(chunks[-1].source_text.endswith("line 120\n"))

    def test_non_python_file_uses_fixed_line_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notes.txt"
            path.write_text("".join(f"item {number}\n" for number in range(1, 52)), encoding="utf-8")

            chunks = chunk_file(path)

        self.assertEqual(len(chunks), 2)
        self.assertEqual((chunks[0].start_line, chunks[0].end_line), (1, 50))
        self.assertEqual((chunks[1].start_line, chunks[1].end_line), (51, 51))
        self.assertEqual(chunks[1].source_text, "item 51\n")
        self.assertTrue(all(chunk.file_path.endswith("notes.txt") for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
