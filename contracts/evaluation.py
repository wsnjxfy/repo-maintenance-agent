"""Canonical evaluation envelope shared by tasks, run.py and the report.

Only the top-level fields in :class:`EvaluationResult` are a contract. A task
may put any JSON-serialisable evidence in ``details``; the report consumes the
stable fields and never needs a task-specific branch.

Metric keys are task-defined (``fail_to_pass`` / ``pass_to_pass`` / ``baseline``
are the conventional ones). Each metric carries its own display label so the
report does not have to know which task produced it.
"""

from __future__ import annotations

from typing import Any, TypedDict


class Metric(TypedDict, total=False):
    label: str
    passed: int
    total: int
    failed: int
    status: str
    cases: list[dict]


class EvaluationResult(TypedDict, total=False):
    resolved: bool
    status: str
    execution_valid: bool
    metrics: dict[str, Metric]
    new_regressions: list[str]
    errors: list[Any]
    cases: list[dict]
    details: dict


def metric(label, passed, total, *, failed=None, status=None, cases=None) -> Metric:
    """Build one displayable metric with non-negative integer counts."""
    passed = int(passed)
    total = int(total)
    if failed is None:
        failed = max(0, total - passed)
    item: Metric = {
        "label": str(label),
        "passed": passed,
        "total": total,
        "failed": int(failed),
    }
    if status is not None:
        item["status"] = str(status)
    if cases is not None:
        item["cases"] = list(cases)
    return item


def evaluation_result(*, resolved, status="completed", execution_valid=True,
                      metrics=None, new_regressions=None, errors=None,
                      cases=None, details=None) -> EvaluationResult:
    """Build the canonical envelope with every collection present."""
    return {
        "resolved": bool(resolved),
        "status": str(status),
        "execution_valid": bool(execution_valid),
        "metrics": dict(metrics or {}),
        "new_regressions": list(new_regressions or []),
        "errors": list(errors or []),
        "cases": list(cases or []),
        "details": details if isinstance(details, dict) else {},
    }


def metric_status(passed, total):
    """Conventional status for a metric card."""
    total = int(total)
    if total <= 0:
        return "unknown"
    return "passed" if int(passed) == total else "failed"
