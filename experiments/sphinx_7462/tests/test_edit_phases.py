"""Offline checks for the staged editing policy and its budgets."""

import unittest

from experiments.sphinx_7462.edit_phases import EditPhases


TOOLS = {name: name for name in (
    "list_files", "search_text", "read_lines", "replace_text", "run_visible_tests", "finish",
)}


def plan(policy, calls=0, actions=0):
    return policy.plan(calls, actions, 40, 30, TOOLS)


class EditPhasesTests(unittest.TestCase):
    def test_location_ends_at_the_first_limit(self):
        for calls, actions in ((10, 8), (12, 3)):
            with self.subTest(calls=calls, actions=actions):
                policy = EditPhases()
                initial_phase, initial_tools, _ = plan(policy, calls=7, actions=5)
                self.assertEqual(initial_phase, "locate")
                self.assertIn("search_text", initial_tools)
                self.assertNotIn("replace_text", initial_tools)
                self.assertNotIn("run_visible_tests", initial_tools)
                phase, tools, message = plan(policy, calls, actions)
                self.assertEqual(phase, "edit")
                self.assertIn("replace_text", tools)
                self.assertIn("read_lines", tools)
                self.assertNotIn("search_text", tools)
                self.assertNotIn("list_files", tools)
                self.assertTrue(message)

    def test_edit_allows_at_most_two_directed_reads(self):
        policy = EditPhases()
        self.assertEqual(plan(policy, 10, 8)[0], "edit")
        for index, response in enumerate((
            {"status": "ok", "result": {"lines": []}},
            {"status": "error", "error": "read failed"},
        )):
            self.assertIn("read_lines", plan(policy, 10 + index, 8 + index)[1])
            policy.observe("read_lines", response)
        self.assertEqual(policy.directed_reads, 2)
        phase, tools, _ = plan(policy, 12, 10)
        self.assertEqual(phase, "edit")
        self.assertNotIn("read_lines", tools)
        self.assertIn("replace_text", tools)
        policy.observe("replace_text", {"status": "ok", "result": {"changed_paths": ["module.py"]}})
        self.assertEqual(plan(policy, 13, 11)[0], "verify")
        policy.observe("run_visible_tests", {"status": "ok", "result": {"status": "passed"}})
        self.assertNotIn("read_lines", plan(policy, 14, 12)[1])

    def test_either_budget_reserves_verification(self):
        for calls, actions in ((36, 13), (24, 28)):
            with self.subTest(calls=calls, actions=actions):
                policy = EditPhases()
                self.assertEqual(plan(policy, 10, 8)[0], "edit")
                phase, tools, _ = plan(policy, calls, actions)
                self.assertEqual(phase, "verify")
                self.assertIn("run_visible_tests", tools)
                self.assertNotIn("replace_text", tools)
                self.assertNotIn("read_lines", tools)
                self.assertNotIn("search_text", tools)
                policy.observe("run_visible_tests", {"status": "ok", "result": {"status": "passed"}})
                self.assertTrue(policy.tested)
                self.assertEqual(plan(policy, calls + 1, actions + 1)[0], "verify")

    def test_successful_edit_requires_a_visible_test_before_more_edits(self):
        policy = EditPhases()
        self.assertEqual(plan(policy, 10, 8)[0], "edit")
        policy.observe("replace_text", {"status": "error", "error": "old text missing"})
        self.assertEqual(plan(policy, 11, 9)[0], "edit")
        policy.observe("replace_text", {"status": "ok", "result": {"changed_paths": ["module.py"]}})
        phase, tools, _ = plan(policy, 12, 10)
        self.assertEqual(phase, "verify")
        self.assertIn("run_visible_tests", tools)
        self.assertNotIn("replace_text", tools)
        policy.observe("run_visible_tests", {"status": "error", "error": "test path missing"})
        self.assertFalse(policy.tested)
        self.assertNotIn("replace_text", plan(policy, 13, 11)[1])
        policy.observe("run_visible_tests", {"status": "ok", "result": {"status": "failed"}})
        self.assertTrue(policy.tested)
        phase, tools, _ = plan(policy, 14, 12)
        self.assertEqual(phase, "edit")
        self.assertIn("replace_text", tools)
        self.assertIn("run_visible_tests", tools)
        self.assertNotIn("search_text", tools)
        self.assertNotIn("list_files", tools)
