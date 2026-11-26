"""Handlers added for navigation, finer edits, recovery and self-review."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from runtime.local import LocalEnvironment
from tools.handlers import HandlerContext, dispatch
from tools.specs import TOOL_SPECS


MOD = (
    "import os\n\n\n"
    "def resolve_timeout(config_value=None, cli_value=None):\n"
    "    return 5\n\n\n"
    "class Downloader:\n"
    "    def fetch(self, url):\n"
    "        return {\"url\": url, \"timeout\": 5}\n"
)


class NewHandlerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hj10-newtools-")
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        (self.workspace / "pkg").mkdir(parents=True)
        (self.workspace / "pkg" / "mod.py").write_text(MOD, encoding="utf-8")
        (self.workspace / "README.md").write_text("Timeout docs\n", encoding="utf-8")
        for command in (
            ["git", "init", "-q"],
            ["git", "config", "user.email", "t@example.test"],
            ["git", "config", "user.name", "tester"],
            ["git", "add", "-A"],
            ["git", "commit", "-qm", "baseline"],
        ):
            subprocess.run(command, cwd=self.workspace, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.env = LocalEnvironment(self.workspace, scratch=root / "scratch")
        self.ctx = HandlerContext(specs=dict(TOOL_SPECS), allowed=set(TOOL_SPECS),
                                  outputs=root / "outputs", python=sys.executable,
                                  test_timeout=90)
        self.ctx.outputs.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def run_ok(self, tool, args):
        obs = dispatch(tool, self.env, self.ctx, args)
        self.assertTrue(obs["ok"], obs)
        return obs

    def read(self, rel):
        return (self.workspace / rel).read_text(encoding="utf-8")

    # --- map_repository ----------------------------------------------------
    def test_map_repository_lists_functions_and_classes(self):
        result = self.run_ok("map_repository", {})["result"]
        mod = result["files"]["pkg/mod.py"]
        kinds = {item["name"]: item for item in mod}
        self.assertEqual(kinds["resolve_timeout"]["kind"], "function")
        self.assertEqual(kinds["Downloader"]["kind"], "class")
        self.assertIn("fetch", [m["name"] for m in kinds["Downloader"]["methods"]])

    # --- find_symbol -------------------------------------------------------
    def test_find_symbol_locates_definition_and_references(self):
        result = self.run_ok("find_symbol", {"symbol": "resolve_timeout"})["result"]
        self.assertEqual(result["definitions"][0]["path"], "pkg/mod.py")
        self.assertEqual(result["definitions"][0]["kind"], "function")
        self.assertGreaterEqual(result["reference_count"], 1)

    def test_find_symbol_rejects_non_identifier(self):
        obs = dispatch("find_symbol", self.env, self.ctx, {"symbol": "a.b("})
        self.assertFalse(obs["ok"])

    # --- search_regex ------------------------------------------------------
    def test_search_regex_matches_pattern(self):
        result = self.run_ok("search_regex", {"pattern": r"def \w+_timeout"})["result"]
        self.assertTrue(any(m["path"] == "pkg/mod.py" for m in result["matches"]))

    def test_search_regex_rejects_invalid_pattern(self):
        obs = dispatch("search_regex", self.env, self.ctx, {"pattern": "("})
        self.assertFalse(obs["ok"])
        self.assertIn("Invalid regular expression", obs["error"])

    def test_search_case_sensitive_must_be_bool(self):
        for tool, args in (("search_text", {"query": "Timeout"}),
                           ("search_regex", {"pattern": "Timeout"})):
            with self.subTest(tool=tool):
                obs = dispatch(tool, self.env, self.ctx,
                               {**args, "case_sensitive": "false"})
                self.assertFalse(obs["ok"])
                self.assertIn("case_sensitive must be a bool", obs["error"])

    # --- create_file -------------------------------------------------------
    def test_create_file_writes_new_file(self):
        obs = self.run_ok("create_file", {"path": "pkg/new.py", "content": "VALUE = 1\n"})
        self.assertEqual(obs["changed_paths"], ["pkg/new.py"])
        self.assertEqual(self.read("pkg/new.py"), "VALUE = 1\n")

    def test_create_file_rejects_existing(self):
        obs = dispatch("create_file", self.env, self.ctx,
                       {"path": "pkg/mod.py", "content": "x = 1\n"})
        self.assertFalse(obs["ok"])

    def test_create_file_rejects_bad_python(self):
        obs = dispatch("create_file", self.env, self.ctx,
                       {"path": "pkg/broken.py", "content": "def (:\n"})
        self.assertFalse(obs["ok"])
        self.assertIn("SyntaxError", obs["error"])
        self.assertFalse((self.workspace / "pkg" / "broken.py").exists())

    # --- edit_lines --------------------------------------------------------
    def test_edit_lines_replaces_range(self):
        self.run_ok("edit_lines", {"path": "pkg/mod.py", "start": 5, "end": 5,
                                   "new": "    return 30"})
        self.assertIn("return 30", self.read("pkg/mod.py"))
        self.assertNotIn("return 5\n", self.read("pkg/mod.py"))
        self.assertTrue(self.read("pkg/mod.py").endswith("\n"))

    def test_edit_lines_keeps_trailing_newline_from_new_content(self):
        self.run_ok("create_file", {"path": "pkg/no_newline.py",
                                    "content": "x = 1"})
        self.run_ok("edit_lines", {"path": "pkg/no_newline.py", "start": 1,
                                   "end": 1, "new": "x = 2\n"})
        self.assertEqual(self.read("pkg/no_newline.py"), "x = 2\n")

    def test_edit_lines_delete_with_empty_new(self):
        before_lines = self.read("README.md")
        self.assertIn("Timeout", before_lines)
        self.run_ok("edit_lines", {"path": "README.md", "start": 1, "end": 1, "new": ""})
        self.assertEqual(self.read("README.md"), "")

    def test_edit_lines_rejects_out_of_range(self):
        obs = dispatch("edit_lines", self.env, self.ctx,
                       {"path": "pkg/mod.py", "start": 1, "end": 9999, "new": "x"})
        self.assertFalse(obs["ok"])

    def test_edit_lines_enforces_python_syntax(self):
        before = self.read("pkg/mod.py")
        obs = dispatch("edit_lines", self.env, self.ctx,
                       {"path": "pkg/mod.py", "start": 5, "end": 5, "new": "    return ("})
        self.assertFalse(obs["ok"])
        self.assertIn("SyntaxError", obs["error"])
        self.assertEqual(self.read("pkg/mod.py"), before)

    # --- revert_file -------------------------------------------------------
    def test_revert_file_restores_tracked_file(self):
        baseline = self.read("pkg/mod.py")
        self.run_ok("edit_lines", {"path": "pkg/mod.py", "start": 5, "end": 5,
                                   "new": "    return 30"})
        obs = self.run_ok("revert_file", {"path": "pkg/mod.py"})
        self.assertEqual(obs["result"]["action"], "restored")
        self.assertEqual(self.read("pkg/mod.py"), baseline)

    def test_revert_file_deletes_new_file(self):
        self.run_ok("create_file", {"path": "pkg/new.py", "content": "V = 1\n"})
        obs = self.run_ok("revert_file", {"path": "pkg/new.py"})
        self.assertEqual(obs["result"]["action"], "deleted")
        self.assertFalse((self.workspace / "pkg" / "new.py").exists())

    # --- show_diff ---------------------------------------------------------
    def test_show_diff_reports_changes(self):
        self.run_ok("edit_lines", {"path": "pkg/mod.py", "start": 5, "end": 5,
                                   "new": "    return 30"})
        result = self.run_ok("show_diff", {})["result"]
        self.assertIn("pkg/mod.py", result["changed_files"])
        self.assertIn("return 30", result["diff"])

    def test_show_diff_stages_changes_and_includes_deleted_files(self):
        self.run_ok("edit_lines", {"path": "pkg/mod.py", "start": 5, "end": 5,
                                   "new": "    return 30"})
        self.env.delete("README.md")
        result = self.run_ok("show_diff", {})["result"]
        self.assertIn("pkg/mod.py", result["changed_files"])
        self.assertIn("README.md", result["changed_files"])
        self.assertIn("deleted file mode", result["diff"])
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.workspace,
                                check=True, capture_output=True, text=True).stdout
        self.assertTrue(any(line.startswith("M ") for line in status.splitlines()),
                        status)

    def test_show_diff_empty_when_clean(self):
        result = self.run_ok("show_diff", {})["result"]
        self.assertEqual(result["changed_files"], [])
        self.assertEqual(result["diff"], "")


if __name__ == "__main__":
    unittest.main()
