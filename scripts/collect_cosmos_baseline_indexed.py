#!/usr/bin/env python3
"""Collect a compact, auditable 30-day Cosmos Hub baseline from indexed events."""

from __future__ import annotations

import argparse
from bisect import bisect_left
import concurrent.futures
import gzip
import hashlib
import io
import json
import math
import os
import threading
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from collect_cosmos_rpc import block_time, first_height_at_or_after, rpc_json, sha256_file, utc_now
from collect_cosmos_study_data import make_meta_tasks, post_batch, run_tasks, validate_endpoints
from extract_cosmos_flows import (
    atom_flag,
    decode_tx_body,
    event_attributes,
    message_class,
    packet_fields,
    parse_coins,
    parse_msg_index,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RPCS = [
    "https://rpc.cosmoshub-main.ccvalidators.com",
    "https://rpc.cosmoshub-4-archive.citizenweb3.com",
    "https://cosmos-rpc.publicnode.com",
]


def convert_compact_metas(
    task: dict[str, Any], responses: list[dict[str, Any]], rpc: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_id = {item["id"]: item for item in responses}
    records: list[dict[str, Any]] = []
    for low, high in task["ranges"]:
        for meta in by_id[f"meta-{low}-{high}"]["result"]["block_metas"]:
            height = int(meta["header"]["height"])
            if low <= height <= high:
                records.append(
                    {
                        "height": height,
                        "time_utc": meta["header"]["time"],
                        "block_hash": meta["block_id"]["hash"],
                        "num_txs": int(meta["num_txs"]),
                        "source_rpc": rpc,
                    }
                )
    records.sort(key=lambda item: item["height"])
    return records, {
        "batch_index": task["index"],
        "first_height": records[0]["height"],
        "last_height": records[-1]["height"],
        "record_count": len(records),
        "rpc": rpc,
    }


def load_block_times(path: Path, first_height: int, end_height: int) -> dict[int, datetime]:
    times: dict[int, datetime] = {}
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            times[int(row["height"])] = datetime.fromisoformat(row["time_utc"].replace("Z", "+00:00"))
    expected = end_height - first_height
    if len(times) != expected or min(times) != first_height or max(times) != end_height - 1:
        raise RuntimeError("compact block-time index is incomplete")
    return times


def search_all(
    preferred_rpc: str, validated_rpcs: list[str], query: str, task_index: int
) -> tuple[list[dict[str, Any]], int, int, Counter[str], list[dict[str, str]]]:
    page_size = 100
    page_source_counts: Counter[str] = Counter()
    endpoint_failovers: list[dict[str, str]] = []
    provenance_lock = threading.Lock()

    def fetch_pages(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rpc_order = [preferred_rpc, *[rpc for rpc in validated_rpcs if rpc != preferred_rpc]]
        for rpc in rpc_order:
            try:
                responses = post_batch(rpc, requests, attempts=6)
                with provenance_lock:
                    page_source_counts[rpc] += len(requests)
                return responses
            except (OSError, RuntimeError) as error:
                with provenance_lock:
                    endpoint_failovers.append({"rpc": rpc, "error": f"{type(error).__name__}: {error}"})
        raise RuntimeError(f"all indexed-search endpoints failed for pages {[item['id'] for item in requests]}")

    first_request = {
        "jsonrpc": "2.0",
        "id": f"search-{task_index}-1",
        "method": "tx_search",
        "params": {"query": query, "prove": False, "page": "1", "per_page": str(page_size), "order_by": "asc"},
    }
    first = fetch_pages([first_request])[0]
    total = int(first["result"]["total_count"])
    pages = max(1, math.ceil(total / page_size))
    responses = [first]
    # A single 100-transaction page is about 1.3 MB for IBC-heavy blocks and is
    # accepted by the validated gateways; batching multiple such pages creates
    # oversized responses that some gateways reject.
    def fetch_page(page: int) -> dict[str, Any]:
        request = {
            "jsonrpc": "2.0",
            "id": f"search-{task_index}-{page}",
            "method": "tx_search",
            "params": {
                "query": query,
                "prove": False,
                "page": str(page),
                "per_page": str(page_size),
                "order_by": "asc",
            },
        }
        return fetch_pages([request])[0]

    # Historical pages are immutable and independent. Two concurrent page
    # requests shorten exhaustive IBC scans without exceeding the conservative
    # rate accepted by the archive gateway; the consistency
    # and duplicate-hash checks below still validate the assembled result.
    if pages > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(2, pages - 1)) as executor:
            responses.extend(executor.map(fetch_page, range(2, pages + 1)))
    responses.sort(key=lambda item: int(item["id"].rsplit("-", 1)[1]))
    if any(int(response["result"]["total_count"]) != total for response in responses):
        raise RuntimeError(f"tx_search total_count changed across pages for {query}")
    txs = [tx for response in responses for tx in (response["result"]["txs"] or [])]
    if len(txs) != total:
        raise RuntimeError(f"tx_search pagination mismatch: {len(txs)} != {total} for {query}")
    if len({tx["hash"] for tx in txs}) != len(txs):
        raise RuntimeError(f"tx_search returned duplicate transaction hashes for {query}")
    endpoint_failovers.sort(key=lambda item: (item["rpc"], item["error"]))
    return txs, total, pages, page_source_counts, endpoint_failovers


def tx_message_context(tx: dict[str, Any]) -> tuple[str, list[str], dict[int, str]]:
    memo, message_types = decode_tx_body(tx["tx"])
    actions: dict[int, str] = {}
    for event in tx["tx_result"].get("events") or []:
        if event["type"] != "message":
            continue
        attributes = event_attributes(event)
        msg_index = parse_msg_index(attributes)
        if msg_index is not None and attributes.get("action"):
            actions[msg_index] = attributes["action"]
    return memo, message_types, actions


def direct_events_for_target(
    tx: dict[str, Any], target: str, direction: str
) -> tuple[list[dict[str, Any]], int]:
    if int(tx["tx_result"].get("code", 0)) != 0:
        return [], 0
    decode_errors = 0
    try:
        _, message_types, actions = tx_message_context(tx)
    except (ValueError, UnicodeDecodeError):
        message_types, actions = [], {}
        decode_errors = 1
    extracted: list[dict[str, Any]] = []
    for ordinal, event in enumerate(tx["tx_result"].get("events") or []):
        if event["type"] != "transfer":
            continue
        attributes = event_attributes(event)
        sender = attributes.get("sender")
        recipient = attributes.get("recipient")
        if direction == "inbound" and recipient != target:
            continue
        if direction == "outbound" and sender != target:
            continue
        msg_index = parse_msg_index(attributes)
        msg_type = actions.get(msg_index) if msg_index is not None else None
        if not msg_type and msg_index is not None and msg_index < len(message_types):
            msg_type = message_types[msg_index]
        if message_class(msg_type) != "direct_bank":
            continue
        for amount, denom in parse_coins(attributes.get("amount", "")):
            if denom == "uatom":
                extracted.append(
                    {
                        "height": int(tx["height"]),
                        "tx_hash": tx["hash"],
                        "event_ordinal": ordinal,
                        "msg_index": msg_index,
                        "sender": sender,
                        "recipient": recipient,
                        "amount_uatom": amount,
                    }
                )
    return extracted, decode_errors


def ibc_events(tx: dict[str, Any], direction: str) -> tuple[list[dict[str, Any]], int]:
    if int(tx["tx_result"].get("code", 0)) != 0:
        return [], 0
    extracted: list[dict[str, Any]] = []
    decode_errors = 0
    events = tx["tx_result"].get("events") or []
    if direction == "outbound":
        for ordinal, event in enumerate(events):
            if event["type"] != "ibc_transfer":
                continue
            attributes = event_attributes(event)
            amount_text = attributes.get("amount", "0")
            denom = attributes.get("denom", "")
            if amount_text.isdigit() and atom_flag("outbound", denom):
                extracted.append(
                    {
                        "height": int(tx["height"]),
                        "tx_hash": tx["hash"],
                        "event_ordinal": ordinal,
                        "amount_uatom": int(amount_text),
                    }
                )
    else:
        for ordinal, event in enumerate(events):
            if event["type"] != "recv_packet":
                continue
            packet = packet_fields(event)
            if packet["src_port"] != "transfer" or packet["dst_port"] != "transfer" or not packet["packet_data_hex"]:
                continue
            try:
                packet_data = json.loads(bytes.fromhex(packet["packet_data_hex"]).decode("utf-8"))
                denom = str(packet_data["denom"])
                amount = int(packet_data["amount"])
            except (ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError):
                decode_errors += 1
                continue
            if atom_flag("inbound", denom):
                extracted.append(
                    {
                        "height": int(tx["height"]),
                        "tx_hash": tx["hash"],
                        "event_ordinal": ordinal,
                        "amount_uatom": amount,
                    }
                )
    return extracted, decode_errors


def process_search_task(
    task: dict[str, Any],
    preferred_rpc: str,
    validated_rpcs: list[str],
    block_times: dict[int, datetime],
    start_time: datetime,
) -> dict[str, Any]:
    txs, total, pages, page_sources, attempted = search_all(
        preferred_rpc, validated_rpcs, task["query"], task["index"]
    )
    digest = hashlib.sha256()
    partial: dict[int, Counter[str]] = defaultdict(Counter)
    event_count = 0
    amount_uatom = 0
    decode_errors = 0
    for tx in txs:
        if task["kind"] in {"recipient", "confirmed_out"}:
            direction = "inbound" if task["kind"] == "recipient" else "outbound"
            events, errors = direct_events_for_target(tx, task["address"], direction)
        else:
            direction = "outbound" if task["kind"] == "ibc_out" else "inbound"
            events, errors = ibc_events(tx, direction)
        decode_errors += errors
        for event in events:
            timestamp = block_times[event["height"]]
            bucket = int((timestamp - start_time).total_seconds() // 300)
            if bucket < 0:
                continue
            amount = int(event["amount_uatom"])
            if task["kind"] == "recipient":
                metric = task["metric"]
            elif task["kind"] == "confirmed_out":
                metric = "confirmed_exchange_out"
            elif task["kind"] == "ibc_out":
                metric = "ibc_outbound"
            else:
                metric = "ibc_inbound"
            partial[bucket][f"{metric}_count"] += 1
            partial[bucket][f"{metric}_uatom"] += amount
            event_count += 1
            amount_uatom += amount
            canonical = {"metric": metric, **event}
            digest.update(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")
    return {
        "index": task["index"],
        "label": task["label"],
        "preferred_rpc": preferred_rpc,
        "page_source_counts": dict(page_sources),
        "endpoint_failovers": attempted,
        "query_sha256": hashlib.sha256(task["query"].encode("utf-8")).hexdigest(),
        "tx_count": total,
        "page_count": pages,
        "extracted_event_count": event_count,
        "extracted_amount_uatom": amount_uatom,
        "extracted_events_sha256": digest.hexdigest(),
        "decode_errors": decode_errors,
        "partial": {bucket: dict(values) for bucket, values in partial.items()},
    }


def write_deterministic_jsonl_gzip(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="\n") as text:
                for row in rows:
                    text.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc", action="append", dest="rpcs")
    parser.add_argument("--start", default="2025-09-10")
    parser.add_argument("--end-exclusive", default="2025-10-10")
    parser.add_argument("--workers", type=int, help="Legacy override for both block and query workers")
    parser.add_argument("--block-workers", type=int, default=3)
    parser.add_argument("--query-workers", type=int, default=1)
    parser.add_argument(
        "--include-watchlist",
        action="store_true",
        help="Also query large-flow watchlist addresses; excluded by default from exchange-flow baselines",
    )
    parser.add_argument("--max-addresses", type=int, help="Testing only: limit recipient queries")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "data" / "processed" / "cosmoshub" / "baseline_30d",
    )
    args = parser.parse_args()
    block_workers = args.workers or args.block_workers
    query_workers = args.workers or args.query_workers
    rpcs = args.rpcs or DEFAULT_RPCS
    start_day = date.fromisoformat(args.start)
    end_day = date.fromisoformat(args.end_exclusive)
    start_time = datetime.combine(start_day, datetime.min.time(), tzinfo=timezone.utc)
    end_time = datetime.combine(end_day, datetime.min.time(), tzinfo=timezone.utc)
    if end_time <= start_time:
        parser.error("--end-exclusive must follow --start")

    candidates_document = json.loads(
        (PROCESSED := PROJECT_ROOT / "data" / "processed" / "cosmoshub")
        .joinpath("exchange_inflow_candidates_2025-10-09_2025-10-12.json")
        .read_text(encoding="utf-8")
    )
    labels_document = json.loads((PROJECT_ROOT / "metadata" / "exchange_address_labels.json").read_text(encoding="utf-8"))
    labels = {row["address"] for row in labels_document["labels"]}
    address_tiers = {row["address"]: row["candidate_tier"] for row in candidates_document["candidates"]}
    for address in labels:
        address_tiers.setdefault(address, "public_label")
    addresses = sorted(address_tiers)
    if args.max_addresses:
        non_labels = [address for address in addresses if address not in labels]
        addresses = sorted(labels | set(non_labels[: args.max_addresses]))

    cross_check_height = 27908328
    endpoint_records: list[dict[str, Any]] = []
    endpoint_failures: list[dict[str, str]] = []
    for rpc in rpcs:
        try:
            endpoint_records.extend(validate_endpoints([rpc], "cosmoshub-4", cross_check_height))
        except (OSError, RuntimeError, SystemExit) as error:
            endpoint_failures.append({"rpc": rpc, "error": f"{type(error).__name__}: {error}"})
    if not endpoint_records:
        raise SystemExit(f"no usable archive endpoint: {endpoint_failures}")
    if len({record["cross_check_block_hash"] for record in endpoint_records}) != 1 or len(
        {record["cross_check_block_results_sha256"] for record in endpoint_records}
    ) != 1:
        raise SystemExit("archive endpoints disagree at the cross-check height")
    validated_rpcs = [record["rpc"] for record in endpoint_records]
    primary = validated_rpcs[0]
    earliest = endpoint_records[0]["earliest_block_height"]
    latest = endpoint_records[0]["latest_block_height"]
    first_height = first_height_at_or_after(primary, start_time, earliest, latest)
    end_height = first_height_at_or_after(primary, end_time, first_height, latest)
    print(f"range {first_height} {block_time(primary, first_height)} -> {end_height} {block_time(primary, end_height)}", flush=True)

    args.output_root.mkdir(parents=True, exist_ok=True)
    block_times_path = args.output_root / f"block_times_{args.start}_{args.end_exclusive}.jsonl.gz"
    block_manifest_path = args.output_root / "block_times_manifest.json"
    reuse_block_times = False
    if block_times_path.exists() and block_manifest_path.exists():
        previous = json.loads(block_manifest_path.read_text(encoding="utf-8"))
        reuse_block_times = (
            previous.get("first_height") == first_height
            and previous.get("end_height_exclusive") == end_height
            and previous.get("sha256") == sha256_file(block_times_path)
        )
    if not reuse_block_times:
        count, summaries, sources = run_tasks(
            make_meta_tasks(first_height, end_height),
            validated_rpcs,
            block_workers,
            block_times_path,
            convert_compact_metas,
            "baseline block times",
        )
        if count != end_height - first_height:
            raise RuntimeError(f"block-time count mismatch: {count} != {end_height - first_height}")
        block_manifest = {
            "first_height": first_height,
            "end_height_exclusive": end_height,
            "record_count": count,
            "sha256": sha256_file(block_times_path),
            "size_bytes": block_times_path.stat().st_size,
            "source_batch_counts": dict(sources),
            "batch_count": len(summaries),
        }
        block_manifest_path.write_text(json.dumps(block_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        print("verified_existing block-time index", flush=True)
    block_times = load_block_times(block_times_path, first_height, end_height)
    ordered_heights = sorted(block_times)
    ordered_times = [block_times[height] for height in ordered_heights]

    def local_height_at_or_after(target: datetime) -> int:
        position = bisect_left(ordered_times, target)
        return ordered_heights[position] if position < len(ordered_heights) else end_height

    tasks: list[dict[str, Any]] = []
    for address in addresses:
        tier = address_tiers[address]
        if address in labels:
            metric = "confirmed_exchange_in"
        elif tier in {"behavioral_high", "behavioral_medium"}:
            metric = "unconfirmed_behavioral_in"
        else:
            metric = "large_flow_watchlist_in"
        tasks.append(
            {
                "kind": "recipient",
                "address": address,
                "metric": metric,
                "label": f"recipient:{address}",
                "query": f"transfer.recipient='{address}' AND tx.height>={first_height} AND tx.height<{end_height}",
                "skip": metric == "large_flow_watchlist_in" and not args.include_watchlist,
            }
        )
    for address in sorted(labels):
        tasks.append(
            {
                "kind": "confirmed_out",
                "address": address,
                "label": f"confirmed_out:{address}",
                "query": f"transfer.sender='{address}' AND tx.height>={first_height} AND tx.height<{end_height}",
            }
        )
    day = start_day
    while day < end_day:
        day_start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        next_day = day + timedelta(days=1)
        day_end = datetime.combine(next_day, datetime.min.time(), tzinfo=timezone.utc)
        day_first = local_height_at_or_after(day_start)
        day_end_height = local_height_at_or_after(day_end)
        tasks.extend(
            [
                {
                    "kind": "ibc_out",
                    "label": f"ibc_out:{day}",
                    "query": f"ibc_transfer.denom='uatom' AND tx.height>={day_first} AND tx.height<{day_end_height}",
                },
                {
                    "kind": "ibc_in",
                    "label": f"ibc_in:{day}",
                    "query": f"recv_packet.packet_dst_port='transfer' AND tx.height>={day_first} AND tx.height<{day_end_height}",
                },
            ]
        )
        day = next_day
    for index, task in enumerate(tasks):
        task["index"] = index
    eligible_tasks = [task for task in tasks if not task.get("skip")]

    workers = min(query_workers, len(validated_rpcs))
    global_buckets: dict[int, Counter[str]] = defaultdict(Counter)
    summaries: list[dict[str, Any]] = []
    preferred_source_counts: Counter[str] = Counter()
    page_source_counts: Counter[str] = Counter()
    checkpoint_root = args.output_root / "query_checkpoints"
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    next_submit = 0
    completed = 0

    def checkpoint_path(task: dict[str, Any]) -> Path:
        return checkpoint_root / f"{task['index']:04d}.jsonl.gz"

    def load_checkpoint(task: dict[str, Any]) -> dict[str, Any] | None:
        path = checkpoint_path(task)
        if not path.exists():
            return None
        try:
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                result = json.loads(stream.readline())
            expected_query_sha = hashlib.sha256(task["query"].encode("utf-8")).hexdigest()
            if (
                result.get("index") != task["index"]
                or result.get("label") != task["label"]
                or result.get("query_sha256") != expected_query_sha
                or not isinstance(result.get("partial"), dict)
            ):
                return None
            return result
        except (OSError, EOFError, json.JSONDecodeError):
            return None

    def merge_result(result: dict[str, Any], fallback_preferred_rpc: str) -> None:
        nonlocal completed
        for source_rpc, page_count in result["page_source_counts"].items():
            page_source_counts[source_rpc] += page_count
        for bucket, values in result["partial"].items():
            global_buckets[int(bucket)].update(values)
        summaries.append({key: value for key, value in result.items() if key != "partial"})
        preferred_source_counts[result.get("preferred_rpc", fallback_preferred_rpc)] += 1
        completed += 1

    pending_tasks: list[dict[str, Any]] = []
    for task in eligible_tasks:
        checkpoint = load_checkpoint(task)
        if checkpoint is None:
            pending_tasks.append(task)
        else:
            merge_result(checkpoint, checkpoint.get("preferred_rpc", primary))
    if completed:
        print(f"verified query checkpoints: {completed}/{len(eligible_tasks)}", flush=True)

    def submit(executor: concurrent.futures.ThreadPoolExecutor, rpc: str, task: dict[str, Any]):
        return executor.submit(process_search_task, task, rpc, validated_rpcs, block_times, start_time)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        inflight: dict[concurrent.futures.Future[Any], str] = {}
        for rpc in validated_rpcs[:workers]:
            if next_submit < len(pending_tasks):
                inflight[submit(executor, rpc, pending_tasks[next_submit])] = rpc
                next_submit += 1
        while inflight:
            done, _ = concurrent.futures.wait(inflight, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                rpc = inflight.pop(future)
                result = future.result()
                write_deterministic_jsonl_gzip(checkpoint_path(tasks[result["index"]]), [result])
                merge_result(result, rpc)
                if completed % 20 == 0 or completed == len(eligible_tasks):
                    print(f"indexed event queries: {completed}/{len(eligible_tasks)}", flush=True)
                if next_submit < len(pending_tasks):
                    inflight[submit(executor, rpc, pending_tasks[next_submit])] = rpc
                    next_submit += 1

    rows: list[dict[str, Any]] = []
    bucket_count = int((end_time - start_time).total_seconds() // 300)
    metric_names = [
        "confirmed_exchange_in",
        "confirmed_exchange_out",
        "unconfirmed_behavioral_in",
        "ibc_inbound",
        "ibc_outbound",
    ]
    for bucket in range(bucket_count):
        values = global_buckets[bucket]
        row: dict[str, Any] = {
            "bucket_start_utc": (start_time + timedelta(minutes=5 * bucket)).isoformat().replace("+00:00", "Z")
        }
        for metric in metric_names:
            row[f"{metric}_count"] = values[f"{metric}_count"]
            row[f"{metric}_atom"] = values[f"{metric}_uatom"] / 1_000_000
        row["confirmed_exchange_net_in_atom"] = (
            values["confirmed_exchange_in_uatom"] - values["confirmed_exchange_out_uatom"]
        ) / 1_000_000
        row["all_behavioral_candidate_in_atom"] = (
            values["confirmed_exchange_in_uatom"] + values["unconfirmed_behavioral_in_uatom"]
        ) / 1_000_000
        row["ibc_net_inbound_atom"] = (values["ibc_inbound_uatom"] - values["ibc_outbound_uatom"]) / 1_000_000
        rows.append(row)

    series_path = args.output_root / f"baseline_5min_{args.start}_{args.end_exclusive}.jsonl.gz"
    write_deterministic_jsonl_gzip(series_path, rows)
    summaries.sort(key=lambda item: item["index"])
    run_started = utc_now()
    manifest = {
        "study_id": "atom_flash_crash_2025_10_10",
        "chain_id": "cosmoshub-4",
        "created_at_utc": run_started,
        "window": f"[{start_time.isoformat()}, {end_time.isoformat()})",
        "first_height": first_height,
        "end_height_exclusive": end_height,
        "block_height_count": end_height - first_height,
        "endpoint_validation": endpoint_records,
        "endpoint_validation_failures": endpoint_failures,
        "block_times": {
            "local_path": block_times_path.relative_to(PROJECT_ROOT).as_posix(),
            "record_count": len(block_times),
            "sha256": sha256_file(block_times_path),
            "size_bytes": block_times_path.stat().st_size,
        },
        "five_minute_series": {
            "local_path": series_path.relative_to(PROJECT_ROOT).as_posix(),
            "record_count": len(rows),
            "sha256": sha256_file(series_path),
            "size_bytes": series_path.stat().st_size,
        },
        "address_query_count": sum(task["kind"] == "recipient" for task in eligible_tasks),
        "excluded_watchlist_address_count": sum(task.get("skip", False) for task in tasks),
        "confirmed_label_count": len(labels),
        "query_count": len(eligible_tasks),
        "query_checkpoint_directory": str(checkpoint_root),
        "query_preferred_source_counts": dict(preferred_source_counts),
        "query_page_source_counts": dict(page_source_counts),
        "protobuf_or_packet_decode_errors": sum(item["decode_errors"] for item in summaries),
        "query_summaries": summaries,
        "limitations": [
            "The compact baseline retains block-time metadata, extracted five-minute aggregates, query counts, and event digests rather than duplicating full tx_search responses.",
            "Direct flows are collected only for public-label addresses and the pre-specified behavioral high/medium registry.",
            "Large-flow watchlist addresses are excluded from the 30-day baseline because they are not exchange-flow candidates; their event-centered four-day records remain separately retained.",
            "Behavioral candidate status is not evidence of exchange ownership.",
        ],
    }
    manifest_path = args.output_root / "baseline_indexed_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"manifest": str(manifest_path), "series": str(series_path), "rows": len(rows), "queries": len(eligible_tasks)}, indent=2))


if __name__ == "__main__":
    main()
