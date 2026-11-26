"""Toy Task: fixture, visibility, protocol and evaluator for the teaching demo."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shutil
import sys
import subprocess

from contracts.evaluation import evaluation_result, metric
from contracts.visibility import Visibility
from mvp.evaluation.demo_eval import FIXTURE, TASK, evaluate


TOOLSET = "toy-v2"

VISIBILITY = Visibility(
    readable=("config.py", "client.py", "cli.py", "tests/test_visible.py"),
    editable=("config.py", "client.py", "cli.py"),
    protected=("tests/test_visible.py",),
)

PROTOCOL = {
    "max_actions": 30,
    "wall_time_seconds": 600,
    "test_timeout_seconds": 90,
}


@dataclass
class ToyTask:
    task_id: str = "toy-timeout"
    problem: str = TASK
    toolset: str = TOOLSET
    visibility: Visibility = VISIBILITY
    protocol: dict = field(default_factory=lambda: dict(PROTOCOL))
    run_prefix: str = "toyv2"
    max_steps: int = 60
    python: str = sys.executable
    git: str = "git"
    runs_root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[0] / "runs")

    def prepare(self, run_dir: Path) -> Path:
        workspace = Path(run_dir) / "workspace"
        shutil.copytree(FIXTURE, workspace,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        (workspace / ".gitignore").write_bytes(b"__pycache__/\n.pytest_cache/\n*.pyc\n")
        for command in (
            [self.git, "init", "-q"],
            [self.git, "config", "user.email", "toy@example.test"],
            [self.git, "config", "user.name", "toy-demo"],
            [self.git, "add", "-A"],
            [self.git, "commit", "-qm", "baseline"],
        ):
            subprocess.run(command, cwd=workspace, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return workspace

    def evaluate(self, run_dir: Path, workspace: Path, patch: Path | None = None) -> dict:
        raw = evaluate(workspace)
        execution_errors = raw.get("execution_errors") or []
        requirements = raw.get("requirements") or {}
        regressions = raw.get("regressions") or {}
        baseline = raw.get("baseline_regressions") or {}
        metrics = {
            "fail_to_pass": metric(
                "需求验收", requirements.get("passed", 0), requirements.get("total", 0),
                failed=requirements.get("failed"), status=requirements.get("status")),
            "pass_to_pass": metric(
                "回归检查", regressions.get("passed", 0), regressions.get("total", 0),
                failed=regressions.get("failed"), status=regressions.get("status")),
        }
        if baseline:
            metrics["baseline"] = metric(
                "修改前回归基准", baseline.get("passed", 0), baseline.get("total", 0),
                failed=baseline.get("failed"), status=baseline.get("status"))
        return evaluation_result(
            resolved=bool(raw.get("passed")),
            status="evaluation_error" if execution_errors else "completed",
            execution_valid=not execution_errors,
            metrics=metrics,
            new_regressions=raw.get("new_regressions") or [],
            errors=list(raw.get("environment_errors") or []) + list(execution_errors),
            cases=raw.get("cases") or [],
            details=raw,
        )
