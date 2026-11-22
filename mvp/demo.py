"""v2 toy demo: same fixture, real LocalEnvironment/Session/tools/Agent.

The old ``run_demo.py`` still drives the toy backend; this script proves the
shared stack can run the same teaching task end to end.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_core.agent import Agent
from agent_core.report import write_report
from runtime.local import LocalEnvironment
from runtime.session import Session
from tools.specs import TOOL_SPECS
from tools.toolsets import tool_labels, tool_names
from run_demo import FIXTURE, TASK, evaluate
from scripted_policy import ToyV2Policy
from toy_config import PROTOCOL, TOOLSET, VISIBILITY


MVP_ROOT = Path(__file__).resolve().parent


def _prepare_workspace(workspace: Path) -> None:
    (workspace / ".gitignore").write_text(
        "__pycache__/\n.pytest_cache/\n*.pyc\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "toy@example.test"],
        ["git", "config", "user.name", "toy-demo"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "baseline"],
    ):
        subprocess.run(command, cwd=workspace, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def run(output_root=None, max_steps=16):
    started = time.perf_counter()
    output_root = Path(output_root) if output_root else MVP_ROOT / "runs"
    run_id = "toyv2-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
    run_dir = output_root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    workspace = run_dir / "workspace"
    shutil.copytree(FIXTURE, workspace, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (run_dir / "task.txt").write_text(TASK, encoding="utf-8")
    _prepare_workspace(workspace)

    env = LocalEnvironment(workspace, visibility=VISIBILITY, scratch=run_dir / "scratch")
    specs = {name: TOOL_SPECS[name] for name in tool_names(TOOLSET)}
    session = Session(env, run_dir / "controller", PROTOCOL, specs, set(specs),
                      initial_manifest=env.manifest())
    session.activate()
    history, status, reason = Agent(session, max_steps=max_steps).run(
        ToyV2Policy(), run_dir / "trajectory.jsonl")

    evaluation = evaluate(workspace)
    submission = run_dir / "controller" / "submission" / "final.patch"
    if submission.exists():
        shutil.copyfile(submission, run_dir / "final.patch")

    summary = {
        "mode": "toy_v2_demo",
        "versions": {"action": "v1", "tools": "v1", "prompt": "v1", "trajectory": "v1"},
        "toolset": TOOLSET,
        "env": "local",
        "visibility": {"readable": list(VISIBILITY.readable),
                       "editable": list(VISIBILITY.editable),
                       "protected": list(VISIBILITY.protected)},
        "model": "scripted_teaching",
        "task": TASK,
        "status": status,
        "stop_reason": reason,
        "steps": len(history),
        "max_steps": max_steps,
        "actions": session.state["action_count"],
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "evaluation": evaluation,
        "final_patch": "final.patch" if submission.exists() else None,
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(summary, history, run_dir / "report.html", labels=tool_labels(TOOLSET))

    print(f"\n状态：{status}；可见测试记录保存在 trajectory.jsonl", flush=True)
    print(f"独立需求测试：{evaluation['requirements']['passed']}/"
          f"{evaluation['requirements']['total']}", flush=True)
    print(f"独立回归测试：{evaluation['regressions']['passed']}/"
          f"{evaluation['regressions']['total']}", flush=True)
    print(f"新增回归：{len(evaluation['new_regressions'])}", flush=True)
    print(f"报告：{run_dir / 'report.html'}", flush=True)
    return summary, history, run_dir


def main(argv=None):
    parser = argparse.ArgumentParser(description="v2 toy demo on LocalEnvironment")
    parser.add_argument("--output-dir", type=Path, help="Parent directory for a new run")
    parser.add_argument("--max-steps", type=int, default=16)
    args = parser.parse_args(argv)
    if args.max_steps < 1:
        parser.error("--max-steps must be positive")
    summary, _, _ = run(args.output_dir, args.max_steps)
    return 0 if summary["status"] == "completed" and summary["evaluation"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
