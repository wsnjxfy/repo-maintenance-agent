"""Every tool has a non-negative implementation version."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.specs import TOOL_SPECS
from tools.toolsets import tool_names, tool_versions


class ToolVersionTests(unittest.TestCase):
    def test_every_spec_has_version(self):
        for name, spec in TOOL_SPECS.items():
            self.assertIsInstance(spec.get("version"), int, name)
            self.assertGreaterEqual(spec["version"], 0, name)

    def test_snapshot_only_selected_tools(self):
        names = ["list_files", "finish"]
        self.assertEqual(tool_versions(names),
                         {"list_files": 0, "finish": 0})

    def test_every_toolset_tool_has_version(self):
        for toolset in ("toy-v2", "repo-v1", "repo-v2"):
            for name in tool_names(toolset):
                self.assertIn(name, tool_versions([name]))
