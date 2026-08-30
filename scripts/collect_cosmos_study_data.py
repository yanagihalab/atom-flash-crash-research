#!/usr/bin/env python3
"""Collect paper-oriented Cosmos Hub metadata, indexed transactions, and event-window raw RPC data."""

from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from collect_cosmos_rpc import (
    block_time,
    first_height_at_or_after,
    rpc_json,
    sha256_file,
    sha256_json,
    utc_now,
    write_rpc_range_batched,
)


USER_AGENT = "atom-flash-crash-research/1.0"


def post_batch(rpc: str, requests: list[dict[str, Any]], attempts: int = 6) -> list[dict[str, Any]]:
    if len(requests) > 10:
        raise ValueError("public CometBFT endpoints allow at most 10 JSON-RPC requests per batch")
    body = json.dumps(requests, separators=(",", ":")).encode("utf-8")
    url = rpc.rstrip("/") + "/"
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url,
            data=body,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.load(response)
            if isinstance(payload, dict) and len(requests) == 1 and payload.get("result") is not None:
                payload = [payload]
            if not isinstance(payload, list):
                raise RuntimeError(f"non-list batch response: {payload}")
            if any(item.get("error") for item in payload):
                raise RuntimeError(f"JSON-RPC error in batch: {[item.get('error') for item in payload if item.get('error')]}")
            return payload
        except (OSError, urllib.error.HTTPError, RuntimeError) as error:
            if attempt == attempts:
                raise RuntimeError(f"batch failed after {attempts} attempts: {rpc}") from error
            retry_after = 0
            if isinstance(error, urllib.error.HTTPError):
                try:
                    retry_after = int(error.headers.get("Retry-After", "0"))
                except ValueError:
                    retry_after = 0
            time.sleep(max(retry_after, min(30, 2 ** (attempt - 1))))
    raise AssertionError("unreachable")


def run_tasks(
    tasks: list[dict[str, Any]],
    rpcs: list[str],
    workers: int,
    output_path: Path,
    convert: Callable[[dict[str, Any], list[dict[str, Any]], str], tuple[list[dict[str, Any]], dict[str, Any]]],
    progress_label: str,
) -> tuple[int, list[dict[str, Any]], Counter[str]]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".part")
    active_rpcs = rpcs[: min(workers, len(rpcs))]
    next_submit = 0
    next_write = 0
    record_count = 0
    summaries: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    buffered: dict[int, tuple[str, list[dict[str, Any]], dict[str, Any]]] = {}

    def fetch(rpc: str, task: dict[str, Any]) -> tuple[int, str, list[dict[str, Any]], dict[str, Any]]:
        responses = post_batch(rpc, task["requests"])
        records, summary = convert(task, responses, rpc)
        return task["index"], rpc, records, summary

    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="\n") as text:
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(active_rpcs)) as executor:
                    inflight: dict[concurrent.futures.Future[Any], str] = {}
                    for rpc in active_rpcs:
                        if next_submit >= len(tasks):
                            break
                        future = executor.submit(fetch, rpc, tasks[next_submit])
                        inflight[future] = rpc
                        next_submit += 1
                    while inflight:
                        done, _ = concurrent.futures.wait(inflight, return_when=concurrent.futures.FIRST_COMPLETED)
                        for future in done:
                            assigned_rpc = inflight.pop(future)
                            index, source_rpc, records, summary = future.result()
                            buffered[index] = (source_rpc, records, summary)
                            if next_submit < len(tasks):
                                new_future = executor.submit(fetch, assigned_rpc, tasks[next_submit])
                                inflight[new_future] = assigned_rpc
                                next_submit += 1
                        while next_write in buffered:
                            source_rpc, records, summary = buffered.pop(next_write)
                            for record in records:
                                text.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                            record_count += len(records)
                            source_counts[source_rpc] += 1
                            summaries.append(summary)
                            next_write += 1
                            if next_write % 100 == 0 or next_write == len(tasks):
                                print(f"{progress_label}: {next_write}/{len(tasks)} batches", flush=True)
    os.replace(temporary, output_path)
    return record_count, summaries, source_counts


def make_meta_tasks(first_height: int, end_height: int) -> list[dict[str, Any]]:
    ranges = [(start, min(start + 19, end_height - 1)) for start in range(first_height, end_height, 20)]
    tasks = []
    for index, offset in enumerate(range(0, len(ranges), 10)):
        selected = ranges[offset : offset + 10]
        requests = [
            {
                "jsonrpc": "2.0",
                "id": f"meta-{low}-{high}",
                "method": "blockchain",
                "params": {"minHeight": str(low), "maxHeight": str(high)},
            }
            for low, high in selected
        ]
        tasks.append({"index": index, "requests": requests, "ranges": selected})
    return tasks


def convert_metas(task: dict[str, Any], responses: list[dict[str, Any]], rpc: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_id = {item["id"]: item for item in responses}
    records = []
    for low, high in task["ranges"]:
        response = by_id[f"meta-{low}-{high}"]
        for meta in response["result"]["block_metas"]:
            height = int(meta["header"]["height"])
            if low <= height <= high:
                records.append({"source_rpc": rpc, "query_min_height": low, "query_max_height": high, "block_meta": meta})
    records.sort(key=lambda item: int(item["block_meta"]["header"]["height"]))
    return records, {
        "batch_index": task["index"],
        "first_height": int(records[0]["block_meta"]["header"]["height"]),
        "last_height": int(records[-1]["block_meta"]["header"]["height"]),
        "record_count": len(records),
        "rpc": rpc,
    }


def make_tx_tasks(first_height: int, end_height: int) -> list[dict[str, Any]]:
    tasks = []
    for index, start in enumerate(range(first_height, end_height, 10)):
        heights = list(range(start, min(start + 10, end_height)))
        requests = [
            {
                "jsonrpc": "2.0",
                "id": f"tx-{height}-1",
                "method": "tx_search",
                "params": {"query": f"tx.height={height}", "prove": False, "page": "1", "per_page": "100", "order_by": "asc"},
            }
            for height in heights
        ]
        tasks.append({"index": index, "requests": requests, "heights": heights})
    return tasks


def convert_txs(task: dict[str, Any], responses: list[dict[str, Any]], rpc: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_id = {item["id"]: item for item in responses}
    records = []
    tx_count = 0
    for height in task["heights"]:
        first = by_id[f"tx-{height}-1"]
        total = int(first["result"]["total_count"])
        first_count = len(first["result"]["txs"] or [])
        tx_count += first_count
        records.append({"source_rpc": rpc, "height": height, "page": 1, "response": first})
        if total > 100:
            page_requests = [
                {
                    "jsonrpc": "2.0",
                    "id": f"tx-{height}-{page}",
                    "method": "tx_search",
                    "params": {"query": f"tx.height={height}", "prove": False, "page": str(page), "per_page": "100", "order_by": "asc"},
                }
                for page in range(2, (total + 99) // 100 + 1)
            ]
            for offset in range(0, len(page_requests), 10):
                extra = post_batch(rpc, page_requests[offset : offset + 10])
                for response in sorted(extra, key=lambda item: int(item["id"].rsplit("-", 1)[1])):
                    page = int(response["id"].rsplit("-", 1)[1])
                    tx_count += len(response["result"]["txs"] or [])
                    records.append({"source_rpc": rpc, "height": height, "page": page, "response": response})
    records.sort(key=lambda item: (item["height"], item["page"]))
    return records, {
        "batch_index": task["index"],
        "first_height": task["heights"][0],
        "last_height": task["heights"][-1],
        "height_count": len(task["heights"]),
        "tx_count": tx_count,
        "rpc": rpc,
    }


def validate_endpoints(rpcs: list[str], expected_chain_id: str, cross_check_height: int) -> list[dict[str, Any]]:
    records = []
    block_hashes = set()
    result_hashes = set()
    for rpc in rpcs:
        status = rpc_json(rpc, "status")["result"]
        chain_id = status["node_info"]["network"]
        if chain_id != expected_chain_id:
            raise SystemExit(f"expected {expected_chain_id}, received {chain_id} from {rpc}")
        block = rpc_json(rpc, "block", {"height": cross_check_height})
        results = rpc_json(rpc, "block_results", {"height": cross_check_height})
        block_hash = block["result"]["block_id"]["hash"]
        result_hash = sha256_json(results["result"])
        block_hashes.add(block_hash)
        result_hashes.add(result_hash)
        records.append(
            {
                "rpc": rpc,
                "chain_id": chain_id,
                "node_version": status["node_info"].get("version"),
                "earliest_block_height": int(status["sync_info"].get("earliest_block_height") or 1),
                "latest_block_height": int(status["sync_info"]["latest_block_height"]),
                "cross_check_height": cross_check_height,
                "cross_check_block_hash": block_hash,
                "cross_check_block_results_sha256": result_hash,
            }
        )
    if len(block_hashes) != 1 or len(result_hashes) != 1:
        raise SystemExit("archive endpoints disagree at the cross-check height")
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    project_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--rpc", action="append", dest="rpcs", required=True)
    parser.add_argument("--expected-chain-id", default="cosmoshub-4")
    parser.add_argument("--start", default="2025-10-09")
    parser.add_argument("--end", default="2025-10-12")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--cross-check-height", type=int, default=27908328)
    parser.add_argument("--event-start", default="2025-10-10T20:30:00Z")
    parser.add_argument("--event-end", default="2025-10-10T22:30:00Z")
    parser.add_argument("--output-root", type=Path, default=project_root / "data" / "raw" / "cosmoshub" / "cosmoshub-4")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end < start:
        parser.error("--end must be on or after --start")
    endpoint_records = validate_endpoints(args.rpcs, args.expected_chain_id, args.cross_check_height)
    primary = args.rpcs[0]
    earliest = endpoint_records[0]["earliest_block_height"]
    latest = endpoint_records[0]["latest_block_height"]
    run_started = utc_now()

    boundaries: dict[date, int] = {}
    low = earliest
    for day in [start + timedelta(days=offset) for offset in range((end - start).days + 2)]:
        target = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        height = first_height_at_or_after(primary, target, low, latest)
        boundaries[day] = height
        low = height
        print(f"boundary {day}: {height} {block_time(primary, height).isoformat()}", flush=True)

    partition_records = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        first_height = boundaries[day]
        end_height = boundaries[day + timedelta(days=1)]
        day_root = args.output_root / "daily" / day.isoformat()
        meta_path = day_root / "block_metas.jsonl.gz"
        tx_path = day_root / "tx_search.jsonl.gz"
        partition_path = day_root / "partition_manifest.json"
        if partition_path.exists():
            existing = json.loads(partition_path.read_text(encoding="utf-8"))
            if (
                existing.get("first_height") == first_height
                and existing.get("end_height_exclusive") == end_height
                and meta_path.exists()
                and tx_path.exists()
                and sha256_file(meta_path) == existing["block_metas"]["sha256"]
                and sha256_file(tx_path) == existing["tx_search"]["sha256"]
            ):
                print(f"verified_existing {day}", flush=True)
                partition_records.append(existing)
                continue

        meta_count, meta_summaries, meta_sources = run_tasks(
            make_meta_tasks(first_height, end_height), args.rpcs, args.workers, meta_path, convert_metas, f"{day} block metas"
        )
        expected_heights = end_height - first_height
        if meta_count != expected_heights:
            raise RuntimeError(f"block metadata count mismatch for {day}: {meta_count} != {expected_heights}")
        tx_record_count, tx_summaries, tx_sources = run_tasks(
            make_tx_tasks(first_height, end_height), args.rpcs, args.workers, tx_path, convert_txs, f"{day} tx search"
        )
        tx_count = sum(item["tx_count"] for item in tx_summaries)
        partition = {
            "date_utc": day.isoformat(),
            "first_height": first_height,
            "end_height_exclusive": end_height,
            "height_count": expected_heights,
            "transaction_count": tx_count,
            "tx_page_record_count": tx_record_count,
            "block_metas": {"local_path": meta_path.relative_to(project_root).as_posix(), "sha256": sha256_file(meta_path), "size_bytes": meta_path.stat().st_size},
            "tx_search": {"local_path": tx_path.relative_to(project_root).as_posix(), "sha256": sha256_file(tx_path), "size_bytes": tx_path.stat().st_size},
            "block_meta_source_batch_counts": dict(meta_sources),
            "tx_source_batch_counts": dict(tx_sources),
        }
        partition_path.write_text(json.dumps(partition, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        partition_records.append(partition)

    event_start = datetime.fromisoformat(args.event_start.replace("Z", "+00:00"))
    event_end = datetime.fromisoformat(args.event_end.replace("Z", "+00:00"))
    event_first = first_height_at_or_after(primary, event_start, boundaries[start], latest)
    event_end_height = first_height_at_or_after(primary, event_end, event_first, latest)
    event_root = args.output_root / "event_window" / "2025-10-10_2030-2230_utc"
    event_blocks = event_root / "blocks.jsonl.gz"
    event_results = event_root / "block_results.jsonl.gz"
    event_count, event_batches = write_rpc_range_batched(
        args.rpcs, event_first, event_end_height, event_blocks, event_results, batch_heights=5, workers=args.workers
    )
    event_record = {
        "window": f"[{args.event_start}, {args.event_end})",
        "first_height": event_first,
        "end_height_exclusive": event_end_height,
        "height_count": event_count,
        "blocks": {"local_path": event_blocks.relative_to(project_root).as_posix(), "sha256": sha256_file(event_blocks), "size_bytes": event_blocks.stat().st_size},
        "block_results": {"local_path": event_results.relative_to(project_root).as_posix(), "sha256": sha256_file(event_results), "size_bytes": event_results.stat().st_size},
        "batch_sources": event_batches,
    }
    event_root.mkdir(parents=True, exist_ok=True)
    (event_root / "partition_manifest.json").write_text(json.dumps(event_record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    metadata_root = project_root / "metadata" / "runs"
    metadata_root.mkdir(parents=True, exist_ok=True)
    run_id = run_started.replace(":", "").replace("-", "").replace(".", "")
    manifest_path = metadata_root / f"{run_id}_cosmoshub_study_data.json"
    manifest = {
        "study_id": "atom_flash_crash_2025_10_10",
        "run_started_at_utc": run_started,
        "run_finished_at_utc": utc_now(),
        "chain_id": args.expected_chain_id,
        "endpoints": endpoint_records,
        "daily_partitions": partition_records,
        "event_window": event_record,
        "limitations": [
            "Daily block_metas and tx_search preserve block headers and indexed transaction results, not consensus-only block events.",
            "Full block and block_results RPC responses are retained for the two-hour event window.",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
