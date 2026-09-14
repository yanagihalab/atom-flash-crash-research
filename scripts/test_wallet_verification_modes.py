#!/usr/bin/env python3
"""Synthetic fixtures for explicit processed-only and fail-closed raw verification."""

from __future__ import annotations

import contextlib
import csv
import gzip
import io
import json
import tempfile
import unittest
import zipfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import verify_wallet_coordination as verifier


class VerificationModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.patch_root = patch.object(verifier, "PROJECT_ROOT", self.root)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)
        self.processed = self.root / "data/processed/cosmoshub"
        self.processed.mkdir(parents=True)
        (self.root / "metadata").mkdir()
        (self.root / "results").mkdir()
        self.atom = self.processed / "atom_transfers_2025-10-09_2025-10-12.jsonl.gz"
        self.ibc = self.processed / "ibc_transfers_2025-10-09_2025-10-12.jsonl.gz"
        self.checkpoint = self.processed / "baseline_30d/query_checkpoints/0165.jsonl.gz"
        self.labels = self.root / "metadata/exchange_address_labels.json"
        self.analysis_path = self.root / "results/wallet_coordination_analysis.json"
        self.full_output = self.root / "results/wallet_coordination_verification.json"
        self.processed_output = self.root / "results/wallet_coordination_processed_verification.json"
        self.trades = self.root / "data/raw/binance/spot/daily/trades/ATOMUSDT/ATOMUSDT-trades-2025-10-10.zip"
        self.agg = self.root / "data/raw/binance/spot/daily/aggTrades/ATOMUSDT/ATOMUSDT-aggTrades-2025-10-10.zip"

        def bank(sender: str, recipient: str, amount: float, seconds: int, memo: str, tx: str) -> dict:
            return {"flow_class": "direct_bank", "sender": sender, "recipient": recipient,
                    "amount_atom": amount, "time_utc": (verifier.FLASH_TIME - timedelta(seconds=seconds)).isoformat(),
                    "tx_memo": memo, "tx_hash": tx}

        rows = [bank("near", "binance", 1, 10800 + i, str(i % 10), f"old{i}") for i in range(58)]
        rows += [bank("near", "binance", 2000, 10, "0", "nearest"),
                 bank("top", "binance", 3000, 60, "0", "top"),
                 bank("kucoin", "near", 1234, 14400, "0", "funding")]
        self.write_jsonl(self.atom, rows)
        self.write_jsonl(self.ibc, [{"is_atom": True, "sender": "top", "receiver": "elsewhere",
                                    "counterparty_chain_hint": "osmo"} for _ in range(893)])
        self.write_jsonl(self.checkpoint, [{"partial": {}}])
        self.labels.write_text(json.dumps({"labels": [{"address": "binance", "label": "Binance"},
                                                       {"address": "kucoin", "label": "KuCoin"}]}))
        microseconds = int(verifier.FLASH_TIME.timestamp() * 1_000_000)
        self.write_zip(self.trades, [[100 + i, ".001", "1", ".001", microseconds, "true"] for i in range(92)])
        self.write_zip(self.agg, [[i, ".001", "1", 0, 0, microseconds, "true"] for i in range(71)])
        self.analysis = {
            "study_id": "synthetic",
            "source_sha256": {key: verifier.sha256_file(path) for key, path in {
                "atom_transfers": self.atom, "ibc_transfers": self.ibc, "labels": self.labels,
                "binance_30d_checkpoint": self.checkpoint, "binance_trades": self.trades,
                "binance_agg_trades": self.agg}.items()},
            "market_side_order_episode": {"raw_trade_count": 92, "first_trade_id": 100, "last_trade_id": 191,
                                          "aggressive_sell_quantity_atom": 92, "execution_quote_usdt": .092,
                                          "aggregate_trade_row_count": 71},
            "nearest_pre_low_binance_inflow": {"tx_hash": "nearest", "sender": "near", "seconds_before_low": 10,
                "sender_profile": {"binance_deposit_count": 59, "binance_unique_memo_cluster_count": 10},
                "direct_funding_from_public_label_kucoin_atom": 1234},
            "exact_pre_low_concentration": {"120m_sender": {"amount_atom": 5000, "group_count": 2,
                "top_groups": [{"key": "top"}], "top1_share": .6, "hhi": .52}},
            "binance_pre_low_inflow_30d_baseline": {f"{minutes}m": {
                "event_amount_atom": 3000, "rolling_5min_percentile_empirical": 100} for minutes in (30, 120)},
            "top_2h_sender_detail": {"profile": {"address": "top", "ibc_match_count": 893}},
            "common_one_hop_funding_sources_for_top10": [{"funded_top10_target_count": 2, "funded_top10_amount_atom": 1000}],
            "evidence_assessment": {"nearest_cosmos_sender_was_the_selling_account": "not_supported",
                "coordinated_wallet_manipulation_or_deliberate_intent": "not_established",
                "named_natural_person_or_beneficial_owner": "not_identifiable_from_public_data"},
            "bottom_line": "Intent is not established.",
            "required_nonpublic_evidence": ["order ID", "deposit-credit", "account", "timing"],
        }
        self.save_analysis()

    @staticmethod
    def write_jsonl(path: Path, rows: list) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")

    @staticmethod
    def write_zip(path: Path, rows: list) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = io.StringIO()
        csv.writer(stream).writerows(rows)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("synthetic.csv", stream.getvalue())

    def save_analysis(self) -> None:
        self.analysis_path.write_text(json.dumps(self.analysis))

    def run_verifier(self, *arguments: str) -> None:
        with patch("sys.argv", ["verify_wallet_coordination.py", *arguments]), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            verifier.main()

    def test_processed_without_raw_preserves_full_report_and_records_skips(self) -> None:
        self.trades.unlink()
        self.agg.unlink()
        self.full_output.write_text("existing full verification\n")
        self.run_verifier("--processed-only")
        report = json.loads(self.processed_output.read_text())
        self.assertEqual(report["verification_status"], "PASS")
        self.assertEqual(report["verification_mode"], "processed_only")
        self.assertEqual(len(report["skipped_checks"]), 3)
        self.assertNotIn("binance_trades", report["verified_source_sha256"])
        self.assertEqual(report["analysis_sha256"], verifier.sha256_file(self.analysis_path))
        self.assertEqual(self.full_output.read_text(), "existing full verification\n")

    def test_processed_rejects_stale_source_hash(self) -> None:
        self.analysis["source_sha256"]["ibc_transfers"] = "0" * 64
        self.save_analysis()
        with self.assertRaises(SystemExit) as raised:
            self.run_verifier("--processed-only")
        self.assertEqual(raised.exception.code, 1)
        report = json.loads(self.processed_output.read_text())
        self.assertEqual(report["verification_status"], "FAIL")
        self.assertFalse(next(row for row in report["checks"] if row["check"] == "processed_source_hashes")["passed"])

    def test_processed_rejects_stale_reported_wallet_metric(self) -> None:
        self.analysis["nearest_pre_low_binance_inflow"]["seconds_before_low"] = 11
        self.save_analysis()
        with self.assertRaises(SystemExit) as raised:
            self.run_verifier("--processed-only")
        self.assertEqual(raised.exception.code, 1)

    def test_processed_refuses_explicit_full_report_path(self) -> None:
        self.full_output.write_text("preserve")
        with self.assertRaises(SystemExit) as raised:
            self.run_verifier("--processed-only", "--output", str(self.full_output))
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(self.full_output.read_text(), "preserve")

    def test_default_full_requires_raw_no_silent_fallback(self) -> None:
        self.trades.unlink()
        with self.assertRaises(FileNotFoundError):
            self.run_verifier()
        self.assertFalse(self.processed_output.exists())
        self.assertFalse(self.full_output.exists())

    def test_default_full_runs_all_raw_checks(self) -> None:
        self.run_verifier()
        report = json.loads(self.full_output.read_text())
        self.assertEqual(report["verification_status"], "PASS")
        self.assertEqual(report["verification_mode"], "full_raw_and_processed")
        self.assertEqual(report["skipped_checks"], [])
        self.assertEqual(len(report["verified_source_sha256"]), 6)

    def test_full_rejects_stale_raw_source_hash(self) -> None:
        self.analysis["source_sha256"]["binance_trades"] = "0" * 64
        self.save_analysis()
        with self.assertRaises(SystemExit) as raised:
            self.run_verifier()
        self.assertEqual(raised.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
