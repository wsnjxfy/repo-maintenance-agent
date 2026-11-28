"""Public recursive lint behavior from Pylint issue #6964."""

from pathlib import Path

from pylint.lint import Run
from pylint.reporters.collecting_reporter import CollectingReporter


def linted_paths(tmp_path, monkeypatch, *, ignore_generated):
    root = tmp_path.resolve()
    generated = root / "src" / "gen"
    generated.mkdir(parents=True)
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (generated / "generated.py").write_text("VALUE = 2\n", encoding="utf-8")
    config = "[MASTER]\n"
    if ignore_generated:
        config += "ignore-paths=^src/gen/.*$\n"
    (root / "sample.pylintrc").write_text(config, encoding="utf-8")
    monkeypatch.chdir(root)
    reporter = CollectingReporter()
    Run(
        [
            "--rcfile=sample.pylintrc",
            "--recursive=y",
            "--persistent=n",
            "--disable=all",
            "--enable=missing-module-docstring",
            ".",
        ],
        reporter=reporter,
        exit=False,
    )
    return {
        Path(message.path).resolve().relative_to(root).as_posix()
        for message in reporter.messages if message.symbol == "missing-module-docstring"
    }


def test_requirement(tmp_path, monkeypatch):
    assert linted_paths(tmp_path, monkeypatch, ignore_generated=True) == {"src/app.py"}


def test_regression(tmp_path, monkeypatch):
    assert linted_paths(tmp_path, monkeypatch, ignore_generated=False) == {
        "src/app.py",
        "src/gen/generated.py",
    }
