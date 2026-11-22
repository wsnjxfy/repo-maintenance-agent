"""Agent loop contract tests with a fake tool transport."""

import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent_core.agent import Action, Agent


class FakeTools:
    def __init__(self, observations):
        self.observations = list(observations)
        self.calls = 0

    def dispatch(self, tool, args):
        observation = self.observations[min(self.calls, len(self.observations) - 1)]
        self.calls += 1
        return observation


class SequencePolicy:
    def __init__(self, tools):
        self.tools = tools

    def next_action(self, history):
        index = len(history)
        return Action(self.tools[index], {}) if index < len(self.tools) else None


class AgentContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hj10-agent-")
        self.path = Path(self.temp.name) / "trajectory.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def run_agent(self, observations, tools, max_steps=6):
        fake = FakeTools(observations)
        with contextlib.redirect_stdout(io.StringIO()):
            history, status, reason = Agent(fake, max_steps=max_steps).run(
                SequencePolicy(tools), self.path)
        return history, status, reason

    def test_tool_error_is_recoverable(self):
        history, status, _ = self.run_agent(
            [{"ok": False, "error": "ValueError: bad args", "kind": "tool"},
             {"ok": True, "result": {"status": "failed"}}],
            ["read_lines", "run_visible_tests"])
        self.assertEqual(len(history), 2)
        self.assertFalse(history[0]["observation"]["ok"])
        self.assertEqual(status, "incomplete")

    def test_three_consecutive_errors_stop_as_policy_failed(self):
        history, status, _ = self.run_agent(
            [{"ok": False, "error": "ValueError: nope", "kind": "tool"}],
            ["read_lines", "read_lines", "read_lines"])
        self.assertEqual(status, "policy_failed")
        self.assertEqual(len(history), 3)

    def test_fatal_observation_stops_incomplete(self):
        history, status, _ = self.run_agent(
            [{"ok": True, "result": {"status": "execution_timeout"}}],
            ["run_visible_tests"])
        self.assertEqual(status, "incomplete")
        self.assertEqual(len(history), 1)

    def test_finish_with_deferred_acceptance_completes(self):
        history, status, reason = self.run_agent(
            [{"ok": True, "tool": "finish",
              "result": {"finished": True, "accepted": None, "reason": "done"}}],
            ["finish"])
        self.assertEqual(status, "completed")
        self.assertEqual(reason, "done")

    def test_dispatch_exception_becomes_a_recoverable_error(self):
        class RaisingTools:
            def dispatch(self, tool, args):
                raise OSError("boom")

        with contextlib.redirect_stdout(io.StringIO()):
            history, status, _ = Agent(RaisingTools(), max_steps=3).run(
                SequencePolicy(["read_lines", "read_lines", "read_lines"]), self.path)
        self.assertEqual(status, "policy_failed")
        self.assertEqual(len(history), 3)
