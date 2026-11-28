"""Apply frozen benchmark tests only after the agent has finished."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tarfile

from contracts.evaluation import evaluation_result, metric, metric_status
from experiments.sphinx_7462.evaluate import _Evaluation
from experiments.sphinx_7462.grading import resolve_label
from runtime.fsguard import changes, files, is_link, safe_path, sha256_bytes, write_json
from runtime.process import process, worker_env


GROUPS = {
    "FAIL_TO_PASS": ("fail_to_pass", "需求检查"),
    "PASS_TO_PASS": ("pass_to_pass", "回归检查"),
}
MAX_PATCH_BYTES = 2_000_000


def _read_control_file(path):
    if is_link(path) or not path.is_file() or path.stat().st_size > MAX_PATCH_BYTES:
        raise ValueError("Unsafe or oversized evaluator input file")
    return path.read_bytes()


def _control_material(task):
    directory = Path(task.control_dir)
    evidence = json.loads(_read_control_file(directory / "evaluation.json"))
    identity = ("instance_id", "repo", "base_commit")
    if [evidence.get(key) for key in identity] != [getattr(task.spec, key) for key in identity]:
        raise ValueError("Evaluation record does not match the frozen repository and baseline")
    labels = {}
    for key, count in (("FAIL_TO_PASS", task.spec.f2p_count), ("PASS_TO_PASS", task.spec.p2p_count)):
        values = evidence.get(key)
        if (not isinstance(values, list) or not values or len(values) != count
                or any(not isinstance(value, str) or not value.strip() for value in values)
                or len(values) != len(set(values))):
            raise ValueError("Invalid frozen evaluation labels for " + key)
        labels[key] = values
    if set(labels["FAIL_TO_PASS"]) & set(labels["PASS_TO_PASS"]):
        raise ValueError("Requirement and regression labels overlap")
    official = _read_control_file(directory / "test.patch")
    pin = evidence["files"]["test.patch"]
    if not official or pin != {"bytes": len(official), "sha256": sha256_bytes(official)}:
        raise ValueError("Official test patch does not match its frozen pin")
    return labels, official


def _worker_evidence(evidence_path, workspace, expected_modules):
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    imports = evidence["imports"]
    if set(imports) != set(expected_modules):
        raise ValueError("Missing provenance for the evaluated package")
    for package, origin in imports.items():
        source = expected_modules[package]
        actual = Path(origin).resolve(strict=True)
        if actual != source.with_suffix(".py") and not actual.is_relative_to(source):
            raise ValueError("The required package was not imported from the evaluated copy")
    outcomes = evidence["outcomes"]
    if (evidence["errors"] or not isinstance(outcomes, dict) or not outcomes
            or any(value not in ("PASSED", "FAILED") for value in outcomes.values())):
        raise ValueError("The worker did not execute all selected cases to completion")
    if Path(evidence["workspace"]).resolve(strict=True) != workspace:
        raise ValueError("Test worker reported a different checked-out workspace")
    return outcomes, imports


def _grade(labels, outcomes, returncode):
    if returncode != int("FAILED" in outcomes.values()):
        raise ValueError("Worker exit code and recorded outcomes disagree")
    claimed = set()
    scored = {}
    for group, values in labels.items():
        rows = [resolve_label(value, outcomes) for value in values]
        for row in rows:
            matches = set(row["matches"])
            if not matches or matches & claimed:
                raise ValueError("A frozen label is missing or matches cases claimed by another label")
            claimed.update(matches)
        scored[group] = rows
    if claimed != set(outcomes):
        raise ValueError("Worker executed cases outside the frozen evaluation selection")
    return scored


def evaluate_repo_split(task, output_dir, patch):
    """Evaluate one frozen candidate in a separate host workspace.

    Frozen benchmark labels are checked with local adapters. This execution
    produces local grading evidence rather than an official benchmark result.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    output_dir = output_dir.resolve()
    workspace = output_dir / "workspace"
    details = {
        "scope": "local_frozen_benchmark_checks",
        "instance_id": task.spec.instance_id,
        "base_commit": task.spec.base_commit,
        "official_harness": False,
        "native_wheel_artifacts": task.spec.native,
    }
    runner = None
    stage = "prepare"
    errors = []
    scored = {}
    try:
        labels, official = _control_material(task)
        details["source_archive_sha256"] = task.check_archive()
        task.extract_archive(workspace)
        runner = _Evaluation(output_dir, workspace, task.python, task.git)
        runner.git_command("git_init", ["-c", "init.templateDir=", "init", "-q"])

        stage = "candidate_patch"
        baseline = files(workspace)
        candidate = _read_control_file(Path(patch)) if patch is not None else b""
        details["candidate_patch"] = runner.apply("candidate", candidate)
        details["candidate_changes"] = sorted(changes(baseline, files(workspace, baseline)))

        stage = "official_tests"
        official_record = runner.apply("official", official)
        details["test_patch_sha256"] = official_record["sha256"]
        task.prepare_runtime(workspace)
        expected = files(workspace, baseline)

        stage = "run_checks"
        source = safe_path(workspace, task.spec.source_dir, allow_dot=True)
        name = task.spec.package
        if not name.isidentifier():
            raise ValueError("Invalid source package")
        package_names = ("pytest", "_pytest") if name == "pytest" else (name,)
        modules = {package: safe_path(source, package) for package in package_names}
        request = runner.outputs / "worker-input.json"
        evidence_path = runner.outputs / "worker-evidence.json"
        write_json(request, {
            "source": str(source),
            "package": name,
            "labels": labels,
            "official_files": official_record["paths"],
        })
        stdout, stderr = runner.outputs / "test.stdout.log", runner.outputs / "test.stderr.log"
        temp = runner.outputs / "tmp"
        temp.mkdir()
        env = worker_env(workspace, task.python)
        env.update(
            TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp),
            HOME=str(temp), USERPROFILE=str(temp), MPLCONFIGDIR=str(temp),
            XDG_CACHE_HOME=str(temp),
        )
        command = [str(task.python), "-B", str(Path(__file__).with_name("worker.py")),
                   str(request), str(evidence_path)]
        run = process(command, workspace, env, stdout, stderr,
                      task.protocol["evaluation_timeout_seconds"])
        details["process"] = {**run, "stdout": str(stdout), "stderr": str(stderr)}
        if run["timed_out"] or run["returncode"] not in (0, 1):
            raise ValueError("Frozen tests did not complete; see worker stderr")
        if files(workspace, expected) != expected:
            raise ValueError("Test execution modified the checked-out source")

        stage = "grading"
        outcomes, origins = _worker_evidence(evidence_path, workspace, modules)
        details["imports"] = origins
        scored = _grade(labels, outcomes, run["returncode"])
    except (OSError, ValueError, TypeError, KeyError, RuntimeError,
            subprocess.SubprocessError, tarfile.TarError) as exc:
        details["failure_stage"] = stage
        errors.append(f"{type(exc).__name__}: {exc}")

    valid = not errors
    metrics = {}
    cases = []
    new_regressions = []
    for group, count in (("FAIL_TO_PASS", task.spec.f2p_count), ("PASS_TO_PASS", task.spec.p2p_count)):
        key, label = GROUPS[group]
        rows = scored.get(group, ()) if valid else ()
        passed = sum(row["passed"] for row in rows)
        metrics[key] = metric(label, passed, count,
                              status=metric_status(passed, count) if valid else "unknown")
        cases.extend({"name": row["label"], "status": "PASSED" if row["passed"] else "FAILED",
                      "detail": ""} for row in rows)
        if valid and group == "PASS_TO_PASS":
            new_regressions.extend(row["label"] for row in rows if not row["passed"])

    details["events"] = runner.events if runner else []
    result = evaluation_result(
        resolved=valid and all(value["status"] == "passed" for value in metrics.values()),
        status="completed" if valid else "evaluation_error",
        execution_valid=valid, metrics=metrics, errors=errors, cases=cases,
        new_regressions=new_regressions, details=details,
    )
    write_json(output_dir / "evaluation.json", result)
    return result
