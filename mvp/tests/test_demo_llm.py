"""LLM policy through run.py with a fake transport (no network)."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mvp.demo import run
from mvp.scripted_policy import FINAL_CONFIG


def action(tool, args):
    return json.dumps({"tool": tool, "args": args})


REPLIES = [
    action("list_files", {"path": ".", "limit": 20}),
    action("read_lines", {"path": "config.py", "start": 1, "end": 10}),
    action("read_lines", {"path": "client.py", "start": 1, "end": 10}),
    action("read_lines", {"path": "cli.py", "start": 1, "end": 10}),
    action("read_lines", {"path": "tests/test_visible.py", "start": 1, "end": 43}),
    action("run_visible_tests", {"paths": ["tests/test_visible.py"]}),
    action("replace_text", {"path": "config.py", "old": "    return 5\n",
                            "new": FINAL_CONFIG}),
    action("replace_text", {"path": "client.py",
                            "old": '    return {"url": url, "timeout": 5}\n',
                            "new": '    return {"url": url, "timeout": timeout}\n'}),
    action("run_visible_tests", {"paths": ["tests/test_visible.py"]}),
    action("finish", {"reason": "latest visible tests pass"}),
]


class FakeTransport:
    def __init__(self):
        self.payloads = []
        self.replies = list(REPLIES)

    def __call__(self, payload):
        self.payloads.append(payload)
        if not self.replies:
            raise AssertionError("unexpected extra model call")
        return {
            "choices": [{"message": {"content": self.replies.pop(0)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }


class LLMDemoTests(unittest.TestCase):
    def run_llm(self, temp, transport, **kwargs):
        with mock.patch.dict(os.environ, {"LLM_API_KEY": "test-key",
                                          "LLM_MODEL": "fake-model"}):
            return run(temp, policy="llm", model="fake-model",
                       transport=transport, **kwargs)

    def test_llm_policy_completes_with_fake_transport(self):
        transport = FakeTransport()
        with tempfile.TemporaryDirectory(prefix="hj10-llm-demo-") as temp:
            summary, history, run_dir = self.run_llm(temp, transport)
            log = (run_dir / "llm_calls.jsonl").read_text(
                encoding="utf-8").splitlines()

        self.assertEqual(summary["status"], "completed")
        self.assertTrue(summary["evaluation"]["resolved"])
        self.assertEqual(summary["evaluation"]["metrics"]["fail_to_pass"]["passed"], 7)
        self.assertEqual(summary["evaluation"]["metrics"]["pass_to_pass"]["passed"], 3)
        self.assertEqual(summary["model"], "fake-model")
        self.assertEqual(summary["model_calls"], 10)
        self.assertEqual(summary["versions"]["prompt"], "v7")
        self.assertEqual(summary["tokens"], {"prompt": 100, "completion": 50})
        self.assertEqual(summary["llm_log"], "llm_calls.jsonl")
        self.assertEqual(len(transport.payloads), 10)
        self.assertTrue(all("llm" in event for event in history))
        self.assertEqual(len(log), 10)
        self.assertTrue(all(json.loads(line)["error"] is None for line in log))

    def test_scripted_summary_stays_v1(self):
        with tempfile.TemporaryDirectory(prefix="hj10-scripted-") as temp:
            summary, _, _ = run(temp, policy="scripted")
        self.assertEqual(summary["versions"]["prompt"], "v1")
        self.assertEqual(summary["model"], "scripted_teaching")
        self.assertEqual(summary["model_calls"], 0)
        self.assertIsNone(summary["tokens"])
        self.assertIsNone(summary["llm_log"])

    def test_no_llm_log_option(self):
        transport = FakeTransport()
        with tempfile.TemporaryDirectory(prefix="hj10-no-log-") as temp:
            summary, _, run_dir = self.run_llm(temp, transport, log_llm=False)
            log_exists = (run_dir / "llm_calls.jsonl").exists()
        self.assertIsNone(summary["llm_log"])
        self.assertFalse(log_exists)

    def test_prompt_lists_only_toy_v2_tools(self):
        transport = FakeTransport()
        with tempfile.TemporaryDirectory(prefix="hj10-prompt-") as temp:
            self.run_llm(temp, transport)
        prompt = transport.payloads[0]["messages"][0]["content"]
        for tool in ("list_files", "read_lines", "replace_text",
                     "run_visible_tests", "finish"):
            self.assertIn(tool, prompt)
        self.assertNotIn("apply_patch", prompt)
        self.assertNotIn("map_repository", prompt)
        self.assertNotIn("run_tests", prompt)
