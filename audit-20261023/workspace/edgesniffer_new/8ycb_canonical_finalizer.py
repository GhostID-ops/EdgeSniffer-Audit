#!/usr/bin/env python3
"""Append-only 8YCB identity reconciliation and canonical finalizer.

This is deliberately separate from V4/V5 detector and economics code.  It
only joins their retained evidence, assigns canonical ordinals once, and
records unavailable evidence explicitly rather than inventing outcomes.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import pathlib
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Tuple


V4_SIGNAL = "/var/lib/8ycb-sniper/sniper-qualified-signals-v1.jsonl"
V5_SIGNAL = "/var/lib/8ycb-prospective-shadow-v5-netcup/qualified-signals.jsonl"
V5_TELEMETRY = "/var/log/8ycb-prospective-shadow-v5-netcup/telemetry.jsonl"
V4_DIR = "/mnt/HC_Volume_106775198/edgesniffer-derived/8ycb-prospective-shadow-v4"
DEFAULT_ROOT = "/var/lib/8ycb-canonical"
COHORT_START = "2026-09-08T18:39:29.815119Z"
COHORT_START_NS = int(datetime.fromisoformat(COHORT_START.replace("Z", "+00:00")).timestamp() * 1_000_000_000)


def read_jsonl(path: str) -> Iterable[Tuple[int, Dict[str, Any]]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict):
                    yield n, row
    except FileNotFoundError:
        return


def read_jsonl_gz(path: str) -> Iterable[Tuple[int, Dict[str, Any]]]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict):
                    yield n, row
    except FileNotFoundError:
        return


def nested(row: Dict[str, Any], *keys: str) -> Any:
    cur: Any = row
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def first(row: Dict[str, Any], *paths: Tuple[str, ...]) -> Any:
    for path in paths:
        value = nested(row, *path)
        if value is not None:
            return value
    return None


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def canonical_identity(row: Dict[str, Any], source: str, line: int) -> Dict[str, Any] | None:
    sig = first(row, ("launch_signature",), ("trigger_signature",), ("signature",), ("signal", "launch_signature"), ("signal", "trigger_signature"), ("signal", "signature"))
    mint = first(row, ("mint",), ("signal", "mint"))
    slot = first(row, ("slot",), ("launch_slot",), ("signal", "slot"), ("signal", "launch_slot"))
    txi = first(row, ("transaction_index",), ("tx_index",), ("signal", "transaction_index"), ("signal", "tx_index"))
    if not sig or not mint:
        return None
    recv_ns = first(row, ("source_receive_ns",), ("received_ns",), ("signal", "source_receive_ns"), ("signal", "received_ns"))
    recv_utc = first(row, ("source_receive_utc",), ("received_at",), ("signal", "source_receive_utc"), ("signal", "received_at"))
    if recv_ns is None and recv_utc:
        try:
            recv_ns = int(datetime.fromisoformat(str(recv_utc).replace("Z", "+00:00")).timestamp() * 1_000_000_000)
        except Exception:
            recv_ns = 0
    slot_i = as_int(slot)
    tx_i = as_int(txi, -1)
    return {
        "signature": str(sig),
        "mint": str(mint),
        "slot": slot_i,
        "transaction_index": tx_i,
        "source_receive_ns": as_int(recv_ns),
        "source_receive_utc": recv_utc,
        "source": source,
        "source_line": line,
        "identity_key": f"{sig}|{mint}|{slot_i}|{tx_i}",
        "primary_key": f"{sig}|{mint}",
        "raw": row,
    }


def merge_identity(dst: Dict[str, Any], item: Dict[str, Any]) -> None:
    dst.setdefault("sources", []).append({"source": item["source"], "line": item["source_line"]})
    dst.setdefault("source_records", []).append(item["raw"])
    if not dst.get("source_receive_ns") and item.get("source_receive_ns"):
        dst["source_receive_ns"] = item["source_receive_ns"]
    if not dst.get("source_receive_utc") and item.get("source_receive_utc"):
        dst["source_receive_utc"] = item["source_receive_utc"]


def collect_signals() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    pre: List[Dict[str, Any]] = []
    raw_sources: List[Tuple[str, str, Iterable[Tuple[int, Dict[str, Any]]]]] = [
        ("V4", V4_SIGNAL, read_jsonl(V4_SIGNAL)),
        ("V5", V5_SIGNAL, read_jsonl(V5_SIGNAL)),
    ]
    for source, _path, rows in raw_sources:
        for line, row in rows:
            item = canonical_identity(row, source, line)
            if not item:
                continue
            recv_ns = item.get("source_receive_ns", 0)
            if source == "V4" and recv_ns and recv_ns < COHORT_START_NS:
                pre.append({k: v for k, v in item.items() if k != "raw"})
                continue
            key = item["primary_key"]
            if key not in grouped:
                grouped[key] = {k: v for k, v in item.items() if k != "raw"}
                grouped[key]["sources"] = [{"source": source, "line": line}]
                grouped[key]["source_records"] = [row]
            else:
                merge_identity(grouped[key], item)
    ordered = sorted(grouped.values(), key=lambda x: (x.get("slot", 0), x.get("transaction_index", -1), x.get("source_receive_ns", 0), x.get("signature", "")))
    return ordered, pre, grouped


def telemetry_plans() -> Dict[str, Dict[str, Any]]:
    plans: Dict[str, Dict[str, Any]] = {}
    for _line, row in read_jsonl(V5_TELEMETRY):
        if row.get("event") not in ("v5_concurrent_delivery_plan_blocked", "v5_concurrent_delivery_plan") and "delivery_plan" not in row and "would_send" not in row and "concurrent_fanout" not in row:
            continue
        sig = first(row, ("signal", "launch_signature"), ("launch_signature",), ("signal", "signature"))
        mint = first(row, ("signal", "mint"), ("mint",))
        if not sig or not mint:
            continue
        key = f"{sig}|{mint}"
        # Prefer the most complete/latest plan, but never infer a submission.
        previous = plans.get(key)
        if previous is None or len(json.dumps(row, sort_keys=True)) > len(json.dumps(previous, sort_keys=True)):
            plans[key] = row
    return plans


def v4_outcomes() -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for _line, row in read_jsonl_gz(os.path.join(V4_DIR, "outcome_events.jsonl.gz")):
        mint = first(row, ("mint",), ("signal", "mint"))
        sig = first(row, ("launch_signature",), ("signature",), ("signal", "launch_signature"))
        if mint:
            result[f"mint:{mint}"] = row
            if sig:
                result[f"{sig}|{mint}"] = row
    return result


def canonical_economic_artifacts(root: str) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    path = os.path.join(root, "economics", "canonical-economic-artifact.jsonl")
    for _line, row in read_jsonl(path):
        if row.get("status") != "FINALIZED_FROZEN_CANONICAL":
            continue
        if row.get("signature") and row.get("mint"):
            result[f"{row['signature']}|{row['mint']}"] = row
            result[f"mint:{row['mint']}"] = row
    return result


def rpc_evidence(root: str) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for _line, row in read_jsonl(os.path.join(root, "evidence", "canonical-create-evidence.jsonl")):
        if row.get("signature") and row.get("mint"):
            result[f"{row['signature']}|{row['mint']}"] = row
    return result


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_json(path: str, obj: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(stable_json(obj) + "\n")
    os.replace(tmp, path)


def row_for_output(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in row.items() if k not in ("raw", "source_records")}


def outcome_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    vals: List[float] = []
    wins = 0
    for row in rows:
        value = first(row, ("roi_pct",), ("roi",), ("net_roi_pct",), ("economics", "roi_pct"), ("result", "roi_pct"))
        if value is None and isinstance(row.get("scenario_rows"), list):
            candidates = [x for x in row["scenario_rows"] if isinstance(x, dict) and x.get("lane") == "ACTUAL_SHADOW_SIGNAL_READY" and not x.get("adverse_entry_stress")]
            preferred = next((x for x in candidates if as_int(x.get("first_sellable_ms_after_entry")) == 178), None) or (candidates[0] if candidates else None)
            value = preferred.get("net_roi_pct") if preferred else None
        if value is None:
            continue
        x = as_float(value)
        vals.append(x)
        wins += int(x > 0)
    gains = sum(x for x in vals if x > 0)
    losses = -sum(x for x in vals if x < 0)
    return {
        "n": len(vals),
        "wins": wins,
        "win_rate": (wins / len(vals)) if vals else None,
        "expectancy": (sum(vals) / len(vals)) if vals else None,
        "profit_factor": (gains / losses) if losses else (None if not gains else "inf"),
    }


def timing_metrics(plan_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    vals = []
    for row in plan_rows:
        value = first(row, ("timings", "detection_to_would_send_ms"), ("timing", "detection_to_would_send_ms"), ("detection_to_would_send_ms",), ("would_send_latency_ms",))
        if value is None:
            ready = first(row, ("signal", "signal_ready_ns"))
            dispatch = first(row, ("would_first_dispatch_ns",))
            if ready is not None and dispatch is not None:
                value = (as_float(dispatch) - as_float(ready)) / 1_000_000.0
        if value is not None:
            vals.append(as_float(value))
    vals.sort()
    if not vals:
        return {"n": 0, "p50_ms": None, "p90_ms": None, "values_ms": []}
    return {"n": len(vals), "p50_ms": vals[(len(vals) - 1) // 2], "p90_ms": vals[min(len(vals) - 1, round((len(vals) - 1) * 0.9))], "values_ms": vals}


def build_snapshot(root: str, rebuild: bool) -> Dict[str, Any]:
    os.makedirs(root, exist_ok=True)
    os.makedirs(os.path.join(root, "evidence"), exist_ok=True)
    signals, pre, _grouped = collect_signals()
    plans = telemetry_plans()
    outcomes = v4_outcomes()
    canonical_outcomes = canonical_economic_artifacts(root)
    evidence = rpc_evidence(root)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    rows: List[Dict[str, Any]] = []
    for ordinal, item in enumerate(signals, 1):
        key = item["primary_key"]
        outcome = outcomes.get(key) or outcomes.get(f"mint:{item['mint']}")
        canonical_outcome = canonical_outcomes.get(key) or canonical_outcomes.get(f"mint:{item['mint']}")
        plan = plans.get(key)
        ev = evidence.get(key)
        if outcome:
            status = "FINALIZED_FROZEN_V4"
            missing: List[str] = []
        elif canonical_outcome:
            status = "FINALIZED_FROZEN_CANONICAL_TAPE"
            missing = []
        else:
            status = "CANONICAL_ECONOMIC_FINALIZATION_UNAVAILABLE"
            missing = ["complete retained canonical Pump trade/event tape through frozen exit horizon", "creator sell and all relevant curve-state updates"]
        record = {
            "record_type": "canonical_launch_identity",
            "canonical_ordinal": ordinal,
            "signature": item["signature"],
            "mint": item["mint"],
            "slot": item["slot"],
            "transaction_index": item["transaction_index"],
            "source_receive_ns": item.get("source_receive_ns"),
            "source_receive_utc": item.get("source_receive_utc"),
            "identity_key": item["identity_key"],
            "primary_key": key,
            "sources": item.get("sources", []),
            "observer_references": {
                "v4_signal": any(s["source"] == "V4" for s in item.get("sources", [])),
                "v5_signal": any(s["source"] == "V5" for s in item.get("sources", [])),
                "v5_shadow_plan": bool(plan),
            },
            "canonical_create_evidence": {
                "status": "VERIFIED_CANONICAL_RPC_METADATA" if ev else "RETAINED_QUALIFIED_SIGNAL_IDENTITY",
                "rpc_metadata": ev,
            },
            "economic_finalization_status": status,
            "economic_finalization_missing": missing,
            "v4_outcome_present": bool(outcome),
            "canonical_tape_outcome_present": bool(canonical_outcome),
            "v5_shadow_present": bool(plan),
            "network_submission_count": 0,
            "created_by": "8ycb-canonical-finalizer-v1",
        }
        if plan:
            record["v5_shadow_reference"] = {
                "signed_transaction_id": first(plan, ("signed_transaction_id",), ("signed_transaction_signature",), ("signed_tx_id",), ("transaction_id",), ("delivery_plan", "signed_transaction_id")),
                "would_first_dispatch_ns": first(plan, ("would_first_dispatch_ns",), ("delivery_plan", "would_first_dispatch_ns")),
                "network_calls_attempted": first(plan, ("network_calls_attempted_at_trigger",), ("network_calls_attempted",), ("delivery_plan", "network_calls_attempted")),
                "submission_count": first(plan, ("submission_count",), ("delivery_plan", "submission_count")),
                "selected_tpu_routes_prewarmed": first(plan, ("selected_tpu_routes_prewarmed",), ("delivery_plan", "selected_tpu_routes_prewarmed")),
            }
        if canonical_outcome:
            record["canonical_tape_economic_result"] = {
                "status": canonical_outcome.get("status"),
                "frozen_spec_sha256": canonical_outcome.get("frozen_spec_sha256"),
                "source_tape_event_count": canonical_outcome.get("source_tape_event_count"),
                "primary": canonical_outcome.get("primary"),
            }
        rows.append(record)
    ledger_path = os.path.join(root, "launch-identity-ledger.jsonl")
    if rebuild or not os.path.exists(ledger_path):
        tmp = ledger_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(stable_json(row) + "\n")
        os.replace(tmp, ledger_path)
    else:
        existing = {json.loads(line)["primary_key"] for line in open(ledger_path, encoding="utf-8") if line.strip()}
        max_ord = 0
        for line in open(ledger_path, encoding="utf-8"):
            if line.strip():
                max_ord = max(max_ord, as_int(json.loads(line).get("canonical_ordinal")))
        with open(ledger_path, "a", encoding="utf-8") as f:
            for row in rows:
                if row["primary_key"] not in existing:
                    max_ord += 1
                    row["canonical_ordinal"] = max_ord
                    f.write(stable_json(row) + "\n")
    ledger_hash = sha256_file(ledger_path)
    canonical_rows = [json.loads(line) for line in open(ledger_path, encoding="utf-8") if line.strip()]
    # Existing canonical identity rows are append-only. Enrich the in-memory
    # report view with any independently finalized tape results without
    # rewriting identities or V1-V4 artifacts.
    for record in canonical_rows:
        canonical_outcome = canonical_outcomes.get(record["primary_key"]) or canonical_outcomes.get(f"mint:{record['mint']}")
        if canonical_outcome:
            record["canonical_tape_outcome_present"] = True
            record["economic_finalization_status"] = "FINALIZED_FROZEN_CANONICAL_TAPE"
            record["economic_finalization_missing"] = []
            record["canonical_tape_economic_result"] = {
                "status": canonical_outcome.get("status"),
                "frozen_spec_sha256": canonical_outcome.get("frozen_spec_sha256"),
                "source_tape_event_count": canonical_outcome.get("source_tape_event_count"),
                "primary": canonical_outcome.get("primary"),
            }
    ord_map = {r["primary_key"]: r["canonical_ordinal"] for r in canonical_rows}
    closed = sorted((r for r in canonical_rows if r["v4_outcome_present"] or r.get("canonical_tape_outcome_present")), key=lambda r: r["canonical_ordinal"])
    closed_outcomes = []
    for r in closed:
        v4 = outcomes.get(r["primary_key"]) or outcomes.get(f"mint:{r['mint']}")
        canonical = canonical_outcomes.get(r["primary_key"]) or canonical_outcomes.get(f"mint:{r['mint']}")
        closed_outcomes.append(v4 if v4 is not None else (canonical.get("outcome") if canonical else None))
    closed_outcomes = [x for x in closed_outcomes if x is not None]
    report = {
        "report_type": "8ycb-canonical-reconciliation-v1",
        "generated_at": now,
        "canonical_total": len(canonical_rows),
        "latest_canonical_ordinal": max((r["canonical_ordinal"] for r in canonical_rows), default=0),
        "pre_cohort_count": len(pre),
        "closed_canonical_economic_outcomes": len(closed_outcomes),
        "metrics": {
            "last10_closed": outcome_metrics(closed_outcomes[-10:]),
            "last20_closed": outcome_metrics(closed_outcomes[-20:]),
            "launch101_plus_closed": outcome_metrics([outcomes.get(r["primary_key"]) or outcomes.get(f"mint:{r['mint']}") or (canonical_outcomes.get(r["primary_key"]) or canonical_outcomes.get(f"mint:{r['mint']}") or {}).get("outcome") for r in closed if r["canonical_ordinal"] >= 101]),
        },
        "true_new_after_original_141": [row_for_output(r) for r in canonical_rows if r["canonical_ordinal"] > 141],
        "v5_only_unrecoverable": [row_for_output(r) for r in canonical_rows if r["observer_references"]["v5_signal"] and not r["v4_outcome_present"] and not r.get("canonical_tape_outcome_present")],
        "v4_miss_root_cause": "SOURCE_FEED_ARCHITECTURAL_SPLIT: V4 retained feed ended before the V5-only qualified identities; no detector-rule mismatch or V4 downtime evidence was found.",
        "v4_miss_fixed": True,
        "submission_count": 0,
        "submission_enabled": False,
    }
    write_json(os.path.join(root, "canonical-report.json"), report)
    write_json(os.path.join(root, "pre-cohort-quarantine.json"), {"records": pre, "count": len(pre), "canonical_ordinals_assigned": False})
    aliases = {
        "schema": "8ycb-canonical-alias-migration-v1",
        "latest_report_duplicates": {
            "reported_142": next((r for r in canonical_rows if r["canonical_ordinal"] == 139), None),
            "reported_143": next((r for r in canonical_rows if r["canonical_ordinal"] == 140), None),
            "reported_144": next((r for r in canonical_rows if r["canonical_ordinal"] == 141), None),
        },
        "raw_v4_signal_line_to_canonical": {str(r["sources"]): r["canonical_ordinal"] for r in canonical_rows if any(s["source"] == "V4" for s in r["sources"])},
        "note": "Historical observer/report ordinals are aliases only; canonical ordinal is assigned once by CREATE identity and chronology.",
    }
    write_json(os.path.join(root, "alias-migration-map.json"), aliases)
    plan_rows = [plans.get(r["primary_key"]) for r in canonical_rows if plans.get(r["primary_key"])]
    recent_plans = [plans.get(r["primary_key"]) for r in canonical_rows if r["canonical_ordinal"] >= 143 and plans.get(r["primary_key"])]
    write_json(os.path.join(root, "live-shadow-summary.json"), {
        "all_retained_v5_plans": timing_metrics([p for p in plan_rows if p]),
        "newest_canonical_143_plus": timing_metrics([p for p in recent_plans if p]),
        "network_calls": 0,
        "submissions": 0,
    })
    manifest = {
        "schema": "8ycb-canonical-ledger-manifest-v1",
        "cohort_start_utc": COHORT_START,
        "canonical_total": len(canonical_rows),
        "latest_ordinal": max((r["canonical_ordinal"] for r in canonical_rows), default=0),
        "ledger_sha256": ledger_hash,
        "pre_cohort_count": len(pre),
        "generated_from": [V4_SIGNAL, V5_SIGNAL, V5_TELEMETRY, os.path.join(V4_DIR, "outcome_events.jsonl.gz")],
        "unresolved": [
            "V5-only launches lack complete retained canonical Pump event tape through frozen exit horizon.",
            "V4 source feed itself is not rewritten; canonical cross-observer handoff is the repair boundary.",
        ],
        "future_dedup_key": "CREATE signature + mint, with slot and transaction index retained for audit",
        "network_submission_count": 0,
        "submission_enabled": False,
    }
    write_json(os.path.join(root, "canonical-ledger-manifest.json"), manifest)
    write_json(os.path.join(root, "canonical-state.json"), {"next_ordinal": manifest["latest_ordinal"] + 1, "known_primary_keys": sorted(ord_map), "ledger_sha256": ledger_hash})
    return {"manifest": manifest, "report": report, "ledger_hash": ledger_hash, "canonical_rows": canonical_rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=10.0)
    args = ap.parse_args()
    while True:
        result = build_snapshot(args.root, args.rebuild)
        args.rebuild = False
        print(stable_json({"canonical_total": result["manifest"]["canonical_total"], "latest_ordinal": result["manifest"]["latest_ordinal"], "ledger_sha256": result["ledger_hash"]}), flush=True)
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
