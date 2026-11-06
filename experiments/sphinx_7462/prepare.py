"""Prepare an unchanged source snapshot and answer-free input for one trial.

This module does not start an agent, install packages, or apply evaluation patches.
All runtime locations are explicit; no credentials are read or written.
"""

import argparse
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import sys
import tarfile


TASK_ID = "sphinx-doc__sphinx-7462"
REPOSITORY = "sphinx-doc/sphinx"
BASE_COMMIT = "b3e26a6c851133b82b50f4b68b53692076574d13"
SOURCE_SHA256 = "f4863f25cc280ec7aa4fb1a9420f019c622cc3b3a949bd781639e749d4f31efc"
INPUT_FIELDS = ("instance_id", "repo", "base_commit", "problem_statement")
CONFIG_NAMES = {
    "conftest.py", "pytest.ini", "setup.cfg", "tox.ini", "pyproject.toml",
    "setup.py", "MANIFEST.in", ".coveragerc", "pip.conf", "pip.ini",
}


def digest(path):
    checksum = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def write_json(path, value):
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def manifest(root):
    root = Path(root)
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if ".git" in relative.parts:
            continue
        if path.is_symlink():
            raise ValueError("Source snapshot contains a link: " + relative.as_posix())
        if path.is_file():
            result[relative.as_posix()] = digest(path)
    return result


def protected_source(path):
    parts = tuple(part.casefold() for part in PurePosixPath(path).parts)
    name = parts[-1]
    return (
        "tests" in parts[:-1]
        or name in {item.casefold() for item in CONFIG_NAMES}
        or fnmatch.fnmatch(name.lower(), "requirements*.txt")
    )


def checked_members(archive):
    """Reject ambiguous paths, links, special files, duplicates and huge inputs."""
    members = archive.getmembers()
    if not members or len(members) > 20000:
        raise ValueError("Empty archive or excessive member count")
    root_name = None
    seen = set()
    total_size = 0
    checked = []
    for member in members:
        name = member.name
        parts = PurePosixPath(name).parts
        if (
            not parts or name.startswith("/") or "\\" in name or ":" in name
            or "\x00" in name or ".." in parts or any(p.endswith((".", " ")) for p in parts)
        ):
            raise ValueError("Unsafe archive path: " + repr(name))
        reserved = {"con", "prn", "aux", "nul", *["com" + str(i) for i in range(1, 10)], *["lpt" + str(i) for i in range(1, 10)]}
        if any(part.split(".", 1)[0].casefold() in reserved for part in parts):
            raise ValueError("Reserved Windows archive path: " + name)
        if not (member.isfile() or member.isdir()) or member.issparse():
            raise ValueError("Links and special archive members are forbidden: " + name)
        if root_name is None:
            root_name = parts[0]
        if parts[0] != root_name:
            raise ValueError("Archive must have one top-level source directory")
        relative = PurePosixPath(*parts[1:])
        if not parts[1:]:
            if not member.isdir():
                raise ValueError("Archive root must be a directory")
            continue
        if ".git" in {part.casefold() for part in relative.parts}:
            raise ValueError("Archive may not supply Git metadata")
        key = relative.as_posix().casefold()
        if key in seen:
            raise ValueError("Duplicate archive path: " + relative.as_posix())
        seen.add(key)
        total_size += member.size
        if member.size > 25 * 1024 * 1024 or total_size > 250 * 1024 * 1024:
            raise ValueError("Archive exceeds source size limits")
        checked.append((member, relative))
    if not checked:
        raise ValueError("Archive contains no source members")
    return checked


def extract_source(source_archive, destination):
    """Extract only validated regular files/directories, dropping the root name."""
    destination = Path(destination)
    with tarfile.open(source_archive, "r:gz") as archive:
        checked = checked_members(archive)
        destination.mkdir(parents=True, exist_ok=False)
        root = destination.resolve()
        for member, relative in checked:
            target = root.joinpath(*relative.parts)
            if not target.resolve().is_relative_to(root):
                raise ValueError("Archive target escaped extraction directory")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)


def safe_environment(python, workspace):
    """Pass Windows essentials and explicit Python settings, never API secrets."""
    allowed = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "LANG")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment.update({
        "PATH": str(Path(python).parent) + os.pathsep + os.defpath,
        "PYTHONPATH": str(workspace),
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    })
    return environment


def probe_environment(python, workspace):
    script = r'''
import importlib.metadata as metadata
import json
from pathlib import Path
import site
import sys
import sphinx
import pytest
editable_paths = []
for directory in site.getsitepackages():
    for link in Path(directory).glob("*.egg-link"):
        content = link.read_text(encoding="utf-8").splitlines()
        if content:
            editable_paths.append({"file": str(link), "target": content[0]})
    for pth in Path(directory).glob("*.pth"):
        for line in pth.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip() and not line.startswith(("#", "import ", "import\t")):
                value = Path(line.strip())
                target = value if value.is_absolute() else pth.parent / value
                editable_paths.append({"file": str(pth), "target": str(target.resolve())})
print(json.dumps({
    "python": sys.executable,
    "python_version": sys.version,
    "prefix": sys.prefix,
    "sphinx_file": sphinx.__file__,
    "pytest_version": pytest.__version__,
    "sys_path": sys.path,
    "editable_paths": editable_paths,
    "packages": sorted([
        {"name": dist.metadata["Name"], "version": dist.version}
        for dist in metadata.distributions()
    ], key=lambda item: item["name"].lower())
}, ensure_ascii=True))
'''
    completed = subprocess.run(
        [str(python), "-B", "-c", script], cwd=workspace,
        env=safe_environment(python, workspace), capture_output=True,
        encoding="utf-8", errors="replace", timeout=45,
    )
    if completed.returncode:
        raise RuntimeError("Environment import probe failed: " + completed.stderr)
    probe = json.loads(completed.stdout)
    actual_source = Path(probe["sphinx_file"]).resolve()
    if not actual_source.is_relative_to(workspace.resolve()):
        raise ValueError("Sphinx import did not come from the clean workspace")
    prefix = Path(probe["prefix"]).resolve()
    for item in probe["editable_paths"]:
        target = Path(item["target"]).resolve()
        if not (target.is_relative_to(workspace.resolve()) or target.is_relative_to(prefix)):
            raise ValueError("External editable/path injection detected: " + item["file"])
    probe.update({"status": "passed", "stderr": completed.stderr})
    return probe


def protocol():
    return {
        "schema_version": 1,
        "experiment_kind": "codex_subagent_single_development_trial",
        "attempt_limit": 1,
        "max_actions": 30,
        "wall_time_seconds": 1200,
        "test_timeout_seconds": 90,
        "prepared_state": "read_only_preflight_does_not_start_solving_clock_or_action_budget",
        "action_budget_scope": "active_workspace_actions_only; prepared_preflight_events_are_logged_but_not_counted",
        "finish_semantics": "stop_and_freeze_operation; not_counted_towards_the_30_workspace_actions",
        "allowed_input_fields": list(INPUT_FIELDS),
        "allowed_actions": ["list_files", "search_text", "read_lines", "apply_patch", "run_visible_tests", "finish"],
        "isolation": {
            "platform": platform.system() + " native",
            "os_sandbox": False,
            "description": "Fresh context and checked workspace tools are behavioral boundaries, not OS isolation; inherited Codex tools remain outside this wrapper's enforcement.",
        },
        "trace": {
            "workspace_actions": "automatic_jsonl_with_separate_full_outputs",
            "native_agent_trace": None,
            "native_trace_completeness": "not_verified_no_solving_agent_started",
            "internal_reasoning": "not_requested_or_available",
        },
        "model_usage": {
            "requested_model": "inherit_current_codex_session",
            "observed_model": None,
            "model_snapshot": None,
            "generation_parameters": None,
            "input_tokens": None,
            "output_tokens": None,
            "reasoning_tokens": None,
            "task_cost_usd": None,
            "availability": "not_observed; never_substitute_character_estimates_or_account_delta",
        },
        "independent_evaluation": "pending_after_frozen_submission; visible_tests_are_not_acceptance",
    }


def instructions():
    return """# Repository maintenance trial — generic operating instructions

Read task-input.json for the unchanged original problem statement. Work only in the supplied clean repository, using the designated workspace tool entry point.

Allowed actions: list_files, search_text, read_lines, apply_patch, run_visible_tests, finish. Search, read, decide, edit, and test independently. You choose relevant code and original tests; no repair locations or evaluation test list are supplied.

Do not read the parent project, controller internals, evaluation materials, prior run logs, unrelated local files, global session history, or issue/PR discussions. Do not browse the network or spawn other agents. These are trial rules, not claims of an OS sandbox.

Original tests, conftest files and test/dependency configuration are read-only. If the controller permits new self-tests, keep them separate under scratch and run them separately. Do not modify or suppress the original tests. Do not install packages or change the environment.

Only the controller may explicitly activate the prepared run. Prepared mode supports read-only preflight: events are logged without spending the solving action budget or starting the clock. Once active, the declared limit is 30 workspace actions and 20 minutes; test processes have a 90-second limit. The finish operation stops/freezes the attempt and does not count towards the 30 actions. Do not bypass the designated tools.

Visible test success is local feedback, not independent acceptance. When finished or unable to proceed, call finish with a brief reason. The controller will freeze all changes and independently evaluate afterward; no acceptance feedback is available during this attempt.

Native Codex system prompts and unexposed token/cost data are not recorded as known. Your summary is explanatory evidence and does not replace the automatic action log. Do not claim access to internal reasoning traces.
"""


def prepare(source_archive, record_path, run_dir, python, git, expected_sha256=SOURCE_SHA256,
            skip_env_probe=False):
    """Create a new run; never reuse an existing directory or invoke an agent."""
    source_archive = Path(source_archive).resolve(strict=True)
    record_path = Path(record_path).resolve(strict=True)
    run_dir = Path(run_dir).absolute()
    python = Path(python).resolve(strict=True)
    git = Path(git).resolve(strict=True)
    if run_dir.exists() or run_dir.is_symlink():
        raise FileExistsError("Refusing to overwrite an existing run directory: " + str(run_dir))
    if len(expected_sha256) != 64 or digest(source_archive) != expected_sha256.lower():
        raise ValueError("Source archive SHA256 does not match the expected fixed snapshot")
    row = json.loads(record_path.read_text(encoding="utf-8"))
    task_input = {key: row[key] for key in INPUT_FIELDS}
    if (task_input["instance_id"], task_input["repo"], task_input["base_commit"]) != (
        TASK_ID, REPOSITORY, BASE_COMMIT
    ):
        raise ValueError("Record does not match the selected task and fixed base commit")
    if not isinstance(task_input["problem_statement"], str) or not task_input["problem_statement"].strip():
        raise ValueError("Original problem_statement must be a non-empty string")
    # Validate the entire archive before creating anything in the run directory.
    with tarfile.open(source_archive, "r:gz") as archive:
        checked_members(archive)
    run_dir.mkdir(parents=True, exist_ok=False)
    try:
        return _populate_run(source_archive, run_dir, python, git, task_input,
                             expected_sha256, skip_env_probe)
    except Exception as exc:
        # Never mark an incomplete run as ready. Existing runs were rejected
        # before mkdir, so this handler only touches the run created above.
        controller = run_dir / "controller"
        try:
            controller.mkdir(exist_ok=True)
            state_path = controller / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {
                "started_at": None, "action_count": 0, "event_count": 0, "last_visible_tests": None,
            }
            state["phase"] = "preparation_failed"
            write_json(state_path, state)
            write_json(controller / "preparation-failure.json", {
                "status": "preparation_failed", "error_type": type(exc).__name__, "error": str(exc),
            })
        except (OSError, ValueError):
            pass
        raise


def _populate_run(source_archive, run_dir, python, git, task_input, expected_sha256, skip_env_probe):
    controller = run_dir / "controller"
    inputs = run_dir / "input"
    controller.mkdir()
    inputs.mkdir()
    (controller / "outputs").mkdir()
    workspace = run_dir / "workspace"
    baseline = controller / "base"
    extract_source(source_archive, baseline)
    extract_source(source_archive, workspace)
    initial = manifest(baseline)
    if manifest(workspace) != initial:
        raise ValueError("Extracted workspace differs from the baseline")
    # An empty repository prevents Sphinx's version probe finding the parent
    # project's unrelated history. No commit, remote, or repair history is added.
    subprocess.run([str(git), "-C", str(workspace), "init", "-q"], check=True,
                   capture_output=True, timeout=30)
    subprocess.run([str(git), "-C", str(workspace), "config", "core.autocrlf", "false"],
                   check=True, capture_output=True, timeout=30)
    config = {
        "schema_version": 1,
        "run_id": run_dir.name,
        "task_id": TASK_ID,
        "repo": REPOSITORY,
        "base_commit": BASE_COMMIT,
        "workspace": str(workspace.resolve()),
        "baseline_dir": str(baseline.resolve()),
        "python": str(python),
        "git": str(git),
        "initial_manifest": initial,
        "protected_paths": sorted(path for path in initial if protected_source(path)),
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "source_archive_sha256": expected_sha256.lower(),
        "git_initialization": "empty_repository_without_commits_or_remotes; blocks_parent_repository_discovery",
    }
    state = {
        "phase": "prepared", "started_at": None, "action_count": 0,
        "event_count": 0, "last_visible_tests": None,
    }
    write_json(inputs / "task-input.json", task_input)
    (inputs / "agent-instructions.md").write_text(instructions(), encoding="utf-8")
    write_json(controller / "config.json", config)
    write_json(controller / "protocol.json", protocol())
    write_json(controller / "state.json", state)
    (controller / "actions.jsonl").write_text("", encoding="utf-8")
    probe = {"status": "skipped", "reason": "explicit_fixture_test_option"}
    if not skip_env_probe:
        try:
            probe = probe_environment(python, workspace)
        except Exception as exc:
            write_json(controller / "environment-probe.json", {
                "status": "failed", "error_type": type(exc).__name__, "error": str(exc),
            })
            state["phase"] = "preparation_failed"
            write_json(controller / "state.json", state)
            raise
    write_json(controller / "environment-probe.json", probe)
    if manifest(workspace) != initial or manifest(baseline) != initial:
        raise ValueError("Preparation/import probe changed original source files")
    summary = {
        "status": "prepared", "solving_agent_started": False,
        "run_dir": str(run_dir.resolve()), "original_files": len(initial),
        "protected_files": len(config["protected_paths"]),
        "source_archive_sha256": expected_sha256.lower(),
        "environment_probe": probe["status"],
        "agent_input_fields": list(task_input),
        "action_count": 0,
    }
    write_json(controller / "preparation-result.json", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-archive", required=True)
    parser.add_argument("--record-path", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--git", required=True)
    parser.add_argument("--expected-sha256", default=SOURCE_SHA256)
    parser.add_argument("--skip-env-probe", action="store_true", help="Only for synthetic fixture tests")
    args = parser.parse_args(argv)
    result = prepare(**vars(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
