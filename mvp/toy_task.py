"""Toy Task: fixture, visibility, protocol and evaluator for the teaching demo."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shutil
import sys
import subprocess

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
    max_steps: int = 16
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
        result = evaluate(workspace)
        result["resolved"] = result["passed"]
        return result
