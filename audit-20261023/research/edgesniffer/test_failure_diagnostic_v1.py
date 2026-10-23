import json
from pathlib import Path
import tempfile
import unittest
from failure_diagnostic_v1 import DISCS, classify, run
from transaction_evidence_v1 import PUMP, b58encode


def failure(sig, status='FAILED'):
    disc = next(k for k,v in DISCS.items() if v == 'buy')
    return {'signature': sig, 'metadata_available_at_ns': 100, 'execution_status': status,
            'execution_error': {'raw': 'x'} if status == 'FAILED' else None,
            'resolved_account_keys': ['payer', 'mint'], 'pump_trade_event_prefixes': [],
            'instructions': [{'program_id': PUMP, 'inner_index': None, 'identity_resolved': True,
                              'discriminator_hex': disc, 'accounts': ['mint'],
                              'data_base58': b58encode(bytes.fromhex(disc)+b'\0'*16)}]}


class FailureTests(unittest.TestCase):
    def test_known_intent_not_fill_and_missing_pump_not_buy(self):
        self.assertEqual(classify(failure('a'))[2:], ('buy', 'direct'))
        r = failure('b'); r['instructions'] = []
        self.assertEqual(classify(r)[2:], ('unknown', 'no_pump_instruction_observed'))

    def test_short_instruction_stays_unknown(self):
        r = failure('a'); r['instructions'][0]['data_base58'] = '1'
        self.assertEqual(classify(r)[2], 'unknown')

    def test_duplicates_retry_context_and_failed_events_not_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); rows = [failure('a'), failure('a'), failure('b'), failure('c', 'SUCCESS')]
            for row in rows[:3]:
                row['pump_trade_event_prefixes'] = [{'mint': 'mint', 'signed_sol_flow_eligible': False}]
            source = p/'input'; source.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            result = run(source, None, p/'out')
            self.assertEqual(result['counts']['FAILED'], 2)
            self.assertEqual(result['counts']['repeated_signature_frames'], 1)
            self.assertEqual(result['retry_like_groups']['transactions_in_groups_ge2'], 2)
            bins = json.loads((p/'out'/'seconds.jsonl').read_text())
            self.assertNotIn('net_executed_sol_lamports', bins)
            self.assertNotIn('successful_sol_events', bins)

    def test_supported_discriminators_match_preserved_decoder(self):
        self.assertEqual(DISCS['66063d1201daebea'], 'buy')
        self.assertEqual(DISCS['07051dc4f5176550'], 'buy_v3')
        self.assertEqual(DISCS['33e685a4017f83ad'], 'sell')


if __name__ == '__main__': unittest.main()
