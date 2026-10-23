import json
import tempfile
import unittest
from pathlib import Path

from auditNativeParityMismatchAdjudicatorV1 import adjudicate


def native_row(sig: str, tx_ns: int, pre: tuple[int, int], post: tuple[int, int]) -> dict:
    return {
        "signature": sig,
        "bonding_curve": "curve",
        "slot": 7,
        "same_source_receive_unix_ns": tx_ns,
        "native_state": {
            "pre_state": {"virtual_sol": pre[0], "virtual_token": pre[1]},
            "post_state": {"virtual_sol": post[0], "virtual_token": post[1]},
            "seed_native_receive_unix_ns": tx_ns - 10,
            "seed_native_receive_source": "account",
            "seed_account_txn_signature": "seed-old",
            "seed_slot": 7,
            "seed_write_version": 1,
        },
    }


class AdjudicatorTests(unittest.TestCase):
    def test_later_seed_explains_finalized_pre(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "native.jsonl").write_text(json.dumps(native_row("tx", 100, (10, 20), (11, 19))) + "\n")
            (root / "finalized.jsonl").write_text(json.dumps({
                "signature": "tx", "slot": 7,
                "pre_state": {"virtual_sol": 15, "virtual_token": 18},
                "post_state": {"virtual_sol": 16, "virtual_token": 17},
            }) + "\n")
            (root / "seeds.jsonl").write_text(json.dumps({
                "bondingCurve": "curve", "slot": 7, "native_receive_unix_ns": 110,
                "virtualSolReserves": 15, "virtualTokenReserves": 18,
                "account_txn_signature": "seed-later", "write_version": 2,
            }) + "\n")
            result = adjudicate(root / "native.jsonl", root / "finalized.jsonl", root / "seeds.jsonl", root / "out.json")
            self.assertEqual(result["summary"]["category_counts"], {"FINALIZED_PRE_APPEARS_AFTER_RECEIPT": 1})
            self.assertEqual(result["summary"]["finalized_pre_found_only_after_receipt_rows"], 1)

    def test_exact_row_is_not_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "native.jsonl").write_text(json.dumps(native_row("tx", 100, (10, 20), (11, 19))) + "\n")
            (root / "finalized.jsonl").write_text(json.dumps({
                "signature": "tx", "slot": 7,
                "pre_state": {"virtual_sol": 10, "virtual_token": 20},
                "post_state": {"virtual_sol": 11, "virtual_token": 19},
            }) + "\n")
            (root / "seeds.jsonl").write_text("")
            result = adjudicate(root / "native.jsonl", root / "finalized.jsonl", root / "seeds.jsonl", root / "out.json")
            self.assertEqual(result["summary"]["category_counts"], {"EXACT_PRE_POST_MATCH": 1})


if __name__ == "__main__":
    unittest.main()
