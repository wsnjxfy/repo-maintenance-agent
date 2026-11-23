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


def finish(env, ctx, reason):
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("A nonempty stopping reason is required")
    return {"reason": reason}


HANDLERS = {
    "list_files": list_files,
    "read_lines": read_lines,
    "search_text": search_text,
    "apply_patch": apply_patch,
    "replace_text": replace_text,
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
