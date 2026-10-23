#!/usr/bin/env python3
"""Regression matrix for durable observed-wallet mint history."""
import importlib.util, hashlib, json, tempfile
from pathlib import Path
P=Path(__file__).with_name("kefi_observed_wallet_ledger_v1.py"); s=importlib.util.spec_from_file_location("ledger",P); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
def action(mint, signature, slot, second, side="BUY"):
 return {"wallet":"w","mint":mint,"action_id":f"{signature}:{mint}:{side}","signature":signature,"slot":slot,"block_time":second,"transaction_summary_sha256":f"h-{signature}","side":side}
def main():
 b=100_500_000_000; rows=[action("X","a",1,101),action("Y","b",2,102),action("Y","c",3,103),action("X","d",4,104)]
 first=m.build(rows,"w",b,"input"); second=m.build(list(reversed(rows)),"w",b,"input")
 assert first==second # deterministic replay
 assert [x["mint"] for x in first]==["X","Y"] # A/X remains non-new after restart; B/Y is new once, C is not
 assert all(x["record_sha256"]==m.record_hash(x) for x in first)
 bad=[dict(x) for x in first]; bad[0]["mint"]="tampered"; assert bad[0]["record_sha256"]!=m.record_hash(bad[0]) # E conflict/corruption detectable
 assert first[0]["observed_at_unix_ns"] < first[1]["observed_at_unix_ns"] # future action cannot revise prior classification
 try: m.build([action("Z","edge",9,100)],"w",b,"input")
 except RuntimeError: pass
 else: raise AssertionError("boundary ambiguity accepted")
 with tempfile.TemporaryDirectory() as temp:
  receipt=Path(temp)/"tail.json"; actions=Path(temp)/"actions.jsonl"
  actions.write_text('{"action":"current"}\n')
  value={"schema":"KEFI_WALLET_ACTION_TAIL_SYNC_V1","wallet":"w","actions_sha256":hashlib.sha256(actions.read_bytes()).hexdigest(),"replay_overlap_slots":32,"started_finalized_slot":10,"completed_finalized_slot":11,"replay_from_slot":0}
  receipt.write_text(json.dumps(value))
  assert m.validated_tail_receipt(receipt,"w",value["actions_sha256"])["replay_from_slot"]==0
  value["actions_sha256"]="wrong"; receipt.write_text(json.dumps(value))
  try: m.validated_tail_receipt(receipt,"w",hashlib.sha256(actions.read_bytes()).hexdigest())
  except RuntimeError: pass
  else: raise AssertionError("stale tail receipt accepted")
 print("PASS")
if __name__=="__main__": main()
