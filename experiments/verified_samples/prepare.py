"""Prepare one or more Verified development samples from public sources."""

from __future__ import annotations

import argparse
import hashlib
from http.client import HTTPException
import json
from pathlib import Path
import sys
import tarfile
import tempfile
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from experiments.sphinx_7462.prepare import checked_members

from .specs import SAMPLES


DATASET = "princeton-nlp/SWE-bench_Verified"
ROWS_API = "https://datasets-server.huggingface.co/rows"
DATA_ROOT = Path(__file__).resolve().parents[2] / "data"
INPUT_FIELDS = ("instance_id", "repo", "base_commit", "problem_statement")
PAYLOAD_NAMES = ("source.tar.gz", "task-input.json", "reference.patch")
MAX_ROW_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
PYLINT_TEST_SYMLINKS = {
    "tests/functional/s/symlink/_binding/__init__.py": "../symlink_module/__init__.py",
    "tests/functional/s/symlink/_binding/symlink_module.py": "../symlink_module/symlink_module.py",
}


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _identity(spec):
    return {key: getattr(spec, key) for key in INPUT_FIELDS[:3]}


def _open(url):
    request = Request(url, headers={"User-Agent": "HJ10-verified-sample-preparation/1"})
    return urlopen(request, timeout=60)


def _get_inputs(spec):
    query = urlencode({
        "dataset": DATASET,
        "config": "default",
        "split": "test",
        "offset": spec.dataset_row,
        "length": 1,
    })
    with _open(f"{ROWS_API}?{query}") as response:
        body = response.read(MAX_ROW_BYTES + 1)
    if len(body) > MAX_ROW_BYTES:
        raise ValueError("Verified dataset row exceeds the input limit")
    payload = json.loads(body)
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError("The Verified dataset did not return exactly one row")
    selected = rows[0]
    row = selected.get("row") if isinstance(selected, dict) else None
    if not isinstance(row, dict) or selected.get("row_idx") != spec.dataset_row:
        raise ValueError("Unexpected Verified dataset row")
    identity = _identity(spec)
    if any(row.get(key) != value for key, value in identity.items()):
        raise ValueError("Verified row does not match the pinned instance, repository and base commit")
    problem = row.get("problem_statement")
    patch = row.get("patch")
    if not isinstance(problem, str) or not problem.strip():
        raise ValueError("Verified row has no problem statement")
    if not isinstance(patch, str) or not patch.startswith("diff --git "):
        raise ValueError("Verified row has no reference patch")
    task_input = {**identity, "problem_statement": problem}
    return _json_bytes(task_input), patch.encode("utf-8")


def _download_archive(url, path):
    total = 0
    with _open(url) as response, path.open("xb") as target:
        for block in iter(lambda: response.read(1024 * 1024), b""):
            total += len(block)
            if total > MAX_ARCHIVE_BYTES:
                raise ValueError("Source archive exceeds the download limit")
            target.write(block)


def _validate_archive(path, base_commit):
    with tarfile.open(path, "r:gz") as archive:
        members = checked_members(archive)
        prefix = members[0][0].name.split("/", 1)[0]
        if not prefix.endswith("-" + base_commit):
            raise ValueError("Source archive does not identify the pinned base commit")


def _omit_pylint_test_symlinks(upstream, destination):
    """Exclude two pinned suite fixtures without following archive links."""
    with tarfile.open(upstream, "r:gz") as source:
        kept = []
        omitted = {}
        for member in source.getmembers():
            relative = member.name.partition("/")[2]
            if relative in PYLINT_TEST_SYMLINKS:
                if (not member.issym() or member.linkname != PYLINT_TEST_SYMLINKS[relative]
                        or relative in omitted):
                    raise ValueError("Unexpected Pylint symlink fixture metadata")
                omitted[relative] = member.linkname
            elif not (member.isfile() or member.isdir()) or member.issparse():
                raise ValueError("Unexpected special member in Pylint archive: " + relative)
            else:
                kept.append(member)
        if omitted != PYLINT_TEST_SYMLINKS:
            raise ValueError("Missing pinned Pylint symlink fixtures")
        with tarfile.open(destination, "x:gz") as clean:
            for member in kept:
                if member.isdir():
                    clean.addfile(member)
                else:
                    with source.extractfile(member) as contents:
                        clean.addfile(member, contents)
    return {
        "upstream_archive": _fingerprint(upstream),
        "omitted_test_symlinks": omitted,
    }


def _fingerprint(path):
    checksum = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            total += len(chunk)
            checksum.update(chunk)
    return {"bytes": total, "sha256": checksum.hexdigest()}


def _check_existing(path, spec):
    if path.is_symlink() or not path.is_dir() or path.resolve() != path:
        raise ValueError(f"Input path is not a local data directory: {path}")
    for name in (*PAYLOAD_NAMES, "freeze.json"):
        file = path / name
        if not file.is_file() or file.resolve() != file:
            raise ValueError(f"Missing or redirected frozen input: {name}")
    freeze = json.loads((path / "freeze.json").read_bytes())
    if not isinstance(freeze, dict) or any(freeze.get(key) != value for key, value in _identity(spec).items()):
        raise ValueError("Existing freeze does not match the selected sample")
    if freeze.get("files") != {name: _fingerprint(path / name) for name in PAYLOAD_NAMES}:
        raise ValueError("Existing sample files fail their frozen SHA-256 checks")


def prepare(spec):
    """Write a verified bundle only after all inputs have been checked."""
    DATA_ROOT.mkdir(exist_ok=True)
    if DATA_ROOT.resolve() != DATA_ROOT:
        raise ValueError("The repository data directory is redirected")
    destination = DATA_ROOT / spec.instance_id
    if destination.exists() or destination.is_symlink():
        _check_existing(destination, spec)
        return destination

    source_url = f"https://codeload.github.com/{spec.repo}/tar.gz/{spec.base_commit}"
    task_input, reference = _get_inputs(spec)
    with tempfile.TemporaryDirectory(prefix=f".{spec.task_id}-", dir=DATA_ROOT) as scratch:
        temporary = Path(scratch)
        staging = temporary / "bundle"
        staging.mkdir()
        upstream = temporary / "upstream.tar.gz"
        source = staging / "source.tar.gz"
        _download_archive(source_url, upstream)
        normalization = None
        if spec.task_id == "pylint-7080":
            normalization = _omit_pylint_test_symlinks(upstream, source)
        else:
            upstream.rename(source)
        _validate_archive(source, spec.base_commit)
        (staging / "task-input.json").write_bytes(task_input)
        (staging / "reference.patch").write_bytes(reference)
        freeze = {
            **_identity(spec),
            "dataset": DATASET,
            "dataset_row": spec.dataset_row,
            "source_url": source_url,
            "files": {name: _fingerprint(staging / name) for name in PAYLOAD_NAMES},
        }
        if normalization is not None:
            freeze["archive_normalization"] = normalization
        (staging / "freeze.json").write_bytes(_json_bytes(freeze))
        if destination.exists() or destination.is_symlink():
            _check_existing(destination, spec)
        else:
            staging.rename(destination)
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", action="append", choices=tuple(SAMPLES),
                        help="Sample to prepare, repeat for more than one. Defaults to all four.")
    args = parser.parse_args(argv)
    failed = False
    for task_id in dict.fromkeys(args.task or SAMPLES):
        try:
            destination = prepare(SAMPLES[task_id])
        except (OSError, HTTPException, ValueError, tarfile.TarError) as exc:
            print(f"{task_id}: {exc}", file=sys.stderr)
            failed = True
        else:
            print(f"{task_id}: {destination.relative_to(DATA_ROOT.parent).as_posix()}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
