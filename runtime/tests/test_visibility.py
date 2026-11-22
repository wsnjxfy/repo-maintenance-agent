"""Visibility rules: instance-level readable/editable/protected boundaries."""

import tempfile
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from contracts.visibility import Visibility, matches
from runtime.local import LocalEnvironment


class PatternTests(unittest.TestCase):
    def test_globs(self):
        self.assertTrue(matches("pkg/a.py", "pkg/*.py"))
        self.assertFalse(matches("pkg/sub/a.py", "pkg/*.py"))
        self.assertTrue(matches("pkg/sub/a.py", "pkg/**"))
        self.assertTrue(matches("a/b/c.py", "*.py"))
        self.assertTrue(matches("tests/test_a.py", "tests/**"))


class VisibilityRuleTests(unittest.TestCase):
    def test_read_edit_and_protected(self):
        visibility = Visibility(readable=["pkg/**"], editable=["pkg/**"],
                                protected=["pkg/secret.py"])
        self.assertTrue(visibility.allows_read("pkg/a.py"))
        self.assertFalse(visibility.allows_read("README.md"))
        self.assertTrue(visibility.allows_edit("pkg/a.py"))
        self.assertFalse(visibility.allows_edit("pkg/secret.py"))
        self.assertFalse(visibility.allows_edit("README.md"))
        self.assertEqual(visibility.filter(["pkg/a.py", "README.md"]), ["pkg/a.py"])

    def test_default_is_permissive(self):
        visibility = Visibility()
        self.assertTrue(visibility.allows_edit("anything.py"))


class EnvironmentBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hj10-visibility-")
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        (self.workspace / "pkg").mkdir(parents=True)
        (self.workspace / "tests").mkdir()
        (self.workspace / "pkg" / "a.py").write_bytes(b"A = 1\n")
        (self.workspace / "pkg" / "secret.py").write_bytes(b"S = 1\n")
        (self.workspace / "README.md").write_bytes(b"readme\n")
        (self.workspace / "tests" / "test_a.py").write_bytes(b"def test_a(): pass\n")
        visibility = Visibility(readable=["pkg/**"], editable=["pkg/**"],
                                protected=["pkg/secret.py"])
        self.env = LocalEnvironment(self.workspace, visibility=visibility,
                                    scratch=root / "scratch")

    def tearDown(self):
        self.temp.cleanup()

    def test_reads_are_limited_to_the_readable_set(self):
        self.assertEqual(self.env.read_text("pkg/a.py"), "A = 1\n")
        with self.assertRaises(PermissionError):
            self.env.read_text("README.md")
        self.assertEqual(self.env.list_files("."), ["pkg/a.py", "pkg/secret.py"])
        self.assertFalse(self.env.exists("README.md"))

    def test_writes_are_limited_to_the_editable_set(self):
        self.env.write_text("pkg/b.py", "B = 2\n")
        self.assertEqual((self.workspace / "pkg" / "b.py").read_text(encoding="utf-8"), "B = 2\n")
        with self.assertRaises(PermissionError):
            self.env.write_text("README.md", "changed\n")
        with self.assertRaises(PermissionError):
            self.env.write_text("pkg/secret.py", "changed\n")

    def test_manifest_covers_only_readable_paths(self):
        self.assertEqual(sorted(self.env.manifest()), ["pkg/a.py", "pkg/secret.py"])
        self.assertTrue(self.env.is_editable("pkg/a.py"))
        self.assertFalse(self.env.is_editable("pkg/secret.py"))
