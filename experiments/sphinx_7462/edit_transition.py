"""Detect repeated localization before the first successful edit."""

from collections import defaultdict
from pathlib import PurePosixPath


class EditTransition:
    def __init__(self, edit_tool):
        self.edit_tool = edit_tool
        self.source_lines = defaultdict(set)
        self.empty_searches = set()
        self.repeats = 0
        self.finished = False

    @property
    def prompt(self):
        return (
            f"If the source you have read supports a concrete local change, use {self.edit_tool} for one small edit now. "
            "If you still need evidence, make one focused lookup for the specific information needed to decide how to edit. "
            "Return exactly one tool action."
        )

    def observe(self, action, response):
        """Return the trigger reason once, after two redundant actions."""
        if self.finished or response["status"] != "ok":
            return None
        tool = action["tool"]
        if tool == self.edit_tool:
            self.finished = True
            return None
        result = response["result"]
        reason = None
        if tool == "read_lines":
            path = PurePosixPath(result["path"])
            if path.suffix != ".py" or any(part.lower() in {"test", "tests", "testing"} for part in path.parts[:-1]):
                return None
            if path.name.startswith("test_") or path.name.endswith("_test.py") or path.name in {"test.py", "conftest.py"}:
                return None
            lines = {item["line"] for item in result["lines"]}
            if lines:
                seen = self.source_lines[path]
                if lines <= seen:
                    reason = "repeated_read"
                seen.update(lines)
        elif tool == "search_text" and result["total"] == 0:
            args = action["args"]
            path = PurePosixPath(args.get("path", ".").replace("\\", "/"))
            case_sensitive = bool(args.get("case_sensitive", True))
            query = args["query"] if case_sensitive else args["query"].casefold()
            key = (path, case_sensitive, query)
            if self.source_lines and key in self.empty_searches:
                reason = "repeated_empty_search"
            self.empty_searches.add(key)
        if reason:
            self.repeats += 1
            if self.repeats == 2:
                self.finished = True
                return reason
        return None
