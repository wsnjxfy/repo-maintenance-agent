"""Vertical slice: LocalEnvironment + handlers + Session + Agent.

Runs on a synthetic repository (no Sphinx inputs required).
"""

import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent_core.agent import Action, Agent
from runtime.local import LocalEnvironment
from runtime.session import Session
from tools.specs import TOOL_SPECS
from tools.toolsets import tool_names


APP_BUG = """def add(a, b):
    return a - b
"""

APP_FIX = """def add(a, b):
    return a + b
"""

TEST_FILE = """from pkg.app import add


def test_add():
    assert add(2, 3) == 5
"""

DIFF = """diff --git a/pkg/app.py b/pkg/app.py
--- a/pkg/app.py
+++ b/pkg/app.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
"""


class SlicePolicy:
    def __init__(self):
        self.actions = [
            ("read_lines", {"path": "pkg/app.py"}),
            ("apply_patch", {"patch": DIFF}),
            ("run_visible_tests", {"paths": ["tests/test_app.py"]}),
            ("finish", {"reason": "fixed add()"}),
        ]

    def next_action(self, history):
        index = len(history)
        if index >= len(self.actions):
            return None
        tool, args = self.actions[index]
        return Action(tool, args)


class VerticalSliceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hj10-slice-")
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        (self.workspace / "pkg").mkdir(parents=True)
        (self.workspace / "tests").mkdir()
        (self.workspace / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (self.workspace / "pkg" / "app.py").write_text(APP_BUG, encoding="utf-8")
        (self.workspace / "tests" / "test_app.py").write_text(TEST_FILE, encoding="utf-8")
        (self.workspace / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=self.workspace, check=True)
        subprocess.run(["git", "config", "user.email", "slice@example.test"], cwd=self.workspace, check=True)
        subprocess.run(["git", "config", "user.name", "slice"], cwd=self.workspace, check=True)
        subprocess.run(["git", "add", "-A"], cwd=self.workspace, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.workspace, check=True)
        self.controller = root / "controller"
        self.env = LocalEnvironment(self.workspace, scratch=self.controller / "scratch",
                                    protected_paths=["tests/test_app.py"])
        self.specs = {name: TOOL_SPECS[name] for name in tool_names("repo-v1")}
        self.session = Session(self.env, self.controller,
                               {"max_actions": 30, "wall_time_seconds": 600,
                                "test_timeout_seconds": 90},
                               self.specs, set(self.specs), initial_manifest=self.env.manifest())
        self.session.activate()

    def tearDown(self):
        self.temp.cleanup()

    def test_read_patch_test_finish(self):
        listing = self.session.dispatch("list_files", {"path": "."})
        self.assertTrue(listing["ok"])
        self.assertIn("pkg/app.py", listing["result"]["files"])

        read = self.session.dispatch("read_lines", {"path": "pkg/app.py", "start": 1, "end": 2})
        self.assertTrue(read["ok"])
        self.assertEqual(read["result"]["lines"][1]["text"], "    return a - b")

        patched = self.session.dispatch("apply_patch", {"patch": DIFF})
        self.assertTrue(patched["ok"], patched)
        self.assertEqual(patched["changed_paths"], ["pkg/app.py"])

        tests = self.session.dispatch("run_visible_tests", {"paths": ["tests/test_app.py"]})
        self.assertTrue(tests["ok"], tests)
        self.assertEqual(tests["result"]["status"], "passed")

        finished = self.session.dispatch("finish", {"reason": "fixed"})
        self.assertTrue(finished["ok"], finished)
        self.assertTrue(finished["result"]["finished"])
        self.assertIsNone(finished["result"]["accepted"])
        patch = Path(finished["result"]["submission"]["patch"])
        self.assertTrue(patch.exists())
        text = patch.read_text(encoding="utf-8")
        self.assertIn("+    return a + b", text)

    def test_budget_rejects_further_actions(self):
        session = Session(self.env, self.controller / "budget",
                          {"max_actions": 1, "wall_time_seconds": 600,
                           "test_timeout_seconds": 90},
                          self.specs, set(self.specs), initial_manifest=self.env.manifest())
        session.activate()
        self.assertTrue(session.dispatch("list_files", {"path": "."})["ok"])
        rejected = session.dispatch("list_files", {"path": "."})
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["kind"], "budget")

    def test_protected_patch_is_rejected(self):
        patch = DIFF.replace("pkg/app.py", "tests/test_app.py")
        result = self.session.dispatch("apply_patch", {"patch": patch})
        self.assertFalse(result["ok"])
        self.assertIn("read-only", result["error"])
        self.assertEqual((self.workspace / "tests" / "test_app.py").read_text(encoding="utf-8"), TEST_FILE)

    def test_failed_patch_is_atomic(self):
        broken = DIFF + """diff --git a/pkg/other.py b/pkg/other.py
--- a/pkg/other.py
+++ b/pkg/other.py
@@ -1,2 +1,2 @@
 def f():
-    return 1
+    return 2
"""
        result = self.session.dispatch("apply_patch", {"patch": broken})
        self.assertFalse(result["ok"])
        self.assertEqual((self.workspace / "pkg" / "app.py").read_text(encoding="utf-8"), APP_BUG)

    def test_agent_completes_the_slice(self):
        with contextlib.redirect_stdout(io.StringIO()):
            history, status, reason = Agent(self.session, max_steps=6).run(
                SlicePolicy(), self.controller / "trajectory.jsonl")
        self.assertEqual(status, "completed", reason)
        self.assertEqual(len(history), 4)
        self.assertTrue((self.controller / "trajectory.jsonl").exists())
        events = [json.loads(line) for line in
                  (self.controller / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(all(event["observation"]["ok"] for event in events))
