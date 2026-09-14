"""Receipt-v2 integrity and relocation regressions in disposable roots only."""
from __future__ import annotations

import copy
import contextlib
from datetime import timedelta
import hashlib
import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import rebuild_baseline_ibc_receipts as rebuild
import promote_verified_ibc_baseline as promotion

from collect_cosmos_baseline_indexed import write_deterministic_jsonl_gzip
from ibc_receive_evidence import IBC_INBOUND_POLICY
from rebuild_baseline_ibc_receipts import (
    BASELINE_START, canonical_update, inbound_day_bounds, inbound_query_text,
    require_local_artifact, sha, validate_inbound_scope, validate_raw_transaction,
)
from verify_cosmos_baseline_indexed import resolve_block_artifact, verify_receipt_bundle
from test_ibc_receive_evidence import event


class ReceiptBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ibc_integrity_test_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.base = self.root / "data/processed/cosmoshub/baseline_30d_success_v2"
        self.base.mkdir(parents=True)
        self.block_times = {i + 1: BASELINE_START + timedelta(days=i) for i in range(30)}
        self.series = {}
        metrics = ("confirmed_exchange_in", "confirmed_exchange_out", "unconfirmed_behavioral_in", "ibc_inbound", "ibc_outbound")
        for bucket in range(8640):
            row = {f"{metric}_{suffix}": 0 for metric in metrics for suffix in ("count", "atom")}
            row.update(confirmed_exchange_net_in_atom=0, all_behavioral_candidate_in_atom=0, ibc_net_inbound_atom=0)
            if bucket % 288 == 0:
                row.update(ibc_inbound_count=1, ibc_inbound_atom=1, ibc_net_inbound_atom=1)
            self.series[BASELINE_START + timedelta(minutes=5 * bucket)] = row
        self.manifest = {"query_count": 371, "end_height_exclusive": 31,
                         "ibc_inbound_extraction_policy": IBC_INBOUND_POLICY,
                         "query_checkpoint_directory": str(self.base / "query_checkpoints")}
        queries, prior_queries, preserved = [], [], []
        for i in range(341):
            query = {"index": i, "label": f"recipient:fixture{i}"}
            queries.append(query)
            prior_queries.append(copy.deepcopy(query))
            path = self.base / "query_checkpoints" / f"{i:04d}.jsonl.gz"
            write_deterministic_jsonl_gzip(path, [{**query, "partial": {}}])
            preserved.append({"index": i, "sha256": sha(path)})
        self.evidence, self.raw_index, day_audits, raw_sources = [], [], [], []
        for i in range(30):
            day, index = self.block_times[i + 1].date().isoformat(), 341 + i
            tx_hash = f"{i + 1:064X}"
            row = {"day": day, "height": i + 1, "tx_hash": tx_hash, "event_ordinal": 2, "amount_uatom": 1_000_000,
                   "denom": "transfer/channel-0/uatom", "policy": IBC_INBOUND_POLICY,
                   "application_success": True, "native_atom_trace": True, "native_credit_matched": True,
                   "include_in_atom_flow": True, "exclusion_reason": None, "immediate_ack_states": [],
                   "packet_key": ["transfer", "channel-0", "transfer", "channel-141", str(i), "0"],
                   "success_event_ordinal": 0, "native_credit_event_ordinal": 1}
            self.evidence.append(row)
            self.raw_index.append({"day": day, "height": i + 1, "tx_index": 0, "tx_hash": tx_hash,
                                   "tx_code": 0, "source_day_query_index": index})
            digest = hashlib.sha256()
            canonical_update(digest, {k: row[k] for k in ("height", "tx_hash", "event_ordinal", "amount_uatom")})
            query = {"index": index, "label": f"ibc_in:{day}", "tx_count": 1, "page_count": 1,
                     "query_sha256": hashlib.sha256(inbound_query_text(i + 1, i + 2).encode()).hexdigest(),
                     "extracted_event_count": 1, "extracted_amount_uatom": 1_000_000,
                     "extracted_events_sha256": digest.hexdigest()}
            prior_queries.append(copy.deepcopy(query))
            raw = {"local_path": f"data/raw/{day}/tx_search.jsonl.gz", "sha256": f"{i + 100:064x}"}
            raw_sources.append(raw)
            query.update(ibc_inbound_extraction_policy=IBC_INBOUND_POLICY, raw_transactions=raw)
            queries.append(query)
            write_deterministic_jsonl_gzip(self.base / "query_checkpoints" / f"{index:04d}.jsonl.gz",
                                          [{**query, "partial": {i * 288: {"ibc_inbound_count": 1, "ibc_inbound_uatom": 1_000_000}}}])
            day_audits.append({"day": day, "legacy_digest_reproduced": True,
                               "legacy_count": 1, "legacy_uatom": 1_000_000,
                               "corrected_count": 1, "corrected_uatom": 1_000_000,
                               "excluded_legacy_atom_count": 0, "excluded_legacy_atom_uatom": 0,
                               "excluded_legacy_atom_packets": [], "unresolved_native_exclusions": [], "raw": raw})
        self.manifest["query_summaries"] = queries
        self.snapshot = {"query_summaries": prior_queries,
                         "historical_absolute_path": "/original/research/location/preserve-exactly"}
        self.audit = {"status": "PASS", "policy": IBC_INBOUND_POLICY, "unresolved_native_exclusions": [],
                      "old_baseline_manifest": str(self.base / "baseline_indexed_manifest_before_success_v2.json"),
                      "old_baseline_manifest_snapshot_relative_path": "baseline_indexed_manifest_before_success_v2.json",
                      "legacy_digests_all_reproduced": True, "non_inbound_bucket_metrics_unchanged": True,
                      "day_count": 30, "day_audits": day_audits, "raw_sources": raw_sources,
                      "raw_global_integrity": {"transaction_count": 30, "unique_tx_hash_count": 30,
                                               "all_day_memberships_verified": True, "all_raw_transactions_indexed": True},
                      "excluded_count": 0, "excluded_uatom": 0, "preserved_non_inbound_checkpoints": preserved}
        self.refresh()

    def refresh(self):
        for key, name, data in (
            ("prior_baseline_snapshot", "baseline_indexed_manifest_before_success_v2.json", self.snapshot),
            ("ibc_receive_evidence", "ibc_receive_evidence.jsonl.gz", self.evidence),
            ("ibc_raw_transaction_index", "ibc_raw_transaction_index.jsonl.gz", self.raw_index),
            ("ibc_receive_policy_audit", "ibc_receive_policy_audit.json", self.audit),
        ):
            path = self.base / name
            if key == "ibc_receive_policy_audit":
                self.audit["old_baseline_manifest_sha256"] = self.manifest["prior_baseline_snapshot"]["sha256"]
            if name.endswith(".gz"):
                write_deterministic_jsonl_gzip(path, data)
            else:
                path.write_text(json.dumps(data, indent=2) + "\n")
            self.manifest[key] = {"local_path": str(path), "sha256": sha(path)}
            if isinstance(data, list):
                self.manifest[key]["record_count"] = len(data)

    def verify(self):
        return verify_receipt_bundle(self.manifest, self.base, self.root, self.block_times, self.series)

    def test_complete_compact_bundle_passes_without_full_raw(self):
        self.assertEqual(self.verify()["raw_transaction_count"], 30)

    def test_every_required_artifact_is_mandatory(self):
        for key in ("ibc_receive_policy_audit", "ibc_receive_evidence", "prior_baseline_snapshot", "ibc_raw_transaction_index"):
            with self.subTest(key=key):
                saved = self.manifest.pop(key)
                with self.assertRaises((KeyError, ValueError)):
                    self.verify()
                self.manifest[key] = saved

    def test_every_required_artifact_hash_is_checked(self):
        for key in ("ibc_receive_policy_audit", "ibc_receive_evidence", "prior_baseline_snapshot", "ibc_raw_transaction_index"):
            with self.subTest(key=key):
                old = self.manifest[key]["sha256"]
                self.manifest[key]["sha256"] = "0" * 64
                with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                    self.verify()
                self.manifest[key]["sha256"] = old

    def test_zero_or_29_inbound_queries_cannot_pass(self):
        original = copy.deepcopy(self.manifest["query_summaries"])
        for keep in (0, 29):
            with self.subTest(keep=keep):
                self.manifest["query_summaries"] = original[:341 + keep]
                with self.assertRaises(ValueError):
                    self.verify()

    def test_duplicate_inbound_day_and_outside_date_are_rejected(self):
        queries = [q for q in self.manifest["query_summaries"] if q["label"].startswith("ibc_in:")]
        for label in (queries[0]["label"], "ibc_in:2025-10-10"):
            invalid = copy.deepcopy(queries)
            invalid[-1]["label"] = label
            with self.assertRaises(ValueError):
                validate_inbound_scope(invalid)

    def test_wrong_day_height_query_digest_is_rejected(self):
        self.manifest["query_summaries"][-1]["query_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "date/height bounds"):
            self.verify()

    def test_duplicate_failed_or_non_atom_raw_tx_is_rejected(self):
        self.raw_index.append({**self.raw_index[0], "tx_code": 7, "day": self.raw_index[-1]["day"]})
        self.refresh()
        with self.assertRaisesRegex(ValueError, "repeated across days"):
            self.verify()

    def test_raw_height_in_wrong_day_is_rejected(self):
        self.raw_index[0]["height"] = 2
        self.refresh()
        with self.assertRaisesRegex(ValueError, "does not belong"):
            self.verify()

    def test_raw_missing_transaction_is_rejected(self):
        self.raw_index.pop()
        self.refresh()
        with self.assertRaisesRegex(ValueError, "day counts"):
            self.verify()

    def test_unresolved_native_exclusion_cannot_be_hidden_by_audit_pass(self):
        self.evidence[0].update(include_in_atom_flow=False, exclusion_reason="no_matching_native_uatom_credit")
        self.refresh()
        with self.assertRaisesRegex(ValueError, "Unresolved native"):
            self.verify()

    def test_evidence_digest_detects_amount_change_after_hash_rebinding(self):
        self.evidence[0]["amount_uatom"] += 1
        self.refresh()
        with self.assertRaisesRegex(ValueError, "digest/totals mismatch"):
            self.verify()

    def test_snapshot_binding_is_checked(self):
        self.audit["old_baseline_manifest_sha256"] = "0" * 64
        path = self.base / "ibc_receive_policy_audit.json"
        path.write_text(json.dumps(self.audit))
        self.manifest["ibc_receive_policy_audit"]["sha256"] = sha(path)
        with self.assertRaisesRegex(ValueError, "exact prior snapshot"):
            self.verify()

    def test_stale_staging_path_is_rejected_even_when_target_hash_matches(self):
        self.manifest["ibc_receive_evidence"]["local_path"] = str(self.base.with_name("old_staging") / "ibc_receive_evidence.jsonl.gz")
        with self.assertRaisesRegex(ValueError, "does not identify this baseline"):
            self.verify()

    def test_live_audit_stale_snapshot_path_is_rejected(self):
        self.audit["old_baseline_manifest"] = str(self.base.with_name("old_staging") / "baseline_indexed_manifest_before_success_v2.json")
        self.refresh()
        with self.assertRaisesRegex(ValueError, "different baseline"):
            self.verify()

    def test_repo_relative_and_bare_paths_pass_with_historical_snapshot_unchanged(self):
        before = (self.base / "baseline_indexed_manifest_before_success_v2.json").read_bytes()
        for key in ("ibc_receive_policy_audit", "prior_baseline_snapshot", "ibc_receive_evidence", "ibc_raw_transaction_index"):
            record = self.manifest[key]
            path = Path(record["local_path"])
            record["local_path"] = path.relative_to(self.root).as_posix()
            record["relative_path"] = path.name
        self.manifest["query_checkpoint_directory"] = "query_checkpoints"
        self.audit["old_baseline_manifest"] = "baseline_indexed_manifest_before_success_v2.json"
        audit_path = self.base / "ibc_receive_policy_audit.json"
        audit_path.write_text(json.dumps(self.audit))
        self.manifest["ibc_receive_policy_audit"]["sha256"] = sha(audit_path)
        self.verify()
        self.assertEqual(before, (self.base / "baseline_indexed_manifest_before_success_v2.json").read_bytes())

    def test_preserved_non_inbound_checkpoint_mutation_is_rejected(self):
        path = self.base / "query_checkpoints/0000.jsonl.gz"
        write_deterministic_jsonl_gzip(path, [{**self.manifest["query_summaries"][0], "partial": {0: {"ibc_outbound_count": 1}}}])
        with self.assertRaisesRegex(ValueError, "non-inbound checkpoint changed"):
            self.verify()

    def test_non_inbound_series_mutation_is_rejected(self):
        self.series[BASELINE_START]["ibc_outbound_atom"] = 1
        with self.assertRaisesRegex(ValueError, "retained checkpoint inputs"):
            self.verify()

    def test_only_shared_canonical_block_index_is_allowed(self):
        name = "block_times_2025-09-10_2025-10-10.jsonl.gz"
        canonical = self.base.with_name("baseline_30d")
        canonical.mkdir()
        path = canonical / name
        path.write_bytes(b"test immutable block index")
        record = {"local_path": str(path), "sha256": sha(path)}
        self.assertEqual(resolve_block_artifact(record, self.base, self.root), path)
        other = self.root / name
        other.write_bytes(path.read_bytes())
        with self.assertRaisesRegex(ValueError, "local or the canonical"):
            resolve_block_artifact({**record, "local_path": str(other)}, self.base, self.root)

    def prepare_rebuild_fixture(self):
        """Thirty complete synthetic raw days and an independent legacy tree."""
        canonical = self.base.with_name("baseline_30d")
        self.base.rename(canonical)
        for name in ("ibc_receive_evidence.py", "collect_cosmos_baseline_indexed.py", "rebuild_baseline_ibc_receipts.py"):
            target = self.root / "scripts" / name
            target.parent.mkdir(exist_ok=True)
            target.write_bytes((rebuild.ROOT / "scripts" / name).read_bytes())
        block_path = canonical / "block_times_2025-09-10_2025-10-10.jsonl.gz"
        write_deterministic_jsonl_gzip(block_path, [{"height": h, "time_utc": t.isoformat()} for h, t in self.block_times.items()])
        (canonical / "block_times_manifest.json").write_text("{}\n")
        series_path = canonical / "baseline_5min_2025-09-10_2025-10-10.jsonl.gz"
        write_deterministic_jsonl_gzip(series_path, [{"bucket_start_utc": t.isoformat().replace("+00:00", "Z"), **row}
                                                   for t, row in self.series.items()])
        queries = copy.deepcopy(self.snapshot["query_summaries"])
        raw_root = self.root / "data/raw/reacquired_receipts"
        for query in queries:
            query.update(preferred_rpc="https://fixture.invalid", page_source_counts={"https://fixture.invalid": 1},
                         endpoint_failovers=[], decode_errors=0)
            if query["label"].startswith("ibc_in:"):
                i = query["index"] - 341
                day = query["label"].split(":", 1)[1]
                data = {"denom": "transfer/channel-0/uatom", "amount": "1000000", "sender": "osmo1sender", "receiver": "cosmos1receiver"}
                packet = dict(packet_src_port="transfer", packet_src_channel="channel-0", packet_dst_port="transfer",
                              packet_dst_channel="channel-141", packet_sequence=str(i), msg_index="0")
                tx = {"hash": f"{i + 1:064X}", "height": str(i + 1), "index": 0,
                      "tx_result": {"code": 0, "events": [event("fungible_token_packet", **data, success="true", msg_index="0"),
                          event("transfer", recipient=data["receiver"], amount="1000000uatom", msg_index="0"),
                          event("recv_packet", **packet, packet_data_hex=json.dumps(data).encode().hex())]}}
                page = {"day": day, "label": query["label"], "page": 1, "first_height": i + 1, "end_height_exclusive": i + 2,
                        "query": inbound_query_text(i + 1, i + 2), "query_sha256": query["query_sha256"],
                        "source_rpc": "https://fixture.invalid", "response": {"result": {"txs": [tx], "total_count": "1"}}}
                write_deterministic_jsonl_gzip(raw_root / day / "tx_search.jsonl.gz", [page])
                partial = {i * 288: {"ibc_inbound_count": 1, "ibc_inbound_uatom": 1_000_000}}
            else:
                query.update(page_count=1, tx_count=0, extracted_event_count=0, extracted_amount_uatom=0,
                             extracted_events_sha256=hashlib.sha256().hexdigest())
                partial = {}
            write_deterministic_jsonl_gzip(canonical / "query_checkpoints" / f"{query['index']:04d}.jsonl.gz", [{**query, "partial": partial}])
        old_manifest = {"query_summaries": queries, "query_count": 371, "first_height": 1, "end_height_exclusive": 31,
                        "block_times": {"local_path": str(block_path), "sha256": sha(block_path), "record_count": 30},
                        "five_minute_series": {"local_path": str(series_path), "sha256": sha(series_path), "record_count": 8640},
                        "limitations": []}
        manifest_path = canonical / "baseline_indexed_manifest.json"
        manifest_path.write_text(json.dumps(old_manifest, indent=2) + "\n")
        day_summaries = []
        for query in queries:
            if not query["label"].startswith("ibc_in:"):
                continue
            day = query["label"].split(":", 1)[1]
            path = raw_root / day / "tx_search.jsonl.gz"
            page = list(rebuild.records(path))[0]
            summary = {k: page[k] for k in ("day", "label", "first_height", "end_height_exclusive", "query", "query_sha256")}
            summary.update(index=query["index"], status="complete", total_count=1, unique_tx_count=1, pages=1,
                           tx_search_path=f"{day}/tx_search.jsonl.gz", tx_search_sha256=sha(path))
            (raw_root / day / "day_manifest.json").write_text(json.dumps(summary))
            day_summaries.append(summary)
        (raw_root / "manifest.json").write_text(json.dumps({"status": "complete", "chain_id": "cosmoshub-4",
            "unique_tx_count": 30, "day_summaries": day_summaries, "block_time_index": {"sha256": sha(block_path)}}))
        return canonical, raw_root, manifest_path.read_bytes()

    def rebind_synthetic_acquisition(self, raw_root, day):
        """For structural-failure tests only; model a bad but consistently hashed collector input."""
        path = raw_root / "manifest.json"
        manifest = json.loads(path.read_text())
        summary = next(r for r in manifest["day_summaries"] if r["day"] == day)
        summary["tx_search_sha256"] = sha(raw_root / day / "tx_search.jsonl.gz")
        (raw_root / day / "day_manifest.json").write_text(json.dumps(summary))
        path.write_text(json.dumps(manifest))

    def invoke_rebuild(self, canonical, raw_root):
        argv = ["rebuild_baseline_ibc_receipts.py", "--baseline-root", str(canonical), "--raw-root", str(raw_root), "--output-root", str(self.base)]
        with mock.patch.object(rebuild, "ROOT", self.root), mock.patch("sys.argv", argv), contextlib.redirect_stdout(io.StringIO()):
            rebuild.main()

    def test_full_rebuild_verify_promote_verify_with_exact_snapshot(self):
        canonical, raw_root, old_bytes = self.prepare_rebuild_fixture()
        self.invoke_rebuild(canonical, raw_root)
        manifest_path = self.base / "baseline_indexed_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        series = {rebuild.datetime.fromisoformat(r["bucket_start_utc"].replace("Z", "+00:00")): r
                  for r in rebuild.records(self.base / "baseline_5min_2025-09-10_2025-10-10.jsonl.gz")}
        verify_receipt_bundle(manifest, self.base, self.root, self.block_times, series)
        report = self.root / "verification.json"
        report.write_text(json.dumps({"verification_status": "PASS", "verified_inputs": {
            "manifest_sha256": sha(manifest_path), "series_sha256": manifest["five_minute_series"]["sha256"],
            "block_times_sha256": manifest["block_times"]["sha256"]}}))
        backup = self.root / "tmp/pdfs/old_baseline"
        argv = ["promote_verified_ibc_baseline.py", "--candidate", str(self.base), "--backup", str(backup), "--verification", str(report), "--apply"]
        with mock.patch.object(promotion, "ROOT", self.root), mock.patch("sys.argv", argv), contextlib.redirect_stdout(io.StringIO()):
            promotion.main()
        promoted = json.loads((canonical / "baseline_indexed_manifest.json").read_text())
        verify_receipt_bundle(promoted, canonical, self.root, self.block_times, series)
        self.assertEqual((canonical / "baseline_indexed_manifest_before_success_v2.json").read_bytes(), old_bytes)
        self.assertEqual((backup / "baseline_indexed_manifest.json").read_bytes(), old_bytes)
        self.assertEqual(promoted["ibc_raw_transaction_index"]["sha256"], manifest["ibc_raw_transaction_index"]["sha256"])

    def test_rebuild_missing_day_stops_without_output(self):
        canonical, raw_root, old_bytes = self.prepare_rebuild_fixture()
        (raw_root / "2025-09-10/tx_search.jsonl.gz").unlink()
        with self.assertRaisesRegex(SystemExit, "Missing full raw days"):
            self.invoke_rebuild(canonical, raw_root)
        self.assertFalse(self.base.exists())
        self.assertEqual((canonical / "baseline_indexed_manifest.json").read_bytes(), old_bytes)

    def test_rebuild_global_duplicate_of_failed_transaction_stops_before_output(self):
        canonical, raw_root, old_bytes = self.prepare_rebuild_fixture()
        path = raw_root / "2025-09-11/tx_search.jsonl.gz"
        rows = list(rebuild.records(path))
        rows[0]["response"]["result"]["txs"][0].update(hash=f"{1:064X}", tx_result={"code": 7, "events": []})
        write_deterministic_jsonl_gzip(path, rows)
        self.rebind_synthetic_acquisition(raw_root, "2025-09-11")
        with self.assertRaisesRegex(ValueError, "repeated across days"):
            self.invoke_rebuild(canonical, raw_root)
        self.assertFalse(self.base.exists())
        self.assertEqual((canonical / "baseline_indexed_manifest.json").read_bytes(), old_bytes)

    def test_rebuild_wrong_transaction_day_stops_before_output(self):
        canonical, raw_root, old_bytes = self.prepare_rebuild_fixture()
        path = raw_root / "2025-09-10/tx_search.jsonl.gz"
        rows = list(rebuild.records(path))
        rows[0]["response"]["result"]["txs"][0]["height"] = "2"
        write_deterministic_jsonl_gzip(path, rows)
        self.rebind_synthetic_acquisition(raw_root, "2025-09-10")
        with self.assertRaisesRegex(ValueError, "does not belong"):
            self.invoke_rebuild(canonical, raw_root)
        self.assertFalse(self.base.exists())
        self.assertEqual((canonical / "baseline_indexed_manifest.json").read_bytes(), old_bytes)

    def test_rebuild_wrong_page_height_bounds_stops_before_output(self):
        canonical, raw_root, old_bytes = self.prepare_rebuild_fixture()
        path = raw_root / "2025-09-10/tx_search.jsonl.gz"
        rows = list(rebuild.records(path))
        rows[0]["end_height_exclusive"] += 1
        write_deterministic_jsonl_gzip(path, rows)
        self.rebind_synthetic_acquisition(raw_root, "2025-09-10")
        with self.assertRaisesRegex(ValueError, "Raw page height bounds"):
            self.invoke_rebuild(canonical, raw_root)
        self.assertFalse(self.base.exists())
        self.assertEqual((canonical / "baseline_indexed_manifest.json").read_bytes(), old_bytes)

    def test_rebuild_rejects_success_event_edit_even_with_unchanged_legacy_native_digest(self):
        canonical, raw_root, old_bytes = self.prepare_rebuild_fixture()
        path = raw_root / "2025-09-10/tx_search.jsonl.gz"
        rows = list(rebuild.records(path))
        attrs = rows[0]["response"]["result"]["txs"][0]["tx_result"]["events"][0]["attributes"]
        next(a for a in attrs if a["key"] == "success")["value"] = "false"
        write_deterministic_jsonl_gzip(path, rows)
        with self.assertRaisesRegex(ValueError, "acquisition-time SHA-256"):
            self.invoke_rebuild(canonical, raw_root)
        self.assertFalse(self.base.exists())
        self.assertEqual((canonical / "baseline_indexed_manifest.json").read_bytes(), old_bytes)

    def test_rebuild_rejects_incomplete_collection_manifest(self):
        canonical, raw_root, old_bytes = self.prepare_rebuild_fixture()
        path = raw_root / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["status"] = "fetching"
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "complete Cosmos Hub acquisition"):
            self.invoke_rebuild(canonical, raw_root)
        self.assertFalse(self.base.exists())
        self.assertEqual((canonical / "baseline_indexed_manifest.json").read_bytes(), old_bytes)

    def test_rebuild_rejects_day_manifest_top_summary_mismatch(self):
        canonical, raw_root, old_bytes = self.prepare_rebuild_fixture()
        path = raw_root / "2025-09-10/day_manifest.json"
        manifest = json.loads(path.read_text())
        manifest["total_count"] += 1
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "differs from completed acquisition summary"):
            self.invoke_rebuild(canonical, raw_root)
        self.assertFalse(self.base.exists())
        self.assertEqual((canonical / "baseline_indexed_manifest.json").read_bytes(), old_bytes)

    def test_rebuild_known_failed_receipt_is_excluded_and_verifies(self):
        canonical, raw_root, old_bytes = self.prepare_rebuild_fixture()
        path = raw_root / "2025-09-10/tx_search.jsonl.gz"
        rows = list(rebuild.records(path))
        attrs = rows[0]["response"]["result"]["txs"][0]["tx_result"]["events"][0]["attributes"]
        next(a for a in attrs if a["key"] == "success")["value"] = "false"
        write_deterministic_jsonl_gzip(path, rows)
        self.rebind_synthetic_acquisition(raw_root, "2025-09-10")
        self.invoke_rebuild(canonical, raw_root)
        manifest = json.loads((self.base / "baseline_indexed_manifest.json").read_text())
        series = {rebuild.datetime.fromisoformat(r["bucket_start_utc"].replace("Z", "+00:00")): r
                  for r in rebuild.records(self.base / "baseline_5min_2025-09-10_2025-10-10.jsonl.gz")}
        detail = verify_receipt_bundle(manifest, self.base, self.root, self.block_times, series)
        self.assertEqual((detail["excluded_count"], detail["excluded_uatom"]), (1, 1_000_000))
        self.assertEqual((canonical / "baseline_indexed_manifest.json").read_bytes(), old_bytes)

    def test_rebuild_unknown_native_exclusion_stops_for_review(self):
        canonical, raw_root, old_bytes = self.prepare_rebuild_fixture()
        path = raw_root / "2025-09-10/tx_search.jsonl.gz"
        rows = list(rebuild.records(path))
        attrs = rows[0]["response"]["result"]["txs"][0]["tx_result"]["events"][1]["attributes"]
        next(a for a in attrs if a["key"] == "amount")["value"] = "999999uatom"
        write_deterministic_jsonl_gzip(path, rows)
        self.rebind_synthetic_acquisition(raw_root, "2025-09-10")
        with self.assertRaisesRegex(SystemExit, "Review unresolved native receipt"):
            self.invoke_rebuild(canonical, raw_root)
        audit = json.loads((self.base / "ibc_receive_policy_audit.json").read_text())
        self.assertEqual(audit["status"], "REVIEW_REQUIRED")
        self.assertEqual(len(audit["unresolved_native_exclusions"]), 1)
        self.assertEqual((canonical / "baseline_indexed_manifest.json").read_bytes(), old_bytes)


if __name__ == "__main__":
    unittest.main()
