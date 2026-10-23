import json
from pathlib import Path
import tempfile
import unittest
from event_continuation_v1 import interval, load, summarize
from test_burst_persistence_v1 import study, seed_rows, tx


class ContinuationTests(unittest.TestCase):
    def test_zero_activity_not_missing_price_or_pnl(self):
        s = study(seed_rows()+[tx(5000, 'other')])
        r = interval(s, 'm', 2_000_000_000, 4_000_000_000, {}, {})
        self.assertEqual(r['status'], 'OBSERVED_INTERVAL')
        self.assertEqual(r['observed_events'], 0)
        self.assertEqual(r['net_executed_sol_flow'], 0)
        self.assertNotIn('pnl', r)

    def test_full_entry_delay_and_no_future_events(self):
        s = study(seed_rows()+[tx(2100, user='x'), tx(2200, user='x'), tx(2300, user='x'), tx(5000, 'other')])
        r = interval(s, 'm', 2200_000_000, 4200_000_000, {}, {})
        self.assertEqual(r['observed_events'], 1)  # At-entry event belongs to entry state, not future flow.

    def test_unknown_and_end_censor_not_zero_observation(self):
        s = study(seed_rows()+[tx(4000, 'other')])
        self.assertEqual(interval(s, 'm', 2_000_000_000, 5_000_000_000, {}, {})['status'], 'RIGHT_CENSORED')
        self.assertEqual(interval(s, 'm', 2_000_000_000, 4_000_000_000, {'m':[3_000_000_000]}, {})['status'], 'AMBIGUOUS_CONTEXT')
        self.assertEqual(interval(s, None, 2, 3, {}, {})['status'], 'NO_CONTROL')

    def test_failed_attempts_never_flow_and_unknown_native_mentions_retained(self):
        rows = seed_rows()+[tx(2500, status='FAILED'), tx(2600, status='UNKNOWN'), tx(5000, 'other')]
        for r in rows:
            r['resolved_account_keys'] = [r['pump_trade_event_prefixes'][0]['mint']]
            r['pump_instruction_count'] = 1
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'input'; p.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            s, ambiguous, failed, _ = load(p, 'test')
            r = interval(s, 'm', 2_000_000_000, 4_000_000_000, ambiguous, failed)
            self.assertEqual(r['status'], 'AMBIGUOUS_CONTEXT')
            self.assertEqual(r['failed_mint_account_mentions_NOT_flow'], 1)
            self.assertEqual(r['net_executed_sol_flow'], 0)

    def test_empty_report_no_fake_uncertainty(self):
        r = summarize([])
        self.assertEqual(r['seeds'], 0)
        self.assertIsNone(r['arms']['burst']['uncertainty']['bootstrap_95_percent_SOL_flow'])


if __name__ == '__main__': unittest.main()
