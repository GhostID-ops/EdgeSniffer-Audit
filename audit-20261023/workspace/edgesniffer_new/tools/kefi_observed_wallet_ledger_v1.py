#!/usr/bin/env python3
"""Create an append-only kEFi observed-wallet mint ledger from canonical actions.

This is a provenance/backfill tool only. It never reads market state, choice
snapshots, evaluator output, outcomes, or PnL.
"""
from __future__ import annotations
import argparse, hashlib, json, os
from pathlib import Path

SCHEMA = "KEFI_OBSERVED_WALLET_MINT_LEDGER_V1"

def canonical(v): return json.dumps(v, sort_keys=True, separators=(",", ":"))
def sha(path: Path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1<<20),b""): h.update(b)
    return h.hexdigest()
def atomic(path: Path, content: bytes):
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+".tmp")
    with tmp.open("wb") as f: f.write(content); f.flush(); os.fsync(f.fileno())
    os.replace(tmp,path)
def record_hash(r):
    text="|".join(str(r[k]) for k in ("sequence","ledger_boundary_unix_ns","mint","first_action_identity","signature","slot","observed_at_unix_ns","source_provenance_sha256","previous_record_sha256"))
    return hashlib.sha256(text.encode()).hexdigest()

def build(actions: list[dict], wallet: str, boundary_ns: int, input_sha: str) -> list[dict]:
    boundary_second=boundary_ns//1_000_000_000
    # A second-resolution RPC action exactly at the nanosecond source boundary
    # cannot be safely ordered, so refuse to silently classify it either way.
    if any(int(x.get("block_time") or -1)==boundary_second for x in actions):
        raise RuntimeError("FAIL_CLOSED: action at second-resolution ledger boundary")
    eligible=[x for x in actions if x.get("wallet")==wallet and x.get("mint") and int(x.get("block_time") or -1)>boundary_second]
    eligible.sort(key=lambda x:(int(x.get("block_time") or 0),int(x.get("slot") or 0),str(x.get("signature")),str(x.get("mint")),str(x.get("action_id"))))
    seen=set(); output=[]; prev="GENESIS"
    for action in eligible:
        mint=str(action["mint"])
        if mint in seen: continue
        seen.add(mint)
        item={"schema":SCHEMA,"sequence":len(output)+1,"ledger_boundary_unix_ns":boundary_ns,"mint":mint,
              "first_action_identity":str(action["action_id"]),"signature":str(action["signature"]),"slot":int(action.get("slot") or 0),
              "observed_at_unix_ns":int(action["block_time"])*1_000_000_000,
              "source_provenance_sha256":str(action["transaction_summary_sha256"]),"previous_record_sha256":prev,
              "source_kind":"FINALIZED_RPC_DIRECT_WALLET_ACTION"}
        item["record_sha256"]=record_hash(item); prev=item["record_sha256"]; output.append(item)
    return output

def validated_tail_receipt(path: Path | None, wallet: str, actions_sha256: str) -> dict | None:
    if path is None:
        return None
    value = json.loads(path.read_text())
    if value.get("schema") != "KEFI_WALLET_ACTION_TAIL_SYNC_V1":
        raise RuntimeError("FAIL_CLOSED: unexpected tail-sync schema")
    if value.get("wallet") != wallet or value.get("actions_sha256") != actions_sha256:
        raise RuntimeError("FAIL_CLOSED: tail-sync receipt is not bound to current wallet actions")
    if int(value.get("replay_overlap_slots", 0)) < 32:
        raise RuntimeError("FAIL_CLOSED: insufficient source replay overlap")
    if int(value.get("completed_finalized_slot", -1)) < int(value.get("started_finalized_slot", 0)):
        raise RuntimeError("FAIL_CLOSED: tail-sync finalized frontier regressed")
    return value

def main():
    p=argparse.ArgumentParser(); p.add_argument("--wallet-actions",type=Path,required=True); p.add_argument("--wallet",required=True); p.add_argument("--ledger-root",type=Path,required=True); p.add_argument("--boundary-unix-ns",type=int,required=True); p.add_argument("--source-manifest",type=Path,required=True); p.add_argument("--tail-sync-receipt",type=Path)
    a=p.parse_args(); ledger=a.ledger_root/"ledger.jsonl"; manifest=a.ledger_root/"manifest.json"
    if ledger.exists() or manifest.exists(): raise SystemExit("FAIL_CLOSED: ledger root already initialized")
    source=json.loads(a.source_manifest.read_text())
    if source.get("schema") != "KEFI_DIRECT_WALLET_ACTION_INDEX_MANIFEST_V1" or source.get("wallet") != a.wallet:
        raise SystemExit("FAIL_CLOSED: source manifest is not the requested wallet index")
    actions_sha = sha(a.wallet_actions)
    tail_sync = validated_tail_receipt(a.tail_sync_receipt, a.wallet, actions_sha)
    if source.get("actions_sha256") != actions_sha and tail_sync is None:
        raise SystemExit("FAIL_CLOSED: wallet actions differ from source manifest without bound tail-sync receipt")
    actions=[json.loads(x) for x in a.wallet_actions.read_text().splitlines() if x.strip()]
    boundary_second = a.boundary_unix_ns // 1_000_000_000
    boundary_ambiguous_actions = sum(1 for action in actions if int(action.get("block_time") or -1) == boundary_second)
    rows=build(actions,a.wallet,a.boundary_unix_ns,sha(a.wallet_actions))
    if not rows: raise SystemExit("FAIL_CLOSED: no wallet actions after fixed boundary")
    atomic(ledger,("\n".join(canonical(x) for x in rows)+"\n").encode())
    result={"schema":"KEFI_OBSERVED_WALLET_MINT_LEDGER_MANIFEST_V1","wallet":a.wallet,"ledger_boundary_unix_ns":a.boundary_unix_ns,"ledger_path":str(ledger),"ledger_sha256":sha(ledger),"record_count":len(rows),"distinct_mints":len(rows),"tail_record_sha256":rows[-1]["record_sha256"],"wallet_actions_path":str(a.wallet_actions),"wallet_actions_sha256":actions_sha,"source_manifest_sha256":sha(a.source_manifest),"source_index_coverage":source.get("coverage"),"tail_sync_receipt_sha256":sha(a.tail_sync_receipt) if a.tail_sync_receipt else None,"tail_sync_replay_from_slot":tail_sync.get("replay_from_slot") if tail_sync else None,"boundary_ambiguous_actions":boundary_ambiguous_actions,"boundary_time_resolution_policy":"second-resolution source actions exactly at the nanosecond boundary are rejected; actions strictly later are included","outcomes_or_pnl_accessed":False}
    atomic(manifest,(canonical(result)+"\n").encode()); print(canonical(result))
if __name__=="__main__": main()
