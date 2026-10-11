"""Write a self-contained, offline HTML viewer for a teaching run."""

from __future__ import annotations

import json
from pathlib import Path


def write_report(summary: dict, events: list[dict], destination: Path) -> None:
    """Render recorded facts without executing or evaluating any agent actions.

    Escaping HTML-sensitive characters also protects the JSON script boundary:
    a repository file containing ``</script>`` cannot become executable markup.
    The viewer uses textContent for every value taken from these records.
    """
    template = Path(__file__).with_name("report_template.html").read_text(
        encoding="utf-8"
    )
    marker = "__HJ10_REPORT_DATA__"
    if template.count(marker) != 1:
        raise ValueError("Report template must contain exactly one data marker")
    payload = json.dumps(
        {"summary": summary, "events": events}, ensure_ascii=False, allow_nan=False
    )
    for literal, escaped in (
        ("&", "\\u0026"),
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        payload = payload.replace(literal, escaped)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(template.replace(marker, payload), encoding="utf-8")
