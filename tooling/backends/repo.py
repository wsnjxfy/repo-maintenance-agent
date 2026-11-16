"""Audited workspace tools for a development trial, not an OS sandbox.

The parent prepares a run; only ``activate_run`` starts its solving clock.
No model is invoked by this module. Python 3.9+ and Git are required.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET


IGNORED_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "venv"}
CONFIG_NAMES = {"conftest.py", "pytest.ini", "setup.cfg", "tox.ini", "pyproject.toml", "setup.py"}
RESERVED_NAMES = {"con", "prn", "aux", "nul"} | {"com" + str(n) for n in range(1, 10)} | {"lpt" + str(n) for n in range(1, 10)}


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


def _is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _safe_path(root, name, allow_dot=False):
    if not isinstance(name, str) or not name or any(ord(c) < 32 for c in name):
        raise ValueError("Expected a nonempty relative path")
    name = name.replace("\\", "/")
    win = PureWindowsPath(name)
    if win.drive or win.root or Path(name).is_absolute() or ":" in name:
        raise ValueError("Absolute paths and drive paths are not allowed")
    parts = name.split("/")
    if any(p == ".." for p in parts) or any(p.casefold() in IGNORED_DIRS for p in parts):
        raise ValueError("Path is outside the exposed workspace")
    if name == "." and allow_dot:
        return root
    clean = [p for p in parts if p not in ("", ".")]
    if not clean:
        raise ValueError("A file or subdirectory path is required")
    if any(p.endswith((".", " ")) or p.split(".")[0].casefold() in RESERVED_NAMES for p in clean):
        raise ValueError("Windows path aliases and reserved device names are not exposed")
    target = root.joinpath(*clean)
    cursor = root
    for component in clean:
        cursor = cursor / component
        if cursor.exists() or cursor.is_symlink():
            if _is_link(cursor):
                raise ValueError("Links and reparse points are not exposed")
    resolved = target.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Path escapes the workspace")
    return resolved


def _files(root, initial=None):
    initial = initial or {}
    found = {}
    for directory, dirs, names in os.walk(str(root), followlinks=False):
        folder = Path(directory)
        for name in list(dirs):
            path = folder / name
            if _is_link(path):
                raise ValueError("Workspace contains a link or reparse point: " + str(path.relative_to(root)))
        dirs[:] = sorted(d for d in dirs if d.casefold() not in IGNORED_DIRS)
        for name in sorted(names):
            path = folder / name
            rel = path.relative_to(root).as_posix()
            if _is_link(path):
                raise ValueError("Workspace contains a link or reparse point: " + rel)
            if rel not in initial and (name.endswith((".pyc", ".pyo")) or name == ".coverage"):
                continue
            if path.is_file():
                found[rel] = _sha(path.read_bytes())
    return found


def _digest(manifest):
    return _sha(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _display(raw, limit):
    text = raw.decode("utf-8", errors="replace")
    if len(text) <= limit:
        return text, False
    half = limit // 2
    return text[:half] + "\n[output truncated; full bytes retained in log]\n" + text[-half:], True


def _changes(before, after):
    return {p: {"before": before.get(p), "after": after.get(p)}
            for p in sorted(set(before) | set(after)) if before.get(p) != after.get(p)}


def _worker_env(workspace, python):
    names = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "TEMP", "TMP", "LANG", "LC_ALL", "NUMBER_OF_PROCESSORS"}
    env = {k: v for k, v in os.environ.items() if k.upper() in names}
    env.update({"PYTHONUTF8": "1", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(workspace), "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
    env["PATH"] = str(Path(python).parent) + os.pathsep + env.get("PATH", "")
    return env


def _process(command, cwd, env, stdout, stderr, timeout):
    started = time.monotonic()
    kwargs = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    timed_out = False
    cleanup = None
    with stdout.open("wb") as out, stderr.open("wb") as err:
        process = subprocess.Popen(command, cwd=str(cwd), env=env, stdout=out, stderr=err, stdin=subprocess.DEVNULL, **kwargs)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "nt":
                taskkill = str(Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "taskkill.exe")
                killed = subprocess.run([taskkill, "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                cleanup = {"method": "taskkill_tree", "returncode": killed.returncode,
                           "output": killed.stdout.decode("utf-8", errors="replace")}
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                cleanup = {"method": "kill_process_group"}
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
                cleanup["fallback"] = "kill_parent"
    return {"returncode": process.returncode, "timed_out": timed_out,
            "elapsed_seconds": round(time.monotonic() - started, 4), "cleanup": cleanup}


class ToolRunner:
    def __init__(self, run_dir):
        self.run_dir = Path(run_dir).resolve()
        self.controller = self.run_dir / "controller"
        self.config = json.loads((self.controller / "config.json").read_text(encoding="utf-8"))
        self.protocol = json.loads((self.controller / "protocol.json").read_text(encoding="utf-8"))
        self.workspace = Path(self.config["workspace"]).resolve()
        self.baseline = Path(self.config["baseline_dir"]).resolve()
        self.initial = self.config["initial_manifest"]
        self.outputs = self.controller / "outputs"
        self.outputs.mkdir(exist_ok=True)
        self._event_id = None

    def _protected(self, name):
        parts = name.split("/")
        leaf = parts[-1].lower()
        if any(p.lower() in {"test", "tests", "testing"} for p in parts[:-1]):
            return True
        if leaf.startswith("test_") or leaf.endswith("_test.py") or leaf == "test.py":
            return True
        if leaf in CONFIG_NAMES or leaf.startswith("requirements") and leaf.endswith(".txt"):
            return True
        for value in self.config.get("protected_paths", []):
            value = value.replace("\\", "/")
            if name.casefold() == value.casefold() or value.endswith("/") and name.casefold().startswith(value.casefold()):
                return True
        return False

    def _git(self, args, cwd=None, data=None):
        env = _worker_env(self.workspace, self.config["python"])
        env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"})
        command = [self.config["git"], "-c", "core.autocrlf=false", "-c", "core.filemode=false", "-c", "core.hooksPath=" + str(self.controller / "no-hooks")] + args
        return subprocess.run(command, cwd=str(cwd or self.workspace), env=env, input=data,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)

    def dispatch(self, action, args=None):
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
                before = _files(self.workspace, self.initial)
                if not isinstance(args, dict):
                    raise ValueError("Tool arguments must be a JSON object")
                allowed = {"list_files", "search_text", "read_lines", "apply_patch", "run_visible_tests", "finish"}
                if action not in allowed:
                    raise ValueError("Unknown workspace tool")
                phase = state.get("phase")
                if phase == "prepared" and action in {"apply_patch", "finish"}:
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
                result = getattr(self, "_" + action)(state=state, **args)
                response.update({"status": "ok", "result": result})
            except (ValueError, TypeError, OSError, RuntimeError, subprocess.SubprocessError, ET.ParseError) as exc:
                response.update({"status": "error", "error": type(exc).__name__ + ": " + str(exc)})
            try:
                after = _files(self.workspace, self.initial)
            except (ValueError, OSError) as exc:
                after = {}
                response["audit_error"] = str(exc)
            response["phase"] = state.get("phase")
            output_file = self.outputs / ("%04d-result.json" % event_id)
            _write_json(output_file, response)
            event = {"event_id": event_id, "action": action, "args": args, "status": response["status"],
                     "phase": state.get("phase"), "started_at": started, "finished_at": _now(),
                     "elapsed_seconds": round(time.monotonic() - timer, 4),
                     "output_file": str(output_file), "output_sha256": _sha(output_file.read_bytes()),
                     "before_digest": _digest(before), "after_digest": _digest(after), "changes": _changes(before, after),
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

    def _apply_patch(self, state, patch):
        if not isinstance(patch, str) or not patch or len(patch.encode("utf-8")) > 2_000_000:
            raise ValueError("Expected a nonempty unified patch of at most 2MB")
        paths = set()
        for line in patch.splitlines():
            if line.startswith(("old mode ", "new mode ", "new file mode ", "deleted file mode ")):
                if line.split()[-1] not in {"100644", "100755"}:
                    raise ValueError("Only regular-file patches are supported")
            if line.startswith("diff --git "):
                tokens = line.split()
                if len(tokens) != 4:
                    raise ValueError("Quoted or whitespace patch paths are unsupported")
                paths.update(tokens[2:])
            elif line.startswith(("--- ", "+++ ")):
                token = line[4:].split("\t", 1)[0]
                if token != "/dev/null":
                    paths.add(token)
            elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
                raise ValueError("Rename/copy patches are unsupported; use deletion and addition")
        if not paths:
            raise ValueError("Patch contains no file headers")
        targets = {}
        for name in paths:
            if not name.startswith(("a/", "b/")):
                raise ValueError("Patch paths must use a/ and b/ prefixes")
            rel = name[2:]
            target = _safe_path(self.workspace, rel)
            if self._protected(rel):
                raise ValueError("Original tests and test configuration are read-only")
            if target.exists() and not target.is_file():
                raise ValueError("Patch target is not a regular file")
            targets[rel] = target.read_bytes() if target.exists() else None
        patch_file = self.outputs / ("%04d-request.patch" % self._event_id)
        patch_file.write_bytes(patch.encode("utf-8"))
        check = self._git(["apply", "--no-index", "--check", "--whitespace=nowarn", "-"], data=patch.encode("utf-8"))
        if check.returncode:
            raise ValueError("Patch check failed: " + check.stderr.decode("utf-8", errors="replace"))
        applied = self._git(["apply", "--no-index", "--whitespace=nowarn", "-"], data=patch.encode("utf-8"))
        if applied.returncode:
            for rel, content in targets.items():
                target = self.workspace / rel
                if content is None:
                    if target.exists():
                        target.unlink()
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
            raise ValueError("Patch application failed and targets were restored: " + applied.stderr.decode("utf-8", errors="replace"))
        state["last_visible_tests"] = None
        return {"changed_paths": sorted(targets), "patch": str(patch_file), "patch_sha256": _sha(patch.encode("utf-8"))}

    def _run_visible_tests(self, state, paths, timeout_seconds=None):
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
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
            imported = probe.stdout.decode("utf-8", errors="replace").strip()
            if probe.returncode or not imported or not Path(imported).resolve().is_relative_to(self.workspace):
                raise ValueError("Sphinx import probe does not point to the active workspace: " + imported)
        prefix = self.outputs / ("%04d-pytest" % self._event_id)
        stdout = prefix.with_suffix(".stdout.log")
        stderr = prefix.with_suffix(".stderr.log")
        junit = prefix.with_suffix(".xml")
        command = [self.config["python"], "-m", "pytest", "-rA", "-vv", "--color=no", "--tb=short", "--junitxml=" + str(junit)] + paths
        before_digest = _digest(_files(self.workspace, self.initial))
        result = _process(command, self.workspace, env, stdout, stderr, timeout_seconds)
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
    parser.add_argument("action", help="list_files/search_text/read_lines/apply_patch/run_visible_tests/finish")
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
