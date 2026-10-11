"""Evaluator-only tests; never copy this file into the agent's task workspace.

These source-controlled tests demonstrate separation of agent tools and evaluation.
Their public availability does not make them a private research benchmark.
"""

import unittest

from cli import prepare_request
from client import build_request
from config import resolve_timeout


class TestRequirements(unittest.TestCase):
    def test_configuration_numbers_reach_client(self):
        for value in (17, 0.25, 1_000_000):
            self.assertEqual(resolve_timeout(config_value=value), value)
            self.assertEqual(
                build_request("asset", value), {"url": "asset", "timeout": value}
            )
            self.assertEqual(
                prepare_request("asset", config_value=value),
                {"url": "asset", "timeout": value},
            )

    def test_cli_priority_validates_only_selected_value(self):
        for ignored_config in (0, -2, False, "invalid", float("nan")):
            self.assertEqual(resolve_timeout(ignored_config, 1.75), 1.75)
            self.assertEqual(
                prepare_request("asset", ignored_config, 1.75),
                {"url": "asset", "timeout": 1.75},
            )

    def test_zero_final_values_are_rejected(self):
        for config_value, cli_value in ((0, None), (7, 0), (None, 0)):
            with self.assertRaises(ValueError):
                resolve_timeout(config_value, cli_value)

    def test_negative_final_values_are_rejected(self):
        for config_value, cli_value in ((-2, None), (7, -0.5)):
            with self.assertRaises(ValueError):
                resolve_timeout(config_value, cli_value)

    def test_non_finite_final_values_are_rejected(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            for config_value, cli_value in ((value, None), (7, value)):
                with self.assertRaises(ValueError):
                    resolve_timeout(config_value, cli_value)

    def test_string_final_values_are_rejected(self):
        for value in ("8", "", "invalid"):
            for config_value, cli_value in ((value, None), (7, value)):
                with self.assertRaises(ValueError):
                    resolve_timeout(config_value, cli_value)

    def test_boolean_final_values_are_rejected(self):
        for value in (True, False):
            for config_value, cli_value in ((value, None), (7, value)):
                with self.assertRaises(ValueError):
                    resolve_timeout(config_value, cli_value)


class TestRegressions(unittest.TestCase):
    def test_default_timeout_remains_five(self):
        self.assertEqual(resolve_timeout(), 5)

    def test_direct_default_request_preserves_url_and_shape(self):
        self.assertEqual(
            build_request("https://example.test/original"),
            {"url": "https://example.test/original", "timeout": 5},
        )

    def test_default_cli_request_preserves_url_and_shape(self):
        self.assertEqual(
            prepare_request("https://example.test/original"),
            {"url": "https://example.test/original", "timeout": 5},
        )


if __name__ == "__main__":
    unittest.main()
