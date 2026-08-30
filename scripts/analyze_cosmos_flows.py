#!/usr/bin/env python3
"""Summarize Cosmos Hub flows around the ATOM flash-crash timestamp."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FLASH_TIME = datetime.fromisoformat("2025-10-10T21:20:37.689043+00:00")
EVENT_START = datetime.fromisoformat("2025-10-10T20:30:00+00:00")
EVENT_END = datetime.fromisoformat("2025-10-10T22:30:00+00:00")
FLASH_HEIGHT = 27908328


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def jsonl_gzip(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def atom(value_uatom: int) -> float:
    return value_uatom / 1_000_000


def bucket_label(seconds_from_flash: float) -> str | None:
    intervals = [
        (-3600, -1800, "-60m_to_-30m"),
        (-1800, -600, "-30m_to_-10m"),
        (-600, 0, "-10m_to_0m"),
        (0, 600, "0m_to_+10m"),
        (600, 1800, "+10m_to_+30m"),
        (1800, 3600, "+30m_to_+60m"),
    ]
    for start, end, label in intervals:
        if start <= seconds_from_flash < end:
            return label
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    processed = PROJECT_ROOT / "data" / "processed" / "cosmoshub"
    parser.add_argument(
        "--atom-transfers",
        type=Path,
        default=processed / "atom_transfers_2025-10-09_2025-10-12.jsonl.gz",
    )
    parser.add_argument(
        "--ibc-transfers",
        type=Path,
        default=processed / "ibc_transfers_2025-10-09_2025-10-12.jsonl.gz",
    )
    parser.add_argument(
        "--candidates",
        type=Path,
        default=processed / "exchange_inflow_candidates_2025-10-09_2025-10-12.json",
    )
    parser.add_argument(
        "--labels",
        type=Path,
        default=PROJECT_ROOT / "metadata" / "exchange_address_labels.json",
    )
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "results" / "cosmoshub_flow_analysis.json")
    args = parser.parse_args()

    candidate_document = json.loads(args.candidates.read_text(encoding="utf-8"))
    label_document = json.loads(args.labels.read_text(encoding="utf-8"))
    candidates = candidate_document["candidates"]
    candidate_addresses = {item["address"] for item in candidates}
    structured_candidate_addresses = {
        item["address"] for item in candidates if item["candidate_tier"] in {"behavioral_high", "behavioral_medium"}
    }
    large_flow_watchlist_addresses = {
        item["address"] for item in candidates if item["candidate_tier"] == "large_flow_watchlist"
    }
    labels = {item["address"]: item for item in label_document["labels"]}

    confirmed_in_uatom: dict[str, int] = defaultdict(int)
    confirmed_out_uatom: dict[str, int] = defaultdict(int)
    confirmed_in_count: dict[str, int] = defaultdict(int)
    confirmed_out_count: dict[str, int] = defaultdict(int)
    candidate_event_in_uatom: dict[str, int] = defaultdict(int)
    candidate_event_in_count: dict[str, int] = defaultdict(int)
    direct_event_total_uatom = 0
    direct_event_total_count = 0
    behavioral_event_uatom = 0
    behavioral_event_count = 0
    watchlist_event_uatom = 0
    watchlist_event_count = 0
    confirmed_event_uatom = 0
    confirmed_event_count = 0
    flash_block_direct_rows: list[dict[str, Any]] = []
    near_flash_candidate_rows: list[dict[str, Any]] = []
    period_stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    five_minute_stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for row in jsonl_gzip(args.atom_transfers):
        if row["flow_class"] != "direct_bank":
            continue
        timestamp = parse_time(row["time_utc"])
        if not (EVENT_START <= timestamp < EVENT_END):
            continue
        amount_uatom = int(row["amount_uatom"])
        direct_event_total_uatom += amount_uatom
        direct_event_total_count += 1
        recipient = row["recipient"]
        sender = row["sender"]
        seconds = float(row["seconds_from_flash"])
        period = bucket_label(seconds)
        if period:
            period_stats[period]["all_direct_in_uatom"] += amount_uatom
            period_stats[period]["all_direct_in_count"] += 1
        bucket_start = EVENT_START + timedelta(minutes=5 * int((timestamp - EVENT_START).total_seconds() // 300))
        bucket = bucket_start.isoformat().replace("+00:00", "Z")
        five_minute_stats[bucket]["all_direct_uatom"] += amount_uatom
        five_minute_stats[bucket]["all_direct_count"] += 1

        if recipient in candidate_addresses:
            candidate_event_in_uatom[recipient] += amount_uatom
            candidate_event_in_count[recipient] += 1
            if abs(seconds) <= 1800:
                near_flash_candidate_rows.append(row)
        if recipient in structured_candidate_addresses:
            behavioral_event_uatom += amount_uatom
            behavioral_event_count += 1
            five_minute_stats[bucket]["behavioral_candidate_in_uatom"] += amount_uatom
            five_minute_stats[bucket]["behavioral_candidate_in_count"] += 1
            if period:
                period_stats[period]["behavioral_candidate_in_uatom"] += amount_uatom
                period_stats[period]["behavioral_candidate_in_count"] += 1
        if recipient in large_flow_watchlist_addresses:
            watchlist_event_uatom += amount_uatom
            watchlist_event_count += 1
            five_minute_stats[bucket]["large_flow_watchlist_in_uatom"] += amount_uatom
            if period:
                period_stats[period]["large_flow_watchlist_in_uatom"] += amount_uatom

        if recipient in labels:
            label = labels[recipient]["label"]
            confirmed_in_uatom[label] += amount_uatom
            confirmed_in_count[label] += 1
            confirmed_event_uatom += amount_uatom
            confirmed_event_count += 1
            five_minute_stats[bucket]["confirmed_exchange_in_uatom"] += amount_uatom
            five_minute_stats[bucket]["confirmed_exchange_in_count"] += 1
            if period:
                period_stats[period]["confirmed_exchange_in_uatom"] += amount_uatom
                period_stats[period]["confirmed_exchange_in_count"] += 1
        if sender in labels:
            label = labels[sender]["label"]
            confirmed_out_uatom[label] += amount_uatom
            confirmed_out_count[label] += 1
            five_minute_stats[bucket]["confirmed_exchange_out_uatom"] += amount_uatom
            five_minute_stats[bucket]["confirmed_exchange_out_count"] += 1
            if period:
                period_stats[period]["confirmed_exchange_out_uatom"] += amount_uatom
                period_stats[period]["confirmed_exchange_out_count"] += 1
        if int(row["height"]) == FLASH_HEIGHT:
            flash_block_direct_rows.append(row)

    ibc_full_uatom: dict[str, int] = defaultdict(int)
    ibc_full_count: dict[str, int] = defaultdict(int)
    ibc_event_uatom: dict[str, int] = defaultdict(int)
    ibc_event_count: dict[str, int] = defaultdict(int)
    ibc_routes: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    flash_block_ibc_rows: list[dict[str, Any]] = []
    for row in jsonl_gzip(args.ibc_transfers):
        if not row["is_atom"]:
            continue
        direction = row["direction"]
        amount_uatom = int(row["amount_base_units"])
        ibc_full_uatom[direction] += amount_uatom
        ibc_full_count[direction] += 1
        chain_hint = row.get("counterparty_chain_hint") or "unknown"
        ibc_routes[(direction, chain_hint)]["amount_uatom"] += amount_uatom
        ibc_routes[(direction, chain_hint)]["count"] += 1
        timestamp = parse_time(row["time_utc"])
        if EVENT_START <= timestamp < EVENT_END:
            ibc_event_uatom[direction] += amount_uatom
            ibc_event_count[direction] += 1
            bucket_start = EVENT_START + timedelta(minutes=5 * int((timestamp - EVENT_START).total_seconds() // 300))
            bucket = bucket_start.isoformat().replace("+00:00", "Z")
            five_minute_stats[bucket][f"ibc_{direction}_uatom"] += amount_uatom
            five_minute_stats[bucket][f"ibc_{direction}_count"] += 1
            period = bucket_label(float(row["seconds_from_flash"]))
            if period:
                period_stats[period][f"ibc_{direction}_uatom"] += amount_uatom
                period_stats[period][f"ibc_{direction}_count"] += 1
        if int(row["height"]) == FLASH_HEIGHT:
            flash_block_ibc_rows.append(row)

    top_candidate_event = []
    candidates_by_address = {item["address"]: item for item in candidates}
    for address, amount_uatom in candidate_event_in_uatom.items():
        source = candidates_by_address[address]
        label = labels.get(address)
        top_candidate_event.append(
            {
                "address": address,
                "public_exchange_label": label["label"] if label else None,
                "label_confidence": label["confidence"] if label else None,
                "candidate_tier": source["candidate_tier"],
                "candidate_score": source["candidate_score"],
                "event_window_inflow_count": candidate_event_in_count[address],
                "event_window_inflow_atom": atom(amount_uatom),
                "four_day_inflow_atom": source["direct_inflow_atom"],
                "four_day_unique_senders": source["unique_senders"],
                "four_day_numeric_memo_count": source["numeric_memo_inflow_count"],
                "four_day_structured_memo_count": source["structured_memo_inflow_count"],
            }
        )
    top_candidate_event.sort(key=lambda item: (-item["event_window_inflow_atom"], item["address"]))

    confirmed_exchanges = []
    for label_item in label_document["labels"]:
        label = label_item["label"]
        address = label_item["address"]
        candidate = candidates_by_address.get(address, {})
        inflow_uatom = confirmed_in_uatom[label]
        outflow_uatom = confirmed_out_uatom[label]
        confirmed_exchanges.append(
            {
                "label": label,
                "address": address,
                "confidence": label_item["confidence"],
                "event_window_inflow_count": confirmed_in_count[label],
                "event_window_inflow_atom": atom(inflow_uatom),
                "event_window_outflow_count": confirmed_out_count[label],
                "event_window_outflow_atom": atom(outflow_uatom),
                "event_window_net_inflow_atom": atom(inflow_uatom - outflow_uatom),
                "four_day_inflow_atom": candidate.get("direct_inflow_atom"),
                "four_day_inflow_count": candidate.get("direct_inflow_count"),
                "four_day_unique_senders": candidate.get("unique_senders"),
                "four_day_numeric_memo_count": candidate.get("numeric_memo_inflow_count"),
                "four_day_structured_memo_count": candidate.get("structured_memo_inflow_count"),
                "evidence": label_item["evidence"],
            }
        )
    confirmed_exchanges.sort(key=lambda item: -item["event_window_inflow_atom"])

    period_order = ["-60m_to_-30m", "-30m_to_-10m", "-10m_to_0m", "0m_to_+10m", "+10m_to_+30m", "+30m_to_+60m"]
    periods = []
    for label in period_order:
        values = period_stats[label]
        periods.append(
            {
                "period": label,
                "confirmed_exchange_in_atom": atom(values["confirmed_exchange_in_uatom"]),
                "confirmed_exchange_out_atom": atom(values["confirmed_exchange_out_uatom"]),
                "confirmed_exchange_net_in_atom": atom(
                    values["confirmed_exchange_in_uatom"] - values["confirmed_exchange_out_uatom"]
                ),
                "behavioral_candidate_in_atom": atom(values["behavioral_candidate_in_uatom"]),
                "large_flow_watchlist_in_atom": atom(values["large_flow_watchlist_in_uatom"]),
                "all_direct_atom": atom(values["all_direct_in_uatom"]),
                "ibc_inbound_atom": atom(values["ibc_inbound_uatom"]),
                "ibc_outbound_atom": atom(values["ibc_outbound_uatom"]),
                "ibc_net_inbound_atom": atom(values["ibc_inbound_uatom"] - values["ibc_outbound_uatom"]),
            }
        )

    five_minute = []
    bucket = EVENT_START
    while bucket < EVENT_END:
        key = bucket.isoformat().replace("+00:00", "Z")
        values = five_minute_stats[key]
        five_minute.append(
            {
                "bucket_start_utc": key,
                "minutes_from_flash": (bucket - FLASH_TIME).total_seconds() / 60,
                "confirmed_exchange_in_atom": atom(values["confirmed_exchange_in_uatom"]),
                "confirmed_exchange_out_atom": atom(values["confirmed_exchange_out_uatom"]),
                "behavioral_candidate_in_atom": atom(values["behavioral_candidate_in_uatom"]),
                "large_flow_watchlist_in_atom": atom(values["large_flow_watchlist_in_uatom"]),
                "all_direct_atom": atom(values["all_direct_uatom"]),
                "ibc_inbound_atom": atom(values["ibc_inbound_uatom"]),
                "ibc_outbound_atom": atom(values["ibc_outbound_uatom"]),
            }
        )
        bucket += timedelta(minutes=5)

    routes = []
    for (direction, chain_hint), values in ibc_routes.items():
        routes.append(
            {
                "direction": direction,
                "counterparty_chain_hint": chain_hint,
                "transfer_count": values["count"],
                "amount_atom": atom(values["amount_uatom"]),
            }
        )
    routes.sort(key=lambda item: (-item["amount_atom"], item["direction"], item["counterparty_chain_hint"]))

    near_flash_candidate_rows.sort(key=lambda row: (-int(row["amount_uatom"]), abs(float(row["seconds_from_flash"]))))
    near_flash_rows = []
    for row in near_flash_candidate_rows[:50]:
        label = labels.get(row["recipient"])
        near_flash_rows.append(
            {
                "time_utc": row["time_utc"],
                "seconds_from_flash": row["seconds_from_flash"],
                "amount_atom": row["amount_atom"],
                "sender": row["sender"],
                "recipient": row["recipient"],
                "public_exchange_label": label["label"] if label else None,
                "memo": row["tx_memo"],
                "tx_hash": row["tx_hash"],
            }
        )

    report = {
        "study_id": "atom_flash_crash_2025_10_10",
        "chain_id": "cosmoshub-4",
        "analysis_generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "flash_timestamp_utc": FLASH_TIME.isoformat().replace("+00:00", "Z"),
        "flash_block_height": FLASH_HEIGHT,
        "event_window": "[2025-10-10T20:30:00Z, 2025-10-10T22:30:00Z)",
        "source_sha256": {
            "atom_transfers": sha256_file(args.atom_transfers),
            "ibc_transfers": sha256_file(args.ibc_transfers),
            "candidates": sha256_file(args.candidates),
            "labels": sha256_file(args.labels),
        },
        "event_window_totals": {
            "all_direct_transfer_count": direct_event_total_count,
            "all_direct_transfer_atom": atom(direct_event_total_uatom),
            "behavioral_candidate_inflow_count": behavioral_event_count,
            "behavioral_candidate_inflow_atom": atom(behavioral_event_uatom),
            "large_flow_watchlist_inflow_count": watchlist_event_count,
            "large_flow_watchlist_inflow_atom": atom(watchlist_event_uatom),
            "confirmed_exchange_inflow_count": confirmed_event_count,
            "confirmed_exchange_inflow_atom": atom(confirmed_event_uatom),
            "ibc_inbound_count": ibc_event_count["inbound"],
            "ibc_inbound_atom": atom(ibc_event_uatom["inbound"]),
            "ibc_outbound_count": ibc_event_count["outbound"],
            "ibc_outbound_atom": atom(ibc_event_uatom["outbound"]),
            "ibc_net_inbound_atom": atom(ibc_event_uatom["inbound"] - ibc_event_uatom["outbound"]),
        },
        "four_day_ibc_totals": {
            "inbound_count": ibc_full_count["inbound"],
            "inbound_atom": atom(ibc_full_uatom["inbound"]),
            "outbound_count": ibc_full_count["outbound"],
            "outbound_atom": atom(ibc_full_uatom["outbound"]),
            "net_inbound_atom": atom(ibc_full_uatom["inbound"] - ibc_full_uatom["outbound"]),
        },
        "confirmed_exchange_addresses": confirmed_exchanges,
        "top_event_window_behavioral_candidates": top_candidate_event[:50],
        "top_event_window_structured_candidates": [
            item for item in top_candidate_event if item["candidate_tier"] in {"behavioral_high", "behavioral_medium"}
        ][:50],
        "top_event_window_large_flow_watchlist": [
            item for item in top_candidate_event if item["candidate_tier"] == "large_flow_watchlist"
        ][:50],
        "flash_relative_periods": periods,
        "five_minute_series": five_minute,
        "top_four_day_ibc_routes": routes[:50],
        "largest_candidate_inflows_within_30m": near_flash_rows,
        "flash_block": {
            "direct_transfer_count": len(flash_block_direct_rows),
            "direct_transfer_atom": sum(row["amount_atom"] for row in flash_block_direct_rows),
            "ibc_atom_transfer_count": sum(1 for row in flash_block_ibc_rows if row["is_atom"]),
            "ibc_atom_transfer_atom": sum(row["amount_atom"] or 0 for row in flash_block_ibc_rows if row["is_atom"]),
        },
        "interpretation_guardrails": [
            "A labeled shared receiving address supports exchange association, not the identity of the sender or the trade beneficiary.",
            "Behavioral candidates without a public label remain unconfirmed.",
            "An on-chain deposit can precede, follow, or be unrelated to an exchange execution; timing alone is not causal evidence.",
            "IBC net flow measures Cosmos Hub packet movement and is not equivalent to centralized-exchange net flow.",
        ],
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["event_window_totals"], ensure_ascii=False, indent=2))
    print(json.dumps(report["four_day_ibc_totals"], ensure_ascii=False, indent=2))
    print(json.dumps(report["confirmed_exchange_addresses"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
