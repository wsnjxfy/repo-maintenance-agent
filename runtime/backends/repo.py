"""Audited workspace tools for a development trial, not an OS sandbox.

The parent prepares a run; only ``activate_run`` starts its solving clock.
No model is invoked by this module. Python 3.9+ and Git are required.
"""
import argparse
import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET

from contracts.patch import parse_patch_paths
from contracts.protected import is_protected, protected_source
from runtime.fsguard import (IGNORED_DIRS, changes as _changes, digest as _digest,
                             display as _display, files as _files,
                             safe_path as _safe_path)
from runtime.process import (process as _process, time_left as _time_left,
                             worker_env as _worker_env)
from contracts.text_edit import replace_unique_text
from tools.toolsets import tool_names

ALLOWED_TOOLS = frozenset(tool_names("repo-v1"))
EDIT_TOOLS = ("apply_patch", "replace_text")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _write_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(str(temp), str(path))


@contextmanager
def _lock(controller):
    path = controller / "tool.lock"
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError("Another workspace operation is running; retry after it completes")
    try:
        os.write(fd, (str(os.getpid()) + "\n").encode("ascii"))
        yield
    finally:
        os.close(fd)
        path.unlink()



class ToolRunner:
    def __init__(self, run_dir):
        self.run_dir = Path(run_dir).resolve()
        self.controller = self.run_dir / "controller"
        self.config = json.loads((self.controller / "config.json").read_text(encoding="utf-8"))
        self.protocol = json.loads((self.controller / "protocol.json").read_text(encoding="utf-8"))
        self.edit_tool = self.protocol.get("edit_tool", "apply_patch")
        if self.edit_tool not in EDIT_TOOLS:
            raise ValueError("Unknown edit tool in the workspace protocol")
        self.workspace = Path(self.config["workspace"]).resolve()
        self.baseline = Path(self.config["baseline_dir"]).resolve()
        self.initial = self.config["initial_manifest"]
        self.outputs = self.controller / "outputs"
        self.outputs.mkdir(exist_ok=True)
        self._event_id = None

    def _protected(self, name):
        return is_protected(name, self.config.get("protected_paths", []))

    def _git(self, args, cwd=None, data=None, *, deadline=None):
        env = _worker_env(self.workspace, self.config["python"])
        env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"})
        command = [self.config["git"], "-c", "core.autocrlf=false", "-c", "core.filemode=false", "-c", "core.hooksPath=" + str(self.controller / "no-hooks")] + args
        return subprocess.run(command, cwd=str(cwd or self.workspace), env=env, input=data,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=min(30, _time_left(deadline)))

    def dispatch(self, action, args=None, *, deadline=None):
        """Use the controller's monotonic deadline for work, allowing finish afterward."""
        with _lock(self.controller):
            state_path = self.controller / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["event_count"] = state.get("event_count", 0) + 1
            event_id = state["event_count"]
            self._event_id = event_id
            started = _now()
            timer = time.monotonic()
            before = {}
            response = {"action": action, "event_id": event_id}
            try:
                if action != "finish" and deadline is not None and timer >= deadline:
                    before = None
                    raise TimeoutError("Trial wall-time limit reached")
                before = _files(self.workspace, self.initial)
                if action != "finish":
                    _time_left(deadline)
                if not isinstance(args, dict):
                    raise ValueError("Tool arguments must be a JSON object")

                allowed = set(ALLOWED_TOOLS)
                if self.edit_tool == "replace_text":
                    # Replacement mode is an explicit experimental variant; the
                    # toolset advertises apply_patch, so allow the configured
                    # alternative only when the protocol selects it.
                    allowed.add("replace_text")

                if action not in allowed:
                    raise ValueError("Unknown workspace tool")
                if action in EDIT_TOOLS and action != self.edit_tool:
                    raise ValueError("Workspace edit mode permits only " + self.edit_tool)
                phase = state.get("phase")
                if phase == "prepared" and action in {self.edit_tool, "finish"}:
                    raise ValueError("Run is prepared, not active; parent activation is required")
                if phase not in {"prepared", "active"}:
                    raise ValueError("Run is not available for further operations: " + str(phase))
                if phase == "active":
                    if state.get("started_at"):
                        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(state["started_at"])).total_seconds()
                        if elapsed > self.protocol["wall_time_seconds"] and action != "finish":
                            raise ValueError("Trial wall-time limit reached")
                    if state["action_count"] >= self.protocol["max_actions"] and action != "finish":
                        raise ValueError("Workspace action limit reached")
                    # Finish is always permitted so the final state can be preserved at the limit.
                    if action != "finish":
                        state["action_count"] += 1
                if action != "finish" and any(self._protected(p) for p in _changes(self.initial, before)):
                    raise ValueError("Protected tests or configuration changed outside the tools")
                tool = getattr(self, "_" + action)
                if action in {self.edit_tool, "run_visible_tests"}:
                    result = tool(state=state, deadline=deadline, **args)
                else:
                    result = tool(state=state, **args)
                response.update({"status": "ok", "result": result})
            except (ValueError, TypeError, OSError, RuntimeError, SyntaxError, subprocess.SubprocessError, ET.ParseError) as exc:
                response.update({"status": "error", "error": type(exc).__name__ + ": " + str(exc)})
            after = {}
            if before is not None:
                try:
                    after = _files(self.workspace, self.initial)
                except (ValueError, OSError) as exc:
                    response["audit_error"] = str(exc)
            response["phase"] = state.get("phase")
            output_file = self.outputs / ("%04d-result.json" % event_id)
            _write_json(output_file, response)
            event = {"event_id": event_id, "action": action, "args": args, "status": response["status"],
                     "phase": state.get("phase"), "started_at": started, "finished_at": _now(),
                     "elapsed_seconds": round(time.monotonic() - timer, 4),
                     "output_file": str(output_file), "output_sha256": _sha(output_file.read_bytes()),
                     "before_digest": _digest(before) if before is not None else None,
                     "after_digest": _digest(after) if before is not None else None,
                     "changes": _changes(before, after) if before is not None else {},
                     "action_count": state.get("action_count", 0)}
            with (self.controller / "actions.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps(event, ensure_ascii=False) + "\n")
            _write_json(state_path, state)
            return response

    def _list_files(self, state, path=".", limit=200):
        target = _safe_path(self.workspace, path, allow_dot=True)
        if not target.is_dir() or not isinstance(limit, int) or not 1 <= limit <= 5000:
            raise ValueError("Expected a directory and a limit between 1 and 5000")
        files = [p for p in _files(self.workspace, self.initial) if (self.workspace / p).is_relative_to(target)]
        return {"files": sorted(files)[:limit], "total": len(files), "truncated": len(files) > limit}

    def _read_lines(self, state, path, start=1, end=None):
        target = _safe_path(self.workspace, path)
        lines = target.read_text(encoding="utf-8").splitlines()
        end = min(len(lines), start + 199) if end is None else end
        if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start or end - start > 2000:
            if lines or start != 1 or end != 0:
                raise ValueError("Expected a positive line range containing at most 2001 lines")
        return {"path": target.relative_to(self.workspace).as_posix(), "lines": [
            {"line": n, "text": lines[n - 1]} for n in range(start, min(end, len(lines)) + 1)],
                "total_lines": len(lines), "truncated": start > 1 or end < len(lines)}

    def _search_text(self, state, query, path=".", limit=100, case_sensitive=True):
        if not isinstance(query, str) or not query or not isinstance(limit, int) or not 1 <= limit <= 2000:
            raise ValueError("Expected a literal query and limit between 1 and 2000")
        target = _safe_path(self.workspace, path, allow_dot=True)
        if not target.exists():
            raise ValueError("Search target does not exist")
        candidates = [target.relative_to(self.workspace).as_posix()] if target.is_file() else [
            p for p in _files(self.workspace, self.initial) if (self.workspace / p).is_relative_to(target)]
        matches = []
        binary = []
        needle = query if case_sensitive else query.casefold()
        for name in sorted(candidates):
            try:
                content = (self.workspace / name).read_text(encoding="utf-8")
            except UnicodeError:
                binary.append(name)
                continue
            for number, line in enumerate(content.splitlines(), 1):
                if needle in (line if case_sensitive else line.casefold()):
                    matches.append({"path": name, "line": number, "text": line})
        # Preserve full search results separately; only the bounded slice is displayed.
        full_file = self.outputs / ("%04d-search-full.json" % self._event_id)
        _write_json(full_file, {"matches": matches, "binary_files_skipped": binary})
        return {"matches": matches[:limit], "total": len(matches), "truncated": len(matches) > limit,
                "full_output": str(full_file), "full_output_sha256": _sha(full_file.read_bytes()), "binary_files_skipped": len(binary)}

    def _apply_patch(self, state, patch, *, deadline=None):
        if not isinstance(patch, str) or not patch or len(patch.encode("utf-8")) > 2_000_000:
            raise ValueError("Expected a nonempty unified patch of at most 2MB")
        targets = {}
        for rel in parse_patch_paths(patch):
            target = _safe_path(self.workspace, rel)
            if self._protected(rel):
                raise ValueError("Original tests and test configuration are read-only")
            if target.exists() and not target.is_file():
                raise ValueError("Patch target is not a regular file")
            targets[rel] = target.read_bytes() if target.exists() else None
        patch_file = self.outputs / ("%04d-request.patch" % self._event_id)
        patch_file.write_bytes(patch.encode("utf-8"))
        patch_bytes = patch.encode("utf-8")
        check = self._git(["apply", "--no-index", "--check", "--whitespace=nowarn", "-"],
                          data=patch_bytes, deadline=deadline)
        if check.returncode:
            raise ValueError("Patch check failed: " + check.stderr.decode("utf-8", errors="replace"))
        try:
            applied = self._git(["apply", "--no-index", "--whitespace=nowarn", "-"],
                                data=patch_bytes, deadline=deadline)
            error = applied.stderr.decode("utf-8", errors="replace") if applied.returncode else None
        except subprocess.TimeoutExpired:
            error = "Git apply exceeded its subprocess timeout"
        if error is not None:
            for rel, content in targets.items():
                target = self.workspace / rel
                if content is None:
                    if target.exists():
                        target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
            raise ValueError("Patch application failed and targets were restored: " + error)
        state["last_visible_tests"] = None
        return {"changed_paths": sorted(targets), "patch": str(patch_file), "patch_sha256": _sha(patch.encode("utf-8"))}

    def _replace_text(self, state, path, old, new, *, deadline=None):
        target = _safe_path(self.workspace, path)
        relative = target.relative_to(self.workspace).as_posix()
        if self._protected(relative) or protected_source(relative) or self._protected(path.replace("\\", "/")):
            raise ValueError("Original tests and test configuration are read-only")
        if not target.is_file():
            raise ValueError("Text replacement requires an existing regular file")
        if not isinstance(old, str) or not isinstance(new, str):
            raise ValueError("Replacement fragments must be strings")
        before = target.read_bytes().decode("utf-8")
        # Line reads omit terminators. Accept LF fragments in files using CRLF.
        without_crlf = before.replace("\r\n", "")
        if without_crlf != before and "\r" not in without_crlf and "\n" not in without_crlf:
            old = old.replace("\r\n", "\n").replace("\n", "\r\n")
            new = new.replace("\r\n", "\n").replace("\n", "\r\n")
        updated = replace_unique_text(before, old, new).encode("utf-8")
        if target.suffix.lower() == ".py":
            ast.parse(updated, filename=relative)
        with tempfile.TemporaryDirectory(prefix=".replace-", dir=str(target.parent)) as temporary:
            replacement = Path(temporary) / "replacement"
            replacement.write_bytes(updated)
            shutil.copymode(target, replacement)
            _time_left(deadline)
            os.replace(replacement, target)
            state["last_visible_tests"] = None
        return {"changed_paths": [relative]}

    def _run_visible_tests(self, state, paths, timeout_seconds=None, *, deadline=None):
        if not isinstance(paths, list) or not paths or len(paths) > 30 or not all(isinstance(p, str) for p in paths):
            raise ValueError("Provide a nonempty list of at most 30 relative pytest targets")
        for node in paths:
            path = node.split("::", 1)[0]
            target = _safe_path(self.workspace, path, allow_dot=True)
            if not target.exists() or path.startswith("-"):
                raise ValueError("Pytest target does not exist")
        maximum = self.protocol["test_timeout_seconds"]
        timeout_seconds = maximum if timeout_seconds is None else timeout_seconds
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= maximum:
            raise ValueError("Test timeout exceeds the protocol or is not positive")
        env = _worker_env(self.workspace, self.config["python"])
        state["last_visible_tests"] = None
        if self.config.get("repo") == "sphinx-doc/sphinx":
            probe = subprocess.run([self.config["python"], "-c", "import sphinx; print(sphinx.__file__)"], cwd=str(self.workspace),
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   timeout=min(20, _time_left(deadline)))
            imported = probe.stdout.decode("utf-8", errors="replace").strip()
            if probe.returncode or not imported or not Path(imported).resolve().is_relative_to(self.workspace):
                raise ValueError("Sphinx import probe does not point to the active workspace: " + imported)
        prefix = self.outputs / ("%04d-pytest" % self._event_id)
        stdout = prefix.with_suffix(".stdout.log")
        stderr = prefix.with_suffix(".stderr.log")
        junit = prefix.with_suffix(".xml")
        command = [self.config["python"], "-m", "pytest", "-rA", "-vv", "--color=no", "--tb=short", "--junitxml=" + str(junit)] + paths
        before_digest = _digest(_files(self.workspace, self.initial))
        result = _process(command, self.workspace, env, stdout, stderr,
                          min(timeout_seconds, _time_left(deadline)))
        stdout_bytes = stdout.read_bytes()
        collection_matches = re.findall(r"\bcollected\s+(\d+)\s+items?\b", stdout_bytes.decode("utf-8", errors="replace"))
        counts = {"collected": int(collection_matches[-1]) if collection_matches else None,
                  "junit_case_records": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0}
        if junit.exists():
            xml = ET.parse(str(junit))
            cases = list(xml.iter("testcase"))
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
        output, output_truncated = _display(stdout_bytes, 24000)
        error_output, error_truncated = _display(stderr.read_bytes(), 8000)
        result.update({"status": status, "command": command, "cwd": str(self.workspace), "log": str(stdout),
                       "stderr": str(stderr), "junit": str(junit) if junit.exists() else None,
                       "output": output, "output_truncated": output_truncated,
                       "stderr_output": error_output, "stderr_truncated": error_truncated,
                       "stdout_sha256": _sha(stdout.read_bytes()), "stderr_sha256": _sha(stderr.read_bytes()),
                       "junit_sha256": _sha(junit.read_bytes()) if junit.exists() else None,
                       "collected_count_source": "pytest_verbose_stdout" if collection_matches else "unavailable",
                       "workspace_digest": before_digest, "independent_evaluation": {"status": "not_run"}})
        state["last_visible_tests"] = result
        return result

    def _finish(self, state, reason):
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("A nonempty stopping reason is required")
        current = _files(self.workspace, self.initial)
        changed = _changes(self.initial, current)
        violations = sorted(p for p in changed if self._protected(p))
        export = self.controller / "submission"
        if export.exists():
            raise ValueError("A final submission already exists")
        pending = Path(tempfile.mkdtemp(prefix="submission-pending-", dir=str(self.controller)))
        try:
            patch_file = pending / "final.patch"
            self._export_patch(current, patch_file)
            visible = state.get("last_visible_tests")
            if visible and visible.get("workspace_digest") != _digest(current):
                visible = dict(visible, stale=True, status="stale")
            result = {"submission_saved": True, "reason": reason, "patch": str(export / "final.patch"),
                      "patch_sha256": _sha(patch_file.read_bytes()), "changes": changed,
                      "new_files": sorted(p for p in current if p not in self.initial), "protected_violations": violations,
                      "protocol_status": "violation" if violations else "no_violation_observed",
                      "workspace_digest": _digest(current), "visible_tests": visible,
                      "independent_evaluation": {"status": "not_run"}, "finished_at": _now()}
            _write_json(pending / "submission.json", result)
            # Publish only a fully formed submission, on the same filesystem.
            os.rename(str(pending), str(export))
        except Exception as exc:
            try:
                _write_json(pending / "diagnostic.json", {"status": "export_failed", "error": str(exc), "time": _now()})
            except OSError:
                pass
            raise RuntimeError("Submission export failed; diagnostics retained at " + str(pending) + ": " + str(exc)) from exc
        state["phase"] = "finished"
        state["finished_at"] = result["finished_at"]
        return result

    def _export_patch(self, current, patch_file):
        # A separate temporary index makes additions/deletions visible without touching the task's index.
        with tempfile.TemporaryDirectory(prefix="snapshot-", dir=str(self.controller)) as temp:
            stage = Path(temp)
            for name in self.initial:
                source = _safe_path(self.baseline, name)
                if not source.is_file() or _sha(source.read_bytes()) != self.initial[name]:
                    raise ValueError("Baseline integrity check failed: " + name)
                dest = stage / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(source), str(dest))
            for args in (["-c", "init.templateDir=", "init", "-q"], ["add", "--force", "--all"]):
                proc = self._git(args, cwd=stage)
                if proc.returncode:
                    raise RuntimeError(proc.stderr.decode("utf-8", errors="replace"))
            tree = self._git(["write-tree"], cwd=stage)
            if tree.returncode:
                raise RuntimeError(tree.stderr.decode("utf-8", errors="replace"))
            for name in self.initial:
                if name not in current:
                    (stage / name).unlink()
            for name in current:
                dest = stage / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(self.workspace / name), str(dest))
            # copy2 can preserve stat tuples for same-size edits on Windows. Rebuild the
            # index from bytes instead of trusting Git's cached size/mtime heuristic.
            cleared = self._git(["read-tree", "--empty"], cwd=stage)
            if cleared.returncode:
                raise RuntimeError("Failed to reset snapshot index")
            added = self._git(["add", "--force", "--all"], cwd=stage)
            patch = self._git(["diff", "--cached", "--binary", "--full-index", "--no-ext-diff", tree.stdout.decode("ascii").strip()], cwd=stage)
            if added.returncode or patch.returncode:
                raise RuntimeError("Failed to export complete workspace diff")
            patch_file.write_bytes(patch.stdout)


def activate_run(run_dir):
    """Parent-only lifecycle operation; no model is started here."""
    run_dir = Path(run_dir).resolve()
    controller = run_dir / "controller"
    with _lock(controller):
        path = controller / "state.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("phase") != "prepared":
            raise ValueError("Only a prepared run can be activated")
        config = json.loads((controller / "config.json").read_text(encoding="utf-8"))
        if _files(Path(config["workspace"]).resolve(), config["initial_manifest"]) != config["initial_manifest"]:
            raise ValueError("Workspace differs from the prepared baseline")
        state.update({"phase": "active", "started_at": _now(), "action_count": 0, "last_visible_tests": None})
        _write_json(path, state)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("action", help="list_files/search_text/read_lines/apply_patch/replace_text/run_visible_tests/finish")
    parser.add_argument("--args-json", default="{}")
    args = parser.parse_args()
    try:
        payload = json.loads(args.args_json)
    except ValueError:
        payload = {"invalid_json_payload": args.args_json}
    result = ToolRunner(args.run_dir).dispatch(args.action, payload)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
