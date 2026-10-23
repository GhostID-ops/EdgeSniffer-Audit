import json
import tempfile
import unittest
from pathlib import Path

from auditNativeCausalReplayBridgeV1 import audit


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


class CausalReplayBridgeTests(unittest.TestCase):
    def test_replays_from_receipt_state_and_marks_finalized_lookahead(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            signature = "tx"
            curve = "curve"
            native = {
                "signature": signature,
                "bonding_curve": curve,
                "slot": 7,
                "same_source_transaction_index": 1,
                "same_source_receive_unix_ns": 100,
                "native_state": {
                    "pre_state": {"virtual_sol": 1000, "virtual_token": 10000, "real_sol": 1000, "real_token": 10000},
                    "post_state": {"virtual_sol": 1100, "virtual_token": 9100, "real_sol": 1100, "real_token": 9100},
                    "protocol_fee_basis_points": 0,
                    "creator_fee_basis_points": 0,
                    "lp_fee_basis_points": 0,
                    "seed_native_receive_unix_ns": 90,
                    "seed_native_receive_source": "account",
                    "seed_account_txn_signature": "old",
                    "seed_slot": 7,
                    "seed_write_version": 1,
                },
            }
            finalized = {
                "signature": signature,
                "slot": 7,
                "pre_state": {"virtual_sol": 1200, "virtual_token": 8334, "real_sol": 1200, "real_token": 8334},
                "post_state": {"virtual_sol": 1300, "virtual_token": 7693, "real_sol": 1300, "real_token": 7693},
            }
            seed = {
                "bondingCurve": curve,
                "slot": 7,
                "native_receive_unix_ns": 110,
                "virtualSolReserves": 1200,
                "virtualTokenReserves": 8334,
                "account_txn_signature": "later",
                "write_version": 2,
            }
            tx = {
                "signature": signature,
                "account_keys": [curve, "fee"],
                "instructions": [{
                    "program_id": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
                    "accounts": [0, 1],
                    "data_hex": "66" + "00" * 23,
                }],
            }
            # Use a real supported buy discriminator and exact-input argument.
            tx["instructions"][0]["data_hex"] = "38fc74089ed fcd5f".replace(" ", "") + (100).to_bytes(8, "little").hex() + (1).to_bytes(8, "little").hex()
            write_jsonl(root / "native.jsonl", [native])
            write_jsonl(root / "finalized.jsonl", [finalized])
            write_jsonl(root / "seeds.jsonl", [seed])
            write_jsonl(root / "tx.jsonl", [tx])
            result = audit(root / "native.jsonl", root / "finalized.jsonl", root / "seeds.jsonl", root / "tx.jsonl", root / "out.json")
            self.assertEqual(result["summary"]["instruction_replay_exact_rows"], 1)
            self.assertEqual(result["summary"]["category_counts"], {"FINALIZED_PRE_LOOKAHEAD_ONLY": 1})


if __name__ == "__main__":
    unittest.main()
