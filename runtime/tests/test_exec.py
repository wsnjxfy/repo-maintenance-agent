"""run_suite classification tests (moved from the toy-era suite)."""

from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from runtime.exec.suite import run_suite


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "mvp" / "fixtures" / "timeout_demo"


class RunSuiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hj10-exec-")
        self.workspace = Path(self.temp.name) / "workspace"
        shutil.copytree(FIXTURE, self.workspace)

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_test_file_is_environment_error(self):
        result = run_suite(self.workspace, self.workspace / "missing-tests.py")
        self.assertEqual(result["status"], "environment_error")
        self.assertEqual(result["total"], 0)

    def test_skipped_tests_are_not_passed(self):
        suite = self.workspace / "skip_tests.py"
        suite.write_text('import unittest\nclass Skipped(unittest.TestCase):\n'
                         '    @unittest.skip("teaching check")\n'
                         '    def test_skipped(self): pass\n', encoding="utf-8")
        result = run_suite(self.workspace, suite)
        self.assertEqual(result["status"], "failed")
        self.assertEqual((result["total"], result["passed"], result["skipped"]), (1, 0, 1))

    def test_subtests_are_counted_at_method_level(self):
        suite = self.workspace / "sub_tests.py"
        suite.write_text('import unittest\nclass Sub(unittest.TestCase):\n'
                         '    def test_many(self):\n'
                         '        for x in range(3):\n'
                         '            with self.subTest(x=x): self.assertEqual(x, 0)\n', encoding="utf-8")
        result = run_suite(self.workspace, suite)
        self.assertEqual(result["status"], "failed")
        self.assertEqual((result["total"], result["passed"], result["failed"]), (1, 0, 1))
        self.assertEqual(len(result["cases"]), 1)

    def test_import_failure_is_a_load_error(self):
        suite = self.workspace / "import_tests.py"
        suite.write_text("from config import a_function_missing_after_a_patch\n", encoding="utf-8")
        self.assertEqual(run_suite(self.workspace, suite)["status"], "test_load_error")

    def test_timeout_is_left_unclassified(self):
        suite = self.workspace / "slow_tests.py"
        suite.write_text("import time\ntime.sleep(1)\n", encoding="utf-8")
        self.assertEqual(run_suite(self.workspace, suite, timeout=0.1)["status"],
                         "execution_timeout")
