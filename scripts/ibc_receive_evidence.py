"""IBC receive eligibility shared by event and baseline extraction.

A successful relay transaction is NOT proof of application-level success.
Native ATOM receipts require the exact returning denom trace, a matching
successful ICS-20 application event, and a matching native bank credit.
Immediate ACKs are informative, not mandatory: middleware may ACK later.
"""
from __future__ import annotations

import json
import hashlib

IBC_INBOUND_POLICY = "native-atom-receipt-success-v2"


def forwarding_receiver(channel, sender):
    """PFM GetReceiver: SDK address.Hash(module name, channel/sender)[:20].

    Matches the retained Hub events, including the upstream module-name typo.
    References: cosmos/ibc-apps packetforward/ibc_middleware.go:GetReceiver and
    types/keys.go; cosmos/cosmos-sdk types/address/hash.go:Hash.
    """
    tag = hashlib.sha256(b"packetfowardmiddleware").digest()
    raw = hashlib.sha256(tag + f"{channel}/{sender}".encode()).digest()[:20]
    alphabet = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
    bits = "".join(f"{byte:08b}" for byte in raw)
    words = [int(bits[i:i + 5].ljust(5, "0"), 2) for i in range(0, len(bits), 5)]
    prefix = "cosmos"
    values = [ord(c) >> 5 for c in prefix] + [0] + [ord(c) & 31 for c in prefix] + words + [0] * 6
    check = 1
    for value in values:
        top = check >> 25
        check = ((check & 0x1FFFFFF) << 5) ^ value
        for bit, generator in enumerate((0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)):
            if (top >> bit) & 1:
                check ^= generator
    check ^= 1
    return prefix + "1" + "".join(alphabet[v] for v in words + [(check >> (5 * (5 - i))) & 31 for i in range(6)])


def attributes(event):
    return {item["key"]: item["value"] for item in event.get("attributes", [])}


def packet_key(attrs):
    return tuple(attrs.get(k) for k in (
        "packet_src_port", "packet_src_channel", "packet_dst_port",
        "packet_dst_channel", "packet_sequence", "msg_index"))


def assess_receives(events):
    """Return one auditable decision per transfer recv_packet (including rejects).

    Application and bank events are consumed once, preventing one success/credit
    from validating multiple packets with identical data in a wrapped message.
    """
    indexed = [(i, e["type"], attributes(e)) for i, e in enumerate(events)]
    consumed_success, consumed_credit = set(), set()
    decisions = []
    for ordinal, kind, attrs in indexed:
        if kind != "recv_packet" or attrs.get("packet_src_port") != "transfer" or attrs.get("packet_dst_port") != "transfer":
            continue
        result = {"event_ordinal": ordinal, "packet_attributes": attrs,
                  "policy": IBC_INBOUND_POLICY, "packet_data": None,
                  "application_success": False, "native_atom_trace": False,
                  "native_credit_matched": False, "include_in_atom_flow": False}
        try:
            data = json.loads(bytes.fromhex(attrs["packet_data_hex"]).decode("utf-8"))
            amount = int(data["amount"])
            if amount < 0 or not isinstance(data["denom"], str):
                raise ValueError("invalid packet amount/denom")
            for key in ("sender", "receiver"):
                if not isinstance(data[key], str):
                    raise ValueError("invalid packet address")
        except (KeyError, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            result["exclusion_reason"] = "packet_decode_error"
            decisions.append(result)
            continue
        result["packet_data"] = data
        msg = attrs.get("msg_index")
        expected_receiver = data["receiver"]
        try:
            forward = json.loads(data.get("memo", "")).get("forward")
        except (ValueError, TypeError, AttributeError):
            forward = None
        if isinstance(forward, dict):
            expected_receiver = forwarding_receiver(attrs["packet_dst_channel"], data["sender"])
        result["application_receiver"] = expected_receiver
        result["packet_forwarding"] = isinstance(forward, dict)
        trace = f"{attrs['packet_src_port']}/{attrs['packet_src_channel']}/uatom"
        result["native_atom_trace"] = data["denom"] == trace
        ack_states = []
        for _, typ, a in indexed:
            if typ != "write_acknowledgement" or packet_key(a) != packet_key(attrs):
                continue
            try:
                ack = json.loads(bytes.fromhex(a["packet_ack_hex"]).decode())
                if not isinstance(ack, dict):
                    raise ValueError("ACK must be an object")
                ack_states.append("error" if "error" in ack else "success" if ack.get("result") == "AQ==" else "other")
            except (KeyError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                ack_states.append("undecodable")
        result["immediate_ack_states"] = ack_states
        matching = [(i, a) for i, typ, a in indexed if typ == "fungible_token_packet"
                    and a.get("msg_index") == msg and msg is not None
                    and all(a.get(key) == str(data[key]) for key in ("denom", "amount", "sender"))
                    and a.get("receiver") == expected_receiver
                    and "success" in a]
        success = [i for i, a in matching if a["success"] == "true" and i not in consumed_success]
        explicit_error = "error" in ack_states or any(a["success"] == "false" for _, a in matching)
        if explicit_error:
            result["exclusion_reason"] = "application_error"
        elif not success:
            result["exclusion_reason"] = "no_matching_success_event"
        else:
            result["application_success"] = True
            result["success_event_ordinal"] = success[0]
            consumed_success.add(success[0])
            credit = [i for i, typ, a in indexed if typ == "transfer" and i not in consumed_credit
                      and a.get("msg_index") == msg and a.get("recipient") == expected_receiver
                      and f"{amount}uatom" in a.get("amount", "").split(",")]
            if result["native_atom_trace"] and credit:
                result["native_credit_matched"] = True
                result["native_credit_event_ordinal"] = credit[0]
                consumed_credit.add(credit[0])
                result["include_in_atom_flow"] = True
                result["exclusion_reason"] = None
            elif not result["native_atom_trace"]:
                result["exclusion_reason"] = "not_native_atom_return_trace"
            else:
                result["exclusion_reason"] = "no_matching_native_uatom_credit"
        decisions.append(result)
    return decisions
