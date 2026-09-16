#!/usr/bin/env python3

import hashlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from shadow_observer import Shadow, TARGET


HERE = Path(__file__).resolve().parent


class ShadowObserverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        recurrent = {"top_repeated_wallets": [{"wallet": "repeat-wallet", "target_launches_bought_within_1s": 5}]}
        recurrent_path = self.root / "v3_recurrent_wallets.json"
        recurrent_path.write_text(json.dumps(recurrent), encoding="utf-8")
        recurrent_hash = hashlib.sha256(recurrent_path.read_bytes()).hexdigest()
        raw = (HERE / "frozen_spec_template.json").read_text(encoding="utf-8")
        raw = raw.replace("__COHORT_START_UTC__", "2000-01-01T00:00:00.000000Z")
        raw = raw.replace("__RECURRENT_SOURCE_SHA256__", recurrent_hash)
        self.spec_path = self.root / "frozen_spec.json"
        self.spec_path.write_text(raw, encoding="utf-8")
        self.global_feed = self.root / "global.jsonl"
        self.wallet_feed = self.root / "wallet.jsonl"
        self.global_feed.touch()
        self.wallet_feed.touch()
        os.environ["NO_SUBMIT_HARD_GUARD"] = "8YCB_OBSERVATION_ONLY_V1"
        self.shadow = Shadow(self.spec_path, self.root / "out", self.global_feed, self.wallet_feed)

    def tearDown(self) -> None:
        self.shadow.global_tail.handle.close()
        self.shadow.wallet_tail.handle.close()
        self.temp.cleanup()

    @staticmethod
    def observation(signature: str, mint: str, side: str, wallet=None, quote=0, tokens=0):
        now = time.time() * 1000
        return {
            "observation_id": f"{signature}:{side}:{wallet}",
            "transaction_signature": signature,
            "mint": mint,
            "side": side,
            "wallet": wallet,
            "quote_lamports": quote,
            "sell_quote_lamports": quote if side == "sell" else None,
            "sell_token_amount_raw": tokens if side == "sell" else None,
            "creation_proven": side == "create",
            "source_slot": 123,
            "source_transaction_index": 4,
            "local_receive_ts_ms": now,
            "decode_complete_ts_ms": now,
            "raw_record": {"instructionIndex": 1},
        }

    def emit_global(self, row):
        self.shadow.process_global(json.dumps(row), time.time_ns())

    def test_target_detection_control_exclusion_enrichment_and_control_atomic(self):
        self.emit_global(self.observation("sig-a", "mint-a", "create"))
        self.emit_global(self.observation("sig-a", "mint-a", "buy", TARGET, 500_000_000))
        self.assertEqual(len(self.shadow.signal_rows), 1)

        self.emit_global(self.observation("sig-control", "mint-control", "create"))
        self.emit_global(self.observation("sig-control", "mint-control", "buy", "creator-control", 250_000_000))
        control = next(row for row in self.shadow.generic_rows if row["mint"] == "mint-control")
        self.assertEqual(self.shadow.active[control["event_id"]]["atomic_quote_lamports"], 250_000_000)

        self.emit_global(self.observation("sig-b", "mint-b", "create"))
        selected_target_control = next(row for row in self.shadow.generic_rows if row["mint"] == "mint-b")
        self.emit_global(self.observation("sig-b", "mint-b", "buy", TARGET, 400_000_000))
        self.assertIn(selected_target_control["event_id"], self.shadow.excluded_control_ids)
        self.assertNotIn(selected_target_control["event_id"], self.shadow.active)

        wallet_row = {
            "kind": "wallet_transaction_observation",
            "provider": "shyft",
            "signature": "sig-a",
            "slot": 123,
            "providerReceivedAtMs": time.time() * 1000,
            "callbackUnixNs": str(time.time_ns()),
            "callbackMonotonicNs": str(time.monotonic_ns()),
            "localSwaps": [{
                "wallet": TARGET, "mint": "mint-a", "side": "buy", "sourceBuySol": 0.5,
                "tokensOutRaw": "123456789", "timestamp": int(time.time()),
            }],
        }
        self.shadow.process_wallet(json.dumps(wallet_row), time.time_ns())
        self.assertEqual(self.shadow.signal_rows[0]["atomic_initial_buy"]["tokens_out_raw"], 123456789)
        self.assertIsNotNone(self.shadow.signal_rows[0]["timing"]["chain_or_block_timestamp_ms"])

        self.shadow.write_outputs()
        for name in (
            "frozen_spec.json", "cohort_manifest.json", "signal_events.jsonl.gz",
            "entry_counterfactuals.jsonl.gz", "outcome_events.jsonl.gz",
            "target_sell_collisions.jsonl.gz", "external_flow.jsonl.gz",
            "generic_controls.jsonl.gz", "checkpoints.json", "service_health.json", "final_report.json",
        ):
            self.assertTrue((self.root / "out" / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
