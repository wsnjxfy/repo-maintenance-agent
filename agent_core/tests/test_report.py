"""Report rendering tests (moved from mvp/tests)."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent_core.report import write_report


class TestReport(unittest.TestCase):
    def test_repository_text_cannot_break_embedded_data(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "report.html"
            content = '</script><script>alert("not executed")</script>&'
            write_report({"task": content}, [], destination)
            html = destination.read_text(encoding="utf-8")
            marker = '<script type="application/json" id="report-data">'
            embedded = html.split(marker, 1)[1].split("</script>", 1)[0]
            self.assertNotIn("<script>", embedded)
            self.assertEqual(json.loads(embedded)["summary"]["task"], content)

    def test_embedding_preserves_per_step_notes(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "report.html"
            events = [{"step": 1, "note": {"finding": "empty tuple", "next": "edit"}}]
            write_report({"status": "completed"}, events, destination)
            html = destination.read_text(encoding="utf-8")
            marker = '<script type="application/json" id="report-data">'
            embedded = html.split(marker, 1)[1].split("</script>", 1)[0]
            self.assertEqual(json.loads(embedded)["events"][0]["note"]["finding"],
                             "empty tuple")


