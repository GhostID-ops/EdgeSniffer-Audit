import base64
import copy
import json
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch

from state_continuity_v1 import (writable_keys, native_index, audit_role, replay_study,
                                checkpoint_context, native_advanced_context, descendant_asof, FEE, GLOBAL, CLOCK, NS)
from synchronized_context_v1 import Scheduler
from run_context_capture_v1 import snapshot_row
from test_burst_persistence_v1 import seed_rows, tx

ROOT=Path(__file__).parent


def empty_index():return {'writes':{},'touches':{},'slots':[],'faults':[],'unknown':[]}


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        f=json.loads((ROOT/'evidence/quote-onchain-fixtures-20261023-v1.jsonl').read_text().splitlines()[0])
        self.curve=f['raw_pre_account']['pubkey'];self.mint=f['raw_context']['mint']['pubkey']
        accounts={self.curve:f['raw_pre_account'],self.mint:f['raw_context']['mint'],
                  FEE:f['raw_context']['fee'],GLOBAL:f['raw_context']['global'],CLOCK:f['raw_context']['clock']}
        slot=f['raw_pre_account']['slot']
        clock=bytearray(base64.b64decode(accounts[CLOCK]['data_base64']));clock[:8]=slot.to_bytes(8,'little')
        accounts[CLOCK]=dict(accounts[CLOCK],data_base64=base64.b64encode(clock).decode())
        values=[{'owner':a['owner'],'data':[a['data_base64'],'base64']} for a in accounts.values()]
        self.row=snapshot_row(list(accounts),10*NS,10*NS+100,{'context':{'slot':slot},'value':values},None)
        self.row.update(purpose='SYNCHRONIZED_SIGNAL_CONTROL_READ_SET',request_plan={'minimum_native_slot':slot})
        self.at=10*NS+200

    def context(self, row=None, index=None, at=None):
        return checkpoint_context([(1,row or self.row)],self.mint,self.curve,at or self.at,index or empty_index())

    def test_complete_raw_readset_decodes_and_quotes_but_is_not_fill(self):
        c=self.context();self.assertEqual(c['reasons'],[])
        self.assertTrue(c['observation_quote_ready']);self.assertFalse(c['continuous_state_certified'])
        self.assertFalse(c['execution_fill_certified'])

    def test_future_response_never_repairs_boundary(self):
        r=copy.deepcopy(self.row);r['response_available_at_ns']=self.at+1
        self.assertEqual(self.context(r)['reasons'],['NO_PRIOR_SYNCHRONIZED_READ_SET'])
        before=self.context();after=checkpoint_context([(1,self.row),(2,r)],self.mint,self.curve,self.at,empty_index())
        self.assertEqual(before,after)

    def test_age_uses_request_not_late_response(self):
        r=copy.deepcopy(self.row);r['request_at_ns']=self.at-NS-1
        self.assertIn('STRICT_1S_REQUEST_AGE_FAIL',self.context(r)['reasons'])

    def test_future_native_write_is_only_later_diagnostic(self):
        index=empty_index();index['writes'][self.curve]=[{'at':self.at+1,'slot':1,'data_base64':'bad'}]
        self.assertTrue(self.context(index=index)['observation_quote_ready'])

    def test_dropped_write_known_touch_and_gap_block_observation(self):
        index=empty_index();index['touches'][self.curve]=[{'at':self.at-1,'slot':1}]
        self.assertIn('KNOWN_TRANSITION_CONFLICT',self.context(index=index)['reasons'])
        index=empty_index();index['faults']=[{'at':self.at-1}]
        self.assertIn('CAPTURE_DISCONTINUITY',self.context(index=index)['reasons'])

    def test_no_native_rpc_splicing_or_inflight_conflict(self):
        index=empty_index();index['writes'][self.curve]=[{'at':self.row['request_at_ns']+1,'slot':1,'data_base64':'different'}]
        self.assertFalse(self.context(index=index)['observation_quote_ready'])

    def test_dead_bank_at_boundary_not_later_knowledge(self):
        index=empty_index();slot=self.row['raw_rpc_result']['context']['slot']
        index['slots']=[{'at':self.at-1,'slot':slot,'status':6}]
        self.assertIn('KNOWN_DEAD_BANK',self.context(index=index)['reasons'])
        index['slots'][0]['at']=self.at+1
        self.assertNotIn('KNOWN_DEAD_BANK',self.context(index=index)['reasons'])

    def test_slot_floor_clock_and_null_accounts(self):
        r=copy.deepcopy(self.row);r['request_plan']['minimum_native_slot']+=1
        self.assertIn('RPC_BANK_BEHIND_REQUESTED_NATIVE_SLOT',self.context(r)['reasons'])
        r=copy.deepcopy(self.row);r['raw_rpc_result']['context']['slot']+=1
        self.assertIn('UNSUPPORTED_CONTEXT:CLOCK_BANK_SLOT_MISMATCH',self.context(r)['reasons'])
        r=copy.deepcopy(self.row);r['raw_rpc_result']['value'][0]=None
        self.assertFalse(self.context(r)['observation_quote_ready'])

    def test_privileges_resolve_alt_and_do_not_count_readonly_mentions(self):
        raw={'transaction':{'message':{'accountKeys':['payer','readonly_signer','write','read'],
            'header':{'numRequiredSignatures':2,'numReadonlySignedAccounts':1,'numReadonlyUnsignedAccounts':1},
            'addressTableLookups':[{'writableIndexes':[1],'readonlyIndexes':[2]}]}},
             'meta':{'loadedAddresses':{'writable':['altwrite'],'readonly':['altread']}}}
        self.assertEqual(writable_keys(raw),{'payer','write','altwrite'})
        raw['meta']['loadedAddresses']['writable']=[];self.assertIsNone(writable_keys(raw))

    def test_missing_and_late_writes_are_distinct_never_certified(self):
        index=empty_index();index['touches']['curve']=[{'at':1,'slot':1,'signature':'sig','status':'UNKNOWN'}]
        a=audit_role(index,'curve',10);self.assertEqual(a['unaccounted_touches'][0]['reason'],'NO_RETAINED_WRITE_FOR_WRITABLE_TOUCH')
        index['writes']['curve']=[{'at':20,'slot':1,'transaction_signature':'sig'}]
        b=audit_role(index,'curve',10);self.assertEqual(b['unaccounted_touches'][0]['reason'],'WRITE_OBSERVED_LATER')
        self.assertFalse(b['complete_write_coverage_certified'])

    def test_failed_transaction_never_invents_curve_write(self):
        raw={'slot':1,'signature':'x','meta':{'err':{'error':1}}}
        index=native_index([{'kind':'TRANSACTION','transaction':raw,'normalized_at_unix_ns':1}])
        self.assertFalse(index['touches']);self.assertFalse(index['unknown'])
        raw['meta']={};index=native_index([{'kind':'TRANSACTION','transaction':raw,'normalized_at_unix_ns':1}])
        self.assertEqual(index['unknown'][0]['status'],'UNKNOWN')

    def test_scheduler_frozen_rules_full_pair_and_tail_preservation(self):
        derive=lambda m,p:{'mappings':{x:{'curve':'curve:'+x} for x in m}}
        scheduler=Scheduler('test',2,None,derive=derive)
        for r in seed_rows():
            with patch('synchronized_context_v1.native_view',return_value=r):
                scheduler.consume({'kind':'TRANSACTION'},1,r['metadata_available_at_ns']+100)
        self.assertEqual(len(scheduler.seed_logs),1)
        self.assertEqual(scheduler.seed_logs[0]['control_mint'],'c')
        plan=scheduler.request_plan(1300_000_000)[0]
        self.assertEqual(set(plan['addresses']),{FEE,GLOBAL,CLOCK,'m','c','curve:m','curve:c'})
        self.assertEqual(scheduler.seed_logs[0]['observer_lag_ns'],100)
        self.assertEqual(scheduler.request_plan(23*NS),[])
        self.assertEqual(len(scheduler.seed_logs),1)

    def test_scheduler_gap_disables_without_erasing_existing_seeds(self):
        s=Scheduler('test',50,None)
        s.seed_logs.append({'seed_id':'retained'})
        s.consume({'kind':'GAP'},1,10)
        self.assertTrue(s.disabled);self.assertEqual(len(s.seed_logs),1)
        self.assertEqual(s.request_plan(20),[])

    def test_presignal_readset_exists_before_first_burst(self):
        derive=lambda m,p:{'mappings':{x:{'curve':'curve:'+x} for x in m}}
        s=Scheduler('test',50,None,derive=derive,pre_signal=True)
        for r in seed_rows()[:4]:
            with patch('synchronized_context_v1.native_view',return_value=r):
                s.consume({'kind':'TRANSACTION'},1,r['metadata_available_at_ns']+100)
        self.assertEqual(s.seed_logs,[])
        plan=s.request_plan(900_000_000)[0]
        self.assertEqual(set(plan['addresses']),{FEE,GLOBAL,CLOCK,'m','c','curve:m','curve:c'})
        self.assertEqual(plan['seed_ids'],[])

    def test_presignal_readset_batches_keep_every_candidate(self):
        derive=lambda m,p:{'mappings':{x:{'curve':'curve:'+x} for x in m}}
        s=Scheduler('test',50,None,derive=derive,pre_signal=True);s.start=0
        for n in range(120):s.study.history[str(n)]=[{'at':NS,'event':{'native':True}}]
        plans=s.request_plan(NS+1)
        self.assertTrue(all(len(p['addresses'])<=100 for p in plans))
        self.assertTrue(all(FEE in p['addresses'] and CLOCK in p['addresses'] for p in plans))
        found={a for p in plans for a in p['addresses']}
        self.assertTrue(set(map(str,range(120)))<=found)

    def test_newer_bank_before_request_and_touch_during_request_block(self):
        slot=self.row['raw_rpc_result']['context']['slot']
        index=empty_index();index['writes'][self.curve]=[{'at':self.row['request_at_ns']-1,
            'slot':slot+1,'data_base64':'different'}]
        self.assertIn('KNOWN_TRANSITION_CONFLICT',self.context(index=index)['reasons'])
        index=empty_index();index['touches'][self.curve]=[{'at':self.row['request_at_ns']+1,'slot':slot+1}]
        self.assertIn('KNOWN_TRANSITION_CONFLICT',self.context(index=index)['reasons'])

    def test_null_metadata_retained_as_unknown(self):
        raw={'transaction':{'message':{}},'meta':None,'slot':1,'signature':'x'}
        self.assertIsNone(writable_keys(raw))
        index=native_index([{'kind':'TRANSACTION','transaction':raw,'normalized_at_unix_ns':1}])
        self.assertEqual(index['unknown'][0]['status'],'UNKNOWN')

    def test_same_slot_rpc_native_order_is_unknown_not_crash(self):
        from quote_context_adapter_v1 import causal_context
        native={'available_at_ns':1,'source_line':1,'origin':'native','decoded':{},'reason':None,
                'account':{'slot':1,'write_version':2}}
        rpc=dict(native,available_at_ns=2,origin='rpc_response',source_line=2,account={'slot':1,'write_version':None})
        c=causal_context({'curve':[native,rpc]},'mint','curve',3,4)
        self.assertIn('RPC_NATIVE_SAME_SLOT_ORDER_UNRESOLVED',c['available_input_reasons'])
        self.assertFalse(c['causal_quote_certified'])

    def test_gap_replay_cannot_invent_post_gap_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);rows=[dict(kind='TRANSACTION',view=r) for r in seed_rows()]
            rows.insert(len(rows)-1,{'kind':'GAP','available_at_ns':1199_000_000})
            (p/'capture.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            (p/'scheduler.jsonl').write_text('')
            with patch('synchronized_context_v1.native_view',side_effect=lambda r,l:r['view']):
                s=replay_study(p,{'synchronized_context':{'population':'test','signal_seconds':50}})
            self.assertEqual(len(s.seeds),0)

    def test_scheduler_floor_includes_native_accounts_and_sequence_faults(self):
        s=Scheduler('test',50,None)
        s.consume({'kind':'ACCOUNT','account':{'slot':22},'sequence':1},1,10)
        self.assertEqual(s.max_slot,22)
        s.consume({'kind':'PING','sequence':3},2,11)
        self.assertTrue(s.disabled)

    def native_advance_fixture(self):
        slot=self.row['raw_rpc_result']['context']['slot'];a=self.row['raw_rpc_result']['value'][0]
        index=empty_index();index['slots']=[{'slot':slot+1,'parent':slot,'status':0,'at':self.at-2}]
        index['touches'][self.curve]=[{'at':self.at-1,'slot':slot+1,'signature':'next','status':'SUCCESS'}]
        index['writes'][self.curve]=[{'at':self.at-1,'slot':slot+1,'write_version':1,'transaction_signature':'next',
            'pubkey':self.curve,'owner':a['owner'],'data_base64':a['data'][0],'is_startup':False}]
        return index

    def advanced(self,index):return native_advanced_context([(1,self.row)],self.mint,self.curve,self.at,index)

    def test_native_advance_accounts_for_new_touch_without_inventing_fill(self):
        index=self.native_advance_fixture()
        self.assertFalse(self.context(index=index)['observation_quote_ready'])
        c=self.advanced(index)
        self.assertEqual(c['reasons'],[]);self.assertTrue(c['observation_quote_ready'])
        self.assertFalse(c['continuous_state_certified']);self.assertFalse(c['execution_fill_certified'])
        self.assertEqual(c['native_transition_sources'][0]['origin'],'native')

    def test_native_advance_missing_write_and_future_parent_fail(self):
        index=self.native_advance_fixture();index['writes'][self.curve]=[]
        self.assertFalse(self.advanced(index)['observation_quote_ready'])
        index=self.native_advance_fixture();index['slots'][0]['at']=self.at+1
        self.assertIn('NO_CAUSAL_PARENT_PATH:curve',self.advanced(index)['reasons'])

    def test_native_advance_unknown_execution_and_dead_branch_fail(self):
        index=self.native_advance_fixture();index['touches'][self.curve][0]['status']='UNKNOWN'
        self.assertIn('UNKNOWN_EXECUTION_TOUCH:curve',self.advanced(index)['reasons'])
        index=self.native_advance_fixture();index['slots'][0]['status']=6
        self.assertIn('KNOWN_DEAD_NATIVE_STATE:curve',self.advanced(index)['reasons'])

    def test_parent_path_rejects_cycle_missing_and_conflict(self):
        index=empty_index();index['slots']=[{'slot':4,'parent':3,'at':1,'status':0},{'slot':3,'parent':2,'at':1,'status':0}]
        self.assertTrue(descendant_asof(index,4,2,2))
        self.assertFalse(descendant_asof(index,4,1,2))
        index['slots'].append({'slot':3,'parent':1,'at':1,'status':0})
        self.assertFalse(descendant_asof(index,4,2,2))

    def test_dead_ancestor_and_startup_write_fail_advanced_context(self):
        index=self.native_advance_fixture();index['writes'][self.curve][0]['is_startup']=True
        self.assertIn('UNPROVEN_NATIVE_TRANSITION_IDENTITY:curve',self.advanced(index)['reasons'])
        index=empty_index();index['slots']=[{'slot':4,'parent':3,'at':1,'status':0},
            {'slot':3,'parent':2,'at':1,'status':6}]
        self.assertFalse(descendant_asof(index,4,2,2))

    def test_sibling_selected_states_cannot_form_one_quote(self):
        index=self.native_advance_fixture();bank=self.row['raw_rpc_result']['context']['slot']
        value=self.row['raw_rpc_result']['value'][1]
        index['slots'].append({'slot':bank+2,'parent':bank,'status':0,'at':self.at-1})
        index['writes'][self.mint]=[{'at':self.at-1,'slot':bank+2,'write_version':2,'transaction_signature':'mintwrite',
            'pubkey':self.mint,'owner':value['owner'],'data_base64':value['data'][0],'is_startup':False}]
        self.assertIn('INCOMPATIBLE_SELECTED_BRANCH:curve',self.advanced(index)['reasons'])

    def test_prerequest_newer_touch_is_not_erased_by_advancement(self):
        index=self.native_advance_fixture();bank=self.row['raw_rpc_result']['context']['slot']
        index['touches'][self.curve].append({'at':self.row['request_at_ns']-1,'slot':bank+2,'signature':'lost','status':'SUCCESS'})
        c=self.advanced(index)
        self.assertIn('MISSING_NATIVE_WRITE_FOR_TOUCH:curve',c['reasons'])
        self.assertIn('TOUCH_OUTSIDE_SELECTED_BRANCH:curve',c['reasons'])

    def test_earlier_same_slot_conflict_not_silently_erased(self):
        index=empty_index();bank=self.row['raw_rpc_result']['context']['slot']
        index['writes'][self.curve]=[{'at':self.row['request_at_ns']-1,'slot':bank,'write_version':2,
            'transaction_signature':'old','data_base64':'different'}]
        self.assertIn('SAME_SLOT_RPC_ORDER_UNRESOLVED:curve',self.advanced(index)['reasons'])


if __name__=='__main__':unittest.main()
