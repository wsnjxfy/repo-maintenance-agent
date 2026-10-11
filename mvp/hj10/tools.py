"""Small tools with an explicit workspace boundary and a fixed test command."""

import ast
import difflib
import json
from pathlib import Path
import re
import subprocess
import sys


MVP_ROOT = Path(__file__).resolve().parents[1]


def run_suite(workspace, test_file, class_name=None, timeout=15):
    command = [sys.executable, "-I", "-B", str(MVP_ROOT / "test_runner.py"),
               "--workspace", str(Path(workspace).resolve()),
               "--test-file", str(Path(test_file).resolve())]
    if class_name:
        command.extend(["--class-name", class_name])
    try:
        completed = subprocess.run(command, cwd=workspace, capture_output=True,
                                   text=True, encoding="utf-8", timeout=timeout)
        result = json.loads(completed.stdout)
        if completed.returncode not in (0, 1, 2) or not isinstance(result, dict):
            raise ValueError("Unexpected test runner result")
        return result
    except subprocess.TimeoutExpired:
        return {"status": "execution_timeout", "total": 0, "passed": 0,
                "failed": 0, "errors": 1, "cases": [], "output": "",
                "error": f"Test process exceeded {timeout} seconds; cause requires inspection"}
    except (OSError, ValueError) as exc:
        message = f"Test runner error: {exc}"
    return {"status": "environment_error", "total": 0, "passed": 0,
            "failed": 0, "errors": 1, "cases": [], "output": "", "error": message}


class Tools:
    """Only expose known toy files. Tests cannot be edited by the policy."""

    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        self.editable = {"config.py", "client.py", "cli.py"}
        self.readable = self.editable | {"tests/test_visible.py"}
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
        if not old or before.count(old) != 1:
            raise ValueError("Patch requires exactly one matching old fragment")
        after = before.replace(old, new, 1)
        if after == before:
            raise ValueError("Patch does not change the file")
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
        accepted = (self.last_tests is not None and self.last_tests["status"] == "passed"
                    and self.last_tests.get("total", 0) > 0
                    and self.last_tests.get("passed") == self.last_tests["total"])
        return {"accepted": accepted, "reason": reason if accepted
                else "Completion rejected: the latest workspace has no passing visible test result."}

    def dispatch(self, tool, args):
        allowed = {"map_repository", "find_symbol", "read_file", "plan", "replace_text",
                   "run_tests", "finish"}
        if tool not in allowed:
            raise ValueError(f"Unknown tool: {tool}")
        return getattr(self, tool)(**args)
