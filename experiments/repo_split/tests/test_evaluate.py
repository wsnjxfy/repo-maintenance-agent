import io
import json
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest

from experiments.repo_split.evaluate import evaluate_repo_split
from experiments.repo_split.runtime import _normalize_archive
from experiments.sphinx_7462.prepare import extract_source
from runtime.fsguard import sha256_bytes


class IndependentEvaluationTests(unittest.TestCase):
    def test_internal_archive_link_becomes_regular_file(self):
        with tempfile.TemporaryDirectory(prefix="hj10-links-") as temp:
            base = Path(temp)
            archive = base / "original.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                top = tarfile.TarInfo("fixture")
                top.type = tarfile.DIRTYPE
                tar.addfile(top)
                content = b"VALUE = 1\n"
                member = tarfile.TarInfo("fixture/fixture_source.py")
                member.size = len(content)
                tar.addfile(member, io.BytesIO(content))
                link = tarfile.TarInfo("fixture/alias_fixture_source.py")
                link.type = tarfile.SYMTYPE
                link.linkname = "fixture_source.py"
                tar.addfile(link)
            normalized = base / "normalized.tar.gz"
            _normalize_archive(archive, normalized)
            extract_source(normalized, base / "copy")
            alias = base / "copy" / "alias_fixture_source.py"
            self.assertFalse(alias.is_symlink())
            self.assertEqual(alias.read_bytes(), content)

    def test_frozen_labels_execute_only_in_evaluation_copy(self):
        git = shutil.which("git")
        self.assertIsNotNone(git)
        with tempfile.TemporaryDirectory(prefix="hj10-independent-") as temp:
            base = Path(temp)
            agent_copy = base / "solver-workspace"
            (agent_copy / "fixture_package").mkdir(parents=True)
            (agent_copy / "fixture_package" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
            control = base / "control-only"
            control.mkdir()
            lines = [
                "import fixture_package",
                "",
                "def test_requirement():",
                "    assert fixture_package.VALUE == 2",
                "",
                "def test_regression():",
                "    assert isinstance(fixture_package.VALUE, int)",
            ]
            patch = ("\n".join([
                "diff --git a/tests/test_frozen.py b/tests/test_frozen.py",
                "new file mode 100644",
                "--- /dev/null",
                "+++ b/tests/test_frozen.py",
                f"@@ -0,0 +1,{len(lines)} @@",
                *("+" + line for line in lines),
            ]) + "\n").encode()
            (control / "test.patch").write_bytes(patch)
            spec = SimpleNamespace(instance_id="fixture", repo="test/fixture", base_commit="0" * 40,
                                   package="fixture_package", f2p_count=1, p2p_count=1,
                                   source_dir=".", native=False)
            record = {
                "instance_id": spec.instance_id,
                "repo": spec.repo,
                "base_commit": spec.base_commit,
                "FAIL_TO_PASS": ["tests/test_frozen.py::test_requirement"],
                "PASS_TO_PASS": ["tests/test_frozen.py::test_regression"],
                "files": {"test.patch": {"bytes": len(patch), "sha256": sha256_bytes(patch)}},
            }
            (control / "evaluation.json").write_text(json.dumps(record), encoding="utf-8")
            task = SimpleNamespace(
                spec=spec, control_dir=control, check_archive=lambda: "synthetic-source",
                extract_archive=lambda path: shutil.copytree(agent_copy, path),
                prepare_runtime=lambda path: None, python=Path(sys.executable), git=git,
                protocol={"evaluation_timeout_seconds": 30},
            )
            result = evaluate_repo_split(task, base / "evaluation", None)
            self.assertTrue(result["execution_valid"], result["errors"])
            self.assertEqual(result["metrics"]["fail_to_pass"]["passed"], 0)
            self.assertEqual(result["metrics"]["pass_to_pass"]["passed"], 1)
            self.assertFalse((agent_copy / "tests").exists())
