"""Generic task launcher: resolve a registry entry, build a policy, call run().

This is the single launch path shared by the CLI and the web portal, so there is
no second copy of the "which policy / which tools" logic. It prints the shared
``run.py`` progress to stdout (encoding-safe) and exits non-zero when the task is
not resolved.

Examples::

    python -m scripts.run_task --task toy --policy scripted
    python -m scripts.run_task --task toy --policy scripted \
        --tools list_files,read_lines,replace_text,run_visible_tests,finish
    python -m scripts.run_task --task sphinx-7462 --policy reference
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_core.agent import Action
from agent_core.llm import policy_from_env
from run import run
from tasks.registry import get_entry
from tools.toolsets import validate_tool_names


class EmptyPolicy:
    """Submits immediately; a free baseline that resolves nothing."""

    def next_action(self, history):
        return Action("finish", {"reason": "empty baseline candidate"})


def _options(args) -> dict:
    """Flatten argparse into the dict the registry build/preflight expect."""
    return {
        "policy": args.policy,
        "budget_usd": args.budget_usd,
        "data_dir": args.data_dir,
        "source_archive": args.source_archive,
        "record_path": args.record_path,
        "review_manifest": args.review_manifest,
        "reference_patch": args.reference_patch,
        "python": args.python,
        "git": args.git,
        "skip_env_probe": args.skip_env_probe,
    }


def _build_policy(args, entry, task, names):
    """Return (policy, policy_factory); exactly one is not None."""
    policy = args.policy
    if policy not in entry.policies:
        raise ValueError(
            f"policy {policy!r} is not valid for task {entry.task_id!r}; "
            f"choose from {', '.join(entry.policies)}")
    if policy == "scripted":
        from mvp.scripted_policy import ToyV2Policy
        return ToyV2Policy(), None
    if policy == "none":
        return EmptyPolicy(), None
    if policy == "reference":
        from tasks.registry import _sphinx_paths
        from experiments.sphinx_7462.oracle import ReferencePatchPolicy
        patch = _sphinx_paths(_options(args))["reference_patch"]
        return ReferencePatchPolicy(patch), None

    # llm: build lazily so llm_calls.jsonl lands inside the run directory.
    def factory(run_dir, task):
        return policy_from_env(
            task.problem, args.model, toolset=task.toolset, tools=names,
            log_path=None if args.no_llm_log else Path(run_dir) / "llm_calls.jsonl",
            budget_usd=args.budget_usd, max_total_tokens=args.max_total_tokens)

    return None, factory


def main(argv=None):
    parser = argparse.ArgumentParser(description="Launch one agent run (CLI/portal shared).")
    parser.add_argument("--task", required=True)
    parser.add_argument("--tools", help="Comma-separated tool subset (default: task default)")
    parser.add_argument("--policy", default="scripted")
    parser.add_argument("--model")
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--budget-usd", type=float)
    parser.add_argument("--max-total-tokens", type=int)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--no-llm-log", action="store_true")
    # Sphinx (and future data-backed tasks) path/interpreter overrides.
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--source-archive", type=Path)
    parser.add_argument("--record-path", type=Path)
    parser.add_argument("--review-manifest", type=Path)
    parser.add_argument("--reference-patch", type=Path)
    parser.add_argument("--python", type=Path)
    parser.add_argument("--git")
    parser.add_argument("--skip-env-probe", action="store_true")
    args = parser.parse_args(argv)

    try:
        entry = get_entry(args.task)
        names = (validate_tool_names([t.strip() for t in args.tools.split(",") if t.strip()])
                 if args.tools else list(entry.default_tools))
        task = entry.build(_options(args))
        policy, factory = _build_policy(args, entry, task, names)
        summary, _, _ = run(task, policy, tools=names, policy_factory=factory,
                            output_root=args.output_dir, max_steps=args.max_steps)
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))
    return 0 if summary.get("resolved") else 1


if __name__ == "__main__":
    raise SystemExit(main())
