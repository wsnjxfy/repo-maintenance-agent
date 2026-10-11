"""Connect command-line/configuration values to the request client."""

from client import build_request
from config import resolve_timeout


def prepare_request(url, config_value=None, cli_value=None):
    timeout = resolve_timeout(config_value, cli_value)
    return build_request(url, timeout)
