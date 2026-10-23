import importlib.util
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


SOURCE = pathlib.Path(__file__).with_name("twoCqAutonomousWhenV1.py")
SPEC = importlib.util.spec_from_file_location("when_lane", SOURCE)
when_lane = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(when_lane)


class DecoderRejectionTest(unittest.TestCase):
    def test_rejection_is_quarantined_and_later_event_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = when_lane.open_db(pathlib.Path(tmp) / "lane.sqlite")
            meta = {
                "signature": "bad-signature",
                "receivedAtUnixNs": 1_000_000_000,
                "slot": 123,
                "rawSha256": "a" * 64,
                "_laneSourcePath": "/retained/evidence.frames",
                "_laneOffset": 456,
            }
            tx = {"successfulAtReceipt": True, "transactionIndex": 0}
            with patch.object(when_lane.coverage, "canonical_create_mints", return_value=["bad-mint"]), patch.object(when_lane.coverage, "decode_causal_events", side_effect=ValueError("invalid Borsh bool 211")):
                when_lane.apply_rows(db, {}, [(meta, tx)], set())
            self.assertEqual(db.execute("select count(*) from decoder_rejections").fetchone()[0], 1)
            self.assertEqual(db.execute("select count(*) from tainted_mints").fetchone()[0], 1)

            state = SimpleNamespace(value=1)
            valid = SimpleNamespace(mint="good-mint", instruction_index=0, is_buy=True, user="external", creator="creator", quote_lamports=1, token_raw=1, pre_state=state, post_state=state)
            meta["signature"] = "good-signature"
            meta["rawSha256"] = "b" * 64
            with patch.object(when_lane.coverage, "canonical_create_mints", return_value=[]), patch.object(when_lane.coverage, "decode_causal_events", return_value=([valid], None)):
                when_lane.apply_rows(db, {}, [(meta, tx)], set())
            self.assertEqual(db.execute("select count(*) from events").fetchone()[0], 1)
            db.close()


if __name__ == "__main__":
    unittest.main()
