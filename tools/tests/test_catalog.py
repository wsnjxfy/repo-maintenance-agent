"""Catalog and toolset contracts (no demo/backend dependency)."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.handlers import HANDLERS
from tools.specs import TOOL_SPECS
from tools.toolsets import (
    TOOLSETS, available_tools, get_toolset, tool_labels, tool_labels_for,
    tool_names, tool_table, tool_table_for, validate_tool_names,
)


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

    def test_handlerless_legacy_specs_are_removed(self):
        available = available_tools()
        self.assertEqual(set(available), {name for name, spec in TOOL_SPECS.items()
                                          if spec["handler"] in HANDLERS})
        for dead in ("read_file", "plan", "run_tests"):
            self.assertNotIn(dead, TOOL_SPECS)
            self.assertNotIn(dead, available)
        # The newly implemented tools are dispatchable.
        for live in ("map_repository", "find_symbol", "search_regex",
                     "create_file", "edit_lines", "revert_file", "show_diff"):
            self.assertIn(live, available)

    def test_show_diff_documents_its_staging_side_effect(self):
        spec = TOOL_SPECS["show_diff"]
        self.assertTrue(spec["mutating"])
        self.assertIn("git add", spec["doc"])
        self.assertIn("stage", spec["doc"])

    def test_validate_tool_names_gates_the_subset(self):
        subset = ["list_files", "finish"]
        self.assertEqual(validate_tool_names(subset), subset)
        with self.assertRaises(ValueError):
            validate_tool_names([])
        with self.assertRaises(ValueError):
            validate_tool_names(["finish", "finish"])
        with self.assertRaises(ValueError):
            validate_tool_names(["does_not_exist"])

    def test_list_based_rendering_matches_toolset_rendering(self):
        names = tool_names("repo-v1")
        self.assertEqual(tool_table_for(names), tool_table("repo-v1"))
        self.assertEqual(tool_labels_for(names), tool_labels("repo-v1"))
        subset = ["list_files", "finish"]
        table = tool_table_for(subset)
        self.assertIn("list_files", table)
        self.assertNotIn("apply_patch", table)

    def test_toy_v2_surface(self):
        names = tool_names("toy-v2")
        self.assertEqual(names, ["list_files", "read_lines", "replace_text",
                                 "run_visible_tests", "finish"])
        self.assertEqual(set(tool_labels("toy-v2")), set(names))
        table = tool_table("toy-v2")
        for name in names:
            self.assertIn(name, table)
            self.assertIn(TOOL_SPECS[name]["doc"], table)
