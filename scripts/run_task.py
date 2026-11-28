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
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_core.agent import Action
from agent_core.llm import policy_from_env
from run import DEFAULT_MAX_ACTIONS, run
from tasks.registry import get_entry
from tools.toolsets import tool_names, validate_tool_names


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
        if entry.reference_policy is None:
            raise ValueError(f"task {entry.task_id!r} has no reference policy")
        return entry.reference_policy(task, _options(args)), None

    # llm: build lazily so llm_calls.jsonl lands inside the run directory.
    def factory(run_dir, task):
        retry_kwargs = {}
        if args.max_parse_attempts is not None:
            retry_kwargs["max_parse_attempts"] = args.max_parse_attempts
        if args.max_dsml_retries is not None:
            retry_kwargs["max_dsml_retries"] = args.max_dsml_retries
        if args.observation_limit is not None:
            retry_kwargs["observation_limit"] = args.observation_limit
        return policy_from_env(
            task.problem, args.model, toolset=task.toolset, tools=names,
            workspace_hint=getattr(task, "workspace_hint", None),
            log_path=None if args.no_llm_log else Path(run_dir) / "llm_calls.jsonl",
            budget_usd=args.budget_usd, max_total_tokens=args.max_total_tokens,
            **retry_kwargs)

    return None, factory


def main(argv=None):
    parser = argparse.ArgumentParser(description="Launch one agent run (CLI/portal shared).")
    parser.add_argument("--task", required=True)
    parser.add_argument("--toolset", help="Toolset name (default: the task's toolset)")
    parser.add_argument("--tools", help="Comma-separated tool subset (default: task default)")
    parser.add_argument("--policy", default="scripted")
    parser.add_argument("--model")
    parser.add_argument("--preflight", action="store_true", help="Check configuration and exit without a run")
    parser.add_argument("--max-steps", type=int, default=60)
    parser.add_argument("--max-actions", type=int, default=DEFAULT_MAX_ACTIONS)
    parser.add_argument("--budget-usd", type=float)
    parser.add_argument("--max-total-tokens", type=int)
    parser.add_argument("--max-parse-attempts", type=int)
    parser.add_argument("--max-dsml-retries", type=int)
    parser.add_argument("--observation-limit", type=int,
                        help="Max characters per observation in the model history")
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
        if args.preflight:
            if args.policy not in entry.policies:
                parser.error(f"policy {args.policy!r} is not valid for {entry.task_id!r}")
            checks = entry.preflight(_options(args))
            print(json.dumps({"task": entry.task_id, "checks": checks}))
            return 0 if checks and all(check["ok"] for check in checks) else 1
        toolset = args.toolset or entry.toolset
        if entry.locked_toolset and toolset != entry.toolset:
            parser.error(f"task {entry.task_id!r} requires toolset {entry.toolset!r}")
        allowed = tool_names(toolset)
        if args.tools:
            names = validate_tool_names(
                [t.strip() for t in args.tools.split(",") if t.strip()])
            extras = sorted(set(names) - set(allowed))
            if extras:
                parser.error(
                    f"tools not in toolset {toolset!r}: {', '.join(extras)}")
        else:
            names = list(allowed) if args.toolset else list(entry.default_tools)
        task = entry.build(_options(args))
        task.toolset = toolset
        policy, factory = _build_policy(args, entry, task, names)
        summary, _, _ = run(task, policy, tools=names, policy_factory=factory,
                            output_root=args.output_dir, max_steps=args.max_steps,
                            max_actions=args.max_actions)
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))
    return 0 if summary.get("resolved") else 1


if __name__ == "__main__":
    raise SystemExit(main())
