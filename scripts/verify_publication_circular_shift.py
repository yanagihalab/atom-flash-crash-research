#!/usr/bin/env python3
"""Raw-dependent, independent Pearson/null check of the primary 5-minute result.

This deliberately does not import the analysis or its FFT implementation. It
reconstructs the input time series and directly evaluates every retained shift.
It is separate from the release's processed-data-only ``make verify`` contract.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
START = datetime(2025, 9, 10, tzinfo=timezone.utc)
EVENT_DAY = datetime(2025, 10, 10, tzinfo=timezone.utc)
END = datetime(2025, 10, 11, tzinfo=timezone.utc)
LAGS = list(range(-12, 13))
SEED = 20251010
PRIMARY_ID = "5m_{sample}_confirmed_exchange_in_atom_to_spot_range_stress"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def jsonl(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


def rebuild_primary_series(root: Path) -> tuple[np.ndarray, np.ndarray, list[Path]]:
    processed = root / "data" / "processed" / "cosmoshub"
    baseline_path = processed / "baseline_30d" / "baseline_5min_2025-09-10_2025-10-10.jsonl.gz"
    transfers_path = processed / "atom_transfers_2025-10-09_2025-10-12.jsonl.gz"
    labels_path = root / "metadata" / "exchange_address_labels.json"
    sources = [baseline_path, transfers_path, labels_path]
    baseline_count = int((EVENT_DAY - START).total_seconds() // 300)
    total_count = int((END - START).total_seconds() // 300)
    flow = np.full(total_count, np.nan)
    seen = set()
    for row in jsonl(baseline_path):
        offset = (utc(row["bucket_start_utc"]) - START).total_seconds()
        index = int(offset // 300)
        if offset % 300 or not 0 <= index < baseline_count or index in seen:
            raise ValueError("Invalid or duplicated baseline five-minute bucket")
        seen.add(index)
        flow[index] = float(row["confirmed_exchange_in_atom"])
    if seen != set(range(baseline_count)):
        raise ValueError("Baseline flow series is incomplete")
    labels = {row["address"] for row in json.loads(labels_path.read_text(encoding="utf-8"))["labels"]}
    event_uatom = [0] * (total_count - baseline_count)
    for row in jsonl(transfers_path):
        timestamp = utc(row["time_utc"])
        if EVENT_DAY <= timestamp < END and row["flow_class"] == "direct_bank" and row["recipient"] in labels:
            index = int((timestamp - EVENT_DAY).total_seconds() // 300)
            event_uatom[index] += int(row["amount_uatom"])
    flow[baseline_count:] = np.asarray(event_uatom, dtype=float) / 1_000_000
    if not np.isfinite(flow).all() or np.any(flow < 0):
        raise ValueError("Exchange inflows must be finite and nonnegative")

    # Each raw minute keeps its original UTC position, including any missing bin.
    minute_stress = np.full(total_count * 5, np.nan)
    minute_seen = set()
    start_us = int(START.timestamp()) * 1_000_000
    for day_offset in range((END - START).days):
        day = (START + timedelta(days=day_offset)).date().isoformat()
        path = root / "data" / "raw" / "binance" / "spot" / "daily" / "klines" / "ATOMUSDT" / "1m" / f"ATOMUSDT-1m-{day}.zip"
        sources.append(path)
        with zipfile.ZipFile(path) as archive:
            members = [name for name in archive.namelist() if name.endswith(".csv")]
            if len(members) != 1:
                raise ValueError(f"Expected one CSV in {path}")
            with archive.open(members[0]) as binary:
                for row in csv.reader(io.TextIOWrapper(binary, encoding="utf-8")):
                    if not row or row[0] == "open_time":
                        continue
                    raw_timestamp = int(row[0])
                    stamp_us = raw_timestamp if raw_timestamp >= 10**15 else raw_timestamp * (1000 if raw_timestamp >= 10**12 else 1_000_000)
                    offset = stamp_us - start_us
                    index = offset // 60_000_000
                    if offset % 60_000_000 or not 0 <= index < len(minute_stress) or index in minute_seen:
                        raise ValueError("Invalid or duplicated raw market minute")
                    minute_seen.add(index)
                    high, low = float(row[2]), float(row[3])
                    if high <= 0 or low <= 0 or not math.isfinite(high + low):
                        raise ValueError("Raw market high/low must be positive and finite")
                    minute_stress[index] = math.log(high / low)
    if len(minute_seen) != len(minute_stress):
        raise ValueError("Raw market minute coverage is incomplete")
    return np.log1p(flow), minute_stress.reshape(-1, 5).max(axis=1), sources


def remove_clock_medians(values: np.ndarray) -> np.ndarray:
    days = values.reshape(-1, 288)
    return (days - np.nanmedian(days, axis=0)[None, :]).reshape(-1)


def direct_shift_curve(x: np.ndarray, y: np.ndarray, shift: int, lags: list[int]) -> np.ndarray:
    """Direct finite-pair Pearson at y[(i + lag + shift) % n], no FFT."""
    length = len(x)
    doubled_y = np.concatenate((y, y))
    correlations = []
    for lag in lags:
        first, last = max(0, -lag), min(length, length - lag)
        left = x[first:last]
        right_first = (first + lag + shift) % length
        right = doubled_y[right_first : right_first + len(left)]
        valid = np.isfinite(left) & np.isfinite(right)
        a, b = left[valid], right[valid]
        if len(a) < 10:
            correlations.append(math.nan)
            continue
        a, b = a - np.mean(a), b - np.mean(b)
        denominator = math.sqrt(float(np.dot(a, a)) * float(np.dot(b, b)))
        correlations.append(float(np.dot(a, b)) / denominator if denominator else math.nan)
    return np.asarray(correlations)


def independent_result(
    x: np.ndarray, y: np.ndarray, sample: str, progress_every: int = 500
) -> dict[str, Any]:
    length = len(x)
    if x.ndim != 1 or y.ndim != 1 or length != len(y):
        raise ValueError("The two series must have matching time axes")
    observed_curve = direct_shift_curve(x, y, 0, LAGS)
    finite = np.isfinite(observed_curve)
    if not finite.any():
        raise ValueError("No observed lag has a valid Pearson correlation")
    best_index = int(np.argmax(np.where(finite, np.abs(observed_curve), -np.inf)))
    observed = float(observed_curve[best_index])
    eligible = np.arange(13, length - 12)
    shifts = eligible if len(eligible) <= 5000 else np.sort(np.random.default_rng(SEED).choice(eligible, size=5000, replace=False))
    if not len(shifts):
        raise ValueError("No eligible circular shifts")
    exceedances = 0
    closest_distance = math.inf
    started = time.perf_counter()
    for completed, shift in enumerate(shifts, start=1):
        curve = direct_shift_curve(x, y, int(shift), LAGS)
        values = np.abs(curve[np.isfinite(curve)])
        if not len(values):
            raise ValueError(f"Untestable null shift {shift}; it must not be silently discarded")
        maximum = float(values.max())
        exceedances += int(maximum >= abs(observed))
        closest_distance = min(closest_distance, abs(maximum - abs(observed)))
        if progress_every and completed % progress_every == 0:
            print(json.dumps({"sample": sample, "shifts_completed": completed, "shifts_total": len(shifts), "elapsed_seconds": round(time.perf_counter() - started, 3)}), flush=True)
    return {
        "analysis_id": PRIMARY_ID.format(sample=sample),
        "time_bin_count": length,
        "max_abs_correlation": observed,
        "max_abs_correlation_lag_minutes": LAGS[best_index] * 5,
        "lag_zero_correlation": float(observed_curve[LAGS.index(0)]),
        "circular_shift_count": len(shifts),
        "null_exceedance_count": exceedances,
        "circular_shift_max_lag_p": (1 + exceedances) / (1 + len(shifts)),
        "closest_null_distance_from_observed_abs_r": closest_distance,
        "shift_offsets_sha256": hashlib.sha256(",".join(str(int(value)) for value in shifts).encode("ascii")).hexdigest(),
        "elapsed_seconds": time.perf_counter() - started,
    }


def compare_reported(actual: dict[str, Any], reported: dict[str, Any]) -> list[dict[str, Any]]:
    differences = []
    for key in ("max_abs_correlation", "max_abs_correlation_lag_minutes", "lag_zero_correlation", "circular_shift_count", "circular_shift_max_lag_p"):
        expected = float(actual[key])
        try:
            value = float(reported[key])
        except (KeyError, TypeError, ValueError):
            value = math.nan
        tolerance = 1e-15 if key.endswith("_p") else 1e-12
        if not math.isclose(expected, value, rel_tol=0, abs_tol=tolerance):
            differences.append({"field": key, "independent": expected, "reported": value if math.isfinite(value) else None})
    return differences


def self_test() -> None:
    rng = np.random.default_rng(817)
    x, y = rng.normal(size=(2, 48))
    x[[0, 5, 20]], y[[2, 9, 30]] = np.nan, np.inf
    maximum_error = 0.0
    for shift in (0, 13, 24, 35):
        curve = direct_shift_curve(x, y, shift, LAGS)
        for lag, actual in zip(LAGS, curve):
            pairs = [(float(x[i]), float(y[(i + lag + shift) % len(y)])) for i in range(max(0, -lag), min(len(x), len(x) - lag)) if math.isfinite(x[i]) and math.isfinite(y[(i + lag + shift) % len(y)])]
            left, right = zip(*pairs)
            left_mean, right_mean = math.fsum(left) / len(left), math.fsum(right) / len(right)
            covariance = math.fsum((a - left_mean) * (b - right_mean) for a, b in pairs)
            variance_x = math.fsum((a - left_mean) ** 2 for a in left)
            variance_y = math.fsum((b - right_mean) ** 2 for b in right)
            expected = covariance / math.sqrt(variance_x * variance_y)
            maximum_error = max(maximum_error, abs(actual - expected))
            assert math.isclose(actual, expected, rel_tol=0, abs_tol=2e-12)
    local = independent_result(x, y, "event_local_4h", progress_every=0)
    assert local["circular_shift_count"] == 23
    current = {"max_abs_correlation": 0.027352189855455556, "max_abs_correlation_lag_minutes": -60, "lag_zero_correlation": 0.015228144456182499, "circular_shift_count": 5000, "circular_shift_max_lag_p": 0.060187962407518496}
    assert not compare_reported(current, dict(current))
    stale = {**current, "circular_shift_max_lag_p": 0.0619876024795041}
    differences = compare_reported(current, stale)
    assert len(differences) == 1 and differences[0]["field"] == "circular_shift_max_lag_p"
    print(json.dumps({"self_test": "PASS", "checks": ["explicit_index_fsum_Pearson", "missing_positions_preserved", "local_23_shifts", "current_values_accepted", "stale_0.0620_rejected"], "maximum_formula_error": maximum_error}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--analysis", type=Path)
    parser.add_argument("--lead-lag", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--progress-every", type=int, default=500)
    parser.add_argument("--self-test", action="store_true", help="Test the verifier, including rejection of stale p values; no input files or artifacts")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    root = args.project_root.resolve()
    analysis_path = args.analysis or root / "results" / "publication_additional_analysis.json"
    lead_path = args.lead_lag or root / "results" / "publication_lead_lag.csv"
    output_path = args.output or root / "results" / "publication_circular_shift_verification.json"
    if output_path.resolve() in {analysis_path.resolve(), lead_path.resolve()}:
        raise ValueError("Verification output must not overwrite an analysis input")
    report: dict[str, Any] = {
        "study_id": "atom_flash_crash_2025_10_10",
        "verification_status": "FAIL",
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "method": "Independent raw reconstruction; explicitly indexed, finite-pair, lag-trimmed Pearson for observations and every circular shift; no analysis/FFT imports",
        "rng_seed": SEED,
        "lag_minutes": [lag * 5 for lag in LAGS],
        "scope": "Primary exact-match exchange inflows versus spot range stress, full 31-day sample and local 4-hour sample only",
        "checks": [],
    }
    try:
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        with lead_path.open(encoding="utf-8", newline="") as stream:
            csv_rows = list(csv.DictReader(stream))
        x, y, source_paths = rebuild_primary_series(root)
        source_paths += [analysis_path, lead_path, Path(__file__).resolve()]
        report["input_sha256"] = [{"path": str(path.resolve()), "sha256": sha256(path)} for path in source_paths]
        local_start = int(((EVENT_DAY + timedelta(hours=19, minutes=30)) - START).total_seconds() // 300)
        local_end = local_start + 48
        samples = (
            ("30d_plus_event_day", remove_clock_medians(x), remove_clock_medians(y)),
            ("event_local_4h", x[local_start:local_end], y[local_start:local_end]),
        )
        for name, left, right in samples:
            actual = independent_result(left, right, name, args.progress_every)
            json_matches = [row for row in analysis["lead_lag"]["summaries"] if row["analysis_id"] == actual["analysis_id"]]
            csv_matches = [row for row in csv_rows if row["analysis_id"] == actual["analysis_id"]]
            if len(json_matches) != 1 or len(csv_matches) != 1:
                raise ValueError(f"Expected one JSON and one CSV row for {actual['analysis_id']}")
            differences = {"json": compare_reported(actual, json_matches[0]), "csv": compare_reported(actual, csv_matches[0])}
            report["checks"].append({"analysis_id": actual["analysis_id"], "passed": not any(differences.values()), "independent": actual, "reported_json": json_matches[0], "reported_csv": csv_matches[0], "differences": differences})
        if all(row["passed"] for row in report["checks"]):
            report["verification_status"] = "PASS"
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output_path), "status": report["verification_status"], "checks": len(report["checks"])}), flush=True)
    if report["verification_status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
