"""Run lifecycle: phase, budget, wall clock, integrity audit and finish export."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from runtime import fsguard
from tools import handlers


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class Session:
    def __init__(self, env, controller, protocol, specs, allowed, initial_manifest=None):
        self.env = env
        self.controller = Path(controller)
        self.controller.mkdir(parents=True, exist_ok=True)
        (self.controller / "outputs").mkdir(exist_ok=True)
        self.protocol = dict(protocol)
        self.specs = specs
        self.allowed = set(allowed)
        self.initial = initial_manifest if initial_manifest is not None else env.manifest()
        self.state_path = self.controller / "state.json"
        self.state = {
            "phase": "prepared", "action_count": 0, "event_count": 0,
            "started_at": None, "last_visible_tests": None, "violations": [],
        }
        self.deadline = None
        self.ctx = handlers.HandlerContext(
            specs=specs, allowed=self.allowed, outputs=self.controller / "outputs",
            protected=env.is_protected, python=env.python,
            test_timeout=float(self.protocol.get("test_timeout_seconds", 90)),
        )

    # --- lifecycle ---------------------------------------------------------
    def activate(self):
        if self.state["phase"] != "prepared":
            raise ValueError("Only a prepared session can be activated")
        self.state["phase"] = "active"
        self.state["started_at"] = _now()
        self.deadline = time.monotonic() + float(self.protocol.get("wall_time_seconds", 1200))
        self._save()
        return self.state

    def _save(self):
        _write_json(self.state_path, self.state)

    def _result(self, action, args, observation, event_id):
        event = {"event_id": event_id, "action": action, "args": args,
                 "status": observation.get("status", "ok" if observation.get("ok") else "error"),
                 "ok": bool(observation.get("ok")), "phase": self.state["phase"],
                 "action_count": self.state["action_count"], "at": _now()}
        with (self.controller / "actions.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._save()

    # --- dispatch ----------------------------------------------------------
    def dispatch(self, action, args=None):
        args = args or {}
        spec = self.specs.get(action)
        if spec is None or action not in self.allowed:
            return {"ok": False, "tool": action, "error": "Unknown workspace tool", "kind": "tool"}
        if self.state["phase"] != "active":
            return {"ok": False, "tool": action, "error": "Run is not active", "kind": "tool"}
        if action != "finish":
            if self.state["action_count"] >= int(self.protocol.get("max_actions", 30)):
                return {"ok": False, "tool": action, "error": "Workspace action limit reached", "kind": "budget"}
            if self.deadline is not None and time.monotonic() >= self.deadline:
                return {"ok": False, "tool": action, "error": "Trial wall-time limit reached", "kind": "timeout"}

        self.state["event_count"] += 1
        event_id = self.state["event_count"]
        self.ctx.event_id = event_id
        before = self.env.manifest() if spec.get("executes_code") else None
        if action == "finish":
            observation = handlers.dispatch("finish", self.env, self.ctx, args)
            if observation.get("ok"):
                submission = self._finish(observation["result"]["reason"])
                observation["result"] = {"finished": True, "accepted": None,
                                         "reason": observation["result"]["reason"],
                                         "submission": submission}
                self.state["phase"] = "finished"
            self._result(action, args, observation, event_id)
            return observation

        observation = handlers.dispatch(action, self.env, self.ctx, args)
        if spec.get("executes_code"):
            after = self.env.manifest()
            changed = fsguard.changes(before, after)
            violations = sorted(name for name in changed if self.env.is_protected(name))
            if violations:
                self.state["violations"] = sorted(set(self.state["violations"]) | set(violations))
            if observation.get("ok"):
                self.state["last_visible_tests"] = observation.get("result")
        self.state["action_count"] += 1
        self._result(action, args, observation, event_id)
        return observation

    # --- finish ------------------------------------------------------------
    def _finish(self, reason):
        current = self.env.manifest()
        changed = fsguard.changes(self.initial, current)
        violations = sorted(name for name in changed if self.env.is_protected(name))
        submission_dir = self.controller / "submission"
        submission_dir.mkdir(exist_ok=True)
        patch = submission_dir / "final.patch"
        self.env.export_patch(patch)
        data = patch.read_bytes()
        result = {"reason": reason, "patch": str(patch), "patch_sha256": _sha(data),
                  "changes": sorted(changed), "protected_violations": violations,
                  "workspace_digest": fsguard.digest(current),
                  "visible_tests": self.state.get("last_visible_tests"),
                  "completed_at": _now()}
        _write_json(submission_dir / "submission.json", result)
        return result
