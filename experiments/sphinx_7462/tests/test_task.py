"""SphinxTask synthetic tests: no real calibration inputs required."""

import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from experiments.sphinx_7462.prepare import BASE_COMMIT, REPOSITORY, TASK_ID
from experiments.sphinx_7462.task import SphinxTask


class SphinxTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hj10-sphinx-task-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git = shutil.which("git")
        self.assertIsNotNone(self.git)
        self.test_patch = "diff --git a/tests/test_module.py b/tests/test_module.py\n"
        self.row = {
            "instance_id": TASK_ID,
            "repo": REPOSITORY,
            "base_commit": BASE_COMMIT,
            "problem_statement": "Synthetic Sphinx maintenance request.",
            "patch": "",
            "test_patch": self.test_patch,
            "FAIL_TO_PASS": ["test_example"],
            "PASS_TO_PASS": ["test_regression"],
        }
        self.review = {
            "instance_id": TASK_ID,
            "repo": REPOSITORY,
            "base_commit": BASE_COMMIT,
            "evaluation": {"FAIL_TO_PASS": ["test_example"], "PASS_TO_PASS": ["test_regression"]},
            "reference_summary": {
                "test_patch_sha256_utf8": hashlib.sha256(
                    self.test_patch.encode()).hexdigest(),
            },
        }
        (self.root / "record.json").write_text(json.dumps(self.row), encoding="utf-8")
        (self.root / "review.json").write_text(json.dumps(self.review), encoding="utf-8")
        self.archive = self.root / "source.tar.gz"
        members = {
            "source/module.py": b"value = 1\n",
            "source/tests/test_module.py": b"def test_example(): assert True\n",
            "source/setup.cfg": b"[tool:pytest]\n",
        }
        with tarfile.open(self.archive, "w:gz") as archive:
            for name, content in members.items():
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))

    def make_task(self):
        return SphinxTask(
            source_archive=self.archive,
            record_path=self.root / "record.json",
            review_manifest=self.root / "review.json",
            python=sys.executable,
            git=self.git,
            skip_env_probe=True,
        )

    def test_prepare_creates_workspace_commit_and_visibility(self):
        task = self.make_task()
        run_dir = self.root / "run"
        run_dir.mkdir()
        workspace = task.prepare(run_dir)
        self.assertEqual((workspace / "module.py").read_bytes(), b"value = 1\n")
        self.assertTrue(task.visibility.allows_read("module.py"))
        self.assertTrue(task.visibility.allows_edit("module.py"))
        self.assertFalse(task.visibility.allows_edit("tests/test_module.py"))
        head = subprocess.run([self.git, "-C", str(workspace), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True)
        self.assertEqual(len(head.stdout.strip()), 40)

    def test_evaluate_normalizes_sphinx_result(self):
        task = self.make_task()
        run_dir = self.root / "run"
        run_dir.mkdir()
        workspace = task.prepare(run_dir)
        fake = {"resolved": True, "status": "completed",
                "grading": {"f2p_passed": 1, "p2p_passed": 0}}
        with mock.patch("experiments.sphinx_7462.task.evaluate_module.evaluate",
                        return_value=fake) as called:
            result = task.evaluate(run_dir, workspace, None)
        self.assertTrue(result["resolved"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["grading"]["f2p_passed"], 1)
        self.assertEqual(Path(called.call_args.kwargs["output_dir"]).resolve(),
                         (run_dir / "evaluation").resolve())

    def test_protocol_limits(self):
        task = self.make_task()
        self.assertEqual(task.protocol["max_actions"], 30)
        self.assertEqual(task.toolset, "repo-v1")
        self.assertEqual(task.max_steps, 30)
