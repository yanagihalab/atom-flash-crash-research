#!/usr/bin/env python3
"""Test whether public data support a wallet-driven ATOM flash-crash hypothesis.

The analysis deliberately separates three identifiers that cannot be joined with
public data: a Cosmos sender address, an exchange deposit memo, and a Binance
trading account/order.  Wallets listed by the output are investigative leads,
not allegations of ownership, intent, or misconduct.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import statistics
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS = PROJECT_ROOT / "results"
OUTPUTS = PROJECT_ROOT / "outputs" / "atom_flash_crash_2025_10_10"
FLASH_TIME = datetime.fromisoformat("2025-10-10T21:20:37.689043+00:00")
FLASH_HEIGHT = 27_908_328
BASELINE_START = datetime.fromisoformat("2025-09-10T00:00:00+00:00")
BASELINE_BUCKET_COUNT = 8_640


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def memo_cluster(memo: str) -> str:
    """Return a non-reversible report identifier instead of publishing routing memos."""
    return "memo_sha256_" + hashlib.sha256(memo.encode("utf-8")).hexdigest()[:12]


def jsonl_gzip(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def percentile_le(values: list[float], observed: float) -> float:
    if not values:
        return math.nan
    return 100 * sum(value <= observed for value in values) / len(values)


def exact_plus_one_upper(values: list[float], observed: float) -> float:
    return (1 + sum(value >= observed for value in values)) / (len(values) + 1)


def concentration(rows: Iterable[dict[str, Any]], key: str) -> dict[str, Any]:
    by_key: Counter[str] = Counter()
    row_count = 0
    for row in rows:
        by_key[str(row[key])] += float(row["amount_atom"])
        row_count += 1
    total = sum(by_key.values())
    shares = [value / total for value in by_key.values()] if total else []
    top = by_key.most_common(5)
    return {
        "transfer_count": row_count,
        "group_count": len(by_key),
        "amount_atom": total,
        "top1_share": max(shares, default=0.0),
        "top5_share": sum(value for _, value in top) / total if total else 0.0,
        "hhi": sum(value * value for value in shares),
        "effective_group_count": 1 / sum(value * value for value in shares) if shares else 0.0,
        "top_groups": [{"key": item, "amount_atom": value, "share": value / total} for item, value in top],
    }


def read_zip_rows(path: Path) -> Iterator[list[str]]:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise ValueError(f"Expected one data member in {path}, found {names}")
        with archive.open(names[0]) as binary:
            yield from csv.reader(io.TextIOWrapper(binary, encoding="utf-8"))


def microsecond_time(value: str) -> datetime:
    return datetime.fromtimestamp(int(value) / 1_000_000, tz=timezone.utc)


def market_episode(trades_zip: Path, agg_zip: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    thresholds = [4.0, 3.5, 3.0, 2.5, 2.0, 1.7, 1.0, 0.1, 0.001]
    first_at_or_below: dict[float, dict[str, Any]] = {}
    last_at_or_above_four: dict[str, Any] | None = None
    exact_trades: list[dict[str, Any]] = []
    for row in read_zip_rows(trades_zip):
        timestamp = microsecond_time(row[4])
        if timestamp > FLASH_TIME:
            break
        trade = {
            "trade_id": int(row[0]),
            "price": float(row[1]),
            "quantity_atom": float(row[2]),
            "quote_usdt": float(row[3]),
            "time_utc": iso(timestamp),
            "buyer_is_maker": row[5].lower() == "true",
        }
        if trade["price"] >= 4:
            last_at_or_above_four = trade
        if timestamp >= datetime(2025, 10, 10, 20, 30, tzinfo=timezone.utc):
            for threshold in thresholds:
                if threshold not in first_at_or_below and trade["price"] <= threshold:
                    first_at_or_below[threshold] = trade
        if timestamp == FLASH_TIME:
            exact_trades.append(trade)

    exact_agg: list[dict[str, Any]] = []
    for row in read_zip_rows(agg_zip):
        timestamp = microsecond_time(row[5])
        if timestamp < FLASH_TIME:
            continue
        if timestamp > FLASH_TIME:
            break
        exact_agg.append(
            {
                "aggregate_trade_id": int(row[0]),
                "price": float(row[1]),
                "quantity_atom": float(row[2]),
                "first_trade_id": int(row[3]),
                "last_trade_id": int(row[4]),
                "time_utc": iso(timestamp),
                "buyer_is_maker": row[6].lower() == "true",
            }
        )

    if not exact_trades or not exact_agg or last_at_or_above_four is None:
        raise ValueError("Could not reconstruct the low-timestamp sell episode")
    raw_quantity = sum(row["quantity_atom"] for row in exact_trades)
    raw_quote = sum(row["quote_usdt"] for row in exact_trades)
    episode = {
        "timestamp_utc": iso(FLASH_TIME),
        "raw_trade_count": len(exact_trades),
        "aggregate_trade_row_count": len(exact_agg),
        "first_trade_id": exact_trades[0]["trade_id"],
        "last_trade_id": exact_trades[-1]["trade_id"],
        "trade_ids_contiguous": exact_trades[-1]["trade_id"] - exact_trades[0]["trade_id"] + 1
        == len(exact_trades),
        "all_buyer_is_maker": all(row["buyer_is_maker"] for row in exact_trades),
        "aggressive_sell_quantity_atom": raw_quantity,
        "execution_quote_usdt": raw_quote,
        "volume_weighted_price_usdt": raw_quote / raw_quantity,
        "first_execution_price_usdt": exact_trades[0]["price"],
        "minimum_execution_price_usdt": min(row["price"] for row in exact_trades),
        "within_timestamp_drop_pct": 100
        * (exact_trades[0]["price"] - min(row["price"] for row in exact_trades))
        / exact_trades[0]["price"],
        "last_trade_at_or_above_4_usdt": last_at_or_above_four,
        "seconds_from_last_4_usdt_trade_to_low": (FLASH_TIME - parse_time(last_at_or_above_four["time_utc"])).total_seconds(),
        "first_trade_at_or_below_threshold": {
            f"{threshold:g}": first_at_or_below[threshold] for threshold in thresholds
        },
        "public_data_inference": (
            "The contiguous, same-microsecond, sell-aggressor sequence is strongly consistent with one "
            "aggressive taker-order episode sweeping multiple price levels. Different-price aggregate-trade "
            "rows do not expose a common order ID, so one versus tightly synchronized orders is not provable."
        ),
        "identity_limitation": "Public trade and aggregate-trade files contain no account, beneficial-owner, or taker-order identifier.",
    }
    return episode, exact_agg


def load_address_baseline(checkpoint: Path) -> list[float]:
    with gzip.open(checkpoint, "rt", encoding="utf-8") as stream:
        document = json.loads(next(stream))
    buckets = [0.0] * BASELINE_BUCKET_COUNT
    for index, metrics in document["partial"].items():
        buckets[int(index)] = metrics.get("confirmed_exchange_in_uatom", 0) / 1_000_000
    return buckets


def aligned_baseline_test(
    rows: list[dict[str, Any]], address: str, minutes: int, buckets: list[float]
) -> dict[str, Any]:
    aligned_end = FLASH_TIME.replace(second=0, microsecond=0)
    aligned_start = aligned_end - timedelta(minutes=minutes)
    observed_rows = [
        row for row in rows if row["recipient"] == address and aligned_start <= row["_time"] < aligned_end
    ]
    observed = sum(row["amount_atom"] for row in observed_rows)
    length = minutes // 5
    rolling = [sum(buckets[index : index + length]) for index in range(len(buckets) - length + 1)]
    nonoverlap = [sum(buckets[index : index + length]) for index in range(0, len(buckets) - length + 1, length)]
    return {
        "aligned_window_start_utc": iso(aligned_start),
        "aligned_window_end_utc": iso(aligned_end),
        "event_transfer_count": len(observed_rows),
        "event_amount_atom": observed,
        "rolling_5min_control_count": len(rolling),
        "rolling_5min_percentile_empirical": percentile_le(rolling, observed),
        "rolling_5min_upper_tail_p_plus_one": exact_plus_one_upper(rolling, observed),
        "rolling_5min_control_median_atom": statistics.median(rolling),
        "rolling_5min_control_p95_atom": sorted(rolling)[int(0.95 * (len(rolling) - 1))],
        "rolling_5min_control_max_atom": max(rolling),
        "nonoverlap_control_count": len(nonoverlap),
        "nonoverlap_percentile_empirical": percentile_le(nonoverlap, observed),
        "nonoverlap_upper_tail_p_plus_one": exact_plus_one_upper(nonoverlap, observed),
        "nonoverlap_control_median_atom": statistics.median(nonoverlap),
        "nonoverlap_control_max_atom": max(nonoverlap),
        "caveat": "Rolling controls overlap and are serially dependent; the p-value is descriptive.",
    }


def four_day_control(
    deposit_rows: list[dict[str, Any]], minutes: int, data_start: datetime, data_end: datetime
) -> dict[str, Any]:
    def window(end: datetime) -> tuple[float, float, float, float]:
        subset = [row for row in deposit_rows if end - timedelta(minutes=minutes) <= row["_time"] < end]
        sender = concentration(subset, "sender")
        memo_rows = [{**row, "memo_cluster": memo_cluster(row["tx_memo"])} for row in subset]
        memo = concentration(memo_rows, "memo_cluster")
        return sender["amount_atom"], sender["top1_share"], sender["hhi"], memo["top1_share"]

    observed = window(FLASH_TIME)
    controls: list[tuple[float, float, float, float]] = []
    current = data_start.replace(second=FLASH_TIME.second, microsecond=FLASH_TIME.microsecond)
    if current < data_start:
        current += timedelta(minutes=1)
    while current <= data_end:
        # Exclude all windows that overlap the event endpoint, avoiding mechanical self-inclusion.
        if abs((current - FLASH_TIME).total_seconds()) > minutes * 60:
            controls.append(window(current))
        current += timedelta(minutes=1)
    names = ["amount_atom", "top_sender_share", "sender_hhi", "top_memo_share"]
    output: dict[str, Any] = {
        "exact_window_start_utc": iso(FLASH_TIME - timedelta(minutes=minutes)),
        "exact_window_end_utc": iso(FLASH_TIME),
        "control_endpoint_spacing": "1 minute with the flash timestamp's seconds and microseconds",
        "control_count": len(controls),
        "event": dict(zip(names, observed)),
        "metrics": {},
        "caveat": "Only four event-centered days are retained at sender level; controls overlap and are descriptive.",
    }
    for index, name in enumerate(names):
        values = [row[index] for row in controls]
        output["metrics"][name] = {
            "event_percentile_empirical": percentile_le(values, observed[index]),
            "control_median": statistics.median(values),
            "control_p95": sorted(values)[int(0.95 * (len(values) - 1))],
            "control_max": max(values),
        }
    return output


def wallet_profile(
    address: str,
    rows: list[dict[str, Any]],
    ibc_rows: list[dict[str, Any]],
    labels: dict[str, str],
    binance_address: str,
) -> dict[str, Any]:
    incoming = [row for row in rows if row["recipient"] == address]
    outgoing = [row for row in rows if row["sender"] == address]
    binance = [row for row in outgoing if row["recipient"] == binance_address]
    sources: Counter[str] = Counter()
    destinations: Counter[str] = Counter()
    for row in incoming:
        sources[row["sender"]] += row["amount_atom"]
    for row in outgoing:
        destinations[row["recipient"]] += row["amount_atom"]
    ibc = [row for row in ibc_rows if row.get("sender") == address or row.get("receiver") == address]
    ibc_by_route: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: {"count": 0, "amount_atom": 0.0})
    for row in ibc:
        key = (row["direction"], row.get("counterparty_chain_hint") or "unknown")
        ibc_by_route[key]["count"] += 1
        ibc_by_route[key]["amount_atom"] += float(row.get("amount_atom") or 0)
    total_in = sum(sources.values())
    total_out = sum(destinations.values())
    top_source = sources.most_common(1)[0] if sources else (None, 0.0)
    incoming_from_labels: Counter[str] = Counter()
    for source, amount in sources.items():
        if source in labels:
            incoming_from_labels[labels[source]] += amount
    return {
        "address": address,
        "four_day_first_seen_utc": min((row["time_utc"] for row in incoming + outgoing), default=None),
        "four_day_last_seen_utc": max((row["time_utc"] for row in incoming + outgoing), default=None),
        "four_day_incoming_count": len(incoming),
        "four_day_incoming_atom": total_in,
        "four_day_outgoing_count": len(outgoing),
        "four_day_outgoing_atom": total_out,
        "four_day_counterparty_count": len(set(sources) | set(destinations)),
        "binance_deposit_count": len(binance),
        "binance_deposit_atom": sum(row["amount_atom"] for row in binance),
        "binance_unique_memo_cluster_count": len({memo_cluster(row["tx_memo"]) for row in binance}),
        "top_direct_source": top_source[0],
        "top_direct_source_public_label": labels.get(top_source[0]),
        "top_direct_source_atom": top_source[1],
        "top_direct_source_share": top_source[1] / total_in if total_in else 0.0,
        "incoming_from_confirmed_exchange_labels_atom": dict(sorted(incoming_from_labels.items())),
        "confirmed_exchange_destination_labels": sorted(
            {labels[recipient] for recipient in destinations if recipient in labels}
        ),
        "ibc_match_count": len(ibc),
        "ibc_routes": [
            {
                "direction": direction,
                "counterparty_chain_hint": chain,
                "count": int(values["count"]),
                "amount_atom": values["amount_atom"],
            }
            for (direction, chain), values in sorted(ibc_by_route.items())
        ],
        "top_direct_sources": [
            {
                "address": source,
                "public_label": labels.get(source),
                "amount_atom": amount,
                "share": amount / total_in if total_in else 0.0,
            }
            for source, amount in sources.most_common(8)
        ],
        "top_direct_destinations": [
            {
                "address": destination,
                "public_label": labels.get(destination),
                "amount_atom": amount,
                "share": amount / total_out if total_out else 0.0,
            }
            for destination, amount in destinations.most_common(8)
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    processed = PROJECT_ROOT / "data" / "processed" / "cosmoshub"
    daily = PROJECT_ROOT / "data" / "raw" / "binance" / "spot" / "daily"
    parser.add_argument("--atom-transfers", type=Path, default=processed / "atom_transfers_2025-10-09_2025-10-12.jsonl.gz")
    parser.add_argument("--ibc-transfers", type=Path, default=processed / "ibc_transfers_2025-10-09_2025-10-12.jsonl.gz")
    parser.add_argument("--labels", type=Path, default=PROJECT_ROOT / "metadata" / "exchange_address_labels.json")
    parser.add_argument("--binance-checkpoint", type=Path, default=processed / "baseline_30d" / "query_checkpoints" / "0165.jsonl.gz")
    parser.add_argument("--trades", type=Path, default=daily / "trades" / "ATOMUSDT" / "ATOMUSDT-trades-2025-10-10.zip")
    parser.add_argument("--agg-trades", type=Path, default=daily / "aggTrades" / "ATOMUSDT" / "ATOMUSDT-aggTrades-2025-10-10.zip")
    parser.add_argument("--output", type=Path, default=RESULTS / "wallet_coordination_analysis.json")
    parser.add_argument("--candidates-csv", type=Path, default=RESULTS / "wallet_coordination_candidates.csv")
    parser.add_argument("--sell-sequence-csv", type=Path, default=RESULTS / "flash_sell_sequence.csv")
    args = parser.parse_args()

    label_document = json.loads(args.labels.read_text(encoding="utf-8"))
    labels = {row["address"]: row["label"] for row in label_document["labels"]}
    binance_address = next(address for address, label in labels.items() if label == "Binance")
    kucoin_address = next(address for address, label in labels.items() if label == "KuCoin")

    rows: list[dict[str, Any]] = []
    for row in jsonl_gzip(args.atom_transfers):
        if row["flow_class"] == "direct_bank":
            row["_time"] = parse_time(row["time_utc"])
            rows.append(row)
    ibc_rows = [row for row in jsonl_gzip(args.ibc_transfers) if row.get("is_atom")]
    data_start = min(row["_time"] for row in rows)
    data_end = max(row["_time"] for row in rows)
    binance_deposits = [row for row in rows if row["recipient"] == binance_address]

    market, sell_sequence = market_episode(args.trades, args.agg_trades)
    baseline = load_address_baseline(args.binance_checkpoint)
    aligned_tests = {
        f"{minutes}m": aligned_baseline_test(rows, binance_address, minutes, baseline)
        for minutes in (30, 120, 360, 1440)
    }
    four_day_tests = {
        f"{minutes}m": four_day_control(binance_deposits, minutes, data_start, data_end)
        for minutes in (30, 120, 360, 1440)
    }

    exact_pre_2h = [row for row in binance_deposits if FLASH_TIME - timedelta(hours=2) <= row["_time"] < FLASH_TIME]
    exact_pre_30m = [row for row in binance_deposits if FLASH_TIME - timedelta(minutes=30) <= row["_time"] < FLASH_TIME]
    sender_concentration_2h = concentration(exact_pre_2h, "sender")
    sender_concentration_30m = concentration(exact_pre_30m, "sender")
    memo_concentration_2h = concentration(
        [{**row, "memo_cluster": memo_cluster(row["tx_memo"])} for row in exact_pre_2h], "memo_cluster"
    )

    nearest = max((row for row in binance_deposits if row["_time"] < FLASH_TIME), key=lambda row: row["_time"])
    nearest_memo = nearest["tx_memo"]
    same_memo = [row for row in binance_deposits if row["tx_memo"] == nearest_memo]
    same_sender = [row for row in binance_deposits if row["sender"] == nearest["sender"]]
    same_amount = [row for row in binance_deposits if math.isclose(row["amount_atom"], nearest["amount_atom"], abs_tol=1e-12)]
    nearest_profile = wallet_profile(nearest["sender"], rows, ibc_rows, labels, binance_address)

    top_sender_amounts: Counter[str] = Counter()
    for row in exact_pre_2h:
        top_sender_amounts[row["sender"]] += row["amount_atom"]
    candidates: list[dict[str, Any]] = []
    for rank, (address, amount) in enumerate(top_sender_amounts.most_common(15), start=1):
        profile = wallet_profile(address, rows, ibc_rows, labels, binance_address)
        sender_rows = [row for row in binance_deposits if row["sender"] == address]
        pre_30 = [row for row in sender_rows if FLASH_TIME - timedelta(minutes=30) <= row["_time"] < FLASH_TIME]
        pre_6h = [row for row in sender_rows if FLASH_TIME - timedelta(hours=6) <= row["_time"] < FLASH_TIME]
        pre_24h = [row for row in sender_rows if FLASH_TIME - timedelta(hours=24) <= row["_time"] < FLASH_TIME]
        last = max((row for row in sender_rows if row["_time"] < FLASH_TIME), key=lambda row: row["_time"])
        candidates.append(
            {
                "rank_by_pre_2h_binance_inflow": rank,
                "address": address,
                "pre_30m_binance_atom": sum(row["amount_atom"] for row in pre_30),
                "pre_2h_binance_atom": amount,
                "pre_2h_share": amount / sender_concentration_2h["amount_atom"],
                "pre_6h_binance_atom": sum(row["amount_atom"] for row in pre_6h),
                "pre_24h_binance_atom": sum(row["amount_atom"] for row in pre_24h),
                "last_pre_flash_binance_time_utc": last["time_utc"],
                "seconds_from_last_binance_transfer_to_low": (FLASH_TIME - last["_time"]).total_seconds(),
                **{key: value for key, value in profile.items() if key not in {"top_direct_sources", "top_direct_destinations", "ibc_routes"}},
            }
        )

    # One-hop common funding is a coordination lead, but service hot-wallet fan-out is a competing explanation.
    top_addresses = {row["address"] for row in candidates[:10]}
    source_to_targets: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        if row["recipient"] in top_addresses:
            source_to_targets[row["sender"]][row["recipient"]] += row["amount_atom"]
    common_sources = []
    for source, targets in source_to_targets.items():
        # Ignore validator/reward dust that touches many addresses but has no funding relevance.
        material_targets = Counter({address: amount for address, amount in targets.items() if amount >= 1.0})
        if len(material_targets) < 2 or sum(material_targets.values()) < 1_000:
            continue
        source_profile = wallet_profile(source, rows, ibc_rows, labels, binance_address)
        common_sources.append(
            {
                "source_address": source,
                "source_public_label": labels.get(source),
                "funded_top10_target_count": len(material_targets),
                "funded_top10_amount_atom": sum(material_targets.values()),
                "targets": [
                    {"address": address, "amount_atom": amount} for address, amount in material_targets.most_common()
                ],
                "source_four_day_counterparty_count": source_profile["four_day_counterparty_count"],
                "source_four_day_incoming_atom": source_profile["four_day_incoming_atom"],
                "source_four_day_outgoing_atom": source_profile["four_day_outgoing_atom"],
                "source_top_direct_source": source_profile["top_direct_source"],
                "source_top_direct_source_public_label": source_profile["top_direct_source_public_label"],
                "source_incoming_from_confirmed_exchange_labels_atom": source_profile[
                    "incoming_from_confirmed_exchange_labels_atom"
                ],
            }
        )
    common_sources.sort(key=lambda row: (-row["funded_top10_target_count"], -row["funded_top10_amount_atom"]))

    # Descriptive post-selection frequency: how often a random retained-data second follows a deposit this closely.
    observed_gap = (FLASH_TIME - nearest["_time"]).total_seconds()
    start_second = data_start.replace(microsecond=0)
    end_second = data_end.replace(microsecond=0)

    def coincidence_coverage(predicate: Callable[[dict[str, Any]], bool]) -> tuple[int, float]:
        times = sorted(row["_time"].timestamp() for row in binance_deposits if predicate(row))
        covered = 0
        total = 0
        cursor = start_second
        previous_index = -1
        while cursor <= end_second:
            stamp = cursor.timestamp()
            while previous_index + 1 < len(times) and times[previous_index + 1] < stamp:
                previous_index += 1
            if previous_index >= 0 and 0 <= stamp - times[previous_index] <= observed_gap:
                covered += 1
            total += 1
            cursor += timedelta(seconds=1)
        return len(times), covered / total

    coincidence = {}
    predicates = {
        "any_binance_deposit": lambda row: True,
        "binance_deposit_at_least_nearest_amount": lambda row: row["amount_atom"] >= nearest["amount_atom"],
        "same_sender": lambda row: row["sender"] == nearest["sender"],
        "same_exact_amount": lambda row: math.isclose(row["amount_atom"], nearest["amount_atom"], abs_tol=1e-12),
        "same_memo_cluster": lambda row: row["tx_memo"] == nearest_memo,
    }
    for name, predicate in predicates.items():
        count, coverage = coincidence_coverage(predicate)
        coincidence[name] = {"deposit_count": count, "random_second_coverage": coverage}

    qml_address = nearest["sender"]
    top_2h_address = top_sender_amounts.most_common(1)[0][0]
    top_2h_profile = wallet_profile(top_2h_address, rows, ibc_rows, labels, binance_address)
    nearest_funding_from_kucoin = sum(
        row["amount_atom"] for row in rows if row["sender"] == kucoin_address and row["recipient"] == qml_address
    )

    output = {
        "study_id": "atom_flash_crash_2025_10_10",
        "generated_at_utc": iso(datetime.now(timezone.utc)),
        "research_question": "Do public data support that a specific user or wallet deliberately engineered the ATOM/USDT flash crash?",
        "scope_guardrail": (
            "Addresses and memo clusters are pseudonymous flow identifiers. The analysis does not identify a natural person, "
            "beneficial owner, Binance account, intent, or wrongdoing."
        ),
        "source_sha256": {
            "atom_transfers": sha256_file(args.atom_transfers),
            "ibc_transfers": sha256_file(args.ibc_transfers),
            "labels": sha256_file(args.labels),
            "binance_30d_checkpoint": sha256_file(args.binance_checkpoint),
            "binance_trades": sha256_file(args.trades),
            "binance_agg_trades": sha256_file(args.agg_trades),
        },
        "market_side_order_episode": market,
        "nearest_pre_low_binance_inflow": {
            "time_utc": nearest["time_utc"],
            "seconds_before_low": observed_gap,
            "blocks_before_low_height": FLASH_HEIGHT - int(nearest["height"]),
            "height": nearest["height"],
            "tx_hash": nearest["tx_hash"],
            "sender": nearest["sender"],
            "recipient_public_label": labels[nearest["recipient"]],
            "recipient_address": nearest["recipient"],
            "amount_atom": nearest["amount_atom"],
            "memo_cluster_id": memo_cluster(nearest_memo),
            "amount_as_share_of_sell_episode": nearest["amount_atom"] / market["aggressive_sell_quantity_atom"],
            "same_memo_four_day_count": len(same_memo),
            "same_memo_four_day_atom": sum(row["amount_atom"] for row in same_memo),
            "same_sender_binance_four_day_count": len(same_sender),
            "same_sender_binance_four_day_atom": sum(row["amount_atom"] for row in same_sender),
            "same_exact_amount_binance_four_day_count": len(same_amount),
            "sender_profile": nearest_profile,
            "direct_funding_from_public_label_kucoin_atom": nearest_funding_from_kucoin,
            "interpretation": (
                "Temporal proximity is a lead, not a link. The transfer is only 20.6% of the sell episode, the memo recurs, "
                "and the sender's many counterparties, multiple Binance memo clusters, and large public-label KuCoin adjacency "
                "are characteristic of service/operational routing rather than a uniquely attributable end user."
            ),
        },
        "temporal_coincidence_post_selection_check": {
            "observed_gap_seconds": observed_gap,
            "four_day_random_second_coverage": coincidence,
            "caveat": (
                "These are descriptive coverage rates, not valid confirmatory p-values: the nearest transfer, amount, sender, "
                "and memo were selected after observing the event, creating a multiple-testing/look-elsewhere problem."
            ),
        },
        "binance_pre_low_inflow_30d_baseline": aligned_tests,
        "binance_sender_level_four_day_controls": four_day_tests,
        "exact_pre_low_concentration": {
            "30m_sender": sender_concentration_30m,
            "120m_sender": sender_concentration_2h,
            "120m_memo_cluster": memo_concentration_2h,
        },
        "top_pre_2h_binance_sender_profiles": candidates,
        "top_2h_sender_detail": {
            "address": top_2h_address,
            "profile": top_2h_profile,
            "interpretation": (
                "This address is the largest two-hour Binance sender, but its high-frequency bidirectional Osmosis IBC activity "
                "and hundreds of repeated fixed-memo Binance deposits are consistent with an automated routing/arbitrage role."
            ),
        },
        "common_one_hop_funding_sources_for_top10": common_sources[:20],
        "evidence_assessment": {
            "one_aggressive_sell_order_episode": "strongly_supported_but_order_id_not_public",
            "one_or_more_specific_binance_accounts_caused_final_wick": "plausible_but_not_publicly_attributable",
            "nearest_cosmos_sender_was_the_selling_account": "not_supported",
            "abnormally_large_binance_preloading_in_prior_30m_or_2h": "not_supported",
            "abnormally_concentrated_binance_preloading_in_prior_2h": "not_supported",
            "coordinated_wallet_manipulation_or_deliberate_intent": "not_established",
            "named_natural_person_or_beneficial_owner": "not_identifiable_from_public_data",
        },
        "required_nonpublic_evidence": [
            "Binance taker order ID, account/subaccount ID, order type, clientOrderId, IP/API-key audit trail, and liquidation flag",
            "Binance deposit-credit timestamp and mapping from the hashed memo cluster to the internal account",
            "Historical ATOM/USDT order-book snapshots immediately before 21:20:37.689043 UTC",
            "Cross-venue account records linking Cosmos transfers to sell orders and withdrawals",
        ],
        "bottom_line": (
            "Public market data isolate a likely single or tightly synchronized aggressive sell-order episode that produced the "
            "terminal wick. Public on-chain data do not connect that episode to a specific Cosmos wallet or user and do not show "
            "exceptional Binance pre-loading or concentration. Deliberate manipulation remains possible but is not established."
        ),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with args.candidates_csv.open("w", newline="", encoding="utf-8") as stream:
        fields = list(candidates[0])
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(candidates)
    with args.sell_sequence_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(sell_sequence[0]))
        writer.writeheader()
        writer.writerows(sell_sequence)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "candidates_csv": str(args.candidates_csv),
                "sell_sequence_csv": str(args.sell_sequence_csv),
                "candidate_count": len(candidates),
                "sell_sequence_rows": len(sell_sequence),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
