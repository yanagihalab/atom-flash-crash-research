#!/usr/bin/env python3
"""Verify compact Cosmos baseline integrity and the overlapping full-raw day."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED = PROJECT_ROOT / "data" / "processed" / "cosmoshub"
START = datetime(2025, 9, 10, tzinfo=timezone.utc)
END = datetime(2025, 10, 10, tzinfo=timezone.utc)
OVERLAP_START = datetime(2025, 10, 9, tzinfo=timezone.utc)
OVERLAP_END = datetime(2025, 10, 10, tzinfo=timezone.utc)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def add_expected(
    expected: dict[int, dict[str, int]], timestamp: datetime, metric: str, amount_uatom: int
) -> None:
    bucket = int((timestamp - OVERLAP_START).total_seconds() // 300)
    if 0 <= bucket < 288:
        expected[bucket][f"{metric}_count"] += 1
        expected[bucket][f"{metric}_uatom"] += amount_uatom


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--baseline-root", type=Path, default=PROCESSED / "baseline_30d"
    )
    parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "results" / "cosmos_baseline_30d_verification.json"
    )
    args = parser.parse_args()
    manifest_path = args.baseline_root / "baseline_indexed_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    block_path = Path(manifest["block_times"]["local_path"])
    series_path = Path(manifest["five_minute_series"]["local_path"])
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: Any) -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    check("block_sha256", sha256_file(block_path) == manifest["block_times"]["sha256"], sha256_file(block_path))
    check("series_sha256", sha256_file(series_path) == manifest["five_minute_series"]["sha256"], sha256_file(series_path))

    block_count = 0
    first_height = last_height = None
    previous_height = None
    previous_time = None
    height_gaps = 0
    nonmonotonic_times = 0
    with gzip.open(block_path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            height = int(row["height"])
            timestamp = parse_time(row["time_utc"])
            if first_height is None:
                first_height = height
            if previous_height is not None and height != previous_height + 1:
                height_gaps += 1
            if previous_time is not None and timestamp < previous_time:
                nonmonotonic_times += 1
            previous_height, previous_time, last_height = height, timestamp, height
            block_count += 1
    check(
        "block_height_continuity",
        height_gaps == 0 and first_height == manifest["first_height"] and last_height == manifest["end_height_exclusive"] - 1,
        {"record_count": block_count, "first_height": first_height, "last_height": last_height, "gaps": height_gaps},
    )
    check("block_time_monotonicity", nonmonotonic_times == 0, {"nonmonotonic_pairs": nonmonotonic_times})

    series: dict[datetime, dict[str, Any]] = {}
    nonfinite = 0
    with gzip.open(series_path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            timestamp = parse_time(row["bucket_start_utc"])
            series[timestamp] = row
            for value in row.values():
                if isinstance(value, float) and not math.isfinite(value):
                    nonfinite += 1
    expected_rows = int((END - START).total_seconds() // 300)
    expected_times = {START + timedelta(minutes=5 * index) for index in range(expected_rows)}
    check(
        "five_minute_continuity",
        set(series) == expected_times and len(series) == expected_rows,
        {"actual_rows": len(series), "expected_rows": expected_rows, "missing": len(expected_times - set(series))},
    )
    check("five_minute_finite_values", nonfinite == 0, {"nonfinite_values": nonfinite})

    summaries = manifest["query_summaries"]
    distinct_endpoints = {row["rpc"] for row in manifest["endpoint_validation"]}
    check(
        "independent_endpoint_crosscheck",
        len(distinct_endpoints) >= 2,
        {"distinct_endpoint_count": len(distinct_endpoints), "endpoints": sorted(distinct_endpoints)},
    )
    check(
        "prespecified_query_scope",
        manifest["address_query_count"] == 307
        and manifest["excluded_watchlist_address_count"] == 49
        and manifest["confirmed_label_count"] == 4
        and manifest["query_count"] == 371,
        {
            "address_query_count": manifest["address_query_count"],
            "excluded_watchlist_address_count": manifest["excluded_watchlist_address_count"],
            "confirmed_label_count": manifest["confirmed_label_count"],
            "query_count": manifest["query_count"],
        },
    )
    candidates_path = PROCESSED / "exchange_inflow_candidates_2025-10-09_2025-10-12.json"
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))["candidates"]
    watchlist_addresses = {row["address"] for row in candidates if row["candidate_tier"] == "large_flow_watchlist"}
    queried_recipients = {
        row["label"].split(":", 1)[1] for row in summaries if row["label"].startswith("recipient:")
    }
    queried_watchlist = sorted(queried_recipients & watchlist_addresses)
    check(
        "watchlist_excluded_from_query_summaries",
        not queried_watchlist,
        {"queried_watchlist_addresses": queried_watchlist, "recipient_summary_count": len(queried_recipients)},
    )
    page_mismatches = [
        row["label"]
        for row in summaries
        if sum(int(count) for count in row["page_source_counts"].values()) != int(row["page_count"])
    ]
    check("query_page_provenance", not page_mismatches, {"mismatches": page_mismatches[:20]})
    check(
        "query_decode_errors",
        manifest["protobuf_or_packet_decode_errors"] == 0,
        {"decode_errors": manifest["protobuf_or_packet_decode_errors"]},
    )

    labels_path = PROJECT_ROOT / "metadata" / "exchange_address_labels.json"
    labels = {row["address"] for row in json.loads(labels_path.read_text(encoding="utf-8"))["labels"]}
    expected: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    atom_path = PROCESSED / "atom_transfers_2025-10-09_2025-10-12.jsonl.gz"
    with gzip.open(atom_path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            timestamp = parse_time(row["time_utc"])
            if not (OVERLAP_START <= timestamp < OVERLAP_END) or row["flow_class"] != "direct_bank":
                continue
            if row["recipient"] in labels:
                add_expected(expected, timestamp, "confirmed_exchange_in", int(row["amount_uatom"]))
            if row["sender"] in labels:
                add_expected(expected, timestamp, "confirmed_exchange_out", int(row["amount_uatom"]))
    ibc_path = PROCESSED / "ibc_transfers_2025-10-09_2025-10-12.jsonl.gz"
    with gzip.open(ibc_path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            timestamp = parse_time(row["time_utc"])
            if not (OVERLAP_START <= timestamp < OVERLAP_END) or not row["is_atom"]:
                continue
            metric = "ibc_inbound" if row["direction"] == "inbound" else "ibc_outbound"
            add_expected(expected, timestamp, metric, int(row["amount_base_units"]))

    differences: list[dict[str, Any]] = []
    totals: dict[str, dict[str, int]] = {}
    for metric in ("confirmed_exchange_in", "confirmed_exchange_out", "ibc_inbound", "ibc_outbound"):
        actual_count = actual_uatom = expected_count = expected_uatom = 0
        for bucket in range(288):
            timestamp = OVERLAP_START + timedelta(minutes=5 * bucket)
            actual = series[timestamp]
            a_count = int(actual[f"{metric}_count"])
            a_uatom = round(float(actual[f"{metric}_atom"]) * 1_000_000)
            e_count = expected[bucket][f"{metric}_count"]
            e_uatom = expected[bucket][f"{metric}_uatom"]
            actual_count += a_count
            actual_uatom += a_uatom
            expected_count += e_count
            expected_uatom += e_uatom
            if (a_count, a_uatom) != (e_count, e_uatom):
                differences.append(
                    {
                        "bucket_start_utc": timestamp.isoformat().replace("+00:00", "Z"),
                        "metric": metric,
                        "actual": [a_count, a_uatom],
                        "expected": [e_count, e_uatom],
                    }
                )
        totals[metric] = {
            "actual_count": actual_count,
            "actual_uatom": actual_uatom,
            "expected_count": expected_count,
            "expected_uatom": expected_uatom,
        }
    check("overlap_full_raw_exact_match", not differences, {"difference_count": len(differences), "totals": totals})

    passed = all(item["passed"] for item in checks)
    output = {
        "study_id": "atom_flash_crash_2025_10_10",
        "verification_status": "PASS" if passed else "FAIL",
        "baseline_manifest": manifest_path.relative_to(PROJECT_ROOT).as_posix(),
        "checks": checks,
        "overlap_differences_first_20": differences[:20],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "status": output["verification_status"], "checks": len(checks)}, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
