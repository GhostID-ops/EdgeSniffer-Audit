#!/usr/bin/env python3
"""One-shot Ranker V2 live edge-capture adapter.

This adapter does not score, tune, or submit. It preserves the frozen Ranker V2
signal as the strategy authority, joins that future-only signal to the matching
execution capsule, writes exactly one selected-candidate row for the Rust live
executor, records the shadow twin, and then disarms itself.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


COHORT = "RANKER_V2_PROSPECTIVE_SHADOW_V2"
LANE = "ranker_v2_prospective_shadow_v2"
POLICY = "ranker_v2_live_edge_capture_v1_tp20_sl20_120s"
THRESHOLD = 0.590596
SOL_IN_LAMPORTS = 1_000_000
WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"
OUT = Path("/mnt/HC_Volume_106775198/edgesniffer-derived/ranker-v2-prospective-shadow-v2")
SOURCE_INPUT = Path("/var/lib/edgesniffer-teacher-impulse-parity-v1/candidate-capsules.jsonl")
DEFAULT_LIVE_OUT = Path("/mnt/HC_Volume_106775198/edgesniffer-derived/ranker-v2-live-edge-capture-v1")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def iso_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def now_ms() -> int:
    return int(time.time() * 1000)


def compact(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 9) if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: compact(val) for key, val in value.items()}
    if isinstance(value, list):
        return [compact(item) for item in value]
    return value


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(compact(value), sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


class DirectHandoffClient:
    def __init__(self, host: str, port: int, enabled: bool):
        self.host = host
        self.port = port
        self.enabled = enabled
        self.sock: socket.socket | None = None

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def send(self, value: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return {
                "enabled": False,
                "ok": True,
                "sendStartMs": None,
                "sendCompleteMs": None,
                "elapsedMs": None,
            }
        if self.sock is None:
            self.sock = socket.create_connection((self.host, self.port), timeout=0.25)
            self.sock.settimeout(0.25)
        send_start = now_ms()
        value.setdefault("transport", {})
        value["transport"]["directHandoff"] = True
        value["transport"]["directHandoffHost"] = self.host
        value["transport"]["directHandoffPort"] = self.port
        value["transport"]["directHandoffSendAtMs"] = send_start
        line = json.dumps(compact(value), sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        try:
            self.sock.sendall(line)
        except OSError:
            self.close()
            self.sock = socket.create_connection((self.host, self.port), timeout=0.25)
            self.sock.settimeout(0.25)
            self.sock.sendall(line)
        send_complete = now_ms()
        value["transport"]["directHandoffSendCompleteAtMs"] = send_complete
        return {
            "enabled": True,
            "ok": True,
            "sendStartMs": send_start,
            "sendCompleteMs": send_complete,
            "elapsedMs": send_complete - send_start,
        }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(compact(value), handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_gzip_jsonl(path: Path):
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except EOFError:
        return


def signal_id(signal: dict[str, Any]) -> str:
    return f"{signal['mint']}:{int(signal['threshold_decision_ts_ms'])}:{float(signal['score']):.9f}"


def signal_ok(signal: dict[str, Any]) -> bool:
    return (
        signal.get("cohort") == COHORT
        and signal.get("event") == "ranker_v2_primary_signal"
        and signal.get("passes_threshold") is True
        and abs(float(signal.get("score_threshold", THRESHOLD)) - THRESHOLD) <= 0.0
    )


def capsule_mint(row: dict[str, Any]) -> str | None:
    return (row.get("candidate") or {}).get("mint") or (row.get("raw_record") or {}).get("mint")


def capsule_ok(row: dict[str, Any]) -> bool:
    return bool(
        capsule_mint(row)
        and (row.get("execution") or {}).get("expected_tokens_out_raw")
        and (row.get("extra_decision_fields") or {}).get("bondingCurve")
        and (row.get("extra_decision_fields") or {}).get("sourceIxAccounts")
    )


def capsule_rank(row: dict[str, Any]) -> tuple[int, int, int]:
    candidate = row.get("candidate") or {}
    raw = row.get("raw_record") or {}
    extra = row.get("extra_decision_fields") or {}
    token_program = candidate.get("token_program") or raw.get("tokenProgram") or candidate.get("tokenProgram")
    accounts = extra.get("sourceIxAccounts") or []
    source_data = candidate.get("source_ix_data_base64") or raw.get("sourceIxDataBase64")
    observed = int((row.get("canonical_state") or {}).get("observed_at_ms") or 0)
    side = raw.get("side")
    native_kind = raw.get("nativeKind")
    is_token2022 = token_program == "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
    sell_template_quality = 2 if is_token2022 and len(accounts) >= 26 else 1 if (not is_token2022 and len(accounts) >= 16) else 0
    buy_source_quality = 2 if side == "buy" and source_data and accounts else 1 if native_kind == "creation" else 0
    return buy_source_quality, sell_template_quality, observed


def parse_reserves_from_state_identity(row: dict[str, Any]) -> tuple[int, int] | None:
    state_identity = (row.get("canonical_state") or {}).get("state_identity")
    if not isinstance(state_identity, str):
        return None
    parts = state_identity.split(":")
    if len(parts) < 4 or parts[0] != "native-shredstream-replay":
        return None
    try:
        return int(parts[-2]), int(parts[-1])
    except ValueError:
        return None


def selected_event(signal: dict[str, Any], capsule: dict[str, Any]) -> dict[str, Any]:
    candidate = capsule.get("candidate") or {}
    raw = capsule.get("raw_record") or {}
    canonical = capsule.get("canonical_state") or {}
    execution = capsule.get("execution") or {}
    extra = capsule.get("extra_decision_fields") or {}
    reserves = parse_reserves_from_state_identity(capsule)
    if reserves is None:
        raise ValueError("capsule missing parseable native reserve state_identity")
    virtual_sol_reserves, virtual_token_reserves = reserves
    raw_features = signal.get("raw_features") or {}
    decision_ms = int(signal["threshold_decision_ts_ms"])
    key = f"ranker-v2-live-edge-capture-v1:{signal_id(signal)}"
    source_signature = (
        capsule.get("transaction_signature")
        or raw.get("signature")
        or f"ranker-v2-source:{signal['mint']}"
    )
    token_program_id = candidate.get("token_program") or raw.get("tokenProgram")
    observed_ms = int(canonical.get("observed_at_ms") or signal.get("event_gated_state_ts_ms") or decision_ms)
    return {
        "schemaVersion": "two-cq-continuous-shadow-event-1",
        "event": "candidate_selected",
        "at": iso_ms(now_ms()),
        "atMs": now_ms(),
        "entryAtMs": decision_ms,
        "lane": LANE,
        "policy": POLICY,
        "key": key,
        "mint": signal["mint"],
        "sourceSignature": source_signature,
        "entryMarketCapUsd": float(signal.get("mc_usd") or canonical.get("market_cap_usd")),
        "mayhemRetryPromoted": False,
        "isWeakMayhemRecovered": False,
        "mayhemBlockReason": "ranker_v2_not_mayhem_candidate",
        "ranker": {
            "cohort": signal.get("cohort"),
            "signalId": signal_id(signal),
            "score": float(signal["score"]),
            "threshold": THRESHOLD,
            "state": signal.get("state"),
            "historicalStateAlias": signal.get("historical_state_alias"),
            "stateTsMs": signal.get("event_gated_state_ts_ms"),
            "decisionTsMs": decision_ms,
            "sourceRowId": signal.get("source_row_id"),
            "selectionSemantics": signal.get("selection_semantics"),
        },
        "directEntryQuote": {
            "status": "ok",
            "solInLamports": SOL_IN_LAMPORTS,
            "tokensOutRaw": int(execution.get("expected_tokens_out_raw")),
            "minTokensOutRaw": int(execution.get("min_tokens_out_raw") or 1),
            "slippageBps": int(execution.get("slippage_bps") or 500),
            "curveSnapshot": {
                "pda": extra.get("bondingCurve"),
                "bondingCurve": extra.get("bondingCurve"),
                "creator": extra.get("creator") or "source-template-only",
                "tokenProgramId": token_program_id,
                "quoteMint": WRAPPED_SOL_MINT,
                "virtualSolReserves": virtual_sol_reserves,
                "virtualTokenReserves": virtual_token_reserves,
                "slot": int(canonical.get("source_slot") or capsule.get("source_slot") or 0),
                "observedAtMs": observed_ms,
            },
        },
        "features": {
            "mint": signal["mint"],
            "key": key,
            "sourceSignature": source_signature,
            "sourceWallet": "ranker-v2-frozen-shadow-v2",
            "wallet": "ranker-v2-frozen-shadow-v2",
            "observedAtMs": decision_ms,
            "marketCapUsd": float(signal.get("mc_usd") or canonical.get("market_cap_usd")),
            "sourceIxAccounts": extra.get("sourceIxAccounts"),
            "sourceIxDataBase64": candidate.get("source_ix_data_base64") or raw.get("sourceIxDataBase64"),
            "tokenProgramId": token_program_id,
            "tokenProgram": token_program_id,
            "buyCount100ms": int(raw_features.get("w100.buy_count") or 0),
            "firstBuyerQuoteShareBps": int(raw_features.get("first_buyer_quote_share_bps") or 0),
            "rankerScore": float(signal["score"]),
            "rankerThreshold": THRESHOLD,
            "rankerState": signal.get("state"),
            "rankerSignalId": signal_id(signal),
            "sourceSlot": int(canonical.get("source_slot") or capsule.get("source_slot") or 0),
            "curveObservedAtMs": observed_ms,
            "virtualSolReserves": virtual_sol_reserves,
            "virtualTokenReserves": virtual_token_reserves,
        },
    }


def prewarm_event(capsule: dict[str, Any]) -> dict[str, Any] | None:
    mint = capsule_mint(capsule)
    extra = capsule.get("extra_decision_fields") or {}
    canonical = capsule.get("canonical_state") or {}
    reserves = parse_reserves_from_state_identity(capsule)
    bonding_curve = extra.get("bondingCurve")
    if not mint or not bonding_curve:
        return None
    if reserves is None:
        virtual_sol_reserves = None
        virtual_token_reserves = None
    else:
        virtual_sol_reserves, virtual_token_reserves = reserves
    observed_ms = int(
        canonical.get("observed_at_ms")
        or capsule.get("local_receive_ts_ms")
        or now_ms()
    )
    return {
        "schemaVersion": "ranker-v2-curve-prewarm-1",
        "event": "curve_prewarm",
        "at": iso_ms(now_ms()),
        "atMs": now_ms(),
        "lane": LANE,
        "policy": POLICY,
        "source": "ranker-v2-live-edge-capture-adapter-capsule-prewarm",
        "mint": mint,
        "bondingCurve": bonding_curve,
        "sourceSlot": int(canonical.get("source_slot") or capsule.get("source_slot") or 0),
        "slot": int(canonical.get("source_slot") or capsule.get("source_slot") or 0),
        "observedAtMs": observed_ms,
        "curveObservedAtMs": observed_ms,
        "virtualSolReserves": virtual_sol_reserves,
        "virtualTokenReserves": virtual_token_reserves,
        "transactionSignature": capsule.get("transaction_signature")
        or (capsule.get("raw_record") or {}).get("signature"),
    }


def initialize_state(state_path: Path) -> dict[str, Any]:
    if state_path.exists():
        return load_json(state_path)
    signals = [s for s in read_gzip_jsonl(OUT / "signals.jsonl.gz") if signal_ok(s) and s.get("mint")]
    state = {
        "armed": True,
        "emitted": False,
        "emittedCount": 0,
        "prewarmedMints": [],
        "startedAtUtc": utc_now(),
        "processedSignalIds": sorted({signal_id(s) for s in signals}),
        "sourceOffset": SOURCE_INPUT.stat().st_size,
        "futureOnlyInitializedFromSignalCount": len({signal_id(s) for s in signals}),
    }
    write_json(state_path, state)
    return state


def run(args: argparse.Namespace) -> int:
    output = Path(args.output)
    selected_path = output / "selected-events.jsonl"
    shadow_path = output / "shadow-twins.jsonl"
    service_events = output / "adapter-events.jsonl"
    state_path = output / "state.json"
    output.mkdir(parents=True, exist_ok=True)
    selected_path.touch(exist_ok=True)
    state = initialize_state(state_path)
    processed = set(state.get("processedSignalIds") or [])
    prewarmed = set(state.get("prewarmedMints") or [])
    emitted_count = int(state.get("emittedCount") or 0)
    capsule_cache: dict[str, dict[str, Any]] = {}
    ranks: dict[str, tuple[int, int, int]] = {}
    direct = DirectHandoffClient(args.direct_host, args.direct_port, args.direct_handoff)
    append_jsonl(service_events, {
        "kind": "rankerV2LiveEdgeCaptureAdapterStarted",
        "atUtc": utc_now(),
        "armed": state.get("armed"),
        "emitted": state.get("emitted"),
        "sourceOffset": state.get("sourceOffset"),
        "processedSignals": len(processed),
        "prewarmCapsules": args.prewarm_capsules,
        "maxEmissions": args.max_emissions,
        "noSubmitValidation": args.no_submit_validation,
        "directHandoff": args.direct_handoff,
        "directHost": args.direct_host if args.direct_handoff else None,
        "directPort": args.direct_port if args.direct_handoff else None,
    })
    try:
      while state.get("armed") and emitted_count < args.max_emissions:
        with SOURCE_INPUT.open("r", encoding="utf-8", errors="replace") as handle:
            offset = int(state.get("sourceOffset") or 0)
            size = SOURCE_INPUT.stat().st_size
            if offset > size:
                offset = size
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
                state["sourceOffset"] = handle.tell()
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                mint = capsule_mint(row)
                if not mint or not capsule_ok(row):
                    continue
                rank = capsule_rank(row)
                if rank > ranks.get(mint, (-1, -1, -1)):
                    ranks[mint] = rank
                    capsule_cache[mint] = row
                    if args.prewarm_capsules and mint not in prewarmed:
                        event = prewarm_event(row)
                        if event is not None:
                            try:
                                handoff = direct.send(event)
                            except OSError as exc:
                                append_jsonl(service_events, {
                                    "kind": "rankerV2LiveEdgeCaptureAdapterDirectHandoffError",
                                    "atUtc": utc_now(),
                                    "mint": mint,
                                    "bondingCurve": event.get("bondingCurve"),
                                    "phase": "prewarm",
                                    "error": str(exc),
                                })
                                continue
                            append_jsonl(selected_path, event)
                            append_jsonl(service_events, {
                                "kind": "rankerV2LiveEdgeCaptureAdapterPrewarmEmitted",
                                "atUtc": utc_now(),
                                "mint": mint,
                                "bondingCurve": event.get("bondingCurve"),
                                "sourceSlot": event.get("sourceSlot"),
                                "observedAtMs": event.get("observedAtMs"),
                                "directHandoff": handoff,
                            })
                            prewarmed.add(mint)

        for signal in read_gzip_jsonl(OUT / "signals.jsonl.gz"):
            if not signal_ok(signal) or not signal.get("mint"):
                continue
            sid = signal_id(signal)
            if sid in processed:
                continue
            mint = signal["mint"]
            capsule = capsule_cache.get(mint)
            if not capsule:
                append_jsonl(service_events, {"kind": "waitingForExecutionCapsule", "atUtc": utc_now(), "mint": mint, "signalId": sid})
                continue
            event = selected_event(signal, capsule)
            try:
                handoff = direct.send(event)
            except OSError as exc:
                append_jsonl(service_events, {
                    "kind": "rankerV2LiveEdgeCaptureAdapterDirectHandoffError",
                    "atUtc": utc_now(),
                    "mint": mint,
                    "signalId": sid,
                    "phase": "selected",
                    "error": str(exc),
                })
                continue
            append_jsonl(shadow_path, {
                "kind": "rankerV2LiveEdgeCaptureShadowTwin",
                "atUtc": utc_now(),
                "signal": signal,
                "selectedEvent": event,
                "capsuleIdentity": {
                    "transactionSignature": capsule.get("transaction_signature") or (capsule.get("raw_record") or {}).get("signature"),
                    "sourceSlot": capsule.get("source_slot") or (capsule.get("canonical_state") or {}).get("source_slot"),
                    "sourceTransactionIndex": capsule.get("source_transaction_index"),
                    "stateIdentity": (capsule.get("canonical_state") or {}).get("state_identity"),
                },
            })
            append_jsonl(selected_path, event)
            processed.add(sid)
            emitted_count += 1
            state.update({
                "armed": emitted_count < args.max_emissions,
                "emitted": emitted_count >= args.max_emissions,
                "emittedAtUtc": utc_now(),
                "emittedCount": emitted_count,
                "emittedMint": mint,
                "emittedSignalId": sid,
                "processedSignalIds": sorted(processed),
                "prewarmedMints": sorted(prewarmed),
                "sourceOffset": state.get("sourceOffset"),
            })
            write_json(state_path, state)
            if emitted_count >= args.max_emissions:
                append_jsonl(service_events, {"kind": "rankerV2LiveEdgeCaptureAdapterDisarmedAfterMaxEmissions", "atUtc": utc_now(), "mint": mint, "signalId": sid, "emittedCount": emitted_count, "directHandoff": handoff})
                return 0
            append_jsonl(service_events, {"kind": "rankerV2LiveEdgeCaptureAdapterSelectedEmission", "atUtc": utc_now(), "mint": mint, "signalId": sid, "emittedCount": emitted_count, "directHandoff": handoff})

        state["processedSignalIds"] = sorted(processed)
        state["prewarmedMints"] = sorted(prewarmed)
        state["emittedCount"] = emitted_count
        write_json(state_path, state)
        write_json(output / "adapter-health.json", {
            "updatedAtUtc": utc_now(),
            "armed": state.get("armed"),
            "emitted": state.get("emitted"),
            "processedSignals": len(processed),
            "prewarmedMints": len(prewarmed),
            "emittedCount": emitted_count,
            "maxEmissions": args.max_emissions,
            "cachedCapsules": len(capsule_cache),
            "sourceOffset": state.get("sourceOffset"),
        })
        time.sleep(args.poll_sec)
    finally:
        direct.close()
    append_jsonl(service_events, {"kind": "rankerV2LiveEdgeCaptureAdapterExitAlreadyDisarmed", "atUtc": utc_now()})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(DEFAULT_LIVE_OUT))
    parser.add_argument("--poll-sec", type=float, default=0.25)
    parser.add_argument("--max-emissions", type=int, default=1)
    parser.add_argument("--prewarm-capsules", action="store_true")
    parser.add_argument("--no-submit-validation", action="store_true")
    parser.add_argument("--direct-handoff", action="store_true")
    parser.add_argument("--direct-host", default="127.0.0.1")
    parser.add_argument("--direct-port", type=int, default=19131)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
