"""Guard against silently reusing the legacy IBC baseline in publication runs."""
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import analyze_publication_extensions as publication
from analyze_publication_extensions import BASELINE_START, load_baseline_chain
from datetime import timedelta
from ibc_receive_evidence import IBC_INBOUND_POLICY


class BaselinePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        project_root = mock.patch.object(publication, "PROJECT_ROOT", self.root)
        project_root.start()
        self.addCleanup(project_root.stop)
        self.series = self.root / "panel.jsonl.gz"
        with gzip.open(self.series, "wt") as stream:
            for index in range(8640):
                stream.write(json.dumps({"bucket_start_utc": (BASELINE_START + timedelta(minutes=5 * index)).isoformat()}) + "\n")
        self.manifest = {"ibc_inbound_extraction_policy": IBC_INBOUND_POLICY,
                         "five_minute_series": {"sha256": hashlib.sha256(self.series.read_bytes()).hexdigest()},
                         "query_summaries": [{"label": f"ibc_in:{(BASELINE_START + timedelta(days=i)).date()}",
                                              "ibc_inbound_extraction_policy": IBC_INBOUND_POLICY} for i in range(30)]}

    def write_manifest(self):
        (self.root / "baseline_indexed_manifest.json").write_text(json.dumps(self.manifest))

    def test_verified_panel_accepted(self):
        self.write_manifest()
        rows, provenance = load_baseline_chain(self.series)
        self.assertEqual(len(rows), 8640)
        self.assertEqual(provenance["ibc_inbound_extraction_policy"], IBC_INBOUND_POLICY)

    def test_legacy_policy_rejected(self):
        self.manifest.pop("ibc_inbound_extraction_policy")
        self.write_manifest()
        with self.assertRaisesRegex(RuntimeError, "has not been rebuilt"):
            load_baseline_chain(self.series)

    def test_stale_digest_rejected(self):
        self.manifest["five_minute_series"]["sha256"] = "0" * 64
        self.write_manifest()
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            load_baseline_chain(self.series)

    def test_partial_day_policy_rejected(self):
        self.manifest["query_summaries"][5].pop("ibc_inbound_extraction_policy")
        self.write_manifest()
        with self.assertRaisesRegex(RuntimeError, "30 distinct"):
            load_baseline_chain(self.series)

    def test_duplicated_day_rejected(self):
        self.manifest["query_summaries"][6] = self.manifest["query_summaries"][5]
        self.write_manifest()
        with self.assertRaisesRegex(RuntimeError, "30 distinct"):
            load_baseline_chain(self.series)


if __name__ == "__main__":
    unittest.main()
