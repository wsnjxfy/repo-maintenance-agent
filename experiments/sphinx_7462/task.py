"""Sphinx 7462 Task: prepared workspace, visibility and independent evaluation."""

from __future__ import annotations

from pathlib import Path
import json
import os

from contracts.evaluation import evaluation_result, metric, metric_status
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
        grading = result.get("grading") or {}
        f2p_passed = int(grading.get("f2p_passed", 0) or 0)
        f2p_total = int(grading.get("f2p_total", 0) or 0)
        p2p_passed = int(grading.get("p2p_passed", 0) or 0)
        p2p_total = int(grading.get("p2p_total", 0) or 0)
        failures = grading.get("failures") or []
        failure_by_name = {item.get("nodeid"): item for item in failures}
        cases = []
        for name, status in (grading.get("actual_statuses") or {}).items():
            case = {"name": name, "status": status}
            failure = failure_by_name.get(name)
            if failure:
                case["detail"] = failure.get("message") or failure.get("detail") or ""
            cases.append(case)
        if not cases:
            cases = [{"name": item.get("nodeid"), "status": item.get("status"),
                      "detail": item.get("message") or item.get("detail") or ""}
                     for item in failures]
        new_regressions = [item.get("label") for item in grading.get("p2p_mapping", [])
                           if not item.get("passed")]
        metrics = {
            "fail_to_pass": metric("F2P（需求验收）", f2p_passed, f2p_total,
                                   status=metric_status(f2p_passed, f2p_total)),
            "pass_to_pass": metric("P2P（回归检查）", p2p_passed, p2p_total,
                                   status=metric_status(p2p_passed, p2p_total)),
        }
        return evaluation_result(
            resolved=bool(result["resolved"]),
            status=result["status"],
            execution_valid=bool(grading.get("execution_valid",
                                             result["status"] == "completed")),
            metrics=metrics,
            new_regressions=new_regressions,
            errors=list(grading.get("validation_errors")
                        or result.get("validation_errors") or []),
            cases=cases,
            details=result,
        )
