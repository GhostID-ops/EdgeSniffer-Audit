import copy
import json
from pathlib import Path
import tempfile
import unittest
from ledger_v1 import Ledger, run, SIZES


def buy(mint='a', oid='s1', ts=1000):
    return {'mint': mint, 'side': 'buy', 'observation_id': oid,
            'local_receive_ts_ms': ts, 'route': 'PUMP_CURVE'}


class LedgerTests(unittest.TestCase):
    def test_incomplete_seed_survives_every_size(self):
        rows = Ledger('p').consume(buy(), 1)
        self.assertEqual(len(rows), 4)
        self.assertEqual({r['notional_lamports'] for r in rows[1:]}, set(SIZES))
        self.assertEqual({r['opportunity_id'] for r in rows}, {rows[0]['opportunity_id']})
        self.assertTrue(all(r['realized_pnl_lamports'] is None for r in rows[1:]))

    def test_future_enrichment_cannot_replace_original_decision(self):
        ledger = Ledger('p')
        prefix = ledger.consume(buy(), 1)
        saved = copy.deepcopy(prefix)
        later = buy(oid='s2', ts=2000)
        later['execution'] = {'success': True}
        self.assertEqual(ledger.consume(later, 2), [])
        self.assertEqual(prefix, saved)
        self.assertEqual(Ledger('p').consume(buy(), 1), saved)

    def test_teacher_identity_does_not_change_admission(self):
        a, b = buy(), buy()
        a['teacher_wallet_match'] = True
        b['teacher_wallet_match'] = False
        x, y = Ledger('p').consume(a, 1), Ledger('p').consume(b, 1)
        for left, right in zip(x, y):
            left.pop('source_sha256', None)
            right.pop('source_sha256', None)
            self.assertEqual(left, right)

    def test_duplicate_and_conflict_are_distinct(self):
        ledger = Ledger('p')
        ledger.consume(buy(), 1)
        self.assertEqual(ledger.consume(buy(), 2), [])
        conflict = buy(mint='other')
        self.assertEqual(ledger.consume(conflict, 3)[0]['reason'], 'identity_content_conflict')

    def test_future_fee_and_state_do_not_admit(self):
        row = buy()
        row.update(state_available_at_ms=2000, fee_available_at_ms=2000,
                   execution={'success': True}, native_economics_state_complete=True)
        result = Ledger('p').consume(row, 1)[0]
        self.assertIn('unavailable_at_decision_state_available_at_ms', result['reasons'])
        self.assertIn('unavailable_at_decision_fee_available_at_ms', result['reasons'])
        self.assertEqual(result['status'], 'BLOCKED_EVIDENCE')

    def test_cli_artifacts_preserve_bad_input_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, out = root/'input', root/'out'
            source.write_text(json.dumps(buy())+'\nBROKEN\n'+json.dumps(buy('b','s2',2000))+'\n')
            result = run(source, out, 'p')
            rows = [json.loads(x) for x in (out/'ledger.jsonl').read_text().splitlines()]
            self.assertEqual(len(rows), 9)
            self.assertEqual(result['counts']['opportunities'], 2)
            self.assertEqual(result['counts']['attempt_rows'], 6)
            self.assertEqual(result['counts']['invalid_json_or_value_rows'], 1)
            with self.assertRaises(FileExistsError):
                run(source, out, 'p')


if __name__ == '__main__':
    unittest.main()
