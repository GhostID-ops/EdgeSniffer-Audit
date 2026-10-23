import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from capture_quality_v1 import in_scope, reconcile_slot, run, compare_metadata
from transaction_evidence_v1 import normalize
from test_transaction_evidence_v1 import fixture


class QualityTests(unittest.TestCase):
    def test_native_finalized_metadata_mismatch_exposed(self):
        native=normalize(fixture());tx=fixture();tx['meta']['err']='failed';tx['meta']['postBalances'][0]+=1
        result=compare_metadata(native,normalize(tx))
        self.assertFalse(result['execution_status_equal'])
        self.assertFalse(result['lamport_balances_equal'])
        self.assertTrue(result['resolved_accounts_equal'])
    def test_scope_includes_alt_loaded_program_and_failed_transaction(self):
        tx=fixture(); tx['meta']['err']='failed'
        self.assertTrue(in_scope(tx))
        report=reconcile_slot([tx], set())
        self.assertEqual(report['missing_signatures'], ['sig'])
        self.assertEqual(report['expected_pump_signatures'], 1)

    def test_extra_observation_is_not_labeled_failed(self):
        report=reconcile_slot([fixture()], {'sig','orphan'})
        self.assertEqual(report['observed_expected'], 1)
        self.assertEqual(report['observed_not_in_finalized_block'], ['orphan'])

    def test_sequence_clock_gaps_unknown_meta_and_denominator(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); source=root/'capture'
            tx=fixture(); tx['meta']=None
            rows=[{'kind':'SESSION_START','session_id':'a','available_at_ns':1},
                  {'kind':'TRANSACTION','sequence':1,'received_at_unix_ns':10,'normalized_at_unix_ns':11,'transaction':tx},
                  {'kind':'GAP','available_at_ns':12}, {'kind':'CONNECTED','available_at_ns':20},
                  {'kind':'TRANSACTION','sequence':3,'received_at_unix_ns':9,'normalized_at_unix_ns':10,'transaction':None},
                  {'kind':'SESSION_END','available_at_ns':30}]
            source.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            report=run(source, root/'out')
            self.assertEqual(report['counts']['transaction_frames'],2)
            self.assertEqual(report['counts']['writer_sequence_discontinuities'],1)
            self.assertEqual(report['counts']['wall_clock_regressions'],1)
            self.assertEqual(report['counts']['transaction_body_unavailable'],1)
            self.assertEqual(report['execution_statuses']['UNKNOWN'],1)
            self.assertEqual(report['reconnect_gaps'],[{'start_ns':12,'end_ns':20}])
            self.assertEqual(report['global_capture_completeness'],'UNKNOWN')

    def test_unresolved_rpc_blocks_not_zero_missing_and_no_backdate(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); source=root/'capture'
            source.write_text(''.join(json.dumps({'kind':'SLOT','slot':s,'slot_state':{'status':0}})+'\n' for s in [1,2,3]))
            with patch('capture_quality_v1.rpc',return_value=(None,None)),patch('capture_quality_v1.time.sleep'):
                report=run(source,root/'out',endpoint='fake',block_limit=1)
            self.assertEqual(report['finalized_comparison']['unresolved_blocks'],1)
            result=json.loads((root/'out/finalized-reference.jsonl').read_text())
            self.assertFalse(result['usable_at_original_decision'])
            self.assertIsNone(result['result'])


if __name__=='__main__':unittest.main()
