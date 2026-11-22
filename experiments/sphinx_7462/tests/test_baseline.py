"""Offline controller checks using a disposable synthetic repository."""

import io
import json
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

from experiments.sphinx_7462 import baseline
from experiments.sphinx_7462.baseline import ModelReply, ScriptedModel, run_baseline
from experiments.sphinx_7462.prepare import BASE_COMMIT, INPUT_FIELDS, REPOSITORY, TASK_ID
from experiments.sphinx_7462.tests.test_evaluate import patch_for, sha, write_json


UNRESOLVED = {
    "status": "completed", "resolved": False,
    "grading": {"f2p_passed": 0, "f2p_total": 1, "p2p_passed": 1, "p2p_total": 1},
}


def action(tool, **args):
    return {"tool": tool, "args": args}


class BaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.git = shutil.which("git")
        if not cls.git:
            raise unittest.SkipTest("Git is required for the synthetic trial")

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="hj10-baseline-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.archive = self.root / "source.tar.gz"
        self.record = self.root / "record.json"
        self.review = self.root / "review.json"
        self.run_dir = self.root / "run"
        test_file = "tests/test_example.py"
        visible = "from module import VALUE\n\ndef test_regression():\n    assert VALUE >= 1\n"
        official = patch_for(test_file, visible, visible + (
            "\ndef test_requirement():\n    assert VALUE == 2  # HIDDEN_TEST_SENTINEL\n"
        ))
        files = {"module.py": "VALUE = 1\n", test_file: visible, "sphinx/__init__.py": "# Probe stub\n"}
        with tarfile.open(self.archive, "w:gz") as archive:
            for name, source in files.items():
                content = source.encode("utf-8")
                member = tarfile.TarInfo("source/" + name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        identity = {"instance_id": TASK_ID, "repo": REPOSITORY, "base_commit": BASE_COMMIT}
        labels = {"FAIL_TO_PASS": [test_file + "::test_requirement"],
                  "PASS_TO_PASS": [test_file + "::test_regression"]}
        write_json(self.record, dict(identity, **labels, problem_statement="Use VALUE 2.",
                                     patch="REFERENCE_PATCH_SENTINEL", test_patch=official,
                                     hints_text="HINT_SENTINEL"))
        write_json(self.review, dict(identity, evaluation=labels, reference_summary={
            "test_patch_sha256_utf8": sha(official.encode("utf-8")),
        }))

    def run_trial(self, model, **options):
        return run_baseline(self.archive, self.record, self.review, self.run_dir,
                            sys.executable, self.git, model,
                            expected_source_sha256=sha(self.archive.read_bytes()),
                            skip_env_probe=True, **options)

    def events(self, filename):
        return [json.loads(line) for line in
                (self.run_dir / "controller" / filename).read_text(encoding="utf-8").splitlines()]

    def test_prepares_edits_freezes_and_evaluates_without_revealing_answers(self):
        scripted = ScriptedModel([
            action("list_files"),
            action("apply_patch", patch=patch_for("module.py", "VALUE = 1\n", "VALUE = 0\n")),
            action("run_visible_tests", paths=["tests/test_example.py"]),
            action("apply_patch", patch=patch_for("module.py", "VALUE = 0\n", "VALUE = 2\n")),
            action("run_visible_tests", paths=["tests/test_example.py"]),
            action("finish", reason="Synthetic change done"),
        ])
        result = self.run_trial(scripted)
        self.assertEqual((result["status"], result["stop_reason"]), ("completed", "model_finished"))
        self.assertTrue(result["resolved"], result)
        self.assertEqual((result["model_calls"], result["workspace_actions"]), (6, 5))
        self.assertEqual((result["evaluation"]["f2p_passed"], result["evaluation"]["p2p_passed"]), (1, 1))
        controller = self.run_dir / "controller"
        frozen = (controller / "submission/final.patch").read_bytes()
        self.assertEqual(result["patch_sha256"], sha(frozen))
        self.assertEqual((controller / "base/module.py").read_bytes(), b"VALUE = 1\n")
        self.assertEqual((self.run_dir / "workspace/module.py").read_bytes(), b"VALUE = 2\n")
        independent = json.loads((self.run_dir / "evaluation/evaluation.json").read_text(encoding="utf-8"))
        self.assertEqual(independent["input"]["candidate_patch_sha256"], sha(frozen))
        self.assertEqual(json.loads((self.run_dir / "summary.json").read_text(encoding="utf-8")), result)
        calls = self.events("model-calls.jsonl")
        self.assertEqual([call["tool_event_id"] for call in calls], [1, 2, 3, 4, 5, 6])
        self.assertEqual(set(calls[0]["request"]["task"]), set(INPUT_FIELDS))
        self.assertIn("failed", json.dumps(calls[3]["request"]))
        self.assertIn("passed", json.dumps(calls[5]["request"]))
        model_inputs = json.dumps([call["request"] for call in calls])
        for hidden in ("REFERENCE_PATCH_SENTINEL", "HINT_SENTINEL", "HIDDEN_TEST_SENTINEL", "test_requirement"):
            self.assertNotIn(hidden, model_inputs)

    def test_replace_text_mode_runs_visible_tests_and_evaluates_the_export(self):
        scripted = ScriptedModel([
            action("replace_text", path="module.py", old="VALUE = 1", new="VALUE = 2"),
            action("run_visible_tests", paths=["tests/test_example.py"]),
            action("finish", reason="Synthetic replacement verified"),
        ])
        result = self.run_trial(scripted, edit_tool="replace_text")
        self.assertTrue(result["resolved"], result)
        self.assertEqual(result["visible_tests"]["status"], "passed")
        calls = self.events("model-calls.jsonl")
        expected_tools = set(baseline.TOOL_ARGUMENTS) - {"apply_patch"} | {"replace_text"}
        for call in calls:
            self.assertEqual(set(call["request"]["tools"]), expected_tools)
            self.assertEqual(call["request"]["instructions"], baseline.INSTRUCTIONS)
        self.assertEqual(self.events("actions.jsonl")[0]["action"], "replace_text")
        frozen = (self.run_dir / "controller/submission/final.patch").read_bytes()
        self.assertEqual(result["patch_sha256"], sha(frozen))
        self.assertEqual((self.run_dir / "evaluation/workspace/module.py").read_bytes(), b"VALUE = 2\n")

    @mock.patch.object(baseline, "evaluate", return_value=UNRESOLVED)
    def test_invalid_responses_use_model_budget_and_keep_reported_usage(self, evaluate):
        invalid_unicode = json.dumps(action("read_lines", path="\ud800"), ensure_ascii=False)
        replies = iter([
            ModelReply("broken JSON", 10, 2),
            ModelReply(json.dumps(action("unknown_tool")), 15, 4),
            ModelReply("[" * 2000 + "]" * 2000, 7, 1),
            ModelReply(invalid_unicode, 9, 3),
            ModelReply(json.dumps(action("finish", reason="stop")), 20, 3),
        ])
        result = self.run_trial(lambda request, **kwargs: next(replies))
        self.assertEqual((result["model_calls"], result["workspace_actions"]), (5, 0))
        self.assertEqual(result["stop_reason"], "model_finished")
        self.assertEqual(result["usage"], {"input_tokens": 61, "output_tokens": 13, "calls_with_usage": 5})
        calls = self.events("model-calls.jsonl")
        self.assertEqual([call["tool_event_id"] for call in calls], [None, None, None, None, 1])
        self.assertEqual([call["status"] for call in calls[:-1]], ["invalid_response"] * 4)
        self.assertEqual(calls[0]["response"], "broken JSON")
        self.assertEqual(calls[0]["input_tokens"], 10)
        self.assertEqual(calls[3]["response"], invalid_unicode)
        self.assertIn("Unknown workspace tool", json.dumps(calls[-1]["request"]))
        evaluate.assert_called_once()

    @mock.patch.object(baseline, "evaluate", return_value=UNRESOLVED)
    def test_action_cap_freezes_without_another_model_call(self, evaluate):
        scripted = ScriptedModel([action("read_lines", path="missing.py"), action("list_files")])
        result = self.run_trial(scripted, max_actions=1)
        self.assertEqual(result["stop_reason"], "action_budget")
        self.assertEqual((result["model_calls"], result["workspace_actions"]), (1, 1))
        self.assertEqual([item["action"] for item in self.events("actions.jsonl")], ["read_lines", "finish"])
        self.assertEqual(self.events("actions.jsonl")[0]["status"], "error")
        evaluate.assert_called_once()

    @mock.patch.object(baseline, "evaluate", return_value=UNRESOLVED)
    def test_model_call_cap_freezes_even_with_no_workspace_actions(self, evaluate):
        result = self.run_trial(ScriptedModel(["invalid", action("list_files")]), max_model_calls=1)
        self.assertEqual(result["stop_reason"], "model_call_budget")
        self.assertEqual((result["model_calls"], result["workspace_actions"]), (1, 0))
        self.assertEqual([item["action"] for item in self.events("actions.jsonl")], ["finish"])
        self.assertEqual((self.run_dir / "controller/submission/final.patch").read_bytes(), b"")
        evaluate.assert_called_once()

    @mock.patch.object(baseline, "evaluate", return_value=UNRESOLVED)
    def test_model_failures_freeze_then_evaluate_once(self, evaluate):
        for exc, reason in ((TimeoutError("timed out"), "model_timeout"),
                            (RuntimeError("mock failure"), "model_error"),
                            (StopIteration(), "model_exhausted")):
            with self.subTest(reason=reason):
                self.run_dir = self.root / reason
                evaluate.reset_mock()
                model = mock.Mock(side_effect=exc)
                result = self.run_trial(model)
                self.assertEqual(result["stop_reason"], reason)
                self.assertEqual(result["workspace_actions"], 0)
                self.assertEqual([item["action"] for item in self.events("actions.jsonl")], ["finish"])
                model.assert_called_once()
                evaluate.assert_called_once()

    @mock.patch.object(baseline, "evaluate", return_value=UNRESOLVED)
    def test_late_model_reply_is_logged_but_not_dispatched(self, evaluate):
        clock = mock.Mock()
        clock.monotonic.return_value = 100.0

        def late_reply(request, *, timeout_seconds):
            self.assertEqual(timeout_seconds, 1.0)
            clock.monotonic.return_value = 101.5
            return ModelReply(json.dumps(action("list_files")), 10, 2)

        with mock.patch.object(baseline, "time", clock):
            result = self.run_trial(late_reply, wall_time_seconds=1.0)
        self.assertEqual(result["stop_reason"], "wall_time_budget")
        self.assertEqual((result["model_calls"], result["workspace_actions"]), (1, 0))
        self.assertEqual(result["usage"]["input_tokens"], 10)
        call, = self.events("model-calls.jsonl")
        self.assertEqual(call["status"], "late_response")
        self.assertIsNone(call["tool_event_id"])
        self.assertEqual([item["action"] for item in self.events("actions.jsonl")], ["finish"])
        evaluate.assert_called_once()

    @mock.patch.object(baseline, "evaluate", return_value={
        "status": "error", "resolved": False, "validation_errors": ["Synthetic evaluation failure"],
    })
    def test_evaluation_error_does_not_change_the_solver_stop_reason(self, evaluate):
        result = self.run_trial(ScriptedModel([action("finish", reason="done")]))
        self.assertEqual(result["stop_reason"], "model_finished")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["failure_stage"], "evaluation")
        self.assertEqual(result["evaluation"]["validation_errors"], ["Synthetic evaluation failure"])
        self.assertTrue((self.run_dir / "controller/submission/final.patch").exists())
        evaluate.assert_called_once()

    @mock.patch.object(baseline, "evaluate")
    def test_existing_run_is_preserved(self, evaluate):
        self.run_dir.mkdir()
        sentinel = self.run_dir / "keep.txt"
        sentinel.write_text("previous evidence", encoding="utf-8")
        model = mock.Mock()
        with self.assertRaises(FileExistsError):
            self.run_trial(model)
        self.assertEqual(list(self.run_dir.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "previous evidence")
        model.assert_not_called()
        evaluate.assert_not_called()
