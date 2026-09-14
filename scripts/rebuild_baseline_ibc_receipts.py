"""Replace only 30 inbound queries in a separate baseline directory, offline.

Never mutates the old baseline or raw files. Requires all 30 retained raw query
days, reproduces every legacy extraction digest before applying the new policy,
and preserves every direct-bank/outbound bucket value. No network is used.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import shutil

from collect_cosmos_baseline_indexed import write_deterministic_jsonl_gzip, load_block_times
from extract_cosmos_flows import atom_flag, packet_fields
from ibc_receive_evidence import IBC_INBOUND_POLICY, assess_receives

ROOT = Path(__file__).resolve().parents[1]
BASELINE_START = datetime(2025, 9, 10, tzinfo=timezone.utc)
BASELINE_END = datetime(2025, 10, 10, tzinfo=timezone.utc)
EXPECTED_INBOUND_LABELS = {f"ibc_in:{(BASELINE_START + timedelta(days=i)).date()}" for i in range(30)}


def validate_inbound_scope(queries):
    """Reject missing/duplicated days or indices, including vacuous all([])."""
    if len(queries) != 30 or {q["label"] for q in queries} != EXPECTED_INBOUND_LABELS:
        raise ValueError("Expected exactly the 30 distinct inbound days 2025-09-10 through 2025-10-09")
    if len({q["index"] for q in queries}) != 30:
        raise ValueError("Inbound query indices must be unique")


def resolve_reference(value, baseline_root, project_root):
    """Accept research absolute paths, project-relative paths, or bare local names.

    Release exporters may use data/... relative to the repository root. A bare
    filename is relative to the baseline directory. No snapshot contents are
    rewritten here: the exact snapshot bytes are a separately hashed artifact.
    """
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    if path.parts and path.parts[0] in {"data", "metadata", "scripts", "results", "outputs"}:
        return (project_root / path).resolve()
    return (baseline_root / path).resolve()


def require_local_artifact(record, baseline_root, project_root, filename):
    """Validate both reference and hash; fail if an old staging path survives."""
    expected = (baseline_root / filename).resolve()
    if expected.parent != baseline_root.resolve():
        raise ValueError(f"Artifact escapes its baseline directory: {filename}")
    value = record.get("local_path", record.get("relative_path"))
    if value is None or resolve_reference(value, baseline_root, project_root) != expected:
        raise ValueError(f"Artifact reference does not identify this baseline's {filename}")
    if record.get("relative_path") is not None and resolve_reference(record["relative_path"], baseline_root, project_root) != expected:
        raise ValueError(f"Conflicting relative reference for {filename}")
    if not expected.is_file() or sha(expected) != record.get("sha256"):
        raise ValueError(f"Missing artifact or SHA-256 mismatch: {filename}")
    return expected


def validate_raw_transaction(tx, day, block_times, seen_hashes):
    """Validate global uniqueness and true day membership for *every* raw tx."""
    tx_hash = tx["hash"]
    if not isinstance(tx_hash, str) or len(tx_hash) != 64 or any(c not in "0123456789ABCDEF" for c in tx_hash):
        raise ValueError("Raw transaction hash must use canonical 64-digit uppercase hexadecimal")
    if tx_hash in seen_hashes:
        raise ValueError(f"Raw transaction repeated across days: {tx_hash}")
    height = int(tx["height"])
    if height not in block_times or block_times[height].date().isoformat() != day:
        raise ValueError(f"Raw transaction height {height} does not belong to {day}")
    seen_hashes.add(tx_hash)


def inbound_day_bounds(block_times, end_height):
    """Compute exact day-height bounds from the retained immutable block index."""
    first = {}
    for height, timestamp in sorted(block_times.items()):
        if not BASELINE_START <= timestamp < BASELINE_END:
            raise ValueError("Block timestamp lies outside the 30-day baseline")
        first.setdefault(timestamp.date().isoformat(), height)
    days = sorted(label.split(":", 1)[1] for label in EXPECTED_INBOUND_LABELS)
    if set(first) != set(days):
        raise ValueError("Block index must cover all 30 baseline dates")
    return {day: (first[day], first[days[i + 1]] if i + 1 < len(days) else end_height)
            for i, day in enumerate(days)}


def inbound_query_text(first, end):
    return f"recv_packet.packet_dst_port='transfer' AND tx.height>={first} AND tx.height<{end}"


def validate_day_query(query, bounds):
    first, end = bounds[query["label"].split(":", 1)[1]]
    if hashlib.sha256(inbound_query_text(first, end).encode()).hexdigest() != query["query_sha256"]:
        raise ValueError(f"Inbound query has incorrect date/height bounds: {query['label']}")


def validate_acquisition_bundle(raw_root, queries, paths, bounds, block_sha):
    """Bind re-extraction to hashes recorded when the raw collector completed."""
    manifest_path = raw_root / "manifest.json"
    collected = json.loads(manifest_path.read_text())
    if collected.get("status") != "complete" or collected.get("chain_id") != "cosmoshub-4":
        raise ValueError("Raw collection manifest must certify complete Cosmos Hub acquisition")
    summaries = collected.get("day_summaries", [])
    expected_days = {q["label"].split(":", 1)[1] for q in queries}
    if len(summaries) != 30 or {r["day"] for r in summaries} != expected_days:
        raise ValueError("Raw acquisition manifest must contain exactly 30 complete days")
    if collected.get("block_time_index", {}).get("sha256") != block_sha:
        raise ValueError("Raw acquisition used a different block-time index")
    if collected.get("unique_tx_count") != sum(q["tx_count"] for q in queries):
        raise ValueError("Raw acquisition global transaction count differs from legacy query scope")
    by_day = {r["day"]: r for r in summaries}
    verified = {}
    for query in queries:
        day = query["label"].split(":", 1)[1]
        summary = by_day[day]
        day_path = raw_root / day / "day_manifest.json"
        if json.loads(day_path.read_text()) != summary:
            raise ValueError(f"Raw day manifest differs from completed acquisition summary: {day}")
        if (summary.get("status") != "complete" or summary.get("index") != query["index"]
                or summary.get("label") != query["label"] or summary.get("query_sha256") != query["query_sha256"]
                or (summary.get("first_height"), summary.get("end_height_exclusive")) != bounds[day]
                or summary.get("total_count") != query["tx_count"] or summary.get("unique_tx_count") != query["tx_count"]
                or summary.get("pages") != query["page_count"]):
            raise ValueError(f"Raw day acquisition metadata differs from exact legacy query: {day}")
        expected_path = (raw_root / summary["tx_search_path"]).resolve()
        if (expected_path != paths[day].resolve() or expected_path != (raw_root / day / "tx_search.jsonl.gz").resolve()
                or sha(expected_path) != summary["tx_search_sha256"]):
            raise ValueError(f"Raw gzip differs from its acquisition-time SHA-256: {day}")
        verified[day] = {"raw_sha256": summary["tx_search_sha256"], "day_manifest_sha256": sha(day_path)}
    return {"local_path": str(manifest_path.resolve()), "sha256": sha(manifest_path),
            "status": "complete", "day_count": 30}, verified


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def records(path):
    with gzip.open(path, "rt") as f:
        yield from (json.loads(line) for line in f)


def canonical_update(digest, event):
    digest.update(json.dumps({"metric": "ibc_inbound", **event}, sort_keys=True, separators=(",", ":")).encode() + b"\n")


def legacy_events(tx):
    if int(tx["tx_result"].get("code", 0)) != 0:
        return []
    result = []
    for ordinal, event in enumerate(tx["tx_result"].get("events") or []):
        if event["type"] != "recv_packet":
            continue
        packet = packet_fields(event)
        if packet["src_port"] != "transfer" or packet["dst_port"] != "transfer" or not packet["packet_data_hex"]:
            continue
        data = json.loads(bytes.fromhex(packet["packet_data_hex"]).decode())
        if atom_flag("inbound", str(data["denom"])):
            result.append({"height": int(tx["height"]), "tx_hash": tx["hash"],
                           "event_ordinal": ordinal, "amount_uatom": int(data["amount"])})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--baseline-root", type=Path, default=ROOT / "data/processed/cosmoshub/baseline_30d")
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    source, output = args.baseline_root.resolve(), args.output_root.resolve()
    if source == output or source in output.parents or output in source.parents:
        raise SystemExit("Output must be a separate sibling or staging directory, never the old baseline or its parent/child")
    if output.exists():
        raise SystemExit("Output already exists; choose a new directory to preserve earlier results")
    manifest_path = source / "baseline_indexed_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    queries = [q for q in manifest["query_summaries"] if q["label"].startswith("ibc_in:")]
    validate_inbound_scope(queries)
    paths = {}
    for path in args.raw_root.rglob("tx_search.jsonl.gz"):
        with gzip.open(path, "rt") as f:
            first = json.loads(next(f))
        day = first.get("day") or first.get("label", "").removeprefix("ibc_in:")
        if day in paths:
            raise SystemExit(f"Duplicate complete raw day {day}")
        paths[day] = path
    missing = [q["label"] for q in queries if q["label"].split(":", 1)[1] not in paths]
    if missing:
        raise SystemExit(f"Missing full raw days; old baseline untouched: {missing}")
    if set(paths) != {label.split(":", 1)[1] for label in EXPECTED_INBOUND_LABELS}:
        raise ValueError("Raw input must contain exactly the 30 expected complete days")
    block_path = resolve_reference(manifest["block_times"]["local_path"], source, ROOT)
    old_series_path = require_local_artifact(manifest["five_minute_series"], source, ROOT,
                                            "baseline_5min_2025-09-10_2025-10-10.jsonl.gz")
    assert sha(block_path) == manifest["block_times"]["sha256"]
    assert sha(old_series_path) == manifest["five_minute_series"]["sha256"]
    block_times = load_block_times(block_path, manifest["first_height"], manifest["end_height_exclusive"])
    bounds = inbound_day_bounds(block_times, manifest["end_height_exclusive"])
    acquisition, acquired_days = validate_acquisition_bundle(args.raw_root, queries, paths, bounds,
                                                              manifest["block_times"]["sha256"])
    start = BASELINE_START
    old_series = list(records(old_series_path))
    assert len(old_series) == 8640
    replacements, day_audits, raw_sources, evidence = {}, [], [], []
    combined = defaultdict(Counter)
    seen_raw_hashes, raw_index = set(), []
    for query in sorted(queries, key=lambda q: q["index"]):
        validate_day_query(query, bounds)
        day = query["label"].split(":", 1)[1]
        path = paths[day]
        txs, page_numbers, page_sources = [], [], Counter()
        for page in records(path):
            assert page["query_sha256"] == query["query_sha256"], (day, "query mismatch")
            if page.get("day") != day or page.get("label", query["label"]) != query["label"]:
                raise ValueError(f"Raw page metadata belongs to the wrong day: {day}")
            if "query" in page and hashlib.sha256(page["query"].encode()).hexdigest() != query["query_sha256"]:
                raise ValueError(f"Raw query text does not match its query digest: {day}")
            if (int(page["first_height"]), int(page["end_height_exclusive"])) != bounds[day]:
                raise ValueError(f"Raw page height bounds do not identify {day}")
            response = page.get("response", page)
            result = response["result"]
            assert int(result["total_count"]) == query["tx_count"], (day, "raw total differs from old query")
            txs.extend(result.get("txs") or [])
            page_numbers.append(int(page["page"]))
            page_sources[page["source_rpc"]] += 1
        assert len(txs) == query["tx_count"] and len({tx["hash"] for tx in txs}) == len(txs), (day, "transaction completeness")
        assert sorted(page_numbers) == list(range(1, query["page_count"] + 1)), (day, "page completeness")
        txs.sort(key=lambda tx: (int(tx["height"]), int(tx["index"])))
        old_digest, new_digest = hashlib.sha256(), hashlib.sha256()
        old_amount = old_count = new_amount = new_count = decode_errors = 0
        partial = defaultdict(Counter)
        exclusions, outcomes = [], Counter()
        for tx in txs:
            validate_raw_transaction(tx, day, block_times, seen_raw_hashes)
            raw_index.append({"day": day, "height": int(tx["height"]), "tx_index": int(tx["index"]),
                              "tx_hash": tx["hash"], "tx_code": int(tx["tx_result"].get("code", 0)),
                              "source_day_query_index": query["index"]})
            for event in legacy_events(tx):
                canonical_update(old_digest, event)
                old_count += 1
                old_amount += event["amount_uatom"]
            if int(tx["tx_result"].get("code", 0)) != 0:
                continue
            for decision in assess_receives(tx["tx_result"].get("events") or []):
                outcome = decision.get("exclusion_reason") or "included_native_atom_receipt"
                outcomes[outcome] += 1
                if decision["packet_data"] is None:
                    decode_errors += 1
                    continue
                data = decision["packet_data"]
                attrs = decision["packet_attributes"]
                row = {"day": day, "height": int(tx["height"]), "tx_hash": tx["hash"],
                       "event_ordinal": decision["event_ordinal"], "amount_uatom": int(data["amount"]),
                       "sender": data["sender"], "receiver": data["receiver"], "denom": data["denom"],
                       "packet_key": [attrs.get(k) for k in ("packet_src_port", "packet_src_channel", "packet_dst_port", "packet_dst_channel", "packet_sequence", "msg_index")],
                       **{k: v for k, v in decision.items() if k not in {"packet_attributes", "packet_data", "event_ordinal"}}}
                evidence.append(row)
                if atom_flag("inbound", data["denom"]) and not decision["include_in_atom_flow"]:
                    exclusions.append(row)
                if not decision["include_in_atom_flow"]:
                    continue
                event = {k: row[k] for k in ("height", "tx_hash", "event_ordinal", "amount_uatom")}
                bucket = int((block_times[event["height"]] - start).total_seconds() // 300)
                assert 0 <= bucket < 8640
                canonical_update(new_digest, event)
                partial[bucket]["ibc_inbound_count"] += 1
                partial[bucket]["ibc_inbound_uatom"] += event["amount_uatom"]
                new_count += 1
                new_amount += event["amount_uatom"]
        assert old_digest.hexdigest() == query["extracted_events_sha256"], (day, "legacy event digest mismatch")
        assert (old_count, old_amount) == (query["extracted_event_count"], query["extracted_amount_uatom"]), (day, "legacy totals mismatch")
        assert decode_errors == 0
        for bucket, values in partial.items():
            combined[bucket].update(values)
        if sha(path) != acquired_days[day]["raw_sha256"]:
            raise ValueError(f"Raw gzip changed during re-extraction: {day}")
        raw_source = {"local_path": str(path.resolve()), "sha256": acquired_days[day]["raw_sha256"],
                      "size_bytes": path.stat().st_size,
                      "acquisition_day_manifest_sha256": acquired_days[day]["day_manifest_sha256"]}
        raw_sources.append(raw_source)
        replacement = {**query, "preferred_rpc": next(iter(page_sources)), "page_source_counts": dict(page_sources),
                       "endpoint_failovers": [], "extracted_event_count": new_count, "extracted_amount_uatom": new_amount,
                       "extracted_events_sha256": new_digest.hexdigest(), "decode_errors": decode_errors,
                       "partial": {k: dict(v) for k, v in partial.items()}, "raw_transactions": raw_source,
                       "ibc_inbound_extraction_policy": IBC_INBOUND_POLICY}
        replacements[query["index"]] = replacement
        day_audits.append({"day": day, "legacy_digest_reproduced": True, "legacy_count": old_count, "legacy_uatom": old_amount,
                           "corrected_count": new_count, "corrected_uatom": new_amount, "outcomes": dict(outcomes),
                           "excluded_legacy_atom_count": len(exclusions), "excluded_legacy_atom_uatom": sum(r["amount_uatom"] for r in exclusions),
                           "excluded_legacy_atom_packets": exclusions,
                           "unresolved_native_exclusions": [r for r in exclusions if r["exclusion_reason"] not in
                                                            {"application_error", "not_native_atom_return_trace"}],
                           "raw": raw_source})
        print(f"verified {day}: native receipts {old_count}->{new_count}, excluded {old_amount - new_amount} uatom", flush=True)
    series = []
    for bucket, previous in enumerate(old_series):
        row = dict(previous)
        values = combined[bucket]
        row["ibc_inbound_count"] = values["ibc_inbound_count"]
        row["ibc_inbound_atom"] = values["ibc_inbound_uatom"] / 1_000_000
        outbound_uatom = int(Decimal(str(row["ibc_outbound_atom"])) * 1_000_000)
        row["ibc_net_inbound_atom"] = (values["ibc_inbound_uatom"] - outbound_uatom) / 1_000_000
        assert all(row[k] == v for k, v in previous.items() if k not in {"ibc_inbound_count", "ibc_inbound_atom", "ibc_net_inbound_atom"})
        series.append(row)
    output.mkdir(parents=True)
    # Keep the exact small original manifest with the audit. Its old absolute
    # location may later become the corrected canonical baseline after promotion.
    prior_manifest_snapshot = output / "baseline_indexed_manifest_before_success_v2.json"
    shutil.copy2(manifest_path, prior_manifest_snapshot)
    assert sha(prior_manifest_snapshot) == sha(manifest_path)
    checkpoint_root = output / "query_checkpoints"
    checkpoint_root.mkdir()
    preserved_checkpoints = []
    for old in manifest["query_summaries"]:
        target = checkpoint_root / f"{old['index']:04d}.jsonl.gz"
        if old["index"] in replacements:
            write_deterministic_jsonl_gzip(target, [replacements[old["index"]]])
        else:
            original = source / "query_checkpoints" / target.name
            shutil.copy2(original, target)
            assert sha(original) == sha(target)
            preserved_checkpoints.append({"index": old["index"], "sha256": sha(original)})
    # Block metadata is immutable and shared by path; no duplicate 30-day index.
    series_path = output / old_series_path.name
    write_deterministic_jsonl_gzip(series_path, series)
    audit_path = output / "ibc_receive_policy_audit.json"
    unresolved = [r for day in day_audits for r in day["unresolved_native_exclusions"]]
    audit = {"status": "REVIEW_REQUIRED" if unresolved else "PASS", "policy": IBC_INBOUND_POLICY,
             "old_baseline_manifest": str(prior_manifest_snapshot),
             "old_baseline_manifest_snapshot_relative_path": prior_manifest_snapshot.name,
             "old_baseline_original_location": str(manifest_path),
             "old_baseline_manifest_sha256": sha(manifest_path), "day_count": 30, "day_audits": day_audits,
             "legacy_digests_all_reproduced": True, "non_inbound_bucket_metrics_unchanged": True,
             "raw_sources": raw_sources, "excluded_count": sum(r["excluded_legacy_atom_count"] for r in day_audits),
             "excluded_uatom": sum(r["excluded_legacy_atom_uatom"] for r in day_audits),
             "unresolved_native_exclusions": unresolved,
             "preserved_non_inbound_checkpoints": preserved_checkpoints,
             "raw_acquisition_manifest": acquisition,
             "extraction_code": {name: sha(ROOT / "scripts" / name) for name in
                                 ("ibc_receive_evidence.py", "collect_cosmos_baseline_indexed.py", "rebuild_baseline_ibc_receipts.py")}}
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    evidence_path = output / "ibc_receive_evidence.jsonl.gz"
    write_deterministic_jsonl_gzip(evidence_path, evidence)
    raw_index_path = output / "ibc_raw_transaction_index.jsonl.gz"
    write_deterministic_jsonl_gzip(raw_index_path, raw_index)
    audit["raw_global_integrity"] = {"transaction_count": len(raw_index), "unique_tx_hash_count": len(seen_raw_hashes),
                                     "all_day_memberships_verified": True, "all_raw_transactions_indexed": True}
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    new_queries = [{k: v for k, v in replacements.get(q["index"], q).items() if k != "partial"} for q in manifest["query_summaries"]]
    page_sources, preferred = Counter(), Counter()
    for q in new_queries:
        page_sources.update(q["page_source_counts"])
        preferred[q["preferred_rpc"]] += 1
    new_manifest = {**manifest, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "query_summaries": new_queries, "query_checkpoint_directory": str(checkpoint_root),
                    "query_page_source_counts": dict(page_sources), "query_preferred_source_counts": dict(preferred),
                    "ibc_inbound_extraction_policy": IBC_INBOUND_POLICY,
                    "prior_baseline_snapshot": {"local_path": str(prior_manifest_snapshot),
                                                "relative_path": prior_manifest_snapshot.name,
                                                "sha256": sha(prior_manifest_snapshot)},
                    "ibc_receive_policy_audit": {"local_path": str(audit_path), "sha256": sha(audit_path)},
                    "ibc_receive_evidence": {"local_path": str(evidence_path), "sha256": sha(evidence_path), "record_count": len(evidence)},
                    "ibc_raw_transaction_index": {"local_path": str(raw_index_path), "sha256": sha(raw_index_path), "record_count": len(raw_index)},
                    "five_minute_series": {"local_path": str(series_path), "sha256": sha(series_path), "record_count": len(series), "size_bytes": series_path.stat().st_size},
                    "limitations": ["Only inbound IBC queries were re-collected and re-extracted under the success/native-credit policy; direct-bank and outbound values are preserved from the prior baseline.",
                                    "Inbound measures native Hub receipt, including forwarding credits with deferred ACK, not final destination delivery. Outbound measures Hub send initiation.",
                                    *[line for line in manifest["limitations"] if not line.startswith("The compact baseline retains")]]}
    (output / "baseline_indexed_manifest.json").write_text(json.dumps(new_manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(output), "status": audit["status"], "excluded_count": audit["excluded_count"],
                      "excluded_uatom": audit["excluded_uatom"], "unresolved_exclusion_count": len(unresolved)}, indent=2))
    if unresolved:
        raise SystemExit("Review unresolved native receipt exclusions before using this staging baseline")


if __name__ == "__main__":
    main()
