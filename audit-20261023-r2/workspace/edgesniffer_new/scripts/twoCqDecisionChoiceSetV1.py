#!/usr/bin/env python3
"""Materialize outcome-blind contemporaneous choice sets for 2CQ development."""

from __future__ import annotations

import argparse
import bisect
import collections
import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
from typing import Any, Iterable

import twoCqCausalEntryTimingFingerprintV1 as timing
import twoCqDecisionSystemDecompositionV1 as decision
import twoCqPostFixEndToEndCollectionDiagnosticV1 as tier_a
from exactCurveEconomicsV1 import decode_trade_events
from reconcilePumpExactEvidenceLedgerV1 import iter_frames


SCHEMA = "2CQ_DECISION_SYSTEM_DECOMPOSITION_V1_CROSS_SECTIONAL"


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: pathlib.Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")
    os.replace(temporary, path)


def write_jsonl_gz(path: pathlib.Path, rows: Iterable[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as stream:
            for row in rows:
                stream.write(canonical(row) + b"\n")
    os.replace(temporary, path)


def read_jsonl_gz(path: pathlib.Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def receive_hours(start_ns: int, end_ns: int) -> list[str]:
    start = dt.datetime.fromtimestamp(start_ns / 1e9, dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    end = dt.datetime.fromtimestamp(end_ns / 1e9, dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    rows = []
    current = start
    while current <= end:
        rows.append(current.strftime("%Y/%m/%d/%H"))
        current += dt.timedelta(hours=1)
    return rows


def finalized_evidence_paths(
    root: pathlib.Path, hours: list[str]
) -> tuple[list[pathlib.Path], dict[str, Any]]:
    paths: set[pathlib.Path] = set()
    missing = []
    globally_inexact: dict[str, Any] = {}
    for hour in hours:
        status_path = root / "partitions" / hour / "reconciliation-status.json"
        if not status_path.exists():
            missing.append(hour)
            continue
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if status.get("temporalHourClosed") is not True or status.get("inputStableDuringRun") is not True:
            missing.append(hour)
            continue
        if status.get("exactReplayComplete") is not True:
            coverage = status.get("coverage") or {}
            globally_inexact[hour] = {
                "missingFinality": int(coverage.get("missingFinality") or 0),
                "missingRawTx": int(coverage.get("missingRawTx") or 0),
                "missingCanonicalOrdering": int(coverage.get("missingCanonicalOrdering") or 0),
                "decodeFailures": int(coverage.get("decodeFailures") or 0),
                "scope": "all captured Solana transactions; Pump-only denominator scanned separately",
            }
        for item in status.get("inputEvidence") or []:
            path = root / str(item["path"])
            if not path.exists():
                raise RuntimeError(f"RECONCILED_EVIDENCE_PATH_MISSING:{path}")
            paths.add(path)
    if missing:
        raise RuntimeError("FINALIZED_PARTITIONS_NOT_READY:" + ",".join(missing))
    return sorted(paths), globally_inexact


def inside_decision_window(received_ns: int, boundaries: list[int]) -> bool:
    position = bisect.bisect_left(boundaries, received_ns)
    return position < len(boundaries) and boundaries[position] - received_ns <= decision.MAX_AGE_MS * 1_000_000


def scan_finalized_universe(
    evidence_paths: list[pathlib.Path], compact_path: pathlib.Path, boundaries: list[int],
) -> tuple[dict[str, list[decision.CausalEvent]], dict[str, int], dict[str, Any]]:
    transaction_mints: dict[str, tuple[str, ...]] = {}
    creates: dict[str, tuple[tuple[int, int, str], str]] = {}
    signatures: set[str] = set()
    decode_failures = 0
    finalized_successful = 0
    # Pass one indexes the canonical denominator without retaining transaction payloads.
    for number, path in enumerate(evidence_paths, 1):
        for metadata, raw in iter_frames(path, path.suffix == ".partial"):
            if metadata.get("kind") != "rpc_finalized_transaction" or metadata.get("successful") is not True:
                continue
            kinds = set(metadata.get("pumpInstructionKinds") or [])
            if not kinds.intersection(decision.TRADE_KINDS | {"CREATE", "CREATE_V2"}):
                continue
            signature = str(metadata.get("signature") or "")
            if not signature:
                continue
            finalized_successful += 1
            signatures.add(signature)
            slot = int(metadata.get("slot") or 0)
            tx_index = int(metadata.get("transactionIndex") if metadata.get("transactionIndex") is not None else -1)
            mints = tuple(str(mint) for mint in metadata.get("discoveredMints") or [])
            transaction_mints[signature] = mints
            if kinds.intersection({"CREATE", "CREATE_V2"}):
                for mint in mints:
                    candidate = ((slot, tx_index, signature), signature)
                    if mint not in creates or candidate[0] < creates[mint][0]:
                        creates[mint] = candidate
        print(json.dumps({"phase": "canonical-index", "path": str(path), "index": number, "total": len(evidence_paths)}), flush=True)

    signatures.update(signature for _, signature in creates.values())
    all_arrivals = decision.targeted_compact_arrivals(
        compact_path, {tier_a.signature_digest(signature) for signature in signatures}
    )
    create_ns: dict[str, int] = {}
    for mint, (_, signature) in creates.items():
        arrival = all_arrivals.get(tier_a.signature_digest(signature))
        if arrival and inside_decision_window(int(arrival[0]), boundaries):
            create_ns[mint] = int(arrival[0])

    relevant_signatures = {
        signature for signature, mints in transaction_mints.items()
        if any(mint in create_ns for mint in mints)
        and (arrival := all_arrivals.get(tier_a.signature_digest(signature))) is not None
        and inside_decision_window(int(arrival[0]), boundaries)
    }
    events: dict[str, dict[tuple[str, int], decision.CausalEvent]] = collections.defaultdict(dict)
    missing_arrivals = 0
    decoded_trade_events = 0
    # Pass two decodes only signatures that can belong to a frozen decision window.
    for number, path in enumerate(evidence_paths, 1):
        for metadata, raw in iter_frames(path, path.suffix == ".partial"):
            signature = str(metadata.get("signature") or "")
            if signature not in relevant_signatures or metadata.get("kind") != "rpc_finalized_transaction":
                continue
            try:
                tx = json.loads(raw).get("result")
                if not tx or (tx.get("meta") or {}).get("err") is not None:
                    continue
                arrival = all_arrivals.get(tier_a.signature_digest(signature))
                if arrival is None:
                    missing_arrivals += 1
                    continue
                received_ns = int(arrival[0])
                slot = int(metadata.get("slot") or tx.get("slot") or 0)
                tx_index = int(metadata.get("transactionIndex") if metadata.get("transactionIndex") is not None else -1)
                for instruction_index, event in enumerate(decode_trade_events((tx.get("meta") or {}).get("logMessages") or [])):
                    if event.mint not in create_ns:
                        continue
                    row = decision.CausalEvent(
                        mint=event.mint, signature=signature, slot=slot,
                        transaction_index=tx_index, instruction_index=instruction_index,
                        received_ns=received_ns, is_buy=bool(event.is_buy), user=str(event.user),
                        creator=str(event.creator), quote_lamports=int(event.immediate_quote_amount),
                        token_raw=int(event.token_amount), pre_state=event.pre_state, post_state=event.post_state,
                    )
                    events[event.mint][(signature, instruction_index)] = row
                    decoded_trade_events += 1
            except Exception:
                decode_failures += 1
        print(json.dumps({"phase": "window-decode", "path": str(path), "index": number, "total": len(evidence_paths)}), flush=True)
    ordered = {mint: sorted(rows.values(), key=lambda row: (row.received_ns, row.order)) for mint, rows in events.items()}
    return ordered, create_ns, {
        "evidencePaths": len(evidence_paths),
        "finalizedSuccessfulPumpTransactions": finalized_successful,
        "canonicalPumpSignatures": len(signatures),
        "windowRelevantSignatures": len(relevant_signatures),
        "decodedTradeEvents": decoded_trade_events,
        "mintsWithEvents": len(ordered),
        "mintsWithCanonicalRealtimeCreate": len(create_ns),
        "tradeEventsWithoutAllenHarkArrival": missing_arrivals,
        "decodeFailures": decode_failures,
    }


def teacher_exposure_raw(events: list[decision.CausalEvent]) -> int:
    return sum((row.token_raw if row.is_buy else -row.token_raw) for row in events if row.user == decision.WALLET)


def build_choice_examples(
    entry_tape: list[dict[str, Any]],
    events: dict[str, list[decision.CausalEvent]],
    create_ns: dict[str, int],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    creation_order = sorted((timestamp, mint) for mint, timestamp in create_ns.items())
    creation_times = [row[0] for row in creation_order]
    universes: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    missing_chosen = collections.Counter()
    candidate_counts = collections.Counter()
    for entry in entry_tape:
        boundary = int(entry["decisionStateReceivedAtUnixNs"])
        boundary_order = (
            int(entry["decisionStateSlot"]), int(entry["decisionStateTransactionIndex"]),
            2**31 - 1, str(entry["decisionStateSignature"]),
        )
        left = bisect.bisect_left(creation_times, boundary - decision.MAX_AGE_MS * 1_000_000)
        right = bisect.bisect_right(creation_times, boundary)
        choices = []
        for _, mint in creation_order[left:right]:
            sequence = events.get(mint) or []
            receipts = [row.received_ns for row in sequence]
            stop = bisect.bisect_right(receipts, boundary)
            prior = [
                row for row in sequence[:stop]
                if row.received_ns < boundary or row.order <= boundary_order
            ]
            external = [row for row in prior if row.user != decision.WALLET]
            if not prior or not external or teacher_exposure_raw(prior) > 1:
                continue
            last = prior[-1]
            if int(last.post_state.real_token) <= 0:
                continue
            last_external = external[-1]
            features = decision.state_features(
                last.post_state, boundary, mint, last.creator, prior, create_ns[mint]
            )
            choices.append({
                "label": mint == entry["mint"],
                "mint": mint,
                "lastExternalAgeMs": (boundary - last_external.received_ns) / 1_000_000,
                "features": features,
            })
        chosen = [row for row in choices if row["label"]]
        if len(chosen) != 1:
            missing_chosen["LAUNCHED_120S"] += 1
            continue
        base = {
            "episodeId": entry["signature"], "eligibleOrdinal": entry["eligibleOrdinal"],
            "mint": entry["mint"], "controlN": len(choices) - 1, "states": choices,
        }
        if base["controlN"] > 0:
            universes["LAUNCHED_120S"].append(base)
            candidate_counts["LAUNCHED_120S"] += len(choices)
        for window in decision.CHOICE_WINDOWS_MS:
            name = f"ACTIVE_{window}MS"
            active = [row for row in choices if row["lastExternalAgeMs"] <= window]
            if sum(row["label"] for row in active) != 1:
                missing_chosen[name] += 1
                continue
            if len(active) > 1:
                universes[name].append(base | {"controlN": len(active) - 1, "states": active})
                candidate_counts[name] += len(active)
    return dict(universes), {
        "missingChosenByUniverse": dict(sorted(missing_chosen.items())),
        "candidateRowsByUniverse": dict(sorted(candidate_counts.items())),
    }


def fit_universe(examples: list[dict[str, Any]]) -> dict[str, Any]:
    train, assessment, dropped = decision.internal_split(examples)
    return {
        "examples": len(examples), "trainN": len(train), "assessmentN": len(assessment),
        "crossBoundaryRepeatDropped": dropped,
        "meanChoiceSetN": sum(len(row["states"]) for row in examples) / len(examples) if examples else None,
        "models": decision.model_suite(train, assessment) if train and assessment else {},
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    parent_contract = json.loads((args.parent / "contract.json").read_text(encoding="utf-8"))
    if parent_contract.get("validationArtifactAcceptedAsInput") is not False:
        raise RuntimeError("PARENT_VALIDATION_BOUNDARY_INVALID")
    entry_tape = read_jsonl_gz(args.parent / "entry-decision-tape.jsonl.gz")
    if len(entry_tape) != decision.DEVELOPMENT_N:
        raise RuntimeError("ENTRY_TAPE_NOT_N350")
    boundaries = sorted(int(row["decisionStateReceivedAtUnixNs"]) for row in entry_tape)
    window_start_ns = boundaries[0] - decision.MAX_AGE_MS * 1_000_000
    end_ns = boundaries[-1]
    hours = receive_hours(window_start_ns, end_ns)
    paths, globally_inexact = finalized_evidence_paths(args.exact_root, hours)
    events, create_ns, evidence = scan_finalized_universe(paths, args.compact_arrivals, boundaries)
    universes, choice_summary = build_choice_examples(entry_tape, events, create_ns)
    for name, rows in universes.items():
        write_jsonl_gz(args.parent / f"choice-set-{name.lower()}.jsonl.gz", rows)
    models = {name: fit_universe(rows) for name, rows in sorted(universes.items())}
    report = {
        "schema": SCHEMA,
        "generatedAtUtc": decision.now_utc(),
        "parentContractSha256": sha256_file(args.parent / "contract.json"),
        "developmentN": len(entry_tape),
        "receiveHours": hours,
        "evidence": evidence,
        "globalReconciliationExceptions": globally_inexact,
        "choiceSet": choice_summary,
        "universes": models,
        "integrity": {
            "validationPathAccepted": False, "validationRowsRead": 0,
            "outcomesRead": 0, "submissions": 0,
            "strictlyPreTeacherBoundary": True,
        },
        "verdict": "2CQ_CROSS_SECTIONAL_CHOICE_SETS_MATERIALIZED_OUTCOME_BLIND",
    }
    atomic_json(args.parent / "cross-sectional-report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=pathlib.Path, required=True)
    parser.add_argument("--exact-root", type=pathlib.Path, required=True)
    parser.add_argument("--compact-arrivals", type=pathlib.Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
