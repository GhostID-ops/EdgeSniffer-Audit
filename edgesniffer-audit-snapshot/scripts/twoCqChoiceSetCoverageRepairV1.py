#!/usr/bin/env python3
"""Repair outcome-blind 2CQ choice-set coverage from existing canonical evidence."""

from __future__ import annotations

import argparse
import bisect
import collections
import gzip
import json
import pathlib
from typing import Any

import twoCqDecisionChoiceSetV1 as legacy
import twoCqDecisionSystemDecompositionV1 as decision
import twoCqPostFixEndToEndCollectionDiagnosticV1 as tier_a
from exactCurveEconomicsV1 import base58_decode, decode_trade_events, resolved_account_keys
from reconcilePumpExactEvidenceLedgerV1 import iter_frames


SCHEMA = "2CQ_CHOICE_SET_COVERAGE_REPAIR_V2"
CREATE_DISCRIMINATORS = {
    bytes((24, 30, 200, 40, 5, 28, 7, 119)),
    bytes((214, 144, 76, 236, 95, 139, 49, 180)),
}


def compiled_instructions(tx: dict[str, Any]) -> list[dict[str, Any]]:
    message = ((tx.get("transaction") or {}).get("message") or {})
    rows = list(message.get("instructions") or [])
    for group in (tx.get("meta") or {}).get("innerInstructions") or []:
        rows.extend(group.get("instructions") or [])
    return rows


def canonical_create_mints(tx: dict[str, Any]) -> set[str]:
    """Resolve Create/CreateV2 mints from instruction accounts, including CPI/v0."""
    keys = resolved_account_keys(tx)
    result: set[str] = set()
    for instruction in compiled_instructions(tx):
        program_index = instruction.get("programIdIndex")
        if program_index is None or int(program_index) >= len(keys):
            continue
        if keys[int(program_index)] != tier_a.PUMP_PROGRAM:
            continue
        encoded = instruction.get("data")
        accounts = instruction.get("accounts") or []
        if not encoded or not accounts:
            continue
        try:
            payload = base58_decode(str(encoded))
        except Exception:
            continue
        if payload[:8] not in CREATE_DISCRIMINATORS:
            continue
        mint_index = int(accounts[0])
        if mint_index < len(keys):
            result.add(str(keys[mint_index]))
    return result


def decode_causal_events(
    tx: dict[str, Any], signature: str, received_ns: int, slot: int, tx_index: int,
) -> tuple[list[decision.CausalEvent], str | None]:
    decoded = decode_trade_events((tx.get("meta") or {}).get("logMessages") or [])
    rows: list[decision.CausalEvent] = []
    by_mint: dict[str, list[Any]] = collections.defaultdict(list)
    for event in decoded:
        by_mint[event.mint].append(event)
    for mint, events in by_mint.items():
        try:
            sources = sorted(
                tier_a.extract_pump_source_observations(
                    tx, signature, mint, received_ns // 1_000_000, "allenhark"
                ),
                key=lambda row: row.instruction_index,
            )
        except Exception:
            return [], "SOURCE_DECODE_FAILED"
        if len(events) != len(sources):
            return [], "EVENT_SOURCE_CARDINALITY_MISMATCH"
        for event, source in zip(events, sources):
            rows.append(decision.CausalEvent(
                mint=mint,
                signature=signature,
                slot=slot,
                transaction_index=tx_index,
                instruction_index=int(source.instruction_index),
                received_ns=received_ns,
                is_buy=bool(event.is_buy),
                user=str(event.user),
                creator=str(event.creator),
                quote_lamports=int(event.immediate_quote_amount),
                token_raw=int(event.token_amount),
                pre_state=event.pre_state,
                post_state=event.post_state,
            ))
    return rows, None


def scan_repaired_universe(
    evidence_paths: list[pathlib.Path], compact_path: pathlib.Path, boundaries: list[int],
    development: list[dict[str, Any]],
) -> tuple[dict[str, list[decision.CausalEvent]], dict[str, int], dict[str, Any]]:
    finalized_signatures: set[str] = set()
    canonical_creates: dict[str, tuple[tuple[int, int, str], str]] = {}
    metadata_creates: dict[str, tuple[tuple[int, int, str], str]] = {}
    selected_signatures = {
        str(row["signature"]) for row in development
    } | {
        str((row.get("tierAEvidence") or {}).get("createSignature") or "")
        for row in development
    }
    selected_metadata: dict[str, dict[str, Any]] = {}
    finalized_successful = 0
    create_decode_failures = 0

    for number, path in enumerate(evidence_paths, 1):
        for metadata, raw in iter_frames(path, path.suffix == ".partial"):
            if metadata.get("kind") != "rpc_finalized_transaction" or metadata.get("successful") is not True:
                continue
            signature = str(metadata.get("signature") or "")
            if not signature:
                continue
            kinds = set(metadata.get("pumpInstructionKinds") or [])
            finalized_successful += 1
            finalized_signatures.add(signature)
            slot = int(metadata.get("slot") or 0)
            tx_index = int(metadata.get("transactionIndex") if metadata.get("transactionIndex") is not None else -1)
            if signature in selected_signatures:
                selected_metadata[signature] = {
                    "kinds": sorted(kinds),
                    "discoveredMints": list(metadata.get("discoveredMints") or []),
                    "slot": slot,
                    "transactionIndex": tx_index,
                }
            if kinds.intersection({"CREATE", "CREATE_V2"}):
                order = (slot, tx_index, signature)
                for mint in metadata.get("discoveredMints") or []:
                    mint = str(mint)
                    candidate = (order, signature)
                    if mint not in metadata_creates or order < metadata_creates[mint][0]:
                        metadata_creates[mint] = candidate
            # Metadata classification is not authoritative for CPI/wrapped Pump
            # activity. Decode every successful finalized transaction first.
            try:
                tx = json.loads(raw).get("result")
                if tx and (tx.get("meta") or {}).get("err") is None:
                    order = (slot, tx_index, signature)
                    for mint in canonical_create_mints(tx):
                        candidate = (order, signature)
                        if mint not in canonical_creates or order < canonical_creates[mint][0]:
                            canonical_creates[mint] = candidate
            except Exception:
                create_decode_failures += 1
        print(json.dumps({"phase": "canonical-index-v2", "index": number, "total": len(evidence_paths)}), flush=True)

    # The canonical instruction-account index is authoritative. V3 hints are used
    # only as a fail-closed audit cross-check, never to invent a control mint.
    hint_mismatch = []
    for row in development:
        mint = str(row["mint"])
        hinted = str((row.get("tierAEvidence") or {}).get("createSignature") or "")
        observed = canonical_creates.get(mint)
        if not observed or observed[1] != hinted:
            hint_mismatch.append({
                "eligibleOrdinal": int(row["eligibleOrdinal"]),
                "mint": mint,
                "v3CreateSignature": hinted,
                "canonicalCreateSignature": observed[1] if observed else None,
            })

    arrivals = decision.targeted_compact_arrivals(
        compact_path, {tier_a.signature_digest(signature) for signature in finalized_signatures}
    )
    create_ns: dict[str, int] = {}
    for mint, (_, signature) in canonical_creates.items():
        arrival = arrivals.get(tier_a.signature_digest(signature))
        if arrival and legacy.inside_decision_window(int(arrival[0]), boundaries):
            create_ns[mint] = int(arrival[0])

    relevant_signatures = {
        signature for signature in finalized_signatures
        if (arrival := arrivals.get(tier_a.signature_digest(signature))) is not None
        and legacy.inside_decision_window(int(arrival[0]), boundaries)
    }
    events: dict[str, dict[tuple[str, int], decision.CausalEvent]] = collections.defaultdict(dict)
    decode_failures = collections.Counter()
    decoded_transactions = 0
    decoded_trade_events = 0
    selected_events_seen: set[str] = set()
    for number, path in enumerate(evidence_paths, 1):
        for metadata, raw in iter_frames(path, path.suffix == ".partial"):
            signature = str(metadata.get("signature") or "")
            if signature not in relevant_signatures or metadata.get("kind") != "rpc_finalized_transaction":
                continue
            try:
                tx = json.loads(raw).get("result")
                if not tx or (tx.get("meta") or {}).get("err") is not None:
                    continue
                received_ns = int(arrivals[tier_a.signature_digest(signature)][0])
                slot = int(metadata.get("slot") or tx.get("slot") or 0)
                tx_index = int(metadata.get("transactionIndex") if metadata.get("transactionIndex") is not None else -1)
                decoded, failure = decode_causal_events(tx, signature, received_ns, slot, tx_index)
                if failure:
                    decode_failures[failure] += 1
                    continue
                decoded_transactions += 1
                for row in decoded:
                    if row.mint not in create_ns:
                        continue
                    events[row.mint][(signature, row.instruction_index)] = row
                    decoded_trade_events += 1
                    if signature in selected_signatures:
                        selected_events_seen.add(signature)
            except Exception:
                decode_failures["TRANSACTION_DECODE_EXCEPTION"] += 1
        print(json.dumps({"phase": "window-decode-v2", "index": number, "total": len(evidence_paths)}), flush=True)

    ordered = {
        mint: sorted(rows.values(), key=lambda row: (row.received_ns, row.order))
        for mint, rows in events.items()
    }
    return ordered, create_ns, {
        "evidencePaths": len(evidence_paths),
        "finalizedSuccessfulPumpTransactions": finalized_successful,
        "finalizedSuccessfulSignatures": len(finalized_signatures),
        "successfulSignaturesInDecisionWindows": len(relevant_signatures),
        "metadataCreateMints": len(metadata_creates),
        "canonicalInstructionCreateMints": len(canonical_creates),
        "canonicalCreatesMissingFromMetadata": len(set(canonical_creates) - set(metadata_creates)),
        "createDecodeFailures": create_decode_failures,
        "mintsWithRealtimeCreateInWindow": len(create_ns),
        "decodedTransactions": decoded_transactions,
        "decodedTradeEvents": decoded_trade_events,
        "mintsWithEvents": len(ordered),
        "decodeFailures": dict(sorted(decode_failures.items())),
        "v3CanonicalCreateHintMismatches": hint_mismatch,
        "selectedDecisionSignaturesSeen": len(selected_events_seen),
        "selectedMetadata": selected_metadata,
    }


def build_repaired_examples(
    entry_tape: list[dict[str, Any]], events: dict[str, list[decision.CausalEvent]],
    create_ns: dict[str, int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    creation_order = sorted((timestamp, mint) for mint, timestamp in create_ns.items())
    creation_times = [row[0] for row in creation_order]
    examples: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []
    for entry in entry_tape:
        boundary = int(entry["decisionStateReceivedAtUnixNs"])
        boundary_order = (
            int(entry["decisionStateSlot"]), int(entry["decisionStateTransactionIndex"]),
            2**31 - 1, str(entry["decisionStateSignature"]),
        )
        mint = str(entry["mint"])
        reasons = []
        if mint not in create_ns:
            reasons.append("CANONICAL_REALTIME_CREATE_NOT_INDEXED")
        elif boundary - create_ns[mint] > decision.MAX_AGE_MS * 1_000_000:
            reasons.append("OUTSIDE_LAUNCHED_120S")
        selected_sequence = events.get(mint) or []
        if not any(row.signature == entry["decisionStateSignature"] for row in selected_sequence):
            reasons.append("SELECTED_DECISION_EVENT_NOT_DECODED")

        left = bisect.bisect_left(creation_times, boundary - decision.MAX_AGE_MS * 1_000_000)
        right = bisect.bisect_right(creation_times, boundary)
        choices = []
        for _, candidate_mint in creation_order[left:right]:
            sequence = events.get(candidate_mint) or []
            prior = [
                row for row in sequence
                if row.received_ns < boundary
                or (row.received_ns == boundary and row.order <= boundary_order)
            ]
            external = [row for row in prior if row.user != decision.WALLET]
            if not prior or not external or legacy.teacher_exposure_raw(prior) > 1:
                continue
            last = prior[-1]
            if int(last.post_state.real_token) <= 0:
                continue
            last_external = external[-1]
            choices.append({
                "label": candidate_mint == mint,
                "mint": candidate_mint,
                "lastExternalAgeMs": (boundary - last_external.received_ns) / 1_000_000,
                "features": decision.state_features(
                    last.post_state, boundary, candidate_mint, last.creator, prior,
                    create_ns[candidate_mint],
                ),
            })
        chosen = [row for row in choices if row["label"]]
        if len(chosen) != 1:
            if not reasons:
                reasons.append("SELECTED_STATE_NOT_EXECUTABLE_AT_BOUNDARY")
            omissions.append({
                "eligibleOrdinal": int(entry["eligibleOrdinal"]),
                "episodeId": entry["signature"],
                "mint": mint,
                "reasons": sorted(set(reasons)),
                "causalAgeMs": (boundary - int(entry["createReceivedAtUnixNs"])) / 1_000_000,
                "earlierExternalControlN": int(entry["earlierExternalControlN"]),
                "candidateN": len(choices),
            })
            continue
        if len(choices) <= 1:
            omissions.append({
                "eligibleOrdinal": int(entry["eligibleOrdinal"]),
                "episodeId": entry["signature"],
                "mint": mint,
                "reasons": ["NO_CONTEMPORANEOUS_ALTERNATIVE"],
                "causalAgeMs": (boundary - int(entry["createReceivedAtUnixNs"])) / 1_000_000,
                "earlierExternalControlN": int(entry["earlierExternalControlN"]),
                "candidateN": len(choices),
            })
            continue
        examples.append({
            "episodeId": entry["signature"],
            "eligibleOrdinal": int(entry["eligibleOrdinal"]),
            "mint": mint,
            "controlN": len(choices) - 1,
            "states": choices,
        })
    return examples, omissions


def coverage_verdict(recovered_n: int, regressed_n: int) -> str:
    if recovered_n > 0 and regressed_n == 0:
        return "2CQ_CHOICE_SET_COVERAGE_REPAIRED_OUTCOME_BLIND"
    return "2CQ_CHOICE_SET_EXISTING_EVIDENCE_COVERAGE_BLOCKER"


def run(args: argparse.Namespace) -> dict[str, Any]:
    args.output.mkdir(parents=True, exist_ok=True)
    entry_tape = legacy.read_jsonl_gz(args.parent / "entry-decision-tape.jsonl.gz")
    development = decision.load_development(args.development)
    if [int(row["eligibleOrdinal"]) for row in entry_tape] != list(range(1, 351)):
        raise RuntimeError("ENTRY_TAPE_NOT_FROZEN_DEVELOPMENT_N350")
    if any(row.get("split") != "DEVELOPMENT" for row in development):
        raise RuntimeError("VALIDATION_BOUNDARY_VIOLATION")
    boundaries = sorted(int(row["decisionStateReceivedAtUnixNs"]) for row in entry_tape)
    hours = legacy.receive_hours(
        boundaries[0] - decision.MAX_AGE_MS * 1_000_000, boundaries[-1]
    )
    paths, globally_inexact = legacy.finalized_evidence_paths(args.exact_root, hours)
    events, create_ns, evidence = scan_repaired_universe(
        paths, args.compact_arrivals, boundaries, development
    )
    examples, omissions = build_repaired_examples(entry_tape, events, create_ns)
    legacy.write_jsonl_gz(args.output / "choice-set-launched_120s.jsonl.gz", examples)
    legacy.write_jsonl_gz(args.output / "omissions.jsonl.gz", omissions)

    original = legacy.read_jsonl_gz(args.parent / "choice-set-launched_120s.jsonl.gz")
    original_ids = {row["episodeId"] for row in original}
    repaired_ids = {row["episodeId"] for row in examples}
    reasons = collections.Counter(
        reason for row in omissions for reason in row["reasons"]
    )
    recovered_n = len(repaired_ids - original_ids)
    regressed_n = len(original_ids - repaired_ids)
    verdict = coverage_verdict(recovered_n, regressed_n)
    report = {
        "schema": SCHEMA,
        "generatedAtUtc": decision.now_utc(),
        "developmentN": 350,
        "originalComparableN": len(original),
        "repairedComparableN": len(examples),
        "recoveredN": recovered_n,
        "regressedN": regressed_n,
        "stillNonComparableN": len(omissions),
        "omissionsByReason": dict(sorted(reasons.items())),
        "evidence": evidence,
        "globalReconciliationExceptions": globally_inexact,
        "model": legacy.fit_universe(examples),
        "integrity": {
            "developmentOrdinals": [1, 350],
            "validationRowsRead": 0,
            "outcomesRead": 0,
            "submissions": 0,
            "canonicalCreateInstructionAccountsUsed": True,
            "tradePreFilterDependsOnDiscoveredMints": False,
            "teacherOnlyCreateSeedingUsed": False,
            "strictlyPreTeacherBoundary": True,
            "crossSectionalPolicyEligibleForSealedValidation": verdict.endswith("REPAIRED_OUTCOME_BLIND"),
        },
        "coverageDisposition": {
            "historicalCanonicalBlockReconstructionRequired": verdict.endswith("COVERAGE_BLOCKER"),
            "teacherOnlyEvidenceSeedingPermitted": False,
            "reason": (
                "Stored exact evidence did not recover any omitted LAUNCHED_120S choice sets; "
                "teacher-only backfill would create asymmetric chosen/control coverage."
                if verdict.endswith("COVERAGE_BLOCKER") else None
            ),
        },
        "sourceHashes": {
            "development": decision.sha256_file(args.development),
            "entryTape": decision.sha256_file(args.parent / "entry-decision-tape.jsonl.gz"),
            "originalChoiceSet": decision.sha256_file(args.parent / "choice-set-launched_120s.jsonl.gz"),
            "script": decision.sha256_file(pathlib.Path(__file__)),
        },
        "verdict": verdict,
    }
    legacy.atomic_json(args.output / "report.json", report)
    legacy.atomic_json(args.output / "contract.json", {
        "schema": SCHEMA + "_CONTRACT",
        "developmentOrdinals": [1, 350],
        "choiceUniverse": "LAUNCHED_120S",
        "canonicalCreateSemantics": "Pump Create/CreateV2 instruction account[0]",
        "tradeWindowSemantics": "all successful Pump trade signatures with AllenHark receipt inside a decision window",
        "outcomesPermitted": False,
        "validationPermitted": False,
        "teacherOnlyEvidenceSeedingPermitted": False,
    })
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=pathlib.Path, required=True)
    parser.add_argument("--development", type=pathlib.Path, required=True)
    parser.add_argument("--exact-root", type=pathlib.Path, required=True)
    parser.add_argument("--compact-arrivals", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    print(json.dumps(run(parser.parse_args()), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
