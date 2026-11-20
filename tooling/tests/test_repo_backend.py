"""Contract checks on disposable, answer-free repositories.

These tests never load Sphinx task data, reference patches, or acceptance tests.
The interpreter running this suite must already provide pytest for subprocesses.
"""

import difflib
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from tooling.backends.repo import ToolRunner, activate_run


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def patch_for(path, old, new):
    """Produce a real git-applicable diff, including new-file headers."""
    mode = "new file mode 100644\n" if not old else "deleted file mode 100644\n" if not new else ""
    return "diff --git a/{0} b/{0}\n".format(path) + mode + "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="a/" + path if old else "/dev/null",
            tofile="b/" + path if new else "/dev/null",
        )
    )


def process_alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        stat = Path("/proc") / str(pid) / "stat"
        return not stat.exists() or stat.read_text().split()[2] != "Z"
    except ProcessLookupError:
        return False


class RunFixture:
    def __init__(self, root, git, test_source=None, max_actions=30, edit_tool=None,
                 module_source="VALUE = 1\n"):
        self.root = root
        self.workspace = root / "workspace"
        self.controller = root / "controller"
        self.baseline = self.controller / "base"
        self.workspace.mkdir(parents=True)
        (self.controller / "outputs").mkdir(parents=True)
        (root / "input").mkdir()
        if test_source is None:
            test_source = "from module import VALUE\n\ndef test_value():\n    assert VALUE == 1\n"
        files = {
            "module.py": module_source,
            "helper.py": "VALUE = 2\n",
            "tests/test_visible.py": test_source,
            "pytest.ini": "[pytest]\n",
            "conftest.py": "# original test configuration\n",
        }
        for name, content in files.items():
            target = self.workspace / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
        shutil.copytree(self.workspace, self.baseline)
        manifest = {name: digest((self.workspace / name).read_bytes()) for name in files}
        config = {
            "schema_version": 1,
            "run_id": root.name,
            "task_id": "answer-free-fixture",
            "repo": "fixture/example",
            "base_commit": "fixture-base",
            "workspace": str(self.workspace.resolve()),
            "baseline_dir": str(self.baseline.resolve()),
            "python": sys.executable,
            "git": git,
            "initial_manifest": manifest,
            "protected_paths": ["tests/test_visible.py", "pytest.ini", "conftest.py"],
        }
        protocol = {"max_actions": max_actions, "wall_time_seconds": 1200, "test_timeout_seconds": 90}
        if edit_tool is not None:
            protocol["edit_tool"] = edit_tool
        state = {
            "phase": "prepared", "started_at": None, "action_count": 0,
            "event_count": 0, "last_visible_tests": None,
        }
        write_json(self.controller / "config.json", config)
        write_json(self.controller / "protocol.json", protocol)
        write_json(self.controller / "state.json", state)
        (self.controller / "actions.jsonl").write_text("", encoding="utf-8")
        self.runner = ToolRunner(root)

    @property
    def state(self):
        return json.loads((self.controller / "state.json").read_text(encoding="utf-8"))

    @property
    def events(self):
        return [json.loads(line) for line in (self.controller / "actions.jsonl").read_text(encoding="utf-8").splitlines()]

    def active(self):
        activate_run(self.root)
        return self

    def call(self, action, **args):
        return self.runner.dispatch(action, args)


class TestWorkspaceTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fallback = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/git/cmd/git.exe"
        cls.git = shutil.which("git") or (str(fallback) if fallback.is_file() else None)
        if not cls.git:
            raise unittest.SkipTest("git is required to verify real patch round trips")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hj10-tools-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture_count = 0

    def fixture(self, **kwargs):
        self.fixture_count += 1
        return RunFixture(self.root / ("run-" + str(self.fixture_count)), self.git, **kwargs)

    def assert_ok(self, response):
        self.assertEqual(response["status"], "ok", response)
        return response["result"]

    def test_preparation_reads_do_not_start_attempt_or_consume_budget(self):
        run = self.fixture()
        self.assert_ok(run.call("list_files"))
        result = self.assert_ok(run.call("search_text", query="VALUE", path="."))
        self.assertIn("module.py", json.dumps(result))
        self.assert_ok(run.call("read_lines", path="module.py", start=1, end=1))
        self.assertEqual(run.state["phase"], "prepared")
        self.assertIsNone(run.state["started_at"])
        self.assertEqual(run.state["action_count"], 0)
        self.assertEqual(len(run.events), 3)

    def test_prepared_state_refuses_edit_and_finish_and_records_denials(self):
        run = self.fixture()
        before = (run.workspace / "module.py").read_bytes()
        edit = patch_for("module.py", "VALUE = 1\n", "VALUE = 9\n")
        for action, args in [("apply_patch", {"patch": edit}), ("finish", {"reason": "not started"})]:
            response = run.runner.dispatch(action, args)
            self.assertEqual(response["status"], "error", response)
        self.assertEqual((run.workspace / "module.py").read_bytes(), before)
        self.assertEqual(len(run.events), 2)
        self.assertTrue(all(event["status"] == "error" for event in run.events))
        self.assertIsNone(run.state["started_at"])

    def test_absolute_and_parent_paths_are_rejected_without_reading_sentinel(self):
        run = self.fixture()
        sentinel = run.root / "outside.txt"
        sentinel.write_text("OUTSIDE_SENTINEL", encoding="utf-8")
        for name in ["../outside.txt", str(sentinel.resolve()), "C:\\outside.txt"]:
            with self.subTest(path=name):
                response = run.call("read_lines", path=name)
                self.assertEqual(response["status"], "error", response)
                self.assertNotIn("OUTSIDE_SENTINEL", json.dumps(response))
        self.assertEqual(len(run.events), 3)

    def test_symlink_escape_is_rejected(self):
        run = self.fixture()
        outside = run.root / "outside"
        outside.mkdir()
        sentinel = outside / "outside.py"
        sentinel.write_text("OUTSIDE_SENTINEL", encoding="utf-8")
        link = run.workspace / "linked"
        junction = False
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            if os.name != "nt":
                self.skipTest("host does not permit creating a symlink: " + str(exc))
            # Junctions exercise Windows reparse-point rejection without symlink privilege.
            created = subprocess.run(
                [os.environ.get("COMSPEC", "cmd.exe"), "/c", "mklink", "/J", str(link), str(outside)],
                capture_output=True, timeout=10,
            )
            if created.returncode:
                self.skipTest("host cannot create a symlink or junction")
            junction = True
        try:
            response = run.call("read_lines", path="linked/outside.py")
            self.assertEqual(response["status"], "error", response)
            self.assertNotIn("OUTSIDE_SENTINEL", json.dumps(response))
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "OUTSIDE_SENTINEL")
        finally:
            if junction:
                os.rmdir(link)  # Removes only this known temporary junction, never its target.

    def test_git_metadata_cannot_be_read_through_case_alias(self):
        run = self.fixture()
        metadata = run.workspace / ".git"
        metadata.mkdir()
        (metadata / "config").write_text("HIDDEN_METADATA_SENTINEL", encoding="utf-8")
        for name in [".git/config", ".GIT/config"]:
            response = run.call("read_lines", path=name)
            self.assertEqual(response["status"], "error", response)
            self.assertNotIn("HIDDEN_METADATA_SENTINEL", json.dumps(response))

    def test_original_tests_configuration_and_new_tests_are_read_only(self):
        run = self.fixture().active()
        changes = [
            ("tests/test_visible.py", (run.workspace / "tests/test_visible.py").read_text(encoding="utf-8"), "def test_fake():\n    pass\n"),
            ("pytest.ini", "[pytest]\n", "[pytest]\naddopts = --ignore=tests\n"),
            ("conftest.py", "# original test configuration\n", "raise SystemExit(0)\n"),
            ("tests/test_new.py", "", "def test_new():\n    pass\n"),
            ("test_new.py", "", "def test_new():\n    pass\n"),
            ("nested/conftest.py", "", "# new configuration\n"),
        ]
        for path, old, new in changes:
            with self.subTest(path=path):
                response = run.call("apply_patch", patch=patch_for(path, old, new))
                self.assertEqual(response["status"], "error", response)
                target = run.workspace / path
                if old:
                    self.assertEqual(target.read_text(encoding="utf-8"), old)
                else:
                    self.assertFalse(target.exists())

    def test_invalid_multi_file_patch_never_partially_applies(self):
        run = self.fixture().active()
        patch = patch_for("module.py", "VALUE = 1\n", "VALUE = 3\n")
        patch += patch_for("helper.py", "VALUE = 999\n", "VALUE = 4\n")
        response = run.call("apply_patch", patch=patch)
        self.assertEqual(response["status"], "error", response)
        self.assertEqual((run.workspace / "module.py").read_text(encoding="utf-8"), "VALUE = 1\n")
        self.assertEqual((run.workspace / "helper.py").read_text(encoding="utf-8"), "VALUE = 2\n")
        self.assertEqual(run.events[-1]["before_digest"], run.events[-1]["after_digest"])

    def test_replacement_mode_edits_once_and_exports_a_round_trip_patch(self):
        run = self.fixture(edit_tool="replace_text", module_source="VALUE = 1\r\nOTHER = 2\r\n").active()
        args = {"path": "module.py", "old": "VALUE = 1\nOTHER = 2", "new": "VALUE = 3\nOTHER = 2"}
        self.assert_ok(run.call("replace_text", **args))
        event, = run.events
        self.assertEqual((event["action"], event["args"], event["action_count"]), ("replace_text", args, 1))
        self.assertEqual(set(event["changes"]), {"module.py"})
        self.assertEqual((run.workspace / "module.py").read_bytes(), b"VALUE = 3\r\nOTHER = 2\r\n")
        self.assertEqual((run.workspace / "helper.py").read_bytes(), b"VALUE = 2\n")

        frozen = self.assert_ok(run.call("finish", reason="replacement complete"))
        artifact = Path(frozen["patch"])
        self.assertEqual(frozen["patch_sha256"], digest(artifact.read_bytes()))
        clean = self.root / "clean-base"
        shutil.copytree(run.baseline, clean)
        subprocess.run([self.git, "-C", str(clean), "init", "-q"], check=True, capture_output=True)
        subprocess.run([self.git, "-C", str(clean), "-c", "core.autocrlf=false", "apply", str(artifact)],
                       check=True, capture_output=True)
        self.assertEqual((clean / "module.py").read_bytes(), (run.workspace / "module.py").read_bytes())
        self.assertEqual(run.state["action_count"], 1)

    def test_replacement_rejects_nonunique_matches_and_protected_or_outside_paths(self):
        original = "REPEAT\nREPEAT\nAAA\n"
        run = self.fixture(edit_tool="replace_text", module_source=original).active()
        cases = [
            ("module.py", "REPEAT", "updated"),
            ("module.py", "AA", "updated"),
            ("module.py", "MISSING", "updated"),
            ("module.py", "", "updated"),
            ("tests/test_visible.py", "VALUE", "updated"),
            ("pytest.ini", "[pytest]", "updated"),
            ("../controller/base/module.py", "REPEAT", "updated"),
            ("absent.py", "REPEAT", "updated"),
        ]
        for path, old, new in cases:
            with self.subTest(path=path, old=old):
                result = run.call("replace_text", path=path, old=old, new=new)
                self.assertEqual(result["status"], "error", result)
                self.assertEqual(run.events[-1]["changes"], {})
        self.assertEqual((run.workspace / "module.py").read_text(encoding="utf-8"), original)
        self.assertEqual((run.baseline / "module.py").read_text(encoding="utf-8"), original)
        self.assertFalse((run.workspace / "absent.py").exists())

    def test_edit_modes_reject_each_others_actions(self):
        default_run = self.fixture().active()
        replace_run = self.fixture(edit_tool="replace_text").active()
        replacement = default_run.call("replace_text", path="module.py", old="VALUE = 1", new="VALUE = 3")
        patch = replace_run.call("apply_patch", patch=patch_for("module.py", "VALUE = 1\n", "VALUE = 3\n"))
        for run, result in ((default_run, replacement), (replace_run, patch)):
            with self.subTest(action=result["action"]):
                self.assertEqual(result["status"], "error", result)
                self.assertEqual(run.state["action_count"], 0)
                self.assertEqual(run.events[-1]["changes"], {})
                self.assertEqual((run.workspace / "module.py").read_bytes(), b"VALUE = 1\n")

    def test_patch_addition_survives_finish_and_applies_to_clean_base(self):
        run = self.fixture().active()
        patch = patch_for("module.py", "VALUE = 1\n", "VALUE = 3\n")
        patch += patch_for("new_module.py", "", "NEW_VALUE = 10\n")
        self.assert_ok(run.call("apply_patch", patch=patch))
        result = self.assert_ok(run.call("finish", reason="fixture complete"))
        self.assertTrue(result["submission_saved"])
        self.assertEqual(result["independent_evaluation"]["status"], "not_run")
        self.assertNotIn("accepted", result)
        artifact = Path(result["patch"])
        self.assertEqual(digest(artifact.read_bytes()), result["patch_sha256"])
        self.assertIn("new_module.py", artifact.read_text(encoding="utf-8"))
        clean = self.root / "clean-base"
        shutil.copytree(run.baseline, clean)
        subprocess.run([self.git, "-C", str(clean), "init", "-q"], check=True, capture_output=True)
        apply = [self.git, "-C", str(clean), "-c", "core.autocrlf=false", "apply"]
        subprocess.run(apply + ["--check", str(artifact)], check=True, capture_output=True)
        subprocess.run(apply + [str(artifact)], check=True, capture_output=True)
        self.assertEqual((clean / "module.py").read_bytes(), (run.workspace / "module.py").read_bytes())
        self.assertEqual((clean / "new_module.py").read_bytes(), (run.workspace / "new_module.py").read_bytes())
        self.assertEqual(run.call("read_lines", path="module.py")["status"], "error")

    def test_failed_export_leaves_no_submission_and_can_be_retried(self):
        run = self.fixture().active()
        baseline_file = run.baseline / "module.py"
        original = baseline_file.read_bytes()
        baseline_file.write_bytes(b"VALUE = 999\n")
        first = run.call("finish", reason="intentional invalid baseline fixture")
        self.assertEqual(first["status"], "error", first)
        self.assertFalse((run.controller / "submission").exists())
        self.assertEqual(run.state["phase"], "active")
        self.assertEqual(len(run.events), 1)
        self.assertEqual(run.events[0]["status"], "error")
        baseline_file.write_bytes(original)
        second = self.assert_ok(run.call("finish", reason="retry after fixture baseline restored"))
        self.assertTrue(second["submission_saved"])
        self.assertTrue(Path(second["patch"]).is_file())
        self.assertEqual(second["independent_evaluation"]["status"], "not_run")
        self.assertEqual([event["status"] for event in run.events], ["error", "ok"])
        self.assertEqual(run.state["phase"], "finished")

    def test_log_records_actual_args_output_hash_and_unchanged_content(self):
        run = self.fixture()
        args = {"path": "module.py", "start": 1, "end": 1}
        response = run.runner.dispatch("read_lines", args)
        self.assert_ok(response)
        event = run.events[-1]
        self.assertEqual(event["args"], args)
        self.assertEqual(event["action"], "read_lines")
        self.assertEqual(event["event_id"], response["event_id"])
        self.assertEqual(event["status"], response["status"])
        output = Path(event["output_file"])
        if not output.is_absolute():
            output = run.root / output
        self.assertEqual(digest(output.read_bytes()), event["output_sha256"])
        self.assertIn("VALUE = 1", output.read_text(encoding="utf-8"))
        self.assertEqual(event["before_digest"], event["after_digest"])
        for key in ["started_at", "finished_at", "elapsed_seconds"]:
            self.assertIn(key, event)

    def test_search_missing_directory_reports_error_instead_of_empty_results(self):
        run = self.fixture()
        response = run.call("search_text", query="VALUE", path="missing-directory")
        self.assertEqual(response["status"], "error", response)
        self.assertEqual(run.events[-1]["status"], "error")

    def test_visible_pytest_distinguishes_pass_failure_skip_empty_and_error(self):
        cases = [
            ("def test_ok():\n    assert 1 == 1\n", "passed", "passed"),
            ("def test_bad():\n    assert 1 == 2\n", "failed", "failed"),
            ("import pytest\ndef test_skip():\n    pytest.skip('fixture')\n", "skipped", "skipped"),
            ("VALUE = 1\n", "no_tests", None),
            ("def broken(\n", "error", "errors"),
        ]
        for source, status, positive_count in cases:
            with self.subTest(expected=status):
                run = self.fixture(test_source=source)
                result = self.assert_ok(run.call("run_visible_tests", paths=["tests/test_visible.py"]))
                self.assertEqual(result["status"], status, result)
                self.assertTrue(Path(result["log"]).is_file())
                if positive_count:
                    self.assertGreater(result[positive_count], 0, result)
                if status == "passed":
                    self.assertEqual(result["collected"], 1)
                    self.assertEqual(result["returncode"], 0)
                    self.assertEqual(result["skipped"], 0)
                if status == "no_tests":
                    self.assertEqual(result["collected"], 0)
                    self.assertNotEqual(result["returncode"], 0)
                if status == "error":
                    self.assertEqual(result["collected"], 0, "collection-error XML records are not collected tests")
                self.assertEqual(run.state["phase"], "prepared")
                self.assertIsNone(run.state["started_at"])

    def test_visible_pass_does_not_claim_independent_success_and_edit_invalidates_it(self):
        run = self.fixture().active()
        result = self.assert_ok(run.call("run_visible_tests", paths=["tests/test_visible.py"]))
        self.assertEqual(result["status"], "passed", result)
        self.assert_ok(run.call("apply_patch", patch=patch_for("module.py", "VALUE = 1\n", "VALUE = 2\n")))
        self.assertIsNone(run.state["last_visible_tests"])
        result = self.assert_ok(run.call("finish", reason="saved without further tests"))
        self.assertEqual(result["independent_evaluation"]["status"], "not_run")
        self.assertNotIn("accepted", result)

    def test_activation_clears_preparation_test_evidence(self):
        run = self.fixture()
        result = self.assert_ok(run.call("run_visible_tests", paths=["tests/test_visible.py"]))
        self.assertEqual(result["status"], "passed", result)
        self.assertIsNotNone(run.state["last_visible_tests"])
        run.active()
        self.assertIsNone(run.state["last_visible_tests"])
        self.assertEqual(run.state["action_count"], 0)
        self.assertEqual(run.state["phase"], "active")

    def test_direct_test_configuration_tamper_is_preserved_and_reported(self):
        run = self.fixture().active()
        (run.workspace / "pytest.ini").write_text("[pytest]\naddopts = --ignore=tests\n", encoding="utf-8")
        result = self.assert_ok(run.call("finish", reason="tamper fixture"))
        self.assertTrue(result["submission_saved"])
        self.assertEqual(result["protocol_status"], "violation")
        self.assertTrue(result["protected_violations"])
        self.assertEqual(result["independent_evaluation"]["status"], "not_run")
        self.assertIn("pytest.ini", Path(result["patch"]).read_text(encoding="utf-8"))

    def test_action_limit_persists_across_runner_instances(self):
        run = self.fixture(max_actions=1).active()
        self.assert_ok(run.call("list_files"))
        another = ToolRunner(run.root)
        response = another.dispatch("read_lines", {"path": "module.py"})
        self.assertEqual(response["status"], "error", response)
        self.assertEqual(run.state["action_count"], 1)
        self.assertEqual(len(run.events), 2)

    def test_expired_wall_time_refuses_work_but_can_freeze_evidence(self):
        run = self.fixture().active()
        state = run.state
        state["started_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1201)).isoformat()
        write_json(run.controller / "state.json", state)
        response = run.call("read_lines", path="module.py")
        self.assertEqual(response["status"], "error", response)
        self.assertIn("wall-time", response["error"])
        result = self.assert_ok(run.call("finish", reason="wall time exhausted"))
        self.assertTrue(result["submission_saved"])
        self.assertEqual(result["independent_evaluation"]["status"], "not_run")
        self.assertEqual(run.state["phase"], "finished")

    def test_expired_shared_deadline_logs_uncharged_action_and_allows_finish(self):
        run = self.fixture().active()
        deadline = time.monotonic() - 1
        response = run.runner.dispatch("read_lines", {"path": "module.py"}, deadline=deadline)
        self.assertEqual(response["status"], "error", response)
        self.assertIn("wall-time", response["error"])
        event, = run.events
        self.assertEqual(event["action_count"], 0)
        self.assertEqual(event["changes"], {})
        self.assertIsNone(event["before_digest"])
        self.assertIsNone(event["after_digest"])
        self.assertTrue(Path(event["output_file"]).is_file())

        result = self.assert_ok(run.runner.dispatch("finish", {"reason": "deadline expired"}, deadline=deadline))
        self.assertTrue(result["submission_saved"])
        self.assertEqual(run.state["action_count"], 0)
        self.assertEqual(run.state["phase"], "finished")

    def test_shared_deadline_caps_probe_and_recomputes_pytest_timeout(self):
        run = self.fixture().active()
        run.runner.config["repo"] = "sphinx-doc/sphinx"
        current_time = 100.0

        def probe(command, **kwargs):
            nonlocal current_time
            self.assertEqual(kwargs["timeout"], 5.0)
            current_time = 104.0
            return subprocess.CompletedProcess(command, 0, str(run.workspace / "module.py").encode(), b"")

        def pytest_process(command, workspace, env, stdout, stderr, timeout):
            self.assertEqual(timeout, 1.0)
            stdout.write_bytes(b"")
            stderr.write_bytes(b"")
            return {"returncode": -1, "timed_out": True}

        with patch("tooling.backends.repo.time.monotonic", side_effect=lambda: current_time), \
             patch("tooling.backends.repo.subprocess.run", side_effect=probe) as probe_call, \
             patch("tooling.backends.repo._process", side_effect=pytest_process) as pytest_call:
            response = run.runner.dispatch("run_visible_tests", {"paths": ["tests/test_visible.py"]}, deadline=105.0)
        probe_call.assert_called_once()
        pytest_call.assert_called_once()
        self.assertEqual(self.assert_ok(response)["status"], "timeout")
        self.assertEqual(run.state["action_count"], 1)

    def test_timeout_keeps_logs_and_terminates_owned_process_tree(self):
        pid_file = self.root / "owned-pids.json"
        source = (
            "import json, os, pathlib, subprocess, sys, time\n"
            "def test_timeout():\n"
            "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "    pathlib.Path(" + repr(str(pid_file)) + ").write_text(json.dumps([os.getpid(), child.pid]))\n"
            "    print('timeout fixture started', flush=True)\n"
            "    time.sleep(60)\n"
        )
        run = self.fixture(test_source=source)
        pids = []
        try:
            result = self.assert_ok(run.call("run_visible_tests", paths=["tests/test_visible.py"], timeout_seconds=3))
            self.assertEqual(result["status"], "timeout", result)
            self.assertTrue(Path(result["log"]).is_file())
            self.assertGreater(Path(result["log"]).stat().st_size, 0)
            self.assertTrue(pid_file.is_file(), "the subprocess must reach its test body before timing out")
            pids = json.loads(pid_file.read_text())
            until = time.monotonic() + 3
            while any(process_alive(pid) for pid in pids) and time.monotonic() < until:
                time.sleep(0.05)
            self.assertFalse(any(process_alive(pid) for pid in pids), "owned descendants survived timeout")
            self.assertEqual(run.events[-1]["action"], "run_visible_tests")
        finally:
            if not pids and pid_file.is_file():
                pids = json.loads(pid_file.read_text())
            for pid in pids:
                if process_alive(pid):
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
                    else:
                        os.kill(pid, signal.SIGKILL)


if __name__ == "__main__":
    unittest.main(verbosity=2)
