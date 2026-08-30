#!/usr/bin/env python3
"""Verify shifted-window comparison sources, definitions, and core results."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def close(actual: float, expected: float, tolerance: float = 1e-9) -> None:
    require(math.isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance), f"{actual} != {expected}")


def percentile(values: list[float], event: float) -> float:
    return 100 * sum(value <= event for value in values) / len(values)


def main() -> None:
    comparison_path = PROJECT_ROOT / "results" / "control_window_comparison.json"
    analysis_path = PROJECT_ROOT / "results" / "cosmoshub_flow_analysis.json"
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))

    require(comparison["window_length_hours"] == 2, "unexpected window length")
    focal = comparison["focal_windows"]
    require([row["window_id"] for row in focal] == [
        "matched_pre_day", "same_day_pre", "event", "matched_post_day1", "matched_post_day2"
    ], "unexpected focal window definitions")
    for row in focal:
        start = datetime.fromisoformat(row["start_utc"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(row["end_utc"].replace("Z", "+00:00"))
        require((end - start).total_seconds() == 7200, f"non-two-hour focal window: {row['window_id']}")

    event = next(row for row in focal if row["window_id"] == "event")
    require(event["start_utc"] == "2025-10-10T20:30:00Z", "event start mismatch")
    require(event["end_utc"] == "2025-10-10T22:30:00Z", "event end mismatch")
    require(focal[0]["start_utc"] == "2025-10-09T20:30:00Z", "primary control is not the previous matched UTC day")

    event_chain = event["chain"]
    totals = analysis["event_window_totals"]
    for metric in (
        "direct_transfer_atom", "structured_candidate_in_atom", "large_flow_watchlist_in_atom",
        "confirmed_exchange_in_atom", "ibc_inbound_atom", "ibc_outbound_atom", "ibc_net_inbound_atom",
    ):
        analysis_metric = "all_direct_transfer_atom" if metric == "direct_transfer_atom" else (
            "behavioral_candidate_inflow_atom" if metric == "structured_candidate_in_atom" else (
                "large_flow_watchlist_inflow_atom" if metric == "large_flow_watchlist_in_atom" else (
                    "confirmed_exchange_inflow_atom" if metric == "confirmed_exchange_in_atom" else metric
                )
            )
        )
        close(float(event_chain[metric]), float(totals[analysis_metric]))

    labeled_outflow = sum(row["event_window_outflow_atom"] for row in analysis["confirmed_exchange_addresses"])
    close(event_chain["confirmed_exchange_out_atom"], labeled_outflow)
    close(
        event_chain["confirmed_exchange_net_in_atom"],
        event_chain["confirmed_exchange_in_atom"] - event_chain["confirmed_exchange_out_atom"],
    )
    require(event["market"]["binance_atomusdt"]["low"] == 0.001, "event Binance low mismatch")

    source_checks: dict[str, Any] = {}
    for key in ("atom_transfers", "ibc_transfers", "candidates", "labels"):
        expected = comparison["source_sha256"][key]
        source_map = {
            "atom_transfers": PROJECT_ROOT / "data/processed/cosmoshub/atom_transfers_2025-10-09_2025-10-12.jsonl.gz",
            "ibc_transfers": PROJECT_ROOT / "data/processed/cosmoshub/ibc_transfers_2025-10-09_2025-10-12.jsonl.gz",
            "candidates": PROJECT_ROOT / "data/processed/cosmoshub/exchange_inflow_candidates_2025-10-09_2025-10-12.json",
            "labels": PROJECT_ROOT / "metadata/exchange_address_labels.json",
        }
        actual = sha256_file(source_map[key])
        require(actual == expected, f"source SHA-256 mismatch: {key}")
        source_checks[key] = {"sha256": actual, "status": "verified"}
    for relative, expected in comparison["source_sha256"]["market"].items():
        actual = sha256_file(PROJECT_ROOT / relative)
        require(actual == expected, f"market source SHA-256 mismatch: {relative}")
    source_checks["market_files"] = {"count": len(comparison["source_sha256"]["market"]), "status": "verified"}

    distribution = comparison["non_event_distribution"]
    windows = distribution["windows"]
    require(distribution["window_count"] == 36 == len(windows), "non-event control count mismatch")
    for row in windows:
        start = datetime.fromisoformat(row["start_utc"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(row["end_utc"].replace("Z", "+00:00"))
        require((end - start).total_seconds() == 7200, f"non-two-hour distribution window: {row['window_id']}")
        require(start.date().isoformat() in {"2025-10-09", "2025-10-11", "2025-10-12"}, "event date leaked into controls")

    metric_checks: dict[str, Any] = {}
    for metric, reported in distribution["chain_event_comparison"].items():
        values = [float(row["chain"][metric]) for row in windows]
        close(reported["control_mean"], statistics.fmean(values))
        close(reported["control_median"], statistics.median(values))
        close(reported["event_percentile_empirical"], percentile(values, float(event_chain[metric])))
        metric_checks[metric] = {
            "event": reported["event"],
            "control_median": reported["control_median"],
            "event_percentile_empirical": reported["event_percentile_empirical"],
            "status": "verified",
        }

    report = {
        "study_id": comparison["study_id"],
        "status": "verified",
        "window_definition": {
            "focal_count": len(focal),
            "non_event_distribution_count": len(windows),
            "duration_hours": 2,
            "primary_control_start_utc": focal[0]["start_utc"],
        },
        "source_checks": source_checks,
        "event_reconciliation": {
            "status": "matches_cosmoshub_flow_analysis",
            "chain": event_chain,
            "binance_atomusdt_low": event["market"]["binance_atomusdt"]["low"],
        },
        "distribution_metric_checks": metric_checks,
    }
    output = PROJECT_ROOT / "results" / "control_window_verification.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
