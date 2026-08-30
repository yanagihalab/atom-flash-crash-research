#!/usr/bin/env python3
"""Download date-partitioned Kraken trades and Coinbase one-minute candles."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


USER_AGENT = "atom-flash-crash-research/1.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def iter_dates(start: date, end_inclusive: date) -> Iterable[date]:
    current = start
    while current <= end_inclusive:
        yield current
        current += timedelta(days=1)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def request_json(url: str, params: dict[str, Any], attempts: int = 4) -> Any:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        except OSError:
            if attempt == attempts:
                raise
            time.sleep(2 ** (attempt - 1))
    raise AssertionError("unreachable")


def write_deterministic_gzip_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="\n") as text:
                for row in rows:
                    text.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def download_kraken_day(endpoint: str, symbol: str, page_size: int, day: date, destination: Path) -> dict[str, Any]:
    start_dt = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(days=1)
    cursor = iso_z(start_dt)
    seen: set[str] = set()
    trades: list[dict[str, Any]] = []
    page_count = 0
    queries: list[dict[str, Any]] = []

    while True:
        params = {"symbol": symbol, "from_ts": cursor, "to_ts": iso_z(end_dt), "count": page_size}
        payload = request_json(endpoint, params)
        if payload.get("error"):
            raise RuntimeError(f"Kraken API error for {day}: {payload['error']}")
        result = payload["result"]
        batch = result["trades"]
        page_count += 1
        queries.append({"from_ts": cursor, "to_ts": iso_z(end_dt), "count_returned": len(batch)})
        for trade in batch:
            timestamp = datetime.fromisoformat(trade["trade_ts"].replace("Z", "+00:00"))
            if start_dt <= timestamp < end_dt and trade["trade_id"] not in seen:
                seen.add(trade["trade_id"])
                trades.append(trade)
        if len(batch) < page_size:
            break
        next_cursor = result["last_ts"]
        if next_cursor <= cursor:
            raise RuntimeError(f"Kraken cursor did not advance for {day}: {cursor}")
        cursor = next_cursor

    trades.sort(key=lambda row: (row["trade_ts"], row["trade_id"]))
    write_deterministic_gzip_jsonl(destination, trades)
    return {
        "date_utc": day.isoformat(),
        "source": "Kraken PostTrade",
        "endpoint": endpoint,
        "symbol": symbol,
        "page_count": page_count,
        "trade_count": len(trades),
        "first_trade_ts": trades[0]["trade_ts"] if trades else None,
        "last_trade_ts": trades[-1]["trade_ts"] if trades else None,
        "queries": queries,
        "local_path": str(destination),
        "sha256": sha256_file(destination),
        "size_bytes": destination.stat().st_size,
    }


def download_coinbase_day(endpoint: str, granularity: int, max_candles: int, day: date, destination: Path) -> dict[str, Any]:
    start_dt = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(days=1)
    chunk = timedelta(seconds=granularity * max_candles)
    cursor = start_dt
    candles: dict[int, list[Any]] = {}
    queries: list[dict[str, Any]] = []
    while cursor < end_dt:
        chunk_end = min(cursor + chunk, end_dt)
        params = {"granularity": granularity, "start": iso_z(cursor), "end": iso_z(chunk_end)}
        batch = request_json(endpoint, params)
        if isinstance(batch, dict) and batch.get("message"):
            raise RuntimeError(f"Coinbase API error for {day}: {batch['message']}")
        queries.append({"start": iso_z(cursor), "end": iso_z(chunk_end), "count_returned": len(batch)})
        for row in batch:
            timestamp = int(row[0])
            if int(start_dt.timestamp()) <= timestamp < int(end_dt.timestamp()):
                candles[timestamp] = row
        cursor = chunk_end

    ordered = [candles[key] for key in sorted(candles)]
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    temporary.write_text(json.dumps(ordered, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temporary, destination)
    expected = 86400 // granularity
    return {
        "date_utc": day.isoformat(),
        "source": "Coinbase Exchange candles",
        "endpoint": endpoint,
        "granularity_seconds": granularity,
        "candle_count": len(ordered),
        "expected_intervals": expected,
        "missing_no_tick_intervals": expected - len(ordered),
        "queries": queries,
        "local_path": str(destination),
        "sha256": sha256_file(destination),
        "size_bytes": destination.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    project_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--config", type=Path, default=project_root / "config" / "dataset.json")
    parser.add_argument("--data-root", type=Path, default=project_root / "data")
    parser.add_argument("--start", help="UTC start date (YYYY-MM-DD); defaults to config")
    parser.add_argument("--end", help="UTC inclusive end date (YYYY-MM-DD); defaults to config")
    args = parser.parse_args()

    with args.config.open(encoding="utf-8") as stream:
        config = json.load(stream)
    start = date.fromisoformat(args.start or config["start_date_utc"])
    end = date.fromisoformat(args.end or config["end_date_utc_inclusive"])
    if end < start:
        parser.error("--end must be on or after --start")

    run_started = utc_now()
    records: list[dict[str, Any]] = []
    kraken = config["kraken"]
    coinbase = config["coinbase"]
    for day in iter_dates(start, end):
        kraken_path = args.data_root / "raw" / "kraken" / "spot" / "trades" / "ATOMUSD" / f"ATOMUSD-trades-{day}.jsonl.gz"
        kraken_record = download_kraken_day(kraken["endpoint"], kraken["symbol"], kraken["page_size"], day, kraken_path)
        kraken_record["local_path"] = str(kraken_path.relative_to(project_root))
        records.append(kraken_record)
        print(f"Kraken   {day}: {kraken_record['trade_count']} trades")

        coinbase_path = args.data_root / "raw" / "coinbase" / "spot" / "candles" / "ATOM-USD" / "60s" / f"ATOM-USD-60s-{day}.json"
        coinbase_record = download_coinbase_day(
            coinbase["endpoint"], coinbase["granularity_seconds"], coinbase["max_candles_per_request"], day, coinbase_path
        )
        coinbase_record["local_path"] = str(coinbase_path.relative_to(project_root))
        records.append(coinbase_record)
        print(f"Coinbase {day}: {coinbase_record['candle_count']} candles")

    metadata_root = project_root / "metadata" / "runs"
    metadata_root.mkdir(parents=True, exist_ok=True)
    run_id = run_started.replace(":", "").replace("-", "").replace(".", "")
    manifest_path = metadata_root / f"{run_id}_comparison_markets.json"
    manifest = {
        "study_id": config["study_id"],
        "run_started_at_utc": run_started,
        "run_finished_at_utc": utc_now(),
        "start_date_utc": start.isoformat(),
        "end_date_utc_inclusive": end.isoformat(),
        "records": records,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
