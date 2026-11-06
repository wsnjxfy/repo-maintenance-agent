"""Preparation tests use synthetic files and never read the real task answer."""

import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

from experiments.sphinx_7462.prepare import (
    BASE_COMMIT, INPUT_FIELDS, REPOSITORY, TASK_ID, manifest, prepare,
)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.git = shutil.which("git")
        self.assertIsNotNone(self.git, "Git is required for the preparation fixture")
        self.record = self.root / "record.json"
        self.problem = "A synthetic maintenance request.\nPreserve this exact text.\n"
        self.row = {
            "instance_id": TASK_ID, "repo": REPOSITORY, "base_commit": BASE_COMMIT,
            "problem_statement": self.problem,
            "patch": "ANSWER_SENTINEL_0192", "test_patch": "HIDDEN_SENTINEL_2831",
            "hints_text": "HINT_SENTINEL_4832", "FAIL_TO_PASS": ["GOLD_TEST_SENTINEL"],
        }
        self.record.write_text(json.dumps(self.row), encoding="utf-8")

    def archive(self, members=None):
        path = self.root / "source.tar.gz"
        entries = members or {
            "source/module.py": b"value = 1\r\n",
            "source/tests/test_module.py": b"def test_example(): assert True\n",
            "source/nested/conftest.py": b"# protected\n",
            "source/requirements-dev.txt": b"pytest==8.3.3\n",
            "source/setup.cfg": b"[tool:pytest]\n",
            "source/TOX.INI": b"# uppercase configuration is protected on Windows\n",
            "source/Checks/TESTS/test_other.py": b"def test_other(): assert True\n",
        }
        with tarfile.open(path, "w:gz") as archive:
            for name, content in entries.items():
                member = tarfile.TarInfo(name)
                if isinstance(content, tarfile.TarInfo):
                    member = content
                    archive.addfile(member)
                else:
                    member.size = len(content)
                    archive.addfile(member, io.BytesIO(content))
        return path

    def invoke(self, archive, **kwargs):
        options = {
            "source_archive": archive, "record_path": self.record,
            "run_dir": self.root / "run", "python": sys.executable, "git": self.git,
            "expected_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "skip_env_probe": True,
        }
        options.update(kwargs)
        return prepare(**options)

    def test_exact_source_and_input_allowlist_without_answer_material(self):
        archive = self.archive()
        result = self.invoke(archive)
        run = self.root / "run"
        original = manifest(run / "controller/base")
        self.assertEqual(original, manifest(run / "workspace"))
        self.assertEqual((run / "workspace/module.py").read_bytes(), b"value = 1\r\n")
        task_input = json.loads((run / "input/task-input.json").read_text(encoding="utf-8"))
        self.assertEqual(list(task_input), list(INPUT_FIELDS))
        self.assertEqual(task_input["problem_statement"], self.problem)
        for path in (run / "input").iterdir():
            content = path.read_text(encoding="utf-8")
            self.assertNotIn("SENTINEL", content)
        state = json.loads((run / "controller/state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["phase"], "prepared")
        self.assertEqual(state["action_count"], 0)
        self.assertIsNone(state["started_at"])
        self.assertFalse(result["solving_agent_started"])
        self.assertEqual((run / "controller/actions.jsonl").read_bytes(), b"")
        config = json.loads((run / "controller/config.json").read_text(encoding="utf-8"))
        self.assertEqual(set(config["protected_paths"]), {
            "tests/test_module.py", "nested/conftest.py", "requirements-dev.txt", "setup.cfg",
            "TOX.INI", "Checks/TESTS/test_other.py",
        })
        history = subprocess.check_output([self.git, "-C", str(run / "workspace"), "rev-list", "--all"])
        self.assertEqual(history, b"")
        remotes = subprocess.check_output([self.git, "-C", str(run / "workspace"), "remote"])
        self.assertEqual(remotes, b"")

    def test_existing_run_is_never_reset_or_overwritten(self):
        archive = self.archive()
        self.invoke(archive)
        file = self.root / "run/workspace/module.py"
        file.write_text("do not erase this\n", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            self.invoke(archive)
        self.assertEqual(file.read_text(encoding="utf-8"), "do not erase this\n")

    def test_archive_hash_mismatch_rejected_before_creating_run(self):
        archive = self.archive()
        with self.assertRaisesRegex(ValueError, "SHA256"):
            self.invoke(archive, expected_sha256="0" * 64)
        self.assertFalse((self.root / "run").exists())

    def test_unsafe_archives_rejected_without_partial_workspace(self):
        link = tarfile.TarInfo("source/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        examples = [
            {"source/../outside": b"escape"},
            {"source/C:/outside": b"escape"},
            {"source/folder\\outside": b"escape"},
            {"source/link": link},
            {"source/a.py": b"a", "source/A.py": b"b"},
            {"source/nul.txt": b"device"},
            {"source/.GIT/config": b"# supplied git metadata"},
        ]
        for members in examples:
            with self.subTest(members=list(members)):
                archive = self.archive(members)
                with self.assertRaises(ValueError):
                    self.invoke(archive)
                self.assertFalse((self.root / "run").exists())

    def test_wrong_task_record_rejected_before_creating_run(self):
        archive = self.archive()
        self.row["base_commit"] = "not-the-selected-base"
        self.record.write_text(json.dumps(self.row), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "selected task"):
            self.invoke(archive)
        self.assertFalse((self.root / "run").exists())

    def test_source_change_during_preflight_marks_run_failed(self):
        archive = self.archive()

        def mutation_during_probe(python, workspace):
            (workspace / "module.py").write_text("changed by a bad import\n", encoding="utf-8")
            return {"status": "passed"}

        with mock.patch("experiments.sphinx_7462.prepare.probe_environment", side_effect=mutation_during_probe):
            with self.assertRaisesRegex(ValueError, "changed original source"):
                self.invoke(archive, skip_env_probe=False)
        controller = self.root / "run/controller"
        state = json.loads((controller / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["phase"], "preparation_failed")
        self.assertFalse((controller / "preparation-result.json").exists())
        failure = json.loads((controller / "preparation-failure.json").read_text(encoding="utf-8"))
        self.assertEqual(failure["status"], "preparation_failed")


if __name__ == "__main__":
    unittest.main()
