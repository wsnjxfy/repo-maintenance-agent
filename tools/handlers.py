"""Tool handlers: semantics only; every filesystem/process effect goes via env.

Handlers are registered by name (``HANDLERS``) and referenced from
``tools/specs.py`` by the ``handler`` field. They never import ``runtime``.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from contracts.patch import parse_patch_paths
from contracts.text_edit import replace_unique_text


MAX_PATCH_BYTES = 2_000_000
FATAL_STATUSES = {"environment_error", "test_load_error", "execution_timeout"}


def _require_case_sensitive(value):
    if not isinstance(value, bool):
        raise ValueError("case_sensitive must be a bool")


@dataclass
class HandlerContext:
    specs: dict
    allowed: set
    outputs: Path
    python: str
    test_timeout: float
    event_id: int = 0


def list_files(env, ctx, path=".", limit=200):
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 2000:
        raise ValueError("limit must be between 1 and 2000")
    names = env.list_files(path)
    return {"files": sorted(names)[:limit], "total": len(names), "truncated": len(names) > limit}


def read_lines(env, ctx, path, start=1, end=None):
    if not isinstance(start, int) or isinstance(start, bool) or start < 1:
        raise ValueError("start must be a positive integer")
    if end is not None and (not isinstance(end, int) or isinstance(end, bool) or end < start):
        raise ValueError("end must be >= start")
    lines = env.read_text(path).splitlines()
    end = len(lines) if end is None else min(end, len(lines))
    return {"path": path, "start": start, "end": end,
            "lines": [{"line": number, "text": lines[number - 1]}
                      for number in range(start, end + 1)],
            "total_lines": len(lines), "truncated": start > 1 or end < len(lines)}


def search_text(env, ctx, query, path=".", limit=100, case_sensitive=True):
    if not isinstance(query, str) or not query:
        raise ValueError("Expected a literal query")
    _require_case_sensitive(case_sensitive)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 2000:
        raise ValueError("limit must be between 1 and 2000")
    candidates = env.list_files(path)
    needle = query if case_sensitive else query.casefold()
    matches = []
    binary = []
    for name in sorted(candidates):
        try:
            content = env.read_text(name)
        except UnicodeError:
            binary.append(name)
            continue
        for number, line in enumerate(content.splitlines(), 1):
            if needle in (line if case_sensitive else line.casefold()):
                matches.append({"path": name, "line": number, "text": line})
    return {"matches": matches[:limit], "total": len(matches), "truncated": len(matches) > limit,
            "binary_files_skipped": len(binary)}


def apply_patch(env, ctx, patch):
    if not isinstance(patch, str) or not patch:
        raise ValueError("Expected a nonempty unified patch")
    body = patch.encode("utf-8")
    if len(body) > MAX_PATCH_BYTES:
        raise ValueError("Patch exceeds the 2MB limit")
    relative = parse_patch_paths(patch)
    snapshots = {}
    for name in relative:
        env.require_editable(name)
        if env.exists(name) and not env.is_file(name):
            raise ValueError("Patch target is not a regular file")
        snapshots[name] = env.read_bytes(name) if env.exists(name) else None

    def restore():
        for name, content in snapshots.items():
            if content is None:
                if env.exists(name):
                    env.delete(name)
            else:
                env.write_bytes(name, content)

    git = getattr(env, "git", "git")
    check = env.run([git, "apply", "--no-index", "--check", "--whitespace=nowarn", "-"],
                    stdin=body, timeout=30)
    if check["returncode"]:
        raise ValueError("Patch check failed: " + check["stderr"])
    applied = env.run([git, "apply", "--no-index", "--whitespace=nowarn", "-"],
                      stdin=body, timeout=30)
    if applied["returncode"]:
        restore()
        raise ValueError("Patch application failed and targets were restored: " + applied["stderr"])
    return {"changed_paths": sorted(relative), "patch_sha256": _sha(body)}


def replace_text(env, ctx, path, old, new):
    env.require_editable(path)
    if not env.exists(path) or not env.is_file(path):
        raise ValueError("Text replacement requires an existing regular file")
    if not isinstance(old, str) or not isinstance(new, str):
        raise ValueError("Replacement fragments must be strings")
    before = env.read_text(path)
    if "\r\n" in before and "\n" not in before.replace("\r\n", ""):
        # Windows checkouts may use CRLF; accept LF fragments from the model.
        old = old.replace("\r\n", "\n").replace("\n", "\r\n")
        new = new.replace("\r\n", "\n").replace("\n", "\r\n")
    after = replace_unique_text(before, old, new)
    if path.lower().endswith(".py"):
        ast.parse(after, filename=path)
    env.write_text(path, after)
    return {"changed_paths": [path]}


def run_visible_tests(env, ctx, paths, timeout_seconds=None):
    if not isinstance(paths, list) or not paths or len(paths) > 30 or not all(isinstance(p, str) for p in paths):
        raise ValueError("Provide a nonempty list of at most 30 relative pytest targets")
    for node in paths:
        target = node.split("::", 1)[0]
        if not env.exists(target):
            raise ValueError("Pytest target does not exist")
    maximum = ctx.test_timeout
    timeout = maximum if timeout_seconds is None else timeout_seconds
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= maximum:
        raise ValueError("Test timeout exceeds the protocol or is not positive")
    prefix = ctx.outputs / ("%04d-pytest" % ctx.event_id)
    out_log = prefix.with_suffix(".stdout.log")
    err_log = prefix.with_suffix(".stderr.log")
    junit = prefix.with_suffix(".xml")
    command = [ctx.python, "-m", "pytest", "-rA", "-vv", "--color=no", "--tb=short",
               "--junitxml=" + str(junit)] + paths
    result = env.run(command, timeout=timeout, logs=(out_log, err_log))
    stdout = result["stdout"]
    matches = re.findall(r"\bcollected\s+(\d+)\s+items?\b", stdout)
    counts = {"collected": int(matches[-1]) if matches else None,
              "junit_case_records": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    if junit.exists():
        cases = list(ET.parse(str(junit)).iter("testcase"))
        counts["junit_case_records"] = len(cases)
        for case in cases:
            if case.find("error") is not None:
                counts["errors"] += 1
            elif case.find("failure") is not None:
                counts["failed"] += 1
            elif case.find("skipped") is not None:
                counts["skipped"] += 1
            else:
                counts["passed"] += 1
    if result["timed_out"]:
        status = "timeout"
    elif result["returncode"] == 5:
        status = "no_tests"
    elif result["returncode"] not in (0, 1) or counts["errors"] or not junit.exists():
        status = "error"
    elif counts["failed"]:
        status = "failed"
    elif counts["skipped"]:
        status = "skipped"
    elif counts["passed"] > 0 and result["returncode"] == 0:
        status = "passed"
    else:
        status = "error"
    result.update(counts)
    result.update({"status": status, "command": command, "junit": str(junit) if junit.exists() else None,
                   "log": str(out_log), "stderr_log": str(err_log)})
    return result


def search_regex(env, ctx, pattern, path=".", limit=100, case_sensitive=True):
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("Expected a nonempty regular expression")
    _require_case_sensitive(case_sensitive)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 2000:
        raise ValueError("limit must be between 1 and 2000")
    try:
        regex = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
    except re.error as exc:
        raise ValueError("Invalid regular expression: %s" % exc)
    matches = []
    binary = []
    for name in sorted(env.list_files(path)):
        try:
            content = env.read_text(name)
        except UnicodeError:
            binary.append(name)
            continue
        for number, line in enumerate(content.splitlines(), 1):
            if regex.search(line):
                matches.append({"path": name, "line": number, "text": line})
    return {"matches": matches[:limit], "total": len(matches), "truncated": len(matches) > limit,
            "binary_files_skipped": len(binary)}


def map_repository(env, ctx, path=".", limit=200):
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 2000:
        raise ValueError("limit must be between 1 and 2000")
    py_files = [name for name in sorted(env.list_files(path)) if name.endswith(".py")]
    truncated = len(py_files) > limit
    outline = {}
    symbol_count = 0
    for name in py_files[:limit]:
        try:
            source = env.read_text(name)
        except (UnicodeError, OSError):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            outline[name] = {"error": "SyntaxError: %s" % exc.msg}
            continue
        symbols = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append({"kind": "function", "name": node.name, "line": node.lineno})
            elif isinstance(node, ast.ClassDef):
                methods = [{"name": child.name, "line": child.lineno}
                           for child in node.body
                           if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))]
                symbols.append({"kind": "class", "name": node.name,
                                "line": node.lineno, "methods": methods})
        symbol_count += len(symbols)
        if symbols:
            outline[name] = symbols
    return {"files": outline, "file_count": len(outline),
            "python_files": len(py_files), "symbol_count": symbol_count,
            "truncated": truncated}


def find_symbol(env, ctx, symbol, path=".", limit=100):
    if not isinstance(symbol, str) or not symbol.isidentifier():
        raise ValueError("symbol must be a valid identifier")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 2000:
        raise ValueError("limit must be between 1 and 2000")
    word = re.compile(r"\b" + re.escape(symbol) + r"\b")
    definitions = []
    references = []
    binary = 0
    for name in sorted(env.list_files(path)):
        try:
            source = env.read_text(name)
        except UnicodeError:
            binary += 1
            continue
        except OSError:
            continue
        if name.endswith(".py"):
            try:
                tree = ast.parse(source)
            except SyntaxError:
                tree = None
            if tree is not None:
                for node in ast.walk(tree):
                    if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                            and node.name == symbol):
                        kind = "class" if isinstance(node, ast.ClassDef) else "function"
                        definitions.append({"path": name, "line": node.lineno, "kind": kind})
        for number, line in enumerate(source.splitlines(), 1):
            if word.search(line):
                references.append({"path": name, "line": number, "text": line})
    return {"definitions": definitions, "references": references[:limit],
            "definition_count": len(definitions), "reference_count": len(references),
            "truncated": len(references) > limit, "binary_files_skipped": binary}


def create_file(env, ctx, path, content=""):
    env.require_editable(path)
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    if env.exists(path):
        raise ValueError("File already exists; use edit_lines or replace_text")
    if path.lower().endswith(".py"):
        ast.parse(content, filename=path)
    env.write_text(path, content)
    return {"changed_paths": [path]}


def edit_lines(env, ctx, path, start, end, new):
    env.require_editable(path)
    if not env.exists(path) or not env.is_file(path):
        raise ValueError("edit_lines requires an existing regular file")
    if not isinstance(start, int) or isinstance(start, bool) or start < 1:
        raise ValueError("start must be a positive integer")
    if not isinstance(end, int) or isinstance(end, bool) or end < start:
        raise ValueError("end must be >= start")
    if not isinstance(new, str):
        raise ValueError("new must be a string")
    before = env.read_text(path)
    lines = before.splitlines()
    if end > len(lines):
        raise ValueError("end exceeds the file length (%d lines)" % len(lines))
    newline = "\r\n" if "\r\n" in before else "\n"
    replacement = new.splitlines() if new != "" else []
    merged = lines[:start - 1] + replacement + lines[end:]
    after = newline.join(merged)
    # Preserve the file's trailing-newline convention. When the edit reaches
    # the last line, also honor a trailing newline explicitly supplied in
    # ``new`` (splitlines() would otherwise discard it).
    keep_trailing = bool(merged) and (
        before.endswith(("\n", "\r"))
        or (new != "" and end == len(lines) and new.endswith(("\n", "\r")))
    )
    if keep_trailing:
        after += newline
    if path.lower().endswith(".py"):
        ast.parse(after, filename=path)
    env.write_text(path, after)
    return {"changed_paths": [path]}


def revert_file(env, ctx, path):
    env.require_editable(path)
    git = getattr(env, "git", "git")
    tracked = env.run([git, "cat-file", "-e", "HEAD:" + path], timeout=30)
    if tracked["returncode"] == 0:
        result = env.run([git, "checkout", "HEAD", "--", path], timeout=30)
        if result["returncode"]:
            raise ValueError("revert failed: " + result["stderr"])
        action = "restored"
    elif env.exists(path) and env.is_file(path):
        env.delete(path)
        action = "deleted"
    else:
        raise ValueError("Cannot revert: path is not in the baseline and does not exist")
    return {"changed_paths": [path], "action": action}


def show_diff(env, ctx, path=None):
    """Show the workspace diff against HEAD.

    ``git add -A`` is intentional here: it lets the model see new/untracked
    files in the returned diff. It also stages changes in the real git index
    (a side effect documented in the tool spec).
    """
    git = getattr(env, "git", "git")
    if path is not None:
        if not isinstance(path, str) or not path:
            raise ValueError("path must be a nonempty string when provided")
        env.require_readable(path)
    staged = env.run([git, "add", "-A"], timeout=30)
    if staged["returncode"]:
        raise ValueError("git add failed: " + staged["stderr"])
    command = [git, "diff", "--cached", "HEAD"]
    if path is not None:
        command += ["--", path]
    result = env.run(command, timeout=30)
    if result["returncode"] not in (0, 1):
        raise ValueError("git diff failed: " + result["stderr"])
    diff = result["stdout"]
    names_command = [git, "diff", "--cached", "--name-only", "-z", "HEAD"]
    if path is not None:
        names_command += ["--", path]
    names = env.run(names_command, timeout=30)
    if names["returncode"] not in (0, 1):
        raise ValueError("git diff --name-only failed: " + names["stderr"])
    files = sorted(name for name in names["stdout"].split("\0") if name)
    truncated = False
    if len(diff) > 20000:
        diff = diff[:20000] + "\n…（diff 已截断）"
        truncated = True
    return {"diff": diff, "changed_files": files, "truncated": truncated}


def finish(env, ctx, reason):
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("A nonempty stopping reason is required")
    return {"reason": reason}


HANDLERS = {
    "list_files": list_files,
    "read_lines": read_lines,
    "search_text": search_text,
    "search_regex": search_regex,
    "map_repository": map_repository,
    "find_symbol": find_symbol,
    "apply_patch": apply_patch,
    "replace_text": replace_text,
    "create_file": create_file,
    "edit_lines": edit_lines,
    "revert_file": revert_file,
    "show_diff": show_diff,
    "run_visible_tests": run_visible_tests,
    "finish": finish,
}


def dispatch(name, env, ctx, args):
    spec = ctx.specs.get(name)
    if spec is None or name not in ctx.allowed:
        return {"ok": False, "tool": name, "error": "Unknown workspace tool", "kind": "tool"}
    try:
        result = HANDLERS[spec["handler"]](env, ctx, **args)
    except (ValueError, TypeError, OSError, SyntaxError) as exc:
        return {"ok": False, "tool": name, "error": f"{type(exc).__name__}: {exc}", "kind": "tool"}
    observation = {"ok": True, "tool": name, "result": result}
    if isinstance(result, dict) and "changed_paths" in result:
        observation["changed_paths"] = result.pop("changed_paths")
    if isinstance(result, dict) and result.get("status") in FATAL_STATUSES:
        observation["fatal"] = True
    return observation


def _sha(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()
