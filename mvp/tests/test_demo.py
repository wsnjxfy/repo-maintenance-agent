"""v2 toy demo: LocalEnvironment + Session + handlers + Agent on the fixture."""

import tempfile
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from demo import run
from toy_config import VISIBILITY


class ToyV2DemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="hj10-toyv2-")
        cls.summary, cls.history, cls.run_dir = run(cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_demo_completes_and_passes_evaluation(self):
        self.assertEqual(self.summary["status"], "completed")
        self.assertTrue(self.summary["evaluation"]["passed"])
        self.assertEqual(self.summary["evaluation"]["requirements"]["passed"], 7)
        self.assertEqual(self.summary["evaluation"]["regressions"]["passed"], 3)
        self.assertEqual(self.summary["toolset"], "toy-v2")
        self.assertEqual(self.summary["env"], "local")

    def test_patch_is_exported_and_contains_the_fix(self):
        patch = self.run_dir / "final.patch"
        self.assertTrue(patch.exists())
        text = patch.read_text(encoding="utf-8")
        self.assertIn("+    return selected", text)
        self.assertIn('+    return {"url": url, "timeout": timeout}', text)

    def test_trajectory_records_v2_observations(self):
        self.assertEqual(len(self.history), 9)
        self.assertTrue(all(event["observation"]["ok"] for event in self.history))

    def test_visibility_protects_the_visible_tests(self):
        self.assertTrue(VISIBILITY.allows_read("tests/test_visible.py"))
        self.assertFalse(VISIBILITY.allows_edit("tests/test_visible.py"))
        self.assertTrue(VISIBILITY.allows_edit("config.py"))


if __name__ == "__main__":
    unittest.main()
