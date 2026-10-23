import copy
import json
from pathlib import Path
import tempfile
import unittest

from ledger_v1 import Ledger, SIZES
from processed_evidence_join_v1 import EvidenceIndex, ledger_input, run, snapshot_tail


def receipt():
    return {'kind': 'rustNativePumpCurveTransactionReceipt', 'signature': 'sig',
            'slot': 10, 'bonding_curve': 'curve', 'mint': 'mint', 'side': 'buy',
            'instruction_index': 0, 'native_receive_unix_ns': 1_000_000_001}


def original(r):
    return {'kind': r['kind'], 'signature': r['signature'], 'slot': r['slot'],
            'bondingCurve': r['bonding_curve'], 'mint': r['mint'], 'side': r['side'],
            'instructionIndex': r['instruction_index'],
            'nativeReceiveUnixNs': r['native_receive_unix_ns']}


def account(**changes):
    row = {'kind': 'rustNativePumpCurveAccountState', 'accountTxnSignature': 'sig',
           'slot': 10, 'bondingCurve': 'curve', 'nativeReceiveUnixNs': 1_100_000_000,
           'decodeCompleteUnixNs': 1_100_010_000, 'writeVersion': 9,
           'virtualSolReserves': 1000, 'virtualTokenReserves': 2000,
           'realSolReserves': 500, 'realTokenReserves': 1500,
           'tokenTotalSupply': 3000, 'complete': False}
    row.update(changes)
    return row


def index(*accounts, raw=True):
    result = EvidenceIndex()
    if raw:
        result.add(original(receipt()), 1)
    for line, row in enumerate(accounts, 2):
        result.add(row, line)
    return result


class JoinTests(unittest.TestCase):
    def test_exact_join_never_certifies_economics(self):
        result = index(account()).join(receipt(), 1)
        self.assertEqual(result['status'], 'IDENTITY_AND_CLOCK_MATCH_ONLY')
        self.assertFalse(result['economic_admission'])
        self.assertIsNone(result['execution_success'])
        self.assertIsNone(result['executed_flow'])
        self.assertEqual(result['state_ref']['available_ns'], 1_100_010_000)

    def test_each_identity_component_is_required(self):
        for field, value in [('accountTxnSignature', 'other'), ('slot', 11), ('bondingCurve', 'other')]:
            with self.subTest(field=field):
                result = index(account(**{field: value})).join(receipt(), 1)
                self.assertIn('account_not_in_sample', result['reasons'])

    def test_decode_not_receive_time_controls_boundary(self):
        boundary = receipt()['native_receive_unix_ns'] + 1_000_000_000
        self.assertEqual(index(account(decodeCompleteUnixNs=boundary)).join(receipt(), 1)['status'],
                         'IDENTITY_AND_CLOCK_MATCH_ONLY')
        result = index(account(decodeCompleteUnixNs=boundary+1)).join(receipt(), 1)
        self.assertIn('account_after_boundary', result['reasons'])

    def test_future_append_cannot_rescue_or_change_boundary(self):
        source = index(account())
        before = source.join(receipt(), 1)
        source.add(account(writeVersion=10, nativeReceiveUnixNs=3_000_000_000,
                           decodeCompleteUnixNs=3_000_000_010), 3)
        self.assertEqual(before, source.join(receipt(), 1))
        missing = index()
        missing.add(account(nativeReceiveUnixNs=3_000_000_000, decodeCompleteUnixNs=3_000_000_010), 3)
        self.assertIsNone(missing.join(receipt(), 1)['state_ref'])

    def test_repeated_delivery_is_not_distinct_write(self):
        result = index(account(), account(nativeReceiveUnixNs=1_200_000_000,
                                         decodeCompleteUnixNs=1_200_000_010)).join(receipt(), 1)
        self.assertEqual(result['status'], 'IDENTITY_AND_CLOCK_MATCH_ONLY')
        self.assertEqual(result['state_ref']['line'], 2)

    def test_conflicts_and_multiple_writes_rejected(self):
        for other, reason in [(account(realSolReserves=4), 'conflicting_account_payloads'),
                              (account(writeVersion=10), 'ambiguous_or_missing_write_version')]:
            self.assertIn(reason, index(account(), other).join(receipt(), 1)['reasons'])

    def test_invalid_and_nonmonotonic_clocks(self):
        for changes in [{'nativeReceiveUnixNs': True}, {'decodeCompleteUnixNs': None},
                        {'decodeCompleteUnixNs': 1}]:
            self.assertIn('invalid_account_clock', index(account(**changes)).join(receipt(), 1)['reasons'])
            self.assertIn('invalid_account_clock', index(account(), account(**changes)).join(receipt(), 1)['reasons'])
        source = index(account(accountTxnSignature='other', nativeReceiveUnixNs=1_300_000_000), account())
        self.assertIn('nonmonotonic_account_receipt', source.join(receipt(), 1)['reasons'])

    def test_phase_and_reserve_failures(self):
        for changes, reason in [({'complete': True}, 'complete_or_unknown_phase'),
                                ({'complete': None}, 'complete_or_unknown_phase'),
                                ({'realSolReserves': -1}, 'invalid_reserves'),
                                ({'realTokenReserves': True}, 'invalid_reserves'),
                                ({'writeVersion': []}, 'ambiguous_or_missing_write_version')]:
            self.assertIn(reason, index(account(**changes)).join(receipt(), 1)['reasons'])

    def test_original_receipt_required_and_wrong_side_does_not_match(self):
        self.assertIn('original_receipt_not_in_sample_or_not_equal',
                      index(account(), raw=False).join(receipt(), 1)['reasons'])
        r = receipt()
        r['side'] = 'sell'
        self.assertIn('original_receipt_not_in_sample_or_not_equal',
                      index(account()).join(r, 1)['reasons'])

    def test_missing_route_not_inferred_from_account(self):
        r = receipt()
        r['bonding_curve'] = None
        self.assertIn('missing_exact_signature_slot_curve', index(account()).join(r, 1)['reasons'])

    def test_ledger_integration_preserves_all_sizes_and_missing_seeds(self):
        source = Ledger('test')
        r = receipt()
        records = source.consume(ledger_input(r), 1)
        self.assertEqual({v['notional_lamports'] for v in records[1:]}, set(SIZES))
        self.assertEqual(records[0]['decision_ready_ms'], 1001)  # never round earlier
        self.assertTrue(all(v['realized_pnl_lamports'] is None for v in records[1:]))
        r.update(mint='second', signature='second')
        self.assertEqual(len(source.consume(ledger_input(r), 2)), 4)

    def test_teacher_label_not_admission_feature(self):
        a, b = receipt(), receipt()
        a['teacher_wallet_match'], b['teacher_wallet_match'] = True, False
        left = index(account()).join(a, 1)
        right = index(account()).join(b, 1)
        left.pop('receipt_sha256'); right.pop('receipt_sha256')
        self.assertEqual(left, right)

    def test_snapshot_boundaries_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'source'
            source.write_bytes(b'first\nsecond\npartial')
            report = snapshot_tail(source, root/'sealed', 16)
            self.assertEqual((root/'sealed').read_bytes(), b'second\n')
            self.assertEqual(report['unsealed_tail_bytes_excluded'], 7)
            with self.assertRaises(FileExistsError):
                snapshot_tail(source, root/'sealed', 16)

    def test_cli_roundtrip_is_deterministic_and_reports_invalid_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'native-pump-account-state-events.jsonl').write_text(
                '\n'.join(json.dumps(x) for x in [original(receipt()), account()])+'\n')
            r2 = copy.deepcopy(receipt()); r2.update(mint='second', signature='second')
            (root/'native-yellowstone-pump-transaction-receipts-v1.jsonl').write_text(
                '\n'.join(json.dumps(x) for x in [receipt(), r2, receipt()])+'\nBAD\n')
            a = run(root, root/'a', 'population')
            b = run(root, root/'b', 'population')
            self.assertEqual(a, b)
            replay = run(root/'a', root/'replay', 'population', sealed_inputs=True)
            self.assertEqual(a['output_hashes'], replay['output_hashes'])
            self.assertEqual(a['join_statuses']['IDENTITY_AND_CLOCK_MATCH_ONLY'], 2)
            self.assertEqual(a['join_statuses']['INVALID_RECEIPT'], 1)
            self.assertEqual(a['ledger_counts']['opportunities'], 2)
            self.assertEqual(a['ledger_counts']['attempt_rows'], 6)
            self.assertEqual(a['economic_admissions'], 0)
            self.assertEqual(len((root/'a/joins.jsonl').read_text().splitlines()), 4)
            with self.assertRaises(FileExistsError):
                run(root, root/'a', 'population')


if __name__ == '__main__':
    unittest.main()
