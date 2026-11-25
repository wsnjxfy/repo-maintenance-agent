"""LLM adapter tests (moved from mvp/tests)."""

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent_core.llm import PROMPT_VERSION, LLMPolicy, load_env, policy_from_env


class TestLLMPolicy(unittest.TestCase):
    """Adapter behavior with a fake transport; no network is touched."""

    def test_default_env_path_points_to_repository_root(self):
        expected = Path(__file__).resolve().parents[2] / ".env"
        environment = {
            "LLM_API_KEY": "offline-test-key",
            "LLM_MODEL": "offline-test-model",
            "LLM_BASE_URL": "https://example.test/v1",
        }
        with mock.patch.dict(os.environ, environment, clear=True), \
             mock.patch("agent_core.llm.load_env") as loader:
            policy = policy_from_env("t")
        loader.assert_called_once_with(expected)
        self.assertEqual(policy.api_key, "offline-test-key")
        self.assertEqual(policy.endpoint, "https://example.test/v1/chat/completions")

    @staticmethod
    def reply(content, prompt=10, completion=5):
        return {"choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": prompt, "completion_tokens": completion}}

    def test_parses_tool_call_and_records_usage(self):
        sent = []

        def transport(payload):
            sent.append(payload)
            return self.reply('{"tool": "run_visible_tests", "args": {}}')

        policy = LLMPolicy(task="t", model="m", transport=transport)
        action = policy.next_action([])
        self.assertEqual((action.tool, action.args, action.phase), ("run_visible_tests", {}, "验证"))
        self.assertEqual(policy.calls, 1)
        self.assertEqual(policy.last_usage["prompt_tokens"], 10)
        self.assertEqual(policy.last_usage["attempts"], 1)
        self.assertIsNone(policy.last_usage["last_error"])
        self.assertIn("run_visible_tests", sent[0]["messages"][0]["content"])

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
        self.assertIn("not enabled", policy.last_usage["last_error"])

    def test_explicit_tool_subset_drives_prompt_and_validation(self):
        sent = []

        def transport(payload):
            sent.append(payload)
            return self.reply('{"tool": "list_files", "args": {"path": "."}}')

        policy = LLMPolicy(task="t", model="m", transport=transport,
                           tools=["list_files", "finish"])
        action = policy.next_action([])
        self.assertEqual(action.tool, "list_files")
        system = sent[0]["messages"][0]["content"]
        self.assertIn("- list_files(", system)
        # A tool that exists in the catalog but is not in this run's subset is
        # absent from the advertised tool table.
        self.assertNotIn("- read_lines(", system)

    def test_tool_outside_subset_is_rejected(self):
        policy = LLMPolicy(
            task="t", model="m", tools=["list_files", "finish"],
            transport=lambda payload: self.reply('{"tool": "replace_text", "args": {}}'))
        with self.assertRaises(RuntimeError):
            policy.next_action([])
        self.assertIn("not enabled", policy.last_usage["last_error"])

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
        self.assertTrue(records[0]["messages"][0]["content"].startswith("你是仓库维护 agent"))
        self.assertEqual(records[1]["raw_content"],
                         '{"tool": "finish", "args": {"reason": "ok"}}')
        self.assertEqual(records[1]["step"], 1)

    def test_accepts_json_code_fence(self):
        policy = LLMPolicy(task="t", model="m", transport=lambda payload: self.reply(
            '```json\n{"tool": "run_visible_tests", "args": {}}\n```'))
        self.assertEqual(policy.next_action([]).tool, "run_visible_tests")

    def test_accepts_json_embedded_in_prose(self):
        policy = LLMPolicy(task="t", model="m", transport=lambda payload: self.reply(
            '我选择读取配置：{"tool": "read_lines", "args": {"path": "config.py"}} 完成'))
        action = policy.next_action([])
        self.assertEqual((action.tool, action.args), ("read_lines", {"path": "config.py"}))

    def test_json_mode_off_omits_response_format(self):
        sent = []

        def transport(payload):
            sent.append(payload)
            return self.reply('{"tool": "run_visible_tests", "args": {}}')

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
            self.assertEqual(policy.max_tokens, 4096)
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    def test_multiple_actions_uses_first_and_flags_extra(self):
        payloads = []

        def transport(payload):
            payloads.append(payload)
            return self.reply('{"tool": "read_lines", "args": {"path": "cli.py"}}\n'
                              '{"tool": "read_lines", "args": {"path": "config.py"}}')

        policy = LLMPolicy(task="t", model="m", transport=transport)
        action = policy.next_action([])
        self.assertEqual((action.tool, action.args), ("read_lines", {"path": "cli.py"}))
        self.assertEqual(policy.last_usage["extra_actions"], 1)
        history = [{"step": 1, "phase": "", "intent": "",
                    "action": {"tool": "read_lines", "args": {"path": "cli.py"}},
                    "observation": {"ok": True}, "elapsed_ms": 0}]
        policy.next_action(history)
        self.assertIn("我只执行了第一个", payloads[1]["messages"][-1]["content"])

    def test_json_array_uses_first_action(self):
        policy = LLMPolicy(task="t", model="m", transport=lambda payload: self.reply(
            '[{"tool": "list_files", "args": {}}, {"tool": "run_visible_tests", "args": {}}]'))
        self.assertEqual(policy.next_action([]).tool, "list_files")
        self.assertEqual(policy.last_usage["extra_actions"], 1)

    def test_top_level_args_are_normalized(self):
        policy = LLMPolicy(task="t", model="m", transport=lambda payload: self.reply(
            '{"tool": "replace_text", "path": "config.py", "old": "a", "new": "b"}'))
        action = policy.next_action([])
        self.assertEqual((action.tool, action.args),
                         ("replace_text", {"path": "config.py", "old": "a", "new": "b"}))

    def test_arguments_json_string_is_parsed(self):
        content = '{"tool": "read_lines", "arguments": "{\\"path\\": \\"config.py\\"}"}'
        policy = LLMPolicy(task="t", model="m", transport=lambda payload: self.reply(content))
        self.assertEqual(policy.next_action([]).args, {"path": "config.py"})

    def test_explicit_args_win_over_top_level_keys(self):
        policy = LLMPolicy(task="t", model="m", transport=lambda payload: self.reply(
            '{"tool": "read_lines", "args": {"path": "cli.py"}, "path": "ignored.py"}'))
        self.assertEqual(policy.next_action([]).args, {"path": "cli.py"})

    def test_prompt_states_args_nesting_with_example(self):
        prompt = LLMPolicy(task="t", model="m").system_prompt()
        self.assertIn('"tool": "replace_text", "args"', prompt)
        self.assertIn("所有参数都放在 args 对象里", prompt)
        self.assertIn("回复以 { 开始、以 } 结束", prompt)
        self.assertNotIn("DSML", prompt)
        self.assertEqual(LLMPolicy(task="t", model="m").prompt_version, PROMPT_VERSION)

    def test_repo_prompt_uses_apply_patch(self):
        prompt = LLMPolicy(task="t", model="m", toolset="repo-v1").system_prompt()
        self.assertIn('"tool": "apply_patch", "args"', prompt)
        self.assertNotIn('"tool": "replace_text"', prompt)

    def test_retry_feedback_asks_for_plain_json_without_parser_details(self):
        sent = []
        replies = iter([self.reply("｜｜DSML｜｜ not json"),
                        self.reply('{"tool": "finish", "args": {"reason": "ok"}}')])

        def transport(payload):
            sent.append(payload)
            return next(replies)

        LLMPolicy(task="t", model="m", transport=transport).next_action([])
        feedback = sent[1]["messages"][-1]["content"]
        self.assertIn("只输出一个 JSON 对象", feedback)
        self.assertNotIn("KeyError", feedback)
        self.assertNotIn("JSONDecodeError", feedback)

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
