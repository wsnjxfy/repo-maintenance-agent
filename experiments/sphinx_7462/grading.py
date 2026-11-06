"""Strict, evidence-checked grading for serial pytest development evaluations.

JUnit supplies complete case identities.  ``-rA -vv`` stdout is independent
corroborating evidence, not a source of silently truncated parameter IDs.
The prefix fallback follows the limited Verified compatibility rule: exact
matches win; only labels with an unclosed ``[`` may match multiple cases.
Unlike upstream's permissive XFAIL/SKIPPED treatment, every match must PASSED.
No task labels or answer patches are embedded in this module.
"""

from collections import Counter
from pathlib import Path, PurePosixPath
import re
import xml.etree.ElementTree as ET


STATUSES = {"PASSED", "FAILED", "SKIPPED", "ERROR", "XFAIL", "XPASS"}
_STATUS_PATTERN = "|".join(sorted(STATUSES, key=len, reverse=True))
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_PROGRESS = re.compile(r"\s*\[\s*\d+%\]\s*$")


def _normalise(value):
    """Match official pytest-v2 whitespace normalisation without losing words."""
    return " ".join(value.split())


def _strip_failure_reason(value):
    # A parameter itself may contain ' - '.  Only strip a summary suffix after
    # the parameter brackets close, preserving e.g. test_expr[a - b].
    depth = 0
    for index, char in enumerate(value):
        if char == "[":
            depth += 1
        elif char == "]":
            depth = max(0, depth - 1)
        elif depth == 0 and value.startswith(" - ", index):
            return value[:index]
    return value


def parse_pytest_log(text):
    """Return a full-name status map and conflicts from serial ``-rA -vv`` logs.

    Both progress lines and short-summary lines are accepted.  Repeated reports
    of the same status agree; incompatible repeats are recorded rather than
    letting a later line silently turn failure into success.
    """
    statuses = {}
    conflicts = []
    for raw in text.splitlines():
        line = _ANSI.sub("", raw).strip()
        line = _PROGRESS.sub("", line).rstrip()
        first = re.match(r"^(" + _STATUS_PATTERN + r")\s+(.+)$", line)
        if first:
            status, name = first.groups()
            if status in {"SKIPPED", "XFAIL"} and re.match(r"^\[\d+\](?:\s|$)", name):
                continue  # SKIPPED [1] path:line: reason is not a case identity.
            if status in {"FAILED", "ERROR", "XFAIL", "XPASS"}:
                name = _strip_failure_reason(name)
        else:
            last = re.match(r"^(.+?)\s+(" + _STATUS_PATTERN + r")(?:\s+\(.*\))?$", line)
            if not last:
                continue
            name, status = last.groups()
        name = _normalise(name)
        if "::" not in name:
            continue
        if name in statuses and statuses[name] != status:
            conflicts.append({"nodeid": name, "statuses": [statuses[name], status]})
        else:
            statuses[name] = status
    return statuses, conflicts


def _candidate_nodeids(case):
    """Recover file/class boundaries from pytest's lossy JUnit classname field."""
    name = case.get("name", "")
    classname = case.get("classname", "")
    candidates = []
    file_name = case.get("file", "").replace("\\", "/")
    if file_name and not PurePosixPath(file_name).is_absolute():
        module = file_name[:-3].replace("/", ".") if file_name.endswith(".py") else ""
        suffix = classname[len(module) + 1:] if module and classname.startswith(module + ".") else ""
        candidates.append(file_name + ("::" + suffix.replace(".", "::") if suffix else "") + "::" + name)
    parts = classname.split(".") if classname else []
    # Every possible module/class split is considered.  The full stdout nodeid
    # uniquely selects the correct one, including unittest-style test classes.
    for boundary in range(1, len(parts) + 1):
        module_path = "/".join(parts[:boundary]) + ".py"
        classes = "::".join(parts[boundary:])
        candidates.append(module_path + ("::" + classes if classes else "") + "::" + name)
    if not candidates:
        candidates.append(name)
    return list(dict.fromkeys(candidates))


def _case_status(case):
    error = case.find("error")
    failure = case.find("failure")
    skipped = case.find("skipped")
    if error is not None:
        return "ERROR", error.get("message", ""), error.text or ""
    if failure is not None:
        return "FAILED", failure.get("message", ""), failure.text or ""
    if skipped is not None:
        # pytest encodes an expected failure as a skipped element.  It remains
        # non-passing under the project's strict rule.
        status = "XFAIL" if "xfail" in skipped.get("type", "").lower() else "SKIPPED"
        return status, skipped.get("message", ""), skipped.text or ""
    return "PASSED", "", ""


def resolve_label(label, actual_statuses):
    """Return every compatible actual case; do not silently pick a good match."""
    if label in actual_statuses:
        matches = [label]
    else:
        normal_label = _normalise(label)
        exact = [key for key in actual_statuses if _normalise(key) == normal_label]
        if exact:
            matches = exact
        elif label.count("[") > label.count("]"):
            matches = [key for key in actual_statuses if _normalise(key).startswith(normal_label)]
        else:
            matches = []
    matches = sorted(matches)
    return {"label": label, "matches": matches,
            "statuses": [actual_statuses[key] for key in matches],
            "passed": bool(matches) and all(actual_statuses[key] == "PASSED" for key in matches)}


def grade_run(junit_path, log_path, f2p, p2p, returncode, timed_out=False):
    """Grade one independently executed pytest run, returning JSON-ready evidence.

    ``returncode=1`` with actual assertion failures is a valid unresolved run.
    Invalid/missing evidence, inconsistent statuses, duplicate case identities,
    absent required labels, errors and zero collection cannot resolve the task.
    """
    errors = []
    actual = {}
    records = []
    failures = []
    log_statuses = {}
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
        log_statuses, conflicts = parse_pytest_log(text)
        if conflicts:
            errors.append({"kind": "conflicting_log_statuses", "details": conflicts})
    except (OSError, TypeError, ValueError) as exc:
        errors.append({"kind": "log_unavailable", "message": str(exc)})
        text = ""
    try:
        tree = ET.parse(str(junit_path))
        cases = list(tree.iter("testcase"))
        if not cases:
            errors.append({"kind": "no_junit_cases"})
        normal_ids = set()
        for case in cases:
            candidates = _candidate_nodeids(case)
            matches = [key for key in candidates if _normalise(key) in log_statuses]
            if len(matches) == 1:
                nodeid = matches[0]
            else:
                # Preserve the usual module path as a diagnostic fallback, but
                # it cannot pass cross-checking when stdout lacks its identity.
                nodeid = candidates[-1]
                errors.append({"kind": "junit_identity_unconfirmed", "candidates": candidates,
                               "matching_candidates": matches})
            normal = _normalise(nodeid)
            status, message, detail = _case_status(case)
            records.append({"nodeid": nodeid, "status": status})
            if nodeid in actual or normal in normal_ids:
                errors.append({"kind": "duplicate_junit_case", "nodeid": nodeid})
            else:
                actual[nodeid] = status
                normal_ids.add(normal)
            logged = log_statuses.get(normal)
            if logged != status:
                errors.append({"kind": "log_junit_status_mismatch", "nodeid": nodeid,
                               "junit_status": status, "log_status": logged})
            if status in {"FAILED", "ERROR"}:
                failures.append({"nodeid": nodeid, "status": status,
                                 "message": message, "detail": detail})
        extras = sorted(key for key in log_statuses if key not in normal_ids)
        if extras:
            errors.append({"kind": "log_cases_missing_from_junit", "nodeids": extras})
    except (OSError, ET.ParseError, TypeError, ValueError) as exc:
        errors.append({"kind": "junit_unavailable", "message": str(exc)})
    for group, labels in (("f2p", f2p), ("p2p", p2p)):
        if not isinstance(labels, list) or not labels or not all(isinstance(item, str) and item for item in labels):
            errors.append({"kind": "invalid_required_labels", "group": group})
            if group == "f2p":
                f2p = []
            else:
                p2p = []
        elif len(set(labels)) != len(labels):
            errors.append({"kind": "duplicate_required_labels", "group": group})
    if set(f2p) & set(p2p):
        errors.append({"kind": "overlapping_required_labels"})
    f2p_mapping = [resolve_label(label, actual) for label in f2p]
    p2p_mapping = [resolve_label(label, actual) for label in p2p]
    missing = [row["label"] for row in f2p_mapping + p2p_mapping if not row["matches"]]
    if missing:
        errors.append({"kind": "required_cases_missing", "labels": missing})
    counts = dict(Counter(record["status"] for record in records))
    if timed_out:
        errors.append({"kind": "test_timeout"})
    if returncode not in (0, 1) or isinstance(returncode, bool):
        errors.append({"kind": "invalid_test_exit_code", "returncode": returncode})
    elif returncode == 0 and any(counts.get(status, 0) for status in ("FAILED", "ERROR")):
        errors.append({"kind": "exit_status_conflicts_with_test_failures"})
    elif returncode == 1 and not any(counts.get(status, 0) for status in ("FAILED", "ERROR")):
        errors.append({"kind": "nonzero_exit_without_failure_evidence"})
    if counts.get("ERROR", 0):
        errors.append({"kind": "test_errors_present", "count": counts["ERROR"]})
    collected_values = re.findall(r"\bcollected\s+(\d+)\s+items?\b", text)
    collected = int(collected_values[-1]) if collected_values else None
    if collected is None or collected == 0:
        errors.append({"kind": "no_positive_collection_evidence"})
    elif collected != len(records):
        errors.append({"kind": "collected_count_mismatch", "collected": collected,
                       "junit_case_records": len(records)})
    execution_valid = not errors
    all_cases_passed = bool(records) and all(record["status"] == "PASSED" for record in records)
    f2p_passed = sum(row["passed"] for row in f2p_mapping)
    p2p_passed = sum(row["passed"] for row in p2p_mapping)
    return {
        "execution_valid": execution_valid, "validation_errors": errors,
        "actual_cases": len(records), "collected": collected, "junit_case_records": len(records),
        "status_counts": counts, "actual_statuses": actual,
        "failures": failures, "f2p_mapping": f2p_mapping, "p2p_mapping": p2p_mapping,
        "f2p_passed": f2p_passed, "f2p_total": len(f2p),
        "p2p_passed": p2p_passed, "p2p_total": len(p2p),
        "p2p_unique_matched_case_count": len({key for row in p2p_mapping for key in row["matches"]}),
        "resolved": bool(execution_valid and returncode == 0 and all_cases_passed
                         and f2p_passed == len(f2p) and p2p_passed == len(p2p)),
        "grading_policy": "all matching actual cases must PASSED; JUnit and complete pytest log must agree",
    }
