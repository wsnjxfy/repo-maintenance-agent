"""Offline checks for the edit reminder's tool-observation rules."""

import unittest

from experiments.sphinx_7462.edit_transition import EditTransition


def read(transition, path, start, end):
    action = {"tool": "read_lines", "args": {"path": path, "start": start, "end": end}}
    result = {"path": path, "lines": [{"line": line, "text": "value = 1"}
                                      for line in range(start, end + 1)]}
    return transition.observe(action, {"status": "ok", "result": result})


def search(transition, query, *, matches=0, **args):
    action = {"tool": "search_text", "args": {"query": query, **args}}
    result = {"total": matches, "matches": [{"path": "lib/module.py", "line": 1, "text": query}]
              if matches else []}
    return transition.observe(action, {"status": "ok", "result": result})


class EditTransitionTests(unittest.TestCase):
    def test_empty_search_and_read_redundancy_share_threshold_and_trigger_once(self):
        transition = EditTransition("replace_text")
        self.assertIsNone(read(transition, "lib/module.py", 1, 3))
        self.assertIsNone(search(transition, "missing"))
        self.assertIsNone(search(transition, "missing", path=".", case_sensitive=True, limit=10))
        self.assertEqual(read(transition, "lib/module.py", 1, 3), "repeated_read")
        self.assertIsNone(read(transition, "lib/module.py", 1, 3))
        self.assertIsNone(search(transition, "missing"))

    def test_partial_overlap_and_different_files_are_new_reads(self):
        transition = EditTransition("replace_text")
        self.assertIsNone(read(transition, "lib/module.py", 1, 3))
        self.assertIsNone(read(transition, "lib/module.py", 3, 5))
        self.assertIsNone(read(transition, "lib/other.py", 1, 3))
        self.assertIsNone(read(transition, "lib/module.py", 2, 4))
        self.assertEqual(read(transition, "lib/module.py", 2, 4), "repeated_read")

    def test_empty_search_uses_query_scope_and_canonical_defaults(self):
        transition = EditTransition("replace_text")
        self.assertIsNone(read(transition, "lib/module.py", 1, 3))
        self.assertIsNone(search(transition, "missing"))
        self.assertIsNone(search(transition, "missing", path="lib"))
        self.assertIsNone(search(transition, "missing", case_sensitive=False))
        self.assertIsNone(search(transition, "found", matches=1))
        self.assertIsNone(search(transition, "found", matches=1))
        self.assertIsNone(search(transition, "missing", path=".", case_sensitive=True, limit=10))
        self.assertEqual(search(transition, "missing", path="lib", limit=20), "repeated_empty_search")

    def test_test_files_and_failed_calls_do_not_start_or_count_toward_trigger(self):
        transition = EditTransition("replace_text")
        failed = {"status": "error", "error": "simulated failure"}
        for path in ("tests/test_module.py", "lib/test_module.py", "README.md"):
            for _ in range(3):
                self.assertIsNone(read(transition, path, 1, 3))
        self.assertIsNone(read(transition, "lib/module.py", 1, 3))
        failed_actions = (
            {"tool": "read_lines", "args": {"path": "lib/module.py", "start": 1, "end": 3}},
            {"tool": "search_text", "args": {"query": "missing"}},
            {"tool": "replace_text", "args": {"path": "lib/module.py", "old": "absent", "new": "changed"}},
        )
        for action in failed_actions:
            self.assertIsNone(transition.observe(action, failed))
        self.assertIsNone(read(transition, "lib/module.py", 1, 3))
        self.assertEqual(read(transition, "lib/module.py", 1, 3), "repeated_read")

    def test_successful_edit_disables_the_trigger(self):
        for edit_tool, args in (
            ("replace_text", {"path": "lib/module.py", "old": "value = 1", "new": "value = 2"}),
            ("apply_patch", {"patch": "fixture patch"}),
        ):
            with self.subTest(edit_tool=edit_tool):
                transition = EditTransition(edit_tool)
                self.assertIsNone(read(transition, "lib/module.py", 1, 3))
                self.assertIsNone(read(transition, "lib/module.py", 1, 3))
                edit = {"tool": edit_tool, "args": args}
                result = {"status": "ok", "result": {"changed_paths": ["lib/module.py"]}}
                self.assertIsNone(transition.observe(edit, result))
                for _ in range(3):
                    self.assertIsNone(read(transition, "lib/module.py", 1, 3))
