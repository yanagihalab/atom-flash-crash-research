#!/usr/bin/env python3
"""Retain resumable raw inbound IBC searches for the fixed 30-day baseline.

Only two concurrent read-only RPC requests are allowed. ``estimate`` downloads
the first page of each of the 30 existing inbound queries; ``fetch`` resumes the
remaining pages. Original compact baseline files are never overwritten.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import gzip
import hashlib
import http.client
import json
import math
import statistics
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "data/processed/cosmoshub/baseline_30d"
DEFAULT_OUTPUT = ROOT / "data/raw/cosmoshub/cosmoshub-4/baseline_ibc_reaudit_2026-09-15"
DEFAULT_RPC = "https://rpc.cosmoshub-main.ccvalidators.com"
START = datetime(2025, 9, 10, tzinfo=timezone.utc)
END = datetime(2025, 10, 10, tzinfo=timezone.utc)
PAGE_SIZE = 100
TRANSPORT = threading.local()


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_sha(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".partial")
    pending.write_bytes(json.dumps(value, indent=2, ensure_ascii=False).encode() + b"\n")
    pending.replace(path)


def load_tasks() -> tuple[list[dict[str, Any]], dict[int, str], dict[str, Any]]:
    meta_path = BASELINE / "block_times_manifest.json"
    meta = json.loads(meta_path.read_text())
    index_path = BASELINE / "block_times_2025-09-10_2025-10-10.jsonl.gz"
    if file_sha(index_path) != meta["sha256"]:
        raise ValueError("Block-time index SHA-256 differs from retained manifest")
    times: dict[int, str] = {}
    first_by_day: dict[str, int] = {}
    last_timestamp = START
    expected_height = int(meta["first_height"])
    with gzip.open(index_path, "rt") as stream:
        for line in stream:
            row = json.loads(line)
            height = int(row["height"])
            stamp = datetime.fromisoformat(row["time_utc"].replace("Z", "+00:00"))
            if height != expected_height or not START <= stamp < END or stamp < last_timestamp:
                raise ValueError(f"Invalid index continuity/date/order at height {height}")
            times[height] = row["time_utc"]
            first_by_day.setdefault(stamp.date().isoformat(), height)
            expected_height += 1
            last_timestamp = stamp
    if len(times) != 438529 or len(times) != meta["record_count"] or expected_height != meta["end_height_exclusive"]:
        raise ValueError("Block-time index is not the audited 438,529-height baseline")
    old_manifest = json.loads((BASELINE / "baseline_indexed_manifest.json").read_text())
    old_queries = {row["label"]: row for row in old_manifest["query_summaries"]}
    tasks = []
    for offset in range(30):
        day = (START + timedelta(days=offset)).date().isoformat()
        next_day = (START + timedelta(days=offset + 1)).date().isoformat()
        first_height = first_by_day[day]
        end_height = first_by_day.get(next_day, int(meta["end_height_exclusive"]))
        query = f"recv_packet.packet_dst_port='transfer' AND tx.height>={first_height} AND tx.height<{end_height}"
        label = f"ibc_in:{day}"
        old = old_queries[label]
        query_sha = hashlib.sha256(query.encode()).hexdigest()
        if old["query_sha256"] != query_sha:
            raise ValueError(f"New query differs from original query: {label}")
        tasks.append({
            "day": day, "index": old["index"], "label": label,
            "first_height": first_height, "end_height_exclusive": end_height,
            "query": query, "query_sha256": query_sha,
            "old_tx_count": old["tx_count"], "old_page_count": old["page_count"],
        })
    return tasks, times, {
        "path": str(index_path.relative_to(ROOT)), "sha256": meta["sha256"],
        "record_count": len(times), "first_height": meta["first_height"],
        "end_height_exclusive": meta["end_height_exclusive"],
    }


def part_path(output: Path, task: dict[str, Any], page: int) -> Path:
    return output / task["day"] / "parts" / f"page-{page:04d}.json"


def close_transport() -> None:
    connection = getattr(TRANSPORT, "connection", None)
    if connection is not None:
        connection.close()
        TRANSPORT.connection = None


def post_rpc(args: argparse.Namespace, request_body: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    """Reuse one HTTPS connection per worker; never share a socket across threads."""
    url = urlsplit(args.rpc)
    connection = getattr(TRANSPORT, "connection", None)
    if connection is None:
        connection = http.client.HTTPSConnection(url.hostname, port=url.port or 443, timeout=args.timeout)
        TRANSPORT.connection = connection
    reused = connection.sock is not None
    target = url.path or "/"
    if url.query:
        target += "?" + url.query
    connection.request("POST", target, body=canonical(request_body), headers={
        "Content-Type": "application/json", "User-Agent": "ATOM-research-IBC-reaudit/1.0",
        "Accept-Encoding": "gzip", "Connection": "keep-alive",
    })
    stream = connection.getresponse()
    body = stream.read()
    encoding = stream.getheader("Content-Encoding", "identity").lower()
    status = stream.status
    if stream.will_close:
        close_transport()
    if status != 200:
        raise RuntimeError(f"RPC HTTP status {status}")
    if encoding not in {"gzip", "identity"}:
        raise RuntimeError(f"Unsupported HTTP content encoding {encoding}")
    return body, {"http_content_encoding": encoding, "http_connection_reused": reused}


def validate_record(record: dict[str, Any], task: dict[str, Any], page: int, times: dict[int, str]) -> None:
    for key in ("day", "index", "first_height", "end_height_exclusive", "query", "query_sha256"):
        if record.get(key) != task[key]:
            raise ValueError(f"Raw page task mismatch: {task['day']} {page} {key}")
    if record["page"] != page or record["per_page"] != PAGE_SIZE:
        raise ValueError("Raw page number/size mismatch")
    response = record["response"]
    if (
        response.get("error")
        or response.get("id") != f"ibc-reaudit-{task['day']}-{page}"
        or digest(response) != record["response_sha256"]
    ):
        raise ValueError("RPC error or response digest mismatch")
    result = response["result"]
    total = int(result["total_count"])
    expected_rows = max(0, min(PAGE_SIZE, total - (page - 1) * PAGE_SIZE))
    txs = result.get("txs") or []
    if len(txs) != expected_rows or page > max(1, math.ceil(total / PAGE_SIZE)):
        raise ValueError(f"Unexpected page size: {task['day']} page {page}")
    seen = set()
    positions = []
    for tx in txs:
        height = int(tx["height"])
        if not task["first_height"] <= height < task["end_height_exclusive"] or times[height][:10] != task["day"]:
            raise ValueError(f"Transaction outside requested date/height window: {height}")
        tx_hash = tx["hash"].upper()
        if hashlib.sha256(base64.b64decode(tx["tx"], validate=True)).hexdigest().upper() != tx_hash:
            raise ValueError(f"Transaction body hash mismatch: {tx_hash}")
        if tx_hash in seen:
            raise ValueError(f"Duplicate transaction in page: {tx_hash}")
        seen.add(tx_hash)
        positions.append((height, int(tx["index"])))
        if not isinstance(tx["tx_result"].get("events"), list):
            raise ValueError(f"Missing full transaction events: {tx_hash}")
    if positions != sorted(positions):
        raise ValueError("Transaction page is not in ascending height/index order")


def collect_page(task: dict[str, Any], page: int, args: argparse.Namespace, times: dict[int, str]) -> dict[str, Any]:
    path = part_path(args.output, task, page)
    if path.exists():
        record = json.loads(path.read_text())
        validate_record(record, task, page, times)
        return record
    if args.mode == "verify":
        raise ValueError(f"Missing raw page in verify mode: {task['day']} {page}")
    request_body = {
        "jsonrpc": "2.0", "id": f"ibc-reaudit-{task['day']}-{page}", "method": "tx_search",
        "params": {"query": task["query"], "prove": False, "page": str(page), "per_page": str(PAGE_SIZE), "order_by": "asc"},
    }
    errors = []
    for attempt in range(args.attempts):
        began = time.monotonic()
        requested_at = now()
        try:
            body, transport_meta = post_rpc(args, request_body)
            decoded = gzip.decompress(body) if transport_meta["http_content_encoding"] == "gzip" else body
            response = json.loads(decoded)
            record = {
                "schema_version": 1, **task, "page": page, "per_page": PAGE_SIZE,
                "source_rpc": args.rpc, "requested_at_utc": requested_at, "fetched_at_utc": now(),
                "elapsed_seconds": round(time.monotonic() - began, 6),
                "http_body_size_bytes": len(body), "http_body_sha256": hashlib.sha256(body).hexdigest(),
                "http_decoded_size_bytes": len(decoded), **transport_meta,
                "response_sha256": digest(response), "previous_attempt_errors": errors, "response": response,
            }
            validate_record(record, task, page, times)
            atomic_json(path, record)
            return record
        except Exception as error:
            close_transport()
            errors.append(f"{type(error).__name__}: {error}")
            if attempt + 1 < args.attempts:
                time.sleep(min(2 ** attempt, 4))
    raise RuntimeError(f"{task['day']} page {page}: {'; '.join(errors)}")


def collect_page_summary(task: dict[str, Any], page: int, args: argparse.Namespace, times: dict[int, str]) -> dict[str, Any]:
    """Avoid retaining gigabytes of parsed page bodies in completed futures."""
    was_cached = part_path(args.output, task, page).exists()
    record = collect_page(task, page, args, times)
    return {
        "day": task["day"], "page": page, "was_cached": was_cached,
        "elapsed_seconds": record["elapsed_seconds"],
        "http_body_size_bytes": record["http_body_size_bytes"],
        "total_count": int(record["response"]["result"]["total_count"]),
    }


def complete_day(task: dict[str, Any], args: argparse.Namespace, times: dict[int, str]) -> dict[str, Any]:
    first = collect_page(task, 1, args, times)
    total = int(first["response"]["result"]["total_count"])
    pages = max(1, math.ceil(total / PAGE_SIZE))
    seen = set()
    previous_position = None
    page_files = []
    path = args.output / task["day"] / "tx_search.jsonl.gz"
    pending = path.with_suffix(path.suffix + ".partial")
    with gzip.open(pending, "wt", encoding="utf-8") as output:
        for page in range(1, pages + 1):
            raw_path = part_path(args.output, task, page)
            record = json.loads(raw_path.read_text())
            validate_record(record, task, page, times)
            if int(record["response"]["result"]["total_count"]) != total:
                raise ValueError(f"Total count changes between pages on {task['day']}")
            for tx in record["response"]["result"].get("txs") or []:
                tx_hash = tx["hash"].upper()
                position = (int(tx["height"]), int(tx["index"]))
                if tx_hash in seen or (previous_position is not None and position <= previous_position):
                    raise ValueError(f"Duplicate or unordered transaction across pages: {tx_hash}")
                seen.add(tx_hash)
                previous_position = position
            output.write(canonical(record).decode() + "\n")
            page_files.append({"page": page, "path": str(raw_path.relative_to(args.output)), "sha256": file_sha(raw_path)})
    if len(seen) != total:
        raise ValueError(f"Incomplete daily transaction count for {task['day']}")
    pending.replace(path)
    summary = {
        **task, "status": "complete", "total_count": total, "pages": pages,
        "count_matches_old_manifest": total == task["old_tx_count"],
        "unique_tx_count": len(seen), "tx_search_path": str(path.relative_to(args.output)),
        "tx_search_sha256": file_sha(path), "page_files": page_files,
        "verified_at_utc": now(), "validation": "query/date/height/page count/order/duplicate hash/body SHA-256 passed",
    }
    atomic_json(args.output / task["day"] / "day_manifest.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("estimate", "fetch", "verify"), default="estimate")
    parser.add_argument("--rpc", default=DEFAULT_RPC)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    parser.add_argument("--timeout", type=float, default=25)
    parser.add_argument("--attempts", type=int, default=2)
    args = parser.parse_args()
    if not args.rpc.startswith("https://"):
        raise ValueError("Only HTTPS archive endpoints are supported")
    tasks, times, index_meta = load_tasks()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output / "manifest.json"
    previous_manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    run_history = previous_manifest.get("run_history", []) + [{"mode": args.mode, "started_at_utc": now()}]
    manifest = {
        "schema_version": 1, "chain_id": "cosmoshub-4",
        "created_at_utc": previous_manifest.get("created_at_utc", now()), "run_history": run_history,
        "mode": args.mode, "status": "running", "source_rpc": args.rpc,
        "transport": "worker-local HTTPS keep-alive; gzip requested when available",
        "workers": args.workers, "block_time_index": index_meta, "tasks": tasks,
        "limitations": ["Public tx_search data, not independently replayed consensus execution.",
                        "Transaction SHA-256 and pagination checks do not authenticate execution-result events.",
                        "Raw events and acknowledgements are retained without filtering on execution or IBC success."],
    }
    atomic_json(manifest_path, manifest)
    first_pages = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(collect_page_summary, task, 1, args, times): task for task in tasks}
        for future in concurrent.futures.as_completed(futures):
            task = futures[future]
            try:
                record = future.result()
            except Exception as error:
                manifest.update(status="first_page_error", error=str(error), updated_at_utc=now())
                atomic_json(manifest_path, manifest)
                for queued in futures:
                    queued.cancel()
                raise
            first_pages[task["day"]] = record
            total = record["total_count"]
            print(f"first_page day={task['day']} total_count={total} old_count={task['old_tx_count']} seconds={record['elapsed_seconds']:.2f}", flush=True)
    total_pages = sum(max(1, math.ceil(row["total_count"] / PAGE_SIZE)) for row in first_pages.values())
    request_seconds = statistics.median(row["elapsed_seconds"] for row in first_pages.values())
    expected_bytes = sum(row["http_body_size_bytes"] * max(1, math.ceil(row["total_count"] / PAGE_SIZE)) for row in first_pages.values())
    manifest["estimate"] = {
        "days": len(tasks), "transactions": sum(row["total_count"] for row in first_pages.values()),
        "total_pages": total_pages, "median_first_page_seconds": request_seconds,
        "estimated_full_minutes_at_configured_workers": total_pages * request_seconds / args.workers / 60,
        "estimated_uncompressed_http_bytes": expected_bytes,
        "count_mismatch_days": [task["day"] for task in tasks if first_pages[task["day"]]["total_count"] != task["old_tx_count"]],
    }
    print("estimate " + json.dumps(manifest["estimate"], sort_keys=True), flush=True)
    manifest.update(status="estimated", updated_at_utc=now())
    atomic_json(manifest_path, manifest)
    if args.mode == "estimate":
        return
    pending_jobs = []
    outstanding = {}
    for task in tasks:
        pages = max(1, math.ceil(first_pages[task["day"]]["total_count"] / PAGE_SIZE))
        outstanding[task["day"]] = pages - 1
        for page in range(2, pages + 1):
            if args.mode == "verify" and not part_path(args.output, task, page).exists():
                raise ValueError(f"Missing raw page in verify mode: {task['day']} {page}")
            pending_jobs.append((task, page))
    completed_pages = 30
    manifest["status"] = "fetching"
    summaries = []
    request_durations = []
    for task in tasks:
        if outstanding[task["day"]] == 0:
            summaries.append(complete_day(task, args, times))
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(collect_page_summary, task, page, args, times): (task, page) for task, page in pending_jobs}
        for future in concurrent.futures.as_completed(futures):
            task, page = futures[future]
            try:
                page_summary = future.result()
            except Exception as error:
                manifest.update(status="page_error", error=str(error), updated_at_utc=now())
                atomic_json(manifest_path, manifest)
                for queued in futures:
                    queued.cancel()
                raise
            completed_pages += 1
            outstanding[task["day"]] -= 1
            if not page_summary["was_cached"]:
                request_durations.append(page_summary["elapsed_seconds"])
            eta = ((total_pages - completed_pages) * statistics.median(request_durations[-30:]) / args.workers / 60) if request_durations else None
            print(f"page_done {completed_pages}/{total_pages} day={task['day']} page={page} eta_minutes={eta}", flush=True)
            if outstanding[task["day"]] == 0:
                day_summary = complete_day(task, args, times)
                summaries.append(day_summary)
                print(f"day_complete day={task['day']} txs={day_summary['total_count']} pages={day_summary['pages']} days_complete={len(summaries)}/30", flush=True)
            manifest.update(completed_pages=completed_pages, day_summaries=summaries,
                            completed_days=len(summaries), recent_eta_minutes=eta, updated_at_utc=now())
            atomic_json(manifest_path, manifest)
    summaries.sort(key=lambda row: row["day"])
    global_hashes = set()
    for task in tasks:
        path = args.output / task["day"] / "tx_search.jsonl.gz"
        with gzip.open(path, "rt") as stream:
            for line in stream:
                for tx in json.loads(line)["response"]["result"].get("txs") or []:
                    tx_hash = tx["hash"].upper()
                    if tx_hash in global_hashes:
                        raise ValueError(f"Duplicate transaction across daily windows: {tx_hash}")
                    global_hashes.add(tx_hash)
    manifest.update(status="complete", day_summaries=summaries, unique_tx_count=len(global_hashes), completed_at_utc=now())
    atomic_json(manifest_path, manifest)
    print(f"complete days={len(summaries)} unique_txs={len(global_hashes)} pages={total_pages}", flush=True)


if __name__ == "__main__":
    main()
