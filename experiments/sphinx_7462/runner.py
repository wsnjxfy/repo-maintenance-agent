"""Run Sphinx 7462 through the shared run.py orchestrator."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent_core.agent import Action
from agent_core.llm import policy_from_env
from experiments.sphinx_7462.oracle import ReferencePatchPolicy
from experiments.sphinx_7462.task import SphinxTask
from run import run


class EmptyPolicy:
    def next_action(self, history):
        return Action("finish", {"reason": "empty baseline candidate"})


def build_task(args):
    return SphinxTask(
        source_archive=args.source_archive,
        record_path=args.record_path,
        review_manifest=args.review_manifest,
        python=args.python,
        git=args.git,
        skip_env_probe=args.skip_env_probe,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-archive", type=Path, required=True)
    parser.add_argument("--record-path", type=Path, required=True)
    parser.add_argument("--review-manifest", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--git", type=Path, required=True)
    parser.add_argument("--policy", choices=("reference", "none", "llm"),
                        default="reference")
    parser.add_argument("--reference-patch", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--budget-usd", type=float)
    parser.add_argument("--max-total-tokens", type=int)
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--output-dir", type=Path, default=Path("runs"))
    parser.add_argument("--no-llm-log", action="store_true")
    parser.add_argument("--skip-env-probe", action="store_true",
                        help="Synthetic fixtures only")
    args = parser.parse_args(argv)

    task = build_task(args)
    if args.policy == "reference":
        patch = args.reference_patch or (args.source_archive.parent / "reference.patch")
        policy = ReferencePatchPolicy(patch)
        factory = None
    elif args.policy == "none":
        policy = EmptyPolicy()
        factory = None
    else:
        policy = None

        def factory(run_dir, task):
            return policy_from_env(
                task.problem, args.model, toolset=task.toolset,
                log_path=None if args.no_llm_log else run_dir / "llm_calls.jsonl",
                budget_usd=args.budget_usd,
                max_total_tokens=args.max_total_tokens,
            )

    try:
        summary, _, _ = run(task, policy, policy_factory=factory,
                            output_root=args.output_dir, max_steps=args.max_steps)
    except (RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    return 0 if summary["resolved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
