#!/usr/bin/env python3
"""Publication-oriented 30-day window tests, robustness checks, and lead-lag analysis."""

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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW = PROJECT_ROOT / "data" / "raw"
PROCESSED = PROJECT_ROOT / "data" / "processed" / "cosmoshub"
RESULTS = PROJECT_ROOT / "results"
BASELINE_START = datetime(2025, 9, 10, tzinfo=timezone.utc)
BASELINE_END = datetime(2025, 10, 10, tzinfo=timezone.utc)
EVENT_DAY_END = datetime(2025, 10, 11, tzinfo=timezone.utc)
EVENT_START = datetime(2025, 10, 10, 20, 30, tzinfo=timezone.utc)
EVENT_END = datetime(2025, 10, 10, 22, 30, tzinfo=timezone.utc)
LEAD_LAG_LOCAL_START = datetime(2025, 10, 10, 19, 30, tzinfo=timezone.utc)
LEAD_LAG_LOCAL_END = datetime(2025, 10, 10, 23, 30, tzinfo=timezone.utc)
RNG_SEED = 20251010


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


def sha256_paths(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(PROJECT_ROOT)).encode("utf-8") + b"\0")
        digest.update(sha256_file(path).encode("ascii") + b"\n")
    return digest.hexdigest()


def epoch_to_us(value: str | int) -> int:
    number = int(value)
    if number >= 10**15:
        return number
    if number >= 10**12:
        return number * 1000
    return number * 1_000_000


def read_binance_klines(path: Path) -> list[dict[str, float | int]]:
    rows: list[dict[str, float | int]] = []
    with zipfile.ZipFile(path) as archive, archive.open(archive.namelist()[0]) as binary:
        for raw in csv.reader(io.TextIOWrapper(binary, encoding="utf-8")):
            if not raw or raw[0] == "open_time":
                continue
            rows.append(
                {
                    "time_us": epoch_to_us(raw[0]),
                    "open": float(raw[1]),
                    "high": float(raw[2]),
                    "low": float(raw[3]),
                    "close": float(raw[4]),
                    "base_volume": float(raw[5]),
                    "quote_volume": float(raw[7]),
                    "trade_count": int(raw[8]),
                }
            )
    rows.sort(key=lambda row: int(row["time_us"]))
    return rows


def read_coinbase(path: Path) -> list[dict[str, float | int]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = [
        {
            "time_us": int(row[0]) * 1_000_000,
            "low": float(row[1]),
            "high": float(row[2]),
            "open": float(row[3]),
            "close": float(row[4]),
            "base_volume": float(row[5]),
            "quote_volume": 0.0,
            "trade_count": 0,
        }
        for row in payload
    ]
    rows.sort(key=lambda row: int(row["time_us"]))
    return rows


def market_paths(day: date) -> dict[str, Path]:
    stamp = day.isoformat()
    return {
        "spot_usdt": RAW / "binance" / "spot" / "daily" / "klines" / "ATOMUSDT" / "1m" / f"ATOMUSDT-1m-{stamp}.zip",
        "spot_usdc": RAW / "binance" / "spot" / "daily" / "klines" / "ATOMUSDC" / "1m" / f"ATOMUSDC-1m-{stamp}.zip",
        "futures_usdt": RAW / "binance" / "futures" / "um" / "daily" / "klines" / "ATOMUSDT" / "1m" / f"ATOMUSDT-1m-{stamp}.zip",
        "mark_usdt": RAW / "binance" / "futures" / "um" / "daily" / "markPriceKlines" / "ATOMUSDT" / "1m" / f"ATOMUSDT-1m-{stamp}.zip",
        "coinbase_usd": RAW / "coinbase" / "spot" / "candles" / "ATOM-USD" / "60s" / f"ATOM-USD-60s-{stamp}.json",
    }


def load_market(start: date, end_inclusive: date) -> tuple[dict[str, dict[int, dict[str, float | int]]], list[Path]]:
    market: dict[str, dict[int, dict[str, float | int]]] = defaultdict(dict)
    inputs: list[Path] = []
    day = start
    while day <= end_inclusive:
        for venue, path in market_paths(day).items():
            if not path.exists():
                raise FileNotFoundError(path)
            inputs.append(path)
            rows = read_coinbase(path) if venue == "coinbase_usd" else read_binance_klines(path)
            for row in rows:
                market[venue][int(row["time_us"])] = row
        day += timedelta(days=1)
    return dict(market), inputs


def load_baseline_chain(path: Path) -> tuple[dict[datetime, dict[str, float | int]], dict[str, str]]:
    rows: dict[datetime, dict[str, float | int]] = {}
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            rows[parse_time(row["bucket_start_utc"])] = row
    expected = int((BASELINE_END - BASELINE_START).total_seconds() // 300)
    if len(rows) != expected or min(rows) != BASELINE_START or max(rows) != BASELINE_END - timedelta(minutes=5):
        raise RuntimeError("30-day chain baseline is incomplete")
    return rows, {"path": path.relative_to(PROJECT_ROOT).as_posix(), "sha256": sha256_file(path)}


def empty_chain_bucket(timestamp: datetime) -> dict[str, float | int]:
    names = (
        "confirmed_exchange_in",
        "confirmed_exchange_out",
        "unconfirmed_behavioral_in",
        "large_flow_watchlist_in",
        "ibc_inbound",
        "ibc_outbound",
    )
    row: dict[str, float | int] = {"bucket_start_utc": iso(timestamp)}
    for name in names:
        row[f"{name}_count"] = 0
        row[f"{name}_atom"] = 0.0
    row["confirmed_exchange_net_in_atom"] = 0.0
    row["all_behavioral_candidate_in_atom"] = 0.0
    row["ibc_net_inbound_atom"] = 0.0
    return row


def load_event_day_chain() -> tuple[dict[datetime, dict[str, float | int]], list[Path]]:
    atom_path = PROCESSED / "atom_transfers_2025-10-09_2025-10-12.jsonl.gz"
    ibc_path = PROCESSED / "ibc_transfers_2025-10-09_2025-10-12.jsonl.gz"
    candidates_path = PROCESSED / "exchange_inflow_candidates_2025-10-09_2025-10-12.json"
    labels_path = PROJECT_ROOT / "metadata" / "exchange_address_labels.json"
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))["candidates"]
    behavioral = {
        row["address"] for row in candidates if row["candidate_tier"] in {"behavioral_high", "behavioral_medium"}
    }
    watchlist = {row["address"] for row in candidates if row["candidate_tier"] == "large_flow_watchlist"}
    labels = {row["address"] for row in json.loads(labels_path.read_text(encoding="utf-8"))["labels"]}
    rows = {
        datetime(2025, 10, 10, tzinfo=timezone.utc) + timedelta(minutes=5 * index): empty_chain_bucket(
            datetime(2025, 10, 10, tzinfo=timezone.utc) + timedelta(minutes=5 * index)
        )
        for index in range(288)
    }

    def add(timestamp: datetime, metric: str, amount_uatom: int) -> None:
        bucket = timestamp.replace(minute=(timestamp.minute // 5) * 5, second=0, microsecond=0)
        if bucket not in rows:
            return
        rows[bucket][f"{metric}_count"] = int(rows[bucket][f"{metric}_count"]) + 1
        rows[bucket][f"{metric}_atom"] = float(rows[bucket][f"{metric}_atom"]) + amount_uatom / 1_000_000

    with gzip.open(atom_path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            timestamp = parse_time(row["time_utc"])
            if timestamp.date() != date(2025, 10, 10) or row["flow_class"] != "direct_bank":
                continue
            if row["recipient"] in labels:
                add(timestamp, "confirmed_exchange_in", int(row["amount_uatom"]))
            if row["sender"] in labels:
                add(timestamp, "confirmed_exchange_out", int(row["amount_uatom"]))
            if row["recipient"] in behavioral:
                add(timestamp, "unconfirmed_behavioral_in", int(row["amount_uatom"]))
            if row["recipient"] in watchlist:
                add(timestamp, "large_flow_watchlist_in", int(row["amount_uatom"]))
    with gzip.open(ibc_path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            timestamp = parse_time(row["time_utc"])
            if timestamp.date() != date(2025, 10, 10) or not row["is_atom"]:
                continue
            metric = "ibc_inbound" if row["direction"] == "inbound" else "ibc_outbound"
            add(timestamp, metric, int(row["amount_base_units"]))
    for row in rows.values():
        row["confirmed_exchange_net_in_atom"] = float(row["confirmed_exchange_in_atom"]) - float(
            row["confirmed_exchange_out_atom"]
        )
        row["all_behavioral_candidate_in_atom"] = float(row["confirmed_exchange_in_atom"]) + float(
            row["unconfirmed_behavioral_in_atom"]
        )
        row["ibc_net_inbound_atom"] = float(row["ibc_inbound_atom"]) - float(row["ibc_outbound_atom"])
    return rows, [atom_path, ibc_path, candidates_path, labels_path]


def aggregate_chain_window(
    chain: dict[datetime, dict[str, float | int]], start: datetime, end: datetime
) -> dict[str, float]:
    selected = [row for timestamp, row in chain.items() if start <= timestamp < end]
    if len(selected) != int((end - start).total_seconds() // 300):
        raise RuntimeError(f"incomplete chain window {iso(start)} to {iso(end)}")
    sums = {
        name: sum(float(row[f"{name}_atom"]) for row in selected)
        for name in (
            "confirmed_exchange_in",
            "confirmed_exchange_out",
            "unconfirmed_behavioral_in",
            "ibc_inbound",
            "ibc_outbound",
        )
    }
    return {
        **{f"{name}_atom": value for name, value in sums.items()},
        "confirmed_exchange_gross_atom": sums["confirmed_exchange_in"] + sums["confirmed_exchange_out"],
        "confirmed_exchange_net_in_atom": sums["confirmed_exchange_in"] - sums["confirmed_exchange_out"],
        "all_behavioral_candidate_in_atom": sums["confirmed_exchange_in"] + sums["unconfirmed_behavioral_in"],
        "ibc_gross_atom": sums["ibc_inbound"] + sums["ibc_outbound"],
        "ibc_net_inbound_atom": sums["ibc_inbound"] - sums["ibc_outbound"],
    }


def aggregate_market_window(
    market: dict[str, dict[int, dict[str, float | int]]], start: datetime, end: datetime
) -> dict[str, float]:
    start_us = int(start.timestamp() * 1_000_000)
    end_us = int(end.timestamp() * 1_000_000)
    spot = [market["spot_usdt"][stamp] for stamp in sorted(market["spot_usdt"]) if start_us <= stamp < end_us]
    expected = int((end - start).total_seconds() // 60)
    if len(spot) != expected:
        raise RuntimeError(f"incomplete Binance spot window {iso(start)}: {len(spot)} != {expected}")
    closes = np.asarray([float(row["close"]) for row in spot], dtype=float)
    realized_abs = float(np.abs(np.diff(np.log(closes))).sum() * 100) if len(closes) > 1 else 0.0
    result = {
        "binance_usdt_range_pct_of_open": (max(float(row["high"]) for row in spot) - min(float(row["low"]) for row in spot))
        / float(spot[0]["open"])
        * 100,
        "binance_usdt_base_volume_atom": sum(float(row["base_volume"]) for row in spot),
        "binance_usdt_quote_volume": sum(float(row["quote_volume"]) for row in spot),
        "binance_usdt_trade_count": sum(float(row["trade_count"]) for row in spot),
        "binance_usdt_realized_abs_return_pct": realized_abs,
    }
    for venue, label in (
        ("spot_usdc", "usdc"),
        ("mark_usdt", "mark"),
        ("coinbase_usd", "coinbase"),
    ):
        ratios: list[float] = []
        for stamp, numerator in market["spot_usdt"].items():
            if start_us <= stamp < end_us and stamp in market[venue]:
                denominator = float(market[venue][stamp]["low"])
                if denominator > 0:
                    ratios.append(float(numerator["low"]) / denominator)
        result[f"max_usdt_vs_{label}_low_discount_pct"] = (1 - min(ratios)) * 100 if ratios else math.nan
    return result


def empirical_test(event: float, controls: list[float], direction: str) -> dict[str, float | int | str | None]:
    if not controls or not math.isfinite(event) or any(not math.isfinite(value) for value in controls):
        return {"event_value": event, "control_count": len(controls), "test_direction": direction}
    ordered = sorted(controls)
    median = statistics.median(controls)
    percentile = 100 * sum(value <= event for value in controls) / len(controls)
    if direction == "upper":
        exceedances = sum(value >= event for value in controls)
    elif direction == "lower":
        exceedances = sum(value <= event for value in controls)
    elif direction == "two_sided_median":
        event_distance = abs(event - median)
        exceedances = sum(abs(value - median) >= event_distance for value in controls)
    else:
        raise ValueError(direction)
    return {
        "event_value": event,
        "control_count": len(controls),
        "control_mean": statistics.fmean(controls),
        "control_median": median,
        "control_p05_empirical": ordered[max(0, math.ceil(0.05 * len(ordered)) - 1)],
        "control_p95_empirical": ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)],
        "event_percentile_empirical": percentile,
        "test_direction": direction,
        "permutation_exceedance_count": exceedances,
        "permutation_p_exact_plus_one": (exceedances + 1) / (len(controls) + 1),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value) if math.isfinite(float(value)) else None
    return value


def minute_array(
    venue: dict[int, dict[str, float | int]], field: str, times: list[datetime]
) -> np.ndarray:
    values = []
    for timestamp in times:
        row = venue.get(int(timestamp.timestamp() * 1_000_000))
        values.append(float(row[field]) if row is not None else math.nan)
    return np.asarray(values, dtype=float)


def signed_returns(closes: np.ndarray) -> np.ndarray:
    result = np.full(len(closes), np.nan)
    valid = (closes[1:] > 0) & (closes[:-1] > 0)
    calculated = np.full(len(closes) - 1, np.nan)
    calculated[valid] = np.log(closes[1:][valid] / closes[:-1][valid])
    result[1:] = calculated
    return result


def remove_slot_median(values: np.ndarray, slots_per_day: int) -> np.ndarray:
    output = values.copy()
    for slot in range(slots_per_day):
        indices = np.arange(slot, len(values), slots_per_day)
        finite = np.isfinite(values[indices])
        if finite.any():
            output[indices[finite]] -= np.median(values[indices[finite]])
    return output


def lag_correlation(x: np.ndarray, y: np.ndarray, lag: int) -> tuple[float, int]:
    if lag > 0:
        left, right = x[:-lag], y[lag:]
    elif lag < 0:
        left, right = x[-lag:], y[:lag]
    else:
        left, right = x, y
    valid = np.isfinite(left) & np.isfinite(right)
    left, right = left[valid], right[valid]
    if len(left) < 10 or np.std(left) == 0 or np.std(right) == 0:
        return math.nan, len(left)
    return float(np.corrcoef(left, right)[0, 1]), len(left)


def circular_shift_pvalue(x: np.ndarray, y: np.ndarray, lags: list[int], observed: float) -> tuple[float, int]:
    valid = np.isfinite(x) & np.isfinite(y)
    x, y = x[valid], y[valid]
    if len(x) < 30 or np.std(x) == 0 or np.std(y) == 0 or not math.isfinite(observed):
        return math.nan, 0
    x = (x - np.mean(x)) / np.std(x)
    y = (y - np.mean(y)) / np.std(y)
    circular = np.fft.ifft(np.conj(np.fft.fft(x)) * np.fft.fft(y)).real / len(x)
    max_lag = max(abs(lag) for lag in lags)
    available = np.arange(max_lag + 1, len(x) - max_lag)
    rng = np.random.default_rng(RNG_SEED)
    shifts = available if len(available) <= 5000 else np.sort(rng.choice(available, size=5000, replace=False))
    lag_array = np.asarray(lags, dtype=int)
    null = np.asarray([np.max(np.abs(circular[(shift + lag_array) % len(x)])) for shift in shifts])
    return float((1 + np.sum(null >= abs(observed))) / (len(null) + 1)), int(len(null))


def lead_lag_result(
    analysis_id: str,
    resolution: str,
    sample: str,
    x_name: str,
    y_name: str,
    x: np.ndarray,
    y: np.ndarray,
    lags: list[int],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    curve = []
    for lag in lags:
        correlation, count = lag_correlation(x, y, lag)
        curve.append(
            {
                "analysis_id": analysis_id,
                "resolution": resolution,
                "sample": sample,
                "x": x_name,
                "y": y_name,
                "lag_intervals": lag,
                "lag_minutes": lag * (1 if resolution == "1m" else 5),
                "correlation": correlation,
                "paired_observations": count,
            }
        )
    finite = [row for row in curve if math.isfinite(float(row["correlation"]))]
    best = max(finite, key=lambda row: abs(float(row["correlation"])))
    lag_zero = next(row for row in curve if row["lag_intervals"] == 0)
    p_value, permutation_count = circular_shift_pvalue(
        x, y, lags, float(best["correlation"])
    )
    summary = {
        "analysis_id": analysis_id,
        "resolution": resolution,
        "sample": sample,
        "x": x_name,
        "y": y_name,
        "lag_convention": "positive lag means x leads y",
        "max_abs_correlation_lag_minutes": best["lag_minutes"],
        "max_abs_correlation": best["correlation"],
        "lag_zero_correlation": lag_zero["correlation"],
        "paired_observations_at_best_lag": best["paired_observations"],
        "circular_shift_max_lag_p": p_value,
        "circular_shift_count": permutation_count,
    }
    return summary, curve


def build_lead_lag(
    market: dict[str, dict[int, dict[str, float | int]]],
    chain: dict[datetime, dict[str, float | int]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    minute_times = [BASELINE_START + timedelta(minutes=index) for index in range(int((EVENT_DAY_END - BASELINE_START).total_seconds() // 60))]
    close_usdt = minute_array(market["spot_usdt"], "close", minute_times)
    close_usdc = minute_array(market["spot_usdc"], "close", minute_times)
    close_futures = minute_array(market["futures_usdt"], "close", minute_times)
    close_mark = minute_array(market["mark_usdt"], "close", minute_times)
    low_usdt = minute_array(market["spot_usdt"], "low", minute_times)
    low_usdc = minute_array(market["spot_usdc"], "low", minute_times)
    high_usdt = minute_array(market["spot_usdt"], "high", minute_times)
    trade_count = minute_array(market["spot_usdt"], "trade_count", minute_times)
    ret_usdt = signed_returns(close_usdt)
    ret_usdc = signed_returns(close_usdc)
    ret_futures = signed_returns(close_futures)
    ret_mark = signed_returns(close_mark)
    range_stress = np.log(high_usdt / low_usdt)
    trade_activity = np.log1p(trade_count)
    usdt_discount = np.maximum(0.0, np.log(low_usdc / low_usdt))
    one_minute_variables = {
        "spot_usdt_return": ret_usdt,
        "spot_usdc_return": ret_usdc,
        "futures_return": ret_futures,
        "mark_return": ret_mark,
        "spot_range_stress": range_stress,
        "spot_trade_activity": trade_activity,
        "usdt_vs_usdc_low_discount": usdt_discount,
    }
    one_minute_pairs = [
        ("spot_usdt_return", "spot_usdc_return"),
        ("futures_return", "spot_usdt_return"),
        ("spot_usdt_return", "mark_return"),
        ("spot_trade_activity", "spot_range_stress"),
        ("usdt_vs_usdc_low_discount", "spot_range_stress"),
    ]
    local_start = int((LEAD_LAG_LOCAL_START - BASELINE_START).total_seconds() // 60)
    local_end = int((LEAD_LAG_LOCAL_END - BASELINE_START).total_seconds() // 60)
    summaries: list[dict[str, Any]] = []
    curves: list[dict[str, Any]] = []
    for sample, selector, deseason in (
        ("30d_plus_event_day", slice(None), True),
        ("event_local_4h", slice(local_start, local_end), False),
    ):
        for x_name, y_name in one_minute_pairs:
            x = one_minute_variables[x_name][selector]
            y = one_minute_variables[y_name][selector]
            if deseason:
                x, y = remove_slot_median(x, 1440), remove_slot_median(y, 1440)
            analysis_id = f"1m_{sample}_{x_name}_to_{y_name}"
            summary, curve = lead_lag_result(analysis_id, "1m", sample, x_name, y_name, x, y, list(range(-5, 6)))
            summaries.append(summary)
            curves.extend(curve)

    five_times = [BASELINE_START + timedelta(minutes=5 * index) for index in range(int((EVENT_DAY_END - BASELINE_START).total_seconds() // 300))]
    five_stress = np.asarray(
        [np.nanmax(range_stress[index * 5 : index * 5 + 5]) for index in range(len(five_times))], dtype=float
    )
    five_return = np.asarray(
        [np.nansum(ret_usdt[index * 5 : index * 5 + 5]) for index in range(len(five_times))], dtype=float
    )
    chain_variables: dict[str, np.ndarray] = {}
    for metric in (
        "confirmed_exchange_in_atom",
        "confirmed_exchange_out_atom",
        "unconfirmed_behavioral_in_atom",
        "all_behavioral_candidate_in_atom",
        "ibc_inbound_atom",
        "ibc_outbound_atom",
    ):
        chain_variables[metric] = np.log1p(np.asarray([float(chain[timestamp][metric]) for timestamp in five_times]))
    for metric in ("confirmed_exchange_net_in_atom", "ibc_net_inbound_atom"):
        raw = np.asarray([float(chain[timestamp][metric]) for timestamp in five_times])
        nonzero = np.abs(raw[raw != 0])
        scale = float(np.median(nonzero)) if len(nonzero) else 1.0
        chain_variables[metric] = np.arcsinh(raw / scale)
    five_pairs = [
        ("confirmed_exchange_in_atom", "spot_range_stress", five_stress),
        ("confirmed_exchange_out_atom", "spot_range_stress", five_stress),
        ("unconfirmed_behavioral_in_atom", "spot_range_stress", five_stress),
        ("all_behavioral_candidate_in_atom", "spot_range_stress", five_stress),
        ("confirmed_exchange_net_in_atom", "spot_return", five_return),
        ("ibc_inbound_atom", "spot_range_stress", five_stress),
        ("ibc_outbound_atom", "spot_range_stress", five_stress),
        ("ibc_net_inbound_atom", "spot_return", five_return),
    ]
    local_five_start = int((LEAD_LAG_LOCAL_START - BASELINE_START).total_seconds() // 300)
    local_five_end = int((LEAD_LAG_LOCAL_END - BASELINE_START).total_seconds() // 300)
    for sample, selector, deseason in (
        ("30d_plus_event_day", slice(None), True),
        ("event_local_4h", slice(local_five_start, local_five_end), False),
    ):
        for x_name, y_name, y_full in five_pairs:
            x, y = chain_variables[x_name][selector], y_full[selector]
            if deseason:
                x, y = remove_slot_median(x, 288), remove_slot_median(y, 288)
            analysis_id = f"5m_{sample}_{x_name}_to_{y_name}"
            summary, curve = lead_lag_result(analysis_id, "5m", sample, x_name, y_name, x, y, list(range(-12, 13)))
            summaries.append(summary)
            curves.extend(curve)
    return summaries, curves


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--baseline-series",
        type=Path,
        default=PROCESSED / "baseline_30d" / "baseline_5min_2025-09-10_2025-10-10.jsonl.gz",
    )
    parser.add_argument("--output-root", type=Path, default=RESULTS)
    args = parser.parse_args()

    baseline_chain, baseline_provenance = load_baseline_chain(args.baseline_series)
    event_chain, event_inputs = load_event_day_chain()
    combined_chain = {**baseline_chain, **event_chain}
    market, market_inputs = load_market(BASELINE_START.date(), EVENT_DAY_END.date() - timedelta(days=1))

    control_windows = []
    for day_offset in range(30):
        day_start = BASELINE_START + timedelta(days=day_offset)
        for block in range(12):
            start = day_start + timedelta(hours=2 * block)
            control_windows.append((start, start + timedelta(hours=2)))
    matched_windows = [
        (BASELINE_START + timedelta(days=day_offset, hours=20, minutes=30), BASELINE_START + timedelta(days=day_offset, hours=22, minutes=30))
        for day_offset in range(30)
    ]
    event_chain_metrics = aggregate_chain_window(combined_chain, EVENT_START, EVENT_END)
    event_market_metrics = aggregate_market_window(market, EVENT_START, EVENT_END)
    event_metrics = {**event_chain_metrics, **event_market_metrics}
    control_metrics = [
        {
            **aggregate_chain_window(baseline_chain, start, end),
            **aggregate_market_window(market, start, end),
            "start_utc": iso(start),
            "end_utc": iso(end),
        }
        for start, end in control_windows
    ]
    matched_metrics = [
        {
            **aggregate_chain_window(baseline_chain, start, end),
            **aggregate_market_window(market, start, end),
            "start_utc": iso(start),
            "end_utc": iso(end),
        }
        for start, end in matched_windows
    ]
    directions = {
        "confirmed_exchange_in_atom": "upper",
        "confirmed_exchange_out_atom": "upper",
        "confirmed_exchange_gross_atom": "upper",
        "confirmed_exchange_net_in_atom": "two_sided_median",
        "unconfirmed_behavioral_in_atom": "upper",
        "all_behavioral_candidate_in_atom": "upper",
        "ibc_inbound_atom": "upper",
        "ibc_outbound_atom": "upper",
        "ibc_gross_atom": "upper",
        "ibc_net_inbound_atom": "two_sided_median",
        "binance_usdt_range_pct_of_open": "upper",
        "binance_usdt_base_volume_atom": "upper",
        "binance_usdt_quote_volume": "upper",
        "binance_usdt_trade_count": "upper",
        "binance_usdt_realized_abs_return_pct": "upper",
        "max_usdt_vs_usdc_low_discount_pct": "upper",
        "max_usdt_vs_mark_low_discount_pct": "upper",
        "max_usdt_vs_coinbase_low_discount_pct": "upper",
    }
    test_rows: list[dict[str, Any]] = []
    tests_nested: dict[str, dict[str, Any]] = {}
    for metric, direction in directions.items():
        group = "chain" if metric in event_chain_metrics else "market"
        tests_nested[metric] = {}
        for control_set, rows in (("all_360", control_metrics), ("matched_clock_30", matched_metrics)):
            test = empirical_test(float(event_metrics[metric]), [float(row[metric]) for row in rows], direction)
            tests_nested[metric][control_set] = test
            test_rows.append({"metric_group": group, "metric": metric, "control_set": control_set, **test})

    lead_lag_summaries, lead_lag_curves = build_lead_lag(market, combined_chain)
    primary_test = tests_nested["confirmed_exchange_in_atom"]
    sensitivity_test = tests_nested["all_behavioral_candidate_in_atom"]
    robustness = {
        "confirmed_only_event_inflow_atom": event_chain_metrics["confirmed_exchange_in_atom"],
        "unconfirmed_behavioral_event_inflow_atom": event_chain_metrics["unconfirmed_behavioral_in_atom"],
        "confirmed_plus_unconfirmed_event_inflow_atom": event_chain_metrics["all_behavioral_candidate_in_atom"],
        "unconfirmed_share_of_combined_event_inflow": (
            event_chain_metrics["unconfirmed_behavioral_in_atom"]
            / event_chain_metrics["all_behavioral_candidate_in_atom"]
            if event_chain_metrics["all_behavioral_candidate_in_atom"]
            else None
        ),
        "confirmed_only_window_tests": primary_test,
        "confirmed_plus_unconfirmed_window_tests": sensitivity_test,
        "interpretation": "Confirmed public-label addresses are primary; unconfirmed behavioral candidates are sensitivity-only, and the large-flow watchlist is excluded from both exchange-flow definitions.",
    }

    args.output_root.mkdir(parents=True, exist_ok=True)
    tests_path = args.output_root / "publication_window_tests.csv"
    lead_path = args.output_root / "publication_lead_lag.csv"
    curves_path = args.output_root / "publication_lead_lag_curves.csv"
    controls_path = args.output_root / "publication_control_windows.csv"
    matched_path = args.output_root / "publication_matched_clock_windows.csv"
    write_csv(tests_path, test_rows)
    write_csv(lead_path, lead_lag_summaries)
    write_csv(curves_path, lead_lag_curves)
    write_csv(controls_path, control_metrics)
    write_csv(matched_path, matched_metrics)
    result = {
        "study_id": "atom_flash_crash_2025_10_10",
        "generated_at_utc": iso(datetime.now(timezone.utc)),
        "event_window": {"start_utc": iso(EVENT_START), "end_utc": iso(EVENT_END)},
        "baseline_window": {"start_utc": iso(BASELINE_START), "end_utc_exclusive": iso(BASELINE_END), "days": 30},
        "control_design": {
            "primary": "360 non-overlapping two-hour windows: 12 UTC-aligned windows on each of 30 pre-event days",
            "matched_clock_sensitivity": "30 windows at 20:30-22:30 UTC on each pre-event day",
            "serial_dependence_caveat": "The rank/randomization p-values require exchangeability and are descriptive because adjacent crypto-market windows may be serially dependent.",
        },
        "event_metrics": event_metrics,
        "window_tests": tests_nested,
        "control_windows": control_metrics,
        "matched_clock_windows": matched_metrics,
        "robustness": robustness,
        "lead_lag": {
            "lag_convention": "positive lag means x leads y",
            "full_sample": "2025-09-10 through 2025-10-10 UTC (30 pre-event days plus event day)",
            "event_local_sample": "2025-10-10 19:30-23:30 UTC",
            "transformations": "Nonnegative activity/flow variables use log1p; signed nets use asinh scaled by the median nonzero absolute value; full-sample variables are de-seasoned by UTC minute/5-minute slot median.",
            "significance": "Circular-shift max-|correlation| test across all reported lags, deterministic seed 20251010.",
            "summaries": lead_lag_summaries,
            "curves": lead_lag_curves,
        },
        "order_book_limitation": {
            "historical_binance_order_book_snapshots_in_acquired_data": False,
            "permitted_claim": "The executed-trade and cross-venue pattern is consistent with a pair-specific liquidity or market-microstructure disruption.",
            "prohibited_claim": "These data prove liquidity depletion/exhaustion.",
            "reason": "Without historical depth snapshots, the study cannot reconstruct resting depth, cancellations, queue age, or the state of the Binance ATOM/USDT book immediately before the print.",
        },
        "source_provenance": {
            "baseline_chain": baseline_provenance,
            "event_chain_combined_sha256": sha256_paths(event_inputs),
            "market_file_count": len(market_inputs),
            "market_files_combined_sha256": sha256_paths(market_inputs),
        },
        "outputs": {
            "window_tests_csv": tests_path.relative_to(PROJECT_ROOT).as_posix(),
            "control_windows_csv": controls_path.relative_to(PROJECT_ROOT).as_posix(),
            "matched_clock_windows_csv": matched_path.relative_to(PROJECT_ROOT).as_posix(),
            "lead_lag_csv": lead_path.relative_to(PROJECT_ROOT).as_posix(),
            "lead_lag_curves_csv": curves_path.relative_to(PROJECT_ROOT).as_posix(),
        },
    }
    output_path = args.output_root / "publication_additional_analysis.json"
    output_path.write_text(
        json.dumps(json_safe(result), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(output_path), "tests": len(test_rows), "lead_lag": len(lead_lag_summaries)}, indent=2))


if __name__ == "__main__":
    main()
