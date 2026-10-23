import copy
import json
from pathlib import Path
import tempfile
import unittest

from burst_persistence_v1 import NS, Study, report, run, uncertainty


def tx(ms, mint='m', user='a', side='buy', q=100, status='SUCCESS', index=None):
    return {'signature': f'{ms}:{mint}:{user}', 'session_id': 'test',
            'source_commitment': 'processed', 'finality_known_at_decision': False,
            'slot': 1, 'transaction_index': ms if index is None else index,
            'received_at_unix_ns': ms*1_000_000, 'metadata_available_at_ns': ms*1_000_000,
            'execution_status': status, 'instruction_identity_complete': True,
            'pump_trade_event_prefixes': [{'mint': mint, 'user': user, 'side': side,
                'signed_sol_flow_eligible': True, 'mayhem': False, 'sol_amount_lamports': 10,
                'virtual_quote_reserves': q, 'virtual_token_reserves': 100}]}


def seed_rows():
    return [tx(0, 'c', side='sell'), tx(0), tx(800, 'c', side='sell'), tx(800),
            tx(1100, user='a', q=102), tx(1150, 'c', side='sell'), tx(1200, user='b', q=104)]


def study(rows):
    s = Study('test')
    for r in rows:
        s.consume(r)
    return s


class BurstTests(unittest.TestCase):
    def test_historical_recovery_cannot_be_admitted_as_native(self):
        r = tx(0); r['source_commitment'] = 'finalized'
        with self.assertRaises(ValueError): study([r])
        r = tx(0); r['finality_known_at_decision'] = True
        with self.assertRaises(ValueError): study([r])

    def test_raw_event_decoder_to_signal_integration(self):
        import base64
        from transaction_evidence_v1 import normalize, PUMP, TRADE
        from test_transaction_evidence_v1 import fixture
        decoded = []
        for row in seed_rows():
            e = row['pump_trade_event_prefixes'][0]
            u = (1 if e['user'] == 'a' else 2)
            mint_byte = 3 if e['mint'] == 'm' else 4
            prefix = (TRADE+bytes([mint_byte])*32+(10).to_bytes(8,'little')+(1).to_bytes(8,'little')+
                      bytes([e['side']=='buy'])+bytes([u])*32+bytes(8)+
                      e['virtual_quote_reserves'].to_bytes(8,'little')+(100).to_bytes(8,'little')+bytes(16))
            raw = (prefix+bytes(129)+(3).to_bytes(4,'little')+b'buy'+bytes(37)+bytes(32)+
                   (10).to_bytes(8,'little')+e['virtual_quote_reserves'].to_bytes(8,'little')+bytes(8))
            original = fixture(); original['transaction']['signatures'] = [row['signature']]
            original['transactionIndex'] = row['transaction_index']
            original['meta']['logMessages'] = [f'Program {PUMP} invoke [1]',
                'Program data: '+base64.b64encode(raw).decode(), f'Program {PUMP} success']
            view = normalize(original)
            for key in ('session_id', 'received_at_unix_ns', 'metadata_available_at_ns', 'source_commitment', 'finality_known_at_decision'):
                view[key] = row[key]
            decoded.append(view)
        s = study(decoded)
        self.assertEqual(len(s.seeds), 1)
        self.assertIsNotNone(s.seeds[0]['control_mint'])

    def test_seed_independent_control_and_prefix_invariance(self):
        s = study(seed_rows())
        self.assertEqual(len(s.seeds), 1)
        self.assertEqual(s.seeds[0]['control_mint'], 'c')
        seeds = copy.deepcopy(s.seeds)
        s.consume(tx(1250, 'c', user='teacher', q=110))
        s.consume(tx(1300, 'c', user='other', q=115))
        self.assertEqual(s.seeds[0], seeds[0])  # Future control signal is not removed.
        other = seed_rows()
        for row in other:
            row['teacher_wallet_match'] = True
        self.assertEqual(study(other).seeds, seeds)

    def test_failed_or_unknown_event_cannot_seed(self):
        for status in ('FAILED', 'UNKNOWN'):
            rows = seed_rows(); rows[-1]['execution_status'] = status
            self.assertEqual(study(rows).seeds, [])

    def test_first_seed_only_and_full_delays_and_all_attempts(self):
        rows = seed_rows()+[tx(1300, user='z', q=110), tx(2200, q=105),
                            tx(18200, q=115), tx(20200, q=116)]
        s = study(rows); self.assertEqual(len(s.seeds), 1)
        row = s.outcomes()[0]
        self.assertEqual(row['arms']['burst']['entry_at_ns'], 2200*1_000_000)
        self.assertEqual(row['arms']['burst']['exit_at_ns'], 18200*1_000_000)
        self.assertEqual(row['arms']['persistence']['entry_at_ns'], 4200*1_000_000)
        self.assertEqual(row['arms']['persistence']['exit_at_ns'], 20200*1_000_000)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); source = p/'source'
            source.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            a = run(source, p/'a', 'test'); b = run(source, p/'b', 'test')
            self.assertEqual(a, b)
            attempts = [json.loads(x) for x in (p/'a'/'attempts.jsonl').read_text().splitlines()]
            self.assertEqual(len(attempts), 6)
            self.assertTrue(all(x['net_pnl_sol'] is None for x in attempts))

    def test_no_future_mark_and_staleness_and_right_censor(self):
        s = study(seed_rows()+[tx(2201, q=200), tx(18200, q=300)])
        self.assertEqual(s.mark('m', 2200*1_000_000)['event']['virtual_quote_reserves'], 104)
        self.assertEqual(s.mark('m', 3202*1_000_000)['reason'], 'stale_state')
        self.assertEqual(s.mark('m', 19000*1_000_000)['reason'], 'right_censored')

    def test_confirmation_uses_new_buyers_and_retention(self):
        rows = seed_rows()+[tx(2500, user='new1', q=105), tx(3200, user='new2', q=106)]
        s = study(rows)
        self.assertEqual(s.confirm(s.seeds[0])[0], 'CONFIRMED')
        rows[-1]['pump_trade_event_prefixes'][0]['user'] = 'a'
        s = study(rows); self.assertEqual(s.confirm(s.seeds[0])[0], 'REJECTED')
        rows[-1]['pump_trade_event_prefixes'][0]['user'] = 'new2'
        rows[-1]['pump_trade_event_prefixes'][0]['virtual_quote_reserves'] = 100
        s = study(rows); self.assertEqual(s.confirm(s.seeds[0])[0], 'REJECTED')

    def test_dominant_buyer_rejected(self):
        rows = seed_rows()+[tx(2500, user='new1', q=105), tx(3200, user='new2', q=106)]
        rows[-1]['pump_trade_event_prefixes'][0]['sol_amount_lamports'] = 100
        s = study(rows); self.assertEqual(s.confirm(s.seeds[0])[0], 'REJECTED')

    def test_rejections_retained_unknown_not_zero_no_fake_control(self):
        s = study(seed_rows()+[tx(3200, user='a', q=105), tx(20200, q=106)])
        s.seeds[0]['control_mint'] = None
        row = s.outcomes()[0]
        self.assertEqual(row['confirmation'], 'REJECTED')
        self.assertEqual(row['arms']['persistence']['policy_return_bps'], 0)
        self.assertIsNone(row['arms']['persistence']['paired_excess_bps'])
        s = study(seed_rows())
        self.assertEqual(s.outcomes()[0]['confirmation'], 'UNKNOWN')
        self.assertIsNone(s.outcomes()[0]['arms']['persistence']['policy_return_bps'])

    def test_late_order_multi_event_and_mayhem_invalidate(self):
        for change in ('late', 'multi', 'mayhem'):
            rows = seed_rows()
            if change == 'late': rows[-1]['transaction_index'] = 1
            if change == 'multi': rows[-1]['pump_trade_event_prefixes'] *= 2
            if change == 'mayhem': rows[-1]['pump_trade_event_prefixes'][0]['mayhem'] = True
            self.assertEqual(study(rows).seeds, [])

    def test_duplicates_clock_regression_and_conflicting_signature(self):
        rows = seed_rows(); s = study(rows)
        s.consume(rows[-1]); self.assertEqual(s.counts['duplicate_transactions'], 1)
        changed = copy.deepcopy(rows[-1]); changed['slot'] = 2
        with self.assertRaises(ValueError): s.consume(changed)
        with self.assertRaises(ValueError): s.consume(tx(2))

    def test_no_control_does_not_remove_seed_and_no_future_eligibility(self):
        s = study([r for r in seed_rows() if r['pump_trade_event_prefixes'][0]['mint'] == 'm'])
        self.assertEqual(len(s.seeds), 1); self.assertIsNone(s.seeds[0]['control_mint'])
        self.assertEqual(report(s, s.outcomes())['without_control'], 1)

    def test_uncertainty_small_sample_explicit_and_deterministic(self):
        self.assertIsNone(uncertainty([(0, 1)])['bootstrap_95_percent_bps'])
        a = [(0, 1), (21*NS, -2)]
        self.assertEqual(uncertainty(a), uncertainty(a))
        self.assertTrue(uncertainty(a)['descriptive_only'])


if __name__ == '__main__':
    unittest.main()
