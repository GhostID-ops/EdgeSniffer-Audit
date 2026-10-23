import unittest
from pathlib import Path
import os
import json
import tempfile
from unittest.mock import patch
from account_boundary_audit_v1 import invariants, select, derive_curves, run


def state(at, slot=1):
    return {'decodeCompleteUnixNs':at,'slot':slot,'writeVersion':slot,'_line':1,
            'virtualSolReserves':30,'realSolReserves':10,'virtualTokenReserves':100,'realTokenReserves':80}


class AccountAuditTests(unittest.TestCase):
    def test_integration_excludes_wrong_account_even_when_reserves_match(self):
        from test_burst_persistence_v1 import seed_rows
        from burst_persistence_v1 import run as primary
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); session=root/'batch/session-01'; (session/'quality').mkdir(parents=True)
            inputs=seed_rows(); accounts=[]
            for tx in inputs:
                e=tx['pump_trade_event_prefixes'][0]; mint=e['mint']
                e.update(real_quote_reserves=10,real_token_reserves=80)
                tx['resolved_account_keys']=['canonical-'+mint,'not-a-curve']
                for curve in tx['resolved_account_keys']:
                    accounts.append({'kind':'rustNativePumpCurveAccountState','bondingCurve':curve,
                        'accountTxnSignature':tx['signature'],'slot':tx['slot'],'writeVersion':tx['transaction_index'],
                        'decodeCompleteUnixNs':tx['metadata_available_at_ns']+1,
                        'virtualSolReserves':e['virtual_quote_reserves'],'virtualTokenReserves':100,
                        'realSolReserves':10,'realTokenReserves':80})
            source=session/'quality/transactions.jsonl'
            source.write_text(''.join(json.dumps(r)+'\n' for r in inputs))
            primary(source,session/'experiment','fixture')
            account_source=root/'accounts'; account_source.write_text(''.join(json.dumps(r)+'\n' for r in accounts))
            with patch('account_boundary_audit_v1.derive_curves',return_value={
                    'mappings':{'m':{'curve':'canonical-m'},'c':{'curve':'canonical-c'}}}):
                result=run(root/'batch',account_source,root/'out',Path('mock-existing-sdk'))
            self.assertEqual(result['counts']['loose_matches_excluded_noncanonical_account'],len(inputs))
            self.assertEqual(result['event_account_comparisons']['all_four_equal'],len(inputs))
            self.assertEqual(result['selected_event_transaction_coverage']['expected_unique'],len(inputs))
            self.assertEqual(result['selected_event_transaction_coverage']['missing_unique'],0)
            self.assertEqual(result['seeds'],1)
            self.assertEqual(result['boundary_statuses']['burst:entry_at_ns:target:OBSERVED_UNCERTIFIED_ACCOUNT_STATE'],1)

    def test_canonical_curve_derivation_and_distinct_mints(self):
        module = Path(os.environ.get('TEST_WEB3_MODULE', str(Path(__file__).resolve().parents[2]/'node_modules/@solana/web3.js')))
        if not module.exists(): self.skipTest('Explicit existing SDK path required; no installation in test')
        mints = {'11111111111111111111111111111111','So11111111111111111111111111111111111111112'}
        a = derive_curves(mints,module); b=derive_curves(mints,module)
        self.assertEqual(a,b)
        self.assertEqual(len({x['curve'] for x in a['mappings'].values()}),2)
        self.assertTrue(all(0 <= x['bump'] <= 255 for x in a['mappings'].values()))
        self.assertEqual(a['program'],'6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P')
    def test_impossible_reserves_rejected(self):
        s=state(1); self.assertIsNone(invariants(s))
        s['realSolReserves']=31; self.assertEqual(invariants(s),'real_exceeds_virtual')

    def test_future_identity_or_state_cannot_repair_boundary(self):
        self.assertEqual(select('m',10,{'m':[(11,'c')]},{'c':[state(1)]})['status'],'NO_CAUSAL_UNIQUE_CURVE_MAPPING')
        self.assertEqual(select('m',10,{'m':[(1,'c')]},{'c':[state(11)]})['status'],'NO_PRIOR_ACCOUNT_STATE')

    def test_prefix_invariance_and_no_economic_certification(self):
        mapping={'m':[(1,'c')]}
        a=select('m',10,mapping,{'c':[state(2)]})
        b=select('m',10,mapping,{'c':[state(2),state(11,2)]})
        self.assertEqual(a,b); self.assertFalse(a['economic_admission'])

    def test_old_canonical_write_and_ambiguous_mapping_rejected(self):
        self.assertEqual(select('m',10,{'m':[(1,'c')]},{'c':[state(2,2),state(3,1)]})['status'],'canonical_write_regression')
        self.assertEqual(select('m',10,{'m':[(1,'c'),(2,'d')]},{})['status'],'NO_CAUSAL_UNIQUE_CURVE_MAPPING')


if __name__ == '__main__': unittest.main()
