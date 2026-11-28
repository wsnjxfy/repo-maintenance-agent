"""Download the frozen public repo-split data without installing a task runtime."""

from __future__ import annotations

import argparse
from http.client import HTTPException
import json
from pathlib import Path
import re
import sys
import tarfile
from tempfile import TemporaryDirectory
import time

from experiments.verified_samples.prepare import (
    _download_archive,
    _fingerprint,
    _identity,
    _json_bytes,
    _validate_archive,
)
from runtime.fsguard import safe_path

from .runtime import _normalize_archive
from .specs import PACK_NAME, load_catalog, load_specs


DEFAULT_PACK = Path(__file__).resolve().parents[2] / "data" / PACK_NAME
_AGENT_FILES = ("source.tar.gz", "task-input.json")
_CONTROL_FILES = {"reference.patch": "patch", "test.patch": "test_patch"}


def _read_json(directory, name):
    return json.loads(safe_path(directory, name).read_bytes())


def _require_identity(record, spec, dataset=None):
    if any(record.get(key) != value for key, value in _identity(spec).items()):
        raise ValueError("Task identity differs from the published catalog")
    if dataset is not None and record.get("dataset") != dataset:
        raise ValueError("Task data differs from the pinned dataset row")


def _input(spec, row):
    _require_identity(row, spec)
    problem = row.get("problem_statement")
    if not isinstance(problem, str) or not problem.strip():
        raise ValueError("The pinned task has no problem statement")
    return {**_identity(spec), "problem_statement": problem}


def _labels(spec, row):
    labels = {}
    for key, count in (("FAIL_TO_PASS", spec.f2p_count), ("PASS_TO_PASS", spec.p2p_count)):
        values = row[key]
        if isinstance(values, str):
            values = json.loads(values)
        if (not isinstance(values, list) or len(values) != count
                or any(not isinstance(label, str) or not label.strip() for label in values)
                or len(set(values)) != count):
            raise ValueError(f"The pinned {key} labels are inconsistent")
        labels[key] = values
    if set(labels["FAIL_TO_PASS"]) & set(labels["PASS_TO_PASS"]):
        raise ValueError("The two evaluation label sets overlap")
    return labels


def _check_files(directory, record, names):
    if record["files"] != {name: _fingerprint(safe_path(directory, name)) for name in names}:
        raise ValueError(f"Existing task files fail their frozen SHA-256 checks: {directory}")


def _verify_agent(directory, spec, dataset):
    frozen = _read_json(directory, "freeze.json")
    _require_identity(frozen, spec, dataset)
    if frozen.get("project_split") != spec.group:
        raise ValueError("The frozen project split differs from the catalog")
    _check_files(directory, frozen, _AGENT_FILES)
    public = _read_json(directory, "task-input.json")
    if public != _input(spec, public):
        raise ValueError("The solver input must contain only the four public fields")


def _verify_control(directory, spec, dataset):
    record = _read_json(directory, "evaluation.json")
    _require_identity(record, spec, dataset)
    _labels(spec, record)
    _check_files(directory, record, _CONTROL_FILES)


def _fetch(url, path):
    for delay in (0.25, 1, None):
        try:
            _download_archive(url, path)
            return
        except (OSError, HTTPException):
            path.unlink(missing_ok=True)
            if delay is None:
                raise
            time.sleep(delay)


def _read_row(connection, parquet, index):
    result = connection.execute(
        "SELECT * FROM read_parquet(?, file_row_number = true) WHERE file_row_number = ?",
        [str(parquet), index],
    )
    values = result.fetchone()
    if values is None or result.fetchone() is not None:
        raise ValueError("The pinned Parquet row is missing or ambiguous")
    return dict(zip((column[0] for column in result.description), values))


def _pinned_row(snapshot, index, cache):
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("Install DuckDB in the controller Python to download missing data") from exc
    expected = snapshot["sha256"]
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("Missing SHA-256 pin for the benchmark source")
    parquet = cache / (expected + ".parquet")
    if not parquet.is_file():
        _fetch(snapshot["file_url"], parquet)
        if _fingerprint(parquet)["sha256"] != expected:
            raise ValueError("Downloaded benchmark data does not match the pinned SHA-256")
    try:
        with duckdb.connect(":memory:") as connection:
            return _read_row(connection, parquet, index)
    except duckdb.Error as exc:
        raise ValueError("The pinned benchmark parquet could not be read") from exc


def _validate_source(archive, spec, scratch):
    with tarfile.open(archive, "r:gz") as opened:
        members = opened.getmembers()
        roots = {member.name.partition("/")[0] for member in members}
        size = sum(member.size for member in members)
        links = sum(member.issym() for member in members)
    if len(roots) != 1 or not next(iter(roots)).endswith("-" + spec.base_commit):
        raise ValueError("The archive does not identify the pinned base commit")
    if len(members) > 20_000 or size > 250 * 1024 * 1024:
        raise ValueError("The archive exceeds the extraction limits")
    if links:
        # Validate the same link policy used later by the runtime without rewriting upstream data.
        _normalize_archive(archive, scratch / "verified-normalized.tar.gz")
    else:
        _validate_archive(archive, spec.base_commit)
    return {
        "archive_root": next(iter(roots)),
        "archive_members": len(members),
        "archive_links": links,
    }


def _publish(staging, destination):
    for delay in (0.25, 1, None):
        if destination.exists() or destination.is_symlink():
            return
        try:
            staging.rename(destination)
            return
        except PermissionError:
            if sys.platform != "win32" or delay is None:
                raise
            time.sleep(delay)


def prepare_one(pack, spec, snapshot, index, cache):
    """Prepare missing inputs. Existing bundles must pass their original pins."""
    agent = safe_path(pack, f"{spec.group}/{spec.instance_id}")
    control = safe_path(pack, f"control-only/{spec.group}/{spec.instance_id}")
    dataset = {key: snapshot[key] for key in ("dataset", "revision", "split")}
    dataset["parquet_row_index"] = index
    if agent.exists():
        _verify_agent(agent, spec, dataset)
    if control.exists():
        _verify_control(control, spec, dataset)
    if agent.exists() and control.exists():
        return False

    row = _pinned_row(snapshot, index, cache)
    public = _input(spec, row)
    labels = _labels(spec, row)
    with TemporaryDirectory(prefix="task-", dir=cache) as tmp:
        scratch = Path(tmp)
        if not agent.exists():
            staging = scratch / "solver"
            staging.mkdir()
            source_url = f"https://codeload.github.com/{spec.repo}/tar.gz/{spec.base_commit}"
            archive = staging / "source.tar.gz"
            _fetch(source_url, archive)
            archive_info = _validate_source(archive, spec, scratch)
            (staging / "task-input.json").write_bytes(_json_bytes(public))
            frozen = {
                "schema_version": 1,
                "project_split": spec.group,
                **_identity(spec),
                "dataset": dataset,
                "source_archive_url": source_url,
                **archive_info,
                "files": {name: _fingerprint(staging / name) for name in _AGENT_FILES},
            }
            (staging / "freeze.json").write_bytes(_json_bytes(frozen))
            agent.parent.mkdir(parents=True, exist_ok=True)
            _publish(staging, agent)
        if not control.exists():
            staging = scratch / "controller"
            staging.mkdir()
            for name, field in _CONTROL_FILES.items():
                patch = row.get(field)
                if not isinstance(patch, str) or not patch.startswith("diff --git "):
                    raise ValueError("A pinned patch is missing")
                (staging / name).write_bytes(patch.encode("utf-8"))
            record = {
                **_identity(spec),
                "dataset": dataset,
                **labels,
                "benchmark_environment_metadata": {
                    key: row[key] for key in ("version", "environment_setup_commit", "docker_image", "test_cmds", "log_parser")
                    if row.get(key) is not None
                },
                "files": {name: _fingerprint(staging / name) for name in _CONTROL_FILES},
                "calibration": "not_run",
            }
            (staging / "evaluation.json").write_bytes(_json_bytes(record))
            control.parent.mkdir(parents=True, exist_ok=True)
            _publish(staging, control)
    _verify_agent(agent, spec, dataset)
    _verify_control(control, spec, dataset)
    return True


def main(argv=None):
    specs = {spec.task_id: spec for spec in load_specs()}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", action="append", choices=tuple(specs),
                        help="A task to prepare. Repeat for more tasks or omit for all 15.")
    parser.add_argument("--pack-dir", type=Path, default=DEFAULT_PACK)
    args = parser.parse_args(argv)
    catalog = load_catalog()
    rows = {row["instance_id"]: row for row in catalog["instances"]}
    snapshots = {snapshot["dataset"]: snapshot for snapshot in catalog["dataset_snapshots"].values()}
    pack = args.pack_dir.resolve()
    data_root = DEFAULT_PACK.parent
    if pack.is_relative_to(data_root.parent) and not pack.is_relative_to(data_root):
        parser.error("--pack-dir inside the checkout must be under its ignored data directory")
    pack.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".prepare-", dir=pack) as tmp:
        for task_id in dict.fromkeys(args.task or specs):
            spec = specs[task_id]
            row = rows[spec.instance_id]
            try:
                modified = prepare_one(pack, spec, snapshots[row["dataset"]], row["parquet_row_index"], Path(tmp))
            except (OSError, ValueError, KeyError, RuntimeError, HTTPException, tarfile.TarError) as exc:
                parser.exit(1, f"{task_id}: {exc}\n")
            print(f"{task_id}: {'prepared' if modified else 'verified existing data'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
