import unittest

from joinNativeReceiptsToFinalizedWalletV1 import load_jsonl


class JoinNativeReceiptTests(unittest.TestCase):
    def test_module_loads(self):
        self.assertEqual(load_jsonl(__import__("pathlib").Path("does-not-exist.jsonl")), [])


if __name__ == "__main__":
    unittest.main()
