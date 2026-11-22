"""Configuration for the v2 toy demo: a fixture-sized instance on LocalEnvironment."""

from contracts.visibility import Visibility


TOOLSET = "toy-v2"

READABLE = (
    "config.py",
    "client.py",
    "cli.py",
    "tests/test_visible.py",
)
EDITABLE = ("config.py", "client.py", "cli.py")
PROTECTED = ("tests/test_visible.py",)

VISIBILITY = Visibility(readable=READABLE, editable=EDITABLE, protected=PROTECTED)

PROTOCOL = {
    "max_actions": 30,
    "wall_time_seconds": 600,
    "test_timeout_seconds": 90,
}
