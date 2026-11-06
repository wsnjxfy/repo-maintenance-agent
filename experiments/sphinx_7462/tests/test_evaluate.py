"""Independent evaluator checks on synthetic, answer-free repositories only."""

import difflib
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
import xml.etree.ElementTree as ET

from experiments.sphinx_7462.evaluate import evaluate
from experiments.sphinx_7462.grading import grade_run
from experiments.sphinx_7462.prepare import BASE_COMMIT, REPOSITORY, TASK_ID


REQUIREMENT = "tests/test_example.py::test_requirement"
REGRESSION = "tests/test_example.py::test_regression"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def patch_for(name, old, new):
    mode = "new file mode 100644\n" if not old else "deleted file mode 100644\n" if not new else ""
    return "diff --git a/{0} b/{0}\n".format(name) + mode + "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile="a/" + name if old else "/dev/null",
            tofile="b/" + name if new else "/dev/null",
        )
    )


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


class GradingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hj10-grading-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def evidence(self, cases, log_cases=None):
        suites = ET.Element("testsuites")
        suite = ET.SubElement(suites, "testsuite", name="pytest", tests=str(len(cases)))
        for nodeid, status in cases:
            path, name = nodeid.split("::", 1)
            case = ET.SubElement(suite, "testcase", classname=path[:-3].replace("/", "."), name=name)
            if status in {"FAILED", "ERROR", "SKIPPED"}:
                kind = {"FAILED": "failure", "ERROR": "error", "SKIPPED": "skipped"}[status]
                ET.SubElement(case, kind, message="synthetic " + status.lower()).text = "synthetic evidence"
        xml = self.root / "result.xml"
        ET.ElementTree(suites).write(str(xml), encoding="utf-8", xml_declaration=True)
        entries = cases if log_cases is None else log_cases
        # pytest normally prints each result in verbose progress and again in -rA.
        lines = ["collected %d items" % len(cases)]
        for nodeid, status in entries:
            lines += ["%s %s [100%%]" % (nodeid, status), "%s %s" % (status, nodeid)]
        log = self.root / "pytest.log"
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return xml, log

    def grade(self, cases, f2p=None, p2p=None, returncode=0, timed_out=False, log_cases=None):
        xml, log = self.evidence(cases, log_cases)
        return grade_run(xml, log, [REQUIREMENT] if f2p is None else f2p,
                         [REGRESSION] if p2p is None else p2p, returncode, timed_out)

    def test_complete_parameter_names_and_truncated_labels_preserve_spaces(self):
        names = ["tests/test_example.py::test_parse[a + b-a + b]",
                 "tests/test_example.py::test_parse[{'x': 1, 'y': 2}-value]"]
        prefix = "tests/test_example.py::test_parse["
        result = self.grade([(name, "PASSED") for name in names] + [(REGRESSION, "PASSED")],
                            f2p=[prefix])
        self.assertTrue(result["execution_valid"], result)
        self.assertTrue(result["resolved"], result)
        self.assertEqual(result["f2p_passed"], 1)
        self.assertEqual(set(result["f2p_mapping"][0]["matches"]), set(names))
        self.assertTrue(set(names).issubset(result["actual_statuses"]))

    def test_exact_identifier_wins_over_other_prefix_candidates(self):
        label = "tests/test_example.py::test_parse[a"
        result = self.grade([(label, "PASSED"), (label + " + b]", "FAILED"), (REGRESSION, "PASSED")],
                            f2p=[label], returncode=1)
        self.assertTrue(result["f2p_mapping"][0]["passed"], result)
        self.assertEqual(result["f2p_mapping"][0]["matches"], [label])
        self.assertFalse(result["resolved"], "An unexpected failure must still prevent success")

    def test_closed_parameter_label_does_not_use_prefix_fallback(self):
        label = "tests/test_example.py::test_parse[a]"
        result = self.grade([(label + "extra", "PASSED"), (REGRESSION, "PASSED")], f2p=[label])
        self.assertFalse(result["resolved"], result)
        self.assertFalse(result["f2p_mapping"][0]["passed"], result)
        self.assertEqual(result["f2p_mapping"][0]["matches"], [])

    def test_truncated_label_requires_every_actual_match_to_pass(self):
        prefix = "tests/test_example.py::test_parse["
        for bad_status in ["FAILED", "SKIPPED", "ERROR"]:
            with self.subTest(status=bad_status):
                result = self.grade([(prefix + "first]", "PASSED"), (prefix + "second]", bad_status),
                                     (REGRESSION, "PASSED")], f2p=[prefix], returncode=1)
                self.assertFalse(result["resolved"], result)
                self.assertEqual(result["f2p_passed"], 0)

    def test_normal_test_failures_are_valid_execution_but_not_resolution(self):
        result = self.grade([(REQUIREMENT, "FAILED"), (REGRESSION, "PASSED")], returncode=1)
        self.assertTrue(result["execution_valid"], result)
        self.assertFalse(result["resolved"], result)
        self.assertEqual((result["f2p_passed"], result["p2p_passed"]), (0, 1))

    def test_missing_skipped_empty_and_timeout_cannot_be_success(self):
        examples = [
            ([(REGRESSION, "PASSED")], 0, False),
            ([(REQUIREMENT, "SKIPPED"), (REGRESSION, "PASSED")], 0, False),
            ([], 5, False),
            ([(REQUIREMENT, "PASSED"), (REGRESSION, "PASSED")], 0, True),
            ([(REQUIREMENT, "PASSED"), (REGRESSION, "PASSED")], 2, False),
        ]
        for cases, code, timed_out in examples:
            with self.subTest(code=code, timed_out=timed_out, cases=cases):
                result = self.grade(cases, returncode=code, timed_out=timed_out)
                self.assertFalse(result["resolved"], result)

    def test_duplicate_junit_and_conflicting_log_are_invalid(self):
        duplicate = self.grade([(REQUIREMENT, "PASSED"), (REQUIREMENT, "PASSED"), (REGRESSION, "PASSED")])
        self.assertFalse(duplicate["execution_valid"], duplicate)
        self.assertFalse(duplicate["resolved"], duplicate)
        conflict = self.grade([(REQUIREMENT, "PASSED"), (REGRESSION, "PASSED")],
                              log_cases=[(REQUIREMENT, "FAILED"), (REGRESSION, "PASSED")])
        self.assertFalse(conflict["execution_valid"], conflict)
        self.assertTrue(conflict["validation_errors"], conflict)
        self.assertFalse(conflict["resolved"], conflict)

    def test_missing_or_malformed_junit_returns_invalid_evidence(self):
        _, log = self.evidence([(REQUIREMENT, "PASSED"), (REGRESSION, "PASSED")])
        for body in [None, "<broken"]:
            with self.subTest(body=body):
                xml = self.root / "unavailable.xml"
                if body is not None:
                    xml.write_text(body, encoding="utf-8")
                result = grade_run(xml, log, [REQUIREMENT], [REGRESSION], 0)
                self.assertFalse(result["execution_valid"], result)
                self.assertFalse(result["resolved"], result)

    def test_collection_count_mismatch_cannot_hide_an_unrecorded_case(self):
        xml, log = self.evidence([(REQUIREMENT, "PASSED"), (REGRESSION, "PASSED")])
        log.write_text(log.read_text(encoding="utf-8").replace("collected 2 items", "collected 3 items"), encoding="utf-8")
        result = grade_run(xml, log, [REQUIREMENT], [REGRESSION], 0)
        self.assertFalse(result["execution_valid"], result)
        self.assertFalse(result["resolved"], result)


class EvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.git = shutil.which("git")
        if not cls.git:
            raise unittest.SkipTest("Git is required for real synthetic patch application")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hj10-evaluate-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.counter = 0

    def fixture(self, replacement_test=None, official_patch=None):
        self.counter += 1
        folder = self.root / ("fixture-%d" % self.counter)
        folder.mkdir()
        original_test = "from module import VALUE\n\ndef test_regression():\n    assert VALUE >= 1\n"
        new_test = original_test + "\ndef test_requirement():\n    assert VALUE == 2\n" if replacement_test is None else replacement_test
        test_patch = patch_for("tests/test_example.py", original_test, new_test) if official_patch is None else official_patch
        candidate = patch_for("module.py", "VALUE = 1\n", "VALUE = 2\n")
        files = {"module.py": b"VALUE = 1\n", "tests/test_example.py": original_test.encode("utf-8"),
                 "pytest.ini": b"[pytest]\n"}
        archive_path = folder / "source.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            for name, body in files.items():
                item = tarfile.TarInfo("source/" + name)
                item.size = len(body)
                archive.addfile(item, io.BytesIO(body))
        row = {"instance_id": TASK_ID, "repo": REPOSITORY, "base_commit": BASE_COMMIT,
               "problem_statement": "Synthetic request; no Sphinx task content.",
               "patch": candidate, "test_patch": test_patch, "hints_text": "ANSWER_SENTINEL_UNUSED",
               "FAIL_TO_PASS": json.dumps([REQUIREMENT]), "PASS_TO_PASS": [REGRESSION]}
        record = folder / "record.json"
        write_json(record, row)
        review = folder / "review.json"
        write_json(review, {"instance_id": TASK_ID, "repo": REPOSITORY, "base_commit": BASE_COMMIT,
                            "reference_summary": {"test_patch_sha256_utf8": sha(test_patch.encode("utf-8")),
                                                  "patch_sha256_utf8": sha(candidate.encode("utf-8"))},
                            "evaluation": {"FAIL_TO_PASS": [REQUIREMENT], "PASS_TO_PASS": [REGRESSION]}})
        candidate_path = folder / "candidate.patch"
        candidate_path.write_bytes(candidate.encode("utf-8"))
        options = {"source_archive": archive_path, "record_path": record, "review_manifest": review,
                   "output_dir": folder / "evaluation", "python": sys.executable, "git": self.git,
                   "expected_source_sha256": sha(archive_path.read_bytes()), "skip_env_probe": True}
        return folder, options, candidate_path

    def test_baseline_fails_requirement_and_explicit_candidate_passes_same_tests(self):
        folder, options, candidate = self.fixture()
        baseline = evaluate(**options)
        self.assertEqual(baseline["status"], "completed", baseline)
        self.assertFalse(baseline["resolved"], baseline)
        self.assertEqual((baseline["grading"]["f2p_passed"], baseline["grading"]["p2p_passed"]), (0, 1))
        fixed_options = dict(options, output_dir=folder / "fixed", candidate_patch=candidate)
        reference = evaluate(**fixed_options)
        self.assertEqual(reference["status"], "completed", reference)
        self.assertTrue(reference["resolved"], reference)
        self.assertEqual((reference["grading"]["f2p_passed"], reference["grading"]["p2p_passed"]), (1, 1))
        # The record contains a reference patch, but baseline must not apply it implicitly.
        self.assertEqual((options["output_dir"] / "workspace/module.py").read_bytes(), b"VALUE = 1\n")
        self.assertEqual((folder / "fixed/workspace/module.py").read_bytes(), b"VALUE = 2\n")

    def test_existing_output_is_never_overwritten(self):
        _, options, _ = self.fixture()
        options["output_dir"].mkdir()
        sentinel = options["output_dir"] / "sentinel.txt"
        sentinel.write_text("preserve existing evidence", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            evaluate(**options)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve existing evidence")

    def test_empty_candidate_is_valid_but_does_not_fix_requirement(self):
        _, options, candidate = self.fixture()
        candidate.write_bytes(b"")
        result = evaluate(**dict(options, candidate_patch=candidate))
        self.assertEqual(result["status"], "completed", result)
        self.assertFalse(result["resolved"], result)
        self.assertTrue(result["grading"]["execution_valid"], result)
        self.assertEqual((result["grading"]["f2p_passed"], result["grading"]["p2p_passed"]), (0, 1))

    def test_bad_candidate_and_conflicting_test_patch_stop_before_pytest(self):
        for stage in ["candidate", "test_patch"]:
            with self.subTest(stage=stage):
                folder, options, candidate = self.fixture()
                if stage == "candidate":
                    candidate.write_bytes(patch_for("module.py", "VALUE = 999\n", "VALUE = 2\n").encode("utf-8"))
                    options["candidate_patch"] = candidate
                else:
                    row = json.loads(options["record_path"].read_text(encoding="utf-8"))
                    row["test_patch"] = patch_for("tests/test_example.py", "not the original content\n", "def test_changed(): pass\n")
                    write_json(options["record_path"], row)
                    review = json.loads(options["review_manifest"].read_text(encoding="utf-8"))
                    review["reference_summary"]["test_patch_sha256_utf8"] = sha(row["test_patch"].encode("utf-8"))
                    write_json(options["review_manifest"], review)
                    options["candidate_patch"] = candidate
                result = evaluate(**options)
                self.assertEqual(result["status"], "error", result)
                self.assertFalse(result["resolved"], result)
                self.assertTrue((options["output_dir"] / "evaluation.json").exists())
                self.assertFalse(list(options["output_dir"].rglob("*.xml")), "Patch failure must not run pytest")
                if stage == "candidate":
                    self.assertEqual((options["output_dir"] / "workspace/module.py").read_bytes(), b"VALUE = 1\n")

    def test_candidate_cannot_modify_tests_or_configuration(self):
        for path, old, new in [("pytest.ini", "[pytest]\n", "[pytest]\naddopts = --ignore=tests\n"),
                               ("tests/test_extra.py", "", "def test_fake(): pass\n")]:
            with self.subTest(path=path):
                _, options, candidate = self.fixture()
                patch = patch_for(path, old, new)
                candidate.write_bytes(patch.encode("utf-8"))
                result = evaluate(**dict(options, candidate_patch=candidate))
                self.assertEqual(result["status"], "error", result)
                self.assertFalse(result["resolved"], result)
                self.assertFalse(list(options["output_dir"].rglob("*.xml")))
                self.assertTrue(any(patch.encode("utf-8") == p.read_bytes() for p in options["output_dir"].rglob("*.patch")),
                                "Rejected candidate bytes must remain available as evidence")

    def test_skipped_zero_collection_load_error_and_timeout_cannot_pass(self):
        examples = [
            ("import pytest\ndef test_regression(): pass\ndef test_requirement(): pytest.skip('synthetic')\n", 90),
            ("NOT_A_TEST = 1\n", 90),
            ("def broken(\n", 90),
            ("import time\ndef test_regression(): pass\ndef test_requirement(): time.sleep(60)\n", 3),
        ]
        for test_source, timeout in examples:
            with self.subTest(test_source=test_source):
                _, options, candidate = self.fixture(replacement_test=test_source)
                result = evaluate(**dict(options, candidate_patch=candidate, timeout_seconds=timeout))
                self.assertFalse(result["resolved"], result)
                self.assertTrue((options["output_dir"] / "evaluation.json").exists())
                self.assertIn("grading", result)
                self.assertFalse(result["grading"]["resolved"], result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
