"""Protected-path rules shared by preparation, tools and evaluation.

Two levels are kept on purpose:

- ``protected_source``: what a source snapshot treats as read-only test/config
  material (used by prepare and by replacement-style tools).
- ``is_protected``: the run-level rule, which also honours
  ``config["protected_paths"]`` recorded at preparation time.
"""

from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath


CONFIG_NAMES = frozenset({
    "conftest.py", "pytest.ini", "setup.cfg", "tox.ini", "pyproject.toml",
    "setup.py", "manifest.in", ".coveragerc", "pip.conf", "pip.ini",
})

TEST_DIRS = frozenset({"test", "tests", "testing"})


def protected_source(path: str) -> bool:
    """Snapshot-level rule: test directories, config names, requirements files."""
    parts = tuple(part.casefold() for part in PurePosixPath(path).parts)
    name = parts[-1]
    return (
        "tests" in parts[:-1]
        or name in CONFIG_NAMES
        or fnmatch.fnmatch(name, "requirements*.txt")
    )


def is_protected(name: str, protected_paths=()) -> bool:
    """Run-level rule: source rule plus test files and recorded protected paths."""
    parts = name.replace("\\", "/").casefold().split("/")
    leaf = parts[-1]
    if any(part in TEST_DIRS for part in parts[:-1]):
        return True
    if leaf.startswith("test_") or leaf.endswith("_test.py") or leaf == "test.py":
        return True
    if leaf in CONFIG_NAMES or (leaf.startswith("requirements") and leaf.endswith(".txt")):
        return True
    for value in protected_paths:
        value = value.replace("\\", "/")
        if name.casefold() == value.casefold() or (
            value.endswith("/") and name.casefold().startswith(value.casefold())
        ):
            return True
    return False
