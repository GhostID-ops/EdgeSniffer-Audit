import copy
import unittest
from burst_persistence_v1 import NS
from test_burst_persistence_v1 import seed_rows, study, tx
from quote_context_v1 import control_reason, control_snapshot, mint_available_ns, freshness, concentration


class ContextTests(unittest.TestCase):
    def test_raw_archive_snapshot_keeps_later_clock_and_identity(self):
        import base64,json
        from quote_archive_inventory_v1 import summarize_snapshot
        raw=json.dumps({'result':{'context':{'slot':7},'value':{'owner':'token-program','data':[base64.b64encode(b'raw mint').decode(),'base64']}}}).encode()
        r=summarize_snapshot({'accountPubkey':'m','receivedAtUnixNs':10,'decodeAtUnixNs':12},raw,{'m'})
        self.assertTrue(r['selected_mint']); self.assertEqual(r['raw_account_bytes'],8)
        self.assertEqual(r['decode_at_ns'],12); self.assertFalse(r['continuity_certified'])

    def test_full_pipeline_denominator_and_replay(self):
        import json,tempfile
        from pathlib import Path
        from unittest.mock import patch
        from account_boundary_audit_v1 import run as account_audit
        from burst_persistence_v1 import run as primary
        from quote_context_v1 import run,sha
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); session=root/'batch/session-01'; (session/'quality').mkdir(parents=True)
            inputs=seed_rows(); accounts=[]
            for txrow in inputs:
                e=txrow['pump_trade_event_prefixes'][0]; e.update(real_quote_reserves=10,real_token_reserves=80)
                txrow['resolved_account_keys']=['curve-'+e['mint']]
                accounts.append({'kind':'rustNativePumpCurveAccountState','bondingCurve':'curve-'+e['mint'],
                    'accountTxnSignature':txrow['signature'],'slot':txrow['slot'],'writeVersion':txrow['transaction_index'],
                    'decodeCompleteUnixNs':txrow['metadata_available_at_ns']+1,'virtualSolReserves':e['virtual_quote_reserves'],
                    'virtualTokenReserves':100,'realSolReserves':10,'realTokenReserves':80})
            source=session/'quality/transactions.jsonl'; source.write_text(''.join(json.dumps(r)+'\n' for r in inputs))
            primary(source,session/'experiment','fixture')
            a=root/'accounts';a.write_text(''.join(json.dumps(r)+'\n' for r in accounts))
            with patch('account_boundary_audit_v1.derive_curves',return_value={'mappings':{'m':{'curve':'curve-m'},'c':{'curve':'curve-c'}}}):
                account_audit(root/'batch',a,root/'canonical',Path('mock-sdk'))
            flow=root/'flow';flow.write_text('')
            mint=root/'mint';mint.write_text(json.dumps({'mint':'m','capturedAtMs':999999})+'\n')
            fee=root/'fee';fee.write_text('{}\n')
            before=sha(session/'experiment/opportunities.jsonl')
            first=run(root/'batch',root/'canonical',root/'canonical/account-snapshot.jsonl',flow,mint,fee,root/'out1')
            second=run(root/'batch',root/'canonical',root/'canonical/account-snapshot.jsonl',flow,mint,fee,root/'out2')
            self.assertEqual(first,second); self.assertEqual(first['attempt_rows_two_arms'],6)
            self.assertEqual(first['valid_quotes'],0);self.assertEqual(first['boundary_context_counts']['prior_retained_mint_rows'],0)
            self.assertEqual(before,sha(session/'experiment/opportunities.jsonl'))
            attempts=[json.loads(x) for x in (root/'out1/attempts.jsonl').read_text().splitlines()]
            self.assertTrue(all(r['net_pnl_sol'] is None and r['execution_status']=='NOT_ATTEMPTED' for r in attempts))
            a.write_text(a.read_text()+'{}\n')
            with self.assertRaisesRegex(ValueError,'snapshot changed'):
                run(root/'batch',root/'canonical',a,flow,mint,fee,root/'out3')

    def test_exact_control_and_exclusions(self):
        s=study(seed_rows()); seed=s.seeds[0]
        d=control_snapshot(s,seed)
        self.assertEqual(d['selected_control'],'c')
        self.assertEqual(d['exclusion_counts'],{'ELIGIBLE':1})
        self.assertEqual(control_reason(s,'c',1200*1_000_000,99),'DIFFERENT_LIQUIDITY_BUCKET')
        self.assertEqual(control_reason(s,'c',1500*1_000_000,6),'NO_EVENT_IN_LAST_250MS')

    def test_control_replay_prefix_and_future_burst(self):
        s=study(seed_rows()); d=control_snapshot(s,s.seeds[0])
        stored=copy.deepcopy(d)
        s.consume(tx(1250,'c',user='new',q=120))
        self.assertEqual(d,stored)
        self.assertEqual(stored['selected_control'],'c')

    def test_mint_repair_clock_not_original_receipt(self):
        r={'nativeReceiveTsMs':1,'capturedAtMs':2,'mintState':{'mintAccountFetchedAtMs':5},'repair':{'repairedAtMs':10}}
        self.assertEqual(mint_available_ns(r),10_000_000)
        self.assertIsNone(mint_available_ns({'nativeReceiveTsMs':1}))

    def test_equal_next_write_not_continuity_and_gap(self):
        reserves=dict(virtualSolReserves=30,virtualTokenReserves=100,realSolReserves=10,realTokenReserves=80)
        mark={'available_at_ns':NS,'deadline_ns':4*NS,'reserves':reserves}
        future=dict(reserves,decodeCompleteUnixNs=5*NS)
        r=freshness(mark,[],[future],0,3*NS)
        self.assertTrue(r['next_observed_write_same_four_reserves'])
        self.assertFalse(r['complete_write_coverage_certified'])
        self.assertFalse(r['transaction_session_covers_interval'])
        self.assertFalse(r['original_1s_age_pass'])

    def test_observed_different_state_is_flagged(self):
        mark={'available_at_ns':NS,'deadline_ns':4*NS,'reserves':dict(virtualSolReserves=30,virtualTokenReserves=100,realSolReserves=10,realTokenReserves=80)}
        p={'at':2*NS,'event':dict(virtual_quote_reserves=31,virtual_token_reserves=99,real_quote_reserves=11,real_token_reserves=79)}
        r=freshness(mark,[p],[],0,6*NS)
        self.assertEqual(r['differing_event_reserves_after_state'],1)
        self.assertEqual(freshness(mark,[dict(p,at=5*NS)],[],0,6*NS)['differing_event_reserves_after_state'],0)

    def test_concentration_removes_mint_in_both_roles_and_no_optimization(self):
        seeds={str(i):{'seed_state':{'event':{'virtual_quote_reserves':40*NS}}} for i in range(3)}
        rows=[]
        for i,(m,c,v) in enumerate([('a','b',10),('b','c',-2),('d','c',1)]):
            legs={'target':{'status':'OBSERVED_INTERVAL','net_executed_sol_flow':v},'control':{'status':'OBSERVED_INTERVAL','net_executed_sol_flow':0}}
            rows.append({'seed_id':str(i),'mint':m,'control_mint':c,'confirmation':'REJECTED','arms':{'burst':legs,'persistence':legs}})
        r=concentration(rows,seeds)
        self.assertEqual(r['burst']['mean_excess_sol_flow'],3)
        self.assertEqual(r['burst']['leave_mint_out_either_role']['b'],{'remaining_pairs':1,'mean':1})
        self.assertEqual(r['burst']['largest_positive_contributions']['1']['mean_after_removal'],-0.5)
        self.assertEqual(r['persistence']['pairs'],0)


if __name__=='__main__':unittest.main()
