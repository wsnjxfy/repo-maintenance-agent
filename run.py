"""Generic run orchestrator: Task -> Environment -> Session -> Agent -> Evaluator.

This is the single entry point for a run. A Task supplies the workspace,
problem statement, visibility, protocol, toolset and evaluator; the Policy
decides actions; LocalEnvironment and Session enforce execution and budgets.
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import time
from typing import Protocol
from uuid import uuid4

from agent_core.agent import Agent
from agent_core.report import write_report
from runtime.local import LocalEnvironment
from runtime.session import Session
from tools.specs import TOOL_SPECS
from tools.toolsets import tool_labels, tool_names


class Task(Protocol):
    task_id: str
    problem: str
    toolset: str
    visibility: object
    protocol: dict
    run_prefix: str
    max_steps: int

    def prepare(self, run_dir: Path) -> Path:
        ...

    def evaluate(self, workspace: Path, patch: Path | None) -> dict:
        ...


def run(task, policy, *, output_root=None, max_steps=None):
    started = time.perf_counter()
    root = Path(output_root) if output_root is not None else Path(
        getattr(task, "runs_root", Path("runs")))
    prefix = getattr(task, "run_prefix", "run")
    max_steps = int(max_steps if max_steps is not None else getattr(task, "max_steps", 16))
    if max_steps < 1:
        raise ValueError("max_steps must be positive")

    run_id = prefix + "-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
    run_dir = root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    workspace = task.prepare(run_dir)
    (run_dir / "task.txt").write_text(task.problem + "\n", encoding="utf-8")

    env = LocalEnvironment(workspace, visibility=task.visibility,
                           scratch=run_dir / "scratch")
    specs = {name: TOOL_SPECS[name] for name in tool_names(task.toolset)}
    session = Session(env, run_dir / "controller", task.protocol, specs, set(specs),
                      initial_manifest=env.manifest())
    session.activate()
    history, status, reason = Agent(session, max_steps=max_steps).run(
        policy, run_dir / "trajectory.jsonl")

    submission = run_dir / "controller" / "submission" / "final.patch"
    patch = submission if submission.exists() else None
    evaluation = task.evaluate(workspace, patch)
    if patch is not None:
        final_patch = run_dir / "final.patch"
        final_patch.write_bytes(patch.read_bytes())
    else:
        final_patch = None

    usages = [event["llm"] for event in history if "llm" in event]
    summary = {
        "mode": "llm_demo" if hasattr(policy, "model") else "scripted_demo",
        "task_id": task.task_id,
        "versions": {
            "action": "v1", "tools": "v1",
            "prompt": getattr(policy, "prompt_version", "v1"),
            "trajectory": "v1",
        },
        "toolset": task.toolset,
        "env": "local",
        "visibility": {"readable": list(task.visibility.readable),
                       "editable": list(task.visibility.editable),
                       "protected": list(task.visibility.protected)},
        "model": getattr(policy, "model", "scripted_teaching"),
        "task": task.problem,
        "status": status,
        "stop_reason": reason,
        "steps": len(history),
        "max_steps": max_steps,
        "actions": session.state["action_count"],
        "model_calls": getattr(policy, "calls", 0),
        "tokens": ({"prompt": sum(item.get("prompt_tokens", 0) for item in usages),
                    "completion": sum(item.get("completion_tokens", 0) for item in usages)}
                   if usages else None),
        "cost_usd": round(sum(item.get("cost_usd", 0.0) for item in usages), 6),
        "llm_log": (Path(policy.log_path).name if getattr(policy, "log_path", None) else None),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "evaluation": evaluation,
        "final_patch": final_patch.name if final_patch else None,
        "run_dir": run_dir.name,
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(summary, history, run_dir / "report.html",
                 labels=tool_labels(task.toolset))

    print(f"\n状态：{status}；可见测试记录保存在 trajectory.jsonl", flush=True)
    print(f"独立需求测试：{evaluation['requirements']['passed']}/"
          f"{evaluation['requirements']['total']}", flush=True)
    print(f"独立回归测试：{evaluation['regressions']['passed']}/"
          f"{evaluation['regressions']['total']}", flush=True)
    print(f"新增回归：{len(evaluation['new_regressions'])}", flush=True)
    print(f"报告：{run_dir / 'report.html'}", flush=True)
    return summary, history, run_dir
