"""Sphinx 7462 Task: prepared workspace, visibility and independent evaluation."""

from __future__ import annotations

from pathlib import Path
import json
import os

from contracts.visibility import Visibility
from experiments.sphinx_7462 import evaluate as evaluate_module
from experiments.sphinx_7462.evaluate import _validated_record
from experiments.sphinx_7462.prepare import (
    BASE_COMMIT, REPOSITORY, TASK_ID, prepare_workspace, protected_source,
    protocol as task_protocol,
)


class SphinxTask:
    task_id = TASK_ID
    toolset = "repo-v1"
    run_prefix = "sphinx"
    max_steps = 30

    def __init__(self, source_archive, record_path, review_manifest,
                 python, git, skip_env_probe=False):
        self.source_archive = Path(source_archive)
        self.record_path = Path(record_path)
        self.review_manifest = Path(review_manifest)
        self.python = Path(python)
        self.git = Path(git)
        self.skip_env_probe = skip_env_probe
        self.protocol = task_protocol()
        self.record = json.loads(self.record_path.read_text(encoding="utf-8"))
        self.review = json.loads(self.review_manifest.read_text(encoding="utf-8"))
        if (self.record.get("instance_id"), self.record.get("repo"),
                self.record.get("base_commit")) != (TASK_ID, REPOSITORY, BASE_COMMIT):
            raise ValueError("record does not match the selected Sphinx instance")
        labels, test_patch = _validated_record(self.record, self.review)
        self.labels = labels
        self.test_patch = test_patch
        self.problem = self.record["problem_statement"]
        self.initial = {}
        self.probe = {}
        self.visibility = Visibility(readable=("**",), editable=("**",), protected=())

    def prepare(self, run_dir):
        run_dir = Path(run_dir)
        workspace = run_dir / "workspace"
        baseline = run_dir / "baseline"
        initial, probe = prepare_workspace(
            self.source_archive, workspace, baseline, self.python, self.git,
            skip_env_probe=self.skip_env_probe)
        self.initial = initial
        self.probe = probe
        protected = tuple(sorted(path for path in initial if protected_source(path)))
        self.visibility = Visibility(readable=("**",), editable=("**",),
                                     protected=protected)
        return workspace

    def evaluate(self, run_dir, workspace, patch):
        run_dir = Path(run_dir).resolve()
        output_dir = run_dir / "evaluation"
        temp_dir = run_dir / "tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        previous = {name: os.environ.get(name) for name in ("TEMP", "TMP")}
        os.environ["TEMP"] = str(temp_dir)
        os.environ["TMP"] = str(temp_dir)
        try:
            result = evaluate_module.evaluate(
                source_archive=self.source_archive,
                record_path=self.record_path,
                review_manifest=self.review_manifest,
                output_dir=output_dir,
                python=self.python,
                git=self.git,
                candidate_patch=patch,
                skip_env_probe=self.skip_env_probe,
            )
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        return {
            "resolved": bool(result["resolved"]),
            "status": result["status"],
            "grading": result.get("grading"),
            "details": result,
        }
