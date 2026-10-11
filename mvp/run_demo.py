"""Run the teaching MVP: python mvp/run_demo.py [--open] [--max-steps 24]."""

import argparse
from datetime import datetime
import difflib
import json
from pathlib import Path
import shutil
import sys
import time
from uuid import uuid4
import webbrowser

from hj10.agent import Agent
from hj10.policy import ScriptedPolicy
from hj10.report import write_report
from hj10.tools import Tools, run_suite


MVP_ROOT = Path(__file__).resolve().parent
FIXTURE = MVP_ROOT / "fixtures" / "timeout_demo"
HIDDEN_TESTS = MVP_ROOT / "evaluation" / "hidden_timeout_tests.py"
TASK = ("为下载请求增加可配置超时：CLI 优先于配置，均缺省时为 5；"
        "最终值须为正的有限 int/float（拒绝 bool 与字符串）；"
        "仅验证最终选中值，并保留原有 URL 和默认请求行为。")


def metrics(result, class_name=None):
    cases = result["cases"]
    if class_name:
        cases = [case for case in cases if f".{class_name}." in case["name"]]
    passed = sum(case["status"] == "passed" for case in cases)
    status = result["status"]
    if cases:
        status = "passed" if passed == len(cases) else "failed"
    return {"total": len(cases), "passed": passed, "failed": len(cases) - passed,
            "status": status}


def evaluate(workspace, baseline=FIXTURE):
    """Called only AFTER the policy and controller have stopped."""
    initial = run_suite(baseline, HIDDEN_TESTS, "TestRegressions")
    final = run_suite(workspace, HIDDEN_TESTS)
    initially_passed = {case["name"] for case in initial["cases"]
                        if case["status"] == "passed"}
    new_regressions = [case["name"] for case in final["cases"]
                       if case["name"] in initially_passed and case["status"] != "passed"]
    errors = [result.get("error", "Unknown environment error") for result in (initial, final)
              if result["status"] == "environment_error"]
    execution_errors = [{"status": result["status"], "detail": result.get("error", "")}
                        for result in (initial, final) if result["status"] not in {"passed", "failed"}]
    return {
        "requirements": metrics(final, "TestRequirements"),
        "regressions": metrics(final, "TestRegressions"),
        "baseline_regressions": metrics(initial),
        "new_regressions": new_regressions,
        "environment_errors": errors,
        "execution_errors": execution_errors,
        "passed": (initial["status"] == "passed" and final["status"] == "passed"
                   and not new_regressions),
        "cases": final["cases"],
        "note": "Evaluator-only teaching checks, publicly versioned; not a private benchmark.",
    }


def final_diff(workspace):
    patches = []
    for name in ("config.py", "client.py", "cli.py"):
        before = (FIXTURE / name).read_text(encoding="utf-8")
        after = (workspace / name).read_text(encoding="utf-8")
        patches.extend(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                            fromfile=f"a/{name}", tofile=f"b/{name}"))
    return "".join(patches)


def run_demo(output_root=None, max_steps=24):
    started = time.perf_counter()
    if max_steps < 1:
        raise ValueError("max_steps must be positive")
    output_root = Path(output_root) if output_root else MVP_ROOT / "runs"
    run_id = "demo-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
    run_dir = output_root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    workspace = run_dir / "workspace"
    shutil.copytree(FIXTURE, workspace, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (run_dir / "task.txt").write_text(TASK, encoding="utf-8")
    print("HJ10 教学 MVP：预设决策，真实文件操作与测试。", flush=True)
    events, status, reason = Agent(Tools(workspace), max_steps).run(
        ScriptedPolicy(), run_dir / "trajectory.jsonl"
    )
    # Evaluation is intentionally outside Agent.run and never enters its history.
    evaluation = evaluate(workspace)
    patch = final_diff(workspace)
    summary = {
        "mode": "scripted_demo", "task": TASK, "status": status,
        "stop_reason": reason, "steps": len(events), "max_steps": max_steps,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "model_calls": 0, "tokens": None, "evaluation": evaluation,
        "final_diff": patch, "run_dir": run_id,
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "final.patch").write_text(patch, encoding="utf-8", newline="\n")
    write_report(summary, events, run_dir / "report.html")
    print(f"\n状态：{status}；可见测试记录保存在 trajectory.jsonl", flush=True)
    print(f"独立需求测试：{evaluation['requirements']['passed']}/{evaluation['requirements']['total']}")
    print(f"独立回归测试：{evaluation['regressions']['passed']}/{evaluation['regressions']['total']}")
    print(f"新增回归：{len(evaluation['new_regressions'])}")
    print(f"报告：{run_dir / 'report.html'}")
    return summary, events, run_dir


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="HJ10 scripted teaching MVP; no model or dependencies needed.")
    parser.add_argument("--max-steps", type=int, default=24, help="Tool call budget (default: 24)")
    parser.add_argument("--output-dir", type=Path, help="Parent directory for a new, unique run")
    parser.add_argument("--open", action="store_true", help="Open the generated offline report")
    args = parser.parse_args()
    if args.max_steps < 1:
        parser.error("--max-steps must be positive")
    summary, _, run_dir = run_demo(args.output_dir, args.max_steps)
    if args.open:
        webbrowser.open((run_dir / "report.html").as_uri())
    return 0 if summary["status"] == "completed" and summary["evaluation"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
