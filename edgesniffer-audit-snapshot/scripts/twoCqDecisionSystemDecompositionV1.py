#!/usr/bin/env python3
"""Outcome-blind decomposition of the frozen 2CQ V3 development cohort.

This study models three different teacher decisions independently:

* WHEN: the selected pre-entry state versus earlier post-event states.
* WHAT: the selected mint versus contemporaneous executable alternatives.
* HOW MUCH: the teacher's natural first-buy size tier.

The sealed validation artifact is deliberately not an input to this program.
"""

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
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any, Iterable

import twoCqCausalEntryTimingFingerprintV1 as timing
import twoCqPostFixEndToEndCollectionDiagnosticV1 as tier_a
from exactCurveEconomicsV1 import CurveState, decode_trade_events
from reconcilePumpExactEvidenceLedgerV1 import iter_frames


SCHEMA = "2CQ_DECISION_SYSTEM_DECOMPOSITION_V1"
WALLET = timing.WALLET
DEVELOPMENT_N = 350
INTERNAL_TRAIN_N = 245
MAX_FIT_CONTROLS_PER_DECISION = 32
MAX_AGE_MS = 120_000
CHOICE_WINDOWS_MS = (250, 1_000, 5_000)
TRADE_KINDS = {
    "BUY", "BUY_EXACT_SOL_IN", "BUY_V2", "BUY_EXACT_QUOTE_IN_V2",
    "SELL", "SELL_V2",
}
COMPACT_FILTER = pathlib.Path(__file__).with_name("compact-arrival-filter-v1")


@dataclass(frozen=True)
class CausalEvent:
    mint: str
    signature: str
    slot: int
    transaction_index: int
    instruction_index: int
    received_ns: int
    is_buy: bool
    user: str
    creator: str
    quote_lamports: int
    token_raw: int
    pre_state: CurveState
    post_state: CurveState

    @property
    def order(self) -> tuple[int, int, int, str]:
        return (self.slot, self.transaction_index, self.instruction_index, self.signature)

    def as_timing_event(self, state: CurveState | None = None, received_ns: int | None = None) -> timing.Event:
        chosen_state = state or self.pre_state
        return timing.Event(
            mint=self.mint,
            signature=self.signature,
            slot=self.slot,
            transaction_index=self.transaction_index,
            instruction_index=self.instruction_index,
            observed_ms=(received_ns if received_ns is not None else self.received_ns) // 1_000_000,
            evidence_id="",
            is_buy=self.is_buy,
            user=self.user,
            creator=self.creator,
            quote_lamports=self.quote_lamports,
            token_raw=self.token_raw,
            pre_state=chosen_state,
            post_state=chosen_state,
        )


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")
    os.replace(temporary, path)


def write_jsonl_gz(path: pathlib.Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as stream:
            for row in rows:
                stream.write(canonical(row) + b"\n")
    os.replace(temporary, path)


def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    with path.open("rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def read_jsonl_gz(path: pathlib.Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def targeted_compact_arrivals(
    path: pathlib.Path, target_digests: set[bytes]
) -> dict[bytes, tuple[int, int]]:
    """Read a stable journal prefix without materializing unrelated slot IDs."""
    arrivals: dict[bytes, tuple[int, int]] = {}
    if COMPACT_FILTER.exists() and os.access(COMPACT_FILTER, os.X_OK):
        with tempfile.NamedTemporaryFile() as targets:
            targets.write(b"".join(sorted(target_digests)))
            targets.flush()
            completed = subprocess.run(
                [str(COMPACT_FILTER), str(path), targets.name],
                check=True, stdout=subprocess.PIPE,
            )
        for offset in range(0, len(completed.stdout), tier_a.COMPACT_RECORD.size):
            raw = completed.stdout[offset:offset + tier_a.COMPACT_RECORD.size]
            if len(raw) != tier_a.COMPACT_RECORD.size:
                raise RuntimeError("COMPACT_FILTER_TRUNCATED_OUTPUT")
            digest, received_ns, slot = tier_a.COMPACT_RECORD.unpack(raw)
            prior = arrivals.get(digest)
            candidate = (int(received_ns), int(slot))
            if prior is None or candidate < prior:
                arrivals[digest] = candidate
        return arrivals
    snapshot_size = path.stat().st_size
    with path.open("rb") as stream:
        if stream.read(len(tier_a.COMPACT_MAGIC)) != tier_a.COMPACT_MAGIC:
            raise RuntimeError("COMPACT_ARRIVAL_MAGIC_MISMATCH")
        while stream.tell() + tier_a.COMPACT_RECORD.size <= snapshot_size:
            raw = stream.read(tier_a.COMPACT_RECORD.size)
            if len(raw) != tier_a.COMPACT_RECORD.size:
                break
            digest, received_ns, slot = tier_a.COMPACT_RECORD.unpack(raw)
            if digest not in target_digests:
                continue
            prior = arrivals.get(digest)
            candidate = (int(received_ns), int(slot))
            if prior is None or candidate < prior:
                arrivals[digest] = candidate
    return arrivals


def load_development(path: pathlib.Path) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    if len(rows) != DEVELOPMENT_N:
        raise RuntimeError(f"DEVELOPMENT_NOT_FROZEN_N350:{len(rows)}")
    expected = list(range(1, DEVELOPMENT_N + 1))
    observed = [int(row.get("eligibleOrdinal") or 0) for row in rows]
    if observed != expected:
        raise RuntimeError("DEVELOPMENT_ORDINALS_NOT_EXACT_1_TO_350")
    if any(row.get("split") != "DEVELOPMENT" for row in rows):
        raise RuntimeError("NON_DEVELOPMENT_ROW_PRESENT")
    if any(int(row.get("outcomesRead") or 0) != 0 for row in rows):
        raise RuntimeError("OUTCOME_TAINT_IN_DEVELOPMENT_CENSUS")
    return rows


def freeze_contract(args: argparse.Namespace, development: list[dict[str, Any]]) -> dict[str, Any]:
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "contract.json"
    contract = {
        "schema": SCHEMA + "_CONTRACT",
        "createdAtUtc": now_utc(),
        "developmentN": DEVELOPMENT_N,
        "developmentEligibleSha256": sha256_file(args.development),
        "censusContractSha256": sha256_file(args.census_contract),
        "censusFreezeSha256": sha256_file(args.census_freeze),
        "correctedFromSupersededTapeSha256": (
            sha256_file(args.superseded_tape_root / "same-mint-temporal-examples.jsonl.gz")
            if args.superseded_tape_root else None
        ),
        "ordinalRange": [1, DEVELOPMENT_N],
        "internalChronologicalTrainOrdinals": [1, INTERNAL_TRAIN_N],
        "internalAssessmentOrdinals": [INTERNAL_TRAIN_N + 1, DEVELOPMENT_N],
        "crossBoundaryRepeatMintsExcludedFromAssessment": True,
        "maximumFitControlsPerDecision": MAX_FIT_CONTROLS_PER_DECISION,
        "fitControlSampling": "deterministic chronology-spread; all controls retained for assessment and forensic tape",
        "designRevision": "CAUSAL_TRIGGER_EVENT_STATE_V2",
        "causalBoundary": "last external event state strictly before teacher AllenHark receipt; teacher transaction contributes zero features",
        "temporalControlSemantics": "chosen state is immediately AFTER the final external event before the teacher buy; controls are states after earlier external events",
        "choiceSetSemantics": {
            "maximumMintAgeMs": MAX_AGE_MS,
            "supportedRoute": "Pump bonding-curve exact SOL reserve state",
            "mustHaveCanonicalCreateAndAllenHarkArrival": True,
            "mustHavePriorSuccessfulExternalPumpEvent": True,
            "mustRemainOnCurve": True,
            "universes": [
                "LAUNCHED_120S",
                "ACTIVE_250MS",
                "ACTIVE_1000MS",
                "ACTIVE_5000MS",
            ],
        },
        "featureFamilies": {
            "progression": list(timing.PROGRESSION_FEATURES),
            "flow": list(timing.FLOW_FEATURES),
            "participants": list(timing.PARTICIPANT_FEATURES),
            "sequence": list(timing.SEQUENCE_FEATURES),
        },
        "teacherActionsModeled": ["WHAT_MINT", "WHEN_ENTER", "HOW_MUCH"],
        "teacherActionsDeferred": ["ADD_WHILE_EXPOSED", "PARTIAL_EXIT", "FINAL_FLATTEN"],
        "deferredReason": "V3 freezes independent entries; later action-state reconstruction requires a separately frozen action tape",
        "outcomesPermitted": False,
        "validationArtifactAcceptedAsInput": False,
        "validationRowsRead": 0,
        "submissionsPermitted": False,
    }
    if path.exists():
        frozen = json.loads(path.read_text(encoding="utf-8"))
        for key in (
            "designRevision", "developmentN", "developmentEligibleSha256", "censusContractSha256",
            "censusFreezeSha256", "ordinalRange", "choiceSetSemantics",
            "featureFamilies", "outcomesPermitted", "validationArtifactAcceptedAsInput",
        ):
            if frozen.get(key) != contract.get(key):
                raise RuntimeError(f"DECOMPOSITION_FREEZE_MISMATCH:{key}")
        return frozen
    atomic_json(path, contract)
    atomic_json(args.output / "freeze.json", {
        "schema": SCHEMA + "_FREEZE",
        "createdAtUtc": contract["createdAtUtc"],
        "contractSha256": sha256_file(path),
    })
    return contract


def cached_transaction(cache: pathlib.Path, signature: str) -> dict[str, Any]:
    candidates = (
        cache / f"tx-{signature}.json",
        cache.parent / "wallet-rpc-cache" / f"tx-{signature}.json",
    )
    path = next((candidate for candidate in candidates if candidate.exists()), None)
    if path is None:
        raise RuntimeError(f"MISSING_FROZEN_TRANSACTION_CACHE:{signature}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = payload.get("result") if "result" in payload else payload
    if result is None:
        raise RuntimeError(f"NULL_FROZEN_TRANSACTION_CACHE:{signature}")
    return result


def cached_older_signatures(cache: pathlib.Path, mint: str, entry_signature: str) -> list[dict[str, Any]]:
    key = hashlib.sha256(f"{mint}:{entry_signature}".encode()).hexdigest()
    path = cache / f"signatures-{key}.json"
    if not path.exists():
        raise RuntimeError(f"MISSING_FROZEN_HISTORY_CACHE:{mint}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("historyBoundaryReached") is not True:
        raise RuntimeError(f"UNPROVEN_FROZEN_HISTORY_BOUNDARY:{mint}")
    return list(payload.get("rows") or payload.get("signatures") or [])


def cached_block_indexes(cache: pathlib.Path, slot: int) -> dict[str, int]:
    path = cache / f"block-index-{slot}.json"
    if not path.exists():
        raise RuntimeError(f"MISSING_FROZEN_BLOCK_INDEX:{slot}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): int(v) for k, v in (payload.get("indexes") or payload).items()}


def source_events(tx: dict[str, Any], signature: str, mint: str, received_ns: int, cache: pathlib.Path) -> list[CausalEvent]:
    if (tx.get("meta") or {}).get("err") is not None:
        return []
    decoded = [event for event in decode_trade_events((tx.get("meta") or {}).get("logMessages") or []) if event.mint == mint]
    if not decoded:
        return []
    sources = tier_a.extract_pump_source_observations(
        tx, signature, mint, received_ns // 1_000_000, "allenhark"
    )
    sources = sorted(sources, key=lambda row: row.instruction_index)
    if len(decoded) != len(sources):
        raise RuntimeError(f"EVENT_SOURCE_CARDINALITY_MISMATCH:{signature}")
    slot = int(tx.get("slot") or 0)
    indexes = cached_block_indexes(cache, slot)
    if signature not in indexes:
        raise RuntimeError(f"SIGNATURE_NOT_IN_FROZEN_BLOCK_INDEX:{signature}")
    rows: list[CausalEvent] = []
    for event, source in zip(decoded, sources):
        rows.append(CausalEvent(
            mint=mint,
            signature=signature,
            slot=slot,
            transaction_index=indexes[signature],
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
    return rows


def selected_mint_events(
    development: list[dict[str, Any]], cache: pathlib.Path, compact_path: pathlib.Path,
) -> tuple[dict[str, list[CausalEvent]], dict[str, int], dict[str, CausalEvent]]:
    required: set[str] = set()
    histories: dict[str, list[dict[str, Any]]] = {}
    for row in development:
        signature = str(row["signature"])
        required.add(signature)
        history = cached_older_signatures(cache, str(row["mint"]), signature)
        histories[signature] = history
        required.update(str(item.get("signature") or "") for item in history if item.get("signature"))
    for signature in sorted(required):
        cached_transaction(cache, signature)
    arrivals = targeted_compact_arrivals(
        compact_path, {tier_a.signature_digest(signature) for signature in required}
    )
    events: dict[str, dict[tuple[str, int], CausalEvent]] = collections.defaultdict(dict)
    entry_events: dict[str, CausalEvent] = {}
    create_ns: dict[str, int] = {}
    for row in development:
        mint = str(row["mint"])
        entry_signature = str(row["signature"])
        create_signature = str((row.get("tierAEvidence") or {}).get("createSignature") or "")
        create_arrival = arrivals.get(tier_a.signature_digest(create_signature)) if create_signature else None
        if create_arrival:
            prior = create_ns.get(mint)
            create_ns[mint] = min(prior, int(create_arrival[0])) if prior else int(create_arrival[0])
        signatures = [str(item.get("signature") or "") for item in histories[entry_signature]] + [entry_signature]
        for signature in signatures:
            arrival = arrivals.get(tier_a.signature_digest(signature))
            if not arrival:
                continue
            tx = cached_transaction(cache, signature)
            for event in source_events(tx, signature, mint, int(arrival[0]), cache):
                events[mint][(event.signature, event.instruction_index)] = event
                if signature == entry_signature and event.user == WALLET and event.is_buy:
                    if entry_signature in entry_events:
                        raise RuntimeError(f"MULTIPLE_TEACHER_ENTRY_EVENTS:{entry_signature}")
                    entry_events[entry_signature] = event
    result = {mint: sorted(rows.values(), key=lambda event: event.order) for mint, rows in events.items()}
    missing = [row["signature"] for row in development if row["signature"] not in entry_events]
    if missing:
        raise RuntimeError(f"DEVELOPMENT_ENTRY_EVENTS_NOT_JOINED:{len(missing)}")
    return result, create_ns, entry_events


def state_features(
    state: CurveState,
    received_ns: int,
    mint: str,
    creator: str,
    prior: list[CausalEvent],
    create_ns: int,
) -> dict[str, float]:
    synthetic = timing.Event(
        mint=mint, signature="SNAPSHOT", slot=0, transaction_index=0,
        instruction_index=0, observed_ms=received_ns // 1_000_000, evidence_id="",
        is_buy=False, user="SNAPSHOT", creator=creator, quote_lamports=0,
        token_raw=0, pre_state=state, post_state=state,
    )
    converted = [row.as_timing_event() for row in prior]
    initial_state = converted[0].pre_state if converted else state
    return timing.state_features(synthetic, converted, create_ns // 1_000_000, initial_state)


def build_temporal_examples(
    development: list[dict[str, Any]],
    events: dict[str, list[CausalEvent]],
    create_ns: dict[str, int],
    entries: dict[str, CausalEvent],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    examples: list[dict[str, Any]] = []
    tape: list[dict[str, Any]] = []
    for row in development:
        mint = str(row["mint"])
        actual = entries[str(row["signature"])]
        sequence = events[mint]
        before = [event for event in sequence if event.order < actual.order and event.received_ns < actual.received_ns]
        external = [event for event in before if event.user != WALLET]
        if not external:
            raise RuntimeError(f"NO_EXTERNAL_TRIGGER_STATE:{actual.signature}")
        controls = []
        for index, event in enumerate(external[:-1]):
            through = [prior for prior in before if prior.received_ns <= event.received_ns and prior.order <= event.order]
            controls.append({
                "label": False,
                "signature": event.signature,
                "receivedNs": event.received_ns,
                "semantics": "POST_EXTERNAL_EVENT",
                "features": state_features(event.post_state, event.received_ns, mint, event.creator, through, create_ns[mint]),
            })
        selected_event = external[-1]
        selected_through = [
            prior for prior in before
            if prior.received_ns <= selected_event.received_ns and prior.order <= selected_event.order
        ]
        actual_features = state_features(
            selected_event.post_state, selected_event.received_ns, mint,
            selected_event.creator, selected_through, create_ns[mint]
        )
        actual_state = {
            "label": True,
            "signature": selected_event.signature,
            "receivedNs": selected_event.received_ns,
            "semantics": "POST_FINAL_EXTERNAL_EVENT_BEFORE_TEACHER_ENTRY",
            "features": actual_features,
        }
        example = {
            "episodeId": actual.signature,
            "eligibleOrdinal": int(row["eligibleOrdinal"]),
            "mint": mint,
            "entrySignature": actual.signature,
            "openedTimestamp": actual.received_ns // 1_000_000,
            "naturalFirstBuySol": actual.quote_lamports / 1e9,
            "sizeTier": timing.size_tier(actual.quote_lamports / 1e9),
            "controlN": len(controls),
            "states": controls + [actual_state],
        }
        examples.append(example)
        tape.append({
            "schema": SCHEMA + "_ENTRY_DECISION",
            "eligibleOrdinal": int(row["eligibleOrdinal"]),
            "mint": mint,
            "signature": actual.signature,
            "entryReceivedAtUnixNs": actual.received_ns,
            "entrySlot": actual.slot,
            "entryTransactionIndex": actual.transaction_index,
            "route": row["route"],
            "acquisitionClass": row["acquisitionClass"],
            "naturalFirstBuySol": actual.quote_lamports / 1e9,
            "sizeTier": timing.size_tier(actual.quote_lamports / 1e9),
            "decisionStateSignature": selected_event.signature,
            "decisionStateReceivedAtUnixNs": selected_event.received_ns,
            "decisionStateSlot": selected_event.slot,
            "decisionStateTransactionIndex": selected_event.transaction_index,
            "decisionToTeacherReceiptMs": (actual.received_ns - selected_event.received_ns) / 1_000_000,
            "createReceivedAtUnixNs": create_ns[mint],
            "earlierExternalControlN": len(controls),
            "features": actual_features,
            "outcomesRead": 0,
        })
    return examples, tape


def correct_superseded_receipt_clock_tape(
    source: pathlib.Path, cache: pathlib.Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    old_examples = read_jsonl_gz(source / "same-mint-temporal-examples.jsonl.gz")
    old_tape = read_jsonl_gz(source / "entry-decision-tape.jsonl.gz")
    if len(old_examples) != DEVELOPMENT_N or len(old_tape) != DEVELOPMENT_N:
        raise RuntimeError("SUPERSEDED_DECISION_TAPE_NOT_N350")
    tape_by_ordinal = {int(row["eligibleOrdinal"]): row for row in old_tape}
    corrected_examples: list[dict[str, Any]] = []
    corrected_tape: list[dict[str, Any]] = []
    for old in old_examples:
        ordinal = int(old["eligibleOrdinal"])
        controls = [row for row in old["states"] if not row["label"]]
        if not controls:
            raise RuntimeError(f"SUPERSEDED_TAPE_HAS_NO_EXTERNAL_STATE:{ordinal}")
        selected = controls[-1] | {
            "label": True,
            "semantics": "POST_FINAL_EXTERNAL_EVENT_BEFORE_TEACHER_ENTRY",
        }
        remaining_controls = controls[:-1]
        corrected_examples.append(old | {
            "controlN": len(remaining_controls),
            "states": remaining_controls + [selected],
        })
        prior_tape = tape_by_ordinal[ordinal]
        tx = cached_transaction(cache, str(selected["signature"]))
        slot = int(tx.get("slot") or 0)
        tx_index = cached_block_indexes(cache, slot)[str(selected["signature"])]
        teacher_receipt = int(prior_tape["entryReceivedAtUnixNs"])
        selected_receipt = int(selected["receivedNs"])
        if selected_receipt >= teacher_receipt:
            raise RuntimeError(f"NON_CAUSAL_SELECTED_STATE:{ordinal}")
        corrected_tape.append(prior_tape | {
            "decisionStateSignature": selected["signature"],
            "decisionStateReceivedAtUnixNs": selected_receipt,
            "decisionStateSlot": slot,
            "decisionStateTransactionIndex": tx_index,
            "decisionToTeacherReceiptMs": (teacher_receipt - selected_receipt) / 1_000_000,
            "earlierExternalControlN": len(remaining_controls),
            "features": selected["features"],
        })
    return corrected_examples, corrected_tape


def internal_split(examples: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    train = [row for row in examples if int(row["eligibleOrdinal"]) <= INTERNAL_TRAIN_N]
    train_mints = {row["mint"] for row in train}
    raw_assessment = [row for row in examples if int(row["eligibleOrdinal"]) > INTERNAL_TRAIN_N]
    assessment = [row for row in raw_assessment if row["mint"] not in train_mints]
    return train, assessment, len(raw_assessment) - len(assessment)


def fit_control_sample(example: dict[str, Any]) -> dict[str, Any]:
    positives = [row for row in example["states"] if row["label"]]
    controls = [row for row in example["states"] if not row["label"]]
    if len(positives) != 1:
        raise RuntimeError("DECISION_MUST_HAVE_EXACTLY_ONE_POSITIVE")
    if len(controls) <= MAX_FIT_CONTROLS_PER_DECISION:
        selected = controls
    else:
        indexes = {
            round(index * (len(controls) - 1) / (MAX_FIT_CONTROLS_PER_DECISION - 1))
            for index in range(MAX_FIT_CONTROLS_PER_DECISION)
        }
        selected = [controls[index] for index in sorted(indexes)]
    return example | {"controlN": len(selected), "states": selected + positives}


def model_suite(train: list[dict[str, Any]], assessment: list[dict[str, Any]]) -> dict[str, Any]:
    families = {
        "LIFECYCLE": timing.PROGRESSION_FEATURES,
        "LIFECYCLE_FLOW": timing.PROGRESSION_FEATURES + timing.FLOW_FEATURES,
        "FULL_CAUSAL": timing.FULL_FEATURES,
    }
    result = {}
    fit_train = [fit_control_sample(row) for row in train]
    for name, features in families.items():
        report, _ = timing.evaluate_model(fit_train, assessment, features)
        report["fitControlPolicy"] = {
            "maximumPerDecision": MAX_FIT_CONTROLS_PER_DECISION,
            "fullAssessmentControlsRetained": True,
        }
        result[name] = report
    return result


def render_report(report: dict[str, Any]) -> str:
    temporal = report["entryTiming"]
    lines = [
        "# 2CQ Decision System Decomposition V1",
        "",
        f"Generated: `{report['generatedAtUtc']}`",
        "",
        "## Integrity",
        "",
        f"- Development rows: **{report['population']['developmentN']}**",
        f"- Validation rows read: **{report['integrity']['validationRowsRead']}**",
        f"- Outcomes read: **{report['integrity']['outcomesRead']}**",
        f"- Submissions: **{report['integrity']['submissions']}**",
        "",
        "## Entry Timing",
        "",
        "The chosen row is the state immediately after the final external event before 2CQ's buy. Controls are states after earlier external events.",
        "",
        "| Model | Assessment AUC | Chosen ranked first | Top 3 | MRR |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, row in temporal["models"].items():
        assessment = row["validation"]
        ranking = assessment["withinMint"]
        lines.append(
            f"| {name} | {assessment['rocAuc']:.3f} | {ranking['actualRankedFirstPct']:.2f}% | "
            f"{ranking['actualRankedTop3Pct']:.2f}% | {ranking['meanReciprocalRank']:.3f} |"
        )
    lines += [
        "",
        "## Current Boundary",
        "",
        "This first artifact completes the corrected same-mint timing and conviction tape. "
        "The full contemporaneous cross-sectional choice-set scan is a separately materialized phase because the latest development hour must first become finalized in the exact ledger.",
        "",
        f"Verdict: `{report['verdict']}`",
        "",
    ]
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    development = load_development(args.development)
    contract = freeze_contract(args, development)
    census_contract = json.loads(args.census_contract.read_text(encoding="utf-8"))
    if census_contract.get("validationSealed") is not True:
        raise RuntimeError("CENSUS_VALIDATION_NOT_SEALED")
    examples_path = args.output / "same-mint-temporal-examples.jsonl.gz"
    tape_path = args.output / "entry-decision-tape.jsonl.gz"
    if examples_path.exists() and tape_path.exists():
        examples = read_jsonl_gz(examples_path)
        tape = read_jsonl_gz(tape_path)
        if len(examples) != DEVELOPMENT_N or len(tape) != DEVELOPMENT_N:
            raise RuntimeError("CACHED_DECISION_TAPE_NOT_N350")
    elif args.superseded_tape_root:
        examples, tape = correct_superseded_receipt_clock_tape(
            args.superseded_tape_root, args.rpc_cache
        )
        write_jsonl_gz(tape_path, tape)
        write_jsonl_gz(examples_path, examples)
    else:
        events, create_ns, entries = selected_mint_events(development, args.rpc_cache, args.compact_arrivals)
        examples, tape = build_temporal_examples(development, events, create_ns, entries)
        write_jsonl_gz(tape_path, tape)
        write_jsonl_gz(examples_path, examples)
    timing_examples = [row for row in examples if int(row["controlN"]) > 0]
    timing_train, timing_assessment, cross_boundary_dropped = internal_split(timing_examples)
    conviction_train, conviction_assessment, conviction_repeat_dropped = internal_split(examples)
    models = model_suite(timing_train, timing_assessment)
    conviction = timing.conviction_model(conviction_train, conviction_assessment)
    report = {
        "schema": SCHEMA,
        "generatedAtUtc": now_utc(),
        "contractSha256": sha256_file(args.output / "contract.json"),
        "population": {
            "developmentN": len(development),
            "uniqueMints": len({row["mint"] for row in development}),
            "sameMintTemporalControls": sum(int(row["controlN"]) for row in examples),
            "timingIdentifiableN": len(timing_examples),
            "internalTimingTrainN": len(timing_train),
            "internalTimingAssessmentN": len(timing_assessment),
            "internalConvictionTrainN": len(conviction_train),
            "internalConvictionAssessmentN": len(conviction_assessment),
            "crossBoundaryRepeatEpisodesDropped": cross_boundary_dropped,
            "convictionCrossBoundaryRepeatEpisodesDropped": conviction_repeat_dropped,
            "routes": dict(sorted(collections.Counter(row["route"] for row in development).items())),
            "acquisitionClasses": dict(sorted(collections.Counter(row["acquisitionClass"] for row in development).items())),
        },
        "entryTiming": {"models": models},
        "conviction": conviction,
        "crossSectionalSelection": {
            "status": "PENDING_FINALIZED_FULL_UNIVERSE_MATERIALIZATION",
            "choiceUniverses": contract["choiceSetSemantics"]["universes"],
            "latestDevelopmentEntryUnixNs": max(int(row["entryReceivedAtUnixNs"]) for row in development),
        },
        "deferredActions": contract["teacherActionsDeferred"],
        "integrity": {
            "developmentSha256": sha256_file(args.development),
            "validationPathAccepted": False,
            "validationRowsRead": 0,
            "outcomesRead": 0,
            "submissions": 0,
            "teacherEntryIncludedInFeatures": False,
            "temporalControlsUsePostEventState": True,
            "compactArrivalFilter": str(COMPACT_FILTER) if COMPACT_FILTER.exists() else "PYTHON_FALLBACK",
            "compactArrivalFilterSha256": sha256_file(COMPACT_FILTER) if COMPACT_FILTER.exists() else None,
            "correctedFromSupersededTape": str(args.superseded_tape_root) if args.superseded_tape_root else None,
        },
        "verdict": "2CQ_DEVELOPMENT_DECISION_TAPE_BUILT_CROSS_SECTIONAL_PENDING",
    }
    atomic_json(args.output / "report.json", report)
    (args.output / "report.md").write_text(render_report(report), encoding="utf-8")
    atomic_json(args.output / "provenance.json", {
        "schema": SCHEMA + "_PROVENANCE",
        "contractSha256": sha256_file(args.output / "contract.json"),
        "developmentSha256": sha256_file(args.development),
        "scriptSha256": sha256_file(pathlib.Path(__file__)),
        "artifacts": {
            name: sha256_file(args.output / name)
            for name in ("entry-decision-tape.jsonl.gz", "same-mint-temporal-examples.jsonl.gz", "report.json", "report.md")
        },
    })
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--development", type=pathlib.Path, required=True)
    parser.add_argument("--census-contract", type=pathlib.Path, required=True)
    parser.add_argument("--census-freeze", type=pathlib.Path, required=True)
    parser.add_argument("--rpc-cache", type=pathlib.Path, required=True)
    parser.add_argument("--compact-arrivals", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--superseded-tape-root", type=pathlib.Path)
    args = parser.parse_args()
    print(json.dumps(run(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
