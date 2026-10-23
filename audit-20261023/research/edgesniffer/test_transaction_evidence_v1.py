import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from transaction_evidence_v1 import PUMP, TRADE, EVENT_CPI, b58encode, normalize
from recover_receipts_v1 import run


def fixture():
    return {'slot': 12, 'blockTime': 100, 'transaction': {'signatures': ['sig'], 'message': {
        'accountKeys': ['user', 'curve'], 'addressTableLookups': [{'accountKey': 'table', 'writableIndexes': [], 'readonlyIndexes': [0]}],
        'instructions': [{'programIdIndex': 2, 'accounts': [0, 1], 'data': '1'}]}},
        'meta': {'err': None, 'fee': 5000, 'preBalances': [10000, 2000, 1], 'postBalances': [4000, 3000, 1],
                 'loadedAddresses': {'writable': [], 'readonly': [PUMP]}, 'innerInstructions': [{'index': 0, 'instructions': [
                     {'programIdIndex': 2, 'accounts': [1], 'data': '1', 'stackHeight': 2}]}],
                 'preTokenBalances': [], 'postTokenBalances': [], 'logMessages': []}}


class TransactionTests(unittest.TestCase):
    def test_signature_conflict_never_certifies_execution(self):
        tx=fixture();tx['signature']='other'
        self.assertEqual(normalize(tx)['execution_status'],'UNKNOWN')
        self.assertEqual(normalize(fixture(),'other')['execution_status'],'UNKNOWN')
    def test_alt_and_cpi_context(self):
        view = normalize(fixture(), 'sig')
        self.assertTrue(view['instruction_identity_complete'])
        self.assertEqual(view['instructions'][1]['outer_index'], 0)
        self.assertEqual(view['instructions'][1]['inner_index'], 0)
        self.assertEqual(view['instructions'][1]['stack_height'], 2)
        self.assertEqual(view['instructions'][1]['program_id'], PUMP)
        self.assertEqual(view['lamport_balance_deltas'][0]['net_delta_lamports'], -6000)
        self.assertFalse(view['economic_admission'])

    def test_missing_meta_and_error_preserved(self):
        tx = fixture(); tx['meta'] = None
        self.assertEqual(normalize(tx)['execution_status'], 'UNKNOWN')
        tx = fixture(); tx['meta']['err'] = {'InstructionError': [1, 'Failure']}
        self.assertEqual(normalize(tx)['execution_status'], 'FAILED')
        del tx['meta']['err']
        self.assertEqual(normalize(tx)['execution_status'], 'UNKNOWN')

    def test_invalid_accounts_and_null_inner(self):
        tx = fixture(); tx['meta']['loadedAddresses'] = {}
        view = normalize(tx)
        self.assertFalse(view['instruction_identity_complete'])
        self.assertIn('alt_resolution_count_mismatch', view['gaps'])
        tx = fixture(); tx['meta']['innerInstructions'] = None
        self.assertIn('inner_context_unrecorded', normalize(tx)['gaps'])

    def test_trade_amounts_require_pump_context_and_success(self):
        raw = TRADE+bytes([3])*32+(100).to_bytes(8,'little')+(200).to_bytes(8,'little')+b'\x01'+bytes([4])*32+bytes(40)
        tx = fixture()
        data = 'Program data: '+base64.b64encode(raw).decode()
        tx['meta']['logMessages'] = [f'Program {PUMP} invoke [1]', data, f'Program {PUMP} success']
        view = normalize(tx)
        self.assertEqual(view['pump_trade_event_prefixes'][0]['legacy_sol_amount_field'], 100)
        self.assertFalse(view['pump_trade_event_prefixes'][0]['signed_sol_flow_eligible'])
        tx['meta']['err'] = 'failed'
        self.assertFalse(normalize(tx)['pump_trade_event_prefixes'][0]['realized_swap_amounts_eligible'])
        tx['meta']['logMessages'] = [data]
        self.assertEqual(normalize(tx)['pump_trade_event_prefixes'], [])

    def test_full_trade_layout_and_cpi_coordinate_no_double_count(self):
        prefix=TRADE+bytes([3])*32+(100).to_bytes(8,'little')+(200).to_bytes(8,'little')+b'\x01'+bytes([4])*32+bytes(40)
        raw=prefix+bytes(129)+(3).to_bytes(4,'little')+b'buy'+bytes(37)+bytes(32)+(100).to_bytes(8,'little')+bytes(16)
        tx=fixture()
        tx['meta']['logMessages']=[f'Program {PUMP} invoke [1]','Program data: '+base64.b64encode(raw).decode(),f'Program {PUMP} success']
        tx['meta']['innerInstructions'][0]['instructions'][0]['data']=b58encode(EVENT_CPI+raw)
        events=normalize(tx)['pump_trade_event_prefixes']
        self.assertEqual(len(events),1)
        self.assertTrue(events[0]['signed_sol_flow_eligible'])
        self.assertEqual(events[0]['sol_amount_lamports'],100)
        self.assertEqual(events[0]['instruction_coordinates']['inner_index'],0)
        tx['meta']['logMessages']=[]
        self.assertEqual(len(normalize(tx)['pump_trade_event_prefixes']),1)

    def test_created_closed_token_account_unknown_not_fabricated_zero(self):
        tx = fixture()
        tx['meta']['postTokenBalances'] = [{'accountIndex': 0, 'mint': 'm', 'uiTokenAmount': {'amount': '5'}}]
        self.assertIsNone(normalize(tx)['token_balance_deltas'][0]['net_delta_raw'])

    def test_rpc_recovery_never_backdated_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); p = root/'receipts'
            p.write_text(json.dumps({'signature': 'sig', 'native_receive_unix_ns': 1})+'\n')
            with patch('recover_receipts_v1.rpc', return_value=(fixture(), None)), patch('recover_receipts_v1.time.sleep'):
                report = run(p, root/'out', [], 'not-a-real-url', 1)
            row = json.loads((root/'out/rpc-recovery.jsonl').read_text())
            self.assertFalse(row['usable_at_original_decision'])
            self.assertGreater(row['metadata_available_at_ns'], 1)
            self.assertEqual(report['native_decision_metadata_recovered'], 0)
            self.assertEqual(report['rpc_counts']['RECOVERED'], 1)
            with self.assertRaises(FileExistsError):
                run(p, root/'out', [])


if __name__ == '__main__':
    unittest.main()
