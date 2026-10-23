#!/usr/bin/env python3
"""Ranker V2 production live-translation research harness.

No-submit only. This harness never sends transactions; it feeds Ranker V2
signals plus matched execution capsules into the Rust dry-builder and records
whether the production construction path could have reached the first-send
boundary.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import select
import struct
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


COHORT = "RANKER_V2_PROSPECTIVE_SHADOW_V2"
THRESHOLD = 0.590596
SPEC_HASH = "ca785e8d919ccc795be2c41c9787ad5a77617767cd75f748a111f93eb00cacee"
OBJECT_HASH = "3784908b49f724ba664cde3cd355b4aee30f5bfa48c73f6797845cd7719e2938"
START_UTC = "2026-09-03T14:26:22.198000Z"
OUT = Path("/mnt/HC_Volume_106775198/edgesniffer-derived/ranker-v2-prospective-shadow-v2")
FREEZE = Path("/mnt/HC_Volume_106775198/edgesniffer-derived/ranker-v2-event-gated-semantic-freeze")
CANONICAL_SPEC = Path("/mnt/HC_Volume_106775198/edgesniffer-derived/executable-alpha-ranker-v2-canonical-freeze/canonical_score_spec.json")
SOURCE_INPUT = Path("/var/lib/edgesniffer-teacher-impulse-parity-v1/candidate-capsules.jsonl")
TRANSLATION_OUT = Path("/mnt/HC_Volume_106775198/edgesniffer-derived/ranker-v2-production-translation-v1")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def now_ms() -> int:
    return int(time.time() * 1000)


def now_ns() -> int:
    return time.time_ns()


def finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def compact(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 9) if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: compact(val) for key, val in value.items()}
    if isinstance(value, list):
        return [compact(item) for item in value]
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(compact(value), handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(compact(value), sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_obj(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def percentile(values: list[float], p: float) -> float | None:
    xs = sorted(v for v in values if finite(v))
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * p
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - pos) + xs[hi] * (pos - lo)


def dist_us(values: list[int]) -> dict[str, Any]:
    ms = [v / 1000.0 for v in values if isinstance(v, int) and v >= 0]
    return {
        "n": len(ms),
        "unit": "ms",
        "p50": percentile(ms, 0.50),
        "p75": percentile(ms, 0.75),
        "p90": percentile(ms, 0.90),
        "p95": percentile(ms, 0.95),
        "p99": percentile(ms, 0.99),
        "max": max(ms) if ms else None,
        "le5ms_pct": sum(1 for x in ms if x <= 5) * 100.0 / len(ms) if ms else None,
        "le10ms_pct": sum(1 for x in ms if x <= 10) * 100.0 / len(ms) if ms else None,
        "le25ms_pct": sum(1 for x in ms if x <= 25) * 100.0 / len(ms) if ms else None,
        "le50ms_pct": sum(1 for x in ms if x <= 50) * 100.0 / len(ms) if ms else None,
    }


def read_gzip_jsonl(path: Path):
    proc = subprocess.Popen(
        ["gzip", "-cd", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue
    proc.wait()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def f64_from_lossless(obj: Any) -> float:
    if isinstance(obj, dict) and "float_hex" in obj:
        return float.fromhex(obj["float_hex"])
    if isinstance(obj, dict) and "bits_hex_be" in obj:
        return struct.unpack(">d", int(obj["bits_hex_be"], 16).to_bytes(8, "big"))[0]
    if isinstance(obj, dict) and "decimal_repr" in obj:
        return float(obj["decimal_repr"])
    return float(obj)


def load_spec() -> tuple[dict[str, Any], dict[str, Any], list[tuple[str, float]], float]:
    spec = load_json(CANONICAL_SPEC)
    stats = {}
    for item in spec["normalization_constants_lossless"]:
        stats[item["feature"]] = {
            "center": f64_from_lossless(item["center_f64"]),
            "scale": f64_from_lossless(item["scale_f64"]),
        }
    weights = [(item["feature"], f64_from_lossless(item["weight_f64"])) for item in spec["weights_lossless"]]
    threshold = f64_from_lossless(spec["threshold"]["value_f64"])
    return spec, stats, weights, threshold


def score_with_contribs(features: dict[str, Any], stats: dict[str, Any], weights: list[tuple[str, float]]) -> float | None:
    score = 0.0
    used = 0
    for name, weight in weights:
        value = features.get(name)
        if not finite(value):
            continue
        st = stats[name]
        z = max(-5.0, min(5.0, (float(value) - st["center"]) / st["scale"]))
        score += weight * z
        used += 1
    return score if used else None


class Builder:
    def __init__(self, path: Path) -> None:
        env = os.environ.copy()
        env["RUST_SELECTED_CANDIDATE_SUBMIT_ENABLED"] = "false"
        env.setdefault("RANKER_V2_TRANSLATION_PRIORITY_FEE_MICRO_LAMPORTS", "0")
        self.proc = subprocess.Popen(
            [str(path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            text=True,
            bufsize=1,
            env=env,
        )
        if self.proc.stdout is None:
            raise RuntimeError("builder stdout unavailable")
        ready, _, _ = select.select([self.proc.stdout], [], [], 20.0)
        if not ready:
            self.proc.kill()
            raise RuntimeError("Ranker builder did not become ready")
        self.ready = json.loads(self.proc.stdout.readline())
        if self.ready.get("kind") != "rankerV2ProductionNoSubmitBuilderReady":
            raise RuntimeError(f"bad builder greeting: {self.ready}")

    def build(self, signal: dict[str, Any], capsule: dict[str, Any], adapter_ns: int) -> dict[str, Any]:
        if self.proc.stdin is None or self.proc.stdout is None:
            raise RuntimeError("builder pipes unavailable")
        req = {
            "kind": "buildRankerV2ProductionNoSubmit",
            "signal": signal,
            "capsule": capsule,
            "adapterReceiveUnixNs": adapter_ns,
        }
        self.proc.stdin.write(json.dumps(req, separators=(",", ":")) + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            self.proc.wait(timeout=3)


def minimal_signal(signal: dict[str, Any], rebase: bool) -> dict[str, Any]:
    out = {
        "cohort": signal["cohort"],
        "mint": signal["mint"],
        "state": signal["state"],
        "historicalStateAlias": signal.get("historical_state_alias"),
        "eventGatedStateTsMs": int(signal["event_gated_state_ts_ms"]),
        "thresholdDecisionTsMs": int(signal["threshold_decision_ts_ms"]),
        "score": float(signal["score"]),
        "scoreThreshold": float(signal["score_threshold"]),
        "passesThreshold": bool(signal["passes_threshold"]),
        "sourceRowId": signal.get("source_row_id"),
        "mcUsd": signal.get("mc_usd"),
    }
    if rebase:
        t = now_ms()
        out["eventGatedStateTsMs"] = t - 2
        out["thresholdDecisionTsMs"] = t - 1
    return out


def capsule_ok(row: dict[str, Any]) -> bool:
    return bool(
        row.get("candidate", {}).get("mint")
        and row.get("execution", {}).get("expected_tokens_out_raw")
        and (row.get("extra_decision_fields") or {}).get("bondingCurve")
        and (row.get("extra_decision_fields") or {}).get("sourceIxAccounts")
    )


def minimal_capsule(row: dict[str, Any]) -> dict[str, Any]:
    candidate = row.get("candidate") or {}
    canonical = row.get("canonical_state") or {}
    execution = row.get("execution") or {}
    extra = row.get("extra_decision_fields") or {}
    return {
        "transaction_signature": row.get("transaction_signature") or row.get("raw_record", {}).get("signature"),
        "source_slot": row.get("source_slot") or row.get("raw_record", {}).get("source_slot"),
        "source_transaction_index": row.get("source_transaction_index") or row.get("raw_record", {}).get("source_transaction_index"),
        "candidate": {
            "mint": candidate.get("mint") or row.get("raw_record", {}).get("mint"),
            "tokenProgram": candidate.get("token_program") or row.get("raw_record", {}).get("tokenProgram"),
            "sourceIxDataBase64": candidate.get("source_ix_data_base64") or row.get("raw_record", {}).get("sourceIxDataBase64"),
        },
        "canonical_state": {
            "sourceSlot": canonical.get("source_slot"),
            "observedAtMs": canonical.get("observed_at_ms"),
            "marketCapUsd": canonical.get("market_cap_usd"),
        },
        "execution": {
            "positionSizeLamports": execution.get("position_size_lamports"),
            "expectedTokensOutRaw": execution.get("expected_tokens_out_raw"),
        },
        "extra_decision_fields": {
            "bondingCurve": extra.get("bondingCurve") or extra.get("bonding_curve"),
            "sourceIxAccounts": extra.get("sourceIxAccounts") or extra.get("source_ix_accounts"),
        },
    }


def capsule_rank(row: dict[str, Any]) -> tuple[int, int, int]:
    token_program = (row.get("candidate") or {}).get("token_program") or (row.get("raw_record") or {}).get("tokenProgram")
    accounts = (row.get("extra_decision_fields") or {}).get("sourceIxAccounts") or []
    source_data = (row.get("candidate") or {}).get("source_ix_data_base64")
    observed = int((row.get("canonical_state") or {}).get("observed_at_ms") or 0)
    side = (row.get("raw_record") or {}).get("side")
    native_kind = (row.get("raw_record") or {}).get("nativeKind")
    is_token2022 = token_program == "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
    sell_template_quality = 2 if is_token2022 and len(accounts) >= 26 else 1 if (not is_token2022 and len(accounts) >= 16) else 0
    buy_source_quality = 2 if side == "buy" and source_data and accounts else 1 if native_kind == "creation" else 0
    return (buy_source_quality, sell_template_quality, observed)


def load_candidate_map(signals: list[dict[str, Any]], start_offset: int) -> dict[str, dict[str, Any]]:
    wanted = {s["mint"] for s in signals}
    matched: dict[str, dict[str, Any]] = {}
    ranks: dict[str, tuple[int, int, int]] = {}
    with SOURCE_INPUT.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(start_offset)
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            mint = row.get("candidate", {}).get("mint") or row.get("raw_record", {}).get("mint")
            if mint in wanted and capsule_ok(row):
                rank = capsule_rank(row)
                if rank > ranks.get(mint, (-1, -1, -1)):
                    ranks[mint] = rank
                    matched[mint] = minimal_capsule(row)
    return matched


def canonical_identity_report() -> dict[str, Any]:
    manifest = load_json(OUT / "cohort_manifest.json")
    freeze = load_json(FREEZE / "freeze_manifest.json")
    return {
        "cohort_manifest_hash_match": manifest.get("canonical_spec_sha256") == SPEC_HASH,
        "cohort_object_hash_match": manifest.get("canonical_object_sha256") == OBJECT_HASH,
        "freeze_classification": freeze.get("classification"),
        "freeze_hash_match": freeze.get("canonical_scorer_sha256") == SPEC_HASH,
        "object_hash_match": freeze.get("canonical_object_sha256") == OBJECT_HASH,
        "threshold": manifest.get("threshold"),
        "threshold_match": abs(float(manifest.get("threshold")) - THRESHOLD) <= 0.0,
        "contract_hashes": freeze.get("contract_hashes"),
        "entry_model": {"decision_plus_ms": 25},
        "sellability_model": {"P50": 964, "P75": 1476, "P90": 1989, "primary": "P75"},
        "exit_model": {"tp_pct": 20.0, "sl_pct": -20.0, "max_hold_ms": 120000},
        "earliest_trigger_per_mint": "first chronological eligible observation per mint with score >= 0.590596",
        "status": "PASS" if manifest.get("canonical_spec_sha256") == SPEC_HASH and freeze.get("classification") == "RANKER_V2_EVENT_GATED_FREEZE_PASS" else "FAIL",
    }


def parity_report(signals: list[dict[str, Any]]) -> dict[str, Any]:
    spec, stats, weights, threshold = load_spec()
    diffs = []
    threshold_disagreements = []
    for row in signals:
        score = score_with_contribs(row.get("raw_features") or {}, stats, weights)
        if score is None:
            threshold_disagreements.append({"mint": row.get("mint"), "reason": "score_uncomputable"})
            continue
        diffs.append(abs(score - float(row["score"])))
        if (score >= threshold) != bool(row["passes_threshold"]):
            threshold_disagreements.append({"mint": row.get("mint"), "stored": row["score"], "recomputed": score})
    first_by_mint = len({s["mint"] for s in signals}) == len(signals)
    return {
        "signals_checked": len(signals),
        "max_abs_score_diff": max(diffs) if diffs else None,
        "p99_abs_score_diff": percentile(diffs, 0.99),
        "threshold_disagreements": len(threshold_disagreements),
        "threshold_disagreement_sample": threshold_disagreements[:10],
        "duplicate_signal_mints": len(signals) - len({s["mint"] for s in signals}),
        "earliest_trigger_uniqueness": first_by_mint,
        "status": "PASS" if not threshold_disagreements and first_by_mint else "FAIL",
    }


def architecture_map() -> dict[str, Any]:
    return {
        "ranker_trigger": {
            "service": "edgesniffer-ranker-v2-prospective-shadow-v2.service",
            "script": "/mnt/HC_Volume_106775198/rankerV2EventGatedProspectiveV2.py",
            "outputs": [str(OUT / "signals.jsonl.gz"), str(OUT / "outcomes.jsonl.gz")],
        },
        "translation_adapter": {
            "script": "/mnt/HC_Volume_106775198/rankerV2ProductionTranslationV1.py",
            "role": "map frozen Ranker SHOULD_TRADE signal to execution capsule; no strategy reinterpretation",
            "queue": "poll compressed Ranker signal file + tail candidate-capsules source file",
            "filesystem_dependencies": [str(OUT / "signals.jsonl.gz"), str(SOURCE_INPUT)],
        },
        "rust_intent_and_tx_ready": {
            "binary": "/root/bot/EdgeSniffer/rust-sidecar/target/release/ranker_v2_no_submit_builder",
            "source": "/root/bot/EdgeSniffer/rust-sidecar/src/bin/ranker_v2_no_submit_builder.rs",
            "imports": ["/root/bot/EdgeSniffer/rust-sidecar/src/executor.rs", "/root/bot/EdgeSniffer/rust-sidecar/src/exit_watcher.rs"],
            "buy_functions": ["executor::dry_build_pumpfun_buy_command", "build_pumpfun_buy_tx", "build_pumpfun_buy_ix", "compile_and_sign"],
            "sell_functions": ["executor::dry_build_pumpfun_sell_command", "build_pumpfun_exit_sell_template_from_source_accounts", "compile_and_sign"],
        },
        "boundaries": [
            "Ranker gzip signal output filesystem boundary",
            "Python adapter in-process JSON mapping",
            "stdin/stdout JSON IPC to Rust dry-builder",
            "read-only getLatestBlockhash RPC for realistic message construction",
            "no network broadcast boundary: first-send is blocked at tx-ready",
        ],
        "serialization_steps": [
            "Ranker signal JSONL gzip write",
            "adapter JSON request",
            "Rust serde deserialize",
            "Solana message compile/sign",
            "bincode serialization inside dry-build response",
        ],
    }


def broadcast_safety(paths: list[Path]) -> dict[str, Any]:
    forbidden = [
        "send" + "Transaction",
        "send" + "Bundle",
        "broadcast_" + "signed_tx(",
        "ExecutePumpfun" + "Buy(",
        "ExecutePumpfun" + "Sell(",
    ]
    hits = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        for pat in forbidden:
            if pat in text:
                hits.append({"path": str(path), "pattern": pat})
    allowed = [
        "dry_build_pumpfun_buy_command",
        "dry_build_pumpfun_sell_command",
        "RUST_SELECTED_CANDIDATE_SUBMIT_ENABLED=false",
    ]
    return {
        "submission_count": 0,
        "forbidden_pattern_hits_in_harness": hits,
        "allowed_no_submit_mechanisms": allowed,
        "status": "PASS" if not hits else "FAIL",
    }


def run_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    identity = canonical_identity_report()
    signals = list(read_gzip_jsonl(OUT / "signals.jsonl.gz"))
    unique = []
    seen = set()
    for sig in sorted(signals, key=lambda r: (r.get("threshold_decision_ts_ms") or 0, r.get("mint") or "")):
        if sig.get("mint") in seen:
            continue
        seen.add(sig["mint"])
        unique.append(sig)
    parity = parity_report(unique)
    manifest = load_json(OUT / "cohort_manifest.json")
    if args.sample_scope == "all":
        sample = unique
    else:
        sample = unique[:25] + unique[190:210] + unique[300:400] + unique[-25:]
    dedup = []
    seen = set()
    for sig in sample:
        if sig["mint"] not in seen:
            seen.add(sig["mint"])
            dedup.append(sig)
    candidate_map = load_candidate_map(dedup, int(manifest["source_input_position_at_start"]))

    build_results = []
    reject_counts = Counter()
    builder = Builder(Path(args.builder))
    try:
        for sig in dedup[: args.max_builds]:
            capsule = candidate_map.get(sig["mint"])
            if not capsule:
                reject_counts["execution_template_unavailable"] += 1
                build_results.append({"ok": False, "mint": sig["mint"], "reason": "execution_template_unavailable", "submissionCount": 0})
                continue
            adapter_ns = now_ns()
            result = builder.build(minimal_signal(sig, rebase=True), capsule, adapter_ns)
            if not result.get("ok"):
                reject_counts[result.get("error") or "rust_dry_build_error"] += 1
            build_results.append(result)
    finally:
        builder.close()

    append_path = out / "snapshot_build_results.jsonl"
    if append_path.exists():
        append_path.unlink()
    for row in build_results:
        append_jsonl(append_path, row)

    ok = [r for r in build_results if r.get("ok")]
    buy_ready = [int(r["shadowDecisionToTxReadyUs"]) for r in ok]
    first_send = [int(r["shadowDecisionToFirstSendBoundaryUs"]) for r in ok]
    sell_ready = [int(r["sellDecisionToFirstSendBoundaryUs"]) for r in ok]
    adapter_to_intent = [int(r["productionReceiveToIntentUs"]) for r in ok]
    intent_to_ready = [int(r["intentToTxReadyUs"]) for r in ok]
    report = {
        "classification": None,
        "created_at_utc": utc_now(),
        "scope": "NO_SUBMIT_PRODUCTION_TRANSLATION_RESEARCH",
        "canonical_identity": identity,
        "canonical_parity": parity,
        "production_architecture": architecture_map(),
        "minimal_ranker_to_rust_interface": {
            "fields": ["signal_id", "mint", "state_ts", "decision_ts", "score", "lifecycle_state", "source_slot", "source_signature"],
            "principle": "Research decides SHOULD_TRADE; Rust execution decides HOW_TO_BUILD_AND_SEND.",
        },
        "fixture_set": {
            "historical_current_signals_loaded": len(unique),
            "deterministic_sample_requested": len(dedup),
            "candidate_capsules_matched": len(candidate_map),
            "build_attempts": len(build_results),
            "successful_dry_builds": len(ok),
            "reject_counts": dict(reject_counts),
        },
        "buy_latency": {
            "shadow_decision_to_production_receive": dist_us([int(r["softwareDecisionToAdapterUs"]) for r in ok]),
            "production_receive_to_intent": dist_us(adapter_to_intent),
            "intent_to_tx_ready": dist_us(intent_to_ready),
            "shadow_decision_to_tx_ready": dist_us(buy_ready),
            "shadow_decision_to_first_send_boundary": dist_us(first_send),
        },
        "critical_25ms_assumption": classify_25ms(first_send),
        "sell_path": {
            "exit_condition_known_to_sell_intent": dist_us([int(r["sellDecisionToIntentUs"]) for r in ok]),
            "sell_intent_to_tx_ready": dist_us([int(r["sellIntentToTxReadyUs"]) for r in ok]),
            "exit_condition_known_to_first_send_boundary": dist_us(sell_ready),
            "tp_sl_timeout_semantics": "TP20 / SL20 / true 120s preserved in shadow model; dry sell probes only construction readiness.",
            "inventory_source": "synthetic dry-build token amount; live first-sellable remains future tiny-live telemetry requirement",
        },
        "first_sellable_reality": {
            "status": "NOT_MEASURED_BY_NO_SUBMIT",
            "required_future_live_fields": [
                "entry submit ts",
                "landing slot/time",
                "actual token balance availability",
                "first exact sellable timestamp",
                "sell landing time",
            ],
        },
        "cost_slippage_reconciliation_schema": live_reconciliation_schema(),
        "broadcast_safety": broadcast_safety([Path(__file__), Path(args.builder_source)]),
        "collector_status": collector_status(),
    }
    report["classification"] = classify_final(report)
    write_json(out / "final_report.json", report)
    return report


def classify_25ms(first_send_us: list[int]) -> str:
    p75 = percentile([x / 1000.0 for x in first_send_us], 0.75)
    p90 = percentile([x / 1000.0 for x in first_send_us], 0.90)
    if p75 is not None and p75 <= 25 and (p90 is None or p90 <= 50):
        return "25MS_SOFTWARE_PATH_COMFORTABLE"
    if p50 := percentile([x / 1000.0 for x in first_send_us], 0.50):
        if p50 <= 25:
            return "25MS_SOFTWARE_PATH_MARGINAL"
    return "25MS_SOFTWARE_PATH_UNREALISTIC"


def live_reconciliation_schema() -> dict[str, list[str]]:
    return {
        "shadow": ["intended_entry_ts", "modeled_entry", "first_sellable_assumption", "shadow_exit", "shadow_return"],
        "live": [
            "submit_ts",
            "landing_slot",
            "landing_time",
            "actual_tokens_received",
            "actual_entry_price",
            "priority_fee",
            "network_fee",
            "jito_tip",
            "other_cost",
            "first_actual_sellable_ts",
            "exit_decision",
            "sell_submit",
            "sell_landing",
            "actual_proceeds",
            "realized_wallet_pnl",
        ],
        "derived": ["entry_degradation", "exit_degradation", "total_execution_degradation", "shadow_vs_live_pnl_gap"],
    }


def collector_status() -> dict[str, Any]:
    health = load_json(OUT / "service_health.json")
    return {
        "service": "edgesniffer-ranker-v2-prospective-shadow-v2.service",
        "pid": health.get("pid"),
        "n_restarts": health.get("n_restarts"),
        "cohort_start": START_UTC,
        "current_N": (health.get("counters") or {}).get("unique_triggered_mints"),
        "resolved": health.get("resolved_primary_outcomes"),
        "open": health.get("open_outcomes"),
        "submissions": health.get("submission_count"),
        "no_submit": health.get("no_submit"),
    }


def classify_final(report: dict[str, Any]) -> str:
    if report["broadcast_safety"]["status"] != "PASS":
        return "RANKER_V2_PRODUCTION_TRANSLATION_SAFETY_BLOCKER"
    if report["canonical_identity"]["status"] != "PASS" or report["canonical_parity"]["status"] != "PASS":
        return "RANKER_V2_PRODUCTION_TRANSLATION_PARITY_BLOCKER"
    if report["fixture_set"]["successful_dry_builds"] == 0:
        return "RANKER_V2_PRODUCTION_TRANSLATION_NEEDS_MAJOR_ENGINEERING"
    if report["fixture_set"]["reject_counts"]:
        return "RANKER_V2_PRODUCTION_TRANSLATION_NEEDS_MINOR_ENGINEERING"
    if report["critical_25ms_assumption"] == "25MS_SOFTWARE_PATH_UNREALISTIC":
        return "RANKER_V2_PRODUCTION_TRANSLATION_LATENCY_BLOCKER"
    return "RANKER_V2_PRODUCTION_TRANSLATION_READY_FOR_FUTURE_TINY_LIVE"


def run_service(args: argparse.Namespace) -> int:
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    state_path = out / "state.json"
    if state_path.exists():
        state = load_json(state_path)
    else:
        current_mints = sorted({row.get("mint") for row in read_gzip_jsonl(OUT / "signals.jsonl.gz") if row.get("mint")})
        state = {
            "processed_mints": current_mints,
            "source_offset": SOURCE_INPUT.stat().st_size,
            "submissionCount": 0,
            "startedAtUtc": utc_now(),
            "futureOnlyInitializedFromSignalCount": len(current_mints),
        }
    processed = set(state.get("processed_mints", []))
    capsule_cache: dict[str, dict[str, Any]] = {}
    builder = Builder(Path(args.builder))
    append_jsonl(out / "service_events.jsonl", {"kind": "service_start", "atUtc": utc_now(), "builder": builder.ready, "submissionCount": 0})
    try:
        while True:
            with SOURCE_INPUT.open("r", encoding="utf-8", errors="replace") as handle:
                offset = int(state.get("source_offset", 0))
                if offset > SOURCE_INPUT.stat().st_size:
                    offset = SOURCE_INPUT.stat().st_size
                handle.seek(offset)
                while True:
                    pos = handle.tell()
                    line = handle.readline()
                    if not line:
                        handle.seek(pos)
                        break
                    if not line.endswith("\n"):
                        handle.seek(pos)
                        break
                    state["source_offset"] = handle.tell()
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    mint = row.get("candidate", {}).get("mint") or row.get("raw_record", {}).get("mint")
                    if mint and capsule_ok(row):
                        existing = capsule_cache.get(mint)
                        if existing is None or capsule_rank(row) > capsule_rank({
                            "candidate": {
                                "token_program": existing.get("candidate", {}).get("tokenProgram"),
                                "source_ix_data_base64": existing.get("candidate", {}).get("sourceIxDataBase64"),
                            },
                            "canonical_state": {"observed_at_ms": existing.get("canonical_state", {}).get("observedAtMs")},
                            "extra_decision_fields": {"sourceIxAccounts": existing.get("extra_decision_fields", {}).get("sourceIxAccounts")},
                        }):
                            capsule_cache[mint] = minimal_capsule(row)

            signals = list(read_gzip_jsonl(OUT / "signals.jsonl.gz"))
            for sig in signals:
                mint = sig.get("mint")
                if not mint or mint in processed:
                    continue
                capsule = capsule_cache.get(mint)
                if not capsule:
                    append_jsonl(out / "translation_events.jsonl", {"kind": "waiting_for_execution_capsule", "mint": mint, "atUtc": utc_now(), "submissionCount": 0})
                    continue
                adapter_ns = now_ns()
                result = builder.build(minimal_signal(sig, rebase=False), capsule, adapter_ns)
                append_jsonl(out / "prospective_build_results.jsonl", result)
                processed.add(mint)
                state["processed_mints"] = sorted(processed)
                state["submissionCount"] = 0
                write_json(state_path, state)
                write_json(out / "prospective_summary.json", summarize_results(out))
            write_json(state_path, state)
            write_json(out / "service_health.json", {"updatedAtUtc": utc_now(), "processed": len(processed), "cachedCapsules": len(capsule_cache), "submissionCount": 0})
            time.sleep(args.poll_sec)
    finally:
        builder.close()


def summarize_results(out: Path) -> dict[str, Any]:
    path = out / "prospective_build_results.jsonl"
    rows = []
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
    ok = [r for r in rows if r.get("ok")]
    first_send = [int(r["shadowDecisionToFirstSendBoundaryUs"]) for r in ok if int(r.get("shadowDecisionToFirstSendBoundaryUs", -1)) >= 0]
    return {
        "updatedAtUtc": utc_now(),
        "results": len(rows),
        "ok": len(ok),
        "errors": len(rows) - len(ok),
        "decisionToTxReady": dist_us([int(r["shadowDecisionToTxReadyUs"]) for r in ok if int(r.get("shadowDecisionToTxReadyUs", -1)) >= 0]),
        "decisionToFirstSendBoundary": dist_us(first_send),
        "submissionCount": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["snapshot", "service"])
    parser.add_argument("--builder", default="/root/bot/EdgeSniffer/rust-sidecar/target/release/ranker_v2_no_submit_builder")
    parser.add_argument("--builder-source", default="/root/bot/EdgeSniffer/rust-sidecar/src/bin/ranker_v2_no_submit_builder.rs")
    parser.add_argument("--output", default=str(TRANSLATION_OUT))
    parser.add_argument("--max-builds", type=int, default=120)
    parser.add_argument("--sample-scope", choices=["deterministic", "all"], default="deterministic")
    parser.add_argument("--poll-sec", type=float, default=2.0)
    args = parser.parse_args()
    if args.mode == "snapshot":
        print(json.dumps(compact(run_snapshot(args)), indent=2, sort_keys=True))
        return 0
    return run_service(args)


if __name__ == "__main__":
    raise SystemExit(main())
