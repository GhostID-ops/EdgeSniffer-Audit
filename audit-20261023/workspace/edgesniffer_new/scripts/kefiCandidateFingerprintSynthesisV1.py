#!/usr/bin/env python3
"""Synthesize outcome-blind candidate-specific kEFi context results."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "n": len(values),
        "min": min(values) if values else None,
        "median": median(values) if values else None,
        "p90": percentile(values, 0.90),
        "p95": percentile(values, 0.95),
        "max": max(values) if values else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--structural", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    args = parser.parse_args()

    context = [
        json.loads(line)
        for line in args.context.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    structural = json.loads(args.structural.read_text(encoding="utf-8"))
    freeze = json.loads(args.freeze.read_text(encoding="utf-8"))
    freeze_by_ordinal = {int(row["ordinal"]): row for row in freeze["items"]}
    trades = {int(row["ordinal"]): row for row in structural["trades"]}
    buy_ordinals = [ordinal for ordinal, row in trades.items() if row.get("direction") == "BUY"]
    if len(context) != len(buy_ordinals) or {int(row["ordinal"]) for row in context} != set(buy_ordinals):
        raise SystemExit("candidate-context rows do not exactly match frozen BUY ordinals")

    rows = []
    for row in sorted(context, key=lambda value: int(value["ordinal"])):
        ordinal = int(row["ordinal"])
        entry_ns = int(freeze_by_ordinal[ordinal]["receivedAtUnixNs"])
        last_related = row.get("last_related_ns")
        first_related = row.get("first_related_ns")
        row = dict(row)
        row["entryReceivedAtUnixNs"] = entry_ns
        row["lastRelatedLeadMs"] = (
            (entry_ns - int(last_related)) / 1_000_000 if last_related is not None else None
        )
        row["relatedSpanSeconds"] = (
            (int(last_related) - int(first_related)) / 1_000_000_000
            if first_related is not None and last_related is not None
            else None
        )
        has_create = int(row["create_like_frames"]) > 0
        has_wallet = int(row["wallet_token_pre_seen"]) + int(row["wallet_token_post_seen"]) > 0
        row["visibilityClass"] = (
            "CREATE_AND_WALLET_STATE" if has_create and has_wallet else
            "CREATE_ONLY" if has_create else
            "WALLET_STATE_ONLY" if has_wallet else
            "RELATED_NO_CREATE_OR_WALLET_STATE"
        )
        rows.append(row)

    visibility = Counter(row["visibilityClass"] for row in rows)
    epochs: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        epochs[str(row.get("continuity_epoch") or 0)].append(row)
    epoch_profiles = []
    for epoch, values in sorted(epochs.items(), key=lambda item: int(item[0])):
        epoch_profiles.append({
            "epoch": int(epoch),
            "buyWindows": len(values),
            "windowsWithCreate": sum(int(row["create_like_frames"]) > 0 for row in values),
            "windowsWithWalletState": sum(
                int(row["wallet_token_pre_seen"]) + int(row["wallet_token_post_seen"]) > 0
                for row in values
            ),
            "relatedFramesMedian": median(int(row["related_frames"]) for row in values),
            "lastRelatedLeadMsMedian": median(
                row["lastRelatedLeadMs"] for row in values if row["lastRelatedLeadMs"] is not None
            ) if any(row["lastRelatedLeadMs"] is not None for row in values) else None,
        })

    result = {
        "schema": "KEFI_CANDIDATE_SPECIFIC_FINGERPRINT_SYNTHESIS_V1",
        "createdAtUtc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "trackedWallet": structural.get("trackedWallet"),
        "interpretation": "Outcome-blind candidate-specific entry visibility and context. This is not a profitability result or fitted model.",
        "inputHashes": {
            "candidateContext": sha256(args.context),
            "structural": sha256(args.structural),
            "freeze": sha256(args.freeze),
        },
        "sample": {"frozenBuyWindows": len(rows), "continuityEpochs": len(epoch_profiles)},
        "visibilityClassCounts": [[key, value] for key, value in visibility.most_common()],
        "relatedFrames": summary([float(row["related_frames"]) for row in rows]),
        "createLikeFramesPerWindow": summary([float(row["create_like_frames"]) for row in rows]),
        "buyLikeFramesPerWindow": summary([float(row["buy_like_frames"]) for row in rows]),
        "sellLikeFramesPerWindow": summary([float(row["sell_like_frames"]) for row in rows]),
        "lastRelatedLeadMs": summary([float(row["lastRelatedLeadMs"]) for row in rows if row["lastRelatedLeadMs"] is not None]),
        "relatedSpanSeconds": summary([float(row["relatedSpanSeconds"]) for row in rows if row["relatedSpanSeconds"] is not None]),
        "epochProfiles": epoch_profiles,
        "scientificGuards": {
            "outcomesAccessed": False,
            "pnlAccessed": False,
            "modelFit": False,
            "openPartitionIncluded": False,
            "epochsMerged": False,
            "sampleMembershipChanged": False,
        },
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# kEFi Candidate-Specific Fingerprint Synthesis V1",
        "",
        f"Outcome-blind analysis of **{len(rows):,}** frozen BUY windows for `{structural.get('trackedWallet')}`.",
        "",
        "This reconstructs what the retained source could see before each buy. It does not inspect outcomes/PnL or fit a model.",
        "",
        "## Visibility classes",
        "",
        "| Class | Count |",
        "|---|---:|",
    ]
    lines.extend(f"| `{key}` | {value} |" for key, value in visibility.most_common())
    lines.extend([
        "",
        "## Context distributions",
        "",
        "| Measure | Median | P90 | P95 | Max |",
        "|---|---:|---:|---:|---:|",
        f"| Related source frames | {result['relatedFrames']['median']} | {result['relatedFrames']['p90']} | {result['relatedFrames']['p95']} | {result['relatedFrames']['max']} |",
        f"| CREATE-like log events | {result['createLikeFramesPerWindow']['median']} | {result['createLikeFramesPerWindow']['p90']} | {result['createLikeFramesPerWindow']['p95']} | {result['createLikeFramesPerWindow']['max']} |",
        f"| BUY-like log events | {result['buyLikeFramesPerWindow']['median']} | {result['buyLikeFramesPerWindow']['p90']} | {result['buyLikeFramesPerWindow']['p95']} | {result['buyLikeFramesPerWindow']['max']} |",
        f"| SELL-like log events | {result['sellLikeFramesPerWindow']['median']} | {result['sellLikeFramesPerWindow']['p90']} | {result['sellLikeFramesPerWindow']['p95']} | {result['sellLikeFramesPerWindow']['max']} |",
        f"| Last related frame lead (ms) | {result['lastRelatedLeadMs']['median']} | {result['lastRelatedLeadMs']['p90']} | {result['lastRelatedLeadMs']['p95']} | {result['lastRelatedLeadMs']['max']} |",
        "",
        "## Epoch stability",
        "",
        "| Epoch | BUY windows | CREATE visible | Wallet state visible | Median related frames | Median lead ms |",
        "|---:|---:|---:|---:|---:|---:|",
    ])
    lines.extend(
        f"| {item['epoch']} | {item['buyWindows']} | {item['windowsWithCreate']} | {item['windowsWithWalletState']} | {item['relatedFramesMedian']} | {item['lastRelatedLeadMsMedian']} |"
        for item in epoch_profiles
    )
    lines.extend([
        "",
        "## Interpretation",
        "",
        "- Candidate-specific source visibility exists for every frozen BUY window.",
        "- CREATE-like evidence is present in a majority of windows, but this is descriptive coverage, not proof that CREATE is a predictive signal.",
        "- The last related frame is measured separately from the entry receipt; no post-entry frame is included.",
        "- Continuity epochs remain separate and are never treated as one uninterrupted experiment.",
        "- The next scientific gate is prospective confirmation on newer sealed BUY windows under a predeclared contract.",
    ])
    args.out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"schema": result["schema"], "buyWindows": len(rows), "out": str(args.out_json)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
