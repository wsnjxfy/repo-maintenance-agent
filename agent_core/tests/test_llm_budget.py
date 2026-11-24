"""Budget and token-cap tests for LLMPolicy (no network)."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent_core.agent import Agent, PolicyBudgetExceeded
from agent_core.llm import LLMPolicy


def reply(tool="list_files", args=None):
    content = json.dumps({"tool": tool, "args": args or {"path": "."}})
    return {"choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


class BudgetTests(unittest.TestCase):
    def policy(self, **kwargs):
        kwargs.setdefault("transport", lambda payload: reply())
        return LLMPolicy(task="Synthetic", api_key="test", model="fake",
                         toolset="toy-v2", **kwargs)

    def test_cost_budget_blocks_before_request(self):
        calls = []

        def transport(payload):
            calls.append(payload)
            return reply()

        policy = self.policy(transport=transport, budget_usd="0.000001",
                             input_price=1_000_000, output_price=1_000_000)
        with self.assertRaises(PolicyBudgetExceeded):
            policy.next_action([])
        self.assertEqual(calls, [])
        self.assertEqual(policy.calls, 0)

    def test_token_cap_blocks_before_request(self):
        policy = self.policy(max_total_tokens=10, max_tokens=4096)
        with self.assertRaises(PolicyBudgetExceeded):
            policy.next_action([])
        self.assertEqual(policy.calls, 0)

    def test_normal_request_accounts_usage(self):
        policy = self.policy(input_price=1.0, output_price=1.0)
        action = policy.next_action([])
        self.assertEqual(action.tool, "list_files")
        self.assertEqual(policy.total_tokens, 15)
        self.assertAlmostEqual(float(policy.spent_usd), 0.000015)

    def test_invalid_budget_rejected(self):
        with self.assertRaises(ValueError):
            self.policy(budget_usd="0")
        with self.assertRaises(ValueError):
            self.policy(max_total_tokens=0)


class _NoopTools:
    def dispatch(self, tool, args):
        raise AssertionError("budget stop should happen before dispatch")


class _BudgetStopPolicy:
    def next_action(self, history):
        raise PolicyBudgetExceeded("budget stop")


class AgentBudgetTests(unittest.TestCase):
    def test_agent_reports_budget_exhausted(self):
        with tempfile.TemporaryDirectory(prefix="hj10-budget-agent-") as temp:
            history, status, reason = Agent(_NoopTools(), max_steps=3).run(
                _BudgetStopPolicy(), Path(temp) / "trajectory.jsonl")
        self.assertEqual(history, [])
        self.assertEqual(status, "budget_exhausted")
        self.assertIn("budget stop", reason)
