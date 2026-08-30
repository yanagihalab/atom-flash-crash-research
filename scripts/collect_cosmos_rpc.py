#!/usr/bin/env python3
"""Collect UTC-partitioned Cosmos Hub blocks and block results from an archive RPC."""

from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import hashlib
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


USER_AGENT = "atom-flash-crash-research/1.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def rpc_json(rpc: str, method: str, params: dict[str, Any] | None = None, attempts: int = 4) -> dict[str, Any]:
    url = f"{rpc.rstrip('/')}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.load(response)
            if payload.get("error"):
                raise RuntimeError(f"RPC error for {url}: {payload['error']}")
            return payload
        except OSError:
            if attempt == attempts:
                raise
            time.sleep(2 ** (attempt - 1))
    raise AssertionError("unreachable")


def rpc_batch(
    rpc: str, heights: list[int], include_block_results: bool, attempts: int = 6
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    requests: list[dict[str, Any]] = []
    for height in heights:
        requests.append({"jsonrpc": "2.0", "id": f"block-{height}", "method": "block", "params": {"height": str(height)}})
        if include_block_results:
            requests.append(
                {"jsonrpc": "2.0", "id": f"block_results-{height}", "method": "block_results", "params": {"height": str(height)}}
            )
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
            if not isinstance(payload, list):
                raise RuntimeError(f"batch response from {rpc} is not a list")
            by_id = {item.get("id"): item for item in payload}
            blocks: list[dict[str, Any]] = []
            results: list[dict[str, Any]] = []
            for height in heights:
                block = by_id.get(f"block-{height}")
                if not block or block.get("error"):
                    raise RuntimeError(f"missing/error block {height} from {rpc}: {block}")
                blocks.append(block)
                if include_block_results:
                    result = by_id.get(f"block_results-{height}")
                    if not result or result.get("error"):
                        raise RuntimeError(f"missing/error block_results {height} from {rpc}: {result}")
                    results.append(result)
            return blocks, results
        except (OSError, urllib.error.HTTPError, RuntimeError) as error:
            if attempt == attempts:
                raise RuntimeError(f"batch failed after {attempts} attempts: {rpc}, heights {heights[0]}-{heights[-1]}") from error
            retry_after = 0
            if isinstance(error, urllib.error.HTTPError):
                try:
                    retry_after = int(error.headers.get("Retry-After", "0"))
                except ValueError:
                    retry_after = 0
            time.sleep(max(retry_after, min(30, 2 ** (attempt - 1))))
    raise AssertionError("unreachable")


def block_time(rpc: str, height: int) -> datetime:
    payload = rpc_json(rpc, "block", {"height": height})
    return parse_time(payload["result"]["block"]["header"]["time"])


def first_height_at_or_after(rpc: str, target: datetime, low: int, high: int) -> int:
    if block_time(rpc, low) >= target:
        return low
    if block_time(rpc, high) < target:
        return high + 1
    while low < high:
        middle = (low + high) // 2
        if block_time(rpc, middle) < target:
            low = middle + 1
        else:
            high = middle
    return low


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_rpc_range_batched(
    rpcs: list[str],
    first_height: int,
    end_height_exclusive: int,
    block_path: Path,
    results_path: Path | None,
    batch_heights: int,
    workers: int,
) -> tuple[int, list[dict[str, Any]]]:
    block_path.parent.mkdir(parents=True, exist_ok=True)
    block_temporary = block_path.with_name(block_path.name + ".part")
    results_temporary = results_path.with_name(results_path.name + ".part") if results_path else None
    chunks = [list(range(start, min(start + batch_heights, end_height_exclusive))) for start in range(first_height, end_height_exclusive, batch_heights)]

    def fetch(rpc: str, index: int, heights: list[int]) -> tuple[int, str, list[dict[str, Any]], list[dict[str, Any]]]:
        blocks, results = rpc_batch(rpc, heights, results_path is not None)
        return index, rpc, blocks, results

    count = 0
    batch_sources: list[dict[str, Any]] = []
    with block_temporary.open("wb") as block_raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=block_raw, mtime=0) as block_compressed:
            with io.TextIOWrapper(block_compressed, encoding="utf-8", newline="\n") as block_text:
                if results_temporary:
                    results_raw = results_temporary.open("wb")
                    results_compressed = gzip.GzipFile(filename="", mode="wb", fileobj=results_raw, mtime=0)
                    results_text = io.TextIOWrapper(results_compressed, encoding="utf-8", newline="\n")
                else:
                    results_raw = results_compressed = results_text = None
                try:
                    tasks = list(enumerate(chunks))
                    active_rpcs = rpcs[: min(workers, len(rpcs))]
                    next_submit = 0
                    next_write = 0
                    buffered: dict[int, tuple[str, list[dict[str, Any]], list[dict[str, Any]]]] = {}
                    with concurrent.futures.ThreadPoolExecutor(max_workers=len(active_rpcs)) as executor:
                        inflight: dict[
                            concurrent.futures.Future[tuple[int, str, list[dict[str, Any]], list[dict[str, Any]]]], str
                        ] = {}
                        for rpc in active_rpcs:
                            if next_submit >= len(tasks):
                                break
                            index, heights = tasks[next_submit]
                            future = executor.submit(fetch, rpc, index, heights)
                            inflight[future] = rpc
                            next_submit += 1
                        while inflight:
                            done, _ = concurrent.futures.wait(inflight, return_when=concurrent.futures.FIRST_COMPLETED)
                            for future in done:
                                assigned_rpc = inflight.pop(future)
                                index, source_rpc, blocks, results = future.result()
                                buffered[index] = (source_rpc, blocks, results)
                                if next_submit < len(tasks):
                                    new_index, new_heights = tasks[next_submit]
                                    new_future = executor.submit(fetch, assigned_rpc, new_index, new_heights)
                                    inflight[new_future] = assigned_rpc
                                    next_submit += 1
                            while next_write in buffered:
                                source_rpc, blocks, results = buffered.pop(next_write)
                                for payload in blocks:
                                    block_text.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
                                if results_text:
                                    for payload in results:
                                        results_text.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
                                count += len(blocks)
                                batch_sources.append(
                                    {
                                        "batch_index": next_write,
                                        "first_height": int(blocks[0]["result"]["block"]["header"]["height"]),
                                        "last_height": int(blocks[-1]["result"]["block"]["header"]["height"]),
                                        "rpc": source_rpc,
                                    }
                                )
                                next_write += 1
                                if count // 1000 != (count - len(blocks)) // 1000:
                                    print(f"{block_path.parent.name}: {count}/{end_height_exclusive - first_height} heights", flush=True)
                finally:
                    if results_text:
                        results_text.close()
                    if results_compressed and not results_compressed.closed:
                        results_compressed.close()
                    if results_raw and not results_raw.closed:
                        results_raw.close()
    os.replace(block_temporary, block_path)
    if results_temporary and results_path:
        os.replace(results_temporary, results_path)
    return count, batch_sources


def iter_dates(start: date, end_inclusive: date) -> Iterable[date]:
    current = start
    while current <= end_inclusive:
        yield current
        current += timedelta(days=1)


def main() -> int:
    parser = argparse.ArgumentParser()
    project_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--rpc", action="append", dest="rpcs", help="Archive RPC URL; repeat to distribute batches")
    parser.add_argument("--expected-chain-id", default="cosmoshub-4")
    parser.add_argument("--start", default="2025-10-09", help="UTC start date, inclusive")
    parser.add_argument("--end", default="2025-10-12", help="UTC end date, inclusive")
    parser.add_argument("--output-root", type=Path, default=project_root / "data" / "raw" / "cosmoshub")
    parser.add_argument("--skip-block-results", action="store_true")
    parser.add_argument("--batch-heights", type=int, default=5, help="Heights per mixed block/results batch; public servers cap batches at 10 requests")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--cross-check-height", type=int, default=27908328)
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end < start:
        parser.error("--end must be on or after --start")

    rpcs = args.rpcs or ["http://127.0.0.1:36657"]
    endpoint_records = []
    endpoint_statuses = []
    for rpc in rpcs:
        status_payload = rpc_json(rpc, "status")
        endpoint_statuses.append(status_payload)
        endpoint_node = status_payload["result"]["node_info"]
        endpoint_sync = status_payload["result"]["sync_info"]
        if endpoint_node["network"] != args.expected_chain_id:
            raise SystemExit(f"refusing collection: expected chain-id {args.expected_chain_id!r}, received {endpoint_node['network']!r} from {rpc}")
        endpoint_records.append(
            {
                "rpc": rpc,
                "chain_id": endpoint_node["network"],
                "node_version": endpoint_node.get("version"),
                "earliest_block_height": int(endpoint_sync.get("earliest_block_height") or 1),
                "latest_block_height": int(endpoint_sync["latest_block_height"]),
            }
        )
    block_hashes = set()
    results_hashes = set()
    for endpoint_record in endpoint_records:
        rpc = endpoint_record["rpc"]
        sample_block = rpc_json(rpc, "block", {"height": args.cross_check_height})
        sample_results = rpc_json(rpc, "block_results", {"height": args.cross_check_height})
        endpoint_record["cross_check_height"] = args.cross_check_height
        endpoint_record["cross_check_block_hash"] = sample_block["result"]["block_id"]["hash"]
        endpoint_record["cross_check_block_results_sha256"] = sha256_json(sample_results["result"])
        block_hashes.add(endpoint_record["cross_check_block_hash"])
        results_hashes.add(endpoint_record["cross_check_block_results_sha256"])
    if len(block_hashes) != 1 or len(results_hashes) != 1:
        raise SystemExit(f"archive RPC cross-check failed at height {args.cross_check_height}")
    status = endpoint_statuses[0]
    node_info = status["result"]["node_info"]
    sync_info = status["result"]["sync_info"]
    chain_id = node_info["network"]

    earliest_height = int(sync_info.get("earliest_block_height") or 1)
    latest_height = int(sync_info["latest_block_height"])
    requested_start = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    requested_end = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    earliest_time = block_time(rpcs[0], earliest_height)
    latest_time = block_time(rpcs[0], latest_height)
    if requested_start < earliest_time or requested_end > latest_time:
        raise SystemExit(
            "archive range unavailable: "
            f"node covers {earliest_time.isoformat()} through {latest_time.isoformat()}, "
            f"requested [{requested_start.isoformat()}, {requested_end.isoformat()})"
        )

    run_started = utc_now()
    records: list[dict[str, Any]] = []
    search_low = earliest_height
    for day in iter_dates(start, end):
        day_start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        first_height = first_height_at_or_after(rpcs[0], day_start, search_low, latest_height)
        end_height = first_height_at_or_after(rpcs[0], day_end, first_height, latest_height)
        search_low = first_height
        day_root = args.output_root / "cosmoshub-4" / day.isoformat()
        block_path = day_root / "blocks.jsonl.gz"
        results_path = day_root / "block_results.jsonl.gz"
        partition_manifest_path = day_root / "partition_manifest.json"
        if partition_manifest_path.exists():
            existing = json.loads(partition_manifest_path.read_text(encoding="utf-8"))
            expected_files = [block_path] + ([] if args.skip_block_results else [results_path])
            manifest_keys = {block_path: "blocks", results_path: "block_results"}
            hashes_ok = all(
                path.exists()
                and manifest_keys[path] in existing
                and sha256_file(path) == existing[manifest_keys[path]]["sha256"]
                for path in expected_files
            )
            if existing.get("first_height") == first_height and existing.get("end_height_exclusive") == end_height and hashes_ok:
                print(f"verified_existing {day}", flush=True)
                records.append(existing)
                continue
        block_count, batch_sources = write_rpc_range_batched(
            rpcs,
            first_height,
            end_height,
            block_path,
            None if args.skip_block_results else results_path,
            args.batch_heights,
            args.workers,
        )
        item: dict[str, Any] = {
            "date_utc": day.isoformat(),
            "first_height": first_height,
            "end_height_exclusive": end_height,
            "block_count": block_count,
            "blocks": {"local_path": block_path.relative_to(project_root).as_posix(), "sha256": sha256_file(block_path), "size_bytes": block_path.stat().st_size},
        }
        if not args.skip_block_results:
            item["block_results_count"] = block_count
            item["block_results"] = {
                "local_path": results_path.relative_to(project_root).as_posix(),
                "sha256": sha256_file(results_path),
                "size_bytes": results_path.stat().st_size,
            }
        item["batch_provenance"] = {
            "rpc_endpoints": rpcs,
            "assignment": "one sequential worker per endpoint; next unassigned batch goes to the first available endpoint",
            "batch_heights": args.batch_heights,
            "workers": args.workers,
            "batches": batch_sources,
        }
        partition_manifest_path.write_text(json.dumps(item, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        records.append(item)

    metadata_root = project_root / "metadata" / "runs"
    metadata_root.mkdir(parents=True, exist_ok=True)
    run_id = run_started.replace(":", "").replace("-", "").replace(".", "")
    manifest_path = metadata_root / f"{run_id}_cosmoshub.json"
    manifest = {
        "study_id": "atom_flash_crash_2025_10_10",
        "run_started_at_utc": run_started,
        "run_finished_at_utc": utc_now(),
        "rpc_endpoints": endpoint_records,
        "expected_chain_id": args.expected_chain_id,
        "observed_chain_id": chain_id,
        "node_version": node_info.get("version"),
        "node_earliest_height": earliest_height,
        "node_latest_height": latest_height,
        "batch_heights": args.batch_heights,
        "workers": args.workers,
        "records": records,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
