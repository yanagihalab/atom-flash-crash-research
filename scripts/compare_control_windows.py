#!/usr/bin/env python3
"""Compare the flash-crash window with shifted two-hour control windows."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import statistics
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED = PROJECT_ROOT / "data" / "processed" / "cosmoshub"
RAW = PROJECT_ROOT / "data" / "raw"
EVENT_START = datetime.fromisoformat("2025-10-10T20:30:00+00:00")
EVENT_END = datetime.fromisoformat("2025-10-10T22:30:00+00:00")
CONTROL_DAYS = ("2025-10-09", "2025-10-11", "2025-10-12")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def epoch_to_us(value: str | int) -> int:
    number = int(value)
    if number >= 10**15:
        return number
    if number >= 10**12:
        return number * 1000
    return number * 1_000_000


def jsonl_gzip(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def focal_windows() -> list[dict[str, Any]]:
    definitions = [
        ("matched_pre_day", "Primary matched pre-event control", datetime.fromisoformat("2025-10-09T20:30:00+00:00")),
        ("same_day_pre", "Adjacent pre-event sensitivity window", datetime.fromisoformat("2025-10-10T18:30:00+00:00")),
        ("event", "Flash-crash event window", EVENT_START),
        ("matched_post_day1", "Post-event matched-time sensitivity window", datetime.fromisoformat("2025-10-11T20:30:00+00:00")),
        ("matched_post_day2", "Post-event matched-time sensitivity window", datetime.fromisoformat("2025-10-12T20:30:00+00:00")),
    ]
    return [
        {"window_id": window_id, "role": role, "start_utc": start, "end_utc": start + timedelta(hours=2)}
        for window_id, role, start in definitions
    ]


def non_event_bins() -> list[dict[str, Any]]:
    bins: list[dict[str, Any]] = []
    for day in CONTROL_DAYS:
        start_of_day = datetime.fromisoformat(f"{day}T00:00:00+00:00")
        for bucket in range(12):
            start = start_of_day + timedelta(hours=bucket * 2)
            bins.append(
                {
                    "window_id": f"control_{day}_{start:%H%M}",
                    "role": "Non-event-day two-hour reference bin",
                    "start_utc": start,
                    "end_utc": start + timedelta(hours=2),
                }
            )
    return bins


def blank_chain_metrics() -> dict[str, int]:
    return defaultdict(int)


def assign_chain_row(
    metrics: dict[str, int],
    row: dict[str, Any],
    flow_type: str,
    structured_addresses: set[str],
    watchlist_addresses: set[str],
    labeled_addresses: set[str],
) -> None:
    if flow_type == "atom":
        if row["flow_class"] != "direct_bank":
            return
        amount = int(row["amount_uatom"])
        metrics["direct_transfer_count"] += 1
        metrics["direct_transfer_uatom"] += amount
        recipient = row["recipient"]
        sender = row["sender"]
        if recipient in structured_addresses:
            metrics["structured_candidate_in_count"] += 1
            metrics["structured_candidate_in_uatom"] += amount
        if recipient in watchlist_addresses:
            metrics["large_flow_watchlist_in_count"] += 1
            metrics["large_flow_watchlist_in_uatom"] += amount
        if recipient in labeled_addresses:
            metrics["confirmed_exchange_in_count"] += 1
            metrics["confirmed_exchange_in_uatom"] += amount
        if sender in labeled_addresses:
            metrics["confirmed_exchange_out_count"] += 1
            metrics["confirmed_exchange_out_uatom"] += amount
    elif row["is_atom"]:
        amount = int(row["amount_base_units"])
        direction = row["direction"]
        metrics[f"ibc_{direction}_count"] += 1
        metrics[f"ibc_{direction}_uatom"] += amount


def finalize_chain(metrics: dict[str, int]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in (
        "direct_transfer",
        "structured_candidate_in",
        "large_flow_watchlist_in",
        "confirmed_exchange_in",
        "confirmed_exchange_out",
        "ibc_inbound",
        "ibc_outbound",
    ):
        result[f"{name}_count"] = metrics[f"{name}_count"]
        result[f"{name}_atom"] = metrics[f"{name}_uatom"] / 1_000_000
    result["confirmed_exchange_net_in_atom"] = (
        metrics["confirmed_exchange_in_uatom"] - metrics["confirmed_exchange_out_uatom"]
    ) / 1_000_000
    result["ibc_net_inbound_atom"] = (metrics["ibc_inbound_uatom"] - metrics["ibc_outbound_uatom"]) / 1_000_000
    return result


def aggregate_chain(windows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    candidates = json.loads(
        (PROCESSED / "exchange_inflow_candidates_2025-10-09_2025-10-12.json").read_text(encoding="utf-8")
    )["candidates"]
    structured_addresses = {
        row["address"] for row in candidates if row["candidate_tier"] in {"behavioral_high", "behavioral_medium"}
    }
    watchlist_addresses = {row["address"] for row in candidates if row["candidate_tier"] == "large_flow_watchlist"}
    labels = json.loads((PROJECT_ROOT / "metadata" / "exchange_address_labels.json").read_text(encoding="utf-8"))["labels"]
    labeled_addresses = {row["address"] for row in labels}
    metrics = {window["window_id"]: blank_chain_metrics() for window in windows}

    sources = [
        (PROCESSED / "atom_transfers_2025-10-09_2025-10-12.jsonl.gz", "atom"),
        (PROCESSED / "ibc_transfers_2025-10-09_2025-10-12.jsonl.gz", "ibc"),
    ]
    for path, flow_type in sources:
        for row in jsonl_gzip(path):
            timestamp = parse_time(row["time_utc"])
            for window in windows:
                if window["start_utc"] <= timestamp < window["end_utc"]:
                    assign_chain_row(
                        metrics[window["window_id"]],
                        row,
                        flow_type,
                        structured_addresses,
                        watchlist_addresses,
                        labeled_addresses,
                    )
    return {window_id: finalize_chain(value) for window_id, value in metrics.items()}


def read_binance_klines(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with zipfile.ZipFile(path) as archive, archive.open(archive.namelist()[0]) as binary:
        for raw in csv.reader(io.TextIOWrapper(binary, encoding="utf-8")):
            if not raw or raw[0] == "open_time":
                continue
            rows.append(
                {
                    "time_us": epoch_to_us(raw[0]),
                    "open": Decimal(raw[1]),
                    "high": Decimal(raw[2]),
                    "low": Decimal(raw[3]),
                    "close": Decimal(raw[4]),
                    "base_volume": Decimal(raw[5]),
                    "quote_volume": Decimal(raw[7]),
                    "trade_count": int(raw[8]),
                    "taker_buy_base": Decimal(raw[9]),
                }
            )
    rows.sort(key=lambda row: row["time_us"])
    return rows


def read_coinbase(path: Path) -> list[dict[str, Any]]:
    raw_rows = json.loads(path.read_text(encoding="utf-8"))
    rows = [
        {
            "time_us": int(row[0]) * 1_000_000,
            "low": Decimal(str(row[1])),
            "high": Decimal(str(row[2])),
            "open": Decimal(str(row[3])),
            "close": Decimal(str(row[4])),
            "base_volume": Decimal(str(row[5])),
            "quote_volume": Decimal(0),
            "trade_count": 0,
            "taker_buy_base": Decimal(0),
        }
        for row in raw_rows
    ]
    rows.sort(key=lambda row: row["time_us"])
    return rows


def read_kraken(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            payload = json.loads(line)
            timestamp = parse_time(payload["trade_ts"])
            price = Decimal(payload["price"])
            quantity = Decimal(payload["quantity"])
            rows.append(
                {
                    "time_us": int(timestamp.timestamp() * 1_000_000),
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "base_volume": quantity,
                    "quote_volume": price * quantity,
                    "trade_count": 1,
                    "taker_buy_base": Decimal(0),
                }
            )
    rows.sort(key=lambda row: row["time_us"])
    return rows


def summarize_market(rows: Iterable[dict[str, Any]], start: datetime, end: datetime, has_aggressor: bool = False) -> dict[str, Any]:
    start_us = int(start.timestamp() * 1_000_000)
    end_us = int(end.timestamp() * 1_000_000)
    selected = [row for row in rows if start_us <= row["time_us"] < end_us]
    if not selected:
        return {"observation_count": 0}
    open_price = selected[0]["open"]
    close_price = selected[-1]["close"]
    base_volume = sum((row["base_volume"] for row in selected), Decimal())
    quote_volume = sum((row["quote_volume"] for row in selected), Decimal())
    trade_count = sum(row["trade_count"] for row in selected)
    high = max(row["high"] for row in selected)
    low = min(row["low"] for row in selected)
    result: dict[str, Any] = {
        "observation_count": len(selected),
        "trade_count": trade_count,
        "open": float(open_price),
        "high": float(high),
        "low": float(low),
        "close": float(close_price),
        "base_volume_atom": float(base_volume),
        "quote_volume": float(quote_volume),
        "return_pct": float((close_price / open_price - 1) * 100),
        "range_pct_of_open": float((high - low) / open_price * 100),
        "low_over_open": float(low / open_price),
    }
    if has_aggressor and base_volume:
        taker_buy_base = sum((row["taker_buy_base"] for row in selected), Decimal())
        result["sell_aggressor_base_share"] = float((base_volume - taker_buy_base) / base_volume)
    return result


def minute_discount(
    numerator: Iterable[dict[str, Any]], denominator: Iterable[dict[str, Any]], start: datetime, end: datetime
) -> dict[str, Any]:
    start_us = int(start.timestamp() * 1_000_000)
    end_us = int(end.timestamp() * 1_000_000)
    numerator_lows = {row["time_us"]: row["low"] for row in numerator if start_us <= row["time_us"] < end_us}
    denominator_lows = {row["time_us"]: row["low"] for row in denominator if start_us <= row["time_us"] < end_us}
    matched = sorted(set(numerator_lows) & set(denominator_lows))
    if not matched:
        return {"matched_minutes": 0}
    ratios = [(timestamp, numerator_lows[timestamp] / denominator_lows[timestamp]) for timestamp in matched if denominator_lows[timestamp]]
    timestamp, minimum_ratio = min(ratios, key=lambda item: item[1])
    return {
        "matched_minutes": len(ratios),
        "minimum_low_ratio": float(minimum_ratio),
        "maximum_discount_pct": float((Decimal(1) - minimum_ratio) * 100),
        "minimum_ratio_minute_utc": iso(datetime.fromtimestamp(timestamp / 1_000_000, timezone.utc)),
    }


def load_market_days() -> tuple[dict[str, dict[str, list[dict[str, Any]]]], dict[str, str]]:
    market: dict[str, dict[str, list[dict[str, Any]]]] = {}
    provenance: dict[str, str] = {}
    for day in ("2025-10-09", "2025-10-10", "2025-10-11", "2025-10-12"):
        paths = {
            "binance_atomusdt": RAW / "binance" / "spot" / "daily" / "klines" / "ATOMUSDT" / "1m" / f"ATOMUSDT-1m-{day}.zip",
            "binance_atomusdc": RAW / "binance" / "spot" / "daily" / "klines" / "ATOMUSDC" / "1m" / f"ATOMUSDC-1m-{day}.zip",
            "binance_futures": RAW / "binance" / "futures" / "um" / "daily" / "klines" / "ATOMUSDT" / "1m" / f"ATOMUSDT-1m-{day}.zip",
            "binance_mark": RAW / "binance" / "futures" / "um" / "daily" / "markPriceKlines" / "ATOMUSDT" / "1m" / f"ATOMUSDT-1m-{day}.zip",
            "kraken_atomusd": RAW / "kraken" / "spot" / "trades" / "ATOMUSD" / f"ATOMUSD-trades-{day}.jsonl.gz",
            "coinbase_atomusd": RAW / "coinbase" / "spot" / "candles" / "ATOM-USD" / "60s" / f"ATOM-USD-60s-{day}.json",
        }
        missing = [str(path) for path in paths.values() if not path.exists()]
        if missing:
            raise FileNotFoundError("missing market inputs:\n" + "\n".join(missing))
        market[day] = {
            "binance_atomusdt": read_binance_klines(paths["binance_atomusdt"]),
            "binance_atomusdc": read_binance_klines(paths["binance_atomusdc"]),
            "binance_futures": read_binance_klines(paths["binance_futures"]),
            "binance_mark": read_binance_klines(paths["binance_mark"]),
            "kraken_atomusd": read_kraken(paths["kraken_atomusd"]),
            "coinbase_atomusd": read_coinbase(paths["coinbase_atomusd"]),
        }
        for path in paths.values():
            provenance[str(path.relative_to(PROJECT_ROOT))] = sha256_file(path)
    return market, provenance


def aggregate_market(
    window: dict[str, Any], market_days: dict[str, dict[str, list[dict[str, Any]]]]
) -> dict[str, Any]:
    day = window["start_utc"].date().isoformat()
    data = market_days[day]
    result = {
        venue: summarize_market(rows, window["start_utc"], window["end_utc"], venue in {"binance_atomusdt", "binance_atomusdc", "binance_futures"})
        for venue, rows in data.items()
    }
    result["binance_usdt_vs_usdc"] = minute_discount(
        data["binance_atomusdt"], data["binance_atomusdc"], window["start_utc"], window["end_utc"]
    )
    result["binance_usdt_vs_coinbase"] = minute_discount(
        data["binance_atomusdt"], data["coinbase_atomusd"], window["start_utc"], window["end_utc"]
    )
    return result


def percentile_rank(values: list[float], value: float) -> float:
    return 100 * sum(control <= value for control in values) / len(values)


def distribution_summary(event: dict[str, Any], controls: list[dict[str, Any]], metrics: list[str]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for metric in metrics:
        values = [float(control[metric]) for control in controls]
        event_value = float(event[metric])
        median = statistics.median(values)
        ordered = sorted(values)
        p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
        output[metric] = {
            "event": event_value,
            "control_count": len(values),
            "control_mean": statistics.fmean(values),
            "control_median": median,
            "control_p95_empirical": p95,
            "event_percentile_empirical": percentile_rank(values, event_value),
            "event_to_control_median_ratio": event_value / median if median and "net_" not in metric else None,
        }
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "results" / "control_window_comparison.json")
    args = parser.parse_args()

    focal = focal_windows()
    distribution = non_event_bins()
    all_windows = focal + distribution
    chain = aggregate_chain(all_windows)
    market_days, market_provenance = load_market_days()
    market = {window["window_id"]: aggregate_market(window, market_days) for window in all_windows}

    focal_rows = []
    for window in focal:
        focal_rows.append(
            {
                "window_id": window["window_id"],
                "role": window["role"],
                "start_utc": iso(window["start_utc"]),
                "end_utc": iso(window["end_utc"]),
                "chain": chain[window["window_id"]],
                "market": market[window["window_id"]],
            }
        )

    distribution_rows = []
    for window in distribution:
        distribution_rows.append(
            {
                "window_id": window["window_id"],
                "start_utc": iso(window["start_utc"]),
                "end_utc": iso(window["end_utc"]),
                "chain": chain[window["window_id"]],
                "market": market[window["window_id"]],
            }
        )

    focal_by_id = {row["window_id"]: row for row in focal_rows}
    event_row = focal_by_id["event"]
    matched_controls = [focal_by_id[window_id] for window_id in ("matched_pre_day", "matched_post_day1", "matched_post_day2")]
    chain_metrics = [
        "direct_transfer_atom",
        "structured_candidate_in_atom",
        "large_flow_watchlist_in_atom",
        "confirmed_exchange_in_atom",
        "confirmed_exchange_out_atom",
        "confirmed_exchange_net_in_atom",
        "ibc_inbound_atom",
        "ibc_outbound_atom",
        "ibc_net_inbound_atom",
    ]
    chain_distribution = distribution_summary(event_row["chain"], [row["chain"] for row in distribution_rows], chain_metrics)
    matched_chain = distribution_summary(event_row["chain"], [row["chain"] for row in matched_controls], chain_metrics)
    market_metrics = ["range_pct_of_open", "base_volume_atom", "quote_volume", "trade_count"]
    market_distribution = distribution_summary(
        event_row["market"]["binance_atomusdt"],
        [row["market"]["binance_atomusdt"] for row in distribution_rows],
        market_metrics,
    )

    atom_path = PROCESSED / "atom_transfers_2025-10-09_2025-10-12.jsonl.gz"
    ibc_path = PROCESSED / "ibc_transfers_2025-10-09_2025-10-12.jsonl.gz"
    candidate_path = PROCESSED / "exchange_inflow_candidates_2025-10-09_2025-10-12.json"
    labels_path = PROJECT_ROOT / "metadata" / "exchange_address_labels.json"
    result = {
        "study_id": "atom_flash_crash_2025_10_10",
        "generated_at_utc": iso(datetime.now(timezone.utc)),
        "window_length_hours": 2,
        "primary_control_definition": "Previous UTC day, identical 20:30-22:30 clock time",
        "sensitivity_control_definition": "Same-clock windows on three non-event dates plus 36 non-overlapping two-hour bins on those dates",
        "focal_windows": focal_rows,
        "non_event_distribution": {
            "window_count": len(distribution_rows),
            "chain_event_comparison": chain_distribution,
            "matched_clock_chain_event_comparison": matched_chain,
            "binance_atomusdt_event_comparison": market_distribution,
            "windows": distribution_rows,
        },
        "source_sha256": {
            "atom_transfers": sha256_file(atom_path),
            "ibc_transfers": sha256_file(ibc_path),
            "candidates": sha256_file(candidate_path),
            "labels": sha256_file(labels_path),
            "market": market_provenance,
        },
        "guardrails": [
            "The previous-day matched-time window is the primary control because it precedes the shock.",
            "Post-event matched-time windows may contain recovery or behavioral responses and are sensitivity controls, not clean normal periods.",
            "The 36 non-event-day bins are an empirical reference distribution, not independent identically distributed observations.",
            "Gross exchange-address inflow and outflow include wallet sweeps and do not equal exchange customer trading flow.",
            "Behavioral candidate labels remain unconfirmed unless supported by the separate public address registry.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
