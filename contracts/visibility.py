"""Instance-level visibility: which paths the model may read or edit.

Patterns are POSIX globs relative to the workspace. ``**`` matches any number
of path segments, ``*`` matches within one segment, and a pattern without a
slash matches a basename at any depth (``conftest.py``).

Visibility is data, not behaviour: handlers and toolsets never carry file
scopes; ``LocalEnvironment`` enforces this object on every primitive.
"""

from __future__ import annotations

import re

from contracts.protected import is_protected as protected_rule


_CACHE: dict[str, re.Pattern] = {}


def _compile(pattern: str) -> re.Pattern:
    if pattern in _CACHE:
        return _CACHE[pattern]
    index = 0
    parts = []
    while index < len(pattern):
        if pattern.startswith("**/", index):
            parts.append("(?:[^/]+/)*")
            index += 3
        elif pattern.startswith("**", index):
            parts.append(".*")
            index += 2
        elif pattern[index] == "*":
            parts.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(pattern[index]))
            index += 1
    compiled = re.compile("^" + "".join(parts) + "$")
    _CACHE[pattern] = compiled
    return compiled


def matches(path: str, pattern: str) -> bool:
    path = path.replace("\\", "/").strip("/")
    pattern = pattern.replace("\\", "/").strip("/")
    if not path or not pattern:
        return False
    if "/" not in pattern:
        pattern = "**/" + pattern
    return bool(_compile(pattern).match(path))


class Visibility:
    def __init__(self, readable=("**",), editable=("**",), protected=()):
        self.readable = tuple(readable)
        self.editable = tuple(editable)
        self.protected = tuple(protected)

    def allows_read(self, path: str) -> bool:
        return any(matches(path, pattern) for pattern in self.readable)

    def _protected(self, path: str) -> bool:
        if any(matches(path, pattern) for pattern in self.protected):
            return True
        return protected_rule(path)

    def allows_edit(self, path: str) -> bool:
        if not self.allows_read(path):
            return False
        if not any(matches(path, pattern) for pattern in self.editable):
            return False
        return not self._protected(path)

    def filter(self, paths):
        return [path for path in paths if self.allows_read(path)]

    def __repr__(self):
        return ("Visibility(readable=%r, editable=%r, protected=%r)"
                % (self.readable, self.editable, self.protected))
