"""Evaluate trusted controller checks under an explicitly selected Python."""

import importlib
import json
from pathlib import Path
import sys


def _verify(source, package):
    names = ("pytest", "_pytest") if package == "pytest" else (package,)
    modules = {}
    for name in names:
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve(strict=True)
        if not path.is_relative_to(source / name):
            raise RuntimeError(f"{name} was not imported from the evaluated repository")
        modules[name] = str(path)
    return modules


def main():
    source, package, check_file, config, junit, evidence = sys.argv[1:]
    source = Path(source).resolve(strict=True)
    sys.path.insert(0, str(source))
    before = _verify(source, package)
    import pytest

    code = pytest.main(["-q", "-c", config, "--noconftest", "-p", "no:cacheprovider",
                        "--import-mode=importlib", "--junitxml", junit, check_file])
    if _verify(source, package) != before:
        raise RuntimeError("Evaluated package locations changed during the checks")
    Path(evidence).write_text(json.dumps(before) + "\n", encoding="utf-8")
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
