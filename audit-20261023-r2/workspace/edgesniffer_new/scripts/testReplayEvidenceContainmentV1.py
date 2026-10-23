"""Counterexamples for audit I01; exercises real replay and its consumers."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import materializeNativePumpCurveStateV1 as replay
import runIndependentStrategyLabV1 as lab
from adapt2CQFrozenScorerNativeV1 import source_event
from materialize2CQFrozenScorerWindowsV1 import build_window
from materialize2CQEventDrivenIndependentWindowsV1 import native_complete, phase_ok


class EvidenceContainmentTests(unittest.TestCase):
    def test_successful_intent_replay_cannot_be_recertified_downstream(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, seeds, out, state = [root / n for n in ('in.jsonl', 'seeds.jsonl', 'out.jsonl', 'state.json')]
            seeds.write_text(json.dumps(dict(bondingCurve='c', virtualSolReserves=30_000_000_000,
                virtualTokenReserves=1_073_000_000_000_000, realSolReserves=1_000_000_000,
                realTokenReserves=793_100_000_000_000, observedAtMs=1000, slot=1))+'\n')
            source.write_text(json.dumps(dict(mint='m', side='buy', quote_lamports=100_000_000,
                source_slot=2, local_receive_ts_ms=2000, transaction_signature='sig',
                execution={'success': True}, raw_record={'bondingCurve': 'c'},
                source_ix_data_base64=base64.b64encode(replay.BUY_EXACT_SOL_IN_DISCRIMINATOR+bytes(16)).decode()))+'\n')
            replay.run(SimpleNamespace(input=source, output=out, state=state,
                account_seed_input=seeds, once=True, start_at_end=False, poll_ms=1))
            row = json.loads(out.read_text())
        self.assertEqual(row['execution_status'], 'SUCCESS')
        self.assertFalse(row['native_economics_state_complete'])
        self.assertIsNone(lab.event_state(row))
        self.assertIsNone(source_event(row, 'creator'))
        self.assertFalse(native_complete(row))
        self.assertFalse(phase_ok(row, 100))
        event = dict(mint='m', signal_receive_ts_ms=2000, signal_signature='sig')
        window, reason = build_window(event, [row], 10000)
        self.assertIsNone(window)
        self.assertEqual(reason, 'missing_native_entry_state_or_fee')
        row['entry_state'] = row['canonical_state']['post_state']
        self.assertIsNone(lab.quote_buy(row, .1, 0))
        self.assertIsNone(lab.quote_sell(row, 1000, .1, 0))
        # Old artifacts claimed success/complete: provenance still disqualifies them.
        row['native_economics_state_complete'] = True
        row['state_transition_authority'] = 'EXECUTED'
        row['canonical_state']['state_transition_authority'] = 'EXECUTED'
        self.assertIsNone(lab.event_state(row))

    def test_nested_negative_evidence_overrides_parseable_state(self):
        value = dict(virtual_sol=30_000_000_000, virtual_token=1_000_000,
                     real_sol=1_000_000_000, real_token=900_000)
        self.assertIsNotNone(lab.event_state({'state': value}))
        for marker in ({'native_economics_state_complete': False},
                       {'execution_status': 'FAILED'},
                       {'state_transition_authority': 'CONDITIONAL_INTENT_REPLAY'}):
            self.assertIsNone(lab.event_state({'state': value, 'canonical_state': marker}))

    def test_malformed_execution_flags_are_unknown(self):
        for value in (None, 'true', 'false', 0, 1):
            self.assertEqual(replay.execution_status({'execution': {'success': value}}), 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
