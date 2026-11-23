"""Thin CLI for the teaching demo: ToyTask + scripted policy over run.py."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_core.llm import policy_from_env
from mvp.scripted_policy import ToyV2Policy
from mvp.toy_task import ToyTask
from run import run as run_task


def run(output_root=None, max_steps=None, policy="scripted",
        model=None, log_llm=True, transport=None):
    task = ToyTask()
    if policy == "scripted":
        return run_task(task, ToyV2Policy(), output_root=output_root, max_steps=max_steps)
    if policy != "llm":
        raise ValueError(f"unknown policy: {policy!r}")

    def factory(run_dir, task):
        return policy_from_env(
            task.problem, model,
            toolset=task.toolset,
            transport=transport,
            log_path=(Path(run_dir) / "llm_calls.jsonl") if log_llm else None,
        )

    return run_task(task, policy_factory=factory,
                    output_root=output_root, max_steps=max_steps)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
    parser = argparse.ArgumentParser(
        description="HJ10 teaching demo: scripted or LLM policy over the shared tool stack.")
    parser.add_argument("--max-steps", type=int, default=16,
                        help="Decision step budget (default: 16)")
    parser.add_argument("--output-dir", type=Path, help="Parent directory for a new run")
    parser.add_argument("--open", action="store_true", help="Open the generated report")
    parser.add_argument("--policy", choices=("scripted", "llm"), default="scripted",
                        help="Policy to drive the loop (default: scripted)")
    parser.add_argument("--model", help="Model name for --policy llm (default: LLM_MODEL from .env)")
    parser.add_argument("--no-llm-log", action="store_true",
                        help="Do not write llm_calls.jsonl in LLM mode")
    args = parser.parse_args(argv)
    if args.max_steps < 1:
        parser.error("--max-steps must be positive")
    try:
        summary, _, run_dir = run(args.output_dir, args.max_steps, policy=args.policy,
                                  model=args.model, log_llm=not args.no_llm_log)
    except RuntimeError as exc:
        parser.error(str(exc))
    if args.open:
        webbrowser.open((run_dir / "report.html").as_uri())
    return 0 if summary["status"] == "completed" and summary["evaluation"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
