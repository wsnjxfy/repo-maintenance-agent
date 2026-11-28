"""Prepare and verify Windows-compatible local runtimes without running tests."""

from __future__ import annotations

from copy import copy
import json
from pathlib import Path, PurePosixPath
import posixpath
import shutil
import subprocess
import tarfile
import tempfile

from experiments.sphinx_7462.prepare import checked_members, digest, extract_source
from runtime.fsguard import safe_path, write_json
from runtime.process import worker_env

RUNTIME_PROOF = "runtime-proof.json"

_PROBE = """
import importlib
import json
import os
from pathlib import Path
import sys
source = Path(sys.argv[1]).resolve()
package = sys.argv[2]
dll_paths = [os.add_dll_directory(str(path)) for path in source.glob("*.libs")
             if path.is_dir()] if hasattr(os, "add_dll_directory") else []
local = importlib.import_module(package)
location = getattr(local, "__file__", None)
if location is None:
    location = next(iter(local.__path__))
origin = Path(location).resolve()
expected = source / package
if origin != expected.with_suffix(".py") and not origin.is_relative_to(expected):
    raise RuntimeError("Repository package resolved outside the extracted source copy")
import pytest
print(json.dumps({"source_import": str(origin.relative_to(source)), "pytest": pytest.__version__,
                  "python": ".".join(map(str, sys.version_info[:3]))}))
"""


def runtime_python(home):
    import os
    return Path(home) / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")


def _normalize_archive(source, destination):
    """Copy internal symlink targets into a new archive, without following filesystem links."""
    with tarfile.open(source, "r:gz") as original, tarfile.open(destination, "w:gz") as out:
        members = original.getmembers()
        by_name = {m.name: m for m in members}
        if len(by_name) != len(members):
            raise ValueError("Duplicate archive members")
        for member in members:
            if member.issym():
                name = member.linkname
                if not name or name.startswith("/") or "\\" in name or ":" in name:
                    raise ValueError("An archive link has an unsafe target")
                target = posixpath.normpath(posixpath.join(posixpath.dirname(member.name), name))
                if PurePosixPath(target).parts[:1] != PurePosixPath(member.name).parts[:1]:
                    raise ValueError("An archive link escapes the repository")
                provider = by_name.get(target)
                if provider is None or not provider.isfile() or provider.issparse():
                    raise ValueError("An archive link must target an internal regular file")
                materialized = copy(provider)
                materialized.name = member.name
                with original.extractfile(provider) as content:
                    out.addfile(materialized, content)
            elif member.isdir():
                out.addfile(member)
            elif member.isfile() and not member.issparse():
                with original.extractfile(member) as content:
                    out.addfile(member, content)
            else:
                raise ValueError("Unsupported archive member type")
    with tarfile.open(destination, "r:gz") as normalized:
        checked_members(normalized)


def prepared_archive(home, raw, proof):
    derived = proof.get("derived_archive_sha256")
    if derived:
        archive = Path(home) / "source-normalized.tar.gz"
        if digest(archive) != derived:
            raise ValueError("The normalized source archive changed")
        return archive
    return raw


def _artifact_paths(site, spec):
    roots = [site / spec.package] + list(site.glob({"sklearn": "scikit_learn", "astropy": "astropy",
                                                    "matplotlib": "matplotlib"}.get(spec.package, spec.package) + ".libs"))
    if spec.native:
        for root in roots:
            if root.is_dir():
                yield from (p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in {".pyd", ".dll", ".so"})
    extras = {
        "pytest": ("_pytest/_version.py",),
        "astropy": ("astropy/version.py", "astropy/_compiler.py", "astropy/cython_version.py"),
        "matplotlib": ("matplotlib/_version.py",),
        "pdm": ("pdm/_version.py", "pdm/__version__.py"),
    }
    yield from (site / rel for rel in extras.get(spec.package, ()) if (site / rel).is_file())


def apply_runtime_files(workspace, home, proof):
    for rel, expected in proof["artifacts"].items():
        source = safe_path(Path(home) / "artifacts", rel)
        if digest(source) != expected:
            raise ValueError("A generated runtime file changed")
        target = safe_path(workspace, rel)
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


def verify_proof(home, python, archive_sha, spec):
    proof = json.loads((home / RUNTIME_PROOF).read_text(encoding="utf-8"))
    if (proof["instance_id"] != spec.instance_id or proof["source_sha256"] != archive_sha
            or proof["python_path"] != str(python.absolute())
            or proof["python_sha256"] != digest(python)):
        raise ValueError("The local runtime proof no longer matches the source and interpreter")
    return proof


def probe_runtime(spec, runtime_home, instance_home):
    """Import the actual baseline package in a temporary source tree. Do not run or collect tests."""
    runtime_home = Path(runtime_home).resolve()
    raw = instance_home / "source.tar.gz"
    frozen = json.loads((instance_home / "freeze.json").read_text(encoding="utf-8"))
    source_sha = digest(raw)
    if frozen["files"]["source.tar.gz"]["sha256"] != source_sha:
        raise ValueError("The original source archive does not match its freeze")
    python = runtime_python(runtime_home)
    if not python.is_file():
        raise FileNotFoundError("The dedicated Python interpreter is missing")
    with tarfile.open(raw, "r:gz") as archive:
        has_links = any(m.issym() for m in archive.getmembers())
    archive = raw
    derived = None
    if has_links:
        archive = runtime_home / "source-normalized.tar.gz"
        if not archive.is_file():
            staging = archive.with_suffix(".tmp")
            _normalize_archive(raw, staging)
            staging.replace(archive)
        derived = digest(archive)
    with tempfile.TemporaryDirectory(prefix="hj10-", dir=runtime_home) as temp:
        workspace = Path(temp) / "work"
        extract_source(archive, workspace)
        path = (workspace / spec.source_dir).resolve()
        env = worker_env(path, python)
        env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", HOME=temp, USERPROFILE=temp,
                   MPLCONFIGDIR=temp, XDG_CACHE_HOME=temp)
        site_path = subprocess.check_output(
            [str(python), "-B", "-c", "import sysconfig; print(sysconfig.get_path('platlib'))"],
            cwd=runtime_home, env=worker_env(runtime_home, python), encoding="utf-8", timeout=30,
        ).strip()
        site = Path(site_path).resolve()
        if not site.is_relative_to(runtime_home / ".venv"):
            raise ValueError("Python dependencies are outside the dedicated environment")
        artifacts = {}
        for item in _artifact_paths(site, spec):
            rel = item.relative_to(site).as_posix()
            # Per-package binary libraries next to the package are installed at the source root.
            target_rel = PurePosixPath(spec.source_dir, rel).as_posix()
            if (workspace / target_rel).exists():
                continue
            stored = runtime_home / "artifacts" / target_rel
            stored.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item, stored)
            artifacts[target_rel] = digest(stored)
        if spec.native and not any(name.endswith((".pyd", ".so")) for name in artifacts):
            raise ValueError("The native build artifacts are missing")
        apply_runtime_files(workspace, runtime_home, {"artifacts": artifacts})
        completed = subprocess.run([str(python), "-B", "-c", _PROBE, str(path), spec.package],
                                   cwd=workspace, env=env, capture_output=True, encoding="utf-8",
                                   errors="replace", timeout=75)
        if completed.returncode:
            raise RuntimeError("Runtime import probe failed:\n" + completed.stderr[-1600:])
        proof = json.loads(completed.stdout.splitlines()[-1])
    proof.update(instance_id=spec.instance_id, source_sha256=source_sha, python_path=str(python.absolute()),
                 python_sha256=digest(python), derived_archive_sha256=derived, artifacts=artifacts,
                 native_wheel_artifacts=spec.native)
    write_json(runtime_home / RUNTIME_PROOF, proof)
    return proof


def main(argv=None):
    """Install the tracked dependency recipe and verify source import only."""
    import argparse

    from experiments.repo_split.specs import PACK_NAME, load_specs

    specs = {spec.task_id: spec for spec in load_specs()}
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--task", required=True, choices=specs)
    parser.add_argument("--pack-dir", type=Path, default=Path(__file__).resolve().parents[2] / "data" / PACK_NAME)
    parser.add_argument("--python", help="Path to the required base Python, or a uv interpreter selector")
    parser.add_argument("--probe-only", action="store_true", help="Reuse an existing dedicated environment")
    args = parser.parse_args(argv)
    spec = specs[args.task]
    pack = args.pack_dir.resolve()
    instance = pack / spec.group / spec.instance_id
    runtime = pack / "runtime" / spec.instance_id

    recipes = json.loads(Path(__file__).with_name("runtime-recipes.json").read_text(encoding="utf-8"))
    recipe = recipes[spec.instance_id]
    try:
        frozen = json.loads((instance / "freeze.json").read_text(encoding="utf-8"))
        if frozen["base_commit"] != spec.base_commit or frozen["instance_id"] != spec.instance_id:
            raise ValueError("The local source snapshot does not match the published catalog")
        if not (instance / "source.tar.gz").is_file():
            raise FileNotFoundError("Run the data preparation command first")
        python = runtime_python(runtime)
        if not args.probe_only:
            uv = shutil.which("uv")
            if uv is None:
                raise FileNotFoundError("uv is required to install the published runtime recipe")
            runtime.mkdir(parents=True, exist_ok=True)
            pins = runtime / "published-requirements.txt"
            pins.write_text("\n".join(recipe["requirements"]) + "\n", encoding="utf-8")
            environment = worker_env(runtime, python)
            if not python.is_file():
                subprocess.run([uv, "venv", "--no-python-downloads", "--python", args.python or recipe["python"],
                                str(runtime / ".venv")], env=environment, check=True)
            version = subprocess.check_output(
                [str(python), "-B", "-c", "import sys; print('.'.join(map(str, sys.version_info[:2])))"],
                cwd=runtime, env=environment, encoding="utf-8", timeout=30,
            ).strip()
            if version != recipe["python"]:
                raise ValueError(f"This runtime recipe requires Python {recipe['python']}")
            subprocess.run([uv, "pip", "install", "--python", str(python), "-r", str(pins)],
                           env=environment, check=True)
        proof = probe_runtime(spec, runtime, instance)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"{type(error).__name__}: {error}\n")
    print(json.dumps({"task": args.task, "python": proof["python"], "source_import": proof["source_import"],
                      "pytest": proof["pytest"], "native_wheel_artifacts": spec.native}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
