"""Opt-in integration test against the retained 2025-10-09 full raw fixture.

ATOM_RUN_RETAINED_RAW_TESTS=1 python3 -m unittest discover -s scripts \
  -p test_ibc_baseline_integration.py -v
No outputs or raw files are written. Legacy aggregates must remain available
until this pre-promotion test has completed.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import unittest

from collect_cosmos_baseline_indexed import ibc_events
from ibc_receive_evidence import assess_receives
from rebuild_baseline_ibc_receipts import canonical_update, legacy_events

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("ATOM_RUN_RETAINED_RAW_TESTS") == "1", "explicit retained-raw integration opt-in")
class RetainedBaselineIntegration(unittest.TestCase):
    def test_october_9_legacy_replay_and_corrected_buckets(self):
        baseline = ROOT / "data/processed/cosmoshub/baseline_30d"
        manifest = json.loads((baseline / "baseline_indexed_manifest.json").read_text())
        # A promoted canonical directory keeps an exact pre-policy snapshot.
        if manifest.get("prior_baseline_snapshot"):
            manifest = json.loads((baseline / manifest["prior_baseline_snapshot"]["relative_path"]).read_text())
        query = next(q for q in manifest["query_summaries"] if q["label"] == "ibc_in:2025-10-09")
        raw = ROOT / "data/raw/cosmoshub/cosmoshub-4/daily/2025-10-09/tx_search.jsonl.gz"
        txs = []
        with gzip.open(raw, "rt") as stream:
            for line in stream:
                for tx in json.loads(line)["response"]["result"].get("txs") or []:
                    if any(e["type"] == "recv_packet" and any(a["key"] == "packet_dst_port" and a["value"] == "transfer" for a in e.get("attributes", []))
                           for e in tx["tx_result"].get("events") or []):
                        txs.append(tx)
        self.assertEqual(len(txs), query["tx_count"])
        self.assertEqual(len(txs), len({tx["hash"] for tx in txs}))
        times = {}
        with gzip.open(ROOT / "data/raw/cosmoshub/cosmoshub-4/daily/2025-10-09/block_metas.jsonl.gz", "rt") as stream:
            for line in stream:
                header = json.loads(line)["block_meta"]["header"]
                times[int(header["height"])] = datetime.fromisoformat(header["time"].replace("Z", "+00:00"))
        start = datetime(2025, 9, 10, tzinfo=timezone.utc)
        digest = hashlib.sha256()
        old, new = defaultdict(Counter), defaultdict(Counter)
        excluded = []
        forwarded, forwarded_no_ack = 0, 0
        for tx in sorted(txs, key=lambda x: (int(x["height"]), int(x["index"]))):
            for event in legacy_events(tx):
                canonical_update(digest, event)
                bucket = int((times[event["height"]] - start).total_seconds() // 300)
                old[bucket].update(count=1, amount_uatom=event["amount_uatom"])
            events, errors = ibc_events(tx, "inbound")
            self.assertEqual(errors, 0)
            for event in events:
                bucket = int((times[event["height"]] - start).total_seconds() // 300)
                new[bucket].update(count=1, amount_uatom=event["amount_uatom"])
            if int(tx["tx_result"].get("code", 0)) == 0:
                for decision in assess_receives(tx["tx_result"].get("events") or []):
                    if decision["native_atom_trace"]:
                        if not decision["include_in_atom_flow"]:
                            excluded.append(decision)
                        elif decision["packet_forwarding"]:
                            forwarded += 1
                            forwarded_no_ack += not decision["immediate_ack_states"]
        self.assertEqual(digest.hexdigest(), query["extracted_events_sha256"])
        self.assertEqual(sum(v["count"] for v in old.values()), 1544)
        self.assertEqual(sum(v["amount_uatom"] for v in old.values()), 387_860_685_653)
        self.assertEqual(sum(v["count"] for v in new.values()), 1543)
        self.assertEqual(sum(v["amount_uatom"] for v in new.values()), 387_853_389_653)
        self.assertEqual(len(excluded), 1)
        self.assertEqual(excluded[0]["exclusion_reason"], "application_error")
        self.assertEqual(int(excluded[0]["packet_data"]["amount"]), 7_296_000)
        self.assertGreater(forwarded_no_ack, 0)
        self.assertEqual(forwarded, forwarded_no_ack)
        changed = [bucket for bucket in old if old[bucket] != new[bucket]]
        self.assertEqual(len(changed), 1)
        self.assertEqual(old[changed[0]]["amount_uatom"] - new[changed[0]]["amount_uatom"], 7_296_000)


if __name__ == "__main__":
    unittest.main()
