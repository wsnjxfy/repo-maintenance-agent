"""Toy backend: the seven teaching tools over a fixture-sized workspace.

This backend only knows the four fixture files. It exists so the agent loop can
be exercised in about a second, and so C0/C1 evidence stays reproducible. It is
deliberately *not* general: real repositories use ``runtime.backends.repo``.
"""

import ast
import difflib
from pathlib import Path
import re

from runtime.exec.suite import run_suite
from runtime.text_edit import replace_unique_text
from tools.toolsets import tool_names

class Tools:
    """Only expose known toy files. Tests cannot be edited by the policy.

    ``finish_requires_tests`` keeps the teaching behaviour ("refuse to finish
    unless the latest visible tests pass"). Research runs set it to ``False`` so
    the tool only delivers a result and the harness does the judging.
    """

    def __init__(self, workspace, finish_requires_tests=True):
        self.workspace = Path(workspace).resolve()
        self.editable = {"config.py", "client.py", "cli.py"}
        self.readable = self.editable | {"tests/test_visible.py"}
        self.finish_requires_tests = finish_requires_tests
        self.toolset = "toy-v1"
        self.allowed = set(tool_names(self.toolset))
        self.last_tests = None

    def _path(self, name, *, edit=False):
        allowed = self.editable if edit else self.readable
        if name not in allowed:
            raise ValueError(f"File is outside the allowed {'edit' if edit else 'read'} set: {name}")
        path = (self.workspace / name).resolve()
        if not path.is_relative_to(self.workspace) or path.is_symlink():
            raise ValueError("Path escapes the workspace")
        return path

    def map_repository(self):
        files = []
        for name in sorted(self.readable):
            content = self._path(name).read_text(encoding="utf-8")
            tree = ast.parse(content)
            symbols = [{"name": node.name, "kind": type(node).__name__, "line": node.lineno}
                       for node in ast.walk(tree)
                       if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
            files.append({"path": name, "lines": len(content.splitlines()), "symbols": symbols})
        return {"files": files, "note": "AST definitions; this is not a resolved call graph."}

    def find_symbol(self, symbol):
        if not symbol.isidentifier():
            raise ValueError("Expected a Python identifier")
        definitions, references = [], []
        pattern = re.compile(r"\b" + re.escape(symbol) + r"\b")
        for item in self.map_repository()["files"]:
            name = item["path"]
            definition_lines = set()
            for entry in item["symbols"]:
                if entry["name"] == symbol:
                    definitions.append({"path": name, **entry})
                    definition_lines.add(entry["line"])
            for number, line in enumerate(self._path(name).read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line) and number not in definition_lines:
                    references.append({"path": name, "line": number, "text": line.strip()})
        return {"symbol": symbol, "definitions": definitions, "references": references,
                "note": "References are textual candidates, not resolved runtime dependencies."}

    def read_file(self, path):
        return {"path": path, "content": self._path(path).read_text(encoding="utf-8")}

    def plan(self, steps):
        if not isinstance(steps, list) or not steps or not all(isinstance(s, str) for s in steps):
            raise ValueError("Plan must contain non-empty text steps")
        return {"steps": steps}

    def replace_text(self, path, old, new):
        destination = self._path(path, edit=True)
        before = destination.read_text(encoding="utf-8")
        after = replace_unique_text(before, old, new)
        ast.parse(after, filename=path)  # Reject syntax errors before writing.
        destination.write_text(after, encoding="utf-8", newline="\n")
        self.last_tests = None  # A new patch invalidates earlier test evidence.
        diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                           fromfile=f"a/{path}", tofile=f"b/{path}"))
        return {"path": path, "changed": True, "diff": diff}

    def run_tests(self):
        self.last_tests = run_suite(self.workspace, self._path("tests/test_visible.py"))
        return self.last_tests

    def finish(self, reason):
        if not self.finish_requires_tests:
            return {"accepted": True, "reason": reason}
        accepted = (self.last_tests is not None and self.last_tests["status"] == "passed"
                    and self.last_tests.get("total", 0) > 0
                    and self.last_tests.get("passed") == self.last_tests["total"])
        return {"accepted": accepted, "reason": reason if accepted
                else "Completion rejected: the latest workspace has no passing visible test result."}

    @property
    def allowed_tools(self):
        """Only the tools declared by the active toolset are dispatchable."""
        return set(self.allowed)

    def dispatch(self, tool, args):
        if tool not in self.allowed:
            raise ValueError(f"Unknown tool: {tool}")
        return getattr(self, tool)(**args)
