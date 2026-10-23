import base64
import copy
from fractions import Fraction
import json
from pathlib import Path
import tempfile
import unittest
import os
import subprocess

from quote_context_adapter_v1 import (PUMP_IDL,FEE_IDL,ROOT,RESERVES,TOKEN22,FEE,GLOBAL,CLOCK,
    Unsupported,idl_account,mint_state,transfer_fee,quote,fee_rates,available,metadata_differences,
    original_coverage,causal_context,decode_instruction,write_jsonl,decode_capture,canary_boundaries,intervening_uncertainty)
from recover_quote_fixtures_v1 import select
from transaction_evidence_v1 import b58encode
from ledger_v1 import SIZES

FIXTURES=ROOT/'evidence/quote-onchain-fixtures-20261023-v1.jsonl'


class QuoteAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures=[json.loads(line) for line in FIXTURES.read_text().splitlines()]
        cls.pump=json.loads(PUMP_IDL.read_text());cls.fee=json.loads(FEE_IDL.read_text())

    def test_independent_onchain_raw_state_instruction_fee_fixtures(self):
        self.assertEqual(len(self.fixtures),16)
        self.assertEqual(len({r['variant'] for r in self.fixtures}),6)
        for f in self.fixtures:
            with self.subTest(signature=f['signature']):
                state=idl_account(f['raw_pre_account'],'BondingCurve',self.pump)
                post=idl_account(f['raw_post_account'],'BondingCurve',self.pump)
                mint=mint_state(f['raw_context']['mint'])
                fees=idl_account(f['raw_context']['fee'],'FeeConfig',self.fee)
                pf,cf=fee_rates(state,mint,fees);ix=f['instruction']
                predicted=quote(state,ix['kind'],ix['amount'],ix['limit'],pf,cf)
                # Oracles are retained raw chain outcomes, NOT locally generated quotes.
                event=f['actual_event']
                self.assertEqual(predicted['post'],{k:post[k] for k in RESERVES})
                self.assertEqual(predicted['curve_amount'],event['sol_amount_lamports'])
                self.assertEqual(predicted['token_amount'],event['token_amount_raw'])
                self.assertEqual(predicted['protocol_fee'],event['protocol_fee_amount'])
                self.assertEqual(predicted['creator_fee'],event['creator_fee_amount'])
                sign=-1 if ix['kind'].startswith('sell') else 1
                delta=next(d for d in f['finalized_lamport_deltas'] if d['account']==f['raw_pre_account']['pubkey'])
                self.assertEqual(delta['net_delta_lamports'],sign*predicted['curve_amount'])

    def test_integer_rounding_and_fee_components(self):
        state=dict(zip(RESERVES,(30000000001,1073000000000000,10000000000,793100000000000)))
        for amount in (1,3,1000000,123456789123):
            q=quote(state,'buy',amount,2**64-1,95,30)
            numerator=amount*state[RESERVES[0]];denominator=state[RESERVES[1]]-amount
            floor,rem=divmod(numerator,denominator)
            self.assertEqual(q['curve_amount'],floor+bool(rem))
            for key,bps in [('protocol_fee',95),('creator_fee',30)]:
                floor,rem=divmod(q['curve_amount']*bps,10000)
                self.assertEqual(q[key],floor+bool(rem))
        self.assertEqual(quote(state,'buy',1,2**64-1,0,0)['creator_fee'],0)

    def test_three_sizes_nonlinear_impact_not_linear_profit(self):
        for f in self.fixtures:
            state=idl_account(f['raw_pre_account'],'BondingCurve',self.pump)
            outputs=[quote(state,'buy_exact_sol_in',size,0,95,30) for size in SIZES]
            ratios=[Fraction(q['token_amount'],s) for s,q in zip(SIZES,outputs)]
            self.assertGreater(ratios[0],ratios[1]);self.assertGreater(ratios[1],ratios[2])
            for size,q in zip(SIZES,outputs):
                self.assertLessEqual(q['user_quote_amount_before_network_costs'],size)
                self.assertGreater(q['token_amount'],0)

    def test_insufficient_reserves_limits_and_unsupported_variants(self):
        state=dict(zip(RESERVES,(30000000000,1000000000000,1000,10000)))
        for args in [('buy',10001,2**64-1),('buy',10,0),('sell',1000000,0),('buy_v3',1,100),('sell_v3',1,0)]:
            with self.subTest(args=args),self.assertRaises(Unsupported):quote(state,*args,95,30)
        with self.assertRaises(Unsupported):quote(state,'buy',1,1000,-1,30)

    def test_owner_discriminator_tail_and_noncanonical_identity_fail(self):
        account=copy.deepcopy(self.fixtures[0]['raw_pre_account'])
        with self.assertRaisesRegex(Unsupported,'OWNER'):idl_account(dict(account,owner='wrong'),'BondingCurve',self.pump)
        data=bytearray(base64.b64decode(account['data_base64']));data[0]^=1
        with self.assertRaisesRegex(Unsupported,'DISCRIMINATOR'):idl_account(dict(account,data_base64=base64.b64encode(data).decode()),'BondingCurve',self.pump)
        data=base64.b64decode(account['data_base64'])+b'\1'
        with self.assertRaisesRegex(Unsupported,'TAIL'):idl_account(dict(account,data_base64=base64.b64encode(data).decode()),'BondingCurve',self.pump)

    def test_real_sdk_canonical_global_fee_identity(self):
        sdk=Path(os.environ.get('TEST_WEB3_MODULE',str(ROOT.parents[1]/'node_modules/@solana/web3.js')))
        result=json.loads(subprocess.run(['node',str(ROOT/'derive_quote_identity_v1.cjs'),str(sdk),str(PUMP_IDL)],text=True,capture_output=True,check=True).stdout)
        self.assertEqual(result['global']['address'],GLOBAL);self.assertEqual(result['fee'],{'address':FEE,'bump':253})

    def test_mint_tlv_epoch_maxfee_and_unknown_extensions(self):
        data=bytearray(166);data[45]=1;data[165]=1
        schedule=lambda epoch,cap,bps:epoch.to_bytes(8,'little')+cap.to_bytes(8,'little')+bps.to_bytes(2,'little')
        payload=bytes(72)+schedule(3,100,10)+schedule(8,3,100)
        raw=bytes(data)+b'\1\0'+len(payload).to_bytes(2,'little')+payload
        account={'owner':TOKEN22,'data_base64':base64.b64encode(raw).decode()};mint=mint_state(account)
        self.assertEqual(transfer_fee(1000,mint,7),1);self.assertEqual(transfer_fee(1000,mint,8),3)
        self.assertEqual(transfer_fee(1,mint,8),1)
        with self.assertRaisesRegex(Unsupported,'EPOCH'):transfer_fee(1000,mint,None)
        with self.assertRaises(Unsupported):mint_state(dict(account,data_base64=base64.b64encode(raw[:-1]).decode()))
        unknown=bytes(data)+b'\xff\0\1\0X'
        self.assertEqual(mint_state(dict(account,data_base64=base64.b64encode(unknown).decode()))['unsupported_extensions'],[255])
        with self.assertRaisesRegex(Unsupported,'DUPLICATE'):mint_state(dict(account,data_base64=base64.b64encode(raw+raw[166:]).decode()))

    def test_fee_tier_and_creator_absence_not_event_fitted(self):
        f=self.fixtures[0];s=idl_account(f['raw_pre_account'],'BondingCurve',self.pump);m=mint_state(f['raw_context']['mint'])
        fee=idl_account(f['raw_context']['fee'],'FeeConfig',self.fee)
        self.assertEqual(fee_rates(s,m,fee),(95,30))
        self.assertEqual(fee_rates(dict(s,creator='11111111111111111111111111111111'),m,fee),(95,0))
        cutoff=s['virtual_quote_reserves']*m['supply']//s['virtual_token_reserves']
        fee['fee_tiers'].append({'market_cap_lamports_threshold':cutoff,'fees':{'lp_fee_bps':0,'protocol_fee_bps':50,'creator_fee_bps':10}})
        self.assertEqual(fee_rates(s,m,fee),(50,10))
        with self.assertRaisesRegex(Unsupported,'CONFIGURABLE'):fee_rates(dict(s,creator_fee_bps=1),m,fee)

    def test_known_metadata_extension_does_not_accept_malformed_payload(self):
        header=bytearray(166);header[45]=1;header[165]=1
        raw=bytes(header)+(18).to_bytes(2,'little')+(1).to_bytes(2,'little')+b'X'
        with self.assertRaisesRegex(Unsupported,'METADATA_POINTER'):mint_state({'owner':TOKEN22,'data_base64':base64.b64encode(raw).decode()})
        raw=bytes(header)+(19).to_bytes(2,'little')+(1).to_bytes(2,'little')+b'X'
        with self.assertRaisesRegex(Unsupported,'TRUNCATED'):mint_state({'owner':TOKEN22,'data_base64':base64.b64encode(raw).decode()})

    def test_future_context_and_prefix_invariance(self):
        rows=[{'available_at_ns':20,'source_line':1,'value':'earlier'}]
        before=available(rows,25)
        rows.append({'available_at_ns':100,'source_line':2,'value':'future-recovery'})
        self.assertEqual(before,available(rows,25));self.assertIsNone(available(rows,19))
        self.assertEqual(available(rows,100)['value'],'future-recovery')
        missing=causal_context({},'m','c',25,100)
        self.assertFalse(missing['causal_quote_certified'])
        self.assertEqual(len(missing['available_input_reasons']),5)

    def test_semantic_metadata_comparison_keeps_inner_identity(self):
        base={'execution_status':'SUCCESS','resolved_account_keys':['x'],'instructions':[{'outer_index':0,'inner_index':None,'stack_height':None}],
            'pump_trade_event_prefixes':[],'lamport_balance_deltas':[],'token_balance_deltas':[{'amount':'1','uiAmount':1.0}],'fee_lamports':1}
        final=copy.deepcopy(base);final['instructions'][0]['stack_height']=1;final['token_balance_deltas'][0]['uiAmount']=2.0
        self.assertEqual(metadata_differences(base,final),[])
        final['token_balance_deltas'][0]['amount']='2'
        self.assertIn('FINALIZED_METADATA_DIFFERENCE:token_balance_deltas',metadata_differences(base,final))
        final=copy.deepcopy(base);final['instructions'][0]['inner_index']=0
        self.assertIn('FINALIZED_METADATA_DIFFERENCE:instructions',metadata_differences(base,final))

    def test_instruction_optional_flags_and_identity(self):
        spec=next(s for s in self.pump['instructions'] if s['name']=='buy')
        raw=bytes(spec['discriminator'])+(10).to_bytes(8,'little')+(100).to_bytes(8,'little')
        ix={'data_base58':b58encode(raw+b'\1\0'),'accounts':[a['name'] for a in spec['accounts']],'outer_index':0,'inner_index':None}
        self.assertEqual(decode_instruction(ix,self.pump)['amount'],10)
        with self.assertRaisesRegex(Unsupported,'PARTIAL'):decode_instruction(dict(ix,data_base58=b58encode(raw+b'\0\1')),self.pump)
        with self.assertRaisesRegex(Unsupported,'ACCOUNTS'):decode_instruction(dict(ix,accounts=[]),self.pump)
        with self.assertRaisesRegex(Unsupported,'TAIL'):decode_instruction(dict(ix,data_base58=b58encode(raw+b'\0\0\0')),self.pump)

    def test_reference_selection_keeps_arithmetic_mismatch(self):
        cases=[{'signature':str(i),'variant':'buy','slot':1,'pre_source':{'slot':1},'predicted':{},'differences':['all']} for i in range(3)]
        self.assertEqual(len(select(cases)),3)
        changed=[dict(c,differences=[]) for c in cases]
        self.assertEqual([c['signature'] for c in select(cases)],[c['signature'] for c in select(changed)])

    def test_unknown_execution_or_missing_alt_cannot_prove_no_write(self):
        def tx(status,keys,gaps=[]):return {'execution_status':status,'resolved_account_keys':keys,'gaps':gaps}
        self.assertTrue(intervening_uncertainty([tx('UNKNOWN',['curve'])],'curve'))
        self.assertTrue(intervening_uncertainty([tx('SUCCESS',['other'],['alt_resolution_count_mismatch'])],'curve'))
        self.assertTrue(intervening_uncertainty([tx('SUCCESS',[])],'curve'))
        self.assertFalse(intervening_uncertainty([tx('FAILED',['curve'])],'curve'))
        self.assertFalse(intervening_uncertainty([tx('SUCCESS',['other'],['inner_context_unrecorded'])],'curve'))

    def test_original468_denominator_future_raw_never_repairs_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);canonical=root/'canonical';prior=root/'prior';out=root/'out'
            for p in (canonical,prior,out):p.mkdir()
            boundaries=[];contexts=[];attempts=[]
            for i in range(78):
                seed=str(i);marks={}
                for arm in ('burst','persistence'):
                    for leg in ('entry','exit'):
                        for role in ('target','control'):
                            label=f'{arm}:{leg}_at_ns:{role}';marks[label]={'deadline_ns':10000000000,'curve':'c','status':'OBSERVED_UNCERTIFIED_ACCOUNT_STATE'}
                            contexts.append({'seed_id':seed,'boundary':label,'state':{'original_1s_age_pass':True},'retained_mint_context':{}})
                    for size in SIZES:attempts.append({'seed_id':seed,'arm':arm,'size_lamports':size,'execution_status':'NOT_ATTEMPTED'})
                boundaries.append({'seed_id':seed,'mint':'m','control_mint':None,'boundaries':marks})
            write_jsonl(canonical/'boundaries.jsonl',boundaries);write_jsonl(prior/'boundary-context.jsonl',contexts);write_jsonl(prior/'attempts.jsonl',attempts)
            capture={'start_ns':20000000000,'end_ns':30000000000}
            first=original_coverage(canonical,prior,{},capture,out)
            self.assertEqual(first['intentions'],468);self.assertEqual(first['capture_overlap_ns'],0)
            self.assertEqual(first['by_boundary']['burst:entry_at_ns:target']['unresolved'],78)
            self.assertEqual(first['complete_causal_intentions'],0)
            future={'m':[{'available_at_ns':25000000000,'source_line':1,'decoded':{'valid':True}}]}
            second=root/'out2';second.mkdir();again=original_coverage(canonical,prior,future,capture,second)
            self.assertEqual(first,again)
            for name in ('original-intentions.jsonl','original-boundary-coverage.jsonl','original-decisions.jsonl'):
                self.assertEqual((out/name).read_bytes(),(second/name).read_bytes())
            write_jsonl(prior/'attempts.jsonl',attempts[:-1])
            with self.assertRaisesRegex(ValueError,'468'):original_coverage(canonical,prior,{},capture,out)

    def test_raw_to_boundary_integration_cannot_certify_continuity(self):
        f=self.fixtures[0]
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);src=root/'src';src.mkdir()
            account=f['raw_pre_account'];mint=f['actual_event']['mint'];at=1000000000
            native={'kind':'ACCOUNT','sequence':1,'received_at_unix_ns':at,'normalized_at_unix_ns':at+1,'account':account}
            write_jsonl(src/'capture.jsonl',[native])
            context=f['raw_context'];keys=[FEE,GLOBAL,mint,CLOCK];values=[context[k] for k in ('fee','global','mint','clock')]
            bootstrap={'addresses':keys,'request_at_ns':at,'response_available_at_ns':at+2,'error':None,
                'raw_rpc_result':{'context':{'slot':account['slot']},'value':[{'owner':a['owner'],'data':[a['data_base64'],'base64']} for a in values]}}
            write_jsonl(src/'bootstrap.jsonl',[bootstrap])
            hist,summary=decode_capture(src)
            before=causal_context(hist,mint,account['pubkey'],at+1,at+100)
            self.assertIn('NO_PRIOR_MINT',before['available_input_reasons'])
            after=causal_context(hist,mint,account['pubkey'],at+3,at+100)
            self.assertTrue(after['inputs_present_and_semantically_supported']);self.assertFalse(after['causal_quote_certified'])
            stale=causal_context(hist,mint,account['pubkey'],at+2000000000,at+3000000000)
            self.assertIn('ORIGINAL_1S_STATE_AGE_FAIL',stale['available_input_reasons'])


if __name__=='__main__':unittest.main()
