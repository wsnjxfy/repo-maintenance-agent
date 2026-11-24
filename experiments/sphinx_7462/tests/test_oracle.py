"""Reference and empty policies for Sphinx runner tests."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from experiments.sphinx_7462.oracle import ReferencePatchPolicy
from experiments.sphinx_7462.runner import EmptyPolicy

PATCH = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-old
+new
"""


class PolicyTests(unittest.TestCase):
    def test_reference_policy_applies_then_finishes(self):
        with tempfile.TemporaryDirectory(prefix="hj10-oracle-") as temp:
            path = Path(temp) / "reference.patch"
            path.write_text(PATCH, encoding="utf-8")
            policy = ReferencePatchPolicy(path)
        first = policy.next_action([])
        second = policy.next_action([{"step": 1}])
        self.assertEqual(first.tool, "apply_patch")
        self.assertEqual(first.args["patch"], PATCH)
        self.assertEqual(second.tool, "finish")

    def test_empty_policy_finishes_immediately(self):
        action = EmptyPolicy().next_action([])
        self.assertEqual(action.tool, "finish")
