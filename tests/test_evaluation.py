from __future__ import annotations

import unittest

from repofix.evaluation import build_evaluation_patch, changed_paths


PRODUCTION_DIFF = """diff --git a/django/forms/models.py b/django/forms/models.py
index 1111111..2222222 100644
--- a/django/forms/models.py
+++ b/django/forms/models.py
@@ -1 +1 @@
-old
+new
"""

TEST_DIFF = """diff --git a/tests/forms_tests/test_models.py b/tests/forms_tests/test_models.py
index 3333333..4444444 100644
--- a/tests/forms_tests/test_models.py
+++ b/tests/forms_tests/test_models.py
@@ -1 +1 @@
-old test
+new test
"""

NEW_TEST_DIFF = """diff --git a/test_regression.py b/test_regression.py
new file mode 100644
index 0000000..5555555
--- /dev/null
+++ b/test_regression.py
@@ -0,0 +1 @@
+assert True
"""


class EvaluationPatchTests(unittest.TestCase):
    def test_filters_test_sections_and_keeps_production_diff(self) -> None:
        result = build_evaluation_patch(PRODUCTION_DIFF + TEST_DIFF + NEW_TEST_DIFF)

        self.assertEqual(result.patch, PRODUCTION_DIFF)
        self.assertEqual(
            result.filtered_test_paths,
            ("tests/forms_tests/test_models.py", "test_regression.py"),
        )

    def test_empty_patch_is_preserved(self) -> None:
        result = build_evaluation_patch("")
        self.assertEqual(result.patch, "")
        self.assertEqual(result.filtered_test_paths, ())

    def test_changed_paths_preserve_diff_order(self) -> None:
        self.assertEqual(
            changed_paths(PRODUCTION_DIFF + TEST_DIFF + NEW_TEST_DIFF),
            (
                "django/forms/models.py",
                "tests/forms_tests/test_models.py",
                "test_regression.py",
            ),
        )


if __name__ == "__main__":
    unittest.main()
