"""Registry contracts: entries resolve, preflight returns renderable checks."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tasks.registry import TASKS, get_entry
from tools.toolsets import available_tools, validate_tool_names


class RegistryTests(unittest.TestCase):
    def test_every_entry_is_coherent(self):
        runnable = set(available_tools())
        for task_id, entry in TASKS.items():
            self.assertEqual(entry.task_id, task_id)
            self.assertTrue(entry.policies)
            # default tools must be dispatchable and non-empty.
            self.assertEqual(validate_tool_names(entry.default_tools), entry.default_tools)
            self.assertTrue(set(entry.default_tools) <= runnable)
            self.assertIn("finish", entry.default_tools)

    def test_unknown_task_rejected(self):
        with self.assertRaises(ValueError):
            get_entry("does-not-exist")

    def test_preflight_returns_badge_dicts(self):
        checks = get_entry("toy").preflight({"policy": "scripted"})
        self.assertTrue(checks)
        for check in checks:
            self.assertEqual(set(check), {"name", "ok", "detail"})
            self.assertIsInstance(check["ok"], bool)

    def test_sphinx_preflight_flags_missing_data(self):
        # No data/ dir on this machine: the data-file checks must be red, not raise.
        checks = get_entry("sphinx-7462").preflight(
            {"policy": "reference", "data_dir": "/nonexistent/path"})
        names = {c["name"]: c["ok"] for c in checks}
        self.assertFalse(names["sphinx-base.tar.gz"])
        self.assertFalse(names["record.json"])


if __name__ == "__main__":
    unittest.main()
