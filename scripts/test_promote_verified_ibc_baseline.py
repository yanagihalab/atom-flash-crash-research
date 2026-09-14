"""Exercise migration safety only in disposable roots, never in study data."""
from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import promote_verified_ibc_baseline as promotion
from collect_cosmos_baseline_indexed import write_deterministic_jsonl_gzip


class PromotionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ibc_promotion_test_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        parent = self.root / "data/processed/cosmoshub"
        self.canonical = parent / "baseline_30d"
        self.candidate = parent / "baseline_30d_success_v2"
        self.backup = self.root / "tmp/pdfs/promotion_backup"
        self.verification = self.root / "results/candidate_verification.json"
        self.canonical.mkdir(parents=True)
        self.candidate.mkdir()
        self.verification.parent.mkdir()
        self.block_name = "block_times.jsonl.gz"
        self.series_name = "baseline_5min_2025-09-10_2025-10-10.jsonl.gz"
        self.block_bytes = b"immutable original block-time index\n"
        (self.canonical / self.block_name).write_bytes(self.block_bytes)
        (self.canonical / "block_times_manifest.json").write_text('{"immutable": true}\n')
        (self.canonical / self.series_name).write_bytes(b"original series\n")
        self.old_manifest_bytes = b'{"original_baseline": true}\n'
        (self.canonical / "baseline_indexed_manifest.json").write_bytes(self.old_manifest_bytes)
        (self.candidate / "baseline_indexed_manifest_before_success_v2.json").write_bytes(self.old_manifest_bytes)
        (self.candidate / self.series_name).write_bytes(b"independently verified replacement series\n")
        self.audit = {
            "status": "PASS", "policy": "native-atom-receipt-success-v2",
            "legacy_digests_all_reproduced": True,
            "non_inbound_bucket_metrics_unchanged": True,
            "unresolved_native_exclusions": [],
            "old_baseline_manifest": str(self.candidate / "baseline_indexed_manifest_before_success_v2.json"),
            "old_baseline_manifest_snapshot_relative_path": "baseline_indexed_manifest_before_success_v2.json",
            "old_baseline_manifest_sha256": promotion.digest(self.candidate / "baseline_indexed_manifest_before_success_v2.json"),
            "raw_sources": [{"sha256": "raw-source-hash-is-immutable", "local_path": str(self.root / "data/raw/full.jsonl.gz")}],
        }
        self.audit_path = self.candidate / "ibc_receive_policy_audit.json"
        self.audit_path.write_text(json.dumps(self.audit))
        self.manifest = {
            "ibc_inbound_extraction_policy": "native-atom-receipt-success-v2",
            "block_times": {"local_path": str(self.canonical / self.block_name), "sha256": promotion.digest(self.canonical / self.block_name)},
            "five_minute_series": {"local_path": str(self.candidate / self.series_name), "sha256": promotion.digest(self.candidate / self.series_name)},
            "ibc_receive_policy_audit": {"local_path": str(self.audit_path), "sha256": promotion.digest(self.audit_path)},
            "query_checkpoint_directory": str(self.candidate / "query_checkpoints"),
            "nested_path": {"items": [str(self.candidate / "query_checkpoints/0001.jsonl.gz")]},
        }
        for key, name in (("prior_baseline_snapshot", "baseline_indexed_manifest_before_success_v2.json"),
                          ("ibc_receive_evidence", "ibc_receive_evidence.jsonl.gz"),
                          ("ibc_raw_transaction_index", "ibc_raw_transaction_index.jsonl.gz")):
            path = self.candidate / name
            if not path.exists():
                write_deterministic_jsonl_gzip(path, [{"immutable": key}])
            self.manifest[key] = {"local_path": str(path), "sha256": promotion.digest(path)}
        (self.candidate / "query_checkpoints").mkdir()
        write_deterministic_jsonl_gzip(self.candidate / "query_checkpoints/0001.jsonl.gz", [{"verified": True}])
        self.refresh_verified_manifest()

    def refresh_verified_manifest(self):
        path = self.candidate / "baseline_indexed_manifest.json"
        path.write_text(json.dumps(self.manifest))
        self.verification.write_text(json.dumps({
            "verification_status": "PASS",
            "verified_inputs": {"manifest_sha256": promotion.digest(path), "series_sha256": promotion.digest(self.candidate / self.series_name),
                                "block_times_sha256": promotion.digest(self.canonical / self.block_name)},
        }))

    def invoke(self, apply=True):
        argv = ["promote_verified_ibc_baseline.py", "--candidate", str(self.candidate),
                "--verification", str(self.verification), "--backup", str(self.backup)]
        if apply:
            argv.append("--apply")
        with mock.patch.object(promotion, "ROOT", self.root), mock.patch("sys.argv", argv), contextlib.redirect_stdout(io.StringIO()):
            promotion.main()

    def assert_original_intact(self):
        self.assertEqual((self.canonical / "baseline_indexed_manifest.json").read_bytes(), self.old_manifest_bytes)
        self.assertEqual((self.canonical / self.series_name).read_bytes(), b"original series\n")
        self.assertFalse(self.backup.exists())

    def test_validation_only_does_not_move_data(self):
        self.invoke(apply=False)
        self.assert_original_intact()
        self.assertEqual(list(self.canonical.parent.glob(".baseline_receipt_v2_*")), [])

    def test_success_preserves_original_and_rewrites_manifest_paths(self):
        self.invoke()
        self.assertEqual((self.backup / "baseline_indexed_manifest.json").read_bytes(), self.old_manifest_bytes)
        self.assertEqual((self.backup / self.series_name).read_bytes(), b"original series\n")
        self.assertEqual((self.canonical / self.series_name).read_bytes(), (self.candidate / self.series_name).read_bytes())
        self.assertEqual((self.canonical / self.block_name).read_bytes(), self.block_bytes)
        new = json.loads((self.canonical / "baseline_indexed_manifest.json").read_text())
        self.assertEqual(new["five_minute_series"]["local_path"], str(self.canonical / self.series_name))
        self.assertEqual(new["query_checkpoint_directory"], str(self.canonical / "query_checkpoints"))
        self.assertEqual(new["nested_path"]["items"], [str(self.canonical / "query_checkpoints/0001.jsonl.gz")])
        self.assertTrue(self.candidate.exists())
        audit = json.loads((self.canonical / "ibc_receive_policy_audit.json").read_text())
        self.assertEqual(audit["old_baseline_manifest"], str(self.canonical / "baseline_indexed_manifest_before_success_v2.json"))
        self.assertEqual(audit["raw_sources"], self.audit["raw_sources"])
        self.assertEqual(new["ibc_receive_policy_audit"]["sha256"], promotion.digest(self.canonical / "ibc_receive_policy_audit.json"))
        self.assertEqual((self.canonical / "baseline_indexed_manifest_before_success_v2.json").read_bytes(), self.old_manifest_bytes)

    def test_changed_candidate_series_is_rejected(self):
        (self.candidate / self.series_name).write_bytes(b"unverified changed series\n")
        with self.assertRaises(SystemExit):
            self.invoke()
        self.assert_original_intact()

    def test_changed_candidate_manifest_is_rejected(self):
        path = self.candidate / "baseline_indexed_manifest.json"
        path.write_text(path.read_text() + "\n")
        with self.assertRaises(SystemExit):
            self.invoke()
        self.assert_original_intact()

    def test_policy_mismatch_is_rejected(self):
        self.manifest["ibc_inbound_extraction_policy"] = "legacy"
        self.refresh_verified_manifest()
        with self.assertRaises(SystemExit):
            self.invoke()
        self.assert_original_intact()

    def test_changed_canonical_manifest_is_rejected(self):
        (self.canonical / "baseline_indexed_manifest.json").write_bytes(b"canonical changed after verification\n")
        with self.assertRaises(SystemExit):
            self.invoke()
        self.assertFalse(self.backup.exists())
        self.assertEqual((self.canonical / self.series_name).read_bytes(), b"original series\n")

    def test_existing_backup_is_not_overwritten(self):
        self.backup.mkdir(parents=True)
        (self.backup / "user_file").write_bytes(b"keep this\n")
        with self.assertRaises(SystemExit):
            self.invoke()
        self.assertEqual((self.backup / "user_file").read_bytes(), b"keep this\n")
        self.assertEqual((self.canonical / self.series_name).read_bytes(), b"original series\n")

    def test_failed_staging_copy_keeps_canonical(self):
        with mock.patch.object(promotion.shutil, "copytree", side_effect=OSError("simulated copy failure")):
            with self.assertRaises(OSError):
                self.invoke()
        self.assert_original_intact()

    def test_failed_install_rename_rolls_original_back(self):
        original_rename = Path.rename
        def failing_install(path, target):
            if path.name.startswith(".baseline_receipt_v2_") and Path(target) == self.canonical:
                raise OSError("simulated install rename failure")
            return original_rename(path, target)
        with mock.patch.object(Path, "rename", autospec=True, side_effect=failing_install):
            with self.assertRaisesRegex(OSError, "simulated install rename failure"):
                self.invoke()
        self.assert_original_intact()

    def test_block_index_changed_after_verification_is_rejected(self):
        # Manifest bytes are unchanged, but the immutable shared block index is
        # no longer the independently verified input. Reject before promotion.
        (self.canonical / self.block_name).write_bytes(b"changed block index after verification\n")
        with self.assertRaises(SystemExit):
            self.invoke()
        self.assert_original_intact()

    def test_audit_changed_after_verification_is_rejected(self):
        changed = copy.deepcopy(self.audit)
        changed["status"] = "REVIEW_REQUIRED"
        changed["unresolved_native_exclusions"] = [{"needs_review": True}]
        self.audit_path.write_text(json.dumps(changed))
        with self.assertRaises(SystemExit):
            self.invoke()
        self.assert_original_intact()

    def test_missing_evidence_is_rejected_before_touching_canonical(self):
        self.manifest.pop("ibc_receive_evidence")
        self.refresh_verified_manifest()
        with self.assertRaises(SystemExit):
            self.invoke()
        self.assert_original_intact()

    def test_stale_external_evidence_path_is_rejected(self):
        self.manifest["ibc_receive_evidence"]["local_path"] = str(self.root / "other/ibc_receive_evidence.jsonl.gz")
        self.refresh_verified_manifest()
        with self.assertRaises(SystemExit):
            self.invoke()
        self.assert_original_intact()

    def test_live_checkpoint_path_is_relocated_and_nonlocal_provenance_is_preserved(self):
        path = self.candidate / "query_checkpoints/0001.jsonl.gz"
        write_deterministic_jsonl_gzip(path, [{"current_evidence": str(self.candidate / "ibc_receive_evidence.jsonl.gz"),
                                              "raw_sha256": "immutable-raw-hash"}])
        self.invoke()
        row = list(promotion.records(self.canonical / "query_checkpoints/0001.jsonl.gz"))[0]
        self.assertEqual(row["current_evidence"], str(self.canonical / "ibc_receive_evidence.jsonl.gz"))
        self.assertEqual(row["raw_sha256"], "immutable-raw-hash")

    def test_repository_relative_candidate_paths_are_relocated(self):
        for key in ("five_minute_series", "ibc_receive_policy_audit", "prior_baseline_snapshot", "ibc_receive_evidence", "ibc_raw_transaction_index"):
            self.manifest[key]["local_path"] = Path(self.manifest[key]["local_path"]).relative_to(self.root).as_posix()
        self.audit["old_baseline_manifest"] = (self.candidate / "baseline_indexed_manifest_before_success_v2.json").relative_to(self.root).as_posix()
        self.audit_path.write_text(json.dumps(self.audit))
        self.manifest["ibc_receive_policy_audit"]["sha256"] = promotion.digest(self.audit_path)
        self.refresh_verified_manifest()
        self.invoke()
        new = json.loads((self.canonical / "baseline_indexed_manifest.json").read_text())
        self.assertEqual(new["ibc_receive_evidence"]["local_path"], (self.canonical / "ibc_receive_evidence.jsonl.gz").relative_to(self.root).as_posix())
        audit = json.loads((self.canonical / "ibc_receive_policy_audit.json").read_text())
        self.assertEqual(audit["old_baseline_manifest"], (self.canonical / "baseline_indexed_manifest_before_success_v2.json").relative_to(self.root).as_posix())


if __name__ == "__main__":
    unittest.main()
