"""Regression: Path-valued interpreter must not poison session JSON."""

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from runtime.local import LocalEnvironment
from runtime.session import Session
from tools.specs import TOOL_SPECS


class PathSerializationTests(unittest.TestCase):
    def test_python_path_is_normalized_for_visible_test_results(self):
        with tempfile.TemporaryDirectory(prefix="hj10-path-") as temp:
            root = Path(temp)
            workspace = root / "workspace"
            (workspace / "tests").mkdir(parents=True)
            (workspace / "tests/test_x.py").write_text(
                "def test_ok():\n    assert True\n", encoding="utf-8")
            git = shutil.which("git")
            self.assertIsNotNone(git)
            env = LocalEnvironment(workspace, python=Path(sys.executable), git=Path(git),
                                   scratch=root / "scratch")
            self.assertIsInstance(env.python, str)
            self.assertIsInstance(env.git, str)
            session = Session(
                env, root / "controller",
                {"max_actions": 5, "test_timeout_seconds": 30},
                {"run_visible_tests": TOOL_SPECS["run_visible_tests"]},
                {"run_visible_tests"})
            session.activate()
            observation = session.dispatch(
                "run_visible_tests", {"paths": ["tests/test_x.py"]})
            self.assertTrue(observation["ok"], observation)
            json.dumps(session.state)  # must not raise on WindowsPath
