"""Portal HTTP contracts via FastAPI TestClient; no real subprocess is spawned."""

from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient

from agent_core.llm import PROMPT_VERSION
from web import server


def _no_launch(record, config):
    """Stand in for _launch: mark the run finished without spawning a child."""
    record.status = "done"
    record.returncode = 0
    record.done.set()


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

    def test_catalog_lists_tasks_and_only_runnable_tools(self):
        data = self.client.get("/api/catalog").json()
        task_ids = {t["task_id"] for t in data["tasks"]}
        self.assertIn("toy", task_ids)
        names = {t["name"] for t in data["tools"]}
        self.assertNotIn("plan", names)          # handler-less spec excluded
        self.assertIn("finish", names)
        finish = next(t for t in data["tools"] if t["name"] == "finish")
        self.assertTrue(finish["required"])
        self.assertEqual(data["required_tool"], "finish")

    def test_preflight_shape(self):
        data = self.client.get("/api/tasks/toy/preflight?policy=scripted").json()
        self.assertTrue(data["checks"])
        self.assertEqual(set(data["checks"][0]), {"name", "ok", "detail"})


class RunValidationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        server.RUNS.clear()

    def test_unknown_task_is_400(self):
        r = self.client.post("/api/runs", json={"task": "nope", "policy": "scripted"})
        self.assertEqual(r.status_code, 400)

    def test_invalid_policy_is_400(self):
        r = self.client.post("/api/runs", json={"task": "toy", "policy": "reference"})
        self.assertEqual(r.status_code, 400)

    def test_missing_edit_tool_is_400(self):
        r = self.client.post("/api/runs", json={
            "task": "toy", "policy": "scripted", "tools": ["list_files"]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("编辑工具", r.json()["detail"])

    def test_valid_request_forces_finish_and_starts(self):
        with mock.patch.object(server, "_launch", _no_launch):
            r = self.client.post("/api/runs", json={
                "task": "toy", "policy": "scripted",
                "tools": ["list_files", "replace_text"]})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIn("finish", body["config"]["tools"])
        self.assertIn("replace_text", body["config"]["tools"])
        self.assertEqual(set(body["config"]["tool_versions"]),
                         set(body["config"]["tools"]))

    def test_llm_request_records_prompt_version(self):
        with mock.patch.object(server, "_launch", _no_launch):
            r = self.client.post("/api/runs", json={
                "task": "toy", "policy": "llm", "tools": ["replace_text"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["config"]["prompt_version"], PROMPT_VERSION)

    def test_concurrency_cap(self):
        with mock.patch.object(server, "_launch", lambda rec, cfg: None):
            first = self.client.post("/api/runs", json={
                "task": "toy", "policy": "scripted", "tools": ["replace_text"]})
            self.assertEqual(first.status_code, 200)
            # The first run stays "running" (launch stub never sets done).
            second = self.client.post("/api/runs", json={
                "task": "toy", "policy": "scripted", "tools": ["replace_text"]})
            self.assertEqual(second.status_code, 429)


if __name__ == "__main__":
    unittest.main()
