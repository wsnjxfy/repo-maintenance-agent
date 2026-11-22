"""Backward-compatible CLI for the teaching demo.

The real implementation lives in ``mvp/demo.py`` (LocalEnvironment + Session +
tools/handlers). This wrapper keeps ``python mvp/run_demo.py`` working.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from demo import run


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
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
