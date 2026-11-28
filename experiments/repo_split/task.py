"""Unified task adapter. Evaluation assets are never copied into the agent workspace."""

from __future__ import annotations

import json
from pathlib import Path

from experiments.repo_split.runtime import apply_runtime_files, prepared_archive, runtime_python, verify_proof
from experiments.repo_split.specs import TOOLSET
from experiments.sphinx_7462.prepare import digest, extract_source, prepare_workspace
from experiments.verified_samples.task import SampleTask
from runtime.fsguard import write_json


class RepoSplitTask(SampleTask):
    toolset = TOOLSET

    def __init__(self, spec, pack, git, python=None):
        pack = Path(pack).resolve()
        self.runtime_home = pack / "runtime" / spec.instance_id
        self.control_dir = pack / "control-only" / spec.group / spec.instance_id
        super().__init__(spec, pack / spec.group / spec.instance_id, python or runtime_python(self.runtime_home), git)
        self.protocol["evaluation_timeout_seconds"] = 300
        self._runtime = verify_proof(self.runtime_home, self.python, self.check_archive(), spec)
        self._source_archive = prepared_archive(self.runtime_home, self.archive, self._runtime)

    def extract_archive(self, workspace):
        self.check_archive()
        extract_source(self._source_archive, workspace)

    def prepare_runtime(self, workspace):
        apply_runtime_files(Path(workspace), self.runtime_home, self._runtime)

    def prepare(self, run_dir):
        run_dir = Path(run_dir)
        workspace = run_dir / "workspace"
        self.check_archive()
        expected = self._runtime["derived_archive_sha256"] or self._runtime["source_sha256"]
        prepare_workspace(self._source_archive, workspace, run_dir / "baseline", self.python, self.git,
                          expected_sha256=expected, skip_env_probe=True)
        write_json(run_dir / "task-input.json", self.task_input)
        return workspace

    def evaluation_assets(self):
        record = json.loads((self.control_dir / "evaluation.json").read_text(encoding="utf-8"))
        self._check_identity(record)
        for label, count in (("FAIL_TO_PASS", self.spec.f2p_count), ("PASS_TO_PASS", self.spec.p2p_count)):
            if not isinstance(record[label], list) or len(record[label]) != count or len(set(record[label])) != count:
                raise ValueError("The control-side test labels changed")
        if set(record["FAIL_TO_PASS"]) & set(record["PASS_TO_PASS"]):
            raise ValueError("Requirement and regression labels overlap")
        for name in ("reference.patch", "test.patch"):
            file = self.control_dir / name
            metadata = record["files"][name]
            if file.stat().st_size != metadata["bytes"] or digest(file) != metadata["sha256"]:
                raise ValueError("A control-side evaluation patch changed")
        return record

    def reference_patch(self, path=None):
        if path is not None:
            raise ValueError("The repository split accepts the frozen reference patch only")
        self.evaluation_assets()
        return self.control_dir / "reference.patch"

    def evaluate(self, run_dir, workspace, patch):
        from .evaluate import evaluate_repo_split
        return evaluate_repo_split(self, Path(run_dir) / "evaluation", patch)
