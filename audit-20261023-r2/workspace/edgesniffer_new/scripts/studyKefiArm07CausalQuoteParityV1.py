#!/usr/bin/env python3
"""Offline causal quote/live-parity study for the frozen ARM07 cohorts.

This is a separate research consumer.  It does not modify the frozen ARM07
policy or either cohort.  It starts from the sealed target timestamp, waits
for the first usable observed PumpSwap state at or after target + 1 second,
quotes the fixed notional against that observed pre-state, and exits only at
observed same-pool states.  Missing source coverage, fee layout, or exit marks
remain explicit failures.

The strict signal-join count requires the target signature to be present in
the parity tape.  The coverage-qualified causal tier permits a timestamp/pool
join when the tape has a usable post-delay state, but records that the target
signature was not joined to the tape.  Neither tier is live execution proof.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kefi_frozen_pumpswap_selection_economics_v1 import (
    quote_buy_exact_quote_in,
    quote_sell_exact_base,
)

WSOL = "So11111111111111111111111111111111111111112"
NOTIONAL = 100_000_000
ENTRY_DELAY_NS = 1_000_000_000
BASE_HORIZON_NS = 30_000_000_000
MAX_HORIZON_NS = 300_000_000_000


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def selected_observations(observations: list[dict[str, Any]], report: dict[str, Any]) -> list[dict[str, Any]]:
    ids = {
        row["observation_id"] for row in report["results"]
        if row.get("result_type") == "SELECTED"
    }
    selected = [row for row in observations if row.get("observation_id") in ids]
    if len(selected) != len(ids):
        raise ValueError("sealed economics report references missing observations")
    return sorted(selected, key=lambda row: int(row["state"]["decoded_event"]["received_at_unix_ns"]))


def source_key(row: dict[str, Any]) -> tuple:
    return (
        int(row["received_at_unix_ns"]),
        str(row["source_instance_id"]),
        int(row["continuity_epoch"]),
        int(row["durable_output_sequence"]),
        int(row["event_index"]),
        str(row["source_frame_sha256"]),
    )


def event_key(row: dict[str, Any]) -> tuple:
    return (
        str(row["source_instance_id"]),
        int(row["continuity_epoch"]),
        int(row["durable_output_sequence"]),
        int(row["event_index"]),
        str(row["source_frame_sha256"]),
    )


def usable_tape_row(row: dict[str, Any]) -> bool:
    required = (
        "source_instance_id", "continuity_epoch", "durable_output_sequence",
        "event_index", "source_frame_sha256", "lp_fee_bps", "protocol_fee_bps",
        "coin_creator_bps", "virtual_quote_reserves", "pool_base_raw",
        "pool_quote_raw", "pool_quote_delta", "base_amount", "received_at_unix_ns",
    )
    return (
        all(field in row for field in required)
        and row.get("exact_transition_validated") is True
        and row.get("quote_mint") == WSOL
        and row.get("mint") != WSOL
        and row.get("side") in {"BUY", "SELL"}
    )


def quote_row(row: dict[str, Any]) -> dict[str, Any]:
    value = dict(row)
    value["virtual_quote_reserves_raw"] = int(row["virtual_quote_reserves"])
    return value


def pre_state(row: dict[str, Any]) -> dict[str, int]:
    base = int(row["pool_base_raw"])
    quote = int(row["pool_quote_raw"])
    amount = int(row["base_amount"])
    delta = int(row["pool_quote_delta"])
    if row["side"] == "BUY":
        return {
            "pool_base_raw": base + amount,
            "pool_quote_raw": quote - delta,
            "virtual_quote_reserves_raw": int(row["virtual_quote_reserves"]),
        }
    return {
        "pool_base_raw": base - amount,
        "pool_quote_raw": quote + delta,
        "virtual_quote_reserves_raw": int(row["virtual_quote_reserves"]),
    }


def observation_event_as_row(observation: dict[str, Any]) -> dict[str, Any]:
    event = dict(observation["state"]["decoded_event"])
    event["virtual_quote_reserves_raw"] = int(event.get("virtual_quote_reserves", 0))
    return event


def observation_pre_state(observation: dict[str, Any]) -> dict[str, int]:
    event = observation_event_as_row(observation)
    return {
        "pool_base_raw": int(event["pool_base_raw"]) + int(event["base_amount"]),
        "pool_quote_raw": int(event["pool_quote_raw"]) - int(event["pool_quote_delta"]),
        "virtual_quote_reserves_raw": int(event.get("virtual_quote_reserves", 0)),
    }


def pnl_for_token(token_out: int, row: dict[str, Any], quote_in: int = NOTIONAL) -> int:
    return int(quote_sell_exact_base(token_out, pre_state(row), quote_row(row))["net_quote_out"]) - quote_in


def stress_token_out(token_out: int, slippage_bps: int) -> int:
    return token_out * (10_000 - slippage_bps) // 10_000


def stress_pnl(token_out: int, row: dict[str, Any], slippage_bps: int) -> int:
    stressed = stress_token_out(token_out, slippage_bps)
    if stressed <= 0:
        return -NOTIONAL
    return pnl_for_token(stressed, row)


def flow_before(rows: list[dict[str, Any]], entry: dict[str, Any], window_ns: int = 30_000_000_000) -> dict[str, Any]:
    start = int(entry["received_at_unix_ns"]) - window_ns
    buys = [row for row in rows if row["pool"] == entry["pool"] and row["mint"] == entry["mint"]
            and start <= int(row["received_at_unix_ns"]) < int(entry["received_at_unix_ns"])]
    buy_quote = sum(int(row["pool_quote_delta"]) for row in buys if row["side"] == "BUY")
    sell_quote = sum(int(row["pool_quote_delta"]) for row in buys if row["side"] == "SELL")
    return {
        "buy_quote_sol": buy_quote / 1_000_000_000,
        "sell_quote_sol": sell_quote / 1_000_000_000,
        "net_quote_sol": (buy_quote - sell_quote) / 1_000_000_000,
        "trade_count": len(buys),
    }


def path_for_entry(rows: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row for row in rows
        if row["source_instance_id"] == entry["source_instance_id"]
        and int(row["continuity_epoch"]) == int(entry["continuity_epoch"])
        and row["pool"] == entry["pool"] and row["mint"] == entry["mint"]
        and int(row["received_at_unix_ns"]) >= int(entry["received_at_unix_ns"])
    ]


def first_at_or_after(path: list[dict[str, Any]], target_ns: int) -> dict[str, Any] | None:
    for row in path:
        if int(row["received_at_unix_ns"]) >= target_ns:
            return row
    return None


def summarize_results(results: list[dict[str, Any]], pnl_key: str = "pnl_lamports") -> dict[str, Any]:
    values = [int(row[pnl_key]) for row in results]
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    return {
        "n": len(values),
        "wins": sum(value > 0 for value in values),
        "win_rate_pct": 100 * sum(value > 0 for value in values) / len(values) if values else None,
        "pnl_sol": sum(values) / 1_000_000_000,
        "profit_factor": gains / losses if losses else None,
        "mean_pnl_sol": statistics.fmean(values) / 1_000_000_000 if values else None,
        "mean_fee_sol": statistics.fmean(int(row["entry_fee_lamports"]) + int(row["exit_fee_lamports"]) for row in results) / 1_000_000_000 if results else None,
        "mean_entry_delay_seconds": statistics.fmean(float(row["entry_delay_seconds"]) for row in results if row.get("entry_delay_seconds") is not None) if any(row.get("entry_delay_seconds") is not None for row in results) else None,
        "mean_exit_delay_seconds": statistics.fmean(float(row["exit_delay_seconds"]) for row in results if row.get("exit_delay_seconds") is not None) if any(row.get("exit_delay_seconds") is not None for row in results) else None,
    }


def causal_trade(
    observation: dict[str, Any],
    rows: list[dict[str, Any]],
    signature_map: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    event = observation["state"]["decoded_event"]
    signal_time = int(event["received_at_unix_ns"])
    signal_signature = str(observation["state"]["raw_evidence"].get("signature", ""))
    exact_signal = signature_map.get(signal_signature)
    candidates = [
        row for row in rows
        if row["pool"] == event["pool"] and row["mint"] == event["mint"]
        and int(row["received_at_unix_ns"]) >= signal_time + ENTRY_DELAY_NS
    ]
    if not candidates:
        return None, "NO_USABLE_POST_DELAY_ENTRY_STATE"
    entry = candidates[0]
    path = path_for_entry(rows, entry)
    exit_row = first_at_or_after(path, int(entry["received_at_unix_ns"]) + BASE_HORIZON_NS)
    if exit_row is None:
        return None, "NO_USABLE_30S_EXIT_MARK"
    try:
        buy = quote_buy_exact_quote_in(NOTIONAL, pre_state(entry), quote_row(entry))
        baseline_sell = quote_sell_exact_base(buy["token_out"], pre_state(exit_row), quote_row(exit_row))
        theoretical = quote_buy_exact_quote_in(NOTIONAL, observation_pre_state(observation), observation_event_as_row(observation))
    except (KeyError, TypeError, ValueError) as error:
        return None, f"QUOTE_FAILED:{type(error).__name__}:{error}"
    flow = flow_before(rows, entry)
    return {
        "observation_id": observation["observation_id"],
        "mint": entry["mint"],
        "pool": entry["pool"],
        "signal_received_at_unix_ns": signal_time,
        "entry_received_at_unix_ns": int(entry["received_at_unix_ns"]),
        "exit_received_at_unix_ns": int(exit_row["received_at_unix_ns"]),
        "entry_delay_seconds": (int(entry["received_at_unix_ns"]) - signal_time) / 1_000_000_000,
        "exit_delay_seconds": (int(exit_row["received_at_unix_ns"]) - int(entry["received_at_unix_ns"])) / 1_000_000_000,
        "signal_tape_join": "EXACT_SIGNATURE" if exact_signal is not None else "TIMESTAMP_POOL_ONLY",
        "entry_event_key": list(event_key(entry)),
        "exit_event_key": list(event_key(exit_row)),
        "entry_pre_effective_quote_sol": (int(pre_state(entry)["pool_quote_raw"]) + int(entry["virtual_quote_reserves"])) / 1_000_000_000,
        "entry_pre_pool_base_raw": int(pre_state(entry)["pool_base_raw"]),
        "entry_token_out": int(buy["token_out"]),
        "theoretical_token_out": int(theoretical["token_out"]),
        "entry_token_slippage_bps": (int(buy["token_out"]) / int(theoretical["token_out"]) - 1) * 10_000 if theoretical["token_out"] else None,
        "entry_fee_lamports": int(buy["fee_total"]),
        "exit_fee_lamports": int(baseline_sell["fee_total"]),
        "entry_fee_bps": int(buy["fee_total"]) * 10_000 / NOTIONAL,
        "exit_fee_bps": int(baseline_sell["fee_total"]) * 10_000 / max(int(baseline_sell["gross_quote_out"]), 1),
        "baseline_exit": baseline_sell,
        "pnl_lamports": int(baseline_sell["net_quote_out"]) - NOTIONAL,
        "flow_30s": flow,
        "path": path,
        "entry_row": entry,
        "exit_row": exit_row,
        "buy": buy,
    }, ""


def policy_exit(trade: dict[str, Any], policy: str, slippage_bps: int = 0) -> dict[str, Any] | None:
    entry = trade["entry_row"]
    path = trade["path"]
    buy = trade["buy"]
    entry_time = int(entry["received_at_unix_ns"])
    dynamic_path = [
        row for row in path
        if int(row["received_at_unix_ns"]) <= entry_time + MAX_HORIZON_NS
    ]
    fixed_exit_time = int(trade["exit_row"]["received_at_unix_ns"])
    partial_path = [
        row for row in path
        if int(row["received_at_unix_ns"]) <= fixed_exit_time
    ]
    def quote_at(row: dict[str, Any], token_out: int) -> tuple[int, int]:
        stressed = stress_token_out(token_out, slippage_bps)
        if stressed <= 0:
            return -NOTIONAL, 0
        sell = quote_sell_exact_base(stressed, pre_state(row), quote_row(row))
        return int(sell["net_quote_out"]) - NOTIONAL, int(sell["fee_total"])
    if policy == "fixed30":
        row = trade["exit_row"]
        pnl, fee = quote_at(row, int(buy["token_out"]))
        return {"row": row, "pnl_lamports": pnl, "exit_fee_lamports": fee, "partial": False}
    if policy.startswith("tp_"):
        tp_bps = int(policy.split("_")[1])
        for row in dynamic_path:
            if int(row["received_at_unix_ns"]) < entry_time:
                continue
            pnl, fee = quote_at(row, int(buy["token_out"]))
            if pnl * 10_000 / NOTIONAL >= tp_bps:
                return {"row": row, "pnl_lamports": pnl, "exit_fee_lamports": fee, "partial": False}
        row = first_at_or_after(dynamic_path, entry_time + MAX_HORIZON_NS)
        if not row:
            return None
        pnl, fee = quote_at(row, int(buy["token_out"]))
        return {"row": row, "pnl_lamports": pnl, "exit_fee_lamports": fee, "partial": False}
    if policy.startswith("protect_"):
        _, arm_bps, floor_bps = policy.split("_")
        armed = False
        for row in dynamic_path:
            pnl, _ = quote_at(row, int(buy["token_out"]))
            roi_bps = pnl * 10_000 / NOTIONAL
            if roi_bps >= int(arm_bps):
                armed = True
            if armed and roi_bps <= int(floor_bps):
                _, fee = quote_at(row, int(buy["token_out"]))
                return {"row": row, "pnl_lamports": pnl, "exit_fee_lamports": fee, "partial": False}
        row = first_at_or_after(dynamic_path, entry_time + MAX_HORIZON_NS)
        if not row:
            return None
        pnl, fee = quote_at(row, int(buy["token_out"]))
        return {"row": row, "pnl_lamports": pnl, "exit_fee_lamports": fee, "partial": False}
    if policy.startswith("partial_"):
        _, fraction_text, tp_text = policy.split("_")
        fraction_bps = int(fraction_text)
        tp_bps = int(tp_text)
        trigger = None
        for row in partial_path:
            pnl, _ = quote_at(row, int(buy["token_out"]))
            if pnl * 10_000 / NOTIONAL >= tp_bps:
                trigger = row
                break
        exit_row = trade["exit_row"]
        if trigger is None:
            pnl, fee = quote_at(exit_row, int(buy["token_out"]))
            return {"row": exit_row, "pnl_lamports": pnl, "exit_fee_lamports": fee, "partial": False}
        partial_tokens = int(buy["token_out"]) * fraction_bps // 10_000
        residual_tokens = int(buy["token_out"]) - partial_tokens
        first, first_fee = quote_at(trigger, partial_tokens)
        second, second_fee = quote_at(exit_row, residual_tokens)
        return {"row": exit_row, "pnl_lamports": first + second + NOTIONAL, "exit_fee_lamports": first_fee + second_fee, "partial": True, "trigger_row": trigger}
    raise ValueError(f"unknown policy {policy}")


def policy_summary(trades: list[dict[str, Any]], policy: str, slippage_bps: int) -> dict[str, Any]:
    results = []
    failures = 0
    for trade in trades:
        result = policy_exit(trade, policy, slippage_bps)
        if result is None or result.get("pnl_lamports") is None:
            failures += 1
            continue
        results.append({
            "pnl_lamports": int(result["pnl_lamports"]),
            "entry_fee_lamports": trade["entry_fee_lamports"],
            "exit_fee_lamports": int(result.get("exit_fee_lamports", trade["exit_fee_lamports"])),
        })
    return {"policy": policy, "slippage_bps": slippage_bps, "failed_or_unavailable": failures, **summarize_results(results)}


def filter_summary(trades: list[dict[str, Any]], label: str, predicate) -> dict[str, Any]:
    selected = [trade for trade in trades if predicate(trade)]
    base = []
    for trade in selected:
        base.append({
            "pnl_lamports": trade["pnl_lamports"],
            "entry_fee_lamports": trade["entry_fee_lamports"],
            "exit_fee_lamports": trade["exit_fee_lamports"],
        })
    return {"filter": label, "selected_n": len(selected), **summarize_results(base)}


def analyze_cohort(manifest_path: Path, report_path: Path, observations_path: Path, tape_path: Path) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    report = load_json(report_path)
    observations = selected_observations(load_jsonl(observations_path), report)
    all_tape = load_jsonl(tape_path)
    usable = sorted([row for row in all_tape if usable_tape_row(row)], key=source_key)
    signature_map = {str(row.get("candidate_signature")): row for row in usable if row.get("candidate_signature")}
    trades: list[dict[str, Any]] = []
    failures: Counter[str] = Counter()
    strict_join_n = 0
    timestamp_join_n = 0
    for observation in observations:
        event = observation["state"]["decoded_event"]
        if str(observation["state"]["raw_evidence"].get("signature", "")) in signature_map:
            strict_join_n += 1
        trade, reason = causal_trade(observation, usable, signature_map)
        if trade is None:
            failures[reason] += 1
            continue
        if trade["signal_tape_join"] == "EXACT_SIGNATURE":
            strict_join_n = max(strict_join_n, 0)
        else:
            timestamp_join_n += 1
        trades.append(trade)
    source_metadata_failures = len(all_tape) - len(usable)
    policies = [
        "fixed30", "tp_100", "tp_200", "protect_100_0",
        "partial_500_100", "partial_500_200",
    ]
    slippage = {str(bps): [policy_summary(trades, policy, bps) for policy in policies] for bps in (0, 25, 50)}
    filters = [
        filter_summary(trades, "liquidity_ge_50_sol", lambda t: t["entry_pre_effective_quote_sol"] >= 50),
        filter_summary(trades, "prior_30s_net_flow_ge_0", lambda t: t["flow_30s"]["net_quote_sol"] >= 0),
        filter_summary(trades, "entry_fee_le_120bps", lambda t: t["entry_fee_bps"] <= 120),
        filter_summary(trades, "liquidity_ge_50_and_net_flow_ge_0", lambda t: t["entry_pre_effective_quote_sol"] >= 50 and t["flow_30s"]["net_quote_sol"] >= 0),
    ]
    strict_trades = [trade for trade in trades if trade["signal_tape_join"] == "EXACT_SIGNATURE"]
    def baseline_rows(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "pnl_lamports": value["pnl_lamports"],
                "entry_fee_lamports": value["entry_fee_lamports"],
                "exit_fee_lamports": value["exit_fee_lamports"],
                "entry_delay_seconds": value["entry_delay_seconds"],
                "exit_delay_seconds": value["exit_delay_seconds"],
            }
            for value in values
        ]
    return {
        "cohort_id": manifest["cohort_id"],
        "cohort_started_at_utc": manifest["cohort_started_at_utc"],
        "registry_sha256": manifest["registry_sha256"],
        "policies_sha256": manifest["policies_sha256"],
        "manifest_sha256": sha256_file(manifest_path),
        "report_sha256": sha256_file(report_path),
        "observations_sha256": sha256_file(observations_path),
        "tape_sha256": sha256_file(tape_path),
        "sealed_theoretical_metrics": report["metrics"],
        "input_counts": {
            "sealed_selected_observations": len(observations),
            "tape_rows_total": len(all_tape),
            "tape_rows_usable_causal": len(usable),
            "tape_rows_rejected_metadata_or_layout": source_metadata_failures,
            "strict_signal_signature_joins": strict_join_n,
            "timestamp_pool_causal_trades": len(trades),
        },
        "failure_reasons": dict(failures),
        "causal_baseline": summarize_results([
            {"pnl_lamports": trade["pnl_lamports"], "entry_fee_lamports": trade["entry_fee_lamports"], "exit_fee_lamports": trade["exit_fee_lamports"],
             "entry_delay_seconds": trade["entry_delay_seconds"], "exit_delay_seconds": trade["exit_delay_seconds"]}
            for trade in trades
        ]),
        "strict_signal_join_baseline": summarize_results(baseline_rows(strict_trades)),
        "slippage_policy_surface": slippage,
        "pre_entry_filter_surface": filters,
        "trade_rows": [
            {key: value for key, value in trade.items() if key not in {"path", "entry_row", "exit_row", "buy"}}
            for trade in trades
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    for prefix in ("old", "new"):
        parser.add_argument(f"--{prefix}-manifest", type=Path, required=True)
        parser.add_argument(f"--{prefix}-report", type=Path, required=True)
        parser.add_argument(f"--{prefix}-observations", type=Path, required=True)
        parser.add_argument(f"--{prefix}-tape", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    old = analyze_cohort(args.old_manifest, args.old_report, args.old_observations, args.old_tape)
    new = analyze_cohort(args.new_manifest, args.new_report, args.new_observations, args.new_tape)
    output = {
        "schema": "KEFI_ARM07_CAUSAL_QUOTE_LIVE_PARITY_STUDY_V1",
        "read_only": True,
        "notional_lamports": NOTIONAL,
        "entry_delay_seconds": 1,
        "base_exit_horizon_seconds": 30,
        "maximum_dynamic_horizon_seconds": 300,
        "implementation_hashes": {
            relative: sha256_file(Path(__file__).resolve().parents[1] / relative)
            for relative in (
                "scripts/studyKefiArm07CausalQuoteParityV1.py",
                "kefi_frozen_pumpswap_selection_economics_v1.py",
                "scripts/pumpSwapExactEconomicsV1.py",
                "scripts/pumpSwapInitDecoderV1.py",
                "scripts/evaluateKefi13Arm07FreshEconomicsV1.py",
            )
        },
        "execution_model": {
            "entry": "first observed usable exact PumpSwap state at or after signal plus one second",
            "exit": "first observed usable same-source-instance/epoch/pool state at or after policy horizon",
            "liquidity": "observed pre-transition effective quote reserve",
            "fees": "exact LP, protocol, and creator fees from the observed transition",
            "price_impact": "integer constant-product quote for the fixed notional",
            "unobserved_slippage": "stress-only 0/25/50 bps token-output haircut; not claimed as measured",
            "network_and_priority_fees": "not present in tape; excluded and must be added before any execution claim",
            "missed_or_failed": "missing exact tape metadata, no post-delay state, quote failure, or no observed exit mark",
        },
        "strictness": {
            "strict_signal_join": "target signature must exist in the parity tape",
            "reported_causal_tier": "timestamp+pool fill is coverage-qualified and is not a live-fill proof when the signal signature is absent",
        },
        "hypotheses_only": {
            "dynamic_selling": "take-profit threshold with observed-state fallback at 300 seconds",
            "profit_protection": "arm at +100 bps and exit at 0 bps or observed 300-second fallback",
            "partial_take_profit": "50% at +100 or +200 bps, residual at the frozen 30-second exit",
            "pre_entry_filters": "liquidity, prior 30-second net flow, entry fee burden, and their conjunction",
            "promotion_rule": "no policy is selected from these same-cohort surfaces; any candidate requires a new frozen prospective policy and fresh data",
        },
        "cohorts": {"old": old, "new": new},
    }
    args.out.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "CAUSAL_STUDY_COMPLETE_READ_ONLY",
        "old_causal_n": old["input_counts"]["timestamp_pool_causal_trades"],
        "new_causal_n": new["input_counts"]["timestamp_pool_causal_trades"],
        "old_pnl_sol": old["causal_baseline"]["pnl_sol"],
        "new_pnl_sol": new["causal_baseline"]["pnl_sol"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
