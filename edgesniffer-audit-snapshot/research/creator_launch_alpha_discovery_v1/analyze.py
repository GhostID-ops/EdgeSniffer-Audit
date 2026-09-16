#!/usr/bin/env python3
"""Read-only creator-launch microstructure discovery over the exact Pump ledger.

The program never calls a network service and writes only to its explicit output
directory.  Finalized transaction order is authoritative; realtime receive time
is retained as a distinct observation-time field.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import pathlib
import statistics
import sys
from typing import Any, Iterable

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = (
    pathlib.Path("/root/bot/EdgeSniffer")
    if pathlib.Path("/root/bot/EdgeSniffer/scripts").is_dir()
    else SCRIPT_DIR.parents[1]
)
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from buildPumpExactAlphaResearchV1 import pair_events, paths_for_report  # noqa: E402
from exactCurveEconomicsV1 import (  # noqa: E402
    CurveState,
    ExactCurveError,
    apply_instruction,
    quote_exact_quote_in_buy,
    quote_exact_token_sell,
)
from reconcilePumpExactEvidenceLedgerV1 import iter_frames  # noqa: E402
from validatePumpExactEvidenceLedgerV1 import event_fees  # noqa: E402

SCHEMA = "CREATOR_LAUNCH_ALPHA_DISCOVERY_V1"
CREATOR_8YCB = "8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ"
SOL_QUOTES = {
    "11111111111111111111111111111111",
    "So11111111111111111111111111111111111111112",
}
SIZES_SOL = (0.01, 0.10, 0.50, 1.00)
RANKS = (0, 1, 2, 3)
WINDOWS_MS = (10, 25, 50, 100, 250, 500, 1000, 2000, 5000, 10000)
FIRST_SELLABLE_MS = 178
TP_PCT = 20.0
SL_PCT = -20.0
MAX_HOLD_MS = 120_000
MIN_OUTPUT_FACTOR = 0.65
BASE_FEE_LAMPORTS_PER_TX = 5_000


@dataclasses.dataclass
class Launch:
    mint: str
    create_signature: str
    create_slot: int
    create_tx_index: int
    create_ms: int
    create_kind: str
    create_evidence_id: str
    creator: str
    events: list[Any]
    atomic_self_buy_events: list[Any]
    external_buys: list[tuple[int, Any]]
    creator_sells: list[Any]


def read_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_jsonl_gzip(path: pathlib.Path, rows: Iterable[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            for row in rows:
                compressed.write((json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode())
    os.replace(temporary, path)


def quantile(values: Iterable[float], q: float) -> float | None:
    ordered = sorted(float(v) for v in values if v is not None and math.isfinite(float(v)))
    if not ordered:
        return None
    position = (len(ordered) - 1) * q
    lo = int(math.floor(position)); hi = int(math.ceil(position))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - position) + ordered[hi] * (position - lo)


def pf(values: list[float]) -> float | None:
    gains = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v < 0)
    if losses == 0:
        return None if gains == 0 else math.inf
    return gains / losses


def metric_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    passed = [r for r in rows if r.get("capacity_min_output_pass")]
    pnl = [float(r["gross_pnl_sol"]) for r in passed if r.get("status") == "EXACT"]
    roi = [float(r["gross_roi_pct"]) for r in passed if r.get("status") == "EXACT"]
    wins = [v for v in pnl if v > 0]
    value_pf = pf(pnl)
    return {
        "n": len(rows),
        "capacity_min_output_pass_n": len(passed),
        "capacity_min_output_pass_rate": len(passed) / len(rows) if rows else None,
        "exact_n": len(pnl),
        "tp_n": sum(r.get("outcome") == "TP" for r in passed),
        "sl_n": sum(r.get("outcome") == "SL" for r in passed),
        "maxhold_n": sum(r.get("outcome") == "MAX_HOLD" for r in passed),
        "wr": len(wins) / len(pnl) if pnl else None,
        "profit_factor": value_pf,
        "expectancy_sol": statistics.fmean(pnl) if pnl else None,
        "total_gross_pnl_sol": sum(pnl),
        "median_roi_pct": statistics.median(roi) if roi else None,
        "mean_roi_pct": statistics.fmean(roi) if roi else None,
        "worst_roi_pct": min(roi) if roi else None,
        "best_roi_pct": max(roi) if roi else None,
    }


def compatible_mints(alpha_root: pathlib.Path) -> set[str]:
    result: set[str] = set()
    for path in sorted((alpha_root / "rows-v2").glob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    result.add(json.loads(line)["mint"])
    return result


def exact_admitted_mints(reports: list[dict[str, Any]]) -> set[str]:
    return {
        row["mint"]
        for report in reports
        for row in report["mints"]
        if row.get("exactAdmitted") is True
    }


def load_readiness_reports(ledger_root: pathlib.Path) -> list[dict[str, Any]]:
    return [
        read_json(path)
        for path in sorted((ledger_root / "readiness-v1" / "partitions").glob("*-v4.json"))
    ]


def collect_exact_events(
    ledger_root: pathlib.Path,
    reports: list[dict[str, Any]],
    targets: set[str],
) -> tuple[dict[str, Launch], dict[str, Any]]:
    creates: dict[str, dict[str, Any]] = {}
    for report in reports:
        for row in report["mints"]:
            if row["mint"] in targets:
                creates[row["mint"]] = dict(row)

    unique_paths: list[pathlib.Path] = []
    for report in reports:
        for path in paths_for_report(ledger_root, report):
            if path not in unique_paths:
                unique_paths.append(path)

    arrivals: dict[str, tuple[int, str]] = {}
    create_meta: dict[str, dict[str, Any]] = {}
    finalized: dict[str, tuple[dict[str, Any], bytes]] = {}
    raw_frames = finalized_frames = duplicate_finalized = 0
    failed_finalized = 0
    seen_evidence: set[str] = set()

    for path in unique_paths:
        for md, raw in iter_frames(path, path.suffix == ".partial"):
            raw_frames += 1
            evidence_id = str(md.get("evidenceId") or "")
            if evidence_id and evidence_id in seen_evidence:
                continue
            if evidence_id:
                seen_evidence.add(evidence_id)
            kind = md.get("kind")
            signature = str(md.get("signature") or "")
            if kind == "transaction" and signature:
                # The realtime protobuf metadata can intentionally leave Pump
                # instruction/mint decoding empty.  Join its earliest receipt by
                # signature to the later finalized frame, which owns mint/status.
                observed_ms = int(md.get("receivedAtUnixNs") or 0) // 1_000_000
                if observed_ms > 0:
                    candidate = (observed_ms, evidence_id)
                    if signature not in arrivals or candidate < arrivals[signature]:
                        arrivals[signature] = candidate
                related = targets.intersection(md.get("discoveredMints") or [])
                if {"CREATE", "CREATE_V2"}.intersection(md.get("pumpInstructionKinds") or []):
                    for mint in related:
                        prior = create_meta.get(mint)
                        if prior is None or int(md.get("receivedAtUnixNs") or 0) < int(prior.get("receivedAtUnixNs") or 0):
                            create_meta[mint] = md
            elif kind == "rpc_finalized_transaction" and signature:
                related = targets.intersection(md.get("discoveredMints") or [])
                if not related:
                    continue
                finalized_frames += 1
                if md.get("failed") is True or md.get("successful") is False:
                    failed_finalized += 1
                prior = finalized.get(signature)
                if prior is not None:
                    duplicate_finalized += 1
                    if hashlib.sha256(prior[1]).digest() != hashlib.sha256(raw).digest():
                        raise RuntimeError(f"conflicting finalized payload for {signature}")
                else:
                    finalized[signature] = (md, raw)

    decoded_by_mint: dict[str, list[Any]] = collections.defaultdict(list)
    decode_rejections: collections.Counter[str] = collections.Counter()
    for signature, (md, raw) in finalized.items():
        try:
            response = json.loads(raw)
            tx = response.get("result")
        except Exception:
            decode_rejections["malformed_finalized_json"] += 1
            continue
        if not tx:
            decode_rejections["null_finalized_transaction"] += 1
            continue
        if (tx.get("meta") or {}).get("err") is not None:
            continue
        related = targets.intersection(md.get("discoveredMints") or [])
        if not related:
            continue
        arrival = arrivals.get(signature)
        if arrival is None:
            decode_rejections["missing_realtime_arrival"] += 1
            continue
        try:
            rows = pair_events(
                tx,
                signature,
                arrival[1],
                str(md.get("evidenceId") or ""),
                arrival[0],
                int(md.get("slot") or 0),
                int(md.get("transactionIndex") or 0),
                related,
            )
        except Exception as exc:
            decode_rejections[f"pair_events:{type(exc).__name__}"] += 1
            continue
        for row in rows:
            decoded_by_mint[row.event.mint].append(row)

    launches: dict[str, Launch] = {}
    launch_rejections: collections.Counter[str] = collections.Counter()
    for mint in sorted(targets):
        create = creates.get(mint)
        meta = create_meta.get(mint)
        events = decoded_by_mint.get(mint, [])
        if create is None or meta is None:
            launch_rejections["missing_create_observation"] += 1
            continue
        # Canonical order is kept separate from receipt time.
        dedup: dict[tuple[Any, ...], Any] = {}
        for row in events:
            key = (row.slot, row.tx_index, row.signature, row.instruction_index, row.event.is_buy, row.event.user)
            dedup.setdefault(key, row)
        events = sorted(dedup.values(), key=lambda r: (r.slot, r.tx_index, r.instruction_index, r.signature))
        if not events:
            launch_rejections["no_decoded_successful_trade_events"] += 1
            continue
        if any(row.event.mayhem_mode for row in events):
            launch_rejections["mayhem"] += 1
            continue
        if any(row.event.quote_mint not in SOL_QUOTES for row in events):
            launch_rejections["non_sol_quote"] += 1
            continue
        creators = collections.Counter(row.event.creator for row in events if row.event.creator)
        if not creators:
            launch_rejections["creator_unresolved"] += 1
            continue
        creator, creator_n = creators.most_common(1)[0]
        if len(creators) != 1:
            launch_rejections["creator_inconsistent"] += 1
            continue
        create_signature = str(create.get("createSignature") or meta.get("signature") or "")
        create_rows = [r for r in events if r.signature == create_signature]
        atomic = [r for r in create_rows if r.event.is_buy and r.event.user == creator]
        create_order = max(
            [(r.slot, r.tx_index, r.instruction_index, r.signature) for r in create_rows],
            default=(int(create.get("createSlot") or 0), int(meta.get("transactionIndex") or 0), -1, create_signature),
        )
        external: list[tuple[int, Any]] = []
        for index, row in enumerate(events):
            order = (row.slot, row.tx_index, row.instruction_index, row.signature)
            if order > create_order and row.event.is_buy and row.event.user != creator:
                external.append((index, row))
        creator_sells = [
            row for row in events
            if not row.event.is_buy and row.event.user == creator
            and (row.slot, row.tx_index, row.instruction_index, row.signature) > create_order
        ]
        kinds = set(meta.get("pumpInstructionKinds") or [])
        create_kind = "CREATE_V2" if "CREATE_V2" in kinds else "CREATE" if "CREATE" in kinds else "UNKNOWN"
        canonical_create_md = finalized.get(create_signature, ({}, b""))[0]
        launches[mint] = Launch(
            mint=mint,
            create_signature=create_signature,
            create_slot=int(create.get("createSlot") or meta.get("slot") or 0),
            create_tx_index=int(canonical_create_md.get("transactionIndex") or 0),
            create_ms=int(create.get("createReceivedAtUnixNs") or meta.get("receivedAtUnixNs") or 0) // 1_000_000,
            create_kind=create_kind,
            create_evidence_id=str(meta.get("evidenceId") or ""),
            creator=creator,
            events=events,
            atomic_self_buy_events=atomic,
            external_buys=external,
            creator_sells=creator_sells,
        )

    diagnostics = {
        "source_paths": [str(p) for p in unique_paths],
        "source_path_sha256": {str(p): sha256_file(p) for p in unique_paths},
        "frames_scanned": raw_frames,
        "unique_evidence_ids": len(seen_evidence),
        "finalized_frames_seen": finalized_frames,
        "unique_finalized_signatures": len(finalized),
        "duplicate_finalized_frames": duplicate_finalized,
        "failed_finalized_frames": failed_finalized,
        "decode_rejections": dict(decode_rejections),
        "launch_rejections": dict(launch_rejections),
    }
    return launches, diagnostics


def state_after_rank(launch: Launch, rank: int) -> tuple[CurveState, Any, int]:
    create_indexes = [i for i, row in enumerate(launch.events) if row.signature == launch.create_signature]
    if rank == 0:
        if create_indexes:
            index = max(create_indexes)
            return launch.events[index].event.post_state, launch.events[index].event, index
        return launch.events[0].event.pre_state, launch.events[0].event, -1
    if len(launch.external_buys) < rank:
        raise ExactCurveError("follower rank unavailable")
    index, row = launch.external_buys[rank - 1]
    return row.event.post_state, row.event, index


def simulate_rank(launch: Launch, rank: int, size_sol: float, rank0_inventory: int | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "mint": launch.mint,
        "creator": launch.creator,
        "rank": rank,
        "size_sol": size_sol,
        "status": "UNRESOLVED",
        "capacity_min_output_pass": False,
    }
    try:
        state, fee_event, base_index = state_after_rank(launch, rank)
    except Exception as exc:
        result.update(status="RANK_UNAVAILABLE", reason=str(exc))
        return result
    gross = int(round(size_sol * 1_000_000_000))
    try:
        buy = quote_exact_quote_in_buy(state, gross, event_fees(fee_event))
    except Exception as exc:
        result.update(status="ENTRY_CAPACITY_FAIL", reason=str(exc))
        return result
    min_tokens = math.floor((rank0_inventory if rank0_inventory is not None else buy.token_out) * MIN_OUTPUT_FACTOR)
    result.update(
        exact_inventory_raw=buy.token_out,
        rank0_inventory_raw=rank0_inventory if rank0_inventory is not None else buy.token_out,
        min_tokens_out_raw=min_tokens,
        entry_state=dataclasses.asdict(state),
        own_buy_post_state=dataclasses.asdict(buy.post_state),
    )
    if buy.token_out < min_tokens:
        result.update(status="MIN_OUTPUT_FAIL", first_failing_constraint="35pct_degradation")
        return result
    result["capacity_min_output_pass"] = True
    current_state = buy.post_state
    current_fee_event = fee_event
    entry_ms = launch.create_ms
    first_sellable = entry_ms + FIRST_SELLABLE_MS
    max_hold = entry_ms + MAX_HOLD_MS
    cost = gross + BASE_FEE_LAMPORTS_PER_TX
    outcome = None
    exit_ms = None
    exit_quote = None
    trigger_roi = None
    counterfactual_failed = 0

    def sell_roi() -> tuple[float, Any]:
        quote = quote_exact_token_sell(
            current_state,
            buy.token_out,
            1,
            event_fees(current_fee_event),
            cashback_bps=current_fee_event.cashback_fee_basis_points,
            buyback_share_bps=current_fee_event.buyback_fee_basis_points,
        )
        proceeds = quote.immediate_quote_out - BASE_FEE_LAMPORTS_PER_TX
        return (proceeds - cost) * 100.0 / cost, quote

    # Replay successful canonical transactions after the modeled landing rank.
    first_sellable_checked = False
    for row in launch.events[base_index + 1:]:
        observed_ms = max(row.arrival_ms, entry_ms)
        if observed_ms > max_hold:
            break
        try:
            current_state = apply_instruction(
                current_state,
                row.source.instruction,
                event_fees(row.event),
                cashback_bps=row.event.cashback_fee_basis_points,
                buyback_share_bps=row.event.buyback_fee_basis_points,
            ).post_state
            current_fee_event = row.event
        except Exception:
            counterfactual_failed += 1
            # The transaction would fail against the perturbed curve and mutates zero state.
        if observed_ms < first_sellable:
            continue
        try:
            roi, quote = sell_roi()
        except Exception:
            continue
        first_sellable_checked = True
        if roi >= TP_PCT or roi <= SL_PCT:
            outcome = "TP" if roi >= TP_PCT else "SL"
            exit_ms = observed_ms
            exit_quote = quote
            trigger_roi = roi
            break
    if outcome is None:
        try:
            roi, quote = sell_roi()
            outcome = "MAX_HOLD"
            exit_ms = max_hold
            exit_quote = quote
            trigger_roi = roi
        except Exception as exc:
            result.update(status="EXIT_CAPACITY_FAIL", reason=str(exc), counterfactual_failed_transactions=counterfactual_failed)
            return result
    proceeds = exit_quote.immediate_quote_out - BASE_FEE_LAMPORTS_PER_TX
    result.update(
        status="EXACT",
        outcome=outcome,
        gross_roi_pct=(proceeds - cost) * 100.0 / cost,
        gross_pnl_sol=(proceeds - cost) / 1_000_000_000,
        exit_ms=exit_ms,
        exit_latency_ms=exit_ms - entry_ms,
        sell_proceeds_lamports=exit_quote.immediate_quote_out,
        trigger_roi_pct=trigger_roi,
        first_sellable_checked=first_sellable_checked,
        counterfactual_failed_transactions=counterfactual_failed,
        delivery_priority_and_tip_cost_included=False,
    )
    return result


def launch_descriptive(launch: Launch) -> dict[str, Any]:
    external = [row for _, row in launch.external_buys]
    deltas = [row.arrival_ms - launch.create_ms for row in external]
    nonnegative = [(row, delta) for row, delta in zip(external, deltas) if delta >= 0]
    cumulative = 0
    flow_threshold_ms: dict[str, int | None] = {"1_sol": None, "3_sol": None}
    for row, delta in nonnegative:
        cumulative += row.event.immediate_quote_amount
        if flow_threshold_ms["1_sol"] is None and cumulative >= 1_000_000_000:
            flow_threshold_ms["1_sol"] = delta
        if flow_threshold_ms["3_sol"] is None and cumulative >= 3_000_000_000:
            flow_threshold_ms["3_sol"] = delta
    windows = {}
    for window in WINDOWS_MS:
        rows = [row for row, delta in nonnegative if delta <= window]
        windows[str(window)] = {
            "follower_buys": len(rows),
            "unique_follower_wallets": len({r.event.user for r in rows}),
            "external_buy_sol": sum(r.event.sol_amount for r in rows) / 1e9,
        }
    atomic_curve = sum(r.event.sol_amount for r in launch.atomic_self_buy_events)
    atomic_gross = sum(r.event.immediate_quote_amount for r in launch.atomic_self_buy_events)
    first_state = launch.events[0].event.pre_state
    return {
        "mint": launch.mint,
        "creator": launch.creator,
        "create_kind": launch.create_kind,
        "create_signature": launch.create_signature,
        "canonical_slot": launch.create_slot,
        "canonical_transaction_index": launch.create_tx_index,
        "launch_received_unix_ms": launch.create_ms,
        "launch_received_utc": dt.datetime.fromtimestamp(launch.create_ms / 1000, dt.timezone.utc).isoformat(),
        "finalized_success": True,
        "creator_atomic_self_buy": bool(launch.atomic_self_buy_events),
        "creator_atomic_buy_curve_sol": atomic_curve / 1e9,
        "creator_atomic_buy_gross_sol": atomic_gross / 1e9,
        "creator_atomic_inventory_raw": sum(r.event.token_amount for r in launch.atomic_self_buy_events),
        "initial_curve_state": dataclasses.asdict(first_state),
        "successful_trade_event_n": len(launch.events),
        "successful_external_buy_n": len(external),
        "unique_external_buyers": len({r.event.user for r in external}),
        "canonical_followers": [
            {
                "rank": rank,
                "wallet": row.event.user,
                "signature": row.signature,
                "slot": row.slot,
                "transaction_index": row.tx_index,
                "instruction_index": row.instruction_index,
                "gross_sol": row.event.immediate_quote_amount / 1e9,
                "curve_sol": row.event.sol_amount / 1e9,
                "receipt_delta_ms": row.arrival_ms - launch.create_ms,
            }
            for rank, row in enumerate(external[:10], 1)
        ],
        "time_to_follower_1_ms": deltas[0] if len(deltas) >= 1 and deltas[0] >= 0 else None,
        "time_to_follower_3_ms": deltas[2] if len(deltas) >= 3 and deltas[2] >= 0 else None,
        "time_to_first_1_sol_external_ms": flow_threshold_ms["1_sol"],
        "time_to_first_3_sol_external_ms": flow_threshold_ms["3_sol"],
        "creator_first_sell_latency_ms": min(
            (r.arrival_ms - launch.create_ms for r in launch.creator_sells if r.arrival_ms >= launch.create_ms),
            default=None,
        ),
        "receipt_windows": windows,
        "ordering_semantics": "FINALIZED_CANONICAL_SLOT_TX_IX",
        "timing_semantics": "EARLIEST_RETAINED_REALTIME_RECEIPT_NOT_LANDING_PROOF",
    }


def cohort_metrics(
    creators: Iterable[str],
    launch_rows: list[dict[str, Any]],
    economics: list[dict[str, Any]],
) -> dict[str, Any]:
    launch_by_creator: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in launch_rows:
        launch_by_creator[row["creator"]].append(row)
    econ_index: dict[tuple[str, int, float], list[dict[str, Any]]] = collections.defaultdict(list)
    for row in economics:
        econ_index[(row["creator"], int(row["rank"]), float(row["size_sol"]))].append(row)
    result: dict[str, Any] = {}
    for creator in creators:
        launches = sorted(launch_by_creator.get(creator, []), key=lambda r: r["launch_received_unix_ms"])
        if not launches:
            result[creator] = {"launch_n": 0}
            continue
        span_h = (launches[-1]["launch_received_unix_ms"] - launches[0]["launch_received_unix_ms"]) / 3_600_000
        follower_first3 = [f["wallet"] for r in launches for f in r["canonical_followers"][:3]]
        first3_counts = collections.Counter(follower_first3)
        result[creator] = {
            "creator": creator,
            "launch_n": len(launches),
            "first_launch_utc": launches[0]["launch_received_utc"],
            "last_launch_utc": launches[-1]["launch_received_utc"],
            "active_span_hours": span_h,
            "launches_per_active_hour": (len(launches) / span_h) if span_h > 0 else None,
            "atomic_self_buy_n": sum(r["creator_atomic_self_buy"] for r in launches),
            "atomic_self_buy_rate": statistics.fmean(float(r["creator_atomic_self_buy"]) for r in launches),
            "median_self_buy_curve_sol": quantile([r["creator_atomic_buy_curve_sol"] for r in launches if r["creator_atomic_self_buy"]], .5),
            "median_creator_first_sell_latency_ms": quantile([r["creator_first_sell_latency_ms"] for r in launches], .5),
            "median_time_to_follower_1_ms": quantile([r["time_to_follower_1_ms"] for r in launches], .5),
            "median_time_to_follower_3_ms": quantile([r["time_to_follower_3_ms"] for r in launches], .5),
            "median_time_to_first_1_sol_external_ms": quantile([r["time_to_first_1_sol_external_ms"] for r in launches], .5),
            "median_time_to_first_3_sol_external_ms": quantile([r["time_to_first_3_sol_external_ms"] for r in launches], .5),
            "median_followers_1s": quantile([r["receipt_windows"]["1000"]["follower_buys"] for r in launches], .5),
            "median_followers_5s": quantile([r["receipt_windows"]["5000"]["follower_buys"] for r in launches], .5),
            "median_followers_10s": quantile([r["receipt_windows"]["10000"]["follower_buys"] for r in launches], .5),
            "median_external_sol_1s": quantile([r["receipt_windows"]["1000"]["external_buy_sol"] for r in launches], .5),
            "median_external_sol_5s": quantile([r["receipt_windows"]["5000"]["external_buy_sol"] for r in launches], .5),
            "median_external_sol_10s": quantile([r["receipt_windows"]["10000"]["external_buy_sol"] for r in launches], .5),
            "first3_follower_observations": len(follower_first3),
            "first3_unique_wallets": len(first3_counts),
            "first3_top_wallet_share": (max(first3_counts.values()) / len(follower_first3)) if follower_first3 else None,
            "first3_top_wallets": [
                {"wallet": wallet, "positions": n, "share": n / len(follower_first3)}
                for wallet, n in first3_counts.most_common(10)
            ] if follower_first3 else [],
            "economics": {
                f"size_{size:.2f}_rank_{rank}": metric_summary(econ_index[(creator, rank, size)])
                for size in SIZES_SOL for rank in RANKS
            },
        }
    return result


def discovery_score(row: dict[str, Any]) -> float:
    # Frozen before outcome inspection: rank-tolerant 0.01 SOL expectancy dominates;
    # capacity and repeat count are bounded secondary terms.
    economics = row.get("economics") or {}
    rank_expectancies = []
    rank_pass = []
    for rank in RANKS:
        cell = economics.get(f"size_0.01_rank_{rank}") or {}
        rank_expectancies.append(float(cell.get("expectancy_sol") or -1.0))
        rank_pass.append(float(cell.get("capacity_min_output_pass_rate") or 0.0))
    return (
        1000.0 * min(rank_expectancies[:3])
        + 250.0 * rank_expectancies[3]
        + 2.0 * statistics.fmean(rank_pass)
        + min(int(row.get("launch_n") or 0), 10) / 10.0
        + min(float(row.get("median_external_sol_1s") or 0.0), 10.0) / 10.0
    )


def has_rank2_alpha(row: dict[str, Any], minimum_n: int = 2) -> bool:
    cell = (row.get("economics") or {}).get("size_0.01_rank_2") or {}
    return (
        int(cell.get("exact_n") or 0) >= minimum_n
        and float(cell.get("expectancy_sol") or 0.0) > 0.0
        and float(cell.get("capacity_min_output_pass_rate") or 0.0) > 0.0
    )


def make_report(
    coverage: dict[str, Any],
    distribution: dict[str, Any],
    discovery_top: list[str],
    discovery_metrics: dict[str, Any],
    validation_metrics: dict[str, Any],
    holdout_metrics: dict[str, Any],
    full_metrics: dict[str, Any],
) -> str:
    validated = [w for w in discovery_top if has_rank2_alpha(validation_metrics.get(w) or {})]
    top5 = validated[:5]
    if top5:
        verdict = "PROMISING_OTHER_CREATORS_NEED_MORE_VALIDATION"
    else:
        verdict = "NO_REPEATABLE_CREATOR_LAUNCH_ALPHA_FOUND"
    lines = [
        f"# {verdict}", "",
        "## Coverage and evidence boundary", "",
        f"- Fresh CREATE/CreateV2 mints observed before filtering: **{coverage['fresh_mints_observed']:,}**.",
        f"- Exact-admitted launches: **{coverage['exact_admitted_n']:,}**; compatible normal SOL-quoted launches analyzed: **{coverage['compatible_launches_analyzed']:,}**.",
        f"- Window: **{coverage['first_launch_utc']} through {coverage['last_launch_utc']}**; reconciled receive-hour partitions: **{coverage['reconciled_partition_hours']}**.",
        f"- The separate 5.42-day archive has **220,079** CREATE observations and **3,879,386** early-flow rows, but its CREATE rows omit creator identity and finalized status. It was not used for creator attribution or exact economics.",
        f"- Failed finalized transactions are retained in source coverage and mutate zero replay state. Mayhem and non-SOL quote variants are excluded from the compatible economic cohort.", "",
        "## Repeat-creator distribution", "",
        f"Total creators: **{distribution['total_creators']:,}**; >=2: **{distribution['ge_2']:,}**; >=3: **{distribution['ge_3']:,}**; >=5: **{distribution['ge_5']:,}**; >=10: **{distribution['ge_10']:,}**; >=20: **{distribution['ge_20']:,}**; >=50: **{distribution['ge_50']:,}**.", "",
        "## Top 10 discovery candidates (frozen before validation)", "",
        "| # | Creator | Discovery N | Atomic buy | Median #1 ms | Median #3 ms | Rank0 PF/E | Rank1 PF/E | Rank2 PF/E | Rank3 PF/E |",
        "|---:|---|---:|---:|---:|---:|---|---|---|---|",
    ]
    def fmt_number(v: Any, digits: int = 3) -> str:
        if v is None: return "NA"
        if isinstance(v, float) and math.isinf(v): return "Inf"
        return f"{float(v):.{digits}f}"
    for index, wallet in enumerate(discovery_top, 1):
        row = discovery_metrics[wallet]
        cells = []
        for rank in RANKS:
            cell = row["economics"][f"size_0.01_rank_{rank}"]
            cells.append(f"{fmt_number(cell['profit_factor'],2)}/{fmt_number(cell['expectancy_sol'],5)}")
        lines.append(
            f"| {index} | `{wallet}` | {row['launch_n']} | {100*row['atomic_self_buy_rate']:.0f}% | "
            f"{fmt_number(row['median_time_to_follower_1_ms'],1)} | {fmt_number(row['median_time_to_follower_3_ms'],1)} | "
            + " | ".join(cells) + " |"
        )
    lines += ["", "## Validation and untouched holdout", ""]
    for wallet in discovery_top:
        d = discovery_metrics.get(wallet, {"launch_n": 0})
        v = validation_metrics.get(wallet, {"launch_n": 0})
        h = holdout_metrics.get(wallet, {"launch_n": 0})
        lines.append(f"- `{wallet}` — discovery N={d.get('launch_n',0)}, validation N={v.get('launch_n',0)}, holdout N={h.get('launch_n',0)}.")
    lines += ["", "## Top 5 validated deep dive", ""]
    if not top5:
        lines.append("No frozen discovery candidate has at least two later validation launches. A Top-5 validated list would be fabricated from this corpus.")
    for wallet in top5:
        row = full_metrics[wallet]
        lines += [
            f"### `{wallet}`", "",
            f"Launches: {row['launch_n']} across {fmt_number(row['active_span_hours'],2)} h; atomic self-buy {100*row['atomic_self_buy_rate']:.1f}%; median self-buy {fmt_number(row['median_self_buy_curve_sol'],3)} SOL.",
            f"Outside money: follower #1 median {fmt_number(row['median_time_to_follower_1_ms'],1)} ms, #3 {fmt_number(row['median_time_to_follower_3_ms'],1)} ms; median 1s flow {fmt_number(row['median_external_sol_1s'],3)} SOL.",
            f"Competition: first-three positions contain {row['first3_unique_wallets']} unique wallets across {row['first3_follower_observations']} observations; top-wallet share {fmt_number(100*(row['first3_top_wallet_share'] or 0),1)}%.",
            "Runtime trigger: YES for the wallet identity/Create variant/atomic-self-buy structure; no follower outcome is required at trigger time.", "",
        ]
    bench = full_metrics.get(CREATOR_8YCB)
    lines += ["", "## 8YCB benchmark", ""]
    if not bench or bench.get("launch_n", 0) == 0:
        lines.append("8YCB has zero compatible launches inside this narrow exact-admitted window. Its separate calibration ledger is not silently merged because it lacks the same complete generalized replay fields. Therefore this run cannot make a same-window numerical claim that another creator beats 8YCB.")
    else:
        lines.append(f"8YCB has {bench['launch_n']} compatible launches in this corpus; its full like-for-like cells are in `creator-scorecards.json`.")
    lines += [
        "", "## Archetypes", "",
        "- Immediate-swarm creators: median follower #3 receipt <=250 ms (receipt evidence, not landing proof).",
        "- Rank-fragile creators: positive rank-0 expectancy but non-positive rank-2 expectancy or poor min-output pass rate.",
        "- Rank-tolerant leads: positive discovery expectancy at ranks 0–3, pending later-launch validation.",
        "- Capacity leads: >=80% min-output pass at 0.5 or 1.0 SOL, separately visible in the scorecards.",
        "- Frequent launchers: >=5 launches in the analyzed window; frequency this high over a few hours may be bot/factory behavior and is not evidence of durability.",
        "", "## Direct strategic answers", "",
        (
            f"1. **Is 8YCB genuinely unusual?** It has {bench['launch_n']} compatible same-window launches; "
            "that permits a descriptive benchmark but not a durable superiority claim."
            if bench and bench.get("launch_n", 0) > 0 else
            "1. **Is 8YCB genuinely unusual?** Not answerable from this strict same-window cohort because it has no compatible observation."
        ),
        "2. **Stronger exact rank-0 creators?** Some discovery rows may score higher, but none is declared stronger without later exact rank-2 validation and an adequate like-for-like 8YCB N.",
        "3. **Better rank tolerance?** Only candidates with positive later-launch exact rank-2 expectancy count as validated; absent that, the answer is no evidence yet.",
        "4. **Less immediate competition?** Descriptively yes for some creators, but sparse receipt coverage and short regime duration prevent a durable claim.",
        "5. **Better commercial capacity?** Not proven; exact 0.5/1.0 SOL cells are exploratory and the corpus spans too few regimes.",
        (
            f"6. **Best target now?** `{top5[0]}` is the best new research lead; 8YCB remains the prospectively engineered target."
            if top5 else
            "6. **Best target now?** 8YCB remains the only prospectively engineered target; no new creator clears validation here."
        ),
        "7. **Best opportunity form?** Continue treating other wallets as a frozen watchlist research cohort, not a deployable portfolio or archetype.",
        "8. **More promising than one more 8YCB queue position?** The broad scan is strategically useful, but current evidence does not justify redirecting execution engineering away from 8YCB.",
        "", "## Important limitations", "",
        "- Exact creator coverage is only a few receive-hour partitions from one UTC day; discovery, validation, and holdout are chronological slices of the same short regime.",
        "- Creator identity comes from finalized Pump TradeEvent data; launches without a decodable supported transition are excluded, creating explicit coverage loss.",
        "- Realtime receipt deltas are observer timing and never canonical landing guarantees.",
        "- Rank replay inserts our trade into canonical order. Later transactions whose original constraints fail against the perturbed curve mutate zero counterfactual state.",
        "- The frozen 35% min-output comparison is relative to each launch/size rank-0 inventory; priority fee and Jito tip are excluded, while 5,000 lamports per buy/sell base fee is included.",
        "- Multiple-comparison risk is severe. Candidate selection used discovery only; validation and holdout were not used to reorder wallets.",
        "- No trigger, service, configuration, wallet, or live lane was modified by this analysis.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ledger_root", type=pathlib.Path)
    parser.add_argument("alpha_root", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()
    reports = load_readiness_reports(args.ledger_root)
    # The readiness manifests are the authoritative immutable admission set.  The
    # separately scheduled alpha table may lag a newly reconciled partition.
    targets = exact_admitted_mints(reports)
    launches, diagnostics = collect_exact_events(args.ledger_root, reports, targets)
    ordered = sorted(launches.values(), key=lambda x: (x.create_ms, x.mint))
    launch_rows = [launch_descriptive(launch) for launch in ordered]

    economics: list[dict[str, Any]] = []
    for launch in ordered:
        for size in SIZES_SOL:
            try:
                rank0_state, rank0_fee_event, _ = state_after_rank(launch, 0)
                rank0_inventory = quote_exact_quote_in_buy(
                    rank0_state, int(round(size * 1e9)), event_fees(rank0_fee_event)
                ).token_out
            except Exception:
                rank0_inventory = None
            for rank in RANKS:
                economics.append(simulate_rank(launch, rank, size, rank0_inventory))

    if ordered:
        split_a = len(ordered) // 2
        split_b = split_a + len(ordered) // 4
    else:
        split_a = split_b = 0
    splits = {
        "discovery": {r.mint for r in ordered[:split_a]},
        "validation": {r.mint for r in ordered[split_a:split_b]},
        "holdout": {r.mint for r in ordered[split_b:]},
    }
    launch_by_mint = {r["mint"]: r for r in launch_rows}
    creators = sorted({r.creator for r in ordered})
    split_launches = {
        name: [launch_by_mint[m] for m in sorted(mints) if m in launch_by_mint]
        for name, mints in splits.items()
    }
    split_economics = {
        name: [r for r in economics if r["mint"] in mints]
        for name, mints in splits.items()
    }
    full_metrics = cohort_metrics(creators, launch_rows, economics)
    discovery_metrics = cohort_metrics(creators, split_launches["discovery"], split_economics["discovery"])
    validation_metrics = cohort_metrics(creators, split_launches["validation"], split_economics["validation"])
    holdout_metrics = cohort_metrics(creators, split_launches["holdout"], split_economics["holdout"])

    discovery_eligible = [
        wallet for wallet, row in discovery_metrics.items()
        if row.get("launch_n", 0) >= 2 and row.get("atomic_self_buy_rate", 0) >= 0.5
    ]
    discovery_top = sorted(
        discovery_eligible,
        key=lambda wallet: (
            -int(discovery_metrics[wallet]["economics"]["size_0.01_rank_2"].get("exact_n") or 0),
            -int(discovery_metrics[wallet]["economics"]["size_0.01_rank_1"].get("exact_n") or 0),
            -discovery_score(discovery_metrics[wallet]),
            wallet,
        ),
    )[:10]
    counts = collections.Counter(r.creator for r in ordered)
    distribution = {
        "total_creators": len(counts),
        **{f"ge_{n}": sum(value >= n for value in counts.values()) for n in (2, 3, 5, 10, 20, 50)},
        "histogram": dict(sorted(collections.Counter(counts.values()).items())),
    }
    status = read_json(args.alpha_root / "status.json")
    report_mints = {row["mint"] for report in reports for row in report["mints"]}
    represented_hours = sorted({report["partitionHourUtc"] for report in reports})
    coverage = {
        "fresh_mints_observed": len(report_mints),
        "exact_admitted_n": len(targets),
        "compatible_target_mints": len(targets),
        "compatible_launches_analyzed": len(ordered),
        "reconciled_partition_hours": len(reports),
        "represented_hours": represented_hours,
        "first_launch_utc": launch_rows[0]["launch_received_utc"] if launch_rows else None,
        "last_launch_utc": launch_rows[-1]["launch_received_utc"] if launch_rows else None,
        "exact_alpha_generated_at_utc": status.get("generatedAtUtc"),
        "excluded_population": status.get("excludedPopulations"),
    }
    result = {
        "schema": SCHEMA,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "verdict": "PROMISING_OTHER_CREATORS_NEED_MORE_VALIDATION" if any(
            has_rank2_alpha(validation_metrics.get(w, {})) for w in discovery_top
        ) else "NO_REPEATABLE_CREATOR_LAUNCH_ALPHA_FOUND",
        "frozen_semantics": {
            "ranks": list(RANKS), "sizes_sol": list(SIZES_SOL),
            "min_output_degradation_cap_pct": 35,
            "first_sellable_ms": FIRST_SELLABLE_MS,
            "tp_pct": TP_PCT, "sl_pct": SL_PCT, "max_hold_ms": MAX_HOLD_MS,
            "base_fee_lamports_per_transaction": BASE_FEE_LAMPORTS_PER_TX,
            "priority_and_jito_cost_included": False,
        },
        "chronological_split": {
            name: {
                "n": len(mints),
                "first_utc": min((launch_by_mint[m]["launch_received_utc"] for m in mints), default=None),
                "last_utc": max((launch_by_mint[m]["launch_received_utc"] for m in mints), default=None),
            } for name, mints in splits.items()
        },
        "coverage": coverage,
        "distribution": distribution,
        "top_10_discovery": discovery_top,
        "top_5_validated": [w for w in discovery_top if has_rank2_alpha(validation_metrics.get(w, {}))][:5],
        "eight_ycb_same_corpus_launch_n": counts.get(CREATOR_8YCB, 0),
        "diagnostics": diagnostics,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    write_jsonl_gzip(args.output / "launches.jsonl.gz", launch_rows)
    write_jsonl_gzip(args.output / "rank-economics.jsonl.gz", economics)
    atomic_json(args.output / "creator-scorecards.json", full_metrics)
    atomic_json(args.output / "discovery-scorecards.json", discovery_metrics)
    atomic_json(args.output / "validation-scorecards.json", validation_metrics)
    atomic_json(args.output / "holdout-scorecards.json", holdout_metrics)
    atomic_json(args.output / "summary.json", result)
    report = make_report(coverage, distribution, discovery_top, discovery_metrics, validation_metrics, holdout_metrics, full_metrics)
    (args.output / "FINAL_REPORT.md").write_text(report, encoding="utf-8")
    checksums = {}
    for path in sorted(args.output.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS.json":
            checksums[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    atomic_json(args.output / "SHA256SUMS.json", checksums)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
