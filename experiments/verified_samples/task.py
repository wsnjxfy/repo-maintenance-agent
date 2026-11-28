"""Shared Task adapter for the four public Verified development examples."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

from contracts.visibility import Visibility
from experiments.sphinx_7462.prepare import INPUT_FIELDS, digest, prepare_workspace
from runtime.fsguard import write_json


class SampleTask:
    toolset = "repo-v1"
    max_steps = 4

    def __init__(self, spec, data_dir, python, git):
        self.spec = spec
        self.task_id = spec.instance_id
        self.run_prefix = spec.task_id
        self.data_dir = Path(data_dir).resolve()
        # Retain the original executable path so a virtualenv keeps its site-packages.
        self.python = Path(python).absolute()
        self.git = shutil.which(str(git)) or str(git)
        if not self.python.is_file():
            raise FileNotFoundError(f"Sample Python executable not found: {self.python}")
        if not shutil.which(self.git) and not Path(self.git).is_file():
            raise FileNotFoundError(f"Sample Git executable not found: {self.git}")
        self.protocol = {"max_actions": 4, "wall_time_seconds": 120, "test_timeout_seconds": 90}
        self.visibility = Visibility()

        self.archive = self.data_dir / "source.tar.gz"
        freeze_bytes = (self.data_dir / "freeze.json").read_text(encoding="utf-8")
        freeze = json.loads(freeze_bytes)
        self._check_identity(freeze)
        self._pins = freeze.get("files")
        if not isinstance(self._pins, dict):
            raise ValueError("The sample freeze has no file hashes")

        self.input_path = self.data_dir / "task-input.json"
        self._check_file(self.input_path, "task-input.json")
        self.task_input = json.loads(self.input_path.read_text(encoding="utf-8"))
        self._check_identity(self.task_input)
        if set(self.task_input) != set(INPUT_FIELDS):
            raise ValueError("The task input must contain only the four public fields")
        self.problem = self.task_input["problem_statement"]
        if not isinstance(self.problem, str) or not self.problem.strip():
            raise ValueError("The public task statement is empty")

    def _check_identity(self, document):
        if not isinstance(document, dict) or (
            document.get("instance_id"), document.get("repo"), document.get("base_commit")
        ) != (self.spec.instance_id, self.spec.repo, self.spec.base_commit):
            raise ValueError("The sample assets do not match the registered instance and base")

    def _check_file(self, path, filename):
        metadata = self._pins.get(filename, {})
        if not isinstance(metadata, dict):
            raise ValueError(f"Invalid frozen file metadata: {filename}")
        checksum = metadata.get("sha256")
        size = metadata.get("bytes")
        if (not isinstance(checksum, str) or len(checksum) != 64
                or type(size) is not int or size <= 0):
            raise ValueError(f"Invalid frozen file metadata: {filename}")
        if path.stat().st_size != size or digest(path) != checksum:
            raise ValueError(f"Sample file does not match its frozen SHA-256: {filename}")
        return checksum

    def check_archive(self):
        return self._check_file(self.archive, "source.tar.gz")

    def reference_patch(self, path=None):
        """Use the frozen dataset reference only in an explicit reference run."""
        file = Path(path) if path is not None else self.data_dir / "reference.patch"
        self._check_file(file, "reference.patch")
        return file

    def prepare(self, run_dir):
        source_sha = self.check_archive()
        run_dir = Path(run_dir)
        workspace = run_dir / "workspace"
        # This helper's optional probe imports Sphinx. The sample worker instead
        # verifies its own package inside the independent evaluation copy.
        prepare_workspace(self.archive, workspace, run_dir / "baseline", self.python, self.git,
                          expected_sha256=source_sha, skip_env_probe=True)
        write_json(run_dir / "task-input.json", self.task_input)
        return workspace

    def evaluate(self, run_dir, workspace, patch):
        from .evaluate import evaluate_sample

        return evaluate_sample(self, Path(run_dir) / "evaluation", patch)
