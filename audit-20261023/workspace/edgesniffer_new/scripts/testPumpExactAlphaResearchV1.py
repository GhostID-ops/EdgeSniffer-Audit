#!/usr/bin/env python3
import gzip,json,pathlib,sys,tempfile,unittest
sys.path.insert(0,str(pathlib.Path(__file__).parent))
from buildPumpExactAlphaResearchV1 import EventRow,PriorWalletIndex,feature_row,label,retain_pump_trade_arrival,retain_target_arrival
from exactCurveEconomicsV1 import CurveState,PumpInstruction,SourceObservation,TradeEvent
from scanPumpExactAlphaFingerprintsV1 import first_per_mint,main as scan_main,metric

class ExactAlphaResearchTests(unittest.TestCase):
 def event(self):
  event=TradeEvent("mint",1_000_000_000,25_000_000_000,True,"buyer",1,
   CurveState(31_000_000_000,1_045_000_000_000,1_000_000_000,765_000_000_000),
   "fee",95,9_500_000,"creator",30,3_000_000,False,"buy",False,0,0,0,0,(),
   "11111111111111111111111111111111",1_000_000_000,31_000_000_000,1_000_000_000)
  source=SourceObservation("mint","sig","buy",1,0,0,0,PumpInstruction("buy",25_000_000_000,2_000_000_000,False),(),"raw")
  return EventRow("sig","observed-evidence","evidence",8,1,0,0,source,event)
 def test_label_is_nonlinear_and_exact_without_mc_proxy(self):
  row=self.event();small=label([row],25,.10,25,500);large=label([row],25,1.0,25,500)
  self.assertEqual("EXACT",small["status"]);self.assertEqual("EXACT",large["status"])
  self.assertNotEqual(small["tokensReceivedRaw"]*10,large["tokensReceivedRaw"])
  self.assertEqual("MAX_HOLD",small["outcome"])
  self.assertEqual(525,small["firstSellableAtMs"])
  self.assertEqual("decision",small["firstSellableFrom"])
  self.assertEqual("evidence",small["entryStateEvidenceId"])
 def test_missing_entry_state_fails_closed(self):
  self.assertEqual("ENTRY_UNRESOLVED",label([],25,.5,25,500)["status"])
 def test_features_use_observation_availability_cutoff(self):
  event=self.event()
  with tempfile.TemporaryDirectory() as tmp:
   index=PriorWalletIndex(pathlib.Path(tmp)/"wallets.sqlite")
   index.replace_partition("p","hash",{
    ("buyer","mint"):(1,"same-mint"),
    ("buyer","prior-mint"):(9,"prior-wallet"),
    ("buyer","future-mint"):(20,"future-wallet"),
   },0)
   self.assertEqual(0,index.malformed_count())
   row=feature_row("mint",{"createReceivedAtUnixNs":1_000_000,"createSlot":1,"createSignature":"create","createObservationEvidenceId":"create-evidence"},
    [event],(1_000_000_000,20,"future-supply"),10,[9,12],[9,12],[(9,"prior"),(12,"future")],
    index,[(9,"failed-prior"),(12,"failed-future")])
   index.close()
  self.assertTrue(row["availabilityCausalityPass"])
  self.assertEqual(1,row["features"]["failedTransactionCount"])
  self.assertEqual(10000,row["features"]["recurrentWalletShareBps"])
  self.assertEqual(["prior-wallet"],row["causalInputs"]["recurrentWalletPriorEvidenceIds"])
  self.assertIsNone(row["features"]["marketCapSol"])
  self.assertEqual(0,row["features"]["marketCapAvailable"])
  self.assertIsNone(row["causalInputs"]["supplyObservation"])
  self.assertEqual(9,row["causalInputs"]["maxFeatureObservationUnixMs"])
 def test_unrelated_duplicate_cannot_erase_target_arrival(self):
  arrivals={};evidence={}
  retain_target_arrival(arrivals,evidence,"sig",10,"target-evidence",{"mint"})
  retain_target_arrival(arrivals,evidence,"sig",11,"unrelated-evidence",set())
  self.assertEqual({"sig":10},arrivals)
  self.assertEqual({"sig":"target-evidence"},evidence)
 def test_pump_trade_arrival_does_not_require_raw_mint_discovery(self):
  arrivals={};evidence={}
  retain_pump_trade_arrival(arrivals,evidence,"later-sig",12,"later-evidence",{"BUY"})
  self.assertEqual({"later-sig":12},arrivals)
  self.assertEqual({"later-sig":"later-evidence"},evidence)
  retain_pump_trade_arrival(arrivals,evidence,"not-trade",13,"ignored",{"CREATE"})
  self.assertNotIn("not-trade",arrivals)
 def test_scanner_economics_are_fee_aware_label_outputs(self):
  def row(mint,value,outcome): return {"mint":mint,"labels":{"size_0.50_entry_25_sellable_500":{"status":"EXACT","returnPct":value,"pnlSol":value/200,"outcome":outcome}}}
  result=metric([row("one",20,"TP"),row("two",-10,"SL")])
  self.assertEqual(2,result["N"]);self.assertAlmostEqual(2,result["PF"]);self.assertAlmostEqual(.05,result["expectancy"])
  self.assertAlmostEqual(.05,result["actualPnlSol"])
 def test_metric_rejects_opportunity_row_inflation(self):
  row={"mint":"same","labels":{"size_0.50_entry_25_sellable_500":{"status":"EXACT","returnPct":20,"pnlSol":.1,"outcome":"TP"}}}
  with self.assertRaisesRegex(RuntimeError,"PSEUDOREPLICATION_BLOCKER"):
   metric([row,row])
 def test_earliest_qualifying_opportunity_is_the_only_trade(self):
  rows=[{"mint":"same","decisionUnixMs":200},{"mint":"same","decisionUnixMs":100},{"mint":"other","decisionUnixMs":150}]
  selected=first_per_mint(rows)
  self.assertEqual({"same":100,"other":150},{row["mint"]:row["decisionUnixMs"] for row in selected})
 def test_n3000_checkpoint_runs_once_without_opening_later_prefixes(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);(root/"rows-v2").mkdir();(root/"manifests-v2").mkdir()
   (root/"status.json").write_text(json.dumps({"exactAdmittedN":3000,"discoveryMintsAvailable":3000}))
   with (root/"discovery-mint-index.jsonl").open("w") as handle:
    for i in range(3000):
     handle.write(json.dumps({"ordinal":i+1,"mint":f"mint{i}","partitionKey":"part","createReceivedAtUnixNs":(1_700_000_000_000+i*1000)*1_000_000})+"\n")
   keys=["size_0.01_entry_25_sellable_500","size_0.10_entry_25_sellable_500",
    "size_0.50_entry_25_sellable_500","size_1.00_entry_25_sellable_500",
    "size_0.50_entry_50_sellable_500","size_0.50_entry_100_sellable_500",
    "size_0.50_entry_25_sellable_1000"]
   with gzip.open(root/"rows-v2"/"part.jsonl.gz","wt") as handle:
    for i in range(3000):
     ret=20 if i%3 else -20
     labels={k:{"status":"EXACT","returnPct":ret,"pnlSol":ret/100*float(k.split("_")[1]),"outcome":"TP" if ret>0 else "SL","replayCanonicalEvidenceIds":[f"canonical-{i}"]} for k in keys}
     features={"buyCount":2+(i%3),"sellCount":i%2,"buyCount100ms":2+(i%2),"sellPressure":0.05*(i%4),
      "netBuySol":float(i%10),"firstBuyerShareBps":4000+(i%3)*500,"marketCapSol":5+(i%40),"globalTrades60sPrior":50+(i%400)}
     row={"mint":f"mint{i}","decisionUnixMs":1_700_000_000_000+i*1000,
      "decisionTimestampUtc":"2023-11-14T22:13:20+00:00","observationState":"FIXED_100MS","features":features,"labels":labels}
     handle.write(json.dumps(row)+"\n")
   row_sha=__import__("hashlib").sha256((root/"rows-v2"/"part.jsonl.gz").read_bytes()).hexdigest()
   (root/"manifests-v2"/"part.json").write_text(json.dumps({"partitionKey":"part","sha256":row_sha,"sourceFingerprintSha256":"source"}))
   old=sys.argv
   try:
    sys.argv=["scan",str(root)];self.assertEqual(0,scan_main())
   finally:sys.argv=old
   report=json.loads((root/"scans"/"N3000.json").read_text())
   self.assertEqual(3000,report["population"]["exactAdmittedPrefixMints"])
   self.assertEqual(0,report["validationRowsInspected"])
   self.assertFalse((root/"scans"/"N5000.json").exists())

if __name__=="__main__":unittest.main()
