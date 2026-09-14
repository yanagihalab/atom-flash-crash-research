"""Incremental, offline early audit of completed inbound-query raw days.

Only completed day gzip files are read; unchanged audited days are skipped.
The canonical baseline and every raw input remain untouched.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from ibc_receive_evidence import IBC_INBOUND_POLICY, assess_receives
from extract_cosmos_flows import atom_flag
from rebuild_baseline_ibc_receipts import ROOT, canonical_update, legacy_events, records, sha


def audit_day(query, path):
    txs, pages = [], []
    for record in records(path):
        assert record["query_sha256"] == query["query_sha256"]
        result = record["response"]["result"]
        assert int(result["total_count"]) == query["tx_count"]
        pages.append(int(record["page"]))
        txs.extend(result.get("txs") or [])
    assert sorted(pages) == list(range(1, query["page_count"] + 1))
    assert len(txs) == len({tx["hash"] for tx in txs}) == query["tx_count"]
    old, new = hashlib.sha256(), hashlib.sha256()
    old_n = old_amount = new_n = new_amount = forwarded = no_ack = 0
    outcomes = Counter()
    excluded, unknown = [], []
    for tx in sorted(txs, key=lambda r: (int(r["height"]), int(r["index"]))):
        for event in legacy_events(tx):
            canonical_update(old, event)
            old_n += 1
            old_amount += event["amount_uatom"]
        if int(tx["tx_result"].get("code", 0)) != 0:
            continue
        for d in assess_receives(tx["tx_result"].get("events") or []):
            reason = d.get("exclusion_reason") or "included_native_atom_receipt"
            outcomes[reason] += 1
            data = d["packet_data"]
            if data is None:
                unknown.append({"tx_hash": tx["hash"], "reason": reason, "event_ordinal": d["event_ordinal"]})
                continue
            event = {"height": int(tx["height"]), "tx_hash": tx["hash"], "event_ordinal": d["event_ordinal"], "amount_uatom": int(data["amount"])}
            if d["include_in_atom_flow"]:
                canonical_update(new, event)
                new_n += 1
                new_amount += event["amount_uatom"]
                forwarded += d["packet_forwarding"]
                no_ack += not d["immediate_ack_states"]
            elif atom_flag("inbound", data["denom"]):
                item = {**event, "reason": reason, "denom": data["denom"],
                        "sender": data["sender"], "receiver": data["receiver"],
                        "packet_forwarding": d["packet_forwarding"], "immediate_ack_states": d["immediate_ack_states"],
                        "packet_attributes": {k: v for k, v in d["packet_attributes"].items() if k != "packet_data_hex"}}
                excluded.append(item)
                if reason not in {"application_error", "not_native_atom_return_trace"}:
                    unknown.append(item)
    assert (old_n, old_amount, old.hexdigest()) == (query["extracted_event_count"], query["extracted_amount_uatom"], query["extracted_events_sha256"])
    assert old_n - new_n == len(excluded)
    assert old_amount - new_amount == sum(e["amount_uatom"] for e in excluded)
    return {"status": "PASS" if not unknown else "REVIEW_REQUIRED", "day": query["label"].split(":", 1)[1],
            "tx_count": len(txs), "page_count": len(pages), "legacy_digest_reproduced": True,
            "legacy_count": old_n, "legacy_amount_uatom": old_amount, "corrected_count": new_n,
            "corrected_amount_uatom": new_amount, "corrected_events_sha256": new.hexdigest(),
            "excluded_count": len(excluded), "excluded_amount_uatom": sum(e["amount_uatom"] for e in excluded),
            "successful_forwarding_count": forwarded, "successful_without_immediate_ack_count": no_ack,
            "outcomes": dict(outcomes), "excluded_packets": excluded, "unknown_exclusions": unknown,
            "raw_path": str(path.resolve()), "raw_sha256": sha(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--day", help="Optional bounded single-day audit")
    args = parser.parse_args()
    manifest_path = ROOT / "data/processed/cosmoshub/baseline_30d/baseline_indexed_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    code_hashes = {name: sha(ROOT / "scripts" / name) for name in
                   ("ibc_receive_evidence.py", "audit_completed_ibc_days.py", "rebuild_baseline_ibc_receipts.py")}
    previous = json.loads(args.output.read_text()) if args.output.exists() else {}
    day_results = previous.get("day_results", {}) if previous.get("code_hashes") == code_hashes and previous.get("legacy_manifest_sha256") == sha(manifest_path) else {}
    new_days = []
    for query in manifest["query_summaries"]:
        if not query["label"].startswith("ibc_in:"):
            continue
        day = query["label"].split(":", 1)[1]
        if args.day and args.day != day:
            continue
        path = args.raw_root / day / "tx_search.jsonl.gz"
        if not path.exists():
            continue
        if day in day_results and day_results[day]["raw_sha256"] == sha(path):
            continue
        day_results[day] = audit_day(query, path)
        new_days.append(day)
        print(json.dumps({k: v for k, v in day_results[day].items() if k not in {"excluded_packets", "unknown_exclusions", "raw_path"}}, ensure_ascii=False), flush=True)
    output = {"policy": IBC_INBOUND_POLICY, "updated_at_utc": datetime.now(timezone.utc).isoformat(),
              "legacy_manifest_sha256": sha(manifest_path), "code_hashes": code_hashes,
              "completed_audited_days": len(day_results), "newly_audited_days": new_days,
              "status": "PASS_PARTIAL" if all(d["status"] == "PASS" for d in day_results.values()) else "REVIEW_REQUIRED",
              "day_results": day_results, "scope_note": "Only complete downloaded days; not certification of the unfinished 30-day baseline."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: output[k] for k in ("completed_audited_days", "newly_audited_days", "status")}))


if __name__ == "__main__":
    main()
