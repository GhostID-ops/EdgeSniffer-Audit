#!/usr/bin/env python3
"""Discovery-only, market-wide causal strategy lab.

This lab is deliberately separate from the 2CQ teacher-conditioned labs.  Its
input is a set of market-wide, venue-admitted windows.  A window is admitted
only when the signal, delayed entry state, exact venue marks, fee fields and
lineage are present.  Strict fixed exits require a mark at or after the exit
horizon.  A separate carry-forward hypothesis may use the last verified native
state when the market-wide tape has reached the deadline but no newer trade
occurred; it is never mixed into the strict fixed-exit results.

The program ranks hypotheses only.  It cannot freeze, promote, submit, or
enable a trading service.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import exactCurveEconomicsV1 as curve

LAMPORTS_PER_SOL = 1_000_000_000
NETWORK_FEE_SOL = 0.00001
ENTRY_DELAY_MS = 1_000
NOTIONALS_SOL = (0.1, 0.5, 1.0)
STRESS_BPS = (0, 25, 50)
HORIZONS_SECONDS = (5, 10, 15, 30, 60, 120, 300)

FILTERS: tuple[dict[str, Any], ...] = (
    {"id": "ALL_EXACT_ROUTE", "min_net_flow_sol": None, "min_price_change_bps": None},
    {"id": "LOW_MC_LT_10K", "max_market_cap_usd": 10_000},
    {"id": "POSITIVE_FLOW", "min_net_flow_sol": 0.0},
    {"id": "FLOW_AND_PRICE_CONTINUATION", "min_net_flow_sol": 0.0, "min_price_change_bps": 0.0},
    {"id": "FLOW_005_AND_PRICE_10BPS", "min_net_flow_sol": 0.05, "min_price_change_bps": 10.0},
    {"id": "RESERVE_GE_2_SOL", "min_effective_quote_reserve_sol": 2.0},
    {"id": "IMPACT_LE_100_BPS", "max_entry_impact_bps": 100.0},
)

EXIT_RULES: tuple[dict[str, Any], ...] = tuple(
    [{"id": f"FIXED_{h}S", "kind": "FIXED", "horizon_seconds": h} for h in HORIZONS_SECONDS]
    + [{"id": f"CARRY_{h}S", "kind": "FIXED_CARRY", "horizon_seconds": h} for h in HORIZONS_SECONDS]
    + [{"id": f"TP_SL_5_5_{h}S", "kind": "TP_SL", "horizon_seconds": h,
        "take_profit_pct": 0.05, "stop_loss_pct": 0.05} for h in (15, 30, 60, 120)]
    + [{"id": f"PROTECT_5_0_{h}S", "kind": "PROTECT", "horizon_seconds": h,
        "activation_pct": 0.05, "protected_pct": 0.0} for h in (30, 60, 120)]
    + [{"id": f"TRAIL_5_3_{h}S", "kind": "TRAILING", "horizon_seconds": h,
        "activation_pct": 0.05, "trail_pct": 0.03} for h in (30, 60, 120)]
    + [{"id": f"PARTIAL_5_50_{h}S", "kind": "PARTIAL_TP", "horizon_seconds": h,
        "take_profit_pct": 0.05, "partial_fraction": 0.50} for h in (30, 60, 120)]
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    opener = path.open
    if path.suffix == ".gz":
        import gzip
        opener = lambda *args, **kwargs: gzip.open(path, "rt", encoding="utf-8")  # type: ignore[assignment]
    with opener(encoding="utf-8") as handle:  # type: ignore[operator]
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def state(value: Any) -> curve.CurveState | None:
    if not isinstance(value, dict):
        return None
    aliases = {
        "virtual_sol": ("virtual_sol", "virtualSolReserves", "virtualSol"),
        "virtual_token": ("virtual_token", "virtualTokenReserves", "virtualToken"),
        "real_sol": ("real_sol", "realSolReserves", "realSol"),
        "real_token": ("real_token", "realTokenReserves", "realToken"),
    }
    values: dict[str, int] = {}
    for name, keys in aliases.items():
        raw = next((value.get(key) for key in keys if value.get(key) is not None), None)
        if raw is None:
            return None
        try:
            values[name] = int(raw)
        except (TypeError, ValueError):
            return None
    result = curve.CurveState(**values)
    try:
        result.validate()
    except Exception:
        return None
    return result


def nested(row: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        value: Any = row
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if value is not None:
            return value
    return None


def timestamp_ms(row: dict[str, Any]) -> int | None:
    raw = nested(row, ("receive_ts_ms",), ("local_receive_ts_ms",), ("checkpoint_receive_ts_ms",), ("signal_ts_ms",), ("signal_receive_ts_ms",),
                 ("ts_ms",), ("timestamp_ms",), ("raw_record", "local_receive_ts_ms"))
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def mint_of(row: dict[str, Any]) -> str | None:
    value = nested(row, ("mint",), ("raw_record", "mint"), ("candidate", "mint"))
    return str(value) if value else None


def route_of(row: dict[str, Any]) -> str | None:
    value = nested(row, ("route",), ("venue_route",), ("canonical_state", "route"),
                   ("raw_record", "route"), ("raw_record", "venue"))
    if value:
        return str(value)
    # The native flow adapter does not duplicate the route label, but it does
    # preserve the Pump bonding-curve account and the Pump program in the
    # source account list.  This is route identification only; exact reserve
    # state is still required before economics admission.
    raw = row.get("raw_record") if isinstance(row.get("raw_record"), dict) else {}
    accounts = (raw.get("sourceIxAccounts") or row.get("source_ix_accounts") or
                nested(row, ("extra", "sourceIxAccounts")) or [])
    if raw.get("bondingCurve") and (
        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P" in accounts
    ):
        return "PUMP_CURVE"
    return None


def state_evidence_rejection(row: dict[str, Any]) -> str | None:
    """Negative-evidence guard, not certification of otherwise untagged data.

    Reject legacy replay too: its labels predate execution/fee verification.
    A successful transaction cannot certify its approximated predecessor state.
    """
    canonical = row.get("canonical_state")
    for evidence in (row, canonical if isinstance(canonical, dict) else {}):
        if evidence.get("native_economics_state_complete") is False:
            return "explicit_incomplete_state"
        if evidence.get("state_transition_authority") == "CONDITIONAL_INTENT_REPLAY":
            return "conditional_replay"
        if evidence.get("state_source") == "NATIVE_SHRED_REPLAY" or evidence.get("schema") == "PUMP_CURVE_NATIVE_REPLAY_STATE_V1":
            return "unverified_replay_model"
        if evidence.get("execution_status") in {"FAILED", "UNKNOWN"}:
            return "unverified_execution"
    return None


def event_state(row: dict[str, Any]) -> curve.CurveState | None:
    if state_evidence_rejection(row):
        return None
    for value in (row.get("post_state"), row.get("state"), row.get("canonical_state"),
                  nested(row, ("canonical_state", "post_state")),
                  nested(row, ("raw_record", "post_state"), ("raw_record", "state"))):
        parsed = state(value)
        if parsed:
            return parsed
    return None


def fee_event(row: dict[str, Any]) -> dict[str, Any]:
    event = row.get("event") if isinstance(row.get("event"), dict) else row
    return event if isinstance(event, dict) else {}


def _fee_int(event: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = event.get(key)
        if value is None:
            continue
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return None


def quote_price(value: curve.CurveState) -> float:
    return value.virtual_sol / value.virtual_token


def fee_for(row: dict[str, Any], stress_bps: int) -> curve.Fees:
    event = fee_event(row)
    total = _fee_int(event, "fee_basis_points", "total_fee_basis_points")
    protocol = _fee_int(event, "protocol_fee_basis_points", "protocol_fee_bps")
    creator = _fee_int(event, "creator_fee_basis_points", "creator_fee_bps")
    creator = creator or 0
    # Some native rows expose only the aggregate fee.  If creator fee is also
    # present, the protocol component is the remainder; do not double-count
    # the creator component as protocol + creator.
    if protocol is None:
        protocol = max(0, (total - creator) if total is not None else 0)
    return curve.Fees(protocol_bps=protocol, creator_bps=creator, lp_bps=stress_bps)


def quote_buy(entry: dict[str, Any], notional_sol: float, stress_bps: int) -> curve.BuyQuote | None:
    if state_evidence_rejection(entry):
        return None
    entry_state = state(entry.get("entry_state"))
    if entry_state is None:
        return None
    try:
        return curve.quote_exact_quote_in_buy(entry_state, int(round(notional_sol * LAMPORTS_PER_SOL)),
                                              fee_for(entry, stress_bps))
    except Exception:
        return None


def quote_sell(mark: dict[str, Any], tokens: int, entry_notional: float, stress_bps: int) -> float | None:
    mark_state = event_state(mark)
    if mark_state is None:
        return None
    try:
        result = curve.quote_exact_token_sell(mark_state, tokens, 0, fee_for(mark, stress_bps))
        return result.immediate_quote_out / LAMPORTS_PER_SOL - entry_notional - NETWORK_FEE_SOL
    except Exception:
        return None


def has_native_receipt_boundary(row: dict[str, Any]) -> bool:
    """Require an explicit native receive clock and causal entry-state lineage."""
    lineage = row.get("lineage") if isinstance(row.get("lineage"), dict) else {}
    signal_time = timestamp_ms(row)
    entry_state = row.get("entry_state")
    return (
        state_evidence_rejection(row) is None
        and lineage.get("source_receive_clock") is True
        and bool(lineage.get("entry_state_source"))
        and signal_time is not None
        and isinstance(entry_state, dict)
    )


def pct(value: float, notional: float) -> float:
    return value / notional if notional else -1.0


def eligible(spec: dict[str, Any], opportunity: dict[str, Any], buy: curve.BuyQuote | None,
             notional: float) -> bool:
    if spec.get("max_market_cap_usd") is not None:
        mc = opportunity.get("market_cap_usd")
        if mc is None or float(mc) >= float(spec["max_market_cap_usd"]):
            return False
    flow = opportunity.get("net_flow_sol")
    price_change = opportunity.get("price_change_bps")
    if spec.get("min_net_flow_sol") is not None and (flow is None or float(flow) < float(spec["min_net_flow_sol"])):
        return False
    if spec.get("min_price_change_bps") is not None and (price_change is None or float(price_change) < float(spec["min_price_change_bps"])):
        return False
    reserve = opportunity.get("effective_quote_reserve_sol")
    if spec.get("min_effective_quote_reserve_sol") is not None and (reserve is None or float(reserve) < float(spec["min_effective_quote_reserve_sol"])):
        return False
    if spec.get("max_entry_impact_bps") is not None:
        impact = opportunity.get("entry_impact_bps")
        if impact is None:
            return False
        if float(impact) > float(spec["max_entry_impact_bps"]):
            return False
    return buy is not None


def mark_rows(opportunity: dict[str, Any], mark_field: str = "marks") -> list[dict[str, Any]]:
    value = opportunity.get(mark_field)
    return value if isinstance(value, list) else []


def marks_until(opportunity: dict[str, Any], entry_time: int, horizon: int,
                mark_field: str = "marks") -> list[dict[str, Any]]:
    result = []
    deadline = entry_time + horizon * 1000
    for mark in mark_rows(opportunity, mark_field):
        mark_time = timestamp_ms(mark)
        if mark_time is not None and entry_time <= mark_time <= deadline and event_state(mark) is not None:
            result.append(mark)
    return result


def first_at_or_after(opportunity: dict[str, Any], deadline: int,
                      mark_field: str = "marks") -> dict[str, Any] | None:
    choices = [m for m in mark_rows(opportunity, mark_field)
               if (timestamp_ms(m) or -1) >= deadline and event_state(m) is not None]
    return min(choices, key=lambda m: timestamp_ms(m) or 2**63) if choices else None


def last_at_or_before(opportunity: dict[str, Any], deadline: int, entry_time: int,
                      mark_field: str = "marks") -> dict[str, Any] | None:
    choices = [m for m in mark_rows(opportunity, mark_field)
               if entry_time <= (timestamp_ms(m) or -1) <= deadline and event_state(m) is not None]
    return max(choices, key=lambda m: timestamp_ms(m) or -1) if choices else None


def horizon_matured(opportunity: dict[str, Any], deadline: int) -> bool:
    """Return whether the source tape has reached the requested exit horizon.

    Older window artifacts do not have an observation cutoff.  They retain the
    historical behavior for compatibility; newly materialized native windows
    always carry this field.
    """
    as_of = opportunity.get("observation_as_of_ts_ms")
    if as_of is None:
        return True
    try:
        return int(as_of) >= deadline
    except (TypeError, ValueError):
        return False


def immature_exit(mfe: float | None, mae: float | None) -> dict[str, Any]:
    return {"status": "IMMATURE_WINDOW", "pnl_sol": None, "exit_reason": "HORIZON_NOT_REACHED",
            "mfe_sol": mfe, "mae_sol": mae}


def exit_result(opportunity: dict[str, Any], buy: curve.BuyQuote, notional: float,
                rule: dict[str, Any], stress_bps: int, entry_time: int,
                mark_field: str = "marks") -> dict[str, Any]:
    horizon = int(rule["horizon_seconds"])
    path = marks_until(opportunity, entry_time, horizon, mark_field)
    values = [(mark, quote_sell(mark, buy.token_out, notional, stress_bps)) for mark in path]
    values = [(mark, value) for mark, value in values if value is not None]
    mfe = max((value for _, value in values), default=None)
    mae = min((value for _, value in values), default=None)
    selected: dict[str, Any] | None = None
    reason = ""
    kind = rule["kind"]
    if kind == "FIXED":
        if not horizon_matured(opportunity, entry_time + horizon * 1000):
            return immature_exit(mfe, mae)
        selected = first_at_or_after(opportunity, entry_time + horizon * 1000, mark_field)
        reason = "FIXED_HORIZON"
    elif kind == "FIXED_CARRY":
        deadline = entry_time + horizon * 1000
        if not horizon_matured(opportunity, deadline):
            return immature_exit(mfe, mae)
        selected = first_at_or_after(opportunity, deadline, mark_field)
        reason = "FIXED_HORIZON"
        if selected is None:
            selected = last_at_or_before(opportunity, deadline, entry_time, mark_field)
            reason = "CARRY_FORWARD_LAST_NATIVE_STATE"
    elif kind in {"TP_SL", "PROTECT", "TRAILING"}:
        high_pct: float | None = None
        armed = False
        for mark, value in values:
            current = pct(value, notional)
            high_pct = current if high_pct is None else max(high_pct, current)
            if kind == "TP_SL" and current >= float(rule["take_profit_pct"]):
                selected, reason = mark, "TAKE_PROFIT"; break
            if kind == "TP_SL" and current <= -float(rule["stop_loss_pct"]):
                selected, reason = mark, "STOP_LOSS"; break
            if kind == "PROTECT" and high_pct >= float(rule["activation_pct"]):
                armed = True
            if kind == "PROTECT" and armed and current <= float(rule["protected_pct"]):
                selected, reason = mark, "PROFIT_PROTECTION"; break
            if kind == "TRAILING" and high_pct >= float(rule["activation_pct"]) and current <= high_pct - float(rule["trail_pct"]):
                selected, reason = mark, "TRAILING_STOP"; break
        if selected is None:
            if not horizon_matured(opportunity, entry_time + horizon * 1000):
                return immature_exit(mfe, mae)
            selected = first_at_or_after(opportunity, entry_time + horizon * 1000, mark_field)
            reason = "HORIZON_FALLBACK"
    elif kind == "PARTIAL_TP":
        trigger = next(((mark, value) for mark, value in values if pct(value, notional) >= float(rule["take_profit_pct"])), None)
        final = first_at_or_after(opportunity, entry_time + horizon * 1000, mark_field)
        if trigger and final:
            fraction = float(rule["partial_fraction"])
            partial = quote_sell(trigger[0], int(buy.token_out * fraction), notional * fraction, stress_bps)
            remainder = quote_sell(final, buy.token_out - int(buy.token_out * fraction), notional * (1 - fraction), stress_bps)
            if partial is not None and remainder is not None:
                return {"status": "ACTIONABLE", "pnl_sol": partial + remainder, "exit_reason": "PARTIAL_TAKE_PROFIT",
                        "mfe_sol": mfe, "mae_sol": mae}
        if not horizon_matured(opportunity, entry_time + horizon * 1000):
            return immature_exit(mfe, mae)
        selected, reason = final, "HORIZON_FALLBACK"
    if selected is None:
        return {"status": "MISSED_EXIT_MARK", "pnl_sol": None, "exit_reason": reason, "mfe_sol": mfe, "mae_sol": mae}
    value = quote_sell(selected, buy.token_out, notional, stress_bps)
    if value is None:
        return {"status": "FAILED_EXIT_QUOTE", "pnl_sol": None, "exit_reason": reason, "mfe_sol": mfe, "mae_sol": mae}
    return {"status": "ACTIONABLE", "pnl_sol": value, "exit_reason": reason, "mfe_sol": mfe, "mae_sol": mae}


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(r["pnl_sol"]) for r in rows if r.get("status") == "ACTIONABLE" and r.get("pnl_sol") is not None]
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v < 0]
    immature = sum(1 for r in rows if r.get("status") == "IMMATURE_WINDOW")
    mature_rows = [r for r in rows if r.get("status") != "IMMATURE_WINDOW"]
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value; peak = max(peak, equity); drawdown = max(drawdown, peak - equity)
    return {
        "attempted": len(rows), "matured": len(mature_rows), "immature": immature,
        "actionable": len(values),
        "missed_or_failed": sum(1 for r in mature_rows if r.get("status") in {"MISSED_EXIT_MARK", "FAILED_EXIT_QUOTE"}),
        "wins": len(wins), "win_rate": len(wins) / len(values) if values else None,
        "pnl_sol": sum(values), "roi_on_actionable": sum(values) / (float(rows[0]["notional_sol"]) * len(values)) if values else None,
        "profit_factor": sum(wins) / abs(sum(losses)) if losses else None,
        "max_drawdown_sol": drawdown,
        "mean_mfe_sol": sum(float(r["mfe_sol"]) for r in rows if r.get("mfe_sol") is not None) / max(1, sum(r.get("mfe_sol") is not None for r in rows)),
        "mean_mae_sol": sum(float(r["mae_sol"]) for r in rows if r.get("mae_sol") is not None) / max(1, sum(r.get("mae_sol") is not None for r in rows)),
    }


def evaluate_population(rows: list[dict[str, Any]], mark_field: str = "marks") -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    admitted = [row for row in rows if row.get("record_type") in {
        "INDEPENDENT_MARKET_WINDOW", "INDEPENDENT_EVENT_DRIVEN_MARKET_WINDOW",
        "MARKET_WINDOW", "INDEPENDENT_MARKET_CONTROL_WINDOW"
    }]
    outcomes: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rejection_counts: dict[str, int] = defaultdict(int)
    for opportunity in admitted:
        if route_of(opportunity) not in {"PUMP_BONDING_CURVE_TOP_LEVEL", "PUMP_CURVE"}:
            rejection_counts["unsupported_or_unresolved_route"] += 1; continue
        signal = timestamp_ms(opportunity)
        if signal is None:
            rejection_counts["missing_signal_receive_time"] += 1; continue
        entry_time = signal + ENTRY_DELAY_MS
        opportunity = dict(opportunity)
        opportunity["entry_time_ms"] = entry_time
        for filter_spec in FILTERS:
            for notional in NOTIONALS_SOL:
                for stress in STRESS_BPS:
                    buy = quote_buy(opportunity, notional, stress)
                    if not eligible(filter_spec, opportunity, buy, notional):
                        continue
                    for rule in EXIT_RULES:
                        strategy_id = f"{filter_spec['id']}|{rule['id']}|{notional:g}|stress{stress}"
                        base = {"strategy_id": strategy_id, "mint": mint_of(opportunity), "filter": filter_spec["id"],
                                "exit_rule": rule["id"], "notional_sol": notional, "stress_bps": stress,
                                "signal_receive_ts_ms": signal, "entry_ts_ms": entry_time,
                                "control_group": opportunity.get("control_group")}
                        row = base | exit_result(opportunity, buy, notional, rule, stress, entry_time, mark_field)  # type: ignore[arg-type]
                        outcomes.append(row); grouped[strategy_id].append(row)
    surface = [{"strategy_id": key, "metrics": summary(rows)} for key, rows in grouped.items()]
    surface.sort(key=lambda item: (-(item["metrics"].get("pnl_sol") or 0.0), -(item["metrics"].get("profit_factor") or -1.0)))
    return outcomes, surface, dict(rejection_counts)


def evaluate(path: Path, controls_path: Path | None = None,
             require_native_receipt_boundary: bool = False,
             mark_field: str = "marks") -> dict[str, Any]:
    opportunities = load_rows(path)
    admitted = [row for row in opportunities if row.get("record_type") in {
        "INDEPENDENT_MARKET_WINDOW", "INDEPENDENT_EVENT_DRIVEN_MARKET_WINDOW", "MARKET_WINDOW"
    }]
    boundary_rejections = 0
    if require_native_receipt_boundary:
        before = len(admitted)
        admitted = [row for row in admitted if has_native_receipt_boundary(row)]
        boundary_rejections = before - len(admitted)
    outcomes, surface, rejection_counts = evaluate_population(admitted, mark_field)
    control_rows = load_rows(controls_path) if controls_path is not None and controls_path.exists() else []
    control_admitted = [row for row in control_rows if row.get("record_type") == "INDEPENDENT_MARKET_CONTROL_WINDOW"]
    control_boundary_rejections = 0
    if require_native_receipt_boundary:
        before = len(control_admitted)
        control_admitted = [row for row in control_admitted if has_native_receipt_boundary(row)]
        control_boundary_rejections = before - len(control_admitted)
    control_outcomes, control_surface, control_rejection_counts = evaluate_population(control_admitted, mark_field)
    candidate_metrics = {row["strategy_id"]: row["metrics"] for row in surface}
    control_metrics = {row["strategy_id"]: row["metrics"] for row in control_surface}
    comparisons = []
    for strategy_id in sorted(set(candidate_metrics) & set(control_metrics)):
        candidate = candidate_metrics[strategy_id]
        control = control_metrics[strategy_id]
        comparisons.append({
            "strategy_id": strategy_id,
            "candidate": candidate,
            "control": control,
            "pnl_delta_sol": (candidate.get("pnl_sol") or 0.0) - (control.get("pnl_sol") or 0.0),
            "mean_pnl_delta_sol": (
                ((candidate.get("pnl_sol") or 0.0) / max(1, int(candidate.get("actionable") or 0)))
                - ((control.get("pnl_sol") or 0.0) / max(1, int(control.get("actionable") or 0)))
            ),
            "candidate_outperforms_control_by_mean_pnl": (
                ((candidate.get("pnl_sol") or 0.0) / max(1, int(candidate.get("actionable") or 0)))
                > ((control.get("pnl_sol") or 0.0) / max(1, int(control.get("actionable") or 0)))
            ),
            "candidate_outperforms_control_by_profit_factor": (
                (candidate.get("profit_factor") or 0.0) > (control.get("profit_factor") or 0.0)
            ),
        })
    comparisons.sort(key=lambda row: (-row["mean_pnl_delta_sol"], row["strategy_id"]))
    return {
        "schema": "EDGESNIFFER_INDEPENDENT_STRATEGY_LAB_V1",
        "generated_at_ms": int(time.time() * 1000),
        "read_only": True, "trading_enabled": False, "policy_frozen": False,
        "selection_performed": False, "promotion_blocked": True,
        "independence_contract": "no_teacher-wallet trigger, no anchor-conditioned admission, no policy selection from this run",
        "entry_delay_ms": ENTRY_DELAY_MS, "notionals_sol": list(NOTIONALS_SOL), "stress_bps": list(STRESS_BPS),
        "causal_boundary_mode": "NATIVE_RECEIPT_PLUS_1000MS" if require_native_receipt_boundary else "WINDOW_SOURCE_DECLARED",
        "causal_boundary_requirements": {
            "native_receive_clock": require_native_receipt_boundary,
            "entry_state_lineage": require_native_receipt_boundary,
            "entry_delay_ms": ENTRY_DELAY_MS,
        },
        "horizons_seconds": list(HORIZONS_SECONDS), "filters": list(FILTERS), "exit_rules": list(EXIT_RULES),
        "opportunities_loaded": len(opportunities), "admitted_windows": len(admitted),
        "control_windows_loaded": len(control_admitted), "control_source_sha256": sha256_file(controls_path) if controls_path else None,
        "rejection_counts": rejection_counts, "control_rejection_counts": control_rejection_counts,
        "boundary_rejections": boundary_rejections,
        "control_boundary_rejections": control_boundary_rejections,
        "outcomes": outcomes, "surface": surface,
        "control_outcomes": control_outcomes, "control_surface": control_surface,
        "control_comparisons": comparisons, "source_sha256": sha256_file(path),
        "notes": [
            "This is discovery only; no candidate is frozen or promoted.",
            "All rows require exact Pump curve state at the causal delayed entry and exact post-entry state marks.",
            "Teacher-wallet observations may be retained as labels/controls but cannot create an independent signal.",
            "The control tape is a same-period market baseline; it is not a causal treatment control.",
            "A surface result without a fresh prospective cohort and native state parity is not validation.",
        ],
        "mark_field": mark_field,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--controls", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-native-receipt-boundary", action="store_true")
    parser.add_argument("--mark-field", default="marks",
                        help="mark list to evaluate; default is strict transaction-event marks")
    args = parser.parse_args()
    result = evaluate(args.input, args.controls, args.require_native_receipt_boundary, args.mark_field)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"admitted_windows": result["admitted_windows"], "outcome_rows": len(result["outcomes"]),
                      "surface_rows": len(result["surface"]), "policy_frozen": False,
                      "promotion_blocked": True}, sort_keys=True))


if __name__ == "__main__":
    main()
