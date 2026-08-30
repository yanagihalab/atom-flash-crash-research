#!/usr/bin/env python3
"""Extract auditable ATOM and IBC flows from the verified Cosmos Hub dataset."""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import math
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FLASH_TIME = datetime.fromisoformat("2025-10-10T21:20:37.689043+00:00")
EVENT_START = datetime.fromisoformat("2025-10-10T20:30:00+00:00")
EVENT_END = datetime.fromisoformat("2025-10-10T22:30:00+00:00")
NUMERIC_MEMO = re.compile(r"^[0-9]{4,20}$")
HEX_ROUTING_MEMO = re.compile(r"^[0-9a-fA-F]{16,32}$")
COIN = re.compile(r"^([0-9]+)(.+)$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def jsonl_gzip(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            yield json.loads(line)


class DeterministicJsonlGzip:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.temporary = path.with_name(path.name + ".part")
        self.raw: io.BufferedWriter | None = None
        self.compressed: gzip.GzipFile | None = None
        self.text: io.TextIOWrapper | None = None
        self.count = 0

    def __enter__(self) -> "DeterministicJsonlGzip":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.raw = self.temporary.open("wb")
        self.compressed = gzip.GzipFile(filename="", mode="wb", fileobj=self.raw, mtime=0)
        self.text = io.TextIOWrapper(self.compressed, encoding="utf-8", newline="\n")
        return self

    def write(self, value: dict[str, Any]) -> None:
        assert self.text is not None
        self.text.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.count += 1

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.text is not None:
            self.text.close()
        elif self.compressed is not None:
            self.compressed.close()
        if self.raw is not None and not self.raw.closed:
            self.raw.close()
        if exc_type is None:
            os.replace(self.temporary, self.path)


def read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if offset >= len(data):
            raise ValueError("truncated protobuf varint")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
        if shift > 70:
            raise ValueError("invalid protobuf varint")


def protobuf_fields(data: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    offset = 0
    while offset < len(data):
        key, offset = read_varint(data, offset)
        field_number = key >> 3
        wire_type = key & 7
        if wire_type == 0:
            value, offset = read_varint(data, offset)
        elif wire_type == 1:
            value = data[offset : offset + 8]
            offset += 8
        elif wire_type == 2:
            length, offset = read_varint(data, offset)
            value = data[offset : offset + length]
            offset += length
        elif wire_type == 5:
            value = data[offset : offset + 4]
            offset += 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire_type}")
        yield field_number, wire_type, value


def decode_any_type_url(data: bytes) -> str | None:
    for field_number, wire_type, value in protobuf_fields(data):
        if field_number == 1 and wire_type == 2:
            assert isinstance(value, bytes)
            return value.decode("utf-8")
    return None


def decode_tx_body(tx_base64: str) -> tuple[str, list[str]]:
    raw = base64.b64decode(tx_base64, validate=True)
    body_bytes: bytes | None = None
    for field_number, wire_type, value in protobuf_fields(raw):
        if field_number == 1 and wire_type == 2:
            assert isinstance(value, bytes)
            body_bytes = value
            break
    if body_bytes is None:
        return "", []
    memo = ""
    message_types: list[str] = []
    for field_number, wire_type, value in protobuf_fields(body_bytes):
        if wire_type != 2:
            continue
        assert isinstance(value, bytes)
        if field_number == 1:
            message_types.append(decode_any_type_url(value) or "")
        elif field_number == 2:
            memo = value.decode("utf-8", errors="replace")
    return memo, message_types


def event_attributes(event: dict[str, Any]) -> dict[str, str]:
    return {item["key"]: item["value"] for item in event.get("attributes", [])}


def parse_msg_index(attributes: dict[str, str]) -> int | None:
    value = attributes.get("msg_index")
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_coins(value: str) -> Iterable[tuple[int, str]]:
    for part in value.split(","):
        match = COIN.match(part.strip())
        if match:
            yield int(match.group(1)), match.group(2)


def message_class(type_url: str | None) -> str:
    if not type_url:
        return "unindexed_or_fee"
    if type_url in {"/cosmos.bank.v1beta1.MsgSend", "/cosmos.bank.v1beta1.MsgMultiSend"}:
        return "direct_bank"
    if type_url == "/ibc.applications.transfer.v1.MsgTransfer":
        return "ibc_transfer"
    if ".staking." in type_url:
        return "staking"
    if ".distribution." in type_url:
        return "distribution"
    if ".gov." in type_url:
        return "governance"
    if ".authz." in type_url or ".group." in type_url:
        return "wrapped_or_group"
    return "other_message"


def structured_routing_memo(value: str) -> bool:
    return bool(NUMERIC_MEMO.fullmatch(value) or (HEX_ROUTING_MEMO.fullmatch(value) and re.search(r"[a-fA-F]", value)))


def bech32_prefix(address: str) -> str | None:
    if "1" not in address:
        return None
    prefix, payload = address.split("1", 1)
    if not prefix or len(payload) < 20 or not prefix.isalnum():
        return None
    return prefix


def timestamp_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def load_block_times(partitions: list[dict[str, Any]]) -> dict[int, str]:
    times: dict[int, str] = {}
    for partition in partitions:
        for row in jsonl_gzip(Path(partition["block_metas"]["local_path"])):
            header = row["block_meta"]["header"]
            times[int(header["height"])] = header["time"]
    return times


def packet_fields(event: dict[str, Any]) -> dict[str, Any]:
    attributes = event_attributes(event)
    return {
        "packet_sequence": attributes.get("packet_sequence"),
        "src_port": attributes.get("packet_src_port"),
        "src_channel": attributes.get("packet_src_channel"),
        "dst_port": attributes.get("packet_dst_port"),
        "dst_channel": attributes.get("packet_dst_channel"),
        "timeout_height": attributes.get("packet_timeout_height"),
        "timeout_timestamp": attributes.get("packet_timeout_timestamp"),
        "msg_index": parse_msg_index(attributes),
        "packet_data_hex": attributes.get("packet_data_hex"),
    }


def pair_packet(packets: list[dict[str, Any]], msg_index: int | None) -> dict[str, Any] | None:
    for packet in packets:
        if packet["msg_index"] == msg_index and packet["src_port"] == "transfer" and packet["dst_port"] == "transfer":
            return packet
    return None


def atom_flag(direction: str, denom: str) -> bool:
    if direction == "outbound":
        return denom == "uatom"
    return denom == "uatom" or denom.endswith("/uatom")


def candidate_score(stats: dict[str, Any]) -> int:
    score = 0
    score += min(25, stats["structured_memo_inflow_count"] * 5)
    score += min(15, stats["unique_structured_memo_count"] * 3)
    score += min(20, stats["unique_senders"] * 2)
    score += min(15, stats["direct_inflow_count"])
    score += min(15, int(math.log10(stats["direct_inflow_uatom"] + 1) * 2))
    if stats["event_window_inflow_count"]:
        score += 5
    if stats["six_hour_inflow_count"]:
        score += 5
    return min(score, 100)


def candidate_tier(stats: dict[str, Any]) -> str | None:
    if stats["structured_memo_inflow_count"] >= 3 and stats["unique_senders"] >= 3 and stats["direct_inflow_count"] >= 5:
        return "behavioral_high"
    if stats["structured_memo_inflow_count"] >= 1 and (stats["unique_senders"] >= 2 or stats["direct_inflow_count"] >= 3):
        return "behavioral_medium"
    if stats["direct_inflow_count"] >= 20 and stats["unique_senders"] >= 10:
        return "behavioral_medium"
    if stats["event_window_inflow_count"] and stats["direct_inflow_uatom"] >= 10_000_000_000:
        return "large_flow_watchlist"
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-manifest",
        type=Path,
        default=PROJECT_ROOT / "metadata" / "runs" / "20260829T043153730115Z_cosmoshub_study_data.json",
    )
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "data" / "processed" / "cosmoshub")
    args = parser.parse_args()

    started = datetime.now(timezone.utc)
    run_manifest = json.loads(args.run_manifest.read_text(encoding="utf-8"))
    assert run_manifest["chain_id"] == "cosmoshub-4"
    partitions = run_manifest["daily_partitions"]
    block_times = load_block_times(partitions)

    atom_path = args.output_root / "atom_transfers_2025-10-09_2025-10-12.jsonl.gz"
    ibc_path = args.output_root / "ibc_transfers_2025-10-09_2025-10-12.jsonl.gz"
    event_path = args.output_root / "event_window_flows_2025-10-10_2030-2230_utc.jsonl.gz"
    candidate_path = args.output_root / "exchange_inflow_candidates_2025-10-09_2025-10-12.json"

    address_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "direct_inflow_count": 0,
            "direct_inflow_uatom": 0,
            "direct_outflow_count": 0,
            "direct_outflow_uatom": 0,
            "senders": set(),
            "nonempty_memo_inflow_count": 0,
            "numeric_memo_inflow_count": 0,
            "numeric_memos": set(),
            "structured_memo_inflow_count": 0,
            "structured_memos": set(),
            "event_window_inflow_count": 0,
            "event_window_inflow_uatom": 0,
            "six_hour_inflow_count": 0,
            "six_hour_inflow_uatom": 0,
            "largest_inflows": [],
        }
    )
    tx_count = 0
    successful_tx_count = 0
    protobuf_decode_errors = 0
    packet_decode_errors = 0
    atom_class_counts: dict[str, int] = defaultdict(int)
    ibc_direction_counts: dict[str, int] = defaultdict(int)
    ibc_atom_direction_counts: dict[str, int] = defaultdict(int)

    with DeterministicJsonlGzip(atom_path) as atom_output, DeterministicJsonlGzip(ibc_path) as ibc_output, DeterministicJsonlGzip(
        event_path
    ) as event_output:
        for partition in partitions:
            for page_record in jsonl_gzip(Path(partition["tx_search"]["local_path"])):
                for tx in page_record["response"]["result"].get("txs") or []:
                    tx_count += 1
                    tx_result = tx["tx_result"]
                    if int(tx_result.get("code", 0)) != 0:
                        continue
                    successful_tx_count += 1
                    height = int(tx["height"])
                    time_utc = block_times[height]
                    timestamp = datetime.fromisoformat(time_utc.replace("Z", "+00:00"))
                    seconds_from_flash = (timestamp - FLASH_TIME).total_seconds()
                    is_event_window = EVENT_START <= timestamp < EVENT_END
                    try:
                        memo, message_types = decode_tx_body(tx["tx"])
                    except (ValueError, UnicodeDecodeError):
                        protobuf_decode_errors += 1
                        memo, message_types = "", []

                    events = tx_result.get("events") or []
                    actions: dict[int, str] = {}
                    for event in events:
                        if event["type"] != "message":
                            continue
                        attributes = event_attributes(event)
                        msg_index = parse_msg_index(attributes)
                        if msg_index is not None and attributes.get("action"):
                            actions[msg_index] = attributes["action"]

                    send_packets = [packet_fields(event) for event in events if event["type"] == "send_packet"]
                    recv_packets = [packet_fields(event) for event in events if event["type"] == "recv_packet"]

                    for ordinal, event in enumerate(events):
                        if event["type"] != "transfer":
                            continue
                        attributes = event_attributes(event)
                        sender = attributes.get("sender")
                        recipient = attributes.get("recipient")
                        if not sender or not recipient:
                            continue
                        msg_index = parse_msg_index(attributes)
                        msg_type = actions.get(msg_index) if msg_index is not None else None
                        if not msg_type and msg_index is not None and msg_index < len(message_types):
                            msg_type = message_types[msg_index]
                        flow_class = message_class(msg_type)
                        for amount, denom in parse_coins(attributes.get("amount", "")):
                            if denom != "uatom":
                                continue
                            row = {
                                "time_utc": time_utc,
                                "height": height,
                                "tx_hash": tx["hash"],
                                "tx_index": int(tx["index"]),
                                "event_ordinal": ordinal,
                                "msg_index": msg_index,
                                "message_type": msg_type,
                                "flow_class": flow_class,
                                "sender": sender,
                                "recipient": recipient,
                                "amount_uatom": amount,
                                "amount_atom": amount / 1_000_000,
                                "tx_memo": memo,
                                "memo_is_numeric_deposit_style": bool(NUMERIC_MEMO.fullmatch(memo)),
                                "memo_is_structured_routing_style": structured_routing_memo(memo),
                                "is_event_window": is_event_window,
                                "seconds_from_flash": seconds_from_flash,
                            }
                            atom_output.write(row)
                            atom_class_counts[flow_class] += 1
                            if is_event_window:
                                event_output.write({"flow_type": "atom_transfer", **row})

                            if flow_class != "direct_bank":
                                continue
                            incoming = address_stats[recipient]
                            incoming["direct_inflow_count"] += 1
                            incoming["direct_inflow_uatom"] += amount
                            incoming["senders"].add(sender)
                            if memo:
                                incoming["nonempty_memo_inflow_count"] += 1
                            if NUMERIC_MEMO.fullmatch(memo):
                                incoming["numeric_memo_inflow_count"] += 1
                                incoming["numeric_memos"].add(memo)
                            if structured_routing_memo(memo):
                                incoming["structured_memo_inflow_count"] += 1
                                incoming["structured_memos"].add(memo)
                            if is_event_window:
                                incoming["event_window_inflow_count"] += 1
                                incoming["event_window_inflow_uatom"] += amount
                            if abs(seconds_from_flash) <= 6 * 3600:
                                incoming["six_hour_inflow_count"] += 1
                                incoming["six_hour_inflow_uatom"] += amount
                            incoming["largest_inflows"].append(
                                {
                                    "amount_uatom": amount,
                                    "time_utc": time_utc,
                                    "tx_hash": tx["hash"],
                                    "sender": sender,
                                    "memo": memo,
                                    "seconds_from_flash": seconds_from_flash,
                                }
                            )
                            outgoing = address_stats[sender]
                            outgoing["direct_outflow_count"] += 1
                            outgoing["direct_outflow_uatom"] += amount

                    for event in events:
                        if event["type"] != "ibc_transfer":
                            continue
                        attributes = event_attributes(event)
                        msg_index = parse_msg_index(attributes)
                        denom = attributes.get("denom", "")
                        amount_text = attributes.get("amount", "0")
                        if not amount_text.isdigit():
                            continue
                        amount = int(amount_text)
                        packet = pair_packet(send_packets, msg_index)
                        row = {
                            "direction": "outbound",
                            "time_utc": time_utc,
                            "height": height,
                            "tx_hash": tx["hash"],
                            "tx_index": int(tx["index"]),
                            "msg_index": msg_index,
                            "sender": attributes.get("sender"),
                            "receiver": attributes.get("receiver"),
                            "counterparty_chain_hint": bech32_prefix(attributes.get("receiver", "")),
                            "denom": denom,
                            "amount_base_units": amount,
                            "is_atom": atom_flag("outbound", denom),
                            "amount_atom": amount / 1_000_000 if atom_flag("outbound", denom) else None,
                            "memo": attributes.get("memo", ""),
                            "packet_sequence": packet.get("packet_sequence") if packet else None,
                            "src_port": packet.get("src_port") if packet else None,
                            "src_channel": packet.get("src_channel") if packet else None,
                            "dst_port": packet.get("dst_port") if packet else None,
                            "dst_channel": packet.get("dst_channel") if packet else None,
                            "timeout_height": packet.get("timeout_height") if packet else None,
                            "timeout_timestamp": packet.get("timeout_timestamp") if packet else None,
                            "is_event_window": is_event_window,
                            "seconds_from_flash": seconds_from_flash,
                        }
                        ibc_output.write(row)
                        ibc_direction_counts["outbound"] += 1
                        if row["is_atom"]:
                            ibc_atom_direction_counts["outbound"] += 1
                        if is_event_window:
                            event_output.write({"flow_type": "ibc_transfer", **row})

                    for packet in recv_packets:
                        if packet["src_port"] != "transfer" or packet["dst_port"] != "transfer" or not packet["packet_data_hex"]:
                            continue
                        try:
                            packet_data = json.loads(bytes.fromhex(packet["packet_data_hex"]).decode("utf-8"))
                            denom = str(packet_data["denom"])
                            amount = int(packet_data["amount"])
                        except (ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError):
                            packet_decode_errors += 1
                            continue
                        row = {
                            "direction": "inbound",
                            "time_utc": time_utc,
                            "height": height,
                            "tx_hash": tx["hash"],
                            "tx_index": int(tx["index"]),
                            "msg_index": packet["msg_index"],
                            "sender": packet_data.get("sender"),
                            "receiver": packet_data.get("receiver"),
                            "counterparty_chain_hint": bech32_prefix(str(packet_data.get("sender", ""))),
                            "denom": denom,
                            "amount_base_units": amount,
                            "is_atom": atom_flag("inbound", denom),
                            "amount_atom": amount / 1_000_000 if atom_flag("inbound", denom) else None,
                            "memo": packet_data.get("memo", ""),
                            "packet_sequence": packet["packet_sequence"],
                            "src_port": packet["src_port"],
                            "src_channel": packet["src_channel"],
                            "dst_port": packet["dst_port"],
                            "dst_channel": packet["dst_channel"],
                            "timeout_height": packet["timeout_height"],
                            "timeout_timestamp": packet["timeout_timestamp"],
                            "is_event_window": is_event_window,
                            "seconds_from_flash": seconds_from_flash,
                        }
                        ibc_output.write(row)
                        ibc_direction_counts["inbound"] += 1
                        if row["is_atom"]:
                            ibc_atom_direction_counts["inbound"] += 1
                        if is_event_window:
                            event_output.write({"flow_type": "ibc_transfer", **row})

    candidates = []
    for address, values in address_stats.items():
        stats = {
            "address": address,
            "bech32_prefix": bech32_prefix(address),
            "direct_inflow_count": values["direct_inflow_count"],
            "direct_inflow_uatom": values["direct_inflow_uatom"],
            "direct_inflow_atom": values["direct_inflow_uatom"] / 1_000_000,
            "unique_senders": len(values["senders"]),
            "nonempty_memo_inflow_count": values["nonempty_memo_inflow_count"],
            "numeric_memo_inflow_count": values["numeric_memo_inflow_count"],
            "unique_numeric_memo_count": len(values["numeric_memos"]),
            "structured_memo_inflow_count": values["structured_memo_inflow_count"],
            "unique_structured_memo_count": len(values["structured_memos"]),
            "direct_outflow_count": values["direct_outflow_count"],
            "direct_outflow_uatom": values["direct_outflow_uatom"],
            "direct_outflow_atom": values["direct_outflow_uatom"] / 1_000_000,
            "net_direct_atom": (values["direct_inflow_uatom"] - values["direct_outflow_uatom"]) / 1_000_000,
            "event_window_inflow_count": values["event_window_inflow_count"],
            "event_window_inflow_atom": values["event_window_inflow_uatom"] / 1_000_000,
            "six_hour_inflow_count": values["six_hour_inflow_count"],
            "six_hour_inflow_atom": values["six_hour_inflow_uatom"] / 1_000_000,
        }
        tier = candidate_tier(stats)
        if tier is None:
            continue
        stats["candidate_tier"] = tier
        stats["candidate_score"] = candidate_score(stats)
        stats["public_exchange_label"] = None
        stats["label_status"] = "unconfirmed_behavioral_candidate"
        stats["largest_inflows"] = sorted(values["largest_inflows"], key=lambda item: item["amount_uatom"], reverse=True)[:5]
        candidates.append(stats)
    candidates.sort(key=lambda item: (-item["candidate_score"], -item["direct_inflow_uatom"], item["address"]))

    candidate_document = {
        "study_id": run_manifest["study_id"],
        "chain_id": "cosmoshub-4",
        "window": "[2025-10-09T00:00:00Z, 2025-10-13T00:00:00Z)",
        "status": "behavioral candidates; not confirmed exchange ownership",
        "candidate_count": len(candidates),
        "rules": {
            "behavioral_high": "at least 3 structured routing memo inflows, 3 unique senders, and 5 direct bank inflows",
            "behavioral_medium": "at least 1 structured routing memo inflow plus repeat/multi-sender activity, or at least 20 inflows from 10 senders",
            "large_flow_watchlist": "at least one event-window inflow and at least 10,000 ATOM total direct inflow, but no sufficient structured-memo evidence; kept separate from exchange-like candidates",
            "numeric_deposit_style_memo": "transaction memo matches ^[0-9]{4,20}$",
            "structured_routing_style_memo": "numeric deposit-style memo or 16-32 hex characters containing at least one A-F letter",
            "scope": "successful direct MsgSend/MsgMultiSend uatom transfer events only; fees, staking, rewards, and IBC escrow are excluded",
        },
        "candidates": candidates,
    }
    candidate_path.write_text(json.dumps(candidate_document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    artifacts = {}
    for path, count in [
        (atom_path, atom_output.count),
        (ibc_path, ibc_output.count),
        (event_path, event_output.count),
        (candidate_path, len(candidates)),
    ]:
        artifacts[path.name] = {
            "local_path": path.relative_to(PROJECT_ROOT).as_posix(),
            "record_count": count,
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }

    finished = datetime.now(timezone.utc)
    summary = {
        "study_id": run_manifest["study_id"],
        "chain_id": "cosmoshub-4",
        "started_at_utc": timestamp_text(started),
        "finished_at_utc": timestamp_text(finished),
        "source_run_manifest": str(args.run_manifest),
        "source_run_manifest_sha256": sha256_file(args.run_manifest),
        "transactions_scanned": tx_count,
        "successful_transactions_scanned": successful_tx_count,
        "protobuf_decode_errors": protobuf_decode_errors,
        "packet_decode_errors": packet_decode_errors,
        "atom_transfer_class_counts": dict(sorted(atom_class_counts.items())),
        "ibc_direction_counts": dict(sorted(ibc_direction_counts.items())),
        "ibc_atom_direction_counts": dict(sorted(ibc_atom_direction_counts.items())),
        "exchange_candidate_count": len(candidates),
        "artifacts": artifacts,
        "limitations": [
            "Behavioral exchange candidates are not proof of exchange ownership.",
            "A numeric memo is consistent with shared-account deposit routing but is not unique to exchanges.",
            "IBC chain hints use bech32 address prefixes and may be absent or ambiguous.",
            "Association in time does not establish causation for the market-price event.",
        ],
    }
    summary_path = PROJECT_ROOT / "results" / "cosmoshub_flow_extraction_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
