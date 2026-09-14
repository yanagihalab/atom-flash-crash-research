"""Audit the retained four-day receipt correction against its preserved inputs."""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path

from ibc_receive_evidence import IBC_INBOUND_POLICY

ROOT = Path(__file__).resolve().parents[1]


def rows(path):
    with gzip.open(path, "rt") as stream:
        yield from (json.loads(line) for line in stream)


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def key(r):
    return (r["tx_hash"], r["msg_index"], r["src_port"], r["src_channel"], r["dst_port"], r["dst_channel"], r["packet_sequence"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup-root", type=Path, required=True)
    args = parser.parse_args()
    processed = ROOT / "data/processed/cosmoshub"
    prior = list(rows(args.backup_root / "ibc_transfers_before.jsonl.gz"))
    current_path = processed / "ibc_transfers_2025-10-09_2025-10-12.jsonl.gz"
    current = list(rows(current_path))
    evidence_path = processed / "ibc_receive_evidence_2025-10-09_2025-10-12.jsonl.gz"
    evidence = list(rows(evidence_path))
    excluded = [r for r in evidence if r.get("legacy_atom_candidate") and not r["include_in_atom_flow"]]
    included = [r for r in evidence if r["include_in_atom_flow"]]
    old_in = [r for r in prior if r["direction"] == "inbound" and r["is_atom"]]
    new_in = [r for r in current if r["direction"] == "inbound" and r["is_atom"]]
    event_in = [r for r in new_in if r["is_event_window"]]
    by_day = defaultdict(lambda: {"count": 0, "amount_uatom": 0})
    for r in excluded:
        by_day[r["time_utc"][:10]]["count"] += 1
        by_day[r["time_utc"][:10]]["amount_uatom"] += r["amount_base_units"]
    canonical = lambda r: json.dumps(r, sort_keys=True, separators=(",", ":"))
    checks = {
        "excluded_10_failed_93103000_uatom": len(excluded) == 10 and sum(r["amount_base_units"] for r in excluded) == 93_103_000 and all(r["exclusion_reason"] == "application_error" for r in excluded),
        "old_minus_excluded_exact_new_native_set": Counter(key(r) for r in old_in) - Counter(key(r) for r in excluded) == Counter(key(r) for r in new_in),
        "included_has_matching_success_and_credit": all(r["application_success"] and r["native_credit_matched"] and r["native_atom_trace"] for r in included),
        "evidence_included_exact_new_set": Counter(key(r) for r in included) == Counter(key(r) for r in new_in),
        "outbound_unchanged": Counter(canonical(r) for r in prior if r["direction"] == "outbound") == Counter(canonical(r) for r in current if r["direction"] == "outbound"),
        "all_native_bank_artifact_unchanged": sha(processed / "atom_transfers_2025-10-09_2025-10-12.jsonl.gz") == sha(args.backup_root / "atom_transfers_before.jsonl.gz"),
        "candidate_registry_unchanged": sha(processed / "exchange_inflow_candidates_2025-10-09_2025-10-12.json") == sha(args.backup_root / "exchange_inflow_candidates_before.json"),
        "event_native_receipts_unchanged": len(event_in) == 1113 and sum(r["amount_base_units"] for r in event_in) == 277_987_972_547,
        "event_583_deferred_ack_receipts_preserved": sum(not r["immediate_ack_states"] for r in event_in) == 583,
    }
    run = json.loads((ROOT / "metadata/runs/20260829T043153730115Z_cosmoshub_study_data.json").read_text())
    raw_sources = []
    for partition in run["daily_partitions"]:
        for name in ("block_metas", "tx_search"):
            record = partition[name]
            path = Path(record["local_path"])
            actual = sha(path)
            raw_sources.append({"local_path": str(path), "sha256": actual, "unchanged": actual == record["sha256"]})
    checks["all_raw_source_hashes_unchanged"] = all(r["unchanged"] for r in raw_sources)
    result = {"policy": IBC_INBOUND_POLICY, "status": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
              "old_native_receipt_count": len(old_in), "new_native_receipt_count": len(new_in),
              "excluded_by_day": dict(by_day), "excluded_native_packets": excluded,
              "all_packet_outcomes": dict(Counter(r.get("exclusion_reason") or "included" for r in evidence)),
              "native_receipt_definition": "successful native Hub credit, including forwarding intermediary credits; not final downstream delivery",
              "raw_sources": raw_sources, "corrected_ibc_sha256": sha(current_path), "evidence_sha256": sha(evidence_path),
              "baseline_note": "This four-day audit does not certify the old 30-day aggregates. That baseline requires separate raw receive re-audit."}
    output = ROOT / "results/ibc_receive_correction_verification.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(output), "status": result["status"], "checks": checks, "excluded_by_day": dict(by_day)}, indent=2))
    if not all(checks.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
