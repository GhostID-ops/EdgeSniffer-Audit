#!/usr/bin/env python3
"""Evidence-completeness gate for the existing frozen economic finalizer.

This process is deliberately conservative: it can publish a canonical replay
candidate only after exact CREATE-to-horizon evidence is present. It never
estimates missing events and it never submits anything.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

HORIZON_SLOTS = 1000
HORIZON_NS = 120_000_000_000


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def stable(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def read_jsonl(path: Path):
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


def load_outcome_keys(v4_dir: Path) -> set[str]:
    import gzip
    out = set()
    path = v4_dir / "outcome_events.jsonl.gz"
    if not path.exists():
        return out
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("signature") and row.get("mint"):
                out.add(f"{row['signature']}|{row['mint']}")
    return out


def load_create_evidence(root: Path) -> set[str]:
    out = set()
    path = root / "evidence" / "canonical-create-evidence.jsonl"
    for row in read_jsonl(path):
        if row.get("signature") and row.get("mint"):
            out.add(f"{row['signature']}|{row['mint']}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--tape", required=True)
    ap.add_argument("--v4-dir", required=True)
    ap.add_argument("--interval", type=float, default=10.0)
    args = ap.parse_args()
    root, ledger, tape = map(Path, (args.root, args.ledger, args.tape))
    root.mkdir(parents=True, exist_ok=True)
    state_path = root / "economic-finalization-state.json"
    artifact_path = root / "economic-gate-events.jsonl"
    previous = {}
    if state_path.exists():
        try:
            previous = json.loads(state_path.read_text(encoding="utf-8"))
        except ValueError:
            previous = {}
    while True:
        targets = {}
        for row in read_jsonl(ledger):
            if row.get("signature") and row.get("mint"):
                targets[int(row["canonical_ordinal"])] = row
        events = {}
        for row in read_jsonl(tape):
            try:
                ordinal = int(row["canonical_ordinal"])
            except (KeyError, TypeError, ValueError):
                continue
            events.setdefault(ordinal, []).append(row)
        existing = load_outcome_keys(Path(args.v4_dir))
        create_evidence = load_create_evidence(root.parent)
        try:
            tape_health = json.loads((tape.parent / "health.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            tape_health = {}
        source_scan_complete = bool(tape_health.get("source_scan_complete"))
        try:
            target_window = json.loads((tape.parent / "target-window-coverage.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            target_window = {}
        target_window_complete = target_window.get("status") == "PASS"
        source_max_ns = int(tape_health.get("max_source_received_ns") or 0)
        source_max_slot = int(tape_health.get("max_source_slot") or 0)
        statuses = {}
        for ordinal, target in sorted(targets.items()):
            rows = events.get(ordinal, [])
            create = [r for r in rows if r.get("evidence_kind") == "CREATE"]
            trades = [r for r in rows if r.get("evidence_kind") == "TRADE_EVENT"]
            complete_fields = all(
                r.get("transaction_signature") and r.get("slot") is not None and r.get("transaction_index") is not None
                and r.get("side") in {"buy", "sell"} and r.get("wallet") and r.get("quote_lamports") is not None
                and r.get("token_amount_raw") is not None and r.get("virtual_sol_reserves_after") is not None
                and r.get("virtual_token_reserves_after") is not None and r.get("source_received_ns")
                and r.get("raw_sha256") and r.get("exact_ordering")
                for r in trades
            )
            launch_ns = int(target.get("source_receive_ns") or 0)
            launch_slot = int(target.get("slot") or 0)
            max_ns = max((int(r.get("source_received_ns") or 0) for r in rows), default=0)
            max_slot = max((int(r.get("slot") or 0) for r in rows), default=0)
            horizon = (source_scan_complete or target_window_complete) and ((launch_ns > 0 and source_max_ns >= launch_ns + HORIZON_NS) or (launch_slot > 0 and source_max_slot >= launch_slot + HORIZON_SLOTS))
            key = f"{target['signature']}|{target['mint']}"
            if key in existing:
                status = "FINALIZED_FROZEN_V4_REFERENCE"
            elif create and trades and complete_fields and horizon:
                status = "READY_FOR_FROZEN_REPLAY"
            elif not create and key in create_evidence:
                status = "PARTIALLY_RECOVERABLE_CREATE_RPC_ONLY"
            elif not create:
                status = "UNRECOVERABLE_CREATE_NOT_IN_RETAINED_SOURCES"
            elif not trades:
                status = "PARTIALLY_RECOVERABLE_NO_TRADE_EVENTS"
            elif not horizon:
                status = "PARTIALLY_RECOVERABLE_HORIZON_NOT_CLOSED"
            else:
                status = "PARTIALLY_RECOVERABLE_REQUIRED_FIELDS_MISSING"
            statuses[str(ordinal)] = {
                "canonical_ordinal": ordinal, "signature": target["signature"], "mint": target["mint"],
                "status": status, "create_count": len(create), "trade_event_count": len(trades),
                "max_source_received_ns": max_ns, "max_slot": max_slot, "horizon_complete": bool(horizon),
                "required_fields_complete": bool(complete_fields),
                "frozen_methodology": "/usr/local/lib/edgesniffer-8ycb-shadow-v4/frozen_spec_template.json",
                "official_result_published": status == "FINALIZED_FROZEN_V4_REFERENCE",
                "submission_count": 0,
            }
        snapshot = {
            "schema": "8ycb-canonical-economic-finalization-state-v1", "updated_at_utc": now(),
            "frozen_methodology_unchanged": True,
            "automatic_pipeline": "canonical launch ledger -> canonical Pump tape -> horizon gate -> existing frozen replay -> canonical economic artifact -> rolling statistics",
            "automatic_finalization_enabled": True,
            "statuses": statuses,
            "real_market_submission_count": 0, "real_market_submission_enabled": False,
        }
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(stable(snapshot) + "\n", encoding="utf-8")
        os.replace(tmp, state_path)
        # Append only newly observed status transitions; no economic result is
        # published by this gate unless a frozen replay artifact exists.
        old_statuses = previous.get("statuses", {}) if isinstance(previous, dict) else {}
        with artifact_path.open("a", encoding="utf-8") as out:
            for ordinal, status in statuses.items():
                if old_statuses.get(ordinal, {}).get("status") != status["status"]:
                    out.write(stable({"schema": "8ycb-canonical-economic-gate-event-v1", "at": now(), **status}) + "\n")
        previous = snapshot
        print(stable({"targets": len(targets), "tape_events": sum(len(v) for v in events.values()), "ready": sum(v["status"] == "READY_FOR_FROZEN_REPLAY" for v in statuses.values())}), flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
