#!/usr/bin/env python3
"""Independently verify the acquired Cosmos Hub research dataset."""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def jsonl_gzip(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise AssertionError(f"invalid JSON in {path}:{line_number}: {exc}") from exc


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def verify_artifact(path: Path, record: dict[str, Any]) -> None:
    assert path.is_file(), f"missing artifact: {path}"
    assert path.stat().st_size == record["size_bytes"], f"size mismatch: {path}"
    assert sha256_file(path) == record["sha256"], f"SHA-256 mismatch: {path}"


def verify_daily_partition(
    partition: dict[str, Any],
    event_first: int,
    event_end: int,
) -> tuple[dict[str, Any], dict[int, list[dict[str, Any]]]]:
    date_utc = datetime.fromisoformat(partition["date_utc"]).replace(tzinfo=timezone.utc)
    next_date = date_utc + timedelta(days=1)
    first_height = partition["first_height"]
    end_height = partition["end_height_exclusive"]
    expected_heights = end_height - first_height
    assert expected_heights == partition["height_count"]

    meta_path = Path(partition["block_metas"]["local_path"])
    tx_path = Path(partition["tx_search"]["local_path"])
    verify_artifact(meta_path, partition["block_metas"])
    verify_artifact(tx_path, partition["tx_search"])

    meta_count = 0
    block_transaction_counts: list[int] = []
    first_time: str | None = None
    last_time: str | None = None
    for expected_height, row in zip(range(first_height, end_height), jsonl_gzip(meta_path), strict=True):
        meta = row["block_meta"]
        header = meta["header"]
        height = int(header["height"])
        assert height == expected_height, f"non-contiguous block metadata at {height}"
        assert header["chain_id"] == "cosmoshub-4", f"wrong chain ID at {height}"
        timestamp = parse_timestamp(header["time"])
        assert date_utc <= timestamp < next_date, f"block {height} outside daily UTC partition"
        first_time = first_time or header["time"]
        last_time = header["time"]
        block_transaction_counts.append(int(meta["num_txs"]))
        meta_count += 1
    assert meta_count == expected_heights

    expected_height = first_height
    expected_page = 1
    expected_pages_for_height = 1
    total_for_height: int | None = None
    transaction_count = 0
    page_record_count = 0
    event_txs: dict[int, list[dict[str, Any]]] = defaultdict(list)

    for row in jsonl_gzip(tx_path):
        height = int(row["height"])
        page = int(row["page"])
        assert height == expected_height, f"missing or out-of-order tx height: expected {expected_height}, got {height}"
        assert page == expected_page, f"missing or out-of-order page at height {height}"

        result = row["response"]["result"]
        total_count = int(result["total_count"])
        txs = result.get("txs") or []
        if page == 1:
            total_for_height = total_count
            expected_pages_for_height = max(1, math.ceil(total_count / 100))
        else:
            assert total_count == total_for_height, f"total_count changed across pages at {height}"

        for tx in txs:
            assert int(tx["height"]) == height
            raw_tx = base64.b64decode(tx["tx"], validate=True)
            assert hashlib.sha256(raw_tx).hexdigest().upper() == tx["hash"], f"Tx hash mismatch at {height}"
            if event_first <= height < event_end:
                event_txs[height].append(tx)

        transaction_count += len(txs)
        page_record_count += 1
        if page == expected_pages_for_height:
            assert total_for_height == block_transaction_counts[height - first_height], (
                f"block metadata/tx_search count mismatch at {height}"
            )
            expected_height += 1
            expected_page = 1
            total_for_height = None
        else:
            expected_page += 1

    assert expected_height == end_height, f"tx height coverage ended at {expected_height}, expected {end_height}"
    assert expected_page == 1
    assert transaction_count == partition["transaction_count"]
    assert page_record_count == partition["tx_page_record_count"]

    return (
        {
            "date_utc": partition["date_utc"],
            "first_height": first_height,
            "end_height_exclusive": end_height,
            "height_count": meta_count,
            "transaction_count": transaction_count,
            "tx_page_record_count": page_record_count,
            "first_block_time": first_time,
            "last_block_time": last_time,
            "block_metas_sha256": partition["block_metas"]["sha256"],
            "tx_search_sha256": partition["tx_search"]["sha256"],
        },
        dict(event_txs),
    )


def verify_event_window(
    event: dict[str, Any],
    event_txs: dict[int, list[dict[str, Any]]],
) -> dict[str, Any]:
    first_height = event["first_height"]
    end_height = event["end_height_exclusive"]
    assert end_height - first_height == event["height_count"]
    blocks_path = Path(event["blocks"]["local_path"])
    results_path = Path(event["block_results"]["local_path"])
    verify_artifact(blocks_path, event["blocks"])
    verify_artifact(results_path, event["block_results"])

    window_text = event["window"]
    start_text, end_text = window_text.removeprefix("[").removesuffix(")").split(", ")
    window_start = parse_timestamp(start_text)
    window_end = parse_timestamp(end_text)

    block_count = 0
    first_time: str | None = None
    last_time: str | None = None
    block_txs: dict[int, list[str]] = {}
    block_ids: dict[int, str] = {}
    for expected_height, row in zip(range(first_height, end_height), jsonl_gzip(blocks_path), strict=True):
        result = row["result"]
        header = result["block"]["header"]
        height = int(header["height"])
        assert height == expected_height
        assert header["chain_id"] == "cosmoshub-4"
        timestamp = parse_timestamp(header["time"])
        assert window_start <= timestamp < window_end
        raw_txs = result["block"]["data"].get("txs") or []
        indexed_txs = event_txs.get(height, [])
        assert raw_txs == [tx["tx"] for tx in indexed_txs], f"block/tx_search Tx mismatch at {height}"
        block_txs[height] = raw_txs
        block_ids[height] = result["block_id"]["hash"]
        first_time = first_time or header["time"]
        last_time = header["time"]
        block_count += 1
    assert block_count == event["height_count"]

    results_count = 0
    for expected_height, row in zip(range(first_height, end_height), jsonl_gzip(results_path), strict=True):
        result = row["result"]
        height = int(result["height"])
        assert height == expected_height
        tx_results = result.get("txs_results") or []
        indexed_txs = event_txs.get(height, [])
        assert len(tx_results) == len(block_txs[height])
        assert tx_results == [tx["tx_result"] for tx in indexed_txs], f"block_results/tx_search mismatch at {height}"
        results_count += 1
    assert results_count == event["height_count"]

    cross_check_height = 27908328
    assert first_height <= cross_check_height < end_height
    cross_check_txs = event_txs.get(cross_check_height, [])
    return {
        "window": event["window"],
        "first_height": first_height,
        "end_height_exclusive": end_height,
        "height_count": block_count,
        "first_block_time": first_time,
        "last_block_time": last_time,
        "blocks_sha256": event["blocks"]["sha256"],
        "block_results_sha256": event["block_results"]["sha256"],
        "cross_check_height": cross_check_height,
        "cross_check_block_id": block_ids[cross_check_height],
        "cross_check_transaction_count": len(cross_check_txs),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-manifest",
        type=Path,
        default=PROJECT_ROOT / "metadata" / "runs" / "20260829T043153730115Z_cosmoshub_study_data.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "results" / "cosmoshub_data_verification.json",
    )
    args = parser.parse_args()

    run_manifest = json.loads(args.run_manifest.read_text(encoding="utf-8"))
    assert run_manifest["chain_id"] == "cosmoshub-4"
    endpoints = run_manifest["endpoints"]
    assert len(endpoints) >= 2
    assert len({item["chain_id"] for item in endpoints}) == 1
    assert len({item["cross_check_block_hash"] for item in endpoints}) == 1
    assert len({item["cross_check_block_results_sha256"] for item in endpoints}) == 1

    event = run_manifest["event_window"]
    event_first = event["first_height"]
    event_end = event["end_height_exclusive"]
    daily_results = []
    event_txs: dict[int, list[dict[str, Any]]] = {}
    previous_end: int | None = None
    for partition in run_manifest["daily_partitions"]:
        if previous_end is not None:
            assert partition["first_height"] == previous_end, "gap or overlap between daily partitions"
        result, partition_event_txs = verify_daily_partition(partition, event_first, event_end)
        daily_results.append(result)
        event_txs.update(partition_event_txs)
        previous_end = partition["end_height_exclusive"]

    event_result = verify_event_window(event, event_txs)
    report = {
        "verification_status": "passed",
        "verified_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "run_manifest": str(args.run_manifest),
        "run_manifest_sha256": sha256_file(args.run_manifest),
        "chain_id": run_manifest["chain_id"],
        "endpoint_count": len(endpoints),
        "cross_endpoint_block_hash_match": True,
        "cross_endpoint_block_results_match": True,
        "daily_partitions": daily_results,
        "totals": {
            "height_count": sum(item["height_count"] for item in daily_results),
            "transaction_count": sum(item["transaction_count"] for item in daily_results),
            "tx_page_record_count": sum(item["tx_page_record_count"] for item in daily_results),
        },
        "event_window": event_result,
        "checks": [
            "artifact sizes and SHA-256 values match the acquisition manifest",
            "all gzip streams and JSONL records are readable",
            "daily block heights are contiguous and timestamps fall inside their UTC partitions",
            "tx_search has complete height and pagination coverage",
            "every block metadata num_txs value equals the complete tx_search count for that height",
            "each indexed transaction raw-byte SHA-256 equals its reported hash",
            "event-window block transactions match tx_search raw transactions in order",
            "event-window block_results match tx_search execution results in order",
            "all archive endpoints agree on the cross-check block and block_results",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
