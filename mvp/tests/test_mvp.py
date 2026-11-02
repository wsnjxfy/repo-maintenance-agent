"""Test controller behavior, real recovery, evaluation separation, and the adapter."""

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hj10.agent import Action, Agent
from hj10.llm import LLMPolicy, load_env, policy_from_env
from hj10.report import write_report
from hj10.tools import TOOL_SPECS, Tools, run_suite, tool_labels, tool_table
from run_demo import FIXTURE, evaluate, run_demo


class TestTeachingRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.original = {str(p.relative_to(FIXTURE)): p.read_bytes()
                        for p in FIXTURE.rglob("*.py")}
        with contextlib.redirect_stdout(io.StringIO()):
            cls.summary, cls.events, cls.run_dir = run_demo(cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_real_multi_file_recovery(self):
        self.assertEqual(self.summary["status"], "completed")
        results = [e["observation"] for e in self.events if e["action"]["tool"] == "run_tests"]
        self.assertEqual([(r["passed"], r["total"]) for r in results], [(1, 8), (5, 8), (8, 8)])
        self.assertIn("a/config.py", self.summary["final_diff"])
        self.assertIn("a/client.py", self.summary["final_diff"])
        self.assertEqual((self.run_dir / "workspace/cli.py").read_bytes(), self.original["cli.py"])

    def test_independent_acceptance_and_regressions(self):
        evaluation = self.summary["evaluation"]
        self.assertTrue(evaluation["passed"])
        self.assertEqual(evaluation["requirements"]["passed"], 7)
        self.assertEqual(evaluation["regressions"]["passed"], 3)
        self.assertEqual(evaluation["baseline_regressions"]["passed"], 3)
        self.assertEqual(evaluation["new_regressions"], [])

    def test_hidden_checks_do_not_enter_policy_history(self):
        serialized = json.dumps(self.events)
        self.assertNotIn("TestRequirements", serialized)
        self.assertNotIn("TestRegressions", serialized)
        self.assertNotIn("hidden_timeout_tests.py", serialized)
        self.assertFalse((self.run_dir / "workspace/evaluation").exists())
        self.assertEqual(self.summary["model_calls"], 0)
        self.assertIsNone(self.summary["tokens"])

    def test_fixtures_and_recorded_artifacts(self):
        actual = {str(p.relative_to(FIXTURE)): p.read_bytes() for p in FIXTURE.rglob("*.py")}
        self.assertEqual(self.original, actual)
        lines = (self.run_dir / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(line) for line in lines], self.events)
        for name in ("summary.json", "final.patch", "report.html", "task.txt"):
            self.assertTrue((self.run_dir / name).is_file())

    def test_new_regression_is_detected(self):
        broken = Path(self.temp.name) / "regressed"
        shutil.copytree(self.run_dir / "workspace", broken)
        path = broken / "client.py"
        path.write_text('def build_request(url, timeout=5):\n    return {"timeout": timeout}\n', encoding="utf-8")
        result = evaluate(broken)
        self.assertFalse(result["passed"])
        self.assertEqual(result["regressions"]["failed"], 2)
        self.assertEqual(len(result["new_regressions"]), 2)

    def test_budget_exhaustion_is_not_success(self):
        with contextlib.redirect_stdout(io.StringIO()):
            summary, events, _ = run_demo(self.temp.name, max_steps=3)
        self.assertEqual(summary["status"], "budget_exhausted")
        self.assertEqual(len(events), 3)
        self.assertFalse(summary["evaluation"]["passed"])
        self.assertEqual(summary["evaluation"]["regressions"]["status"], "passed")
        self.assertEqual(summary["evaluation"]["requirements"]["status"], "failed")
        self.assertEqual(summary["final_diff"], "")


class TestToolBoundaries(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "workspace"
        shutil.copytree(FIXTURE, self.workspace)
        self.tools = Tools(self.workspace)

    def tearDown(self):
        self.temp.cleanup()

    def test_read_and_edit_allowlists(self):
        with self.assertRaises(ValueError):
            self.tools.read_file("../evaluation/hidden_timeout_tests.py")
        with self.assertRaises(ValueError):
            self.tools.replace_text("tests/test_visible.py", "import unittest", "")
        with self.assertRaises(ValueError):
            self.tools.dispatch("shell", {"command": "anything"})

    def test_invalid_or_ambiguous_patch_leaves_file_unchanged(self):
        path = self.workspace / "config.py"
        before = path.read_bytes()
        with self.assertRaises(SyntaxError):
            self.tools.replace_text("config.py", "    return 5\n", "    return (\n")
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.tools.replace_text("config.py", "not in the file", "anything")
        self.assertEqual(path.read_bytes(), before)

    def test_finish_requires_tests_for_latest_code(self):
        self.assertFalse(self.tools.finish("done")["accepted"])
        # This simulates a previous passing result, then a new real file edit.
        self.tools.last_tests = {"status": "passed", "total": 8, "passed": 8}
        self.assertTrue(self.tools.finish("done")["accepted"])
        self.tools.replace_text("client.py", '"timeout": 5', '"timeout": timeout')
        self.assertFalse(self.tools.finish("done")["accepted"])

    def test_missing_test_file_is_environment_error(self):
        result = run_suite(self.workspace, self.workspace / "missing-tests.py")
        self.assertEqual(result["status"], "environment_error")
        self.assertEqual(result["total"], 0)

    def test_skipped_tests_cannot_be_accepted_as_passed(self):
        suite = self.workspace / "skip_tests.py"
        suite.write_text('import unittest\nclass Skipped(unittest.TestCase):\n'
                         '    @unittest.skip("teaching check")\n'
                         '    def test_skipped(self): pass\n', encoding="utf-8")
        result = run_suite(self.workspace, suite)
        self.assertEqual(result["status"], "failed")
        self.assertEqual((result["total"], result["passed"], result["skipped"]), (1, 0, 1))
        self.tools.last_tests = result
        self.assertFalse(self.tools.finish("done")["accepted"])

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

    def test_import_failure_cause_is_not_assumed_to_be_environment(self):
        suite = self.workspace / "import_tests.py"
        suite.write_text('from config import a_function_missing_after_a_patch\n', encoding="utf-8")
        self.assertEqual(run_suite(self.workspace, suite)["status"], "test_load_error")

    def test_timeout_cause_is_left_unclassified(self):
        suite = self.workspace / "slow_tests.py"
        suite.write_text('import time\ntime.sleep(1)\n', encoding="utf-8")
        self.assertEqual(run_suite(self.workspace, suite, timeout=0.1)["status"], "execution_timeout")

    def test_invalid_tool_action_stops_with_record(self):
        class BadPolicy:
            def next_action(self, history):
                return Action("shell", {"command": "not executed"})

        with contextlib.redirect_stdout(io.StringIO()):
            history, status, _ = Agent(self.tools).run(BadPolicy(), Path(self.temp.name) / "trace.jsonl")
        self.assertEqual(status, "policy_failed")
        self.assertEqual(len(history), 1)
        self.assertIn("error", history[0]["observation"])


class TestReport(unittest.TestCase):
    def test_repository_text_cannot_break_embedded_data(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "report.html"
            content = '</script><script>alert("not executed")</script>&'
            write_report({"task": content}, [], destination)
            html = destination.read_text(encoding="utf-8")
            marker = '<script type="application/json" id="report-data">'
            embedded = html.split(marker, 1)[1].split("</script>", 1)[0]
            self.assertNotIn("<script>", embedded)
            self.assertEqual(json.loads(embedded)["summary"]["task"], content)


class TestInterfaceContract(unittest.TestCase):
    """Guard the v1 contracts recorded in docs/interfaces.md."""

    REQUIRED_EVENT_FIELDS = {"step", "phase", "intent", "action", "observation", "elapsed_ms"}

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        with contextlib.redirect_stdout(io.StringIO()):
            cls.summary, cls.events, cls.run_dir = run_demo(cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_trajectory_schema_matches_v1(self):
        for event in self.events:
            self.assertTrue(self.REQUIRED_EVENT_FIELDS <= set(event))
            self.assertTrue({"tool", "args"} <= set(event["action"]))
            self.assertIsInstance(event["elapsed_ms"], int)
        self.assertEqual(self.summary["versions"],
                         {"action": "v1", "tools": "v1", "prompt": "v1", "trajectory": "v1"})

    def test_tool_catalog_is_the_single_source(self):
        tools = Tools(self.run_dir / "workspace")
        self.assertEqual(set(TOOL_SPECS), tools.allowed_tools)
        self.assertEqual(set(TOOL_SPECS), set(tool_labels()))
        table = tool_table()
        for name, spec in TOOL_SPECS.items():
            self.assertIn(name, table)
            self.assertIn(spec["doc"], table)

    def test_report_labels_come_from_the_catalog(self):
        destination = Path(self.temp.name) / "labels.html"
        write_report({"task": "labels"}, [], destination)
        html = destination.read_text(encoding="utf-8")
        marker = '<script type="application/json" id="report-data">'
        embedded = html.split(marker, 1)[1].split("</script>", 1)[0]
        self.assertEqual(json.loads(embedded)["labels"], tool_labels())

    def test_malformed_tool_args_stop_with_error_observation(self):
        class MissingArgsPolicy:
            def next_action(self, history):
                return Action("replace_text", {"path": "config.py"})

        with contextlib.redirect_stdout(io.StringIO()):
            history, status, _ = Agent(Tools(self.run_dir / "workspace")).run(
                MissingArgsPolicy(), Path(self.temp.name) / "bad-args.jsonl")
        self.assertEqual(status, "policy_failed")
        self.assertEqual(len(history), 1)
        self.assertIn("error", history[0]["observation"])


class TestLLMPolicy(unittest.TestCase):
    """Adapter behavior with a fake transport; no network is touched."""

    @staticmethod
    def reply(content, prompt=10, completion=5):
        return {"choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": prompt, "completion_tokens": completion}}

    def test_parses_tool_call_and_records_usage(self):
        sent = []

        def transport(payload):
            sent.append(payload)
            return self.reply('{"tool": "run_tests", "args": {}}')

        policy = LLMPolicy(task="t", model="m", transport=transport)
        action = policy.next_action([])
        self.assertEqual((action.tool, action.args, action.phase), ("run_tests", {}, "验证"))
        self.assertEqual(policy.calls, 1)
        self.assertEqual(policy.last_usage["prompt_tokens"], 10)
        self.assertEqual(policy.last_usage["attempts"], 1)
        self.assertIsNone(policy.last_usage["last_error"])
        self.assertIn("run_tests", sent[0]["messages"][0]["content"])

    def test_retries_once_then_succeeds(self):
        replies = iter([self.reply("not json"),
                        self.reply('{"tool": "finish", "args": {"reason": "ok"}}')])
        policy = LLMPolicy(task="t", model="m", transport=lambda payload: next(replies))
        self.assertEqual(policy.next_action([]).tool, "finish")
        self.assertEqual(policy.calls, 2)
        self.assertEqual(policy.last_usage["prompt_tokens"], 20)
        self.assertEqual(policy.last_usage["attempts"], 2)
        self.assertTrue(policy.last_usage["last_error"].startswith("JSONDecodeError"))

    def test_unknown_tool_fails_after_two_attempts(self):
        policy = LLMPolicy(task="t", model="m",
                           transport=lambda payload: self.reply('{"tool": "shell", "args": {}}'))
        with self.assertRaises(RuntimeError):
            policy.next_action([])
        self.assertEqual(policy.calls, 2)
        self.assertEqual(policy.last_usage["attempts"], 2)
        self.assertIn("unknown tool", policy.last_usage["last_error"])

    def test_call_log_records_every_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "llm_calls.jsonl"
            replies = iter([self.reply("not json"),
                            self.reply('{"tool": "finish", "args": {"reason": "ok"}}')])
            policy = LLMPolicy(task="t", model="m",
                               transport=lambda payload: next(replies), log_path=path)
            policy.next_action([])
            records = [json.loads(line)
                       for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([record["attempt"] for record in records], [1, 2])
        self.assertIn("JSONDecodeError", records[0]["error"])
        self.assertIsNone(records[1]["error"])
        self.assertTrue(records[0]["messages"][0]["content"].startswith("你是一个仓库维护 agent"))
        self.assertEqual(records[1]["raw_content"],
                         '{"tool": "finish", "args": {"reason": "ok"}}')
        self.assertEqual(records[1]["step"], 1)

    def test_accepts_json_code_fence(self):
        policy = LLMPolicy(task="t", model="m", transport=lambda payload: self.reply(
            '```json\n{"tool": "run_tests", "args": {}}\n```'))
        self.assertEqual(policy.next_action([]).tool, "run_tests")

    def test_accepts_json_embedded_in_prose(self):
        policy = LLMPolicy(task="t", model="m", transport=lambda payload: self.reply(
            '我选择读取配置：{"tool": "read_file", "args": {"path": "config.py"}} 完成'))
        action = policy.next_action([])
        self.assertEqual((action.tool, action.args), ("read_file", {"path": "config.py"}))

    def test_json_mode_off_omits_response_format(self):
        sent = []

        def transport(payload):
            sent.append(payload)
            return self.reply('{"tool": "run_tests", "args": {}}')

        LLMPolicy(task="t", model="m", transport=transport, json_mode="off").next_action([])
        self.assertNotIn("response_format", sent[0])

    def test_policy_from_env_falls_back_to_legacy_names(self):
        names = ("LLM_API_KEY", "DEEPSEEK_API_KEY", "LLM_MODEL", "DEEPSEEK_MODEL",
                 "LLM_BASE_URL", "DEEPSEEK_BASE_URL", "LLM_JSON_MODE")
        saved = {name: os.environ.get(name) for name in names}
        try:
            for name in names:
                os.environ.pop(name, None)
            os.environ["DEEPSEEK_API_KEY"] = "legacy-key"
            os.environ["DEEPSEEK_MODEL"] = "legacy-model"
            os.environ["DEEPSEEK_BASE_URL"] = "https://example.test/v1"
            with tempfile.TemporaryDirectory() as temp:
                policy = policy_from_env("t", env_path=Path(temp) / "missing.env")
            self.assertEqual(policy.model, "legacy-model")
            self.assertEqual(policy.endpoint, "https://example.test/v1/chat/completions")
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_env_loader_does_not_override_existing_values(self):
        saved = {name: os.environ.get(name)
                 for name in ("LLM_MODEL", "LLM_API_KEY")}
        try:
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / ".env"
                path.write_text("LLM_MODEL=from_file\nLLM_API_KEY=k\n",
                                encoding="utf-8")
                os.environ["LLM_MODEL"] = "keep"
                os.environ.pop("LLM_API_KEY", None)
                load_env(path)
                self.assertEqual(os.environ["LLM_MODEL"], "keep")
                self.assertEqual(os.environ["LLM_API_KEY"], "k")
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
