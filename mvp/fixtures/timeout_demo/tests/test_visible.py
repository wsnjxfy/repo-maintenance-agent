"""Visible development tests: the teaching policy may inspect these failures."""

import unittest

from cli import prepare_request
from config import resolve_timeout


class TestTimeoutMaintenance(unittest.TestCase):
    def test_default_timeout(self):
        self.assertEqual(resolve_timeout(), 5)

    def test_configuration_timeout(self):
        self.assertEqual(resolve_timeout(config_value=12), 12)

    def test_command_line_overrides_configuration(self):
        self.assertEqual(resolve_timeout(config_value=12, cli_value=3), 3)

    def test_zero_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_timeout(config_value=0)

    def test_zero_command_line_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_timeout(config_value=12, cli_value=0)

    def test_negative_timeout_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_timeout(cli_value=-1)

    def test_boolean_timeout_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_timeout(cli_value=True)

    def test_timeout_reaches_request(self):
        request = prepare_request(
            "https://example.test/file", config_value=12, cli_value=2.5
        )
        self.assertEqual(request, {"url": "https://example.test/file", "timeout": 2.5})


if __name__ == "__main__":
    unittest.main()
