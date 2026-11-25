"""Task registry: one entry per runnable task, shared by CLI and web portal.

Each entry knows how to *build* its Task object, which tools and policies make
sense for it, and how to *preflight* the environment (data files, interpreter,
API keys) before a run is launched. Adding a new sample = adding one entry here;
the portal's task dropdown and tool grid grow automatically.

This module sits at the orchestration layer (a peer of ``run.py``): it may import
across the task packages, which the restricted layers may not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Callable

from agent_core.llm import ENV_PATH, load_env
from tools.toolsets import tool_names

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Check:
    """One preflight line item rendered as a green/red badge in the UI."""

    name: str
    ok: bool
    detail: str = ""

    def asdict(self):
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class TaskEntry:
    task_id: str
    title: str
    toolset: str
    default_tools: list
    policies: list
    build: Callable
    preflight: Callable
    edit_tools: tuple = ("replace_text", "apply_patch")
    notes: str = ""

    def asdict(self):
        return {
            "task_id": self.task_id,
            "title": self.title,
            "toolset": self.toolset,
            "default_tools": list(self.default_tools),
            "policies": list(self.policies),
            "notes": self.notes,
        }


# --- shared preflight helpers -----------------------------------------------

def _python_has_pytest(python) -> Check:
    try:
        result = subprocess.run([str(python), "-c", "import pytest"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return Check("python 可运行 pytest", False, f"{type(exc).__name__}: {exc}")
    ok = result.returncode == 0
    detail = str(python) if ok else result.stdout.decode("utf-8", "replace")[:300]
    return Check("python 可运行 pytest", ok, detail)


def _git_value(options) -> str:
    """Return an executable path when possible; fall back to the command name."""
    return str(options.get("git") or shutil.which("git") or "git")


def _git_check(options) -> Check:
    value = _git_value(options)
    found = shutil.which(value) or (value if Path(value).is_file() else None)
    return Check("git 可执行", bool(found), str(found or value))


def _env_keys_present(require_prices: bool) -> list:
    load_env(ENV_PATH)
    import os
    checks = []
    for key in ("LLM_API_KEY", "LLM_MODEL"):
        present = bool(os.environ.get(key))
        checks.append(Check(f".env {key}", present, "已设置" if present else "缺失"))
    if require_prices:
        for key in ("LLM_PRICE_INPUT", "LLM_PRICE_OUTPUT"):
            value = os.environ.get(key)
            ok = value not in (None, "", "0")
            checks.append(Check(f".env {key}", ok,
                                "预算模式需要正价格" if not ok else value))
    return checks


# --- toy --------------------------------------------------------------------

def _build_toy(options):
    from mvp.toy_task import ToyTask
    return ToyTask(git=_git_value(options))


def _preflight_toy(options):
    from mvp.evaluation.demo_eval import FIXTURE
    checks = [_git_check(options),
              _python_has_pytest(sys.executable),
              Check("测试样例目录", FIXTURE.is_dir(), str(FIXTURE))]
    if options.get("policy") == "llm":
        checks += _env_keys_present(require_prices=bool(options.get("budget_usd")))
    return [c.asdict() for c in checks]


# --- sphinx-7462 ------------------------------------------------------------

DEFAULT_SPHINX_DATA = REPO_ROOT / "data" / "sphinx-doc__sphinx-7462"
DEFAULT_VENV_PYTHON = REPO_ROOT / ".venv39" / "bin" / "python"


def _sphinx_paths(options):
    data_dir = Path(options.get("data_dir") or DEFAULT_SPHINX_DATA)
    return {
        "source_archive": Path(options.get("source_archive") or data_dir / "sphinx-base.tar.gz"),
        "record_path": Path(options.get("record_path") or data_dir / "record.json"),
        "review_manifest": Path(options.get("review_manifest") or data_dir / "review.json"),
        "reference_patch": Path(options.get("reference_patch") or data_dir / "reference.patch"),
        "python": Path(options.get("python") or DEFAULT_VENV_PYTHON),
        "git": _git_value(options),
    }


def _build_sphinx(options):
    from experiments.sphinx_7462.task import SphinxTask
    paths = _sphinx_paths(options)
    return SphinxTask(
        source_archive=paths["source_archive"],
        record_path=paths["record_path"],
        review_manifest=paths["review_manifest"],
        python=paths["python"],
        git=paths["git"],
        skip_env_probe=bool(options.get("skip_env_probe")),
    )


def _preflight_sphinx(options):
    paths = _sphinx_paths(options)
    checks = [
        Check("sphinx-base.tar.gz", paths["source_archive"].is_file(),
              str(paths["source_archive"])),
        Check("record.json", paths["record_path"].is_file(), str(paths["record_path"])),
        Check("review.json", paths["review_manifest"].is_file(),
              str(paths["review_manifest"])),
        Check("reference.patch", paths["reference_patch"].is_file(),
              str(paths["reference_patch"])),
        _git_check(options),
        _python_has_pytest(paths["python"]),
    ]
    if options.get("policy") == "llm":
        checks += _env_keys_present(require_prices=bool(options.get("budget_usd")))
    return [c.asdict() for c in checks]


# --- the catalog ------------------------------------------------------------

TASKS = {
    "toy": TaskEntry(
        task_id="toy",
        title="玩具样例（timeout demo）",
        toolset="toy-v2",
        default_tools=tool_names("toy-v2"),
        policies=["scripted", "llm"],
        build=_build_toy,
        preflight=_preflight_toy,
        notes="固定夹具，免 API key 的 scripted 策略可直接跑通。",
    ),
    "sphinx-7462": TaskEntry(
        task_id="sphinx-7462",
        title="Sphinx 7462",
        toolset="repo-v1",
        default_tools=tool_names("repo-v1"),
        policies=["reference", "none", "llm"],
        build=_build_sphinx,
        preflight=_preflight_sphinx,
        notes="需要 data/sphinx-doc__sphinx-7462/ 四个档与 py3.9 venv；先用 reference 冒烟。",
    ),
}


def get_entry(task_id):
    try:
        return TASKS[task_id]
    except KeyError:
        raise ValueError(
            "Unknown task: {!r}. Available: {}".format(
                task_id, ", ".join(sorted(TASKS)))) from None
