"""Generic run orchestrator: Task -> Environment -> Session -> Agent -> Evaluator.

This is the single entry point for a run. A Task supplies the workspace,
problem statement, visibility, protocol, toolset and evaluator; the Policy
decides actions; LocalEnvironment and Session enforce execution and budgets.

A summary is written for every run that has a run directory, including runs
where preparation, the agent, finalization or evaluation failed. The summary
records which stage failed so a failed evaluation cannot hide an earlier agent
failure.
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys
import time
from typing import Protocol
from uuid import uuid4

from agent_core.agent import AGENT_VERSION, Agent
from agent_core.report import write_report
from runtime.local import LocalEnvironment
from runtime.session import Session
from tools.specs import TOOL_SPECS
from tools.toolsets import tool_labels_for, tool_names, tool_versions, validate_tool_names


DEFAULT_MAX_ACTIONS = 60


class Task(Protocol):
    task_id: str
    problem: str
    toolset: str
    visibility: object
    protocol: dict
    run_prefix: str
    max_steps: int
    python: str
    git: str

    def prepare(self, run_dir: Path) -> Path:
        ...

    def evaluate(self, run_dir: Path, workspace: Path, patch: Path | None) -> dict:
        ...


def _configure_stdout():
    """Avoid UnicodeEncodeError on non-UTF-8 consoles (e.g. Windows CI)."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def _emit(message):
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        safe = message.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe, flush=True)


def _write_summary(path, summary):
    """Write the durable run record even if a value is not plain JSON."""
    path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")


def _visibility_summary(task):
    visibility = getattr(task, "visibility", None)
    if visibility is None:
        return None
    return {"readable": list(getattr(visibility, "readable", ())),
            "editable": list(getattr(visibility, "editable", ())),
            "protected": list(getattr(visibility, "protected", ()))}


def run(task, policy=None, *, tools=None, policy_factory=None, output_root=None,
        max_steps=None, max_actions=None):
    _configure_stdout()
    started = time.perf_counter()
    root = Path(output_root) if output_root is not None else Path(
        getattr(task, "runs_root", Path("runs")))
    prefix = getattr(task, "run_prefix", "run")
    max_steps = int(max_steps if max_steps is not None else getattr(task, "max_steps", 16))
    if max_steps < 1:
        raise ValueError("max_steps must be positive")
    if max_actions is None:
        max_actions = int(getattr(task, "protocol", {}).get("max_actions",
                                                            DEFAULT_MAX_ACTIONS))
    else:
        max_actions = int(max_actions)
    if max_actions < 1:
        raise ValueError("max_actions must be positive")
    protocol = dict(getattr(task, "protocol", {}))
    protocol["max_actions"] = max_actions

    run_id = prefix + "-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
    run_dir = root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    requested_names = list(tools) if tools is not None else None
    names = []
    tool_versions_map = {}
    history = []
    status, reason = "incomplete", "run did not start"
    stage = "policy"
    session = None
    evaluation = None
    final_patch = None
    error = None
    error_stage = None
    finalize_error = None

    try:
        if requested_names is None:
            requested_names = tool_names(task.toolset)
        names = list(requested_names)
        names = validate_tool_names(names)
        tool_versions_map = tool_versions(names)
        if policy_factory is not None:
            policy = policy_factory(run_dir, task)
        if policy is None:
            raise ValueError("run() needs a policy or a policy_factory")

        stage = "prepare"
        workspace = task.prepare(run_dir)
        (run_dir / "task.txt").write_text(task.problem + "\n", encoding="utf-8")

        stage = "environment"
        env = LocalEnvironment(workspace, visibility=task.visibility,
                               python=getattr(task, "python", None),
                               git=getattr(task, "git", "git"),
                               scratch=run_dir / "scratch")
        specs = {name: TOOL_SPECS[name] for name in names}
        session = Session(env, run_dir / "controller", protocol, specs, set(specs),
                          initial_manifest=env.manifest())
        session.activate()

        stage = "agent"
        history, status, reason = Agent(session, max_steps=max_steps).run(
            policy, run_dir / "trajectory.jsonl")

        stage = "finalize"
        submission = run_dir / "controller" / "submission" / "final.patch"
        if not submission.exists():
            session.finalize(reason)
        patch = submission if submission.exists() else None
        if patch is not None:
            final_patch = run_dir / "final.patch"
            final_patch.write_bytes(patch.read_bytes())

        stage = "evaluate"
        evaluation = task.evaluate(run_dir, workspace, patch)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        error_stage = stage
        if stage == "evaluate":
            # The agent has already stopped; keep its status and record the
            # evaluation failure separately so the summary is still useful.
            evaluation = {"resolved": False, "status": "evaluation_error", "error": error}
        else:
            status, reason = "run_failed", error
            evaluation = {"resolved": False, "status": "not_run", "error": error}
            if session is not None and session.state.get("phase") == "active":
                try:
                    session.finalize(reason)
                    submission = run_dir / "controller" / "submission" / "final.patch"
                    if final_patch is None and submission.exists():
                        final_patch = run_dir / "final.patch"
                        final_patch.write_bytes(submission.read_bytes())
                except Exception as exc:
                    finalize_error = f"{type(exc).__name__}: {exc}"

    if not isinstance(evaluation, dict):
        evaluation = {"resolved": False, "status": "invalid_evaluation",
                      "error": f"evaluate() returned {type(evaluation).__name__}"}
    resolved = bool(evaluation.get("resolved", evaluation.get("passed", False)))
    if final_patch is not None and not final_patch.exists():
        final_patch = None
    final_diff = None
    if final_patch is not None:
        try:
            final_diff = final_patch.read_text(encoding="utf-8", errors="replace")
            if len(final_diff) > 500_000:
                final_diff = final_diff[:500_000] + "\n... (truncated)"
        except OSError:
            final_diff = None

    usages = [event["llm"] for event in history if "llm" in event]
    summary = {
        "mode": "llm_demo" if hasattr(policy, "model") else "scripted_demo",
        "task_id": getattr(task, "task_id", ""),
        "versions": {
            "agent": AGENT_VERSION,
            "action": "v1", "tools": "v1",
            "prompt": getattr(policy, "prompt_version", "v1"),
            "trajectory": "v1",
        },
        "agent_version": AGENT_VERSION,
        "prompt_version": getattr(policy, "prompt_version", None),
        "context_policy": ({
            "max_history": getattr(policy, "max_history", None),
            "observation_limit": getattr(policy, "observation_limit", None),
            "notes_limit": getattr(policy, "notes_limit", None),
            "read_repeat_warning": getattr(policy, "read_repeat_warning", None),
            "stale_edit_steps": getattr(policy, "stale_edit_steps", None),
        } if hasattr(policy, "max_history") else None),
        "retry_policy": ({
            "max_parse_attempts": getattr(policy, "max_parse_attempts", None),
            "max_dsml_retries": getattr(policy, "max_dsml_retries", None),
        } if hasattr(policy, "max_parse_attempts") else None),
        "toolset": getattr(task, "toolset", ""),
        "tools_enabled": names,
        "tool_versions": tool_versions_map,
        "env": "local",
        "visibility": _visibility_summary(task),
        "model": getattr(policy, "model", "scripted_teaching"),
        "task": getattr(task, "problem", ""),
        "status": status,
        "stop_reason": reason,
        "steps": len(history),
        "max_steps": max_steps,
        "max_actions": max_actions,
        "actions": session.state["action_count"] if session is not None else 0,
        "model_calls": getattr(policy, "calls", 0),
        "tokens": ({"prompt": sum(item.get("prompt_tokens", 0) for item in usages),
                    "completion": sum(item.get("completion_tokens", 0) for item in usages)}
                   if usages else None),
        "cost_usd": round(sum(item.get("cost_usd", 0.0) for item in usages), 6),
        "budget_usd": (float(policy.budget_usd)
                       if getattr(policy, "budget_usd", None) is not None else None),
        "spent_usd": float(getattr(policy, "spent_usd", 0.0)),
        "llm_log": (Path(policy.log_path).name if getattr(policy, "log_path", None) else None),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "resolved": resolved,
        "evaluation": evaluation,
        "final_patch": final_patch.name if final_patch else None,
        "final_diff": final_diff,
        "run_dir": run_dir.name,
    }
    if error is not None:
        summary["error"] = error
        summary["error_stage"] = error_stage
    if finalize_error is not None:
        summary["finalize_error"] = finalize_error

    summary_path = run_dir / "summary.json"
    _write_summary(summary_path, summary)

    try:
        write_report(summary, history, run_dir / "report.html",
                     labels=tool_labels_for(names))
    except Exception as exc:
        # The summary is the durable record; a report rendering problem must not
        # hide it. Record the failure and rewrite the summary.
        summary["report_error"] = f"{type(exc).__name__}: {exc}"
        _write_summary(summary_path, summary)

    _emit(f"\n状态：{status}；可见测试记录保存在 trajectory.jsonl")
    _emit(f"resolved：{str(resolved).lower()}")
    if error is not None:
        _emit(f"错误（{error_stage}）：{error}")
    metrics = evaluation.get("metrics")
    if isinstance(metrics, dict):
        for key, item in metrics.items():
            if isinstance(item, dict) and "passed" in item and "total" in item:
                _emit(f"{item.get('label', key)}：{item['passed']}/{item['total']}")
    if isinstance(evaluation.get("new_regressions"), list):
        _emit(f"新增回归：{len(evaluation['new_regressions'])}")
    _emit(f"报告：{run_dir / 'report.html'}")
    return summary, history, run_dir
