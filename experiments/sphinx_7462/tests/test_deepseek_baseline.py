"""Paid adapter checks with an in-memory HTTP transport."""

import json
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest import mock

from experiments.sphinx_7462.baseline import DeepSeekModel, ModelReply


REPLY_TEXT = '{"tool":"finish","args":{"reason":"offline check"}}'


def provider_reply(include_usage=True):
    reply = {
        "model": "deepseek-flash",
        "choices": [{"message": {"content": REPLY_TEXT}, "finish_reason": "stop"}],
    }
    if include_usage:
        reply["usage"] = {
            "prompt_tokens": 240, "completion_tokens": 17, "total_tokens": 257,
            "prompt_cache_hit_tokens": 120, "prompt_cache_miss_tokens": 120,
        }
    return reply


class DeepSeekModelTests(unittest.TestCase):
    def setUp(self):
        self.request = {
            "task": {"problem_statement": "Review the local fixture."},
            "instructions": "Return one JSON action.",
            "tools": {"finish": "reason"},
            "history": [],
        }
        self.transport = mock.Mock(return_value=provider_reply())
        self.policy = SimpleNamespace(
            model="deepseek-flash", endpoint="https://api.deepseek.com/chat/completions",
            api_key="offline-placeholder", max_tokens=4096, json_mode="on", timeout=120,
            _post=self.transport, _content=lambda reply: reply["choices"][0]["message"]["content"],
        )

    def test_sends_one_bounded_json_request_and_returns_usage(self):
        self.request["history"] = [
            {"observation": "OLDEST_MARKER " + "源" * 5000},
            {"observation": "LATEST_MARKER"},
        ]
        timeouts = []

        def send(_messages):
            timeouts.append(self.policy.timeout)
            return provider_reply()

        self.transport.side_effect = send
        model = DeepSeekModel(self.policy, budget_usd=2.0)
        reply = model(self.request, timeout_seconds=7.5)
        self.assertIsInstance(reply, ModelReply)
        self.assertEqual((reply.text, reply.input_tokens, reply.output_tokens), (REPLY_TEXT, 240, 17))
        self.assertEqual(model.http_requests, 1)
        self.assertEqual(timeouts, [7.5])
        self.transport.assert_called_once()
        messages = json.dumps(self.transport.call_args.args[0])
        self.assertIn("Review the local fixture.", messages)
        self.assertIn("LATEST_MARKER", messages)
        self.assertNotIn("OLDEST_MARKER", messages)
        self.assertEqual(reply.api["history_items_sent"], 1)
        self.assertAlmostEqual(reply.api["peak_cost_estimate_usd"], 0.00005712)

    def test_insufficient_reservation_refuses_before_transport(self):
        model = DeepSeekModel(self.policy, budget_usd=0.000001)
        with self.assertRaises(RuntimeError):
            model(self.request, timeout_seconds=10)
        self.transport.assert_not_called()
        self.assertEqual(model.http_requests, 0)

    def test_spent_usage_reduces_the_remaining_reservation(self):
        reserve = (DeepSeekModel.PROVIDER_CONTEXT * DeepSeekModel.INPUT_MISS
                   + self.policy.max_tokens * DeepSeekModel.OUTPUT)
        model = DeepSeekModel(self.policy, budget_usd=Decimal(reserve) / 1_000_000_000)
        model(self.request, timeout_seconds=10)
        with self.assertRaises(RuntimeError):
            model(self.request, timeout_seconds=10)
        self.transport.assert_called_once()
        self.assertEqual(model.http_requests, 1)

    def test_missing_usage_blocks_the_next_transport_call(self):
        self.transport.return_value = provider_reply(include_usage=False)
        model = DeepSeekModel(self.policy, budget_usd=2.0)
        reply = model(self.request, timeout_seconds=10)
        self.assertIsNone(reply.input_tokens)
        self.assertIsNone(reply.output_tokens)
        self.assertIsNone(model.summary()["peak_cost_estimate_usd"])
        with self.assertRaises(RuntimeError):
            model(self.request, timeout_seconds=10)
        self.transport.assert_called_once()
        self.assertEqual(model.http_requests, 1)

    def test_failed_transport_is_counted_without_retry(self):
        self.transport.side_effect = RuntimeError("mock transport failure")
        model = DeepSeekModel(self.policy, budget_usd=2.0)
        with self.assertRaises(RuntimeError):
            model(self.request, timeout_seconds=10)
        self.transport.assert_called_once()
        self.assertEqual(model.http_requests, 1)
