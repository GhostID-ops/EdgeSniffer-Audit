#!/usr/bin/env python3
"""Bounded offline control replay through the installed frozen Shadow class."""
from __future__ import annotations

import gzip
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path


def read_gz(path):
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def load_module(path):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("frozen_shadow", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def main():
    root = Path("/var/lib/8ycb-canonical")
    tape_path = root / "pump-tape" / "evidence.jsonl"
    health = json.loads((root / "pump-tape" / "health.json").read_text()) if (root / "pump-tape" / "health.json").exists() else {}
    ledger_path = root / "launch-identity-ledger.jsonl"
    v4 = Path("/mnt/HC_Volume_106775198/edgesniffer-derived/8ycb-prospective-shadow-v4")
    shadow_path = Path("/usr/local/lib/edgesniffer-8ycb-shadow-v3/shadow_observer.py")
    controls = {140, 141, 142}
    ledger = {int(json.loads(x)["canonical_ordinal"]): json.loads(x) for x in ledger_path.read_text().splitlines() if x.strip()}
    tape = {n: [] for n in controls}
    for row in read_jsonl(tape_path):
        n = int(row.get("canonical_ordinal", -1))
        if n in tape:
            tape[n].append(row)
    signals = {}
    for row in read_gz(v4 / "signal_events.jsonl.gz"):
        for n in controls:
            if row.get("signature") == ledger[n]["signature"] and row.get("mint") == ledger[n]["mint"]:
                signals[n] = row
    # The old authoritative V4 artifact defines the historical event-scope
    # boundary for equivalence.  Values are still taken exclusively from the
    # canonical tape below; these signatures only prevent newly retained
    # canonical events outside the old V4 scope from changing the replay.
    v4_scope = {n: set() for n in controls}
    for row in read_jsonl(v4 / "event-scoped-normalized.jsonl"):
        for n in controls:
            if row.get("mint") == ledger[n]["mint"] and row.get("transaction_signature"):
                v4_scope[n].add(row["transaction_signature"])
    mod = load_module(shadow_path)
    out_root = root / "control-replay"
    out_root.mkdir(parents=True, exist_ok=True)
    report = {"schema": "8ycb-canonical-control-replay-v1", "controls": {}, "submission_count": 0, "submission_enabled": False}
    os.environ["NO_SUBMIT_HARD_GUARD"] = "8YCB_OBSERVATION_ONLY_V3"
    for n in sorted(controls):
        rows = tape.get(n, [])
        signal_row = signals.get(n)
        result = {"canonical_ordinal": n, "tape_rows": len(rows), "signal_present": bool(signal_row), "status": "NOT_RUN"}
        target = ledger[n]
        max_slot = max((int(r.get("slot") or 0) for r in rows), default=0)
        max_ns = max((int(r.get("source_received_ns") or 0) for r in rows), default=0)
        launch_ns = int(target.get("source_receive_ns") or 0)
        horizon = bool(health.get("source_scan_complete")) and ((launch_ns > 0 and int(health.get("max_source_received_ns") or 0) >= launch_ns + 120_000_000_000) or (int(health.get("max_source_slot") or 0) >= int(target.get("slot") or 0) + 1000))
        if not signal_row or not any(r.get("evidence_kind") == "CREATE" for r in rows) or not any(r.get("evidence_kind") == "TRADE_EVENT" for r in rows):
            result["status"] = "INSUFFICIENT_TAPE"
        elif not horizon:
            result["status"] = "HORIZON_OPEN"
            result["horizon_complete"] = False
        else:
            result["horizon_complete"] = True
        if result["status"] != "NOT_RUN":
            report["controls"][str(n)] = result
            continue
        work = out_root / str(n)
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        global_feed = work / "global.jsonl"
        wallet_feed = work / "wallet.jsonl"
        global_feed.touch(); wallet_feed.touch()
        shadow = mod.Shadow(v4 / "frozen_spec.json", work, global_feed, wallet_feed, None, initial_global_offset=0, initial_wallet_offset=0)
        atomic = next((r for r in rows if r.get("evidence_kind") == "TRADE_EVENT" and r.get("side") == "buy" and r.get("transaction_signature") == signal_row.get("signature") and r.get("wallet") == mod.TARGET), None)
        if not atomic:
            result["status"] = "INSUFFICIENT_ATOMIC_BUY"
            report["controls"][str(n)] = result
            continue
        receive_ns = int(signal_row["timing"].get("observer_create_decode_complete_unix_ns") or atomic["source_received_ns"])
        ready_ns = int(signal_row["timing"].get("signal_ready_unix_ns") or receive_ns)
        atomic_quote = atomic["quote_lamports"]
        if os.environ.get("CONTROL_DIAGNOSTIC_LEGACY_QUOTE") == "1":
            atomic_quote = signal_row.get("atomic_initial_buy", {}).get("quote_lamports", atomic_quote)
        qualified = {
            "schema": "8ycb-sniper-qualified-signal-v1", "kind": "sniper_qualified_signal",
            "qualification_version": "8YCB_ATOMIC_TARGET_BUY_FROZEN_V1", "trigger_mode": "ATOMIC_TARGET_BUY",
            "transaction_succeeded": True, "target_is_signer": True, "creator_condition_proven": True,
            "outer_create_v2_proven": True, "target_wallet": mod.TARGET, "mint": signal_row["mint"],
            "trigger_signature": signal_row["signature"], "source": "CANONICAL_PUMP_TAPE",
            "slot": signal_row["source_slot"], "transaction_index": signal_row.get("source_transaction_index"),
            "source_receive_ns": receive_ns, "decode_complete_ns": receive_ns, "signal_ready_ns": ready_ns,
            "atomic_buy": {"same_transaction": True, "same_mint": True, "same_user": True, "nonzero_qualifying_operand": True, "quote_lamports": atomic_quote, "first_operand": 1, "second_operand": 0},
        }
        shadow.process_sniper_signal(json.dumps(qualified), ready_ns)
        flow_rows = []
        for r in sorted(rows, key=lambda x: (int(x.get("slot") or 0), int(x.get("transaction_index") or 0), x.get("exact_ordering", ""))):
            if r.get("evidence_kind") == "TRADE_EVENT" and r.get("transaction_signature") not in v4_scope[n]:
                continue
            if r.get("evidence_kind") == "CREATE":
                flow_rows.append({"side":"create", "creation_proven":True, "transaction_signature":r["transaction_signature"], "mint":r["mint"], "source_slot":r["slot"], "source_transaction_index":r["transaction_index"], "local_receive_ts_ms":int(r["source_received_ns"])/1e6})
            elif r.get("evidence_kind") == "TRADE_EVENT":
                side = r["side"]
                flow_rows.append({"side":side, "transaction_signature":r["transaction_signature"], "mint":r["mint"], "wallet":r["wallet"], "source_slot":r["slot"], "source_transaction_index":r["transaction_index"], "local_receive_ts_ms":int(r["source_received_ns"])/1e6, "quote_lamports":r["quote_lamports"] if side=="buy" else None, "sell_quote_lamports":r["quote_lamports"] if side=="sell" else None, "sell_token_amount_raw":r["token_amount_raw"] if side=="sell" else 0, "raw_record":{"instructionIndex":r.get("instruction_coordinate"), "trade_event":True, "token_amount_raw":r["token_amount_raw"], "virtual_sol_reserves_after":r.get("virtual_sol_reserves_after"), "virtual_token_reserves_after":r.get("virtual_token_reserves_after")}})
        for row in flow_rows:
            shadow.process_global(json.dumps(row), int(float(row["local_receive_ts_ms"])*1_000_000))
        shadow.finalize_ready()
        shadow.write_outputs()
        shadow.write_manifest()
        expected = next((r for r in read_gz(v4 / "outcome_events.jsonl.gz") if r.get("signature") == signal_row.get("signature")), None)
        actual = shadow.outcomes[-1] if shadow.outcomes else None
        exp_primary = next((r for r in (expected or {}).get("scenario_rows", []) if r.get("lane") == "ACTUAL_SHADOW_SIGNAL_READY" and r.get("first_sellable_ms_after_entry") == 178 and not r.get("adverse_entry_stress")), None)
        act_primary = next((r for r in (actual or {}).get("scenario_rows", []) if r.get("lane") == "ACTUAL_SHADOW_SIGNAL_READY" and r.get("first_sellable_ms_after_entry") == 178 and not r.get("adverse_entry_stress")), None)
        result.update({"status":"MATCH" if exp_primary and act_primary and exp_primary.get("net_roi_pct") == act_primary.get("net_roi_pct") else "MISMATCH", "expected_net_roi_pct":(exp_primary or {}).get("net_roi_pct"), "replay_net_roi_pct":(act_primary or {}).get("net_roi_pct"), "expected_tape_flow_count":(expected or {}).get("tape_flow_count"), "replay_tape_flow_count":(actual or {}).get("tape_flow_count")})
        report["controls"][str(n)] = result
    report["control_launches_tested"] = sum(v.get("status") in {"MATCH","MISMATCH"} for v in report["controls"].values())
    report["control_replay_match_count"] = sum(v.get("status") == "MATCH" for v in report["controls"].values())
    report["control_replay_mismatch_count"] = sum(v.get("status") == "MISMATCH" for v in report["controls"].values())
    (root / "control-replay-report.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")


def read_jsonl(path):
    if not path.exists(): return []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try: yield json.loads(line)
            except ValueError: continue


if __name__ == "__main__":
    main()
