from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from shadow_observer import FollowFile, Shadow, TARGET, integrity_defaults


SIG = "signature-exact"
MINT = "mint-exact"


class MatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.shadow = Shadow.__new__(Shadow)
        self.shadow.integrity_counts = integrity_defaults()
        self.shadow.decoder_errors = 0
        self.shadow.dropped_rows = 0
        self.shadow.rejection_path = Path(self.temp.name) / "rejections.jsonl"

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def create(slot=443866447, signature=SIG, mint=MINT):
        return {"signature": signature, "mint": mint, "source_slot": slot, "source_transaction_index": 44}

    @staticmethod
    def atomic(slot=443866447, signature=SIG, mint=MINT):
        return {"signature": signature, "mint": mint, "slot": slot, "tx_index": 44, "wallet": TARGET, "side": "buy", "quote_lamports": 1}

    def test_a_integer_integer_matches(self):
        self.assertTrue(self.shadow.atomic_matches(self.create(443866447), self.atomic(443866447)))

    def test_b_integer_numeric_string_matches(self):
        self.assertTrue(self.shadow.atomic_matches(self.create(443866447), self.atomic("443866447")))

    def test_c_numeric_string_integer_matches(self):
        self.assertTrue(self.shadow.atomic_matches(self.create("443866447"), self.atomic(443866447)))

    def test_d_both_numeric_strings_match(self):
        self.assertTrue(self.shadow.atomic_matches(self.create("443866447"), self.atomic("443866447")))

    def test_e_different_slots_do_not_match(self):
        self.assertFalse(self.shadow.atomic_matches(self.create(443866447), self.atomic("443866448")))

    def test_f_malformed_slot_fails_closed_and_counts(self):
        self.assertFalse(self.shadow.atomic_matches(self.create("443866447oops"), self.atomic(443866447)))
        self.assertEqual(self.shadow.integrity_counts["malformed_numeric_fields"], 1)
        self.assertEqual(self.shadow.integrity_counts["signal_affecting_dropped_rows"], 1)
        self.assertEqual(self.shadow.integrity_counts["rejected_pair_reasons"]["malformed_numeric_match_field"], 1)
        row = json.loads(self.shadow.rejection_path.read_text(encoding="utf-8"))
        self.assertEqual(row["category"], "malformed_numeric_field")

    def test_g_different_mint_does_not_match(self):
        self.assertFalse(self.shadow.atomic_matches(self.create(), self.atomic(mint="other-mint")))

    def test_h_different_signature_does_not_match(self):
        self.assertFalse(self.shadow.atomic_matches(self.create(), self.atomic(signature="other-signature")))

    def test_i_known_missed_event_qualifies(self):
        fixture = Path(__file__).parent / "fixtures"
        create_row = json.loads((fixture / "missed_event_global_rows.jsonl").read_text(encoding="utf-8").splitlines()[0])
        wallet_row = json.loads((fixture / "missed_event_wallet_rows.jsonl").read_text(encoding="utf-8").splitlines()[0])
        swap = wallet_row["localSwaps"][0]
        create = {
            "signature": create_row["transaction_signature"], "mint": create_row["mint"],
            "source_slot": create_row["source_slot"], "source_transaction_index": create_row["source_transaction_index"],
        }
        atomic = {
            "signature": wallet_row["signature"], "mint": swap["mint"], "slot": wallet_row["slot"],
            "tx_index": None, "wallet": swap["wallet"], "side": swap["side"],
            "quote_lamports": round(swap["sourceBuySol"] * 1e9),
        }
        self.assertTrue(self.shadow.atomic_matches(create, atomic))


class CompleteLineFollowerTests(unittest.TestCase):
    def test_partial_eof_is_not_consumed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "feed.jsonl"
            path.write_bytes(b'{"slot":"443866')
            follower = FollowFile(path, 0)
            self.assertEqual(follower.read_available(), [])
            self.assertEqual(follower.position, 0)
            with path.open("ab") as handle:
                handle.write(b'447"}\n')
            rows = follower.read_available()
            self.assertEqual(len(rows), 1)
            self.assertEqual(json.loads(rows[0][0]), {"slot": "443866447"})
            follower.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
