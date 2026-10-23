#!/usr/bin/env python3
"""Outcome-blind integrity gate for the prospective kEFi paper result."""
from __future__ import annotations
import argparse, hashlib, json
from collections import Counter
from pathlib import Path

EXPECTED_MODEL = "e13ceacd57462addf03ee2c1c7ad40ca0db1aa9936c64ceaa44e2cfca06eead4"

def digest(p: Path) -> str:
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1<<20),b""): h.update(b)
    return h.hexdigest()

def jsonl(p: Path):
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]

def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--model",type=Path,required=True)
    ap.add_argument("--candidates",type=Path,required=True)
    ap.add_argument("--windows",type=Path,required=True)
    ap.add_argument("--signals",type=Path,required=True)
    ap.add_argument("--summary",type=Path,required=True)
    ap.add_argument("--receipt",type=Path,required=True)
    ns=ap.parse_args()
    candidates=jsonl(ns.candidates); windows=jsonl(ns.windows); signals=jsonl(ns.signals)
    candidate_keys=[(x.get("signature"),x.get("event_index",0)) for x in candidates]
    window_keys=[(x.get("target_signature"),x.get("target_event_index",0)) for x in windows]
    signal_keys=[(x.get("signature"),x.get("event_index",0)) for x in signals]
    duplicate_candidates=[k for k,v in Counter(candidate_keys).items() if v>1]
    duplicate_windows=[k for k,v in Counter(window_keys).items() if v>1]
    duplicate_signals=[k for k,v in Counter(signal_keys).items() if v>1]
    window_by_sig={x.get("target_signature"):x for x in windows}
    source_checks=[]; manifest_checks=[]
    for c in candidates:
        sig=c["signature"]
        result=ns.root/"source-spool-results"/(sig+".json")
        ok=result.exists()
        result_row={}
        if ok:
            result_row=json.loads(result.read_text(encoding="utf-8")); ok=result_row.get("status")=="COMPLETE"
        source_checks.append(ok)
        mroot=ns.root/str(result_row.get("windowRoot",""))
        mp=mroot/"manifest.json"
        mok=mp.exists()
        if mok:
            mr=json.loads(mp.read_text(encoding="utf-8"))
            mok=(mr.get("candidate",{}).get("signature")==sig and
                 mr.get("outcomeBlind") is True and
                 mr.get("postEntryDataMustBeExcluded") is True and
                 int(mr.get("requestedCaptureEndNs",0))-int(mr.get("requestedCaptureStartNs",0))==120_000_000_000)
            for sp in mr.get("sourcePartitions",[]):
                pp=ns.root/"source-spool"/"partitions"/sp["partitionKey"] / "manifest.json"
                if not pp.exists() or json.loads(pp.read_text(encoding="utf-8")).get("temporalHourClosed") is not True: mok=False
        manifest_checks.append(mok)
    model_sha=digest(ns.model)
    window_complete=all(x.get("window_complete") is True and int(x.get("replay_event_count",0))>0 for x in windows)
    signal_refs=all((x.get("signature"),x.get("event_index",0)) in set(window_keys) for x in signals)
    passed=(model_sha==EXPECTED_MODEL and not duplicate_candidates and not duplicate_windows and not duplicate_signals and
            all(source_checks) and all(manifest_checks) and window_complete and signal_refs)
    receipt={
        "schema":"KEFI_PROSPECTIVE_PAPER_GATE_INTEGRITY_RECEIPT_V1",
        "status":"PASS" if passed else "FAIL_CLOSED",
        "model_sha256":model_sha,
        "expected_model_sha256":EXPECTED_MODEL,
        "candidate_rows":len(candidates),
        "complete_windows":len(windows),
        "signals":len(signals),
        "duplicate_candidates":duplicate_candidates,
        "duplicate_windows":duplicate_windows,
        "duplicate_signals":duplicate_signals,
        "all_source_results_complete":all(source_checks),
        "all_window_manifests_verified":all(manifest_checks),
        "all_replay_windows_have_events":window_complete,
        "signal_window_identity_check":signal_refs,
        "outcomes_accessed":False,"pnl_accessed":False,"execution_enabled":False,
        "window_completeness_basis":"materializer COMPLETE result + closed source manifest + nonempty canonical replay event set",
    }
    ns.receipt.parent.mkdir(parents=True,exist_ok=True)
    ns.receipt.write_text(json.dumps(receipt,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(receipt,sort_keys=True))
    return 0 if passed else 2
if __name__=="__main__": raise SystemExit(main())
