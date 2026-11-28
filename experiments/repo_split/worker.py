"""Control-side worker. Select only frozen test labels and report exact outcomes."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import re
import sys
import traceback
import unittest


def _origin(name, source):
    module = importlib.import_module(name)
    location = module.__file__ or next(iter(module.__path__))
    origin = Path(location).resolve()
    expected = source / name
    if origin != expected.with_suffix(".py") and not origin.is_relative_to(expected):
        raise RuntimeError("Test package was not imported from the evaluated checkout: " + name)
    return str(origin)


def _normalise(name):
    return " ".join(name.split())


def _match(label, available):
    if label in available:
        return [label]
    canonical = _normalise(label)
    equivalents = [node for node in available if _normalise(node) == canonical]
    if equivalents or label.count("[") <= label.count("]"):
        return equivalents
    return [node for node in available if _normalise(node).startswith(canonical)]


def _pytest(labels, workspace, outcomes, errors):
    import pytest

    class Selection:
        def pytest_collection_modifyitems(self, session, config, items):
            by_id = {item.nodeid: item for item in items}
            if len(items) != len(by_id):
                raise ValueError("Duplicate pytest node IDs")
            chosen = set()
            for label in labels:
                matches = _match(label, by_id)
                if not matches or chosen.intersection(matches):
                    raise ValueError("Frozen pytest label has no unique selection: " + label)
                chosen.update(matches)
            discarded = [item for item in items if item.nodeid not in chosen]
            config.hook.pytest_deselected(items=discarded)
            items[:] = [item for item in items if item.nodeid in chosen]

        def pytest_runtest_logreport(self, report):
            node, stage = report.nodeid, report.when
            if report.skipped or hasattr(report, "wasxfail"):
                errors.append("Skipped or expected-failure outcome: " + node)
            elif report.failed and stage != "call":
                errors.append("Pytest fixture failure: " + node)
            elif stage == "call":
                if node in outcomes:
                    errors.append("Duplicate pytest execution: " + node)
                outcomes[node] = "PASSED" if report.passed else "FAILED"

    paths = set()
    for label in labels:
        rel, separator, _ = label.partition("::")
        path = (workspace / rel).resolve()
        if not separator or not path.is_relative_to(workspace) or not path.is_file():
            raise ValueError("Invalid frozen pytest test file")
        paths.add(rel)
    return int(pytest.main(["-q", "--color=no", "-p", "no:cacheprovider",
                            "--rootdir", str(workspace), *sorted(paths)], plugins=[Selection()]))


def _django(labels, workspace, outcomes, errors):
    from unittest.mock import patch
    from django.test.runner import DiscoverRunner

    os.environ["DJANGO_SETTINGS_MODULE"] = "test_sqlite"
    sys.path.insert(0, str(workspace / "tests"))
    import runtests

    expected = {}
    for label in labels:
        match = re.fullmatch(r"(\w+) \(([\w.]+)\)", label)
        if not match:
            raise ValueError("Invalid frozen Django test label")
        name, parent = match.groups()
        test_id = parent if parent.endswith("." + name) else parent + "." + name
        if test_id in expected:
            raise ValueError("Duplicate frozen Django test ID")
        expected[test_id] = label

    class Result(unittest.TextTestResult):
        def _capture(self, test, status):
            name = expected.get(test.id())
            if not name or name in outcomes or status not in {"PASSED", "FAILED"}:
                errors.append("Unexpected Django result: " + test.id())
            else:
                outcomes[name] = status

        def addSuccess(self, test):
            self._capture(test, "PASSED")
            super().addSuccess(test)

        def addFailure(self, test, err):
            self._capture(test, "FAILED")
            super().addFailure(test, err)

        def addError(self, test, err):
            self._capture(test, "ERROR")
            super().addError(test, err)

        def addSkip(self, test, reason):
            self._capture(test, "SKIPPED")
            super().addSkip(test, reason)

    with patch.object(DiscoverRunner, "get_resultclass", return_value=Result):
        failures = runtests.django_tests(
            verbosity=1, interactive=False, failfast=False, keepdb=False, reverse=False,
            test_labels=list(expected), debug_sql=False, parallel=1, tags=None, exclude_tags=None,
            test_name_patterns=None, start_at=None, start_after=None, pdb=False,
        )
    return int(bool(failures))


def _sympy(labels, workspace, official_files, outcomes, errors):
    from sympy.testing.runtests import PyTestReporter, SymPyTests

    expected = set(labels)
    if any(not name.isidentifier() for name in expected):
        raise ValueError("Invalid frozen SymPy function label")

    class Reporter(PyTestReporter):
        current = None

        def entering_test(self, fn):
            self.current = fn.__name__
            super().entering_test(fn)

        def record(self, status):
            if self.current not in expected or self.current in outcomes or status not in {"PASSED", "FAILED"}:
                errors.append("Unexpected SymPy outcome")
            else:
                outcomes[self.current] = status

        def test_pass(self):
            self.record("PASSED")
            super().test_pass()

        def test_fail(self, exc_info):
            self.record("FAILED")
            super().test_fail(exc_info)

        def test_skip(self, reason):
            self.record("SKIPPED")
            super().test_skip(reason)

        def test_exception(self, exc_info):
            self.record("ERROR")
            super().test_exception(exc_info)

    class Selection(SymPyTests):
        def matches(self, fn):
            return fn.__name__ in expected

    reporter = Reporter(colors=False)
    selected = Selection(reporter, kw=tuple(labels), seed=1)
    files = [(workspace / name).resolve() for name in official_files]
    if not files or any(not f.is_relative_to(workspace) or not f.is_file() for f in files):
        raise ValueError("Invalid frozen SymPy test file")
    selected._testfiles = [str(path) for path in files]
    return int(not selected.test(sort=True))


def main(request_path, evidence_path):
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    source = Path(request["source"]).resolve()
    workspace = Path.cwd().resolve()
    package = request["package"]
    outcomes, errors, imports = {}, [], {}
    labels = request["labels"]["FAIL_TO_PASS"] + request["labels"]["PASS_TO_PASS"]
    sys.path.insert(0, str(source))
    # Keep handles alive while loading native support files on Windows.
    dll_paths = [
        os.add_dll_directory(str(directory)) for directory in source.glob("*.libs")
        if directory.is_dir()
    ] if hasattr(os, "add_dll_directory") else []
    code = 2
    try:
        packages = ("pytest", "_pytest") if package == "pytest" else (package,)
        imports = {name: _origin(name, source) for name in packages}
        if package == "django":
            code = _django(labels, workspace, outcomes, errors)
        elif package == "sympy":
            code = _sympy(labels, workspace, request["official_files"], outcomes, errors)
        else:
            code = _pytest(labels, workspace, outcomes, errors)
        if {name: _origin(name, source) for name in packages} != imports:
            errors.append("Package import origins changed during evaluation")
    except (Exception, SystemExit) as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    finally:
        for handle in dll_paths:
            handle.close()
    Path(evidence_path).write_text(json.dumps({"workspace": str(workspace), "imports": imports,
                                               "outcomes": outcomes, "errors": errors}), encoding="utf-8")
    return 2 if errors else code


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
