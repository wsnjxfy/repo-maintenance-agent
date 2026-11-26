"""Portal HTTP contracts via FastAPI TestClient; no real subprocess is spawned."""

from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient

from agent_core.agent import AGENT_VERSION
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
        self.assertNotIn("plan", names)          # legacy handler-less spec removed
        self.assertIn("finish", names)
        finish = next(t for t in data["tools"] if t["name"] == "finish")
        self.assertTrue(finish["required"])
        self.assertEqual(data["required_tool"], "finish")
        toolsets = {t["name"]: t for t in data["toolsets"]}
        self.assertIn("repo-v2", toolsets)
        self.assertIn("create_file", toolsets["repo-v2"]["tools"])
        create = next(t for t in data["tools"] if t["name"] == "create_file")
        self.assertTrue(create["is_edit"])
        self.assertIn("edit_lines", data["edit_tools"])

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

    def test_unknown_toolset_is_400(self):
        r = self.client.post("/api/runs", json={
            "task": "toy", "policy": "scripted", "toolset": "no-such-toolset"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Unknown toolset", r.json()["detail"])

    def test_tools_must_be_in_selected_toolset(self):
        r = self.client.post("/api/runs", json={
            "task": "toy", "policy": "scripted", "toolset": "toy-v2",
            "tools": ["create_file", "finish"]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("不在选择的 toolset", r.json()["detail"])

    def test_invalid_observation_limit_is_400(self):
        r = self.client.post("/api/runs", json={
            "task": "toy", "policy": "scripted", "observation_limit": 0})
        self.assertEqual(r.status_code, 400)
        self.assertIn("observation_limit", r.json()["detail"])

    def test_observation_limit_is_recorded(self):
        with mock.patch.object(server, "_launch", _no_launch):
            r = self.client.post("/api/runs", json={
                "task": "toy", "policy": "scripted", "observation_limit": 4000})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["config"]["observation_limit"], 4000)
        self.assertEqual(r.json()["config"]["max_steps"], 60)

    def test_new_edit_tools_are_accepted(self):
        with mock.patch.object(server, "_launch", _no_launch):
            r = self.client.post("/api/runs", json={
                "task": "toy", "policy": "scripted", "toolset": "repo-v2",
                "tools": ["create_file", "edit_lines", "finish"]})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["config"]["toolset"], "repo-v2")
        self.assertIn("create_file", body["config"]["tools"])
        self.assertIn("edit_lines", body["config"]["tools"])

    def test_toolset_surface_is_used_when_no_tools_are_requested(self):
        with mock.patch.object(server, "_launch", _no_launch):
            r = self.client.post("/api/runs", json={
                "task": "toy", "policy": "scripted", "toolset": "repo-v2"})
        self.assertEqual(r.status_code, 200, r.text)
        tools = set(r.json()["config"]["tools"])
        self.assertIn("create_file", tools)
        self.assertIn("show_diff", tools)
        self.assertIn("finish", tools)

    def test_valid_request_forces_finish_and_starts(self):
        with mock.patch.object(server, "_launch", _no_launch):
            r = self.client.post("/api/runs", json={
                "task": "toy", "policy": "scripted",
                "tools": ["list_files", "replace_text"]})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIn("finish", body["config"]["tools"])
        self.assertIn("replace_text", body["config"]["tools"])
        self.assertEqual(body["config"]["toolset"], "toy-v2")
        self.assertEqual(body["config"]["max_actions"], 60)
        self.assertEqual(set(body["config"]["tool_versions"]),
                         set(body["config"]["tools"]))

    def test_max_actions_can_be_overridden_and_must_be_positive(self):
        with mock.patch.object(server, "_launch", _no_launch):
            r = self.client.post("/api/runs", json={
                "task": "toy", "policy": "scripted",
                "tools": ["replace_text"], "max_actions": 75})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["config"]["max_actions"], 75)
        bad = self.client.post("/api/runs", json={
            "task": "toy", "policy": "scripted",
            "tools": ["replace_text"], "max_actions": 0})
        self.assertEqual(bad.status_code, 400)

    def test_llm_request_records_prompt_version(self):
        with mock.patch.object(server, "_launch", _no_launch):
            r = self.client.post("/api/runs", json={
                "task": "toy", "policy": "llm", "tools": ["replace_text"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["config"]["prompt_version"], PROMPT_VERSION)
        self.assertEqual(r.json()["config"]["agent_version"], AGENT_VERSION)

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
