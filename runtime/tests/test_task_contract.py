"""Task contract tests for run.py: python/git, evaluate(run_dir), finalize."""

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent_core.agent import Action
from contracts.visibility import Visibility
from run import run


class FinishPolicy:
    def next_action(self, history):
        return Action("finish", {"reason": "done"})


class StopPolicy:
    def next_action(self, history):
        return None


class ReadThenFinishPolicy:
    def __init__(self, reads=4):
        self.reads = reads

    def next_action(self, history):
        if len(history) < self.reads:
            return Action("list_files", {"path": "."})
        return Action("finish", {"reason": "done"})


@dataclass
class FakeTask:
    workspace: Path
    visibility: Visibility
    protocol: dict
    problem: str = "Synthetic task"
    task_id: str = "fake-task"
    toolset: str = "toy-v2"
    run_prefix: str = "fake"
    max_steps: int = 3
    python: str = sys.executable
    git: str = "git"

    def prepare(self, run_dir):
        return self.workspace

    def evaluate(self, run_dir, workspace, patch):
        self.calls = getattr(self, "calls", []) + [(run_dir, workspace, patch)]
        return {"resolved": True, "status": "completed"}


class FailingEvaluateTask(FakeTask):
    def evaluate(self, run_dir, workspace, patch):
        raise RuntimeError("evaluation exploded")


class FailingPrepareTask(FakeTask):
    def prepare(self, run_dir):
        raise RuntimeError("prepare exploded")


class TaskContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hj10-task-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "app.py").write_text("value = 1\n", encoding="utf-8")
        git = shutil.which("git")
        self.assertIsNotNone(git)
        subprocess.run([git, "-C", str(self.workspace), "init", "-q"], check=True)
        subprocess.run([git, "-C", str(self.workspace), "-c", "user.email=a@b.test",
                        "-c", "user.name=test", "add", "-A"], check=True)
        subprocess.run([git, "-C", str(self.workspace), "-c", "user.email=a@b.test",
                        "-c", "user.name=test", "commit", "-qm", "baseline"], check=True)
        self.git = git

    def task(self, cls=FakeTask):
        return cls(
            workspace=self.workspace,
            visibility=Visibility(readable=("**",), editable=("**",)),
            protocol={"max_actions": 3, "wall_time_seconds": 30,
                      "test_timeout_seconds": 30},
            git=self.git,
        )

    def test_evaluate_receives_run_dir_workspace_and_patch(self):
        task = self.task()
        summary, _, run_dir = run(task, FinishPolicy(), output_root=self.root / "runs")
        self.assertEqual(summary["status"], "completed")
        self.assertTrue(summary["resolved"])
        called_run_dir, called_workspace, called_patch = task.calls[0]
        self.assertEqual(called_run_dir, run_dir)
        self.assertEqual(called_workspace, self.workspace)
        self.assertIsNotNone(called_patch)

    def test_finalize_exports_patch_when_policy_stops(self):
        task = self.task()
        summary, _, run_dir = run(task, StopPolicy(), output_root=self.root / "runs")
        self.assertIn(summary["status"], {"incomplete", "budget_exhausted"})
        self.assertEqual(summary["final_patch"], "final.patch")
        self.assertTrue((run_dir / "final.patch").exists())
        self.assertIsNotNone(task.calls[0][2])

    def test_max_actions_override_allows_more_actions(self):
        task = self.task()
        summary, _, _ = run(task, ReadThenFinishPolicy(4),
                            output_root=self.root / "runs", max_steps=6,
                            max_actions=7)
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["max_actions"], 7)
        self.assertEqual(summary["actions"], 4)

    def test_task_protocol_max_actions_still_applies_by_default(self):
        task = self.task()
        summary, _, _ = run(task, ReadThenFinishPolicy(4),
                            output_root=self.root / "runs", max_steps=6)
        self.assertEqual(summary["status"], "budget_exhausted")
        self.assertEqual(summary["max_actions"], 3)
        self.assertEqual(summary["actions"], 3)

    def test_evaluation_failure_still_writes_summary_and_patch(self):
        task = self.task(FailingEvaluateTask)
        summary, _, run_dir = run(task, FinishPolicy(), output_root=self.root / "runs")
        self.assertTrue((run_dir / "summary.json").exists())
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["error_stage"], "evaluate")
        self.assertEqual(summary["evaluation"]["status"], "evaluation_error")
        self.assertFalse(summary["resolved"])
        self.assertEqual(summary["final_patch"], "final.patch")
        self.assertTrue((run_dir / "final.patch").exists())

    def test_prepare_failure_still_writes_summary(self):
        task = self.task(FailingPrepareTask)
        summary, history, run_dir = run(task, FinishPolicy(), output_root=self.root / "runs")
        self.assertTrue((run_dir / "summary.json").exists())
        self.assertEqual(summary["status"], "run_failed")
        self.assertEqual(summary["error_stage"], "prepare")
        self.assertEqual(summary["evaluation"]["status"], "not_run")
        self.assertFalse(summary["resolved"])
        self.assertEqual(history, [])
