"""Run: python3 -m unittest discover -s scripts -p test_ibc_receive_evidence.py."""
import json
import unittest

from ibc_receive_evidence import assess_receives, forwarding_receiver
from collect_cosmos_baseline_indexed import ibc_events


def event(kind, **attrs):
    return {"type": kind, "attributes": [{"key": k, "value": str(v)} for k, v in attrs.items()]}


def fixture(success="true", ack=None, native=True, memo="", wrong_receiver=False):
    data = {"sender": "osmo1original", "receiver": "cosmos1destination", "amount": "1234567",
            "denom": "transfer/channel-0/uatom" if native else "transfer/channel-99/uatom", "memo": memo}
    packet = dict(packet_src_port="transfer", packet_src_channel="channel-0", packet_dst_port="transfer",
                  packet_dst_channel="channel-141", packet_sequence="7", msg_index="1")
    receiver = forwarding_receiver("channel-141", data["sender"]) if memo else data["receiver"]
    if wrong_receiver:
        receiver = "cosmos1wrong"
    result = [event("recv_packet", **packet, packet_data_hex=json.dumps(data).encode().hex()),
              event("fungible_token_packet", **{**data, "receiver": receiver}, success=success, msg_index="1"),
              event("transfer", sender="cosmos1escrow", recipient=receiver, amount="1234567uatom", msg_index="1")]
    if ack is not None:
        result.append(event("write_acknowledgement", **packet, packet_ack_hex=json.dumps(ack).encode().hex()))
    return result


class ReceiveEvidenceTests(unittest.TestCase):
    def test_success_ack(self):
        self.assertTrue(assess_receives(fixture(ack={"result": "AQ=="}))[0]["include_in_atom_flow"])

    def test_success_without_ack(self):
        self.assertTrue(assess_receives(fixture())[0]["include_in_atom_flow"])

    def test_failed_application_even_code_zero(self):
        events = fixture(success="false", ack={"error": "failed"})
        self.assertFalse(assess_receives(events)[0]["include_in_atom_flow"])
        rows, errors = ibc_events({"height": "1", "hash": "X", "tx_result": {"code": 0, "events": events}}, "inbound")
        self.assertEqual((rows, errors), ([], 0))

    def test_success_event_cannot_override_error_ack(self):
        self.assertFalse(assess_receives(fixture(ack={"error": "rollback"}))[0]["include_in_atom_flow"])

    def test_ack_without_success_event_is_insufficient(self):
        self.assertFalse(assess_receives([e for e in fixture(ack={"result": "AQ=="}) if e["type"] != "fungible_token_packet"])[0]["include_in_atom_flow"])

    def test_native_credit_required(self):
        self.assertFalse(assess_receives(fixture()[:2])[0]["include_in_atom_flow"])

    def test_foreign_uatom_suffix_not_native(self):
        self.assertFalse(assess_receives(fixture(native=False))[0]["include_in_atom_flow"])

    def test_no_cross_message_credit_matching(self):
        events = fixture()
        events[2]["attributes"][-1]["value"] = "2"
        self.assertFalse(assess_receives(events)[0]["include_in_atom_flow"])

    def test_wrong_receiver_does_not_match(self):
        self.assertFalse(assess_receives(fixture(wrong_receiver=True))[0]["include_in_atom_flow"])

    def test_forwarding_receiver_and_deferred_ack(self):
        decision = assess_receives(fixture(memo='{"forward":{"channel":"channel-623","port":"transfer","receiver":"archway1destination"}}'))[0]
        self.assertTrue(decision["include_in_atom_flow"])
        self.assertTrue(decision["packet_forwarding"])

    def test_forwarding_known_chain_fixture(self):
        self.assertEqual(forwarding_receiver("channel-141", "osmo1a8x9yyvv9lhhu4aduxvrfqxc23qtg5809m7tsy"),
                         "cosmos1pkx8mtr665taxex2hggy3h9av6a95hrl5c35s6")

    def test_one_credit_not_reused(self):
        events = fixture()
        events.append(events[0])
        self.assertEqual(sum(r["include_in_atom_flow"] for r in assess_receives(events)), 1)

    def test_two_success_events_one_credit(self):
        events = fixture()
        events.extend([events[0], events[1]])
        self.assertEqual(sum(r["include_in_atom_flow"] for r in assess_receives(events)), 1)

    def test_one_success_event_two_credits(self):
        events = fixture()
        events.extend([events[0], events[2]])
        self.assertEqual(sum(r["include_in_atom_flow"] for r in assess_receives(events)), 1)

    def test_malformed_ack_json_type_flagged(self):
        for ack in ([], "not an ACK", 123):
            with self.subTest(ack=ack):
                decision = assess_receives(fixture(ack=ack))[0]
                self.assertEqual(decision["immediate_ack_states"], ["undecodable"])
                self.assertTrue(decision["include_in_atom_flow"])

    def test_packet_decode_error_retained(self):
        events = fixture()
        events[0]["attributes"][-1]["value"] = "not hex"
        self.assertEqual(assess_receives(events)[0]["exclusion_reason"], "packet_decode_error")


if __name__ == "__main__":
    unittest.main()
