"""Thin CLI for the teaching demo: ToyTask + scripted policy over run.py."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mvp.scripted_policy import ToyV2Policy
from mvp.toy_task import ToyTask
from run import run as run_task


def run(output_root=None, max_steps=None):
    return run_task(ToyTask(), ToyV2Policy(), output_root=output_root, max_steps=max_steps)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass
    parser = argparse.ArgumentParser(
        description="HJ10 teaching demo: scripted policy over the shared tool stack.")
    parser.add_argument("--max-steps", type=int, default=16,
                        help="Decision step budget (default: 16)")
    parser.add_argument("--output-dir", type=Path, help="Parent directory for a new run")
    parser.add_argument("--open", action="store_true", help="Open the generated report")
    args = parser.parse_args(argv)
    if args.max_steps < 1:
        parser.error("--max-steps must be positive")
    summary, _, run_dir = run(args.output_dir, args.max_steps)
    if args.open:
        webbrowser.open((run_dir / "report.html").as_uri())
    return 0 if summary["status"] == "completed" and summary["evaluation"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
