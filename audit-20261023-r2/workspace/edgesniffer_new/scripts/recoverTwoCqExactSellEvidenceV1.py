#!/usr/bin/env python3
"""Recover finalized 2CQ exits for the frozen N3920 26-mint cohort."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
import time
import urllib.parse
import urllib.request
from typing import Any

from buildPumpExactAlphaResearchV1 import pair_events, paths_for_report
from reconcilePumpExactEvidenceLedgerV1 import iter_frames


SCHEMA = "TWO_CQ_EXACT_SELL_EVIDENCE_RECOVERY_V1"
WALLET = "2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG"
PUMP_CURVE = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
WSOL = "So11111111111111111111111111111111111111112"


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def utc(ms: int | None) -> str | None:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat() if ms else None


def median(values: list[float | int]) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def read_env_key(path: pathlib.Path, name: str) -> str:
    for line in path.read_text(errors="ignore").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(f"missing {name} in {path}")


def fetch_wallet_history(wallet: str, key: str, oldest_seconds: int, max_pages: int = 100) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    before = None
    for page in range(max_pages):
        query = {"api-key": key, "limit": "100"}
        if before:
            query["before"] = before
        url = f"https://api-mainnet.helius-rpc.com/v0/addresses/{wallet}/transactions?{urllib.parse.urlencode(query)}"
        last_error = None
        for attempt in range(6):
            try:
                with urllib.request.urlopen(url, timeout=30) as response:
                    batch = json.load(response)
                break
            except Exception as error:
                last_error = error
                time.sleep(min(8, 0.5 * (2**attempt)))
        else:
            raise RuntimeError(f"wallet history failed page {page}: {last_error}")
        if not isinstance(batch, list) or not batch:
            break
        rows.extend(batch)
        before = str(batch[-1]["signature"])
        oldest = min(int(row.get("timestamp") or 0) for row in batch)
        print(json.dumps({"historyPage": page + 1, "rows": len(rows), "oldestUtc": utc(oldest * 1000)}), flush=True)
        if oldest <= oldest_seconds:
            break
        time.sleep(0.35)
    deduped = {str(row["signature"]): row for row in rows if row.get("signature")}
    return sorted(deduped.values(), key=lambda row: (int(row.get("timestamp") or 0), int(row.get("slot") or 0), str(row["signature"])))


def wallet_token_deltas(tx: dict[str, Any], wallet: str) -> dict[str, int]:
    deltas: dict[str, int] = collections.defaultdict(int)
    for account in tx.get("accountData") or []:
        for change in account.get("tokenBalanceChanges") or []:
            if change.get("userAccount") != wallet:
                continue
            mint = change.get("mint")
            if not mint or mint == WSOL:
                continue
            try:
                deltas[str(mint)] += int((change.get("rawTokenAmount") or {}).get("tokenAmount") or 0)
            except (TypeError, ValueError):
                continue
    return dict(deltas)


def wallet_native_delta(tx: dict[str, Any], wallet: str) -> int:
    return sum(int(account.get("nativeBalanceChange") or 0) for account in tx.get("accountData") or [] if account.get("account") == wallet)


def program_ids(tx: dict[str, Any]) -> set[str]:
    output: set[str] = set()
    stack = list(tx.get("instructions") or [])
    while stack:
        instruction = stack.pop()
        if not isinstance(instruction, dict):
            continue
        if instruction.get("programId"):
            output.add(str(instruction["programId"]))
        stack.extend(instruction.get("innerInstructions") or [])
    return output


def venue(tx: dict[str, Any]) -> str:
    programs = program_ids(tx)
    if PUMPSWAP in programs:
        return "PUMPSWAP_SELL"
    if PUMP_CURVE in programs:
        return "PUMP_CURVE_SELL"
    source = str(tx.get("source") or "UNKNOWN")
    return f"OTHER_DEX_SELL:{source}"


def fixed_actions(history: list[dict[str, Any]], cohort: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for tx in history:
        if tx.get("transactionError") is not None:
            continue
        signature = str(tx.get("signature") or "")
        timestamp_ms = int(tx.get("timestamp") or 0) * 1000
        native_delta = wallet_native_delta(tx, WALLET)
        for mint, delta in wallet_token_deltas(tx, WALLET).items():
            if mint not in cohort or timestamp_ms + 1000 < cohort[mint]["buyUnixMs"] or delta == 0:
                continue
            identity = (signature, mint)
            if identity in seen:
                continue
            seen.add(identity)
            if delta > 0:
                kind = "BUY_OR_RECEIVE" if native_delta < 0 else "TOKEN_TRANSFER_IN"
            elif native_delta > 0:
                kind = "SELL"
            else:
                kind = "TOKEN_TRANSFER_OUT"
            output[mint].append({
                "signature": signature, "timestampUnixMs": timestamp_ms, "timestampUtc": utc(timestamp_ms),
                "slot": int(tx.get("slot") or 0), "kind": kind, "tokenDeltaRaw": delta,
                "walletNativeDeltaLamports": native_delta, "feeLamports": int(tx.get("fee") or 0),
                "source": tx.get("source"), "type": tx.get("type"),
                "venue": venue(tx) if kind == "SELL" else None,
                "programIds": sorted(program_ids(tx)),
            })
    for actions in output.values():
        actions.sort(key=lambda row: (row["timestampUnixMs"], row["slot"], row["signature"]))
    return output


def scan_exact(
    evidence_root: pathlib.Path,
    prefix: list[dict[str, Any]],
    cohort: dict[str, dict[str, Any]],
    interesting_signatures: set[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    owners = {str(row["mint"]): str(row["partitionKey"]) for row in prefix if row.get("mint") in cohort}
    reports = []
    for path in sorted((evidence_root / "readiness-v1/partitions").glob("*-v4.json")):
        report = json.loads(path.read_text())
        targets = {mint for mint, partition in owners.items() if partition == report["partitionKey"]}
        if targets:
            reports.append((report, targets))
    per_mint: dict[str, Any] = {mint: {"frames": [], "decoded2CqSells": [], "decodedOtherSells": [], "sourceExactReplayComplete": None} for mint in cohort}
    signatures: dict[str, Any] = collections.defaultdict(lambda: {"transactionFrames": [], "finalizedFrames": [], "decoded": []})
    for report, targets in reports:
        for mint in targets:
            per_mint[mint]["sourceExactReplayComplete"] = report.get("sourceExactReplayComplete")
        raw_arrival: dict[str, tuple[int, str]] = {}
        raw_payload: dict[str, bytes] = {}
        finalized_payload: dict[str, tuple[dict[str, Any], bytes]] = {}
        paths = paths_for_report(evidence_root, report)
        scan_sequence = 0
        for path in paths:
            for metadata, raw in iter_frames(path, path.suffix == ".partial"):
                scan_sequence += 1
                signature = str(metadata.get("signature") or "")
                related = targets.intersection(metadata.get("discoveredMints") or [])
                observed_ms = int(metadata.get("receivedAtUnixNs") or 0) // 1_000_000
                if related:
                    for mint in related:
                        per_mint[mint]["frames"].append({
                            "kind": metadata.get("kind"), "signature": signature,
                            "observedUnixMs": observed_ms, "evidenceId": metadata.get("evidenceId"),
                            "partitionKey": report["partitionKey"],
                        })
                if not signature or signature not in interesting_signatures:
                    continue
                item = {"partitionKey": report["partitionKey"], "path": str(path), "scanSequence": scan_sequence, "observedUnixMs": observed_ms, "evidenceId": metadata.get("evidenceId"), "discoveredMints": metadata.get("discoveredMints") or [], "pumpInstructionKinds": metadata.get("pumpInstructionKinds") or []}
                if metadata.get("kind") == "transaction":
                    signatures[signature]["transactionFrames"].append(item)
                    prior = raw_arrival.get(signature)
                    candidate = (observed_ms, str(metadata.get("evidenceId") or ""))
                    if observed_ms > 0 and (prior is None or candidate < prior):
                        raw_arrival[signature] = candidate
                        raw_payload[signature] = raw
                elif metadata.get("kind") == "rpc_finalized_transaction":
                    signatures[signature]["finalizedFrames"].append(item)
                    finalized_payload[signature] = (metadata, raw)
        for signature, (metadata, raw) in finalized_payload.items():
            try:
                tx = json.loads(raw).get("result")
                if not tx or (tx.get("meta") or {}).get("err") is not None:
                    continue
                arrival = raw_arrival.get(signature)
                if arrival is None:
                    continue
                decoded = pair_events(tx, signature, arrival[1], str(metadata.get("evidenceId") or ""), arrival[0], int(metadata.get("slot") or 0), int(metadata.get("transactionIndex") or 0), set(cohort))
                for row in decoded:
                    item = {"mint": row.event.mint, "user": row.event.user, "isBuy": row.event.is_buy, "tokenAmountRaw": row.event.token_amount, "quoteAmountLamports": row.event.immediate_quote_amount, "slot": row.slot, "transactionIndex": row.tx_index, "instructionIndex": row.instruction_index, "observationEvidenceId": row.observation_evidence_id, "canonicalEvidenceId": row.canonical_evidence_id}
                    signatures[signature]["decoded"].append(item)
                    if not row.event.is_buy:
                        key = "decoded2CqSells" if row.event.user == WALLET else "decodedOtherSells"
                        per_mint[row.event.mint][key].append(item | {"signature": signature, "arrivalUnixMs": row.arrival_ms})
            except Exception as error:
                signatures[signature]["decodeError"] = str(error)
        print(json.dumps({"exactPartition": report["partitionKey"], "targets": len(targets)}), flush=True)
    for mint, data in per_mint.items():
        deduped = {(row["kind"], row["signature"], row["evidenceId"]): row for row in data["frames"]}
        data["frames"] = sorted(deduped.values(), key=lambda row: (row["observedUnixMs"], str(row["evidenceId"])))
        for key in ("decoded2CqSells", "decodedOtherSells"):
            decoded = {(row["signature"], row["instructionIndex"]): row for row in data[key]}
            data[key] = sorted(decoded.values(), key=lambda row: (row["arrivalUnixMs"], row["slot"], row["transactionIndex"], row["instructionIndex"]))
    return per_mint, dict(signatures)


def build_manifest(previous: dict[str, Any], exact_signatures: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for record in previous["records"]:
        actual = record["actual2Cq"]
        signature = actual["firstBuySignature"]
        decoded = [row for row in exact_signatures.get(signature, {}).get("decoded", []) if row.get("mint") == record["mint"] and row.get("user") == WALLET and row.get("isBuy")]
        proof = decoded[0] if decoded else {}
        rows.append({
            "mint": record["mint"], "ordinal": record["ordinal"], "buySignature": signature,
            "buyUnixMs": actual["firstBuyUnixMs"], "buyUtc": utc(actual["firstBuyUnixMs"]),
            "slot": proof.get("slot", actual["firstBuySlot"]), "transactionIndex": proof.get("transactionIndex"),
            "instructionIndex": proof.get("instructionIndex"), "entryAgeMs": actual["entryAgeMs"],
            "solBought": actual["buySol"], "tokensBoughtRaw": actual["tokensBoughtRaw"],
            "observationEvidenceId": proof.get("observationEvidenceId"),
            "canonicalEvidenceId": proof.get("canonicalEvidenceId"),
        })
    return sorted(rows, key=lambda row: row["ordinal"])


def summarize_mint(mint: str, cohort_row: dict[str, Any], actions: list[dict[str, Any]], exact: dict[str, Any], signatures: dict[str, Any], history_oldest_ms: int, history_newest_ms: int) -> dict[str, Any]:
    exact_sell_by_signature = {row["signature"]: row for row in exact["decoded2CqSells"]}
    normalized_actions = []
    for action in actions:
        normalized = dict(action)
        proof = exact_sell_by_signature.get(action["signature"])
        if proof:
            normalized["heliusTimestampUnixMs"] = normalized["timestampUnixMs"]
            normalized["timestampUnixMs"] = proof["arrivalUnixMs"]
            normalized["timestampUtc"] = utc(proof["arrivalUnixMs"])
            normalized["timestampPrecision"] = "EXACT_RAW_RECEIVE"
        else:
            normalized["timestampPrecision"] = "HELIUS_SECOND_RESOLUTION"
        normalized_actions.append(normalized)
    normalized_actions.sort(key=lambda row: (row["timestampUnixMs"], row["slot"], row["signature"]))
    actions = normalized_actions
    sells = [row for row in actions if row["kind"] == "SELL"]
    transfers = [row for row in actions if row["kind"].startswith("TOKEN_TRANSFER")]
    inventory = int(cohort_row["tokensBoughtRaw"])
    sell_rows = []
    for action in actions:
        if action["signature"] == cohort_row["buySignature"]:
            continue
        if action["slot"] < cohort_row["slot"] or action["timestampUnixMs"] < cohort_row["buyUnixMs"]:
            continue
        before = inventory
        inventory += int(action["tokenDeltaRaw"])
        if action["kind"] == "SELL":
            sold = abs(int(action["tokenDeltaRaw"]))
            sell_rows.append(action | {"inventoryBeforeRaw": before, "fractionOfThenInventory": sold / before if before > 0 else None})
        inventory = max(0, inventory)
    first_sell = sell_rows[0] if sell_rows else None
    full_exit = inventory <= max(1, int(cohort_row["tokensBoughtRaw"] * 0.001)) and bool(sell_rows) and not transfers
    exact_latest = max((int(row["observedUnixMs"]) for row in exact["frames"]), default=None)
    categories = []
    for sell in sell_rows:
        proof = signatures.get(sell["signature"], {})
        decoded = proof.get("decoded") or []
        exact_wallet_sell = any(row.get("mint") == mint and row.get("user") == WALLET and row.get("isBuy") is False for row in decoded)
        exact_any_sell = any(row.get("mint") == mint and row.get("isBuy") is False for row in decoded)
        if sell["venue"].startswith("PUMPSWAP") or sell["venue"].startswith("OTHER_DEX"):
            category = "E_UNSUPPORTED_EXIT_VENUE"
        elif proof.get("finalizedFrames") and exact_wallet_sell:
            discovered = any(mint in frame.get("discoveredMints", []) for frame in proof["finalizedFrames"])
            raw_eligible = any(
                mint in frame.get("discoveredMints", [])
                or bool(set(frame.get("pumpInstructionKinds", [])) & {"BUY", "BUY_EXACT_SOL_IN", "BUY_V2", "BUY_EXACT_QUOTE_IN_V2", "SELL", "SELL_V2"})
                for frame in proof["transactionFrames"]
            )
            finalized_first = min((frame["scanSequence"] for frame in proof["finalizedFrames"]), default=10**18)
            transaction_first = min((frame["scanSequence"] for frame in proof["transactionFrames"]), default=10**18)
            if not raw_eligible:
                category = "A_RAW_OBSERVATION_METADATA_JOIN_GAP"
            elif finalized_first < transaction_first:
                category = "H_ONE_PASS_FRAME_ORDER_JOIN_BUG"
            else:
                category = "C_MINT_JOIN_FAILED" if not discovered else "PRESENT_AND_ATTRIBUTABLE"
        elif proof.get("finalizedFrames") and exact_any_sell:
            category = "B_WALLET_ATTRIBUTION_FAILED"
        elif proof.get("finalizedFrames") or proof.get("transactionFrames"):
            category = "A_SELL_DECODER_MISSED"
        elif exact_latest is not None and sell["timestampUnixMs"] <= exact_latest:
            category = "F_ABSENT_DESPITE_FINALIZED_ONCHAIN"
        else:
            category = "D_OUTSIDE_EXACT_LEDGER_COVERAGE"
        categories.append(category)
        sell["exactJoinCategory"] = category
    if not sell_rows:
        history_covers_entry = history_oldest_ms <= cohort_row["buyUnixMs"] <= history_newest_ms
        disposition = "NO_SELL_OBSERVED" if history_covers_entry else "INSUFFICIENT_HISTORY"
    elif full_exit:
        disposition = "FULLY_CLOSED"
    else:
        disposition = "PARTIALLY_CLOSED_OR_TRANSFERRED"
    return {
        "mint": mint, "entryUnixMs": cohort_row["buyUnixMs"], "entryUtc": cohort_row["buyUtc"],
        "historyCoversEntry": history_oldest_ms <= cohort_row["buyUnixMs"] <= history_newest_ms,
        "historyLatestUtc": utc(history_newest_ms), "latestExactEvidenceUnixMs": exact_latest,
        "latestExactEvidenceUtc": utc(exact_latest),
        "exactObservedSpanAfterEntryMs": max(0, exact_latest - cohort_row["buyUnixMs"]) if exact_latest else None,
        "exact120sReplayAvailable": exact.get("sourceExactReplayComplete") is True,
        "exactCoverageProof": "partition sourceExactReplayComplete" if exact.get("sourceExactReplayComplete") is True else "UNPROVEN",
        "actions": actions, "sellCount": len(sell_rows), "sells": sell_rows,
        "firstSellLatencyMs": first_sell["timestampUnixMs"] - cohort_row["buyUnixMs"] if first_sell else None,
        "firstSellFraction": first_sell.get("fractionOfThenInventory") if first_sell else None,
        "fullExitLatencyMs": sell_rows[-1]["timestampUnixMs"] - cohort_row["buyUnixMs"] if full_exit else None,
        "remainingInventoryRaw": inventory, "disposition": disposition,
        "exitVenues": dict(collections.Counter(row["venue"] for row in sell_rows)),
        "joinCategories": dict(collections.Counter(categories)),
        "tokenTransfers": transfers,
        "exactDecoded2CqSellCount": len(exact["decoded2CqSells"]),
        "exactDecodedOtherSellCount": len(exact["decodedOtherSells"]),
    }


def write_immutable(path: pathlib.Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise RuntimeError(f"immutable output differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("previous_report", type=pathlib.Path)
    parser.add_argument("evidence_root", type=pathlib.Path)
    parser.add_argument("research_root", type=pathlib.Path)
    parser.add_argument("env_file", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    args = parser.parse_args()
    previous = json.loads(args.previous_report.read_text())
    if previous.get("boundaryOrdinal") != 3920 or previous.get("exactCorpus", {}).get("uniqueMints") != 26:
        raise RuntimeError("fixed cohort identity mismatch")
    cohort = {record["mint"]: {"mint": record["mint"], "ordinal": record["ordinal"], "buySignature": record["actual2Cq"]["firstBuySignature"], "buyUnixMs": record["actual2Cq"]["firstBuyUnixMs"], "buyUtc": utc(record["actual2Cq"]["firstBuyUnixMs"]), "tokensBoughtRaw": record["actual2Cq"]["tokensBoughtRaw"]} for record in previous["records"]}
    oldest = min(row["buyUnixMs"] for row in cohort.values()) // 1000 - 60
    history = fetch_wallet_history(WALLET, read_env_key(args.env_file, "HELIUS_API_KEY"), oldest)
    if not history or min(int(row.get("timestamp") or 0) for row in history) > oldest:
        raise RuntimeError("authoritative history did not reach fixed cohort start")
    actions = fixed_actions(history, cohort)
    interesting = {row["buySignature"] for row in cohort.values()}
    interesting.update(action["signature"] for rows in actions.values() for action in rows)
    prefix = [json.loads(line) for line in (args.research_root / "discovery-mint-index.jsonl").read_text().splitlines()[:3920]]
    exact, signatures = scan_exact(args.evidence_root, prefix, cohort, interesting)
    manifest = build_manifest(previous, signatures)
    manifest_sha = sha(manifest)
    by_mint_manifest = {row["mint"]: row for row in manifest}
    history_oldest = min(int(row.get("timestamp") or 0) for row in history) * 1000
    history_newest = max(int(row.get("timestamp") or 0) for row in history) * 1000
    mints = [summarize_mint(mint, by_mint_manifest[mint], actions.get(mint, []), exact[mint], signatures, history_oldest, history_newest) for mint in sorted(cohort, key=lambda value: cohort[value]["ordinal"])]
    venue_transaction_counts = collections.Counter()
    venue_mint_counts = collections.Counter()
    category_counts = collections.Counter()
    for row in mints:
        venue_transaction_counts.update(row["exitVenues"])
        venue_mint_counts.update(row["exitVenues"].keys())
        category_counts.update(row["joinCategories"])
        if not row["sells"]:
            venue_mint_counts[row["disposition"]] += 1
    first_latencies = [row["firstSellLatencyMs"] for row in mints if row["firstSellLatencyMs"] is not None]
    first_fractions = [row["firstSellFraction"] for row in mints if row["firstSellFraction"] is not None]
    report = {
        "schema": SCHEMA, "generatedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "classification": "2CQ_SELL_EVIDENCE_MIXED", "wallet": WALLET,
        "fixedCohortN": len(manifest), "cohortManifestSha256": manifest_sha,
        "sourceDecompositionReportSha256": hashlib.sha256(args.previous_report.read_bytes()).hexdigest(),
        "history": {"source": "HELIUS_FINALIZED_ENHANCED_HISTORY_SUPPLEMENT", "transactionsFetched": len(history), "oldestUtc": utc(history_oldest), "newestUtc": utc(history_newest), "injectedIntoOfficialLedger": False},
        "soldMintN": sum(bool(row["sells"]) for row in mints),
        "fullExitMintN": sum(row["disposition"] == "FULLY_CLOSED" for row in mints),
        "partialOrTransferMintN": sum(row["disposition"] == "PARTIALLY_CLOSED_OR_TRANSFERRED" for row in mints),
        "noSellObservedMintN": sum(row["disposition"] == "NO_SELL_OBSERVED" for row in mints),
        "venueMintCounts": dict(venue_mint_counts), "venueTransactionCounts": dict(venue_transaction_counts), "joinCategoryCounts": dict(category_counts),
        "firstSellLatencyMs": {"n": len(first_latencies), "median": median(first_latencies), "min": min(first_latencies) if first_latencies else None, "max": max(first_latencies) if first_latencies else None},
        "firstSellFraction": {"n": len(first_fractions), "median": median(first_fractions)},
        "exactSellSchedulesReconstructable": sum(len(row["sells"]) == row["exactDecoded2CqSellCount"] and bool(row["sells"]) for row in mints),
        "mints": mints,
        "integrity": {"officialLedgerModified": False, "validationRowsInspected": 0, "holdoutRowsInspected": 0, "liveSubmissions": 0},
    }
    if report["soldMintN"] == 0:
        report["classification"] = "2CQ_EXACT_SELLS_NOT_RECOVERABLE"
    elif category_counts.get("H_ONE_PASS_FRAME_ORDER_JOIN_BUG", 0) or category_counts.get("A_RAW_OBSERVATION_METADATA_JOIN_GAP", 0):
        report["classification"] = "2CQ_SELL_JOIN_BUG_FOUND_AND_FIXED_OFFLINE"
    elif report["exactSellSchedulesReconstructable"] == report["soldMintN"]:
        report["classification"] = "2CQ_EXACT_SELL_EVIDENCE_RECOVERED"
    elif category_counts and set(category_counts) == {"D_OUTSIDE_EXACT_LEDGER_COVERAGE"}:
        report["classification"] = "2CQ_SELLS_OUTSIDE_EXACT_LEDGER_COVERAGE"
    elif category_counts and all(key == "E_UNSUPPORTED_EXIT_VENUE" for key in category_counts):
        report["classification"] = "2CQ_SELLS_EXIST_BUT_EXIT_VENUE_UNSUPPORTED"
    args.output.mkdir(parents=True, exist_ok=True)
    write_immutable(args.output / "fixed-26-cohort-manifest.json", json.dumps({"schema": SCHEMA + "_COHORT", "sha256": manifest_sha, "rows": manifest}, indent=2, sort_keys=True).encode() + b"\n")
    # Only fixed-cohort-touching history is persisted; credentials are never written.
    supplement = [row for row in history if set(wallet_token_deltas(row, WALLET)).intersection(cohort)]
    supplement_payload = b"".join(canonical(row) + b"\n" for row in supplement)
    supplement_path = args.output / "historical-exit-supplement.jsonl.gz"
    if not supplement_path.exists():
        with supplement_path.open("wb") as raw:
            with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
                compressed.write(supplement_payload)
    report_payload = json.dumps(report, indent=2, sort_keys=True).encode() + b"\n"
    write_immutable(args.output / "report.json", report_payload)
    provenance = {"schema": SCHEMA + "_PROVENANCE", "cohortManifestSha256": manifest_sha, "reportSha256": hashlib.sha256(report_payload).hexdigest(), "supplementSha256": hashlib.sha256(supplement_path.read_bytes()).hexdigest(), "scriptSha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(), "validationRowsInspected": 0, "holdoutRowsInspected": 0}
    write_immutable(args.output / "provenance.json", json.dumps(provenance, indent=2, sort_keys=True).encode() + b"\n")
    print(json.dumps({key: report[key] for key in ("classification", "fixedCohortN", "soldMintN", "fullExitMintN", "exactSellSchedulesReconstructable", "venueMintCounts", "venueTransactionCounts", "joinCategoryCounts")}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
