"""Evaluation helpers for the teaching fixture (kept outside the agent loop)."""

from __future__ import annotations

import difflib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from runtime.exec.suite import run_suite


MVP_ROOT = Path(__file__).resolve().parents[1]
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
