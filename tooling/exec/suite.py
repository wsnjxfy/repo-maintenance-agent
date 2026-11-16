"""Run a trusted test suite in a fresh process and return structured JSON.

The agent never chooses an arbitrary command or a hidden test file. This helper
is process separation, not an operating-system sandbox for untrusted code.
"""

import json
from pathlib import Path
import subprocess
import sys


RUNNER = Path(__file__).with_name("unittest_runner.py")


def run_suite(workspace, test_file, class_name=None, timeout=15):
    command = [sys.executable, "-I", "-B", str(RUNNER),
               "--workspace", str(Path(workspace).resolve()),
               "--test-file", str(Path(test_file).resolve())]
    if class_name:
        command.extend(["--class-name", class_name])
    try:
        completed = subprocess.run(command, cwd=workspace, capture_output=True,
                                   text=True, encoding="utf-8", timeout=timeout)
        result = json.loads(completed.stdout)
        if completed.returncode not in (0, 1, 2) or not isinstance(result, dict):
            raise ValueError("Unexpected test runner result")
        return result
    except subprocess.TimeoutExpired:
        return {"status": "execution_timeout", "total": 0, "passed": 0,
                "failed": 0, "errors": 1, "cases": [], "output": "",
                "error": f"Test process exceeded {timeout} seconds; cause requires inspection"}
    except (OSError, ValueError) as exc:
        message = f"Test runner error: {exc}"
    return {"status": "environment_error", "total": 0, "passed": 0,
            "failed": 0, "errors": 1, "cases": [], "output": "", "error": message}
