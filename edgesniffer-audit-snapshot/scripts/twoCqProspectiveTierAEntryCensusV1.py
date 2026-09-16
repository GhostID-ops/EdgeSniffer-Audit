#!/usr/bin/env python3
"""Future-only, outcome-blind Tier-A entry census for the 2CQ teacher wallet."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
from dataclasses import dataclass
from typing import Any, Iterable

from buildPumpExactAlphaResearchV1 import paths_for_report
from exactCurveEconomicsV1 import decode_trade_events, extract_pump_source_observations
from reconcilePumpExactEvidenceLedgerV1 import iter_frames


SCHEMA = "2CQ_PROSPECTIVE_TIER_A_ENTRY_CENSUS_V1"
WALLET = "2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG"
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
TRADE_KINDS = {"BUY", "BUY_EXACT_SOL_IN", "BUY_V2", "BUY_EXACT_QUOTE_IN_V2", "SELL", "SELL_V2"}
TARGET_N = 500
DEVELOPMENT_N = 350


def freeze_study_design(
    root: pathlib.Path,
    historical_script: pathlib.Path,
    historical_report: pathlib.Path,
) -> dict[str, Any]:
    """Freeze the prior study contract without evaluating prospective outcomes."""
    freeze_path = root / "study-design-freeze.json"
    model_path = root / "locked-v1-entry-timing-model.json"
    script_sha = sha256_file(historical_script)
    report_sha = sha256_file(historical_report)

    if freeze_path.exists():
        frozen = json.loads(freeze_path.read_text(encoding="utf-8"))
        if frozen.get("historicalStudyScriptSha256") != script_sha:
            raise RuntimeError("HISTORICAL_STUDY_SCRIPT_CHANGED_FAIL_CLOSED")
        if frozen.get("historicalReportSha256") != report_sha:
            raise RuntimeError("HISTORICAL_MODEL_REPORT_CHANGED_FAIL_CLOSED")
        if not model_path.exists() or sha256_file(model_path) != frozen.get("lockedEntryTimingModelSha256"):
            raise RuntimeError("LOCKED_V1_MODEL_ARTIFACT_CHANGED_FAIL_CLOSED")
        return frozen

    historical = json.loads(historical_report.read_text(encoding="utf-8"))
    timing = historical["entryTiming"]
    full = timing["fullCausalModel"]
    feature_names = list(full["featureNames"])
    coefficients = {
        row["feature"]: row["standardizedCoefficient"]
        for row in full["coefficients"]
    }
    locked_model = {
        "schema": SCHEMA + "_LOCKED_V1_ENTRY_TIMING_MODEL",
        "sourceReportSha256": report_sha,
        "modelClass": "mint-balanced weighted binary logistic regression with L2=0.03",
        "featureNames": feature_names,
        "trainMeans": [full["trainMeans"][name] for name in feature_names],
        "trainScales": [full["trainScales"][name] for name in feature_names],
        "intercept": full["intercept"],
        "coefficients": [coefficients[name] for name in feature_names],
        "performanceMetricsIncluded": False,
        "prospectiveDataRead": False,
    }
    atomic_json(model_path, locked_model)

    frozen = {
        "schema": SCHEMA + "_STUDY_DESIGN_FREEZE",
        "createdAtUtc": now_utc(),
        "historicalStudyScript": str(historical_script),
        "historicalStudyScriptSha256": script_sha,
        "historicalReport": str(historical_report),
        "historicalReportSha256": report_sha,
        "lockedEntryTimingModel": str(model_path),
        "lockedEntryTimingModelSha256": sha256_file(model_path),
        "prospectiveSplit": {
            "developmentCalibrationOrdinals": [1, DEVELOPMENT_N],
            "untouchedValidationOrdinals": [DEVELOPMENT_N + 1, TARGET_N],
        },
        "causalBoundary": "teacher entry excluded; candidate features use only finalized successful events canonically and observationally earlier",
        "sameMintControls": "all valid earlier external Pump transaction states with strict canonical and local-arrival precedence",
        "entryTimingTarget": "actual 2CQ first-entry state versus earlier same-mint external transaction states",
        "convictionTarget": "natural first-buy SOL tier",
        "entryTimingModelClass": "mint-balanced weighted binary logistic regression with L2=0.03",
        "convictionModelClass": "five-class multinomial softmax regression with L2=0.04",
        "sizeTierBoundariesSol": historical["conviction"]["tierBoundariesSol"],
        "progressionFeatureNames": timing["progressionBaseline"]["featureNames"],
        "fullFeatureNames": feature_names,
        "predeclaredFamilyAblations": list(timing["predeclaredFamilyAblations"].keys()),
        "newFeaturesPermitted": False,
        "newThresholdsPermitted": False,
        "interimPerformanceInspectionPermitted": False,
        "futureOutcomesPermitted": False,
        "chosenVsIgnoredPermitted": False,
        "prospectiveDataReadDuringFreeze": False,
    }
    atomic_json(freeze_path, frozen)
    return frozen


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


def append_jsonl(path: pathlib.Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as stream:
        for row in rows:
            stream.write(canonical(row) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("rb") as stream:
        for line in stream:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def detector_identity(path: pathlib.Path) -> dict[str, int]:
    stat = path.stat()
    return {"device": int(stat.st_dev), "inode": int(stat.st_ino), "size": int(stat.st_size)}


def partition_key_from_ms(timestamp_ms: int) -> str:
    value = dt.datetime.fromtimestamp(timestamp_ms / 1000, dt.timezone.utc)
    return value.strftime("%Y/%m/%d/%H")


def parse_candidate(row: dict[str, Any], cohort_start_ms: int) -> dict[str, Any] | None:
    timestamp_ms = int(row.get("detectedAtMs") or 0)
    accounts = set(row.get("sourceIxAccounts") or [])
    if timestamp_ms < cohort_start_ms:
        return None
    if row.get("kind") != "swap" or row.get("wallet") != WALLET:
        return None
    if row.get("isNativeInput") is not True or PUMP_PROGRAM not in accounts:
        return None
    if row.get("sourcePumpSwapInstruction") is not None:
        return None
    signature = str(row.get("signature") or "")
    mint = str(row.get("outputMint") or "")
    if not signature or not mint:
        return None
    return {
        "schema": SCHEMA + "_RAW_ENTRY",
        "observedAtUtc": dt.datetime.fromtimestamp(timestamp_ms / 1000, dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "observedUnixMs": timestamp_ms,
        "signature": signature,
        "mint": mint,
        "sourceSlot": int(row.get("sourceSlot") or 0),
        "sourceTransactionIndex": row.get("sourceTransactionIndex"),
        "sourceIxDataBase64": row.get("sourceIxDataBase64"),
        "sourceIxAccounts": row.get("sourceIxAccounts") or [],
        "candidateSource": str(row.get("source") or "unknown"),
        "detectorPartitionKey": partition_key_from_ms(timestamp_ms),
    }


def initialize(
    root: pathlib.Path,
    detector: pathlib.Path,
    exact_root: pathlib.Path,
    candidate_source_label: str,
    eligibility_mode: str,
) -> dict[str, Any]:
    contract_path = root / "cohort-contract.json"
    if contract_path.exists():
        return json.loads(contract_path.read_text(encoding="utf-8"))
    identity = detector_identity(detector)
    started = dt.datetime.now(dt.timezone.utc)
    health_path = exact_root / "health.json"
    health = json.loads(health_path.read_text(encoding="utf-8")) if health_path.exists() else {}
    contract = {
        "schema": SCHEMA + "_CONTRACT",
        "cohortStartUtc": started.isoformat().replace("+00:00", "Z"),
        "cohortStartUnixMs": int(started.timestamp() * 1000),
        "teacherWallet": WALLET,
        "targetEligibleN": TARGET_N,
        "developmentOrdinals": [1, DEVELOPMENT_N],
        "untouchedValidationOrdinals": [DEVELOPMENT_N + 1, TARGET_N],
        "detectorPath": str(detector),
        "candidateSourceLabel": candidate_source_label,
        "eligibilityMode": eligibility_mode,
        "detectorStartOffset": identity["size"],
        "detectorDevice": identity["device"],
        "detectorInode": identity["inode"],
        "exactLedgerRoot": str(exact_root),
        "exactLedgerProcessInstanceAtStart": health.get("processInstanceUuid"),
        "causalBoundary": "only finalized successful events with canonical order and local arrival strictly before the 2CQ entry are pre-entry evidence",
        "outcomesPermittedBeforeN500": False,
        "frozenSplitPrecommittedBeforeCollection": True,
    }
    root.mkdir(parents=True, exist_ok=True)
    atomic_json(contract_path, contract)
    atomic_json(root / "state.json", {
        "schema": SCHEMA + "_STATE",
        "detectorOffset": identity["size"],
        "detectorDevice": identity["device"],
        "detectorInode": identity["inode"],
        "initializedAtUtc": contract["cohortStartUtc"],
        "lastRunUtc": None,
    })
    atomic_json(root / "freeze.json", {
        "schema": SCHEMA + "_FREEZE",
        "contractSha256": sha256_file(contract_path),
        "createdAtUtc": contract["cohortStartUtc"],
    })
    atomic_json(root / "candidate-source-provenance.json", {
        "schema": SCHEMA + "_CANDIDATE_SOURCE_PROVENANCE",
        "createdAtUtc": contract["cohortStartUtc"],
        "candidateSourceLabel": candidate_source_label,
        "detectorPath": str(detector),
        "arrivalSemantics": "local receipt timestamp of the raw Yellowstone transaction by PUMP_EXACT_EVIDENCE_LEDGER_V1",
        "canonicalEvidenceRequirement": "candidate remains pending until finalized exact evidence and strict earlier same-mint controls are available",
        "outcomeBlind": True,
        "submissions": 0,
    })
    return contract


def ingest_candidates(root: pathlib.Path, detector: pathlib.Path, contract: dict[str, Any]) -> dict[str, int]:
    state_path = root / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    identity = detector_identity(detector)
    if identity["device"] != state["detectorDevice"] or identity["inode"] != state["detectorInode"]:
        raise RuntimeError("DETECTOR_FILE_IDENTITY_CHANGED_FAIL_CLOSED")
    offset = int(state["detectorOffset"])
    if identity["size"] < offset:
        raise RuntimeError("DETECTOR_FILE_TRUNCATED_FAIL_CLOSED")
    existing = {row["signature"] for row in read_jsonl(root / "raw-2cq-entries.jsonl")}
    candidates: list[dict[str, Any]] = []
    malformed = 0
    with detector.open("rb") as stream:
        stream.seek(offset)
        while True:
            line = stream.readline()
            if not line:
                break
            try:
                parsed = parse_candidate(json.loads(line), int(contract["cohortStartUnixMs"]))
            except Exception:
                malformed += 1
                continue
            if parsed is not None and parsed["signature"] not in existing:
                existing.add(parsed["signature"])
                candidates.append(parsed)
        new_offset = stream.tell()
    append_jsonl(root / "raw-2cq-entries.jsonl", candidates)
    state.update({"detectorOffset": new_offset, "lastRunUtc": now_utc()})
    atomic_json(state_path, state)
    return {"newRawEntries": len(candidates), "malformedDetectorRows": malformed}


def readiness_reports(exact_root: pathlib.Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    reports: dict[str, dict[str, Any]] = {}
    mints: dict[str, dict[str, Any]] = {}
    for path in sorted((exact_root / "readiness-v1/partitions").glob("*-v4.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        key = str(report["partitionKey"])
        reports[key] = report
        for item in report.get("mints") or []:
            mints.setdefault(str(item["mint"]), {"partitionKey": key, **item})
    return reports, mints


@dataclass(frozen=True)
class Event:
    mint: str
    signature: str
    slot: int
    transaction_index: int
    instruction_index: int
    arrival_ms: int
    arrival_evidence_id: str
    canonical_evidence_id: str
    is_buy: bool
    user: str
    creator: str
    quote_lamports: int
    token_raw: int
    pre_state: dict[str, int]
    post_state: dict[str, int]

    @property
    def order(self) -> tuple[int, int, int, str]:
        return (self.slot, self.transaction_index, self.instruction_index, self.signature)

    def payload(self) -> dict[str, Any]:
        return {
            "mint": self.mint, "signature": self.signature, "slot": self.slot,
            "transactionIndex": self.transaction_index, "instructionIndex": self.instruction_index,
            "arrivalUnixMs": self.arrival_ms, "arrivalEvidenceId": self.arrival_evidence_id,
            "canonicalEvidenceId": self.canonical_evidence_id, "isBuy": self.is_buy,
            "user": self.user, "creator": self.creator, "quoteLamports": self.quote_lamports,
            "tokenRaw": self.token_raw, "preState": self.pre_state, "postState": self.post_state,
        }


def curve_payload(state: Any) -> dict[str, int]:
    return {
        "virtual_sol": int(state.virtual_sol), "virtual_token": int(state.virtual_token),
        "real_sol": int(state.real_sol), "real_token": int(state.real_token),
    }


def wallet_token_amount(transaction: dict[str, Any], side: str, mint: str) -> int | None:
    balances = (transaction.get("meta") or {}).get(side) or []
    matched = []
    for row in balances:
        if str(row.get("mint") or "") != mint or str(row.get("owner") or "") != WALLET:
            continue
        amount = ((row.get("uiTokenAmount") or {}).get("amount"))
        if amount is not None:
            matched.append(int(amount))
    return sum(matched) if matched else None


def scan_report(exact_root: pathlib.Path, report: dict[str, Any], target_mints: set[str]) -> tuple[dict[str, list[Event]], dict[str, dict[str, Any]], dict[str, Any]]:
    paths = paths_for_report(exact_root, report)
    arrivals: dict[str, tuple[int, str]] = {}
    finalized_transactions: dict[str, dict[str, Any]] = {}
    provisional: list[Event] = []
    malformed = 0
    target_bytes = [mint.encode() for mint in target_mints]
    for path in paths:
        for metadata, raw in iter_frames(path, path.suffix == ".partial"):
            signature = str(metadata.get("signature") or "")
            if metadata.get("kind") == "transaction" and signature:
                kinds = set(metadata.get("pumpInstructionKinds") or [])
                if kinds & TRADE_KINDS:
                    observed_ms = int(metadata.get("receivedAtUnixNs") or 0) // 1_000_000
                    evidence_id = str(metadata.get("evidenceId") or "")
                    if observed_ms and evidence_id:
                        current = arrivals.get(signature)
                        candidate = (observed_ms, evidence_id)
                        if current is None or candidate < current:
                            arrivals[signature] = candidate
                continue
            if metadata.get("kind") != "rpc_finalized_transaction" or not signature:
                continue
            if not any(value in raw for value in target_bytes):
                continue
            try:
                transaction = json.loads(raw).get("result")
                if not transaction or (transaction.get("meta") or {}).get("err") is not None:
                    continue
                decoded = decode_trade_events((transaction.get("meta") or {}).get("logMessages") or [])
                matching = [event for event in decoded if event.mint in target_mints]
                if not matching:
                    continue
                finalized_transactions[signature] = transaction
                slot = int(metadata.get("slot") or transaction.get("slot") or 0)
                tx_index = int(metadata.get("transactionIndex") if metadata.get("transactionIndex") is not None else -1)
                canonical_id = str(metadata.get("evidenceId") or "")
                for mint in {event.mint for event in matching}:
                    sources = extract_pump_source_observations(transaction, signature, mint, 0, "finalized_chain")
                    sources.sort(key=lambda item: item.instruction_index)
                    for event in [item for item in matching if item.mint == mint]:
                        source_index = next(
                            (index for index, item in enumerate(sources) if (item.side == "buy") == bool(event.is_buy)),
                            None,
                        )
                        if source_index is None:
                            raise RuntimeError(f"SOURCE_EVENT_PAIR_MISSING:{signature}:{mint}")
                        source = sources.pop(source_index)
                        provisional.append(Event(
                            mint=event.mint, signature=signature, slot=slot,
                            transaction_index=tx_index, instruction_index=int(source.instruction_index),
                            arrival_ms=0, arrival_evidence_id="", canonical_evidence_id=canonical_id,
                            is_buy=bool(event.is_buy), user=event.user, creator=event.creator,
                            quote_lamports=int(event.immediate_quote_amount), token_raw=int(event.token_amount),
                            pre_state=curve_payload(event.pre_state), post_state=curve_payload(event.post_state),
                        ))
            except Exception:
                malformed += 1
    events: dict[str, list[Event]] = collections.defaultdict(list)
    finalized_without_arrival = 0
    for event in provisional:
        arrival = arrivals.get(event.signature)
        if arrival is None:
            finalized_without_arrival += 1
            arrival = (0, "")
        events[event.mint].append(Event(
            **{**event.__dict__, "arrival_ms": arrival[0], "arrival_evidence_id": arrival[1]}
        ))
    for mint, rows in events.items():
        deduped = {(row.signature, row.instruction_index): row for row in rows}
        events[mint] = sorted(deduped.values(), key=lambda row: row.order)
    return dict(events), finalized_transactions, {
        "evidencePathN": len(paths), "rawTradeArrivalN": len(arrivals),
        "targetFinalizedEventN": len(provisional), "finalizedWithoutArrivalN": finalized_without_arrival,
        "malformedTargetTransactionN": malformed,
    }


def exclusion(candidate: dict[str, Any], reasons: list[str], detail: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema": SCHEMA + "_EXCLUSION", "classifiedAtUtc": now_utc(),
        "signature": candidate["signature"], "mint": candidate["mint"],
        "observedAtUtc": candidate["observedAtUtc"], "reasons": sorted(set(reasons)),
        "detail": detail or {},
    }


def evaluate_candidate(
    candidate: dict[str, Any],
    events: list[Event],
    transactions: dict[str, dict[str, Any]],
    ordinal: int,
    readiness: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    reasons: list[str] = []
    matching = [row for row in events if row.signature == candidate["signature"] and row.user == WALLET and row.is_buy]
    if len(matching) != 1:
        return None, ["ENTRY_SIGNATURE_EVENT_JOIN_NOT_EXACT"]
    entry = matching[0]
    if entry.arrival_ms <= 0 or not entry.arrival_evidence_id:
        reasons.append("ENTRY_REALTIME_ARRIVAL_MISSING")
    if entry.transaction_index < 0:
        reasons.append("ENTRY_CANONICAL_TRANSACTION_INDEX_MISSING")
    transaction = transactions.get(candidate["signature"])
    if transaction is None:
        return None, ["FINALIZED_ENTRY_TRANSACTION_MISSING"]
    create_received_ns = int((readiness or {}).get("createReceivedAtUnixNs") or 0)
    create_ms = create_received_ns // 1_000_000
    if create_ms <= 0 or create_ms >= entry.arrival_ms:
        reasons.append("CREATE_CAUSAL_ARRIVAL_MISSING_OR_INVALID")
    pre_balance = wallet_token_amount(transaction, "preTokenBalances", candidate["mint"])
    post_balance = wallet_token_amount(transaction, "postTokenBalances", candidate["mint"])
    if post_balance is None:
        reasons.append("POST_ENTRY_WALLET_BALANCE_UNPROVEN")
    effective_pre = 0 if pre_balance is None and post_balance is not None else pre_balance
    if effective_pre != 0:
        reasons.append("WALLET_NOT_FLAT_BEFORE_ENTRY")
    if post_balance is not None and effective_pre is not None and post_balance <= effective_pre:
        reasons.append("ENTRY_ACQUISITION_NOT_POSITIVE")
    prior = [row for row in events if row.order < entry.order]
    if any(row.signature == entry.signature for row in prior):
        reasons.append("ENTRY_TRANSACTION_LEAKS_INTO_PREENTRY")
    prior_external = [row for row in prior if row.user != WALLET]
    controls = [row for row in prior_external if row.arrival_ms < entry.arrival_ms]
    if not controls:
        reasons.append("NO_VALID_EARLIER_SAME_MINT_CONTROL")
    if any(row.arrival_ms <= 0 for row in prior):
        reasons.append("PREENTRY_CAUSAL_ARRIVAL_MISSING")
    if any(row.transaction_index < 0 for row in prior):
        reasons.append("PREENTRY_CANONICAL_TRANSACTION_INDEX_MISSING")
    if reasons:
        return None, reasons
    split = "DEVELOPMENT_CALIBRATION" if ordinal <= DEVELOPMENT_N else "UNTOUCHED_VALIDATION"
    return {
        "schema": SCHEMA + "_ELIGIBLE_ENTRY",
        "eligibleOrdinal": ordinal, "split": split,
        "mint": candidate["mint"], "entrySignature": candidate["signature"],
        "entryObservedAtUtc": candidate["observedAtUtc"],
        "candidateSource": candidate.get("candidateSource"),
        "creation": {
            "signature": (readiness or {}).get("createSignature"),
            "slot": int((readiness or {}).get("createSlot") or 0),
            "observedUnixMs": create_ms,
            "observedAtUtc": (readiness or {}).get("createReceivedAtUtc"),
        },
        "entry": entry.payload(),
        "walletBalanceProof": {
            "preRaw": int(effective_pre or 0), "postRaw": int(post_balance or 0),
            "deltaRaw": int(post_balance or 0) - int(effective_pre or 0),
            "knownFlatBefore": effective_pre == 0,
        },
        "preEntryEvents": [row.payload() for row in prior],
        "validEarlierControlStates": [row.payload() for row in controls],
        "initialCurveState": prior[0].pre_state,
        "preEntryFeatureBoundary": {
            "entryExcluded": True, "teacherEntryContribution": 0,
            "canonicalOrderStrictlyBefore": True, "arrivalStrictlyBefore": True,
        },
    }, []


def reconcile_candidates(
    root: pathlib.Path,
    exact_root: pathlib.Path,
    eligibility_mode: str,
) -> dict[str, Any]:
    candidates = read_jsonl(root / "raw-2cq-entries.jsonl")
    eligible = read_jsonl(root / "eligible-tier-a.jsonl")
    exclusions = read_jsonl(root / "exclusions.jsonl")
    terminal = {row["entrySignature"] for row in eligible} | {row["signature"] for row in exclusions}
    pending = [row for row in candidates if row["signature"] not in terminal]
    reports, mint_readiness = readiness_reports(exact_root)
    grouped: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    new_exclusions: list[dict[str, Any]] = []
    still_pending = 0
    for candidate in pending:
        readiness = mint_readiness.get(candidate["mint"])
        if readiness is None:
            if candidate["detectorPartitionKey"] in reports:
                new_exclusions.append(exclusion(candidate, ["CREATION_EVIDENCE_ABSENT_FROM_COMPLETE_PARTITIONS"]))
            else:
                still_pending += 1
            continue
        report = reports.get(readiness["partitionKey"])
        if report is None or report.get("sourceExactReplayComplete") is not True:
            still_pending += 1
            continue
        if eligibility_mode == "exact-admitted" and readiness.get("exactAdmitted") is not True:
            new_exclusions.append(exclusion(candidate, ["MINT_NOT_EXACT_ADMITTED"], {
                "readinessExclusionReasons": readiness.get("exclusionReasons") or []
            }))
            continue
        grouped[readiness["partitionKey"]].append(candidate)

    new_eligible: list[dict[str, Any]] = []
    report_scans: dict[str, Any] = {}
    next_ordinal = len(eligible) + 1
    for partition_key, group in sorted(grouped.items()):
        targets = {row["mint"] for row in group}
        try:
            events, transactions, scan = scan_report(exact_root, reports[partition_key], targets)
            report_scans[partition_key] = scan
        except Exception as error:
            for candidate in group:
                new_exclusions.append(exclusion(candidate, ["EXACT_PARTITION_SCAN_FAILED"], {"error": str(error)}))
            continue
        for candidate in sorted(group, key=lambda row: (row["observedUnixMs"], row["signature"])):
            row, reasons = evaluate_candidate(
                candidate,
                events.get(candidate["mint"], []),
                transactions,
                next_ordinal,
                mint_readiness.get(candidate["mint"]),
            )
            if row is None:
                new_exclusions.append(exclusion(candidate, reasons, {"partitionKey": partition_key}))
            else:
                new_eligible.append(row)
                next_ordinal += 1
    append_jsonl(root / "eligible-tier-a.jsonl", new_eligible)
    append_jsonl(root / "exclusions.jsonl", new_exclusions)
    return {
        "newEligible": len(new_eligible), "newExcluded": len(new_exclusions),
        "stillPending": still_pending, "reportScans": report_scans,
    }


def write_summary(root: pathlib.Path, exact_root: pathlib.Path, run: dict[str, Any]) -> dict[str, Any]:
    candidates = read_jsonl(root / "raw-2cq-entries.jsonl")
    eligible = read_jsonl(root / "eligible-tier-a.jsonl")
    exclusions = read_jsonl(root / "exclusions.jsonl")
    reasons: collections.Counter[str] = collections.Counter()
    for row in exclusions:
        reasons.update(row.get("reasons") or [])
    health_path = exact_root / "health.json"
    health = json.loads(health_path.read_text(encoding="utf-8")) if health_path.exists() else {}
    stat = os.statvfs(exact_root)
    free_bytes = stat.f_bavail * stat.f_frsize
    summary = {
        "schema": SCHEMA + "_STATUS", "atUtc": now_utc(),
        "eligibleTierA": len(eligible), "target": TARGET_N,
        "developmentEligible": min(len(eligible), DEVELOPMENT_N),
        "untouchedValidationEligible": max(0, len(eligible) - DEVELOPMENT_N),
        "raw2CqEntriesObserved": len(candidates), "excluded": len(exclusions),
        "pending": len(candidates) - len(eligible) - len(exclusions),
        "exclusionsByReason": dict(sorted(reasons.items())),
        "joinSuccessRatePct": (100.0 * len(eligible) / len(candidates)) if candidates else None,
        "collector": {
            "processAlive": health.get("processAlive"), "evidenceFlowHealthy": health.get("evidenceFlowHealthy"),
            "streamConnected": health.get("streamConnected"), "processInstanceUuid": health.get("processInstanceUuid"),
            "lastRawStreamUpdateUtc": health.get("lastRawStreamUpdateUtc"),
            "lastExactCompletePartitionUtc": health.get("lastExactCompletePartitionUtc"),
            "reconciliationBacklog": health.get("reconciliationBacklog"), "reconnects": health.get("reconnects"),
        },
        "storage": {
            "freeBytes": free_bytes, "freeGbDecimal": free_bytes / 1_000_000_000,
            "estimatedBytesPerDay": health.get("estimatedBytesPerDay"),
            "estimatedDaysRemaining": free_bytes / health["estimatedBytesPerDay"] if health.get("estimatedBytesPerDay") else None,
        },
        "partitionCompleteness": {
            "readyV4Reports": len(list((exact_root / "readiness-v1/partitions").glob("*-v4.json"))),
            "lastCompleteUtc": health.get("lastExactCompletePartitionUtc"),
        },
        "interimPerformanceInspected": False, "outcomesRead": 0, "submissions": 0,
        "lastRun": run,
        "n500Ready": len(eligible) >= TARGET_N,
    }
    atomic_json(root / "status.json", summary)
    return summary


def run(args: argparse.Namespace) -> dict[str, Any]:
    contract = initialize(
        args.output,
        args.detector,
        args.exact_root,
        args.candidate_source_label,
        args.eligibility_mode,
    )
    if contract.get("candidateSourceLabel", "legacy-jito-detected-swaps") != args.candidate_source_label:
        raise RuntimeError("CANDIDATE_SOURCE_LABEL_CHANGED_FAIL_CLOSED")
    if contract.get("eligibilityMode", "exact-admitted") != args.eligibility_mode:
        raise RuntimeError("ELIGIBILITY_MODE_CHANGED_FAIL_CLOSED")
    freeze_study_design(args.output, args.historical_script, args.historical_report)
    ingestion = ingest_candidates(args.output, args.detector, contract)
    reconciliation = reconcile_candidates(args.output, args.exact_root, args.eligibility_mode)
    summary = write_summary(args.output, args.exact_root, {"ingestion": ingestion, "reconciliation": reconciliation})
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detector", type=pathlib.Path, required=True)
    parser.add_argument("--exact-root", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--historical-script", type=pathlib.Path, required=True)
    parser.add_argument("--historical-report", type=pathlib.Path, required=True)
    parser.add_argument("--candidate-source-label", default="legacy-jito-detected-swaps")
    parser.add_argument(
        "--eligibility-mode",
        choices=("exact-admitted", "tier-a-entry"),
        default="exact-admitted",
    )
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
