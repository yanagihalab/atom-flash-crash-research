#!/usr/bin/env python3
"""Independently verify wallet/order coordination results from primary local files."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FLASH_TIME = datetime.fromisoformat("2025-10-10T21:20:37.689043+00:00")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def zipped_csv(path: Path) -> Iterator[list[str]]:
    with zipfile.ZipFile(path) as archive:
        name = next(name for name in archive.namelist() if not name.endswith("/"))
        with archive.open(name) as binary:
            yield from csv.reader(io.TextIOWrapper(binary, encoding="utf-8"))


def market_time(value: str) -> datetime:
    return datetime.fromtimestamp(int(value) / 1_000_000, tz=timezone.utc)


def close(left: float, right: float, tolerance: float = 1e-9) -> bool:
    return math.isclose(left, right, rel_tol=1e-12, abs_tol=tolerance)


def main() -> None:
    parser = argparse.ArgumentParser()
    processed = PROJECT_ROOT / "data" / "processed" / "cosmoshub"
    daily = PROJECT_ROOT / "data" / "raw" / "binance" / "spot" / "daily"
    parser.add_argument("--analysis", type=Path, default=PROJECT_ROOT / "results" / "wallet_coordination_analysis.json")
    parser.add_argument("--atom-transfers", type=Path, default=processed / "atom_transfers_2025-10-09_2025-10-12.jsonl.gz")
    parser.add_argument("--ibc-transfers", type=Path, default=processed / "ibc_transfers_2025-10-09_2025-10-12.jsonl.gz")
    parser.add_argument("--labels", type=Path, default=PROJECT_ROOT / "metadata" / "exchange_address_labels.json")
    parser.add_argument("--checkpoint", type=Path, default=processed / "baseline_30d" / "query_checkpoints" / "0165.jsonl.gz")
    parser.add_argument("--trades", type=Path, default=daily / "trades" / "ATOMUSDT" / "ATOMUSDT-trades-2025-10-10.zip")
    parser.add_argument("--agg-trades", type=Path, default=daily / "aggTrades" / "ATOMUSDT" / "ATOMUSDT-aggTrades-2025-10-10.zip")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "results" / "wallet_coordination_verification.json")
    args = parser.parse_args()

    analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
    labels_doc = json.loads(args.labels.read_text(encoding="utf-8"))
    labels = {row["address"]: row["label"] for row in labels_doc["labels"]}
    binance = next(address for address, label in labels.items() if label == "Binance")
    kucoin = next(address for address, label in labels.items() if label == "KuCoin")
    rows = []
    for row in jsonl(args.atom_transfers):
        if row["flow_class"] == "direct_bank":
            row["_time"] = parse_time(row["time_utc"])
            rows.append(row)
    deposits = [row for row in rows if row["recipient"] == binance]

    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: Any) -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    expected_hashes = {
        "atom_transfers": sha256_file(args.atom_transfers),
        "ibc_transfers": sha256_file(args.ibc_transfers),
        "labels": sha256_file(args.labels),
        "binance_30d_checkpoint": sha256_file(args.checkpoint),
        "binance_trades": sha256_file(args.trades),
        "binance_agg_trades": sha256_file(args.agg_trades),
    }
    check("source_hashes", analysis["source_sha256"] == expected_hashes, expected_hashes)

    exact_raw = []
    for row in zipped_csv(args.trades):
        timestamp = market_time(row[4])
        if timestamp < FLASH_TIME:
            continue
        if timestamp > FLASH_TIME:
            break
        exact_raw.append(row)
    market = analysis["market_side_order_episode"]
    raw_quantity = sum(float(row[2]) for row in exact_raw)
    raw_quote = sum(float(row[3]) for row in exact_raw)
    check(
        "exact_low_raw_trade_sequence",
        len(exact_raw) == market["raw_trade_count"] == 92
        and int(exact_raw[0][0]) == market["first_trade_id"]
        and int(exact_raw[-1][0]) == market["last_trade_id"]
        and close(raw_quantity, market["aggressive_sell_quantity_atom"])
        and close(raw_quote, market["execution_quote_usdt"])
        and all(row[5].lower() == "true" for row in exact_raw),
        {"rows": len(exact_raw), "quantity_atom": raw_quantity, "quote_usdt": raw_quote},
    )

    exact_agg = []
    for row in zipped_csv(args.agg_trades):
        timestamp = market_time(row[5])
        if timestamp < FLASH_TIME:
            continue
        if timestamp > FLASH_TIME:
            break
        exact_agg.append(row)
    check(
        "exact_low_aggregate_trade_sequence",
        len(exact_agg) == market["aggregate_trade_row_count"] == 71
        and all(row[6].lower() == "true" for row in exact_agg)
        and close(min(float(row[1]) for row in exact_agg), 0.001),
        {"rows": len(exact_agg), "minimum_price": min(float(row[1]) for row in exact_agg)},
    )

    nearest = max((row for row in deposits if row["_time"] < FLASH_TIME), key=lambda row: row["_time"])
    reported_nearest = analysis["nearest_pre_low_binance_inflow"]
    check(
        "nearest_pre_low_binance_transfer",
        nearest["tx_hash"] == reported_nearest["tx_hash"]
        and nearest["sender"] == reported_nearest["sender"]
        and close(nearest["amount_atom"], 2000.0)
        and close((FLASH_TIME - nearest["_time"]).total_seconds(), reported_nearest["seconds_before_low"]),
        {"tx_hash": nearest["tx_hash"], "sender": nearest["sender"], "amount_atom": nearest["amount_atom"]},
    )

    pre2h = [row for row in deposits if FLASH_TIME - timedelta(hours=2) <= row["_time"] < FLASH_TIME]
    by_sender: Counter[str] = Counter()
    for row in pre2h:
        by_sender[row["sender"]] += row["amount_atom"]
    total = sum(by_sender.values())
    top_sender, top_amount = by_sender.most_common(1)[0]
    hhi = sum((amount / total) ** 2 for amount in by_sender.values())
    reported_concentration = analysis["exact_pre_low_concentration"]["120m_sender"]
    check(
        "pre2h_sender_concentration",
        close(total, reported_concentration["amount_atom"])
        and len(by_sender) == reported_concentration["group_count"]
        and top_sender == reported_concentration["top_groups"][0]["key"]
        and close(top_amount / total, reported_concentration["top1_share"])
        and close(hhi, reported_concentration["hhi"]),
        {"amount_atom": total, "senders": len(by_sender), "top_sender": top_sender, "hhi": hhi},
    )

    with gzip.open(args.checkpoint, "rt", encoding="utf-8") as stream:
        checkpoint = json.loads(next(stream))
    buckets = [0.0] * 8_640
    for index, metrics in checkpoint["partial"].items():
        buckets[int(index)] = metrics.get("confirmed_exchange_in_uatom", 0) / 1_000_000
    baseline_differences = []
    for minutes in (30, 120):
        end = FLASH_TIME.replace(second=0, microsecond=0)
        start = end - timedelta(minutes=minutes)
        event = sum(row["amount_atom"] for row in deposits if start <= row["_time"] < end)
        length = minutes // 5
        controls = [sum(buckets[index : index + length]) for index in range(len(buckets) - length + 1)]
        percentile = 100 * sum(value <= event for value in controls) / len(controls)
        reported = analysis["binance_pre_low_inflow_30d_baseline"][f"{minutes}m"]
        if not close(event, reported["event_amount_atom"]) or not close(
            percentile, reported["rolling_5min_percentile_empirical"]
        ):
            baseline_differences.append(
                {"minutes": minutes, "event": event, "percentile": percentile, "reported": reported}
            )
    check("binance_30d_baseline_30m_120m", not baseline_differences, baseline_differences)

    nearest_sender = nearest["sender"]
    nearest_sender_deposits = [row for row in deposits if row["sender"] == nearest_sender]
    kucoin_funding = sum(
        row["amount_atom"] for row in rows if row["sender"] == kucoin and row["recipient"] == nearest_sender
    )
    profile = reported_nearest["sender_profile"]
    check(
        "nearest_sender_service_fingerprint",
        len(nearest_sender_deposits) == profile["binance_deposit_count"] == 59
        and len({row["tx_memo"] for row in nearest_sender_deposits}) == profile["binance_unique_memo_cluster_count"] == 10
        and close(kucoin_funding, reported_nearest["direct_funding_from_public_label_kucoin_atom"]),
        {"binance_deposits": len(nearest_sender_deposits), "memo_count": len({row["tx_memo"] for row in nearest_sender_deposits}), "kucoin_funding_atom": kucoin_funding},
    )

    top_profile = analysis["top_2h_sender_detail"]["profile"]
    ibc_matches = [
        row
        for row in jsonl(args.ibc_transfers)
        if row.get("is_atom") and (row.get("sender") == top_sender or row.get("receiver") == top_sender)
    ]
    check(
        "top_sender_ibc_fingerprint",
        top_profile["address"] == top_sender
        and len(ibc_matches) == top_profile["ibc_match_count"] == 893
        and {row.get("counterparty_chain_hint") for row in ibc_matches} == {"osmo"},
        {"address": top_sender, "ibc_matches": len(ibc_matches)},
    )

    common_sources = analysis["common_one_hop_funding_sources_for_top10"]
    check(
        "material_common_source_filter",
        all(row["funded_top10_target_count"] >= 2 and row["funded_top10_amount_atom"] >= 1_000 for row in common_sources),
        {"rows": len(common_sources)},
    )
    assessment = analysis["evidence_assessment"]
    check(
        "claim_guardrails",
        assessment["nearest_cosmos_sender_was_the_selling_account"] == "not_supported"
        and assessment["coordinated_wallet_manipulation_or_deliberate_intent"] == "not_established"
        and assessment["named_natural_person_or_beneficial_owner"] == "not_identifiable_from_public_data"
        and "not established" in analysis["bottom_line"].lower(),
        assessment,
    )
    check(
        "required_nonpublic_join_fields",
        len(analysis["required_nonpublic_evidence"]) >= 4
        and any("order ID" in row for row in analysis["required_nonpublic_evidence"])
        and any("deposit-credit" in row for row in analysis["required_nonpublic_evidence"]),
        analysis["required_nonpublic_evidence"],
    )

    passed = all(row["passed"] for row in checks)
    result = {
        "study_id": analysis["study_id"],
        "verification_status": "PASS" if passed else "FAIL",
        "check_count": len(checks),
        "checks": checks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "status": result["verification_status"], "checks": len(checks)}, indent=2))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
