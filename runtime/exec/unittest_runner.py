"""Run a trusted toy test suite in a fresh process and return structured JSON.

The agent never chooses an arbitrary command or a hidden test file. This runner
is process separation, not an operating-system sandbox for untrusted code.
"""

import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []
        self._records = {}

    def _record(self, test, status, detail=""):
        name = test.id()
        if name not in self._records:
            record = {"name": name, "status": status, "detail": detail}
            self._records[name] = record
            self.cases.append(record)
        elif status != "passed":
            record = self._records[name]
            record["status"] = status
            record["detail"] += "\n" + detail

    def addSuccess(self, test):
        super().addSuccess(test)
        self._record(test, "passed")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._record(test, "failed", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self._record(test, "error", self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._record(test, "skipped", reason)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._record(test, "expected_failure", self._exc_info_to_string(err, test))

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._record(test, "unexpected_success", "Unexpected success")

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            status = "failed" if issubclass(err[0], test.failureException) else "error"
            self._record(test, status, str(subtest) + "\n" + self._exc_info_to_string(err, test))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--test-file", required=True, type=Path)
    parser.add_argument("--class-name")
    args = parser.parse_args()
    output = io.StringIO()
    try:
        sys.path.insert(0, str(args.workspace.resolve()))
        spec = importlib.util.spec_from_file_location("task_tests", args.test_file.resolve())
        if spec is None or spec.loader is None:
            raise ImportError("Cannot load test suite")
        module = importlib.util.module_from_spec(spec)
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            spec.loader.exec_module(module)
            target = getattr(module, args.class_name) if args.class_name else module
            loader = unittest.defaultTestLoader
            suite = (loader.loadTestsFromTestCase(target) if args.class_name
                     else loader.loadTestsFromModule(target))
            result = unittest.TextTestRunner(
                stream=output, verbosity=2, resultclass=RecordedResult
            ).run(suite)
        if result.testsRun == 0:
            raise RuntimeError("No tests were discovered")
        passed = sum(case["status"] == "passed" for case in result.cases)
        all_passed = result.wasSuccessful() and passed == result.testsRun
        payload = {
            "status": "passed" if all_passed else "failed",
            "total": result.testsRun,
            "passed": passed,
            "failed": sum(case["status"] == "failed" for case in result.cases),
            "errors": sum(case["status"] == "error" for case in result.cases),
            "skipped": len(result.skipped),
            "cases": result.cases, "output": output.getvalue(),
        }
        code = 0 if all_passed else 1
    except Exception as exc:
        # Import and execution errors can be caused by a patch OR an environment.
        # Keep their cause unresolved instead of assigning blame automatically.
        status = "environment_error" if isinstance(exc, OSError) else "test_load_error"
        payload = {"status": status, "total": 0, "passed": 0,
                   "failed": 0, "errors": 1, "cases": [],
                   "output": output.getvalue(), "error": f"{type(exc).__name__}: {exc}"}
        code = 2
    print(json.dumps(payload, ensure_ascii=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
