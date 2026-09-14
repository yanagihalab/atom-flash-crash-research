#!/usr/bin/env python3
"""Verify compact Cosmos baseline integrity and the overlapping full-raw day."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ibc_receive_evidence import IBC_INBOUND_POLICY
from rebuild_baseline_ibc_receipts import (
    canonical_update, inbound_day_bounds, records, require_local_artifact,
    resolve_reference, validate_day_query, validate_inbound_scope,
    validate_raw_transaction,
)


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


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def resolve_block_artifact(record, baseline_root, project_root):
    """Only the immutable block index may be shared with canonical at staging."""
    name = "block_times_2025-09-10_2025-10-10.jsonl.gz"
    path = resolve_reference(record.get("local_path", record.get("relative_path")), baseline_root, project_root)
    allowed = {(baseline_root / name).resolve(),
               (project_root / "data/processed/cosmoshub/baseline_30d" / name).resolve()}
    require(path in allowed, "Block index must be local or the canonical shared block index")
    if record.get("relative_path") is not None:
        require(resolve_reference(record["relative_path"], baseline_root, project_root) == path,
                "Conflicting block-index relative path")
    require(path.is_file() and sha256_file(path) == record.get("sha256"), "Block-index SHA-256 mismatch or missing file")
    return path


def verify_receipt_bundle(manifest, baseline_root, project_root, block_times, series):
    """Offline compact-evidence integrity; full raw is re-extracted by rebuild.

    All current artifacts must belong to this baseline. Historical paths *inside*
    the exact prior-manifest snapshot are provenance, not paths to dereference.
    This permits a release to rebind the snapshot/audit/manifest JSON hashes after
    an explicit path sanitization without changing raw/event/series digests.
    """
    summaries = manifest["query_summaries"]
    require(len(summaries) == manifest["query_count"] == 371, "Actual query count is not 371")
    require(len({q["index"] for q in summaries}) == len(summaries), "Duplicate query indices")
    require(len({q["label"] for q in summaries}) == len(summaries), "Duplicate query labels")
    queries = [q for q in summaries if q["label"].startswith("ibc_in:")]
    validate_inbound_scope(queries)
    require(manifest.get("ibc_inbound_extraction_policy") == IBC_INBOUND_POLICY
            and all(q.get("ibc_inbound_extraction_policy") == IBC_INBOUND_POLICY for q in queries),
            "Every inbound query must use receipt-success-v2")
    bounds = inbound_day_bounds(block_times, manifest["end_height_exclusive"])
    for query in queries:
        validate_day_query(query, bounds)
    by_day = {q["label"].split(":", 1)[1]: q for q in queries}
    names = {"ibc_receive_policy_audit": "ibc_receive_policy_audit.json",
             "ibc_receive_evidence": "ibc_receive_evidence.jsonl.gz",
             "prior_baseline_snapshot": "baseline_indexed_manifest_before_success_v2.json",
             "ibc_raw_transaction_index": "ibc_raw_transaction_index.jsonl.gz"}
    paths = {key: require_local_artifact(manifest[key], baseline_root, project_root, name)
             for key, name in names.items()}
    audit = json.loads(paths["ibc_receive_policy_audit"].read_text())
    snapshot = json.loads(paths["prior_baseline_snapshot"].read_text())
    snapshot_sha = sha256_file(paths["prior_baseline_snapshot"])
    require(audit.get("status") == "PASS" and audit.get("policy") == IBC_INBOUND_POLICY
            and audit.get("unresolved_native_exclusions") == [], "Missing or unresolved receipt audit")
    require(audit.get("old_baseline_manifest_sha256") == snapshot_sha, "Audit does not bind the exact prior snapshot")
    require(resolve_reference(audit["old_baseline_manifest"], baseline_root, project_root) == paths["prior_baseline_snapshot"],
            "Audit still references a different baseline's prior snapshot")
    if "old_baseline_manifest_snapshot_relative_path" in audit:
        require(resolve_reference(audit["old_baseline_manifest_snapshot_relative_path"], baseline_root, project_root)
                == paths["prior_baseline_snapshot"], "Conflicting audit snapshot relative reference")
    require(audit.get("legacy_digests_all_reproduced") is True
            and audit.get("non_inbound_bucket_metrics_unchanged") is True, "Missing legacy-preservation audit")
    prior_queries = snapshot["query_summaries"]
    prior_inbound = [q for q in prior_queries if q["label"].startswith("ibc_in:")]
    validate_inbound_scope(prior_inbound)
    require(len(prior_queries) == len(summaries) and {q["index"] for q in prior_queries} == {q["index"] for q in summaries},
            "Prior/current query scope differs")
    prior_by_day = {q["label"].split(":", 1)[1]: q for q in prior_inbound}
    prior_other = {q["index"]: q for q in prior_queries if not q["label"].startswith("ibc_in:")}
    require(prior_other == {q["index"]: q for q in summaries if not q["label"].startswith("ibc_in:")},
            "Non-inbound query summaries changed")
    days = audit["day_audits"]
    require(len(days) == audit["day_count"] == 30 and {d["day"] for d in days} == set(by_day),
            "Audit does not contain exactly 30 distinct expected days")
    day_audit = {d["day"]: d for d in days}
    seen_hashes, raw_by_hash, raw_counts = set(), {}, Counter()
    for row in records(paths["ibc_raw_transaction_index"]):
        validate_raw_transaction({"hash": row["tx_hash"], "height": row["height"]}, row["day"], block_times, seen_hashes)
        require(row["day"] in by_day and row["source_day_query_index"] == by_day[row["day"]]["index"],
                "Raw-index query/day does not match the exact inbound scope")
        require(isinstance(row["tx_index"], int) and row["tx_index"] >= 0
                and isinstance(row["tx_code"], int) and row["tx_code"] >= 0, "Malformed raw transaction index/code")
        raw_by_hash[row["tx_hash"]] = row
        raw_counts[row["day"]] += 1
    require(len(raw_by_hash) == manifest["ibc_raw_transaction_index"]["record_count"], "Raw-index record count mismatch")
    require(len({(r["height"], r["tx_index"]) for r in raw_by_hash.values()}) == len(raw_by_hash),
            "Duplicate raw transaction position")
    require(raw_counts == Counter({day: q["tx_count"] for day, q in by_day.items()}), "Raw-index day counts are incomplete")
    integrity = audit["raw_global_integrity"]
    require(integrity.get("transaction_count") == integrity.get("unique_tx_hash_count") == len(raw_by_hash)
            and integrity.get("all_day_memberships_verified") is True
            and integrity.get("all_raw_transactions_indexed") is True, "Raw-global audit differs from compact index")
    evidence_by_day, event_keys, evidence_count = defaultdict(list), set(), 0
    expected_inbound = defaultdict(Counter)
    success_used, credit_used = set(), set()
    for row in records(paths["ibc_receive_evidence"]):
        tx = raw_by_hash.get(row["tx_hash"])
        require(tx is not None and tx["tx_code"] == 0 and tx["day"] == row["day"] and tx["height"] == row["height"],
                "Receipt evidence has no corresponding successful indexed transaction")
        key = (row["tx_hash"], row["event_ordinal"])
        require(key not in event_keys, "Duplicate receipt evidence event")
        event_keys.add(key)
        require(row.get("policy") == IBC_INBOUND_POLICY, "Evidence uses an unknown receipt policy")
        require(len(row["packet_key"]) == 6 and row["packet_key"][0] == row["packet_key"][2] == "transfer"
                and type(row["amount_uatom"]) is int and row["amount_uatom"] >= 0, "Malformed receipt denomination/amount/key")
        native_trace = row["denom"] == f"{row['packet_key'][0]}/{row['packet_key'][1]}/uatom"
        require(row.get("native_atom_trace") is native_trace, "Native-return classification contradicts the packet trace")
        if row.get("exclusion_reason") == "not_native_atom_return_trace":
            require(not native_trace, "Native receipt incorrectly excluded as a foreign trace")
        if row["include_in_atom_flow"]:
            require(all(row.get(k) is True for k in ("application_success", "native_atom_trace", "native_credit_matched"))
                    and row.get("exclusion_reason") is None and "error" not in row["immediate_ack_states"],
                    "Included native receipt lacks success/credit evidence")
            require(row["denom"] == f"{row['packet_key'][0]}/{row['packet_key'][1]}/uatom",
                    "Included native receipt has a foreign denomination trace")
            success_key = (row["tx_hash"], row["success_event_ordinal"])
            credit_key = (row["tx_hash"], row["native_credit_event_ordinal"])
            require(success_key not in success_used and credit_key not in credit_used, "Success or credit event reused")
            success_used.add(success_key)
            credit_used.add(credit_key)
            bucket = int((block_times[row["height"]] - START).total_seconds() // 300)
            expected_inbound[bucket]["ibc_inbound_count"] += 1
            expected_inbound[bucket]["ibc_inbound_uatom"] += row["amount_uatom"]
        elif row["denom"] == "uatom" or row["denom"].endswith("/uatom"):
            require(row["exclusion_reason"] in {"application_error", "not_native_atom_return_trace"},
                    "Unresolved native receipt exclusion in evidence")
        evidence_by_day[row["day"]].append(row)
        evidence_count += 1
    require(evidence_count == manifest["ibc_receive_evidence"]["record_count"], "Evidence record count mismatch")
    excluded_count = excluded_uatom = 0
    for day, query in by_day.items():
        old, daily = prior_by_day[day], day_audit[day]
        validate_day_query(old, bounds)
        require(old["index"] == query["index"] and old["tx_count"] == query["tx_count"]
                and old["page_count"] == query["page_count"], "Inbound source query changed")
        require(query["raw_transactions"] == daily["raw"], "Raw provenance differs between query and audit")
        rows = sorted(evidence_by_day[day], key=lambda r: (r["height"], raw_by_hash[r["tx_hash"]]["tx_index"], r["event_ordinal"]))
        legacy = [r for r in rows if r["denom"] == "uatom" or r["denom"].endswith("/uatom")]
        included = [r for r in rows if r["include_in_atom_flow"]]
        excluded = [r for r in legacy if not r["include_in_atom_flow"]]
        for selected, summary, prefix in ((legacy, old, "legacy"), (included, query, "corrected")):
            digest = hashlib.sha256()
            for row in selected:
                canonical_update(digest, {k: row[k] for k in ("height", "tx_hash", "event_ordinal", "amount_uatom")})
            total = sum(r["amount_uatom"] for r in selected)
            require((len(selected), total, digest.hexdigest()) ==
                    (summary["extracted_event_count"], summary["extracted_amount_uatom"], summary["extracted_events_sha256"]),
                    f"{day} {prefix} evidence digest/totals mismatch")
            require(daily[f"{prefix}_count"] == len(selected) and daily[f"{prefix}_uatom"] == total,
                    f"{day} {prefix} audit totals mismatch")
        require(daily["legacy_digest_reproduced"] is True and daily["unresolved_native_exclusions"] == [],
                "Unresolved or unverified day audit")
        require(daily["excluded_legacy_atom_packets"] == excluded
                and daily["excluded_legacy_atom_count"] == len(excluded)
                and daily["excluded_legacy_atom_uatom"] == sum(r["amount_uatom"] for r in excluded),
                "Excluded-packet audit differs from evidence")
        excluded_count += len(excluded)
        excluded_uatom += sum(r["amount_uatom"] for r in excluded)
    require((excluded_count, excluded_uatom) == (audit["excluded_count"], audit["excluded_uatom"]), "Global exclusion totals mismatch")
    require(audit["raw_sources"] == [day_audit[q["label"].split(":", 1)[1]]["raw"] for q in sorted(queries, key=lambda q: q["index"])],
            "Global raw provenance is incomplete")
    checkpoint_root = baseline_root / "query_checkpoints"
    require(resolve_reference(manifest["query_checkpoint_directory"], baseline_root, project_root) == checkpoint_root.resolve(),
            "Checkpoint directory must belong to this baseline")
    preserved = audit["preserved_non_inbound_checkpoints"]
    require(len(preserved) == len(prior_other) and {r["index"] for r in preserved} == set(prior_other),
            "Missing or duplicated preserved-checkpoint hashes")
    preserved_hashes = {r["index"]: r["sha256"] for r in preserved}
    combined = defaultdict(Counter)
    for query in summaries:
        path = checkpoint_root / f"{query['index']:04d}.jsonl.gz"
        require(path.resolve().parent == checkpoint_root.resolve() and path.is_file(), "Missing/locality-violating checkpoint")
        if query["index"] in preserved_hashes:
            require(sha256_file(path) == preserved_hashes[query["index"]], "Preserved non-inbound checkpoint changed")
        rows = list(records(path))
        require(len(rows) == 1 and {k: v for k, v in rows[0].items() if k != "partial"} == query,
                "Checkpoint summary differs from manifest")
        for bucket, values in rows[0]["partial"].items():
            require(0 <= int(bucket) < 8640, "Checkpoint bucket outside baseline")
            combined[int(bucket)].update(values)
    metrics = ("confirmed_exchange_in", "confirmed_exchange_out", "unconfirmed_behavioral_in", "ibc_inbound", "ibc_outbound")
    require(len(series) == 8640, "Expected all 8,640 five-minute buckets")
    for bucket in range(8640):
        row, values = series[START + timedelta(minutes=5 * bucket)], combined[bucket]
        require(values["ibc_inbound_count"] == expected_inbound[bucket]["ibc_inbound_count"]
                and values["ibc_inbound_uatom"] == expected_inbound[bucket]["ibc_inbound_uatom"], "Inbound checkpoint differs from evidence")
        for metric in metrics:
            require(row[f"{metric}_count"] == values[f"{metric}_count"]
                    and Decimal(str(row[f"{metric}_atom"])) * 1_000_000 == values[f"{metric}_uatom"],
                    "Five-minute series differs from retained checkpoint inputs")
        derived = {"confirmed_exchange_net_in_atom": values["confirmed_exchange_in_uatom"] - values["confirmed_exchange_out_uatom"],
                   "all_behavioral_candidate_in_atom": values["confirmed_exchange_in_uatom"] + values["unconfirmed_behavioral_in_uatom"],
                   "ibc_net_inbound_atom": values["ibc_inbound_uatom"] - values["ibc_outbound_uatom"]}
        require(all(Decimal(str(row[k])) * 1_000_000 == value for k, value in derived.items()), "Derived series metrics mismatch")
    return {"day_count": 30, "raw_transaction_count": len(raw_by_hash), "receipt_evidence_count": evidence_count,
            "excluded_count": excluded_count, "excluded_uatom": excluded_uatom,
            "scope": "Compact evidence/index/checkpoint verification; full raw extraction and raw-file hashing occur in rebuild_baseline_ibc_receipts.py."}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--baseline-root", type=Path, default=PROCESSED / "baseline_30d"
    )
    parser.add_argument(
        "--output", type=Path, default=PROJECT_ROOT / "results" / "cosmos_baseline_30d_verification.json"
    )
    args = parser.parse_args()
    args.baseline_root = args.baseline_root.resolve()
    manifest_path = args.baseline_root / "baseline_indexed_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    block_path = resolve_block_artifact(manifest["block_times"], args.baseline_root, PROJECT_ROOT)
    series_path = require_local_artifact(manifest["five_minute_series"], args.baseline_root, PROJECT_ROOT,
                                         "baseline_5min_2025-09-10_2025-10-10.jsonl.gz")
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
    block_times = {}
    with gzip.open(block_path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            height = int(row["height"])
            timestamp = parse_time(row["time_utc"])
            block_times[height] = timestamp
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
    check("block_baseline_date_bounds", block_count == manifest["block_times"]["record_count"]
          and block_count == manifest["end_height_exclusive"] - manifest["first_height"]
          and all(START <= timestamp < END for timestamp in block_times.values()),
          {"required_start_inclusive": START.isoformat(), "required_end_exclusive": END.isoformat()})

    series: dict[datetime, dict[str, Any]] = {}
    nonfinite = 0
    series_record_count = 0
    with gzip.open(series_path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            series_record_count += 1
            timestamp = parse_time(row["bucket_start_utc"])
            series[timestamp] = row
            for value in row.values():
                if isinstance(value, float) and not math.isfinite(value):
                    nonfinite += 1
    expected_rows = int((END - START).total_seconds() // 300)
    expected_times = {START + timedelta(minutes=5 * index) for index in range(expected_rows)}
    check(
        "five_minute_continuity",
        set(series) == expected_times and len(series) == series_record_count == expected_rows == manifest["five_minute_series"]["record_count"],
        {"actual_rows": series_record_count, "unique_timestamps": len(series), "expected_rows": expected_rows, "missing": len(expected_times - set(series))},
    )
    check("five_minute_finite_values", nonfinite == 0, {"nonfinite_values": nonfinite})

    summaries = manifest["query_summaries"]
    inbound_queries = [row for row in summaries if row["label"].startswith("ibc_in:")]
    check("ibc_inbound_success_policy", len(inbound_queries) == 30 and manifest.get("ibc_inbound_extraction_policy") == IBC_INBOUND_POLICY
          and all(row.get("ibc_inbound_extraction_policy") == IBC_INBOUND_POLICY for row in inbound_queries),
          {"required_policy": IBC_INBOUND_POLICY, "query_count": len(inbound_queries),
           "note": "Legacy aggregate-only receive checkpoints cannot certify application success; re-audit raw receives before certification."})
    try:
        detail = verify_receipt_bundle(manifest, args.baseline_root, PROJECT_ROOT, block_times, series)
    except (ValueError, KeyError, TypeError, OSError, EOFError) as error:
        check("ibc_receive_required_bundle_integrity", False, {"error": str(error)})
    else:
        check("ibc_receive_required_bundle_integrity", True, detail)
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
        and manifest["query_count"] == len(summaries) == 371,
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
        "baseline_manifest": str(manifest_path.relative_to(PROJECT_ROOT)),
        "verified_inputs": {
            "manifest_sha256": sha256_file(manifest_path),
            "series_sha256": sha256_file(series_path),
            "block_times_sha256": sha256_file(block_path),
        },
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
