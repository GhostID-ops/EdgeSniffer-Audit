#!/usr/bin/env python3
"""Build the human-readable 8YCB direct-create audit report and manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any


TARGET = "8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ"


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def metric(value: Any, digits: int = 4) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def pct(value: Any, digits: int = 2) -> str:
    return "N/A" if value is None else f"{100 * float(value):.{digits}f}%"


def pf(value: Any) -> str:
    return "N/A (no losses)" if value is None else f"{float(value):.3f}"


def short(value: str, size: int = 8) -> str:
    return value[:size] + "…"


def service_status(name: str) -> dict[str, Any]:
    result = subprocess.run(["systemctl", "show", name, "-p", "ActiveState", "-p", "SubState", "-p", "MainPID", "-p", "NRestarts", "--no-pager"], text=True, capture_output=True, check=False)
    values = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1); values[key] = int(value) if key in {"MainPID", "NRestarts"} and value.isdigit() else value
    values["service"] = name
    values["read_only_command_exit_code"] = result.returncode
    return values


def econ_row(label: str, row: dict[str, Any]) -> str:
    return f"| {label} | {row['n']} | {pct(row['win_rate'])} | {pf(row['profit_factor'])} | {metric(row['expectancy_net_roi_pct'])}% | {metric(row['normalized_net_pnl_sol_at_0p1'], 6)} | {row['tp']}/{row['sl']}/{row['timeout']} |"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    root = Path(args.root)
    txs = json.loads((root / "ten_createv2_transactions.json").read_text())
    flows = json.loads((root / "ten_successful_flow_and_curve.json").read_text())
    grid = json.loads((root / "ten_direct_copy_grid.json").read_text())
    history = json.loads((root / "historical_ab_economics.json").read_text())
    census = json.loads((root / "historical_createv2_census.json").read_text())
    detection = json.loads((root / "detection_latency.json").read_text())
    summary = json.loads((root / "summary.json").read_text())
    status = {"prospective_v3": service_status("edgesniffer-8ycb-prospective-shadow-v3.service"),
              "audit_process_note": "Research scripts made read-only RPC/file reads and wrote only to the isolated audit directory. No transaction submission path was invoked."}
    write_json(root / "collector_status_at_completion.json", status)

    direct = grid["25"]["178"]
    a = history["A"]["grid"]["25"]["178"]
    b = history["B"]["grid"]["25"]["178"]
    lines = [
        "# 8YCB CreateV2-only forensic + direct-launch strategy audit V1",
        "",
        f"**Final classification: `{summary['classification']}`**",
        "",
        "## Executive answer",
        "",
        f"1. **All {summary['ten_genuine_8ycb_launches']}/10 are genuine 8YCB launches.** Each finalized transaction succeeded; 8YCB is fee payer, signer, and the CreateV2 creator account. The mint is the second signer.",
        "2. **There is no same-transaction target buy because these transactions deliberately contain only CreateV2 and three outbound System transfers.** There is no Pump Buy/BuyExactSolIn, no buy TradeEvent, no target token-account credit, and no other atomic buyer. The newly minted 1,000,000,000 tokens (raw 1,000,000,000,000,000 at six decimals) go to the bonding-curve-owned token account, not 8YCB.",
        f"3. **The familiar frozen recurrent swarm did not enter:** {summary['ten_with_recurrent_wallet_1s']}/10 launches had a frozen recurrent wallet inside 1 second. Only two launches had any successful external Pump buy inside 120 seconds; the immediate HrAT buyer was not recurrent.",
        f"4. **Immediate CreateV2 copy lost money:** primary +25 ms entry / P75 178 ms sellability produced PF {pf(direct['base']['profit_factor'])}, expectancy {metric(direct['base']['expectancy_net_roi_pct'])}%, and {metric(direct['base']['normalized_net_pnl_sol_at_0p1'], 6)} SOL across ten normalized 0.1-SOL positions.",
        f"5. **Entry stress remains negative:** +5% PF {pf(direct['entry_5pct_worse']['profit_factor'])}, expectancy {metric(direct['entry_5pct_worse']['expectancy_net_roi_pct'])}%; +10% PF {pf(direct['entry_10pct_worse']['profit_factor'])}, expectancy {metric(direct['entry_10pct_worse']['expectancy_net_roi_pct'])}%.",
        f"6. **Retained historical A/B comparison:** atomic-buy A N={history['A']['launches']}, PF {pf(a['base']['profit_factor'])}, E {metric(a['base']['expectancy_net_roi_pct'])}%; Create-only B N={history['B']['launches']}, PF {pf(b['base']['profit_factor'])}, E {metric(b['base']['expectancy_net_roi_pct'])}% using the same status-corrected evaluator.",
        "7. **Requiring the atomic buy helps.** CreateV2 proves identity and launch intent, but the target's atomic capital commitment selects a materially more followed and more economic population.",
        f"8. **Direct CreateV2 detection is earlier but not enough to rescue B:** inline source-row decode was 0 ms P50/P75/P90 in these ten fixtures, versus current atomic pairing P50 {metric(detection['current_atomic_create_receipt_to_signal_ready_ms']['p50'], 3)} ms, P75 {metric(detection['current_atomic_create_receipt_to_signal_ready_ms']['p75'], 3)} ms, P90 {metric(detection['current_atomic_create_receipt_to_signal_ready_ms']['p90'], 3)} ms.",
        "",
        "## Scope and integrity",
        "",
        f"V2 census window: {census['window']['requested_start']} through {census['window']['requested_end']}; first/last successful target transaction {census['window']['first_successful_target_tx']} through {census['window']['last_successful_target_tx']}. Historical flow economics use the frozen 5.42-day retained stream overlap only. All economic mutations below are gated to RPC-confirmed successful finalized signatures; failed and unresolved signatures mutate nothing.",
        "",
        f"Status gate: {history['signature_status']['requested_unique_signatures']} unique tape signatures — " + ", ".join(f"{key}={value}" for key, value in sorted(history['signature_status']['counts'].items())) + ".",
        "",
        "## Ten finalized CreateV2 transactions",
        "",
        "| # | Mint | Finalized UTC | Signature | Slot / canonical index | Target SOL Δ | Buy? | Separate target buy ≤120s |",
        "|---:|---|---|---|---:|---:|---|---|",
    ]
    for index, (tx, flow) in enumerate(zip(txs, flows), 1):
        target_delta = next((row["delta_lamports"] for row in tx["sol_deltas"] if row["account"] == TARGET), None)
        lines.append(f"| {index} | `{flow['mint']}` | {tx['utc']} | `{tx['signature']}` | {tx['slot']} / {tx['canonical_transaction_index']} | {metric(target_delta / 1e9 if target_delta is not None else None, 9)} SOL | {'yes' if tx['any_buy_in_transaction'] else 'no'} | {'yes' if tx['separate_target_buy_within_120s'] else 'no'} |")
    common_inner = [f"{row.get('program') or short(row.get('program_id') or '')}:{row.get('parsed_type') or row.get('discriminator_hex') or 'raw'}" for row in txs[0]["inner_instructions"]]
    lines += [
        "",
        "All ten classify `GENUINE_8YCB_TOKEN_LAUNCH`. Signers are exactly `[8YCB, mint]`; 8YCB is fee payer and CreateV2 account 5 (creator). The only outer Pump instruction is discriminator `d6904cec5f8b31b4` (`CreateV2`), followed by System transfers of 0.01 SOL, approximately 0.04669 SOL, and 0.001 SOL from 8YCB. Those recipients receive funds but do not sign, fund, create, or buy, so there is no second deployer/funder account.",
        "",
        "Common inner instruction sequence (15 instructions): `" + " → ".join(common_inner) + "`. It creates and initializes the Token-2022 mint/metadata and bonding-curve token account, mints the supply to the curve-owned account, then removes mint authority.",
        "",
        "Exact outer/inner account lists, per-account SOL deltas, token deltas, and raw finalized RPC transactions are in `ten_createv2_transactions.json` and `ten_successful_activity_raw.json.gz`.",
        "",
        "## Successful post-create flow and curve path",
        "",
        "| Mint | First buyer latency | Successful trades ≤120s | Buy SOL 100/250/500/1000/2000/5000ms | MC create / 100 / 250 / 500 / 1s / 2s / 5s / 10s (SOL) | MFE / MAE | +20/+50/+100 time |",
        "|---|---:|---:|---|---|---|---|",
    ]
    for row in flows:
        windows = row["windows"]
        sols = "/".join(metric(windows[str(ms)]["external_buy_sol"], 6) for ms in [100, 250, 500, 1000, 2000, 5000])
        mc = [row["market_curve_at_create"]["market_cap_sol"]] + [row["market_curve_checkpoints"][str(ms)]["market_cap_sol"] for ms in [100, 250, 500, 1000, 2000, 5000, 10000]]
        mc_text = "/".join(metric(value, 3) for value in mc)
        latency = "none" if row["first_successful_external_buyer_latency_ms"] is None else f"{row['first_successful_external_buyer_latency_ms']} ms ({row['first_successful_external_buyer_timing_source']})"
        hits = "/".join("—" if row[f"time_to_{value}pct_ms"] is None else str(row[f"time_to_{value}pct_ms"]) + "ms" for value in [20, 50, 100])
        lines.append(f"| `{short(row['mint'])}` | {latency} | {row['successful_trade_count_120s']} | {sols} | {mc_text} | {metric(row['mfe_curve_appreciation_pct_120s'], 3)}% / {metric(row['mae_curve_appreciation_pct_120s'], 3)}% | {hits} |")
    lines += [
        "",
        "Eight launches remained at the initial 27.958993 SOL modeled market cap with no successful Pump trade. `6Mx…` received one 0.197530863-SOL buy about 24 seconds after create and the same wallet later sold. `HrAT…` received a locally observed 3.456790122-SOL buy at +18 ms, then an unobserved-by-normalizer but finalized on-chain sell in the next slot; a later 0.097777777-SOL buy was also sold. Chain-only subsecond timestamps are estimates and are labeled as such in the artifact; canonical slot/index ordering and economic state are exact.",
        "",
        "## Frozen recurrent-wallet participation",
        "",
        "| Mint | Recurrent wallets 100/250/500/1000ms | Recurrent SOL ≤1s | External SOL ≤1s | Share |",
        "|---|---|---:|---:|---:|",
    ]
    for row in flows:
        counts = "/".join(str(len(row["windows"][str(ms)]["recurrent_wallets"])) for ms in [100, 250, 500, 1000])
        rec = row["windows"]["1000"]["recurrent_sol"]
        total = row["windows"]["1000"]["external_buy_sol"]
        lines.append(f"| `{short(row['mint'])}` | {counts} | {metric(rec, 6)} | {metric(total, 6)} | {pct(rec / total) if total else 'N/A'} |")
    lines += [
        "",
        "Aggregate: 0/10 recurrent participation, 0 recurrent SOL versus 3.456790122 external SOL within one second, recurrent share 0%. The familiar cohort did not swarm these Create-only launches.",
        "",
        "## `8YCB_DIRECT_CREATE_COPY` economics",
        "",
        "Frozen evaluator: 0.1 SOL; Pump fee 125 bps each side; estimated non-Pump round-trip cost 0.00811 SOL; TP +20% gross, SL -20% gross, true 120 seconds from modeled entry; P75 sellability 178 ms after entry. PF and expectancy below are net of the frozen non-Pump cost.",
        "",
        "| Scenario | N | WR | PF | Net expectancy | Net PnL SOL | TP/SL/timeout |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for delay in [25, 50, 100, 250, 500, 1000]:
        for variant, label in [("base", "base"), ("entry_5pct_worse", "+5% entry"), ("entry_10pct_worse", "+10% entry")]:
            lines.append(econ_row(f"{delay} ms / {label}", grid[str(delay)]["178"][variant]))
    lines += [
        "",
        "Sellability sensitivity at +25 ms entry:",
        "",
        "| First-sellable | N | WR | PF | Net expectancy | Net PnL SOL | TP/SL/timeout |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for sellable in [122, 178, 346]:
        lines.append(econ_row(f"{sellable} ms", grid["25"][str(sellable)]["base"]))
    lines += [
        "",
        "## Apples-to-apples retained-stream A/B comparison",
        "",
        "Primary is +25 ms / P75 178 ms, same status-corrected evaluator and frozen recurrent-wallet set.",
        "",
        "| Measure | A: Create + atomic 8YCB buy | B: genuine CreateV2-only |",
        "|---|---:|---:|",
        f"| Launches in complete retained sample | {history['A']['launches']} | {history['B']['launches']} |",
        f"| External-buyer launches ≤120s | {history['A']['external']['launches_with_external_buy']} | {history['B']['external']['launches_with_external_buy']} |",
        f"| First-buyer P50/P75/P90 ms | {metric(history['A']['external']['first_external_buy_latency_ms']['p50'])}/{metric(history['A']['external']['first_external_buy_latency_ms']['p75'])}/{metric(history['A']['external']['first_external_buy_latency_ms']['p90'])} | {metric(history['B']['external']['first_external_buy_latency_ms']['p50'])}/{metric(history['B']['external']['first_external_buy_latency_ms']['p75'])}/{metric(history['B']['external']['first_external_buy_latency_ms']['p90'])} |",
        f"| Recurrent participation ≤1s | {history['A']['external']['launches_with_recurrent_within_1s']}/{history['A']['launches']} ({pct(history['A']['external']['recurrent_participation_rate'])}) | {history['B']['external']['launches_with_recurrent_within_1s']}/{history['B']['launches']} ({pct(history['B']['external']['recurrent_participation_rate'])}) |",
        f"| Recurrent SOL share ≤1s | {pct(history['A']['external']['recurrent_share'])} | {pct(history['B']['external']['recurrent_share'])} |",
        f"| Median gross MFE / MAE | {metric(a['base']['mfe_gross_roi_pct_p50'])}% / {metric(a['base']['mae_gross_roi_pct_p50'])}% | {metric(b['base']['mfe_gross_roi_pct_p50'])}% / {metric(b['base']['mae_gross_roi_pct_p50'])}% |",
        f"| TP / SL / timeout | {a['base']['tp']}/{a['base']['sl']}/{a['base']['timeout']} | {b['base']['tp']}/{b['base']['sl']}/{b['base']['timeout']} |",
        f"| WR | {pct(a['base']['win_rate'])} | {pct(b['base']['win_rate'])} |",
        f"| PF | {pf(a['base']['profit_factor'])} | {pf(b['base']['profit_factor'])} |",
        f"| Net expectancy | {metric(a['base']['expectancy_net_roi_pct'])}% | {metric(b['base']['expectancy_net_roi_pct'])}% |",
        f"| +5% PF / E | {pf(a['entry_5pct_worse']['profit_factor'])} / {metric(a['entry_5pct_worse']['expectancy_net_roi_pct'])}% | {pf(b['entry_5pct_worse']['profit_factor'])} / {metric(b['entry_5pct_worse']['expectancy_net_roi_pct'])}% |",
        f"| +10% PF / E | {pf(a['entry_10pct_worse']['profit_factor'])} / {metric(a['entry_10pct_worse']['expectancy_net_roi_pct'])}% | {pf(b['entry_10pct_worse']['profit_factor'])} / {metric(b['entry_10pct_worse']['expectancy_net_roi_pct'])}% |",
        f"| Target first-sell coverage / P50 | {history['A']['creator_sell_timing']['with_target_sell']}/{history['A']['launches']} / {metric(history['A']['creator_sell_timing']['delay_ms']['p50'])} ms | {history['B']['creator_sell_timing']['with_target_sell']}/{history['B']['launches']} / {metric(history['B']['creator_sell_timing']['delay_ms']['p50'])} |",
        "",
        "## Broader historical frequency and burst check",
        "",
        f"Across {census['classified_total']} unambiguous successful 8YCB CreateV2 launches: A={census['A_count']} ({pct(census['A_frequency'])}); B={census['B_count']} ({pct(census['B_frequency'])}); ambiguous multi-asset creates={census['ambiguous_count']} and are excluded from A/B.",
        "",
        "| Chronological split | N | A | B | B rate | Range |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in census["chronological_splits"]:
        lines.append(f"| {row['split']} | {row['n']} | {row['A']} | {row['B']} | {pct(row['B_rate'])} | {row['start']} → {row['end']} |")
    burst = census["B_burst_clustering"]["120"]
    lines += [
        "",
        f"B is bursty: with consecutive gaps ≤120 seconds there are {burst['groups_with_2plus']} multi-event groups containing {burst['events_in_2plus_groups']} B launches; the largest contains {burst['largest_group_n']} launches over {burst['largest_group_duration_s']} seconds. Therefore the new ten-event burst is unusual visually but not historically exceptional.",
        "",
        "Chronological primary economics for both populations are in `historical_ab_economics.json`; the same frozen thresholds were applied without tuning.",
        "",
        "## Causal detection feasibility",
        "",
        f"Create-only path: normalized Create receipt → inline creator/signature account verification → signal-ready, N={detection['direct_receipt_to_signal_ready_ms']['n']}, P50/P75/P90/max={metric(detection['direct_receipt_to_signal_ready_ms']['p50'])}/{metric(detection['direct_receipt_to_signal_ready_ms']['p75'])}/{metric(detection['direct_receipt_to_signal_ready_ms']['p90'])}/{metric(detection['direct_receipt_to_signal_ready_ms']['max'])} ms.",
        "",
        f"Current atomic path: Create receipt → same-signature target-buy decode/pair → signal-ready, N={detection['current_atomic_create_receipt_to_signal_ready_ms']['n']}, P50/P75/P90/max={metric(detection['current_atomic_create_receipt_to_signal_ready_ms']['p50'], 3)}/{metric(detection['current_atomic_create_receipt_to_signal_ready_ms']['p75'], 3)}/{metric(detection['current_atomic_create_receipt_to_signal_ready_ms']['p90'], 3)}/{metric(detection['current_atomic_create_receipt_to_signal_ready_ms']['max'], 3)} ms.",
        "",
        "Dropping the buy requirement is measurably earlier—typically only single-digit milliseconds, with one atomic pairing outlier above 100 ms. That latency benefit does not compensate for the much weaker Create-only demand/economics.",
        "",
        "## Interpretation",
        "",
        "CreateV2-only events are equivalent in authorship and token-launch reality, but **not equivalent economic signals**. The atomic 8YCB buy is not merely a parser convenience: it is public capital commitment and, in this sample, selects launches with much stronger immediate external/recurrent participation. The evidence does not support changing the frozen trigger or starting a direct-create live lane.",
        "",
        "## Limitations",
        "",
    ]
    lines.extend(f"- {item}" for item in summary["limitations"])
    lines += [
        "- The ten-event cohort is small and outcome-clustered; its negative result is corroborated by the independent retained B sample, but neither substitutes for future prospective capture.",
        "- A locally observed event has millisecond timing; chain-only events have exact finalized order/state but only block-second timing. No false millisecond precision is assigned to the latter.",
        "- Successful-chain reconstruction proves transaction effects, not wallet ownership relationships or intent among transfer recipients/followers.",
        "- The direct 0-ms processing figure begins at source receipt and does not include Shred propagation or chain finality.",
        "",
        f"Collector at completion: V3 `{status['prospective_v3'].get('ActiveState')}/{status['prospective_v3'].get('SubState')}`, PID {status['prospective_v3'].get('MainPID')}, NRestarts {status['prospective_v3'].get('NRestarts')}. It was left unchanged.",
    ]
    (root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    checks = {
        "ten_count": len(txs) == 10,
        "ten_all_finalized_success": all(row["status"] == "SUCCESS" for row in txs),
        "ten_all_target_fee_payer": all(row["fee_payer"] == TARGET for row in txs),
        "ten_all_target_signed": all(row["target_signed"] for row in txs),
        "ten_all_genuine_classification": all(row["classification"] == "GENUINE_8YCB_TOKEN_LAUNCH" for row in txs),
        "ten_no_atomic_buy": all(not row["any_buy_in_transaction"] for row in txs),
        "historical_status_unknown_zero": history["signature_status"]["counts"].get("UNKNOWN", 0) == 0,
        "summary_classification_allowed": summary["classification"] in {
            "8YCB_DIRECT_CREATE_COPY_SUPPORTED", "8YCB_DIRECT_CREATE_COPY_PROMISING_NEEDS_PROSPECTIVE_TEST",
            "8YCB_ATOMIC_BUY_FILTER_REMAINS_ECONOMICALLY_IMPORTANT", "8YCB_CREATEV2_EVENTS_NOT_EQUIVALENT_LAUNCHES",
            "8YCB_CREATEV2_FORENSIC_DATA_BLOCKER"},
        "prospective_v3_left_running": status["prospective_v3"].get("ActiveState") == "active" and status["prospective_v3"].get("SubState") == "running",
        "prospective_v3_zero_restarts": status["prospective_v3"].get("NRestarts") == 0,
    }
    manifest = {"schema": "8ycb-direct-create-copy-audit-validation-v1", "status": "PASS" if all(checks.values()) else "FAIL", "checks": checks}
    write_json(root / "validation_manifest.json", manifest)
    files = sorted(path for path in root.iterdir() if path.is_file() and path.name != "SHA256SUMS")
    checksum_lines = []
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        checksum_lines.append(f"{digest}  {path.name}")
    (root / "SHA256SUMS").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "report": str(root / "REPORT.md"), "files": len(files) + 1}, sort_keys=True))


if __name__ == "__main__":
    main()
