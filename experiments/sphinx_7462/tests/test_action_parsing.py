"""Offline checks for the baseline action parser and call records."""

import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from experiments.sphinx_7462.baseline import ModelReply, _model_call, _parse_with_fallback, _solve


READ = {"tool": "read_lines", "args": {"path": "example.py", "start": 1}}
FINISH = {"tool": "finish", "args": {"reason": "done"}}


class ActionParsingTests(unittest.TestCase):
    def test_exact_action_uses_strict_parser(self):
        self.assertEqual(_parse_with_fallback(json.dumps(READ)), (READ, None))

    def test_fallback_uses_first_json_object(self):
        response = f"Inspect the file first.\n{json.dumps(READ)}\n{json.dumps(FINISH)}"
        self.assertEqual(_parse_with_fallback(response), (READ, 1))

    def test_fallback_normalizes_top_level_parameters(self):
        response = json.dumps({"tool": "read_lines", "path": "example.py", "start": 1})
        self.assertEqual(_parse_with_fallback(response), (READ, 0))

    def test_fallback_rejects_malformed_explicit_args(self):
        for bad_args in ([], None, "invalid JSON", "[]"):
            with self.subTest(bad_args=bad_args):
                response = json.dumps({"tool": "read_lines", "args": bad_args,
                                       "path": "example.py", "start": 1})
                with self.assertRaises(ValueError):
                    _parse_with_fallback(response)

    def test_fallback_rejects_unknown_first_tool(self):
        response = f'{{"tool":"unknown","args":{{}}}}\n{json.dumps(FINISH)}'
        with self.assertRaises(ValueError):
            _parse_with_fallback(response)

    def test_model_call_records_parser_and_extra_objects(self):
        strict = json.dumps(READ)
        cases = [
            (strict, "strict", None),
            (f"```json\n{strict}\n```", "fallback", 0),
            (f"{strict}\n{json.dumps(FINISH)}", "fallback", 1),
        ]
        for text, parser, extras in cases:
            with self.subTest(parser=parser, extras=extras):
                model = mock.Mock(return_value=ModelReply(text, 10, 2))
                call, action, stop = _model_call(model, {}, 1, time.monotonic() + 10)
                self.assertEqual(action, READ)
                self.assertIsNone(stop)
                self.assertEqual(call["status"], "ok")
                self.assertEqual(call["parser"], parser)
                if extras is None:
                    self.assertNotIn("extra_objects", call)
                else:
                    self.assertEqual(call["extra_objects"], extras)
                self.assertEqual(call["response"], text)
                self.assertIsNone(call["tool_event_id"])
                model.assert_called_once()

    def test_next_request_identifies_skipped_actions_after_first_dispatch(self):
        search = {"tool": "search_text", "args": {"query": "example"}}
        search_text = json.dumps(search)
        multiple = f"{search_text}\n{json.dumps(READ)}"
        cases = (
            (multiple, "ok", "Executed only the first action (search_text)."),
            (multiple, "error", "Attempted only the first action (search_text), which returned an error."),
            (f"```json\n{search_text}\n```", "ok", None),
            (search_text, "ok", None),
        )
        for reply, status, expected in cases:
            with self.subTest(status=status, expected=expected), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / "state.json").write_text('{"action_count": 1}', encoding="utf-8")
                search_result = {"action": "search_text", "status": status, "event_id": 1,
                                 "result": {"matches": []}, "error": "simulated search error"}
                finish_result = {"action": "finish", "status": "ok", "event_id": 2,
                                 "result": {"submission_saved": True}}
                dispatch = mock.Mock(side_effect=[search_result, finish_result])
                runner = SimpleNamespace(controller=root, run_dir=root, dispatch=dispatch)
                model = mock.Mock(side_effect=[ModelReply(reply), ModelReply(json.dumps(FINISH))])
                summary = {"workspace_actions": 0}

                _solve(runner, {}, model, max_actions=3, max_model_calls=2,
                       wall_time_seconds=10, summary=summary)

                self.assertEqual([call.args for call in dispatch.call_args_list],
                                 [("search_text", search["args"]), ("finish", FINISH["args"])])
                self.assertEqual(summary["stop_reason"], "model_finished")
                observation, = model.call_args_list[1].args[0]["history"]
                if expected is None:
                    self.assertNotIn("action_feedback", observation)
                else:
                    feedback = observation["action_feedback"]
                    self.assertTrue(feedback.startswith(expected), feedback)
                    self.assertIn("Skipped 1 additional JSON object", feedback)
                    self.assertIn("return exactly one next action", feedback)
