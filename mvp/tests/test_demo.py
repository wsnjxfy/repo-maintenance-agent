"""v2 toy demo: LocalEnvironment + Session + handlers + Agent on the fixture."""

import shutil
import tempfile
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mvp.demo import run
from mvp.toy_task import ToyTask, VISIBILITY


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
        self.assertTrue(self.summary["evaluation"]["resolved"])
        metrics = self.summary["evaluation"]["metrics"]
        self.assertEqual(metrics["fail_to_pass"]["passed"], 7)
        self.assertEqual(metrics["pass_to_pass"]["passed"], 3)
        self.assertEqual(self.summary["toolset"], "toy-v2")
        self.assertEqual(self.summary["env"], "local")
        self.assertEqual(set(self.summary["tool_versions"]),
                         set(self.summary["tools_enabled"]))
        self.assertTrue(all(v == 0 for v in self.summary["tool_versions"].values()))

    def test_artifacts_exist(self):
        for name in ("summary.json", "trajectory.jsonl", "report.html", "final.patch"):
            self.assertTrue((self.run_dir / name).exists(), name)

    def test_patch_is_exported_and_contains_the_fix(self):
        patch = self.run_dir / "final.patch"
        self.assertTrue(patch.exists())
        text = patch.read_text(encoding="utf-8")
        self.assertIn("+    return selected", text)
        self.assertIn('+    return {"url": url, "timeout": timeout}', text)

    def test_trajectory_records_v2_observations(self):
        self.assertEqual(len(self.history), 10)
        self.assertTrue(all(event["observation"]["ok"] for event in self.history))

    def test_hidden_checks_do_not_enter_history(self):
        trajectory = (self.run_dir / "trajectory.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("hidden_timeout", trajectory)
        self.assertNotIn("TestRequirements", trajectory)

    def test_budget_exhaustion_is_not_success(self):
        with tempfile.TemporaryDirectory(prefix="hj10-toyv2-budget-") as temp:
            summary, _, _ = run(temp, max_steps=2)
        self.assertEqual(summary["status"], "budget_exhausted")
        self.assertFalse(summary["evaluation"]["resolved"])

    def test_new_regression_is_detected(self):
        with tempfile.TemporaryDirectory(prefix="hj10-toyv2-regression-") as temp:
            workspace = Path(temp) / "workspace"
            shutil.copytree(self.run_dir / "workspace", workspace)
            client = workspace / "client.py"
            client.write_text(client.read_text(encoding="utf-8").replace(
                '"url": url', '"url": "broken"'), encoding="utf-8")
            evaluation = ToyTask().evaluate(Path(temp), workspace)
        self.assertFalse(evaluation["resolved"])
        self.assertTrue(evaluation["new_regressions"])

    def test_visibility_protects_the_visible_tests(self):
        self.assertTrue(VISIBILITY.allows_read("tests/test_visible.py"))
        self.assertFalse(VISIBILITY.allows_edit("tests/test_visible.py"))
        self.assertTrue(VISIBILITY.allows_edit("config.py"))



if __name__ == "__main__":
    unittest.main()
