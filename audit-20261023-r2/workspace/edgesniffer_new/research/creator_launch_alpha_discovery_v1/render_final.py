#!/usr/bin/env python3
"""Render the frozen Creator Launch Alpha Discovery V1 result.

This consumes an already-completed, immutable analysis directory.  It does not
touch any collector, sniper, service, configuration, or source evidence.
"""

from __future__ import annotations

import collections
import gzip
import hashlib
import json
import math
import pathlib
import statistics
import sys


CX = "CxCLPfEMmEoWg5wHSMFY2BLCRhRqgqRueKczpTPaTwN"
YCB = "8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ"


def read_json(path: pathlib.Path):
    return json.loads(path.read_text(encoding="utf-8"))


def pf(value):
    if value is None:
        return "NA"
    if isinstance(value, float) and math.isinf(value):
        return "Inf"
    return f"{value:.2f}"


def num(value, digits=3):
    return "NA" if value is None else f"{value:.{digits}f}"


def pct(value, digits=1):
    return "NA" if value is None else f"{100 * value:.{digits}f}%"


def cell(score, size, rank):
    return score["economics"][f"size_{size:.2f}_rank_{rank}"]


def pf_e(score, size, rank):
    row = cell(score, size, rank)
    expectation = row["expectancy_sol"]
    return f"{pf(row['profit_factor'])}/{('NA' if expectation is None else f'{expectation:+.6f}')}"


def split_line(score):
    if not score or "economics" not in score:
        return "N=0"
    return (
        f"N={score['launch_n']}; "
        + ", ".join(f"r{rank} {pf_e(score, 0.01, rank)}" for rank in range(4))
    )


def main() -> int:
    root = pathlib.Path(sys.argv[1]).resolve()
    summary = read_json(root / "summary.json")
    full = read_json(root / "creator-scorecards.json")
    discovery = read_json(root / "discovery-scorecards.json")
    validation = read_json(root / "validation-scorecards.json")
    holdout = read_json(root / "holdout-scorecards.json")
    with gzip.open(root / "launches.jsonl.gz", "rt", encoding="utf-8") as handle:
        launches = [json.loads(line) for line in handle if line.strip()]
    with gzip.open(root / "rank-economics.jsonl.gz", "rt", encoding="utf-8") as handle:
        economics = [json.loads(line) for line in handle if line.strip()]

    assert len(launches) == 1415
    assert len({row["mint"] for row in launches}) == len(launches)
    assert len({row["create_signature"] for row in launches}) == len(launches)
    assert len(economics) == len(launches) * 16
    assert all(row["finalized_success"] is True for row in launches)
    assert all(row["ordering_semantics"] == "FINALIZED_CANONICAL_SLOT_TX_IX" for row in launches)
    assert all(
        [(f["slot"], f["transaction_index"], f["instruction_index"]) for f in row["canonical_followers"]]
        == sorted((f["slot"], f["transaction_index"], f["instruction_index"]) for f in row["canonical_followers"])
        for row in launches
    )

    create_kinds = collections.Counter(row["create_kind"] for row in launches)
    self_buy_n = sum(row["creator_atomic_self_buy"] for row in launches)
    external_1 = sum(row["successful_external_buy_n"] >= 1 for row in launches)
    external_3 = sum(row["successful_external_buy_n"] >= 3 for row in launches)

    flow = []
    for ms in (10, 25, 50, 100, 250, 500, 1000, 2000, 5000, 10000):
        rows = [launch["receipt_windows"][str(ms)] for launch in launches]
        flow.append((
            ms,
            sum(row["follower_buys"] > 0 for row in rows),
            sum(row["follower_buys"] for row in rows),
            sum(row["external_buy_sol"] for row in rows),
        ))

    repeat = {wallet: row for wallet, row in full.items() if row.get("launch_n", 0) >= 2 and "economics" in row}

    def positive(row, rank, size=0.01):
        metric = cell(row, size, rank)
        return metric["exact_n"] >= 2 and (metric["expectancy_sol"] or 0) > 0

    archetypes = {
        "immediate swarm (median receipt #3 <=250 ms)": sum(
            row["median_time_to_follower_3_ms"] is not None and row["median_time_to_follower_3_ms"] <= 250
            for row in repeat.values()
        ),
        "delayed first follower (median receipt #1 >=500 ms)": sum(
            row["median_time_to_follower_1_ms"] is not None and row["median_time_to_follower_1_ms"] >= 500
            for row in repeat.values()
        ),
        "rank-0 positive but rank-2 non-positive/insufficient": sum(
            positive(row, 0) and not positive(row, 2) for row in repeat.values()
        ),
        "positive through ranks 0-3": sum(
            all(positive(row, rank) for rank in range(4)) for row in repeat.values()
        ),
        "0.5-SOL rank-2 positive with >=80% min-output pass": sum(
            positive(row, 2, 0.5) and cell(row, 0.5, 2)["capacity_min_output_pass_rate"] >= 0.8
            for row in repeat.values()
        ),
        "1.0-SOL rank-2 positive with >=80% min-output pass": sum(
            positive(row, 2, 1.0) and cell(row, 1.0, 2)["capacity_min_output_pass_rate"] >= 0.8
            for row in repeat.values()
        ),
        ">=5 observed launches": sum(row["launch_n"] >= 5 for row in repeat.values()),
    }

    candidate = full[CX]
    benchmark = full[YCB]
    candidate_launches = [row for row in launches if row["creator"] == CX]
    early_wallets = collections.defaultdict(list)
    for launch in candidate_launches:
        for follower in launch["canonical_followers"][:3]:
            early_wallets[follower["wallet"]].append((follower["rank"], follower["curve_sol"], follower["receipt_delta_ms"]))

    status_counts = collections.Counter(row["status"] for row in economics)
    validation_record = {
        "schema": "CREATOR_LAUNCH_ALPHA_DISCOVERY_V1_VALIDATION",
        "launch_rows": len(launches),
        "unique_mints": len({row["mint"] for row in launches}),
        "unique_create_signatures": len({row["create_signature"] for row in launches}),
        "rank_economic_rows": len(economics),
        "expected_rank_economic_rows": len(launches) * 16,
        "all_launches_finalized_success": all(row["finalized_success"] for row in launches),
        "canonical_follower_order_invariant": True,
        "duplicate_finalized_frames": summary["diagnostics"]["duplicate_finalized_frames"],
        "economic_status_counts": dict(sorted(status_counts.items())),
        "failed_finalized_frames_retained_as_zero_state_evidence": summary["diagnostics"]["failed_finalized_frames"],
        "result": "PASS",
    }
    (root / "validation.json").write_text(json.dumps(validation_record, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# PROMISING_OTHER_CREATORS_NEED_MORE_VALIDATION",
        "",
        "## Decision",
        "",
        f"The only new creator that survived the frozen chronological screen is `{CX}`. It has **9** exact launches: discovery **5**, validation **2**, untouched holdout **2**. Its 0.01-SOL economics stay positive through ranks 0-3 in all three slices, and its follower queue is materially slower than 8YCB's. This is a strong prospective-shadow candidate, not validated production alpha: both later slices have only two launches and all data come from one short UTC-day regime.",
        "",
        "8YCB is not displaced. In the same corpus it has only 3 launches, so neither wallet has enough like-for-like evidence for a durable winner claim. The new wallet's advantage is **rank tolerance and apparent capacity**; 8YCB's advantage is **higher observed rank-0 expectancy and much stronger outside-money impulse**.",
        "",
        "## Universe and evidence boundary",
        "",
        f"- Before exact filtering: **{summary['coverage']['fresh_mints_observed']:,}** unique Pump launch mints across four reconciled CREATE-hour partitions (05, 14, 17, and 19 UTC).",
        f"- Exact-admitted compatible launches: **{summary['coverage']['exact_admitted_n']:,}**. Conservatively analyzed after creator-consistency checks: **{len(launches):,}**.",
        f"- Exact analyzed window: **{summary['coverage']['first_launch_utc']} through {summary['coverage']['last_launch_utc']}**. This is not continuous coverage between those CREATE hours.",
        f"- Launch forms: CreateV2 **{create_kinds['CREATE_V2']:,}**; Create **{create_kinds['CREATE']:,}**. All analyzed CREATE transactions were finalized-successful.",
        f"- Atomic creator self-buy: **{self_buy_n:,}/{len(launches):,} ({100*self_buy_n/len(launches):.2f}%)**. At least one successful external buy: **{external_1:,} ({100*external_1/len(launches):.2f}%)**; at least three: **{external_3:,} ({100*external_3/len(launches):.2f}%)**.",
        "- Excluded from exact admission: 1,327/2,801 mints. Per-manifest reasons overlap: no supported exact transition 1,327; unsupported Mayhem transition 626; CREATE not finalized/missing finalized snapshot 271 each; ordering/continuity mismatch 78; reserve/fee mismatch 26; unsupported TradeEvent schema 52; missing canonical ordering 1.",
        "- A further 59 exact-admitted mints were conservatively dropped because decoded TradeEvents did not yield one consistent creator identity. Two individual transitions raised exact-curve decode rejections; they did not create duplicate launch rows.",
        f"- Evidence scan: **{summary['diagnostics']['frames_scanned']:,}** unique frames; **{summary['diagnostics']['finalized_frames_seen']:,}** finalized signatures; **{summary['diagnostics']['failed_finalized_frames']:,}** failed finalized frames retained for status accounting and given zero state mutation; duplicate finalized frames **0**.",
        "- The larger frozen archive spans 5.420034 days (220,079 CREATE observations; 3,879,386 early-flow rows; 17.4 GB), but its normalized CREATE rows omit creator identity and finalized status. It was unsuitable for trustworthy creator attribution or exact replay and was not silently mixed in.",
        "",
        "### Frozen economics",
        "",
        "Ranks 0/1/2/3; sizes 0.01/0.1/0.5/1.0 SOL; 35% degradation cap relative to that launch/size's rank-0 inventory; P75 first-sellable 178 ms; TP +20%; SL -20%; max hold 120 s. Exact integer Pump math, observed dynamic fee parameters, own price impact, and canonical success-only replay are used. A later transaction whose original constraint fails on the perturbed curve mutates zero counterfactual state. Results include 5,000-lamport base fees on buy and sell but exclude priority fee and Jito tip.",
        "",
        "## Repeat-creator population",
        "",
        f"Creators: **{summary['distribution']['total_creators']:,}**; >=2 launches **{summary['distribution']['ge_2']:,}**; >=3 **{summary['distribution']['ge_3']:,}**; >=5 **{summary['distribution']['ge_5']:,}**; >=10 **{summary['distribution']['ge_10']:,}**; >=20 **0**; >=50 **0**.",
        "",
        "`creator-scorecards.json` contains launch count, observed frequency, active span, self-buy rate/size, creator first-sell latency, follower timing/flow, recurring early wallets, and every rank/size economic cell for all creators; this report does not paste 206 repeat-wallet rows.",
        "",
        "## Universe follower-flow pulse",
        "",
        "Receipt timing is observer-local. Canonical follower rank comes only from finalized slot/transaction/instruction order; the two are never treated as interchangeable.",
        "",
        "| Receipt window | Launches with external buy | Successful buys | External curve SOL |",
        "|---:|---:|---:|---:|",
    ]
    for ms, launch_n, buy_n, sol in flow:
        lines.append(f"| {ms:,} ms | {launch_n:,} | {buy_n:,} | {sol:,.3f} |")
    lines += [
        "",
        "Canonical follower availability: #1 on 1,020 launches; #2 on 770; #3 on 641; #5 on 502; #10 on 355. Receipt timestamps were available only where an observed realtime copy joined the finalized signature; a reported 0 ms means the same millisecond bucket, not guaranteed simultaneity or landing order.",
        "",
        "## Chronological false-discovery control",
        "",
        f"- Discovery: **707** launches, {summary['chronological_split']['discovery']['first_utc']} to {summary['chronological_split']['discovery']['last_utc']}.",
        f"- Validation: **353** launches, {summary['chronological_split']['validation']['first_utc']} to {summary['chronological_split']['validation']['last_utc']}.",
        f"- Untouched holdout: **355** launches, {summary['chronological_split']['holdout']['first_utc']} to {summary['chronological_split']['holdout']['last_utc']}.",
        "",
        "The candidate screen was frozen sample-size-first: exact rank-2 N, exact rank-1 N, then a bounded rank-tolerance/flow composite, with discovery N>=2 and atomic-self-buy rate >=50%. Validation and holdout did not reorder it. Consequently, several high-N rows below are explicit negative/rejected screens rather than profitable recommendations. A post-validation positive-only rerank would be cleaner-looking but invalid.",
        "",
        "## Top 10 frozen discovery screen",
        "",
        "PF/E columns are 0.01-SOL gross strategy results.",
        "",
        "| # | Creator | D N | self-buy | median #1/#3 receipt | r0 PF/E | r1 PF/E | r2 PF/E | r3 PF/E | Later r2 | Decision |",
        "|---:|---|---:|---:|---:|---|---|---|---|---|---|",
    ]
    for index, wallet in enumerate(summary["top_10_discovery"], 1):
        d = discovery[wallet]
        v = validation.get(wallet) or {"launch_n": 0}
        h = holdout.get(wallet) or {"launch_n": 0}
        v_n = v.get("launch_n", 0)
        h_n = h.get("launch_n", 0)
        later = f"V{v_n}"
        if v_n and "economics" in v:
            later += f" {pf_e(v, 0.01, 2)}"
        later += f"; H{h_n}"
        if h_n and "economics" in h:
            later += f" {pf_e(h, 0.01, 2)}"
        validated = wallet == CX
        if validated:
            decision = "survived"
        elif v_n >= 2 and "economics" in v and (cell(v, 0.01, 2)["expectancy_sol"] or 0) <= 0:
            decision = "failed validation"
        else:
            decision = "insufficient later N"
        lines.append(
            f"| {index} | `{wallet}` | {d['launch_n']} | {pct(d['atomic_self_buy_rate'],0)} | "
            f"{num(d['median_time_to_follower_1_ms'],1)}/{num(d['median_time_to_follower_3_ms'],1)} ms | "
            + " | ".join(pf_e(d, 0.01, rank) for rank in range(4))
            + f" | {later} | {decision} |"
        )

    lines += [
        "",
        "The strongest apparent discovery wallet with meaningful N was `2J2rtMBRLxAbFkSLgrsuSNN8N5KMTJVaRTdnGfbZ5Vjw`: D N=8 and rank-2 PF 2.90/E +0.000660 SOL. Its next four launches were all rank-2 losses (PF 0, E -0.000887), a direct demonstration of the multiple-testing/regime risk. Only one wallet met the minimal later gate of validation N>=2 with positive exact rank-2 expectancy.",
        "",
        "## Validated shortlist (1 of a possible 5)",
        "",
        f"### `{CX}`",
        "",
        f"- Pattern: 9/9 CreateV2 launches with the same-transaction self-buy; median curve input **{candidate['median_self_buy_curve_sol']:.6f} SOL**.",
        f"- Frequency: 9 observed launches between {candidate['first_launch_utc']} and {candidate['last_launch_utc']} (**{candidate['launches_per_active_hour']:.2f}/active-hour span**). This is not a continuous-day rate because CREATE-hour coverage has gaps.",
        f"- Outside money: median follower #1 receipt **{candidate['median_time_to_follower_1_ms']:.1f} ms**, #3 **{candidate['median_time_to_follower_3_ms']:.1f} ms**; median 1s/5s/10s flow **{candidate['median_external_sol_1s']:.3f}/{candidate['median_external_sol_5s']:.3f}/{candidate['median_external_sol_10s']:.3f} SOL**; median followers 1s/5s/10s **{candidate['median_followers_1s']:.0f}/{candidate['median_followers_5s']:.0f}/{candidate['median_followers_10s']:.0f}**.",
        f"- Large-flow latency: median time to first 1 SOL external **{candidate['median_time_to_first_1_sol_external_ms']:.1f} ms** and first 3 SOL **{candidate['median_time_to_first_3_sol_external_ms']:.1f} ms**, among launches that reached those thresholds.",
        f"- Creator first sell: median **{candidate['median_creator_first_sell_latency_ms'] / 1000:.3f} s**.",
        "- Rank tolerance: ranks 0-3 remain positive in discovery, validation, holdout, and the full N=9 result. Rank 3 is the first capacity warning: one of nine launches fails the 35% min-output gate. No rank beyond 3 was claimed.",
        "- Trigger sufficiency: **YES**. Creator identity, mint, CreateV2, same-transaction creator buy, buy size, curve state, and transaction structure are all present in the launch frame. No later follower outcome is needed to fire this identity-based trigger.",
        "",
        "### Chronological 0.01-SOL result",
        "",
        "| Slice | N | rank0 PF/E | rank1 PF/E | rank2 PF/E | rank3 PF/E |",
        "|---|---:|---|---|---|---|",
        f"| Discovery | {discovery[CX]['launch_n']} | " + " | ".join(pf_e(discovery[CX], 0.01, rank) for rank in range(4)) + " |",
        f"| Validation | {validation[CX]['launch_n']} | " + " | ".join(pf_e(validation[CX], 0.01, rank) for rank in range(4)) + " |",
        f"| Holdout | {holdout[CX]['launch_n']} | " + " | ".join(pf_e(holdout[CX], 0.01, rank) for rank in range(4)) + " |",
        f"| Full | {candidate['launch_n']} | " + " | ".join(pf_e(candidate, 0.01, rank) for rank in range(4)) + " |",
        "",
        "Validation and holdout PF are infinite because both launches in each slice won; that is tiny-N behavior, not certainty.",
        "",
        "### Full exact size/rank scorecard",
        "",
        "Each cell is pass/exact N, TP-SL-maxhold, PF, expectancy, total PnL, and median ROI.",
        "",
        "| Size | Rank | pass/exact | TP-SL-H | PF | E SOL | total SOL | median ROI |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for size in (0.01, 0.1, 0.5, 1.0):
        for rank in range(4):
            row = cell(candidate, size, rank)
            lines.append(
                f"| {size:.2f} | {rank} | {row['capacity_min_output_pass_n']}/{row['exact_n']} of {row['n']} | "
                f"{row['tp_n']}-{row['sl_n']}-{row['maxhold_n']} | {pf(row['profit_factor'])} | "
                f"{num(row['expectancy_sol'],6)} | {row['total_gross_pnl_sol']:+.6f} | {num(row['median_roi_pct'],2)}% |"
            )

    lines += [
        "",
        "At 0.5 and 1.0 SOL, full rank-2 PF/E are 3.12/+0.042061 and 2.30/+0.061650 SOL respectively, with 9/9 min-output pass. Those are counterfactual gross strategy results over N=9, not proof that production delivery or realized exits can support those sizes. At 1 SOL, only 4/9 rank-2 launches hit TP and five max-held; discovery-only 1-SOL rank-2 was negative. Capacity therefore remains provisional.",
        "",
        "### Recurring early competition",
        "",
        f"The first three canonical positions contain {candidate['first3_unique_wallets']} unique wallets across {candidate['first3_follower_observations']} observations. The top single wallet owns {100*candidate['first3_top_wallet_share']:.1f}%—lower concentration than 8YCB's 33.3%, but a recurring pack clearly exists.",
        "",
        "| Wallet | first-3 positions | typical curve SOL | observed ranks |",
        "|---|---:|---:|---|",
    ]
    for wallet, observations in sorted(early_wallets.items(), key=lambda item: (-len(item[1]), -sum(row[1] for row in item[1])))[:6]:
        lines.append(
            f"| `{wallet}` | {len(observations)} | {statistics.median(row[1] for row in observations):.6f} | "
            + ",".join(str(row[0]) for row in observations) + " |"
        )

    lines += [
        "",
        "## Like-for-like 8YCB benchmark",
        "",
        f"8YCB has only **{benchmark['launch_n']}** compatible launches here, all CreateV2 + atomic 3.0-SOL self-buy. Median follower #1 is the same millisecond bucket, #3 is **{benchmark['median_time_to_follower_3_ms']:.1f} ms**, 1-second external flow is **{benchmark['median_external_sol_1s']:.3f} SOL**, and creator first sell is **{benchmark['median_creator_first_sell_latency_ms']/1000:.3f} s**. Two recurring wallets each occupy 3/9 first-three positions with typical size 1.5 SOL.",
        "",
        "| Creator | N | observed frequency | median self-buy | #1/#3 receipt | 1s external SOL | creator first sell | 0.01 r0/r1/r2/r3 PF/E | 0.5 r2 PF/E | 1.0 r2 PF/E |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|---|",
        f"| CxCL... | {candidate['launch_n']} | {candidate['launches_per_active_hour']:.2f}/h | {candidate['median_self_buy_curve_sol']:.3f} | {candidate['median_time_to_follower_1_ms']:.0f}/{candidate['median_time_to_follower_3_ms']:.0f} ms | {candidate['median_external_sol_1s']:.3f} | {candidate['median_creator_first_sell_latency_ms']/1000:.3f}s | " + "; ".join(pf_e(candidate,0.01,r) for r in range(4)) + f" | {pf_e(candidate,0.5,2)} | {pf_e(candidate,1.0,2)} |",
        f"| 8YCB | {benchmark['launch_n']} | {benchmark['launches_per_active_hour']:.2f}/h | {benchmark['median_self_buy_curve_sol']:.3f} | {benchmark['median_time_to_follower_1_ms']:.0f}/{benchmark['median_time_to_follower_3_ms']:.0f} ms | {benchmark['median_external_sol_1s']:.3f} | {benchmark['median_creator_first_sell_latency_ms']/1000:.3f}s | " + "; ".join(pf_e(benchmark,0.01,r) for r in range(4)) + f" | {pf_e(benchmark,0.5,2)} | {pf_e(benchmark,1.0,2)} |",
        "",
        "On this strict slice, 8YCB is a powerful but rank-fragile immediate swarm: 0.01-SOL rank 0 is 3/3 TP (PF Inf, E +0.002988), while ranks 1/2/3 are negative. CxCL has lower rank-0 expectancy (+0.001325) but remains positive at ranks 1-3 and has much slower competition. The comparison is not decisive because N=3 versus N=9 is too small and the broader historical 8YCB work is not silently merged into this exact same-window table.",
        "",
        "## Data-defined archetypes among 206 repeat creators",
        "",
    ]
    for label, count in archetypes.items():
        lines.append(f"- {label}: **{count}** creators.")
    lines += [
        "",
        "These categories overlap. Only the CxCL identity survived the frozen later-launch gate. Atomic self-buy by itself is not a useful archetype discriminator because 90.25% of the compatible universe does it.",
        "",
        "## Direct strategic answers",
        "",
        "1. **Is 8YCB unusual?** Yes in speed and intensity: same-millisecond first followers, 60 ms median #3, 6.47 SOL external in 1 second, and rapid creator selling. No in the mere presence of an atomic self-buy, which occurs on 90.25% of launches.",
        "2. **Stronger exact rank-0 economics?** No validated creator beats 8YCB's same-corpus 0.01-SOL rank-0 expectancy. Several N=2-3 discovery wallets look higher, but none has adequate later confirmation.",
        "3. **Better rank tolerance?** Yes, CxCL is the clearest lead: positive ranks 0-3 in discovery, validation, holdout, and full results, whereas 8YCB's same-corpus rank 1-3 results are negative.",
        "4. **Less immediate competition?** Yes. CxCL's median #1/#3 receipt is 108/1,382.5 ms versus 8YCB's same-ms/60 ms, with 0.318 versus 6.470 SOL in the first second. This is less queue pressure, but also weaker immediate causal demand.",
        "5. **Better commercial capacity?** CxCL appears better in the counterfactual grid through 1 SOL and rank 2, but N=9, max-hold dependence at 1 SOL, and excluded delivery costs make that provisional.",
        "6. **Best target now?** CxCL is the best **new research target** because it trades some headline impulse for a much wider profitable queue window. 8YCB remains the better-engineered and more prospectively observed live target.",
        "7. **Best opportunity shape?** A small identity portfolio beginning with 8YCB plus prospective CxCL observation—not a generic atomic-buy filter. The generic structure is far too common, and one new wallet is not enough to prove a durable archetype.",
        "8. **More promising than shaving one more 8YCB queue position?** For marginal research time, yes: validating CxCL across more days has higher information value because its observed edge survives ranks 1-3. For production capital, no conclusion yet; no new sniper or trigger should be deployed from N=9.",
        "",
        "## Important limitations",
        "",
        "- Only four non-contiguous CREATE-hour partitions from one UTC day are exact-ready. Global 50/25/25 splitting prevents look-ahead but does not create independent market regimes.",
        "- Validation and holdout for the sole survivor are N=2 each. Infinite PF is an artifact of no losses in two observations.",
        "- Multiple comparisons are severe: 883 creators and 206 repeats were scanned. The 2J2 failure shows why discovery results cannot be trusted alone.",
        "- Receipt timing is provider-local and millisecond-quantized here. It supports causal observation comparisons, not canonical landing guarantees.",
        "- Counterfactual insertion changes curve state; later original transactions that would fail constraints are zero-state. That is internally exact but remains a model of alternate chain history.",
        "- Priority fee, Jito tip, route latency, failures to land, and real sell competition are excluded. Especially at 0.01 SOL, delivery cost can overwhelm gross strategy PnL.",
        "- The 5.42-day normalized archive cannot supply creator identities/finalized exact states, so it cannot extend durability claims.",
        "- Unsupported Mayhem/non-SOL quote variants and inconsistent creator identities are excluded rather than approximated; conclusions do not generalize to them.",
        "",
        "## Integrity and artifacts",
        "",
        "Validation passed: 1,415 unique mint/signature launch rows, 22,640 rank-size rows (1,415 x 4 ranks x 4 sizes), canonical follower-order invariant, zero duplicate finalized frames, and failed finalized evidence excluded from state mutation. Source-file hashes and output hashes are preserved.",
        "",
        "Artifacts: `summary.json`, `launches.jsonl.gz`, `rank-economics.jsonl.gz`, `creator-scorecards.json`, `discovery-scorecards.json`, `validation-scorecards.json`, `holdout-scorecards.json`, `validation.json`, and `SHA256SUMS.json`.",
        "",
        "No sniper, collector, service, configuration, wallet, trigger, fee, or live lane was modified by this research task. This task submitted no transaction; the separately authorized Shot #2 lane was allowed to proceed independently.",
    ]

    report_text = "\n".join(lines) + "\n"
    (root / "FINAL_REPORT.md").write_text(report_text, encoding="utf-8")

    checksums = {}
    for path in sorted(root.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS.json":
            checksums[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (root / "SHA256SUMS.json").write_text(json.dumps(checksums, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Publish an unambiguous pointer at the research root. Earlier development
    # renders are retained for auditability but must not be mistaken for final.
    if root.name == "run-v3":
        authority = {
            "schema": "CREATOR_LAUNCH_ALPHA_DISCOVERY_V1_AUTHORITY",
            "authoritative_run": "run-v3",
            "authoritative_report": "run-v3/FINAL_REPORT.md",
            "report_sha256": checksums["FINAL_REPORT.md"],
            "rejected_intermediate_outputs": ["top-level pre-run-v3 analysis files", "run-v2"],
            "reason": "run-v3 fixes realtime-signature-to-finalized-mint joining and is the validated 1,415-launch result",
        }
        (root.parent / "FINAL_REPORT.md").write_text(report_text, encoding="utf-8")
        (root.parent / "AUTHORITATIVE_RUN.json").write_text(
            json.dumps(authority, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps({"report": str(root / 'FINAL_REPORT.md'), "validation": validation_record, "sha256_files": len(checksums)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
