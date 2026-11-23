"""Backward-compatible CLI: python mvp/run_demo.py ..."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mvp.demo import main


if __name__ == "__main__":
    raise SystemExit(main())
