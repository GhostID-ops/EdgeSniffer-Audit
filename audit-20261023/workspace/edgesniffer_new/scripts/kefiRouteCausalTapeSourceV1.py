#!/usr/bin/env python3
"""Receive-only native route-causal PumpSwap tape for KEFI research.

This is deliberately separate from the existing PumpSwap tape collector.  The
existing tape is preserved and remains the source of historical evidence.  This
collector adds the missing route provenance for every exact PumpSwap transition,
including non-KEFI contemporaneous controls:

* direct top-level PumpSwap versus PumpSwap invoked through a router/CPI;
* the exact transaction and decoder lineage for each transition;
* pre/post pool state, fees, and durable source sequencing;
* tracked-wallet and non-tracked control rows in the same native stream.

It never assigns PnL, selects a policy, submits transactions, or changes an
existing collector.  It is intended to run as a separate receive-only service.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import pumpSwapExactEconomicsV1 as pumpswap
import twoCqRouteEvidenceAdaptersV1 as route_adapters


SCHEMA = "KEFI_ROUTE_CAUSAL_TAPE_V1"
STATE_SCHEMA = "KEFI_ROUTE_CAUSAL_TAPE_SOURCE_STATE_V1"
PUMP_CURVE_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP_PROGRAM_ID = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class RpcClient:
    def __init__(self, url: str, timeout: float = 20.0):
        self.url = url
        self.timeout = timeout
        self.request_id = 0

    def call(self, method: str, params: list[Any]) -> Any:
        self.request_id += 1
        payload = json.dumps({"jsonrpc": "2.0", "id": self.request_id, "method": method, "params": params}).encode()
        request = urllib.request.Request(self.url, data=payload, headers={"content-type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            result = json.loads(response.read().decode())
        if result.get("error"):
            raise RuntimeError(f"RPC_{method}:{result['error']}")
        return result.get("result")


def append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_state(path: Path, cohort_id: str, boundary: str) -> dict[str, Any]:
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("schema") != STATE_SCHEMA or state.get("cohort_id") != cohort_id or state.get("boundary_utc") != boundary:
            raise RuntimeError("FAIL_CLOSED: native route source state contract mismatch")
        state.setdefault("seen_signatures", [])
        state.setdefault("error_samples", [])
        state.setdefault("ignored_noncanonical", 0)
        state.setdefault("source_instance_id", uuid.uuid4().hex)
        state.setdefault("continuity_epoch", 1)
        state.setdefault("next_durable_output_sequence", int(state.get("records_written", 0)))
        state.setdefault("teacher_source_byte_offset", 0)
        return state
    return {
        "schema": STATE_SCHEMA,
        "cohort_id": cohort_id,
        "boundary_utc": boundary,
        "source_instance_id": uuid.uuid4().hex,
        "continuity_epoch": 1,
        "next_durable_output_sequence": 0,
        "seen_signatures": [],
        "records_written": 0,
        "errors": 0,
        "error_samples": [],
        "ignored_noncanonical": 0,
        "teacher_source_byte_offset": 0,
    }


def existing_signatures(path: Path) -> set[str]:
    result: set[str] = set()
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("schema") == SCHEMA and row.get("candidate_signature"):
                result.add(str(row["candidate_signature"]))
    return result


def discover_pools(source: Path) -> set[str]:
    pools: set[str] = set()
    if not source.exists():
        return pools
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("schema") != "KEFI_13_ARM_FINALIZED_RPC_RAW_TX_V1":
            continue
        try:
            for transition in pumpswap.validate_transaction(row.get("transaction") or {}):
                pools.add(str(transition["pool"]))
        except Exception:
            continue
    return pools


def source_rows_since(source: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    """Read only newly appended finalized-source frames from an append-only file."""
    if not source.exists():
        return [], offset
    size = source.stat().st_size
    if size < offset:
        offset = 0
    rows: list[dict[str, Any]] = []
    with source.open("rb") as handle:
        handle.seek(offset)
        while True:
            line = handle.readline()
            if not line:
                break
            if line.strip():
                rows.append(json.loads(line.decode("utf-8")))
        return rows, handle.tell()


def is_mixed_noncanonical_route(transaction: dict[str, Any]) -> bool:
    logs = (transaction.get("meta") or {}).get("logMessages") or []
    has_pump_event = bool(pumpswap.decode_events(logs))
    has_curve_leg = any(f"Program {PUMP_CURVE_PROGRAM_ID} invoke" in str(line) for line in logs)
    if not has_pump_event or not has_curve_leg:
        return False
    try:
        return not pumpswap.decode_instructions(transaction)
    except Exception:
        return False


def route_provenance(transaction: dict[str, Any]) -> dict[str, Any] | None:
    resolved = route_adapters.resolve_route(transaction)
    venues = resolved.get("underlyingVenues") or []
    if len(venues) != 1 or venues[0].get("venue") != "PUMPSWAP":
        return None
    venue = venues[0]
    invocation = str(venue.get("invocation") or "")
    routers = list(resolved.get("routers") or [])
    route = "PUMPSWAP_DIRECT" if invocation == "TOP_LEVEL" and not routers else "PUMPSWAP_CPI"
    top, inner = route_adapters.invoked_programs(transaction)
    return {
        "route": route,
        "route_certainty": "EXACT_ROUTE",
        "underlying_venue": "PUMPSWAP",
        "venue_program_id": venue.get("programId"),
        "invocation": invocation,
        "routers": routers,
        "top_level_programs": top,
        "inner_programs": inner,
        "unknown_programs": list(resolved.get("unknownPrograms") or []),
    }


def native_rows(
    transaction: dict[str, Any], signature: str, block_time: int | None, slot: int,
    tracked_wallet: str, *, source_instance_id: str, continuity_epoch: int,
    durable_output_sequence_start: int,
) -> list[dict[str, Any]]:
    provenance = route_provenance(transaction)
    if provenance is None:
        return []
    transitions = pumpswap.validate_transaction(transaction)
    rows: list[dict[str, Any]] = []
    for event_index, transition in enumerate(transitions):
        post = transition["postState"]
        pre = transition.get("preState") or {}
        rows.append({
            "schema": SCHEMA,
            "candidate_signature": signature,
            "event_index": event_index,
            "event_timestamp": int(block_time or transition.get("timestamp", 0)),
            "captured_at_utc": utc_now(),
            "slot": int(slot),
            "source_frame_sha256": sha256(transaction),
            "source_partition": "finalized-rpc-route-causal-tape",
            "source_instance_id": source_instance_id,
            "continuity_epoch": continuity_epoch,
            "durable_output_sequence": durable_output_sequence_start + len(rows),
            "user": transition["user"],
            "is_tracked_teacher": transition["user"] == tracked_wallet,
            "exact_transition_validated": True,
            "route_evidence_native": True,
            **provenance,
            "side": transition["side"],
            "amount_mode": transition["amountMode"],
            "instruction_amount_raw": transition.get("instructionAmountRaw"),
            "instruction_limit_raw": transition.get("instructionLimitRaw"),
            "base_amount": int(transition["amountRaw"]),
            "mint": transition["baseMint"],
            "pool": transition["pool"],
            "quote_mint": transition["quoteMint"],
            "pre_state": pre,
            "post_state": post,
            "pool_base_raw": int(post["base"]),
            "pool_quote_raw": int(post["quote"]),
            "pool_quote_delta": int(transition["poolQuoteDeltaRaw"]),
            "virtual_quote_reserves": int(post["virtual_quote"]),
            "curve_quote_amount": int(transition["curveQuoteAmountRaw"]),
            "user_quote_amount": int(transition["userQuoteAmountRaw"]),
            "limit_quote_amount": int(transition["limitQuoteAmount"]),
            "min_base_amount": int(transition["minBaseAmount"]),
            "lp_fee_bps": int(transition["lpFeeBps"]),
            "lp_fee": int(transition["lpFeeRaw"]),
            "protocol_fee_bps": int(transition["protocolFeeBps"]),
            "protocol_fee": int(transition["protocolFeeRaw"]),
            "coin_creator_bps": int(transition["coinCreatorBps"]),
            "coin_creator_fee": int(transition["creatorFeeRaw"]),
            "cashback_bps": int(transition["cashbackBps"]),
            "cashback": int(transition["cashbackRaw"]),
            "buyback_bps": int(transition["buybackBps"]),
            "buyback_fee": int(transition["buybackFeeRaw"]),
        })
    return rows


def run_once(client: RpcClient, state: dict[str, Any], pool_source: Path, output: Path, state_path: Path, tracked_wallet: str, limit: int = 25) -> dict[str, int]:
    seen = set(state["seen_signatures"])
    seen.update(existing_signatures(output))
    boundary = dt.datetime.fromisoformat(state["boundary_utc"].replace("Z", "+00:00"))
    fetched = written = teacher_written = errors = ignored = 0
    next_sequence = int(state.get("next_durable_output_sequence", state.get("records_written", 0)))

    # Prioritize exact tracked-wallet events from the finalized source.  This
    # prevents a long historical control scan from delaying the KEFI signal
    # counters when the source already contains the relevant transactions.
    new_source_rows, new_offset = source_rows_since(pool_source, int(state.get("teacher_source_byte_offset", 0)))
    for raw in new_source_rows:
        if raw.get("schema") != "KEFI_13_ARM_FINALIZED_RPC_RAW_TX_V1":
            continue
        block_time = raw.get("block_time")
        if block_time is not None and dt.datetime.fromtimestamp(int(block_time), tz=dt.timezone.utc) < boundary:
            continue
        signature = str(raw.get("signature") or "")
        if not signature or signature in seen:
            continue
        try:
            rows = native_rows(
                raw.get("transaction") or {}, signature, block_time, int(raw.get("slot") or -1), tracked_wallet,
                source_instance_id=str(state["source_instance_id"]),
                continuity_epoch=int(state["continuity_epoch"]),
                durable_output_sequence_start=next_sequence,
            )
            teacher_rows = [row for row in rows if row.get("user") == tracked_wallet]
            if not teacher_rows:
                continue
            for row in teacher_rows:
                row["durable_output_sequence"] = next_sequence
                append_record(output, row)
                state["records_written"] += 1
                next_sequence += 1
                written += 1
                teacher_written += 1
            seen.add(signature)
        except Exception as error:
            errors += 1
            if len(state.setdefault("error_samples", [])) < 20:
                state["error_samples"].append({"signature": signature, "error": type(error).__name__ + ":" + str(error), "phase": "tracked_source_priority"})
    state["teacher_source_byte_offset"] = new_offset
    state["next_durable_output_sequence"] = next_sequence
    state["seen_signatures"] = list(seen)[-20_000:]
    state["updated_at_utc"] = utc_now()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    pools = discover_pools(pool_source)
    for pool in sorted(pools):
        try:
            summaries = client.call("getSignaturesForAddress", [pool, {"commitment": "finalized", "limit": limit}]) or []
            for summary in reversed(summaries):
                signature = str(summary.get("signature", ""))
                if not signature or signature in seen or summary.get("err") is not None:
                    continue
                block_time = summary.get("blockTime")
                if block_time is not None and dt.datetime.fromtimestamp(int(block_time), tz=dt.timezone.utc) < boundary:
                    seen.add(signature)
                    continue
                try:
                    transaction = client.call("getTransaction", [signature, {"encoding": "jsonParsed", "commitment": "finalized", "maxSupportedTransactionVersion": 1}])
                    fetched += 1
                    seen.add(signature)
                    if not transaction or (transaction.get("meta") or {}).get("err") is not None:
                        continue
                    if is_mixed_noncanonical_route(transaction):
                        ignored += 1
                        continue
                    rows = native_rows(
                        transaction, signature, block_time,
                        int(transaction.get("slot", summary.get("slot", -1))),
                        tracked_wallet,
                        source_instance_id=str(state["source_instance_id"]),
                        continuity_epoch=int(state["continuity_epoch"]),
                        durable_output_sequence_start=next_sequence,
                    )
                    for row in rows:
                        if row["pool"] != pool:
                            continue
                        # Sequence numbers belong to durable output rows, not
                        # decoder event indexes.  A transaction can contain
                        # events for other pools, so assigning the sequence
                        # before this pool filter can create duplicates when
                        # those other events are discarded.
                        row["durable_output_sequence"] = next_sequence
                        append_record(output, row)
                        state["records_written"] += 1
                        next_sequence += 1
                        written += 1
                except Exception as error:
                    errors += 1
                    if len(state.setdefault("error_samples", [])) < 20:
                        state["error_samples"].append({"pool": pool, "signature": signature, "error": type(error).__name__ + ":" + str(error)})
        except Exception as error:
            errors += 1
            if len(state.setdefault("error_samples", [])) < 20:
                state["error_samples"].append({"pool": pool, "error": type(error).__name__ + ":" + str(error)})
    state["seen_signatures"] = list(seen)[-20_000:]
    state["next_durable_output_sequence"] = next_sequence
    state["errors"] += errors
    state["ignored_noncanonical"] = int(state.get("ignored_noncanonical", 0)) + ignored
    return {"pools": len(pools), "fetched": fetched, "written": written, "teacher_written": teacher_written, "errors": errors, "ignored": ignored}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rpc-url", default=os.environ.get("SECURE_RPC_URL") or os.environ.get("SOLANA_RPC_URL"))
    parser.add_argument("--pool-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--cohort-id", required=True)
    parser.add_argument("--boundary-utc", required=True)
    parser.add_argument("--tracked-wallet", default=os.environ.get("TRACKED_WALLET"))
    parser.add_argument("--interval-seconds", type=float, default=15.0)
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    if not args.rpc_url or not args.tracked_wallet:
        raise SystemExit("FAIL_CLOSED: RPC and tracked wallet are required")
    state = load_state(args.state, args.cohort_id, args.boundary_utc)
    client = RpcClient(args.rpc_url)
    while True:
        result = run_once(client, state, args.pool_source, args.output, args.state, args.tracked_wallet, limit=args.limit)
        state["updated_at_utc"] = utc_now()
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, sort_keys=True), flush=True)
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
