"""Evaluate the two public behavior checks on an independent source copy."""

from __future__ import annotations

import json
from pathlib import Path
import xml.etree.ElementTree as ET

from contracts.evaluation import evaluation_result, metric, metric_status
from experiments.sphinx_7462.evaluate import _Evaluation
from experiments.sphinx_7462.prepare import extract_source
from runtime.fsguard import changes, files, safe_path, sha256_bytes, write_json
from runtime.process import process, worker_env


CHECK_DIR = Path(__file__).with_name("checks")
CHECKS = {
    "test_requirement": ("fail_to_pass", "公开需求检查"),
    "test_regression": ("pass_to_pass", "公开回归检查"),
}


def _checked_cases(junit):
    cases = {}
    for node in ET.parse(junit).iter("testcase"):
        name = node.get("name")
        if name not in CHECKS or name in cases:
            raise ValueError("The evaluator must report exactly the two declared checks")
        if node.find("error") is not None or node.find("skipped") is not None:
            raise ValueError(f"The sample check did not complete normally: {name}")
        failure = node.find("failure")
        cases[name] = {
            "name": name,
            "status": "PASSED" if failure is None else "FAILED",
            "detail": "" if failure is None else (failure.get("message") or ""),
        }
    if set(cases) != set(CHECKS):
        raise ValueError("The evaluator did not execute both public checks")
    return cases


def evaluate_sample(task, output_dir, patch):
    output_dir.mkdir(parents=True, exist_ok=False)
    output_dir = output_dir.resolve()
    workspace = output_dir / "workspace"
    details = {"scope": "public_development_checks", "instance_id": task.spec.instance_id,
               "base_commit": task.spec.base_commit, "workspace": str(workspace)}
    cases = {}
    errors = []
    runner = None
    stage = "prepare"
    try:
        details["source_archive_sha256"] = task.check_archive()
        extract_source(task.archive, workspace)
        runner = _Evaluation(output_dir, workspace, task.python, task.git)
        runner.git_command("git_init", ["-c", "init.templateDir=", "init", "-q"])

        stage = "candidate_patch"
        candidate = Path(patch).read_bytes() if patch is not None else b""
        if len(candidate) > 2_000_000:
            raise ValueError("Candidate patch exceeds the 2 MB sample limit")
        details["candidate_patch_sha256"] = sha256_bytes(candidate)
        base_manifest = files(workspace)
        details["candidate_patch"] = runner.apply("candidate", candidate)
        before = files(workspace, base_manifest)
        details["candidate_changes"] = sorted(changes(base_manifest, before))

        stage = "public_checks"
        source = safe_path(workspace, task.spec.source_dir, allow_dot=True)
        check_source = (CHECK_DIR / task.spec.check_file).read_bytes()
        # The trusted checks are outside the candidate repository for both phases.
        check_file = runner.outputs / task.spec.check_file
        check_file.write_bytes(check_source)
        config_file = runner.outputs / "pytest.ini"
        config_file.write_text("[pytest]\n", encoding="utf-8")
        junit = runner.outputs / "pytest.xml"
        provenance = runner.outputs / "import-provenance.json"
        stdout = runner.outputs / "pytest.stdout.log"
        stderr = runner.outputs / "pytest.stderr.log"
        temp = runner.outputs / "tmp"
        temp.mkdir()
        environment = worker_env(workspace, task.python)
        environment.update(TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp),
                           PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
        command = [str(task.python), "-B", str(Path(__file__).with_name("worker.py")),
                   str(source), task.spec.package, str(check_file), str(config_file),
                   str(junit), str(provenance)]
        execution = process(command, workspace, environment, stdout, stderr,
                            task.protocol["test_timeout_seconds"])
        details["process"] = {
            **execution, "stdout": str(stdout), "stderr": str(stderr), "junit": str(junit),
            "check_sha256": sha256_bytes(check_source), "provenance": str(provenance),
        }
        if files(workspace, before) != before:
            raise ValueError("Test execution modified the evaluated source")
        if execution["timed_out"] or execution["returncode"] not in (0, 1):
            raise ValueError("The public checks could not run to completion")
        imports = json.loads(provenance.read_text(encoding="utf-8"))
        details["imports"] = imports
        if set(imports) != ({"pytest", "_pytest"} if task.spec.package == "pytest"
                            else {task.spec.package}):
            raise ValueError("Missing checked package import locations")
        for name, location in imports.items():
            if not Path(location).resolve(strict=True).is_relative_to(source / name):
                raise ValueError(f"Imported {name} from outside the evaluation copy")

        stage = "grading"
        cases = _checked_cases(junit)
        expected_code = int(any(case["status"] == "FAILED" for case in cases.values()))
        if execution["returncode"] != expected_code:
            raise ValueError("The pytest exit code and JUnit check results disagree")
    except (OSError, ValueError, RuntimeError, ET.ParseError) as exc:
        details["failure_stage"] = stage
        errors.append(f"{type(exc).__name__}: {exc}")

    valid = not errors
    metrics = {}
    for name, (key, label) in CHECKS.items():
        passed = int(valid and cases[name]["status"] == "PASSED")
        metrics[key] = metric(label, passed, 1, status=metric_status(passed, 1) if valid else "unknown")
    details["events"] = runner.events if runner is not None else []
    result = evaluation_result(
        resolved=valid and all(case["status"] == "PASSED" for case in cases.values()),
        status="completed" if valid else "evaluation_error",
        execution_valid=valid, metrics=metrics, errors=errors,
        cases=list(cases.values()), details=details,
    )
    write_json(output_dir / "evaluation.json", result)
    return result
