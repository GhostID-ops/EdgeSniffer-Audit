#!/usr/bin/env python3
"""Run the installed frozen V4 economics against complete canonical tape rows."""
from __future__ import annotations

import gzip
import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path

GUARD = "8YCB_OBSERVATION_ONLY_V3"
TARGET_WALLET = "8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ"
HORIZON_NS = 120_000_000_000
HORIZON_SLOTS = 1000


def read_jsonl(path):
    if not path.exists():
        return
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def load_module(path):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("canonical_frozen_shadow", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def stable(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def main():
    root = Path("/var/lib/8ycb-canonical")
    ledger_path = root / "launch-identity-ledger.jsonl"
    tape_path = root / "pump-tape" / "evidence.jsonl"
    health_path = root / "pump-tape" / "health.json"
    target_window_path = root / "pump-tape" / "target-window-coverage.json"
    out_root = Path(os.environ.get("CANONICAL_REPLAY_OUT_ROOT", str(root / "economics" / "replays")))
    artifact = Path(os.environ.get("CANONICAL_REPLAY_ARTIFACT", str(root / "economics" / "canonical-economic-artifact.jsonl")))
    ordinal_filter = {int(x) for x in os.environ.get("CANONICAL_REPLAY_ORDINALS", "").split(",") if x.strip()}
    once = os.environ.get("CANONICAL_REPLAY_ONCE") == "1"
    v4 = Path("/mnt/HC_Volume_106775198/edgesniffer-derived/8ycb-prospective-shadow-v4")
    shadow_path = Path("/usr/local/lib/edgesniffer-8ycb-shadow-v3/shadow_observer.py")
    out_root.mkdir(parents=True, exist_ok=True)
    os.environ["NO_SUBMIT_HARD_GUARD"] = GUARD
    mod = load_module(shadow_path)
    completed = set()
    for row in read_jsonl(artifact):
        if row.get("status") == "FINALIZED_FROZEN_CANONICAL":
            completed.add(f"{row.get('signature')}|{row.get('mint')}")
    while True:
        targets = {r["primary_key"]: r for r in read_jsonl(ledger_path) if r.get("primary_key")}
        by_ordinal = {}
        for row in read_jsonl(tape_path):
            try:
                by_ordinal.setdefault(int(row["canonical_ordinal"]), []).append(row)
            except (KeyError, TypeError, ValueError):
                pass
        try:
            health = json.loads(health_path.read_text())
        except (FileNotFoundError, ValueError):
            health = {}
        try:
            target_window = json.loads(target_window_path.read_text())
        except (FileNotFoundError, ValueError):
            target_window = {}
        for key, target in sorted(targets.items(), key=lambda item: int(item[1].get("canonical_ordinal", 0))):
            if ordinal_filter and int(target.get("canonical_ordinal", 0)) not in ordinal_filter:
                continue
            if key in completed:
                continue
            rows = by_ordinal.get(int(target["canonical_ordinal"]), [])
            creates = [r for r in rows if r.get("evidence_kind") == "CREATE"]
            trades = [r for r in rows if r.get("evidence_kind") == "TRADE_EVENT"]
            launch_ns = int(target.get("source_receive_ns") or 0)
            launch_slot = int(target.get("slot") or 0)
            max_ns = max((int(r.get("source_received_ns") or 0) for r in rows), default=0)
            max_slot = max((int(r.get("slot") or 0) for r in rows), default=0)
            horizon = (bool(health.get("source_scan_complete")) or (target_window.get("status") == "PASS" and int(target.get("canonical_ordinal", 0)) >= 143)) and ((launch_ns and int(health.get("max_source_received_ns") or 0) >= launch_ns + HORIZON_NS) or (launch_slot and int(health.get("max_source_slot") or 0) >= launch_slot + HORIZON_SLOTS))
            required = all(r.get("source_received_ns") and r.get("raw_sha256") and r.get("virtual_sol_reserves_after") is not None and r.get("virtual_token_reserves_after") is not None for r in trades)
            atomic = next((r for r in trades if r.get("side") == "buy" and r.get("transaction_signature") == target.get("signature") and r.get("wallet") == TARGET_WALLET), None)
            if not creates or not trades or not horizon or not required or not atomic:
                continue
            source_records = target.get("source_records") or []
            signal_record = next((r for r in source_records if r.get("schema") == "8ycb-prospective-signal-v3"), None)
            receive_ns = int((signal_record or {}).get("timing", {}).get("observer_create_decode_complete_unix_ns") or target.get("source_receive_ns") or atomic.get("source_received_ns") or 0)
            ready_ns = int((signal_record or {}).get("timing", {}).get("signal_ready_unix_ns") or receive_ns)
            if not receive_ns or not ready_ns:
                continue
            work = out_root / str(target["canonical_ordinal"])
            if work.exists():
                shutil.rmtree(work)
            work.mkdir(parents=True)
            global_feed, wallet_feed = work / "global.jsonl", work / "wallet.jsonl"
            global_feed.touch(); wallet_feed.touch()
            shadow = mod.Shadow(v4 / "frozen_spec.json", work, global_feed, wallet_feed, None, initial_global_offset=0, initial_wallet_offset=0)
            signal = {
                "schema": "8ycb-sniper-qualified-signal-v1", "kind": "sniper_qualified_signal", "event_id": f"8ycb-canonical-{target['canonical_ordinal']}",
                "cohort_start_utc": shadow.spec["cohort_start_utc"], "frozen_spec_sha256": shadow.spec_hash,
                "mint": target["mint"], "trigger_signature": target["signature"], "target_wallet": TARGET_WALLET,
                "slot": target["slot"], "transaction_index": target.get("transaction_index"), "source": "CANONICAL_PUMP_TAPE",
                "source_receive_ns": receive_ns, "decode_complete_ns": receive_ns, "signal_ready_ns": ready_ns,
                "transaction_succeeded": True, "target_is_signer": True, "creator_condition_proven": True, "outer_create_v2_proven": True,
                "trigger_mode": "ATOMIC_TARGET_BUY", "qualification_version": "8YCB_ATOMIC_TARGET_BUY_FROZEN_V1",
                "atomic_buy": {"same_transaction": True, "same_mint": True, "same_user": True, "nonzero_qualifying_operand": True, "quote_lamports": atomic["quote_lamports"], "first_operand": 1, "second_operand": 0},
                "timing": {"chain_or_block_timestamp_ms": None, "earliest_feed_receive_ms": receive_ns / 1e6, "decoded_create_ms": receive_ns / 1e6, "decoded_atomic_buy_ms": receive_ns / 1e6, "signal_ready_unix_ns": ready_ns, "observer_create_line_read_unix_ns": receive_ns, "observer_create_decode_start_unix_ns": receive_ns, "observer_create_decode_complete_unix_ns": receive_ns, "observer_atomic_line_read_unix_ns": receive_ns, "observer_atomic_decode_start_unix_ns": receive_ns, "observer_atomic_decode_complete_unix_ns": receive_ns},
                "atomic_initial_buy": {"quote_lamports": atomic["quote_lamports"], "tokens_out_raw": atomic["token_amount_raw"], "source": "canonical_pump_tape"},
                "event_definition_result": "CREATE_AND_SAME_TRANSACTION_8YCB_ATOMIC_BUY_PROVEN", "transaction_construction": "OMITTED_V3",
            }
            shadow.process_sniper_signal(json.dumps(signal), ready_ns)
            for row in sorted(trades, key=lambda r: (int(r.get("slot") or 0), int(r.get("transaction_index") or 0), r.get("exact_ordering", ""))):
                side = row["side"]
                flow = {"side": side, "transaction_signature": row["transaction_signature"], "mint": row["mint"], "wallet": row["wallet"], "source_slot": row["slot"], "source_transaction_index": row["transaction_index"], "local_receive_ts_ms": int(row["source_received_ns"]) / 1e6, "quote_lamports": row["quote_lamports"] if side == "buy" else None, "sell_quote_lamports": row["quote_lamports"] if side == "sell" else None, "sell_token_amount_raw": row["token_amount_raw"] if side == "sell" else 0, "raw_record": {"instructionIndex": row.get("instruction_coordinate"), "trade_event": True, "token_amount_raw": row["token_amount_raw"], "virtual_sol_reserves_after": row.get("virtual_sol_reserves_after"), "virtual_token_reserves_after": row.get("virtual_token_reserves_after")}}
                shadow.process_global(json.dumps(flow), int(float(flow["local_receive_ts_ms"]) * 1_000_000))
            shadow.finalize_ready()
            if not shadow.outcomes:
                continue
            shadow.write_outputs(); shadow.write_manifest()
            outcome = shadow.outcomes[-1]
            primary = next((r for r in outcome.get("scenario_rows", []) if r.get("lane") == "ACTUAL_SHADOW_SIGNAL_READY" and r.get("first_sellable_ms_after_entry") == 178 and not r.get("adverse_entry_stress")), None)
            row = {"schema": "8ycb-canonical-economic-result-v1", "status": "FINALIZED_FROZEN_CANONICAL", "canonical_ordinal": target["canonical_ordinal"], "signature": target["signature"], "mint": target["mint"], "source_tape_event_count": len(rows), "frozen_spec_sha256": shadow.spec_hash, "outcome": outcome, "primary": primary, "submission_count": 0, "submission_enabled": False}
            with artifact.open("a", encoding="utf-8") as f:
                f.write(stable(row) + "\n")
            completed.add(key)
        if once:
            return
        time.sleep(10)


if __name__ == "__main__":
    main()
