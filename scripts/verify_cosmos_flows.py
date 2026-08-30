#!/usr/bin/env python3
"""Independently verify derived Cosmos Hub flow artifacts and core totals."""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED = PROJECT_ROOT / "data" / "processed" / "cosmoshub"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def jsonl_gzip(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                yield json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from error


def canonical(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    summary_path = PROJECT_ROOT / "results" / "cosmoshub_flow_extraction_summary.json"
    analysis_path = PROJECT_ROOT / "results" / "cosmoshub_flow_analysis.json"
    labels_path = PROJECT_ROOT / "metadata" / "exchange_address_labels.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    labels_document = json.loads(labels_path.read_text(encoding="utf-8"))
    labeled_addresses = {row["address"] for row in labels_document["labels"]}

    artifact_checks: dict[str, dict[str, Any]] = {}
    loaded_rows: dict[str, list[dict[str, Any]]] = {}
    for name, expected in summary["artifacts"].items():
        path = Path(expected["local_path"])
        require(path.exists(), f"missing artifact: {path}")
        actual_size = path.stat().st_size
        actual_sha256 = sha256_file(path)
        require(actual_size == expected["size_bytes"], f"size mismatch: {name}")
        require(actual_sha256 == expected["sha256"], f"SHA-256 mismatch: {name}")
        if name.endswith(".jsonl.gz"):
            rows = list(jsonl_gzip(path))
            actual_records = len(rows)
            loaded_rows[name] = rows
        else:
            document = json.loads(path.read_text(encoding="utf-8"))
            actual_records = document["candidate_count"]
        require(actual_records == expected["record_count"], f"record-count mismatch: {name}")
        artifact_checks[name] = {
            "size_bytes": actual_size,
            "record_count": actual_records,
            "sha256": actual_sha256,
            "status": "verified",
        }

    atom_name = "atom_transfers_2025-10-09_2025-10-12.jsonl.gz"
    ibc_name = "ibc_transfers_2025-10-09_2025-10-12.jsonl.gz"
    event_name = "event_window_flows_2025-10-10_2030-2230_utc.jsonl.gz"
    candidate_name = "exchange_inflow_candidates_2025-10-09_2025-10-12.json"
    atom_rows = loaded_rows[atom_name]
    ibc_rows = loaded_rows[ibc_name]
    event_rows = loaded_rows[event_name]

    source_event_rows: list[dict[str, Any]] = []
    for row in atom_rows:
        if row["is_event_window"]:
            source_event_rows.append({"flow_type": "atom_transfer", **row})
    for row in ibc_rows:
        if row["is_event_window"]:
            source_event_rows.append({"flow_type": "ibc_transfer", **row})
    require(
        Counter(map(canonical, source_event_rows)) == Counter(map(canonical, event_rows)),
        "event-window artifact is not an exact subset of the four-day flow artifacts",
    )

    direct_rows = [row for row in event_rows if row["flow_type"] == "atom_transfer" and row["flow_class"] == "direct_bank"]
    ibc_atom_rows = [row for row in event_rows if row["flow_type"] == "ibc_transfer" and row["is_atom"]]
    confirmed_in = [row for row in direct_rows if row["recipient"] in labeled_addresses]
    confirmed_out = [row for row in direct_rows if row["sender"] in labeled_addresses]
    ibc_in = [row for row in ibc_atom_rows if row["direction"] == "inbound"]
    ibc_out = [row for row in ibc_atom_rows if row["direction"] == "outbound"]
    totals = analysis["event_window_totals"]
    require(len(direct_rows) == totals["all_direct_transfer_count"], "direct-transfer count mismatch")
    require(sum(row["amount_uatom"] for row in direct_rows) == round(totals["all_direct_transfer_atom"] * 1_000_000), "direct-transfer amount mismatch")
    require(len(confirmed_in) == totals["confirmed_exchange_inflow_count"], "confirmed-exchange inflow count mismatch")
    require(sum(row["amount_uatom"] for row in confirmed_in) == round(totals["confirmed_exchange_inflow_atom"] * 1_000_000), "confirmed-exchange inflow amount mismatch")
    require(len(ibc_in) == totals["ibc_inbound_count"], "IBC inbound count mismatch")
    require(sum(row["amount_base_units"] for row in ibc_in) == round(totals["ibc_inbound_atom"] * 1_000_000), "IBC inbound amount mismatch")
    require(len(ibc_out) == totals["ibc_outbound_count"], "IBC outbound count mismatch")
    require(sum(row["amount_base_units"] for row in ibc_out) == round(totals["ibc_outbound_atom"] * 1_000_000), "IBC outbound amount mismatch")

    expected_sources = {
        "atom_transfers": artifact_checks[atom_name]["sha256"],
        "ibc_transfers": artifact_checks[ibc_name]["sha256"],
        "candidates": artifact_checks[candidate_name]["sha256"],
        "labels": sha256_file(labels_path),
    }
    require(analysis["source_sha256"] == expected_sources, "analysis source hashes do not match current inputs")

    report = {
        "study_id": analysis["study_id"],
        "chain_id": analysis["chain_id"],
        "status": "verified",
        "artifact_checks": artifact_checks,
        "event_subset": {
            "status": "exact_multiset_match",
            "record_count": len(event_rows),
            "atom_transfer_records": sum(row["flow_type"] == "atom_transfer" for row in event_rows),
            "ibc_transfer_records": sum(row["flow_type"] == "ibc_transfer" for row in event_rows),
        },
        "event_window_recalculation": {
            "direct_transfer_count": len(direct_rows),
            "direct_transfer_atom": sum(row["amount_uatom"] for row in direct_rows) / 1_000_000,
            "confirmed_exchange_inflow_count": len(confirmed_in),
            "confirmed_exchange_inflow_atom": sum(row["amount_uatom"] for row in confirmed_in) / 1_000_000,
            "confirmed_exchange_outflow_count": len(confirmed_out),
            "confirmed_exchange_outflow_atom": sum(row["amount_uatom"] for row in confirmed_out) / 1_000_000,
            "confirmed_exchange_net_inflow_atom": (
                sum(row["amount_uatom"] for row in confirmed_in) - sum(row["amount_uatom"] for row in confirmed_out)
            ) / 1_000_000,
            "ibc_inbound_count": len(ibc_in),
            "ibc_inbound_atom": sum(row["amount_base_units"] for row in ibc_in) / 1_000_000,
            "ibc_outbound_count": len(ibc_out),
            "ibc_outbound_atom": sum(row["amount_base_units"] for row in ibc_out) / 1_000_000,
        },
        "analysis_source_sha256": expected_sources,
        "decode_errors": {
            "protobuf": summary["protobuf_decode_errors"],
            "ibc_packet": summary["packet_decode_errors"],
        },
    }
    output = PROJECT_ROOT / "results" / "cosmoshub_flow_verification.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
