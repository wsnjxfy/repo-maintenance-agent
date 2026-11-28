from collections import Counter
import tempfile
import unittest

from experiments.repo_split.specs import load_catalog
from tasks.registry import TASKS
from tools.specs import TOOL_SPECS


class PublishedCatalogTests(unittest.TestCase):
    def test_clean_checkout_exposes_disjoint_groups(self):
        rows = load_catalog()["instances"]
        self.assertEqual(len({row["repo"].casefold() for row in rows}), 15)
        self.assertEqual(Counter(row["split"] for row in rows), {"dev": 10, "test": 5})
        allowed = {
            "split", "repo", "instance_id", "purpose", "dataset", "official_split",
            "parquet_row_index", "base_commit", "f2p_count", "p2p_count",
        }
        self.assertTrue(all(set(row) == allowed for row in rows))
        grouped = [entry for entry in TASKS.values() if entry.group]
        self.assertEqual(Counter(entry.group for entry in grouped), {"dev": 10, "test": 5})
        for entry in grouped:
            self.assertIn("llm", entry.policies)
            self.assertTrue(entry.locked_toolset)
            self.assertFalse(any(TOOL_SPECS[name]["executes_code"] for name in entry.default_tools))

    def test_missing_local_material_has_a_failed_preflight(self):
        with tempfile.TemporaryDirectory(prefix="hj10-no-data-") as missing:
            checks = TASKS["dev-flask-5014"].preflight({"data_dir": missing})
        self.assertTrue(any(not check["ok"] for check in checks))
