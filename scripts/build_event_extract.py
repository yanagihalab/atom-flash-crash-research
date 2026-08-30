#!/usr/bin/env python3
"""Build a compact, auditable event extract from downloaded market data."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import zipfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "data"
EVENT = datetime.fromisoformat("2025-10-10T21:20:37.689043+00:00")
EVENT_US = int(EVENT.timestamp() * 1_000_000)
MINUTE_START = EVENT.replace(second=0, microsecond=0)
MINUTE_END = MINUTE_START + timedelta(minutes=1)
EXTRACT_START = MINUTE_START - timedelta(minutes=5)
EXTRACT_END = MINUTE_START + timedelta(minutes=5)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def epoch_to_us(value: str | int) -> int:
    number = int(value)
    if number >= 10**15:
        return number
    if number >= 10**12:
        return number * 1000
    return number * 1_000_000


def iso_from_us(value: int) -> str:
    return datetime.fromtimestamp(value / 1_000_000, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def read_binance_spot_trades(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with zipfile.ZipFile(path) as archive, archive.open(archive.namelist()[0]) as binary:
        for row in csv.reader(io.TextIOWrapper(binary, encoding="utf-8")):
            rows.append(
                {
                    "trade_id": int(row[0]),
                    "price": Decimal(row[1]),
                    "quantity": Decimal(row[2]),
                    "quote_quantity": Decimal(row[3]),
                    "time_us": epoch_to_us(row[4]),
                    "is_buyer_maker": row[5] == "True",
                    "is_best_match": row[6] == "True",
                }
            )
    return rows


def read_binance_futures_trades(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with zipfile.ZipFile(path) as archive, archive.open(archive.namelist()[0]) as binary:
        for row in csv.DictReader(io.TextIOWrapper(binary, encoding="utf-8")):
            rows.append(
                {
                    "trade_id": int(row["id"]),
                    "price": Decimal(row["price"]),
                    "quantity": Decimal(row["qty"]),
                    "quote_quantity": Decimal(row["quote_qty"]),
                    "time_us": epoch_to_us(row["time"]),
                    "is_buyer_maker": row["is_buyer_maker"] == "true",
                }
            )
    return rows


def read_kraken(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with gzip.open(path, mode="rt", encoding="utf-8") as stream:
        for line in stream:
            payload = json.loads(line)
            timestamp = datetime.fromisoformat(payload["trade_ts"].replace("Z", "+00:00"))
            rows.append(
                {
                    "trade_id": payload["trade_id"],
                    "price": Decimal(payload["price"]),
                    "quantity": Decimal(payload["quantity"]),
                    "quote_quantity": Decimal(payload["price"]) * Decimal(payload["quantity"]),
                    "time_us": int(timestamp.timestamp() * 1_000_000),
                }
            )
    return rows


def summarize_trade_minute(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    start_us = int(MINUTE_START.timestamp() * 1_000_000)
    end_us = int(MINUTE_END.timestamp() * 1_000_000)
    selected = [row for row in rows if start_us <= row["time_us"] < end_us]
    if not selected:
        return {"trade_count": 0}
    return {
        "trade_count": len(selected),
        "open": str(selected[0]["price"]),
        "high": str(max(row["price"] for row in selected)),
        "low": str(min(row["price"] for row in selected)),
        "close": str(selected[-1]["price"]),
        "base_volume": str(sum((row["quantity"] for row in selected), Decimal())),
        "quote_volume": str(sum((row["quote_quantity"] for row in selected), Decimal())),
    }


def write_event_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    start_us = int(EXTRACT_START.timestamp() * 1_000_000)
    end_us = int(EXTRACT_END.timestamp() * 1_000_000)
    selected = [row for row in rows if start_us <= row["time_us"] < end_us]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as text:
                writer = csv.writer(text, lineterminator="\n")
                writer.writerow(["trade_id", "trade_time_utc", "timestamp_us", "price_usdt", "quantity_atom", "quote_quantity_usdt", "is_buyer_maker", "aggressor_side", "is_best_match"])
                for row in selected:
                    writer.writerow(
                        [
                            row["trade_id"],
                            iso_from_us(row["time_us"]),
                            row["time_us"],
                            row["price"],
                            row["quantity"],
                            row["quote_quantity"],
                            row["is_buyer_maker"],
                            "sell" if row["is_buyer_maker"] else "buy",
                            row["is_best_match"],
                        ]
                    )
    os.replace(temporary, path)


def main() -> None:
    primary_path = DATA_ROOT / "raw" / "binance" / "spot" / "daily" / "trades" / "ATOMUSDT" / "ATOMUSDT-trades-2025-10-10.zip"
    atomusdc_path = DATA_ROOT / "raw" / "binance" / "spot" / "daily" / "trades" / "ATOMUSDC" / "ATOMUSDC-trades-2025-10-10.zip"
    futures_path = DATA_ROOT / "raw" / "binance" / "futures" / "um" / "daily" / "trades" / "ATOMUSDT" / "ATOMUSDT-trades-2025-10-10.zip"
    mark_path = DATA_ROOT / "raw" / "binance" / "futures" / "um" / "daily" / "markPriceKlines" / "ATOMUSDT" / "1m" / "ATOMUSDT-1m-2025-10-10.zip"
    kraken_path = DATA_ROOT / "raw" / "kraken" / "spot" / "trades" / "ATOMUSD" / "ATOMUSD-trades-2025-10-10.jsonl.gz"
    coinbase_path = DATA_ROOT / "raw" / "coinbase" / "spot" / "candles" / "ATOM-USD" / "60s" / "ATOM-USD-60s-2025-10-10.json"
    required = [primary_path, atomusdc_path, futures_path, mark_path, kraken_path, coinbase_path]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit("missing required input files:\n" + "\n".join(missing))

    primary = read_binance_spot_trades(primary_path)
    atomusdc = read_binance_spot_trades(atomusdc_path)
    futures = read_binance_futures_trades(futures_path)
    kraken = read_kraken(kraken_path)
    minimum = min(row["price"] for row in primary)
    min_rows = [row for row in primary if row["price"] == minimum]
    min_time = min_rows[0]["time_us"]
    same_time = [row for row in primary if row["time_us"] == min_time]
    min_index = next(index for index, row in enumerate(primary) if row["price"] == minimum)
    previous_trade = primary[min_index - 1]
    first_same_time_index = min_index
    while first_same_time_index > 0 and primary[first_same_time_index - 1]["time_us"] == min_time:
        first_same_time_index -= 1
    previous_distinct_timestamp = primary[first_same_time_index - 1]
    next_trade = primary[min_index + len(min_rows)]
    below_one = [row for row in primary if row["price"] < Decimal("1")]
    last_below_one = max(row["time_us"] for row in below_one)
    first_excursion_start = min_index
    while first_excursion_start > 0 and primary[first_excursion_start - 1]["price"] < Decimal("1"):
        first_excursion_start -= 1
    first_excursion_end = min_index
    while first_excursion_end + 1 < len(primary) and primary[first_excursion_end + 1]["price"] < Decimal("1"):
        first_excursion_end += 1
    first_excursion = primary[first_excursion_start : first_excursion_end + 1]
    first_at_or_above_one = primary[first_excursion_end + 1]

    recovery: dict[str, Any] = {}
    for threshold in (Decimal("1"), Decimal("1.5"), Decimal("2"), Decimal("2.5"), Decimal("3")):
        row = next(candidate for candidate in primary[min_index + 1 :] if candidate["price"] >= threshold)
        recovery[str(threshold)] = {
            "trade_time_utc": iso_from_us(row["time_us"]),
            "price": str(row["price"]),
            "elapsed_seconds": round((row["time_us"] - min_time) / 1_000_000, 6),
        }

    coinbase_rows = json.loads(coinbase_path.read_text(encoding="utf-8"))
    minute_epoch = int(MINUTE_START.timestamp())
    coinbase = next(row for row in coinbase_rows if int(row[0]) == minute_epoch)

    with zipfile.ZipFile(mark_path) as archive, archive.open(archive.namelist()[0]) as binary:
        mark_rows = list(csv.DictReader(io.TextIOWrapper(binary, encoding="utf-8")))
    mark = next(row for row in mark_rows if epoch_to_us(row["open_time"]) == int(MINUTE_START.timestamp() * 1_000_000))

    event_output = DATA_ROOT / "processed" / "event_window" / "binance_spot_atomusdt_trades_2025-10-10_2115-2125_utc.csv.gz"
    write_event_csv(event_output, primary)
    results = {
        "study_id": "atom_flash_crash_2025_10_10",
        "event_time_utc": iso_from_us(min_time),
        "event_time_jst": datetime.fromtimestamp(min_time / 1_000_000, timezone.utc).astimezone(timezone(timedelta(hours=9))).isoformat(timespec="microseconds"),
        "primary_daily_summary": {
            "trade_count": len(primary),
            "open": str(primary[0]["price"]),
            "high": str(max(row["price"] for row in primary)),
            "low": str(minimum),
            "close": str(primary[-1]["price"]),
        },
        "minimum_price_event": {
            "minimum_price_usdt": str(minimum),
            "minimum_trade_count": len(min_rows),
            "minimum_quantity_atom": str(sum((row["quantity"] for row in min_rows), Decimal())),
            "minimum_quote_quantity_usdt": str(sum((row["quote_quantity"] for row in min_rows), Decimal())),
            "below_one_trade_count": len(below_one),
            "below_one_quantity_atom": str(sum((row["quantity"] for row in below_one), Decimal())),
            "all_sub_one_trades_last_time_utc": iso_from_us(last_below_one),
            "all_sub_one_trades_span_from_minimum_milliseconds": round((last_below_one - min_time) / 1000, 6),
            "first_sub_one_excursion": {
                "trade_count": len(first_excursion),
                "quantity_atom": str(sum((row["quantity"] for row in first_excursion), Decimal())),
                "first_trade_time_utc": iso_from_us(first_excursion[0]["time_us"]),
                "last_trade_time_utc": iso_from_us(first_excursion[-1]["time_us"]),
                "observed_trade_span_milliseconds": round((first_excursion[-1]["time_us"] - first_excursion[0]["time_us"]) / 1000, 6),
                "next_trade_at_or_above_one_time_utc": iso_from_us(first_at_or_above_one["time_us"]),
                "time_from_minimum_to_next_trade_at_or_above_one_milliseconds": round((first_at_or_above_one["time_us"] - min_time) / 1000, 6),
            },
            "same_matching_timestamp_trade_count": len(same_time),
            "same_matching_timestamp_quantity_atom": str(sum((row["quantity"] for row in same_time), Decimal())),
            "same_timestamp_all_sell_aggressor": all(row["is_buyer_maker"] for row in same_time),
            "same_timestamp_first_price": str(same_time[0]["price"]),
            "same_timestamp_last_price": str(same_time[-1]["price"]),
            "immediately_previous_trade": {
                "trade_time_utc": iso_from_us(previous_trade["time_us"]),
                "price": str(previous_trade["price"]),
                "elapsed_to_minimum_milliseconds": round((min_time - previous_trade["time_us"]) / 1000, 6),
            },
            "previous_distinct_matching_timestamp": {
                "trade_time_utc": iso_from_us(previous_distinct_timestamp["time_us"]),
                "price": str(previous_distinct_timestamp["price"]),
                "elapsed_to_minimum_milliseconds": round((min_time - previous_distinct_timestamp["time_us"]) / 1000, 6),
            },
            "immediately_next": {
                "trade_time_utc": iso_from_us(next_trade["time_us"]),
                "price": str(next_trade["price"]),
                "elapsed_from_minimum_milliseconds": round((next_trade["time_us"] - min_time) / 1000, 6),
            },
            "recovery": recovery,
        },
        "event_minute_cross_venue": {
            "minute_start_utc": MINUTE_START.isoformat().replace("+00:00", "Z"),
            "binance_spot_atomusdt": summarize_trade_minute(primary),
            "binance_spot_atomusdc": summarize_trade_minute(atomusdc),
            "binance_usdm_futures_atomusdt": summarize_trade_minute(futures),
            "binance_usdm_mark_price": {"open": mark["open"], "high": mark["high"], "low": mark["low"], "close": mark["close"]},
            "kraken_spot_atomusd": summarize_trade_minute(kraken),
            "coinbase_spot_atomusd": {
                "open": str(coinbase[3]),
                "high": str(coinbase[2]),
                "low": str(coinbase[1]),
                "close": str(coinbase[4]),
                "base_volume": str(coinbase[5]),
            },
        },
        "provenance": {
            str(path.relative_to(PROJECT_ROOT)): {"sha256": sha256_file(path), "size_bytes": path.stat().st_size} for path in required
        },
        "event_extract": {
            "local_path": str(event_output.relative_to(PROJECT_ROOT)),
            "sha256": sha256_file(event_output),
            "window": "[2025-10-10T21:15:00Z, 2025-10-10T21:25:00Z)",
        },
    }
    results_root = PROJECT_ROOT / "results"
    results_root.mkdir(parents=True, exist_ok=True)
    output = results_root / "event_verification.json"
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
