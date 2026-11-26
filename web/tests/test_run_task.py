"""The shared launch CLI drives a real toy run end-to-end (scripted, free)."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent_core.agent import AGENT_VERSION
from scripts import run_task


class RunTaskTests(unittest.TestCase):
    def test_toy_scripted_resolves_and_records_tools_enabled(self):
        with tempfile.TemporaryDirectory() as temp:
            code = run_task.main(["--task", "toy", "--policy", "scripted",
                                  "--output-dir", temp, "--max-steps", "16"])
            self.assertEqual(code, 0)
            summary = sorted(Path(temp).glob("*/summary.json"))[-1]
            data = json.loads(summary.read_text(encoding="utf-8"))
            self.assertTrue(data["resolved"])
            self.assertIn("finish", data["tools_enabled"])
            self.assertEqual(data["toolset"], "toy-v2")
            self.assertEqual(data["max_actions"], 60)
            self.assertEqual(data["agent_version"], AGENT_VERSION)
            self.assertEqual(data["versions"]["agent"], AGENT_VERSION)

    def test_tool_subset_is_honored(self):
        with tempfile.TemporaryDirectory() as temp:
            run_task.main(["--task", "toy", "--policy", "scripted",
                           "--output-dir", temp,
                           "--tools", "list_files,read_lines,replace_text,run_visible_tests,finish"])
            data = json.loads(sorted(Path(temp).glob("*/summary.json"))[-1]
                               .read_text(encoding="utf-8"))
            self.assertNotIn("search_text", data["tools_enabled"])
            self.assertEqual(set(data["tools_enabled"]),
                             {"list_files", "read_lines", "replace_text",
                              "run_visible_tests", "finish"})

    def test_toolset_override_is_honored(self):
        with tempfile.TemporaryDirectory() as temp:
            code = run_task.main(["--task", "toy", "--policy", "scripted",
                                  "--toolset", "repo-v2", "--output-dir", temp,
                                  "--max-steps", "16"])
            self.assertEqual(code, 0)
            data = json.loads(sorted(Path(temp).glob("*/summary.json"))[-1]
                              .read_text(encoding="utf-8"))
            self.assertEqual(data["toolset"], "repo-v2")
            self.assertIn("create_file", data["tools_enabled"])
            self.assertIn("finish", data["tools_enabled"])


if __name__ == "__main__":
    unittest.main()
