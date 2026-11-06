"""Independently evaluate a frozen production patch on a fresh source snapshot.

This is a local Windows-compatible development evaluator, not an OS sandbox or
an execution of the official Linux/tox harness. It never starts a model or reads
the record's reference ``patch`` as its candidate. Python 3.9+ and Git required.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import time

from experiments.sphinx_7462.prepare import (
    BASE_COMMIT, REPOSITORY, SOURCE_SHA256, TASK_ID,
    digest, extract_source, manifest, probe_environment, protected_source,
)
from experiments.sphinx_7462.tools import (
    IGNORED_DIRS, _changes, _digest, _files, _process, _safe_path,
    _worker_env, _write_json,
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def _label_list(value, name):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise ValueError(name + " must be a JSON list") from exc
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise ValueError(name + " must contain nonempty string labels")
    if len(value) != len(set(value)):
        raise ValueError(name + " contains duplicate labels")
    return value


def _validated_record(record, review):
    identity = (TASK_ID, REPOSITORY, BASE_COMMIT)
    for name, value in (("record", record), ("review manifest", review)):
        if (value.get("instance_id"), value.get("repo"), value.get("base_commit")) != identity:
            raise ValueError(name + " does not match the selected task and fixed base commit")
    labels = {}
    for kind in ("FAIL_TO_PASS", "PASS_TO_PASS"):
        actual = _label_list(record.get(kind), "record." + kind)
        expected = _label_list(review.get("evaluation", {}).get(kind), "manifest." + kind)
        if set(actual) != set(expected):
            raise ValueError("Record and review manifest disagree on " + kind)
        labels[kind] = expected
    if set(labels["FAIL_TO_PASS"]) & set(labels["PASS_TO_PASS"]):
        raise ValueError("Requirement and regression label sets overlap")
    body = record.get("test_patch")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("Official test_patch must be a nonempty string")
    expected_hash = review.get("reference_summary", {}).get("test_patch_sha256_utf8")
    if not isinstance(expected_hash, str) or _sha_bytes(body.encode("utf-8")) != expected_hash:
        raise ValueError("Official test_patch SHA256 does not match the review manifest")
    return labels, body.encode("utf-8")


def _patch_paths(body, workspace):
    """Validate Git patch paths before letting Git access the workspace.

    Only regular-file, a/ b/-prefixed Git patches are accepted. Renames, copies,
    quoted/whitespace paths and symlinks are intentionally unsupported here.
    """
    try:
        text = body.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("Patch is not UTF-8 Git unified diff") from exc
    paths = set()
    saw_diff = False
    in_header = False
    for line in text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) != 4 or any('"' in value for value in parts[2:]):
                raise ValueError("Quoted or whitespace patch paths are unsupported")
            paths.update(parts[2:])
            saw_diff = True
            in_header = True
        elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            raise ValueError("Rename/copy patches are unsupported")
        elif in_header and line.startswith(("old mode ", "new mode ", "new file mode ", "deleted file mode ")):
            if line.split()[-1] not in {"100644", "100755"}:
                raise ValueError("Only regular-file patches are allowed")
        elif in_header and line.startswith(("--- ", "+++ ")):
            value = line[4:].split("\t", 1)[0]
            if value != "/dev/null":
                paths.add(value)
        elif line.startswith("@@"):
            in_header = False
    if not saw_diff or not paths:
        raise ValueError("Expected a Git unified patch with file headers")
    relative = set()
    for value in paths:
        if not value.startswith(("a/", "b/")):
            raise ValueError("Patch paths must have a/ or b/ prefixes")
        name = value[2:]
        _safe_path(workspace, name)
        if any(part.casefold() in IGNORED_DIRS for part in name.replace("\\", "/").split("/")):
            raise ValueError("Patch targets generated metadata")
        if name.casefold().endswith((".pyc", ".pyo")) or name.casefold() == ".coverage":
            raise ValueError("Patch targets generated metadata")
        relative.add(name.replace("\\", "/"))
    return sorted(relative)


def _protected(name):
    parts = name.replace("\\", "/").casefold().split("/")
    leaf = parts[-1]
    return (protected_source(name)
            or any(part in {"test", "tests", "testing"} for part in parts[:-1])
            or leaf.startswith("test_") or leaf.endswith("_test.py") or leaf == "test.py")


class _Evaluation:
    def __init__(self, output_dir, workspace, python, git):
        self.output_dir = output_dir
        self.workspace = workspace
        self.python = python
        self.git = git
        self.outputs = output_dir / "outputs"
        self.outputs.mkdir()
        self.events = []

    def git_command(self, stage, args, body=None):
        env = _worker_env(self.workspace, self.python)
        env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_TERMINAL_PROMPT": "0"})
        command = [str(self.git), "-c", "core.autocrlf=false", "-c", "core.filemode=false",
                   "-c", "core.hooksPath=" + str(self.output_dir / "no-hooks")] + args
        started = time.monotonic()
        result = subprocess.run(command, cwd=str(self.workspace), env=env, input=body,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        stdout = self.outputs / (stage + ".stdout.log")
        stderr = self.outputs / (stage + ".stderr.log")
        stdout.write_bytes(result.stdout)
        stderr.write_bytes(result.stderr)
        event = {"stage": stage, "command": command, "cwd": str(self.workspace),
                 "returncode": result.returncode, "elapsed_seconds": round(time.monotonic() - started, 4),
                 "stdout": str(stdout), "stderr": str(stderr),
                 "stdout_sha256": digest(stdout), "stderr_sha256": digest(stderr)}
        self.events.append(event)
        with (self.output_dir / "events.jsonl").open("a", encoding="utf-8") as target:
            target.write(json.dumps(event, ensure_ascii=False) + "\n")
        if result.returncode:
            raise RuntimeError(stage + " failed; see captured Git stderr")
        return event

    def apply(self, kind, body):
        if not body:
            return {"status": "skipped_empty", "sha256": _sha_bytes(body), "paths": []}
        paths = _patch_paths(body, self.workspace)
        if kind == "candidate":
            forbidden = [name for name in paths if _protected(name)]
            if forbidden:
                raise ValueError("Candidate patch changes protected tests/configuration: " + ", ".join(forbidden))
        elif any(not _protected(name) for name in paths):
            raise ValueError("Official test patch unexpectedly changes production files")
        check = self.git_command(kind + "_check", ["apply", "--no-index", "--check", "--whitespace=nowarn", "-"], body)
        applied = self.git_command(kind + "_apply", ["apply", "--no-index", "--whitespace=nowarn", "-"], body)
        return {"status": "applied", "sha256": _sha_bytes(body), "paths": paths,
                "check": check, "apply": applied}


def evaluate(source_archive, record_path, review_manifest, output_dir, python, git,
             candidate_patch=None, timeout_seconds=90, expected_source_sha256=SOURCE_SHA256,
             skip_env_probe=False):
    """Evaluate one explicit frozen candidate; an empty candidate tests the base.

    Invalid task/input data and existing output directories are rejected before
    any workspace is created. Errors after creation are retained in evaluation.json.
    ``skip_env_probe`` and a nondefault archive SHA are for synthetic fixtures.
    """
    source_archive = Path(source_archive).resolve(strict=True)
    record_path = Path(record_path).resolve(strict=True)
    review_manifest = Path(review_manifest).resolve(strict=True)
    output_dir = Path(output_dir).absolute()
    python = Path(python).resolve(strict=True)
    git = Path(git).resolve(strict=True)
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError("Refusing to overwrite an existing evaluation directory")
    if (not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 90):
        raise ValueError("Test timeout must be positive and at most 90 seconds")
    if not isinstance(expected_source_sha256, str) or len(expected_source_sha256) != 64:
        raise ValueError("Expected source SHA256 must be a 64-character string")
    if digest(source_archive) != expected_source_sha256.lower():
        raise ValueError("Source archive SHA256 does not match the fixed snapshot")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    review = json.loads(review_manifest.read_text(encoding="utf-8"))
    labels, test_patch = _validated_record(record, review)
    candidate_path = Path(candidate_patch).resolve(strict=True) if candidate_patch is not None else None
    candidate = candidate_path.read_bytes() if candidate_path is not None else b""
    if len(candidate) > 2_000_000:
        raise ValueError("Candidate patch exceeds the 2MB development-trial limit")
    output_dir.mkdir(parents=True, exist_ok=False)
    workspace = output_dir / "workspace"
    result = {"schema_version": 1, "instance_id": TASK_ID, "repo": REPOSITORY,
              "base_commit": BASE_COMMIT, "status": "error", "resolved": False,
              "started_at": _now(), "output_dir": str(output_dir.resolve()),
              "workspace": str(workspace.resolve()), "validation_errors": [],
              "solving_agent_started": False,
              "platform": platform.system() + " native compatibility evaluation",
              "official_linux_container_executed": False, "official_tox_wrapper_executed": False,
              "os_sandbox": False,
              "input": {"source_archive_sha256": expected_source_sha256.lower(),
                        "record_sha256": digest(record_path), "review_manifest_sha256": digest(review_manifest),
                        "candidate_patch_sha256": _sha_bytes(candidate),
                        "candidate_supplied": candidate_path is not None,
                        "candidate_source": str(candidate_path) if candidate_path is not None else None,
                        "official_test_patch_sha256": _sha_bytes(test_patch)},
              "patches": {}, "environment": None, "process": None, "grading": None}
    stage = "workspace_preparation"
    run = None
    try:
        # Retain exact patch bytes for audit, outside the evaluated repository.
        (output_dir / "candidate.patch").write_bytes(candidate)
        (output_dir / "official-test.patch").write_bytes(test_patch)
        extract_source(source_archive, workspace)
        initial = manifest(workspace)
        _write_json(output_dir / "source-manifest.initial.json", initial)
        run = _Evaluation(output_dir, workspace, python, git)
        run.git_command("git_init", ["-c", "init.templateDir=", "init", "-q"])
        stage = "initial_environment_probe"
        if skip_env_probe:
            result["environment"] = {"status": "skipped", "reason": "explicit_synthetic_fixture_option"}
        else:
            result["environment"] = probe_environment(python, workspace)
        _write_json(output_dir / "environment-probe.json", result["environment"])
        if _files(workspace, initial) != initial:
            raise ValueError("Initial environment probe changed source files")

        stage = "candidate_patch"
        result["patches"]["candidate"] = run.apply("candidate", candidate)
        after_candidate = _files(workspace, initial)
        candidate_changes = _changes(initial, after_candidate)
        if any(_protected(name) for name in candidate_changes):
            raise ValueError("Candidate modified protected tests/configuration")

        stage = "official_test_patch"
        result["patches"]["official_tests"] = run.apply("official_tests", test_patch)
        before_tests = _files(workspace, initial)
        trusted_changes = _changes(after_candidate, before_tests)
        expected_test_paths = set(result["patches"]["official_tests"]["paths"])
        if not set(trusted_changes).issubset(expected_test_paths):
            raise ValueError("Test patch changed files outside its declared targets")
        test_targets = sorted(name for name in expected_test_paths if name.endswith(".py")
                              and Path(name).name != "conftest.py")
        if not test_targets:
            raise ValueError("Official test patch supplies no Python test targets")
        stage = "postpatch_import_probe"
        if not skip_env_probe:
            post_probe = probe_environment(python, workspace)
            _write_json(output_dir / "postpatch-environment-probe.json", post_probe)
            result["postpatch_environment"] = post_probe
        if _files(workspace, initial) != before_tests:
            raise ValueError("Post-patch import probe changed source files")

        stage = "pytest"
        stdout = run.outputs / "pytest.stdout.log"
        stderr = run.outputs / "pytest.stderr.log"
        combined = run.outputs / "pytest.combined.log"
        junit = run.outputs / "pytest.xml"
        command = [str(python), "-m", "pytest", "-rA", "-vv", "--color=no", "--tb=short",
                   "--junitxml=" + str(junit)] + test_targets
        process = _process(command, workspace, _worker_env(workspace, python), stdout, stderr, timeout_seconds)
        combined.write_bytes(stdout.read_bytes() + b"\n" + stderr.read_bytes())
        process.update({"command": command, "cwd": str(workspace), "timeout_seconds": timeout_seconds,
                        "test_targets": test_targets, "stdout": str(stdout), "stderr": str(stderr),
                        "combined_log": str(combined), "junit": str(junit),
                        "stdout_sha256": digest(stdout), "stderr_sha256": digest(stderr),
                        "combined_log_sha256": digest(combined),
                        "junit_sha256": digest(junit) if junit.exists() else None})
        result["process"] = process

        after_tests = _files(workspace, initial)
        test_execution_changes = _changes(before_tests, after_tests)
        integrity = {"original_files": len(initial), "initial_manifest_digest": _digest(initial),
                     "candidate_changes": candidate_changes,
                     "official_test_changes": trusted_changes,
                     "pretest_manifest_digest": _digest(before_tests),
                     "posttest_manifest_digest": _digest(after_tests),
                     "test_execution_source_changes": test_execution_changes,
                     "source_unchanged_during_test_execution": not test_execution_changes,
                     "total_changes_from_base": _changes(initial, after_tests)}
        result["source_integrity"] = integrity
        _write_json(output_dir / "source-integrity.json", integrity)
        stage = "grading"
        from experiments.sphinx_7462.grading import grade_run
        grading = grade_run(junit, combined, labels["FAIL_TO_PASS"], labels["PASS_TO_PASS"],
                            process["returncode"], timed_out=process["timed_out"])
        result["grading"] = grading
        result["validation_errors"].extend(grading["validation_errors"])
        if test_execution_changes:
            result["validation_errors"].append("Test execution changed evaluated source files")
        result["resolved"] = bool(grading["resolved"] and not result["validation_errors"])
        result["status"] = "completed" if grading["execution_valid"] and not test_execution_changes else "error"
    except Exception as exc:
        result["validation_errors"].append(type(exc).__name__ + ": " + str(exc))
        result["failure_stage"] = stage
        result["status"] = "error"
        result["resolved"] = False
    finally:
        result["finished_at"] = _now()
        result["events"] = run.events if run is not None else []
        _write_json(output_dir / "evaluation.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-archive", required=True)
    parser.add_argument("--record-path", required=True)
    parser.add_argument("--review-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--git", required=True)
    parser.add_argument("--candidate-patch")
    parser.add_argument("--timeout-seconds", type=float, default=90)
    parser.add_argument("--expected-source-sha256", default=SOURCE_SHA256)
    parser.add_argument("--skip-env-probe", action="store_true", help="Synthetic fixture tests only")
    args = parser.parse_args(argv)
    try:
        result = evaluate(**vars(args))
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({"status": "error", "resolved": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": result["status"], "resolved": result["resolved"],
                      "evaluation": str(Path(args.output_dir).resolve() / "evaluation.json"),
                      "validation_errors": result["validation_errors"]}, ensure_ascii=False))
    return 2 if result["status"] == "error" else 0 if result["resolved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
