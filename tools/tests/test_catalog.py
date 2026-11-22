"""Catalog and toolset contracts (no demo/backend dependency)."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.specs import TOOL_SPECS
from tools.toolsets import TOOLSETS, get_toolset, tool_labels, tool_names, tool_table


class CatalogTests(unittest.TestCase):
    def test_every_toolset_tool_is_defined(self):
        for name, entry in TOOLSETS.items():
            self.assertTrue(entry["tools"], name)
            self.assertEqual(len(set(entry["tools"])), len(entry["tools"]), name)
            self.assertTrue(set(entry["tools"]) <= set(TOOL_SPECS), name)

    def test_specs_carry_handler_and_audit_metadata(self):
        for name, spec in TOOL_SPECS.items():
            self.assertIn("handler", spec, name)
            self.assertIn("phase", spec, name)
            self.assertIn("executes_code", spec, name)

    def test_unknown_toolset_is_rejected(self):
        with self.assertRaises(ValueError):
            get_toolset("does-not-exist")

    def test_toy_v2_surface(self):
        names = tool_names("toy-v2")
        self.assertEqual(names, ["list_files", "read_lines", "apply_patch",
                                 "run_visible_tests", "finish"])
        self.assertEqual(set(tool_labels("toy-v2")), set(names))
        table = tool_table("toy-v2")
        for name in names:
            self.assertIn(name, table)
            self.assertIn(TOOL_SPECS[name]["doc"], table)
