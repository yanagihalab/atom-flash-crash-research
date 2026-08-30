#!/usr/bin/env python3
"""Download and verify immutable Binance public-data archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


USER_AGENT = "atom-flash-crash-research/1.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def fetch(url: str, destination: Path, attempts: int = 4) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
            os.replace(partial, destination)
            return
        except (OSError, urllib.error.URLError) as error:
            partial.unlink(missing_ok=True)
            if attempt == attempts:
                raise RuntimeError(f"download failed after {attempts} attempts: {url}") from error
            time.sleep(2 ** (attempt - 1))


def archive_relative_path(dataset: dict[str, Any], symbol: str, day: date) -> Path:
    market = dataset["market"]
    data_type = dataset["data_type"]
    interval = dataset.get("interval")
    filename_parts = [symbol]
    if interval:
        filename_parts.append(interval)
    else:
        filename_parts.append(data_type)
    filename_parts.append(day.isoformat())
    filename = "-".join(filename_parts) + ".zip"
    pieces = [market, "daily", data_type, symbol]
    if interval:
        pieces.append(interval)
    return Path(*pieces, filename)


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def main() -> int:
    parser = argparse.ArgumentParser()
    project_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--config", type=Path, default=project_root / "config" / "dataset.json")
    parser.add_argument("--data-root", type=Path, default=project_root / "data")
    parser.add_argument("--start", help="UTC start date (YYYY-MM-DD); defaults to config")
    parser.add_argument("--end", help="UTC inclusive end date (YYYY-MM-DD); defaults to config")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    start = date.fromisoformat(args.start or config["start_date_utc"])
    end = date.fromisoformat(args.end or config["end_date_utc_inclusive"])
    if end < start:
        parser.error("--end must be on or after --start")

    base_url = config["binance_base_url"].rstrip("/")
    raw_root = args.data_root / "raw" / "binance"
    run_started = utc_now()
    records: list[dict[str, Any]] = []

    for dataset in config["binance_datasets"]:
        for symbol in dataset["symbols"]:
            for day in iter_dates(start, end):
                relative = archive_relative_path(dataset, symbol, day)
                url = f"{base_url}/{relative.as_posix()}"
                checksum_url = url + ".CHECKSUM"
                destination = raw_root / relative
                checksum_path = destination.with_name(destination.name + ".CHECKSUM")
                record: dict[str, Any] = {
                    "study_id": config["study_id"],
                    "source": "Binance Public Data",
                    "market": dataset["market"],
                    "data_type": dataset["data_type"],
                    "interval": dataset.get("interval"),
                    "symbol": symbol,
                    "date_utc": day.isoformat(),
                    "url": url,
                    "checksum_url": checksum_url,
                    "local_path": str(destination.relative_to(project_root)),
                }
                if args.dry_run:
                    record["status"] = "planned"
                    records.append(record)
                    print(url)
                    continue

                if not checksum_path.exists():
                    fetch(checksum_url, checksum_path)
                expected = checksum_path.read_text(encoding="utf-8").split()[0].lower()
                if len(expected) != 64:
                    raise RuntimeError(f"invalid checksum file: {checksum_path}")

                if destination.exists():
                    actual = sha256_file(destination)
                    if actual != expected:
                        raise RuntimeError(f"existing archive checksum mismatch: {destination}")
                    status = "verified_existing"
                else:
                    fetch(url, destination)
                    actual = sha256_file(destination)
                    if actual != expected:
                        destination.unlink(missing_ok=True)
                        raise RuntimeError(f"downloaded archive checksum mismatch: {url}")
                    status = "downloaded_verified"

                record.update(
                    {
                        "status": status,
                        "expected_sha256": expected,
                        "actual_sha256": actual,
                        "size_bytes": destination.stat().st_size,
                        "verified_at_utc": utc_now(),
                    }
                )
                records.append(record)
                print(f"{status:20s} {relative}")

    metadata_root = project_root / "metadata" / "runs"
    metadata_root.mkdir(parents=True, exist_ok=True)
    run_id = run_started.replace(":", "").replace("-", "").replace(".", "")
    manifest_path = metadata_root / f"{run_id}_binance.json"
    payload = {
        "study_id": config["study_id"],
        "run_started_at_utc": run_started,
        "run_finished_at_utc": utc_now(),
        "config_path": str(args.config),
        "start_date_utc": start.isoformat(),
        "end_date_utc_inclusive": end.isoformat(),
        "dry_run": args.dry_run,
        "file_count": len(records),
        "files": records,
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
