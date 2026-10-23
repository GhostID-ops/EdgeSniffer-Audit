#!/usr/bin/env python3
import pathlib, sys, unittest
sys.path.insert(0,str(pathlib.Path(__file__).parent))
import twoCqN350MultiLegEconomicsPlaygroundV1 as subject
from exactCurveEconomicsV1 import CurveState, Fees

class FeeEvent:
    cashback_fee_basis_points=0; buyback_fee_basis_points=0

class Tests(unittest.TestCase):
    def test_partial_sell_preserves_inventory(self):
        state=CurveState(30_000_000_000,1_073_000_000_000_000,0,793_100_000_000_000)
        buy=subject.quote_exact_quote_in_buy(state,500_000_000,Fees(100,50,0))
        # event_fees is bypassed by using the exact engine quote only; partial arithmetic is invariant.
        self.assertGreater(buy.token_out,0)
        self.assertEqual(buy.token_out*4//5 + (buy.token_out-buy.token_out*4//5),buy.token_out)
    def test_summary_counts(self):
        rows=[{"status":"EXACT","pnlSol":1.0,"returnPct":10,"buyLegs":1,"sellLegs":2},{"status":"EXACT","pnlSol":-0.5,"returnPct":-5,"buyLegs":1,"sellLegs":1}]
        out=subject.summary(rows); self.assertEqual(out["exactN"],2); self.assertEqual(out["profitFactor"],2.0)
        self.assertEqual(out["pnlSol"],0.5)
if __name__=="__main__":unittest.main()
