"""Persisted web-run history tests (no subprocesses, no real model)."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient

from agent_core.llm import PROMPT_VERSION
from web import server


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hj10-web-history-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch = mock.patch.object(server, "RUNS_ROOT", self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        server.RUNS.clear()

    def tearDown(self):
        server.RUNS.clear()

    def make_run(self, run_id="20260101-120000-abcdef", status=None):
        run_dir = self.root / run_id
        child = run_dir / "sphinx-20260101-120000-111111"
        child.mkdir(parents=True)
        summary = {
            "task_id": "sphinx-doc__sphinx-7462",
            "mode": "llm_demo",
            "tools_enabled": ["list_files", "finish"],
            "tool_versions": {"list_files": 0, "finish": 0},
            "versions": {"prompt": PROMPT_VERSION},
            "model": "deepseek-flash",
            "max_steps": 30,
            "status": "completed",
            "resolved": True,
        }
        (child / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        (run_dir / "web.log").write_text("line one\nline two\n", encoding="utf-8")
        if status is not None:
            (run_dir / "web-run.json").write_text(json.dumps({
                "id": run_id,
                "config": {"task": "sphinx-7462", "policy": "llm",
                           "tools": ["list_files", "finish"]},
                "created": 1.0, "status": status, "returncode": 0,
            }), encoding="utf-8")
        return run_dir

    def test_legacy_run_is_loaded_from_summary(self):
        self.make_run()
        loaded = server.load_runs()
        self.assertEqual(len(loaded), 1)
        record = loaded[0]
        self.assertEqual(record.config["task"], "sphinx-7462")
        self.assertEqual(record.config["policy"], "llm")
        self.assertEqual(record.status, "done")
        self.assertEqual(record.log_lines, ["line one", "line two"])
        self.assertEqual(record.tool_versions(), {"list_files": 0, "finish": 0})
        self.assertEqual(record.prompt_version(), PROMPT_VERSION)
        self.assertTrue(record.done.is_set())

    def test_stale_running_metadata_becomes_error(self):
        self.make_run(status="running")
        loaded = server.load_runs()
        self.assertEqual(loaded[0].status, "error")

    def test_api_lists_history_after_reload(self):
        self.make_run()
        server.load_runs()
        client = TestClient(server.app)
        data = client.get("/api/runs").json()
        self.assertEqual(len(data["runs"]), 1)
        self.assertEqual(data["runs"][0]["config"]["task"], "sphinx-7462")
        self.assertEqual(data["runs"][0]["prompt_version"], PROMPT_VERSION)
        self.assertTrue(data["runs"][0]["summary"]["resolved"])
