#!/usr/bin/env python3
"""Render the final human-readable 8YCB V3 forensic report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def pct(value: float) -> str:
    return f"{100 * value:.2f}%"


def econ_cell(row: dict) -> str:
    return f"WR {pct(row['wr'])}, PF {row['profit_factor']:.2f}, E {row['expectancy_roi_pct']:+.2f}%"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v3", required=True)
    args = parser.parse_args()
    base = Path(args.v3)
    s = json.loads((base / "v3_summary.json").read_text())
    cov = s["coverage"]
    pnl = s["native_pnl_decomposition"]
    native = pnl["native_closed_economics"]
    open_zero = pnl["open_inventory_zero_value_stress"]
    exits = s["target_exit_timing"]
    target_arrival = s["external_buyer_arrivals"]["target"]
    generic_arrival = s["external_buyer_arrivals"]["time_matched_generic"]
    repeat = s["repeat_follower_cohorts"]
    fs = s["first_sellable"]
    follow = s["follower_economics"]
    collisions = s["target_sell_collisions"]
    reflex = s["reflexivity"]
    survival = reflex["external_demand_survival_after_target_first_sell"]
    meta = s["metadata_generalization"]

    target_latency_rows = []
    generic_latency_rows = []
    entry_rows = []
    safe_rows = []
    for delay in [25, 50, 100, 250, 500, 1000]:
        base_row = follow["target"][f"delay_{delay}ms"]
        stress = follow["target"][f"delay_{delay}ms_entry_5pct_worse"]
        generic = follow["time_matched_generic"][f"delay_{delay}ms"]
        generic_stress = follow["time_matched_generic"][f"delay_{delay}ms_entry_5pct_worse"]
        target_latency_rows.append(
            f"| {delay} | {pct(base_row['wr'])} | {base_row['profit_factor']:.2f} | {base_row['expectancy_roi_pct']:+.2f}% | "
            f"{pct(stress['wr'])} | {stress['profit_factor']:.2f} | {stress['expectancy_roi_pct']:+.2f}% |"
        )
        generic_latency_rows.append(
            f"| {delay} | {pct(generic['wr'])} | {generic['profit_factor']:.2f} | {generic['expectancy_roi_pct']:+.2f}% | "
            f"{generic_stress['profit_factor']:.2f} | {generic_stress['expectancy_roi_pct']:+.2f}% |"
        )
        degradation = base_row["entry_degradation_vs_atomic_pct"]
        entry_rows.append(
            f"| {delay} | {base_row['median_entry_average_price_sol_per_token']:.10g} | {degradation['p50']:+.2f}% | {degradation['p90']:+.2f}% | {base_row['median_start_roundtrip_roi_pct']:+.2f}% |"
        )
        safe_rows.append(
            f"| {delay} | {econ_cell(follow['target'][f'delay_{delay}ms_safe_p50'])} | "
            f"{econ_cell(follow['target'][f'delay_{delay}ms_safe_p75'])} | "
            f"{econ_cell(follow['target'][f'delay_{delay}ms_safe_p90'])} |"
        )
    top_wallet_rows = []
    for row in repeat["top_repeated_wallets"][:10]:
        sell_delay = "n/a" if row["median_observed_sell_delay_ms"] is None else f"{row['median_observed_sell_delay_ms']:.0f}"
        top_wallet_rows.append(
            f"| `{row['wallet']}` | {row['target_launches_bought_within_1s']} | {row['median_delay_ms']:.0f} | "
            f"{row['gross_buy_sol_within_1s']:.3f} | {row['target_launches_sold_within_120s']} | {sell_delay} |"
        )
    model_rows = []
    for name in ["metadata_content_only", "atomic_action_only", "metadata_plus_atomic_action"]:
        model = meta["chronological_models"][name]
        model_rows.append(
            f"| {name} | {model['splits']['train']['roc_auc']:.3f} | {model['splits']['validation']['roc_auc']:.3f} | {model['splits']['holdout']['roc_auc']:.3f} |"
        )
    daily_rows = []
    for day, values in follow["target_daily_250ms"].items():
        base_day = values["base"]
        stress_day = values["entry_5pct_worse"]
        base_pf = "n/a" if base_day["profit_factor"] is None else f"{base_day['profit_factor']:.2f}"
        stress_pf = "n/a" if stress_day["profit_factor"] is None else f"{stress_day['profit_factor']:.2f}"
        daily_rows.append(
            f"| {day} | {base_day['n']} | {base_pf} | {base_day['expectancy_roi_pct']:+.2f}% | "
            f"{stress_pf} | {stress_day['expectancy_roi_pct']:+.2f}% |"
        )

    actual = reflex["250ms_entry_p50_safe_counterfactuals"]["actual_tape"]
    no_target_sells = reflex["250ms_entry_p50_safe_counterfactuals"]["external_demand_no_8ycb_sells"]
    no_repeat = reflex["250ms_entry_p50_safe_counterfactuals"]["without_repeat_early_cohort"]
    top_meta = meta["generic_outcome_generalization_at_250ms"]["top_metadata_score_quintile"]
    rest_meta = meta["generic_outcome_generalization_at_250ms"]["bottom_four_quintiles"]

    report = f"""# 8YCB Causal Flow + Third-Party Followability Audit V3

## 1. Executive conclusion

8YCB's public self-CREATE plus atomic initial buy is followed by unusually fast, repeatable external demand and retains positive **curve-only** follower economics through several latency assumptions. The edge is materially reduced by 8YCB's own rapid selling and by a +5% entry penalty. The evidence supports a prospective no-submit shadow, not implementation or live trading.

The signal is primarily **8YCB identity + atomic initial-buy action**, with reflexive/recurrent follower attention. Metadata content alone generalizes weakly, and generic CREATE structure is substantially worse.

## 2. Exact coverage and integrity

- Native V2 ledger window: `{cov['v2_native_window_start']}` through `{cov['v2_native_window_end']}`.
- Frozen V3 stream: `{cov['frozen_stream_start']}` through `{cov['frozen_stream_end']}` — **{cov['frozen_stream_duration_days']:.4f} days**, exactly **{cov['frozen_stream_bytes']:,} bytes**.
- Full frozen universe: **{cov['all_observed_create_rows']:,} CREATEs** and **{s['stream_integrity']['compact_rows_scanned']:,}** compact rows.
- Complete target horizons: **{cov['complete_target_120s_horizons']}**. Time-matched generic controls: **{cov['complete_time_matched_generic_120s_horizons']:,}**, five nearest controls per target before excluding one 8YCB-authored control.
- Generic match timing: median 2.595 seconds, maximum 41.074 seconds from its target.
- Nine selected flow rows above 100 SOL were treated as decoder/sentinel anomalies and excluded. No >1.1B-token sell rows were found.
- The native stream omitted routed 8YCB sells. **{s['stream_integrity']['target_sell_rows_injected_from_native_ledger']}** native sell deltas were therefore replayed at the midpoint of their block second; collision timing has about ±500 ms uncertainty.

The 5.42-day stream is below the requested seven-day completeness target. Results are a bounded sample, not a complete-period proof.

## 3. Native PnL decomposition and PF integrity

- Gross curve-side/tracker-comparable closed PnL: **{pnl['gross_curve_side_tracker_comparable_pnl_sol']:+.6f} SOL**.
- Native realized closed PnL: **{pnl['native_realized_pnl_sol']:+.6f} SOL**.
- Gross-to-native drag: **{pnl['gross_to_native_cost_drag_sol']:.6f} SOL**, including **{pnl['closed_position_network_fees_sol']:.6f} SOL** known network fees and **{pnl['residual_drag_pump_tips_rent_and_other_lamport_flows_sol']:.6f} SOL** residual Pump/tip/rent/other lamport flows.
- Closed positions: **{native['n']:,}**; native WR **{pct(native['wr'])}**; PF **{native['profit_factor']:.2f}**; expectancy **{native['expectancy_sol']:+.6f} SOL**; median **{native['median_trade_sol']:+.6f} SOL**.
- Unresolved: **{pnl['unresolved_positions']}** positions, **{pnl['unresolved_native_cash_pnl_sol']:+.6f} SOL** cash PnL on **{pnl['unresolved_native_cost_sol']:.6f} SOL** cost.
- If unresolved inventory is valued at zero: **{open_zero['total_pnl_sol']:+.6f} SOL**, WR **{pct(open_zero['wr'])}**, PF **{open_zero['profit_factor']:.2f}**, max drawdown **{open_zero['max_drawdown_sol']:.3f} SOL**.
- Counting every unresolved position as a loss gives WR **{pct(pnl['all_unresolved_counted_as_losses_wr'])}**.

The enormous closed-only PF is real under the reconstructed ledger but is not a follower PF and is strongly flattered by excluding unresolved inventory. The zero-inventory stress is the safer integrity view.

## 4. 8YCB exit timing

- First sell: P10/P50/P75/P90/P99 = **{exits['hold_to_first_sell_seconds']['p10']:.0f}/{exits['hold_to_first_sell_seconds']['p50']:.0f}/{exits['hold_to_first_sell_seconds']['p75']:.0f}/{exits['hold_to_first_sell_seconds']['p90']:.0f}/{exits['hold_to_first_sell_seconds']['p99']:.0f} seconds**.
- Last sell: P10/P50/P75/P90/P99 = **{exits['hold_to_last_sell_seconds']['p10']:.0f}/{exits['hold_to_last_sell_seconds']['p50']:.0f}/{exits['hold_to_last_sell_seconds']['p75']:.0f}/{exits['hold_to_last_sell_seconds']['p90']:.0f}/{exits['hold_to_last_sell_seconds']['p99']:.0f} seconds**.
- **{pct(exits['first_sell_cumulative_rates']['5'])}** begin selling by 5 seconds; **{pct(exits['first_sell_cumulative_rates']['10'])}** by 10 seconds.
- Exit classes: `{exits['exit_archetypes']}`.

This is a very short creator-distribution window. Third parties must acquire and become safely sellable before a material fraction of target exits.

## 5. External buyer arrival

For 8YCB launches, first external buy delay P10/P50/P75/P90 is **{target_arrival['first_external_buy_delay_ms']['p10']:.0f}/{target_arrival['first_external_buy_delay_ms']['p50']:.0f}/{target_arrival['first_external_buy_delay_ms']['p75']:.0f}/{target_arrival['first_external_buy_delay_ms']['p90']:.0f} ms**. External demand appears on **{pct(target_arrival['window_rates']['50'])}** by 50 ms, **{pct(target_arrival['window_rates']['100'])}** by 100 ms, and **{pct(target_arrival['window_rates']['250'])}** by 250 ms. Mean gross external buy flow is **{target_arrival['window_mean_gross_buy_sol']['1000']:.3f} SOL** in the first second.

Time-matched generic launches are much weaker: first-buy median **{generic_arrival['first_external_buy_delay_ms']['p50']:.0f} ms**, only **{pct(generic_arrival['window_rates']['100'])}** with a buy by 100 ms and **{pct(generic_arrival['window_rates']['1000'])}** by 1 second, with **{generic_arrival['window_mean_gross_buy_sol']['1000']:.3f} SOL** mean first-second flow.

All measured external demand follows the public CREATE/atomic-buy boundary and cannot explain 8YCB's private decision to launch.

## 6. Repeat follower/copy-like cohort and reflexivity

- **{repeat['repeat_cohort_wallets']}** wallets bought at least five distinct 8YCB launches within one second.
- This cohort appears within one second on **{pct(repeat['target_launches_with_repeat_cohort_rate'])}** of 8YCB targets versus **{pct(repeat['generic_controls_with_same_cohort_rate'])}** of time-matched generic controls.
- It supplies **{pct(reflex['early_1s_repeat_cohort_gross_buy_sol_share'])}** of target first-second gross external buy SOL.

| Wallet | Target launches bought ≤1s | Median buy delay ms | Buy SOL ≤1s | Launches sold ≤120s | Median observed sell delay ms |
|---|---:|---:|---:|---:|---:|
{chr(10).join(top_wallet_rows)}

These are behavioral recurrence labels, not proof that a wallet is a copybot, owned by one entity, or coordinated.

At a +250 ms entry with P50 safe-sell timing, actual replay is **{econ_cell(actual)}**. Removing 8YCB's sells improves it to **{econ_cell(no_target_sells)}**, demonstrating that creator distribution materially compresses third-party returns. Removing the recurrent early cohort produces **{econ_cell(no_repeat)}**; the improvement means this cohort is not the sole positive-alpha source and its later selling is net harmful in this replay.

## 7. Metadata generalization and signal attribution

| Model, trained on earliest 50% only | Train AUC | Validation AUC | Holdout AUC |
|---|---:|---:|---:|
{chr(10).join(model_rows)}

- Creator identity is deterministic: 8YCB on 100% of targets and **{pct(meta['identity_feature']['generic_creator_is_8ycb_rate'])}** after exclusion in the final generic sample; one raw 8YCB-authored match was removed from generic economics.
- Atomic-buy action/size generalizes much better than text metadata, reaching holdout AUC **{meta['chronological_models']['atomic_action_only']['splits']['holdout']['roc_auc']:.3f}**.
- Text-only metadata is near chance chronologically (holdout AUC **{meta['chronological_models']['metadata_content_only']['splits']['holdout']['roc_auc']:.3f}**).
- `metadata.j7tracker.io` occurs on **{pct(meta['metadata_host']['target_j7tracker_rate'])}** of targets versus **{pct(meta['metadata_host']['generic_j7tracker_rate'])}** of controls, but this is infrastructure/identity evidence, not independent narrative quality.
- Among generic controls at +250 ms, the top target-like text-score quintile is **{econ_cell(top_meta)}** versus **{econ_cell(rest_meta)}** for the other four quintiles. This is modest outcome lift, not a stable standalone selector.

Conclusion: the event signal is **mixed, dominated by 8YCB identity and atomic initial-buy size/action, with a reflexive/recurrent follower response**. Generic CREATE structure is weak; content metadata is secondary.

## 8. Follower-entry reconstruction

All simulations use a 0.1 SOL curve entry after observing the public event, 125 bps Pump fees, frozen +20/-20/120s exits, and injected native target sells. They exclude network fees, priority/tips, contention, failed landing, and account-creation costs.

| Delay ms | Median entry price SOL/token | Median degradation vs 8YCB | P90 degradation | Median immediate round-trip ROI |
|---:|---:|---:|---:|---:|
{chr(10).join(entry_rows)}

## 9. Frozen follower economics and +5% stress

| Delay ms | WR | PF | Expectancy | +5% WR | +5% PF | +5% expectancy |
|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(target_latency_rows)}

At 0.1 SOL per trade, unstressed cumulative curve PnL across the 219 targets is **{follow['target']['delay_250ms']['total_pnl_sol_at_0p1_sol']:+.4f} SOL** at +250 ms and **{follow['target']['delay_1000ms']['total_pnl_sol_at_0p1_sol']:+.4f} SOL** at +1 second. These are replay totals, not live returns.

At +250 ms, the top five winners contribute **{pct(follow['target']['delay_250ms']['top5_positive_pnl_share'])}** of gross positive PnL and the top five losses contribute **{pct(follow['target']['delay_250ms']['top5_loss_share'])}** of gross loss; max chronological drawdown at 0.1 SOL is **{follow['target']['delay_250ms']['max_drawdown_sol_at_0p1_sol']:.4f} SOL**.

| UTC day | n | +250 ms PF | Expectancy | +5% PF | +5% expectancy |
|---|---:|---:|---:|---:|---:|
{chr(10).join(daily_rows)}

## 10. Empirical P50/P75/P90 first-sellable results

Independent Rust dust telemetry contains **{fs['unique_safe_first_sellable_trades']}** unique safe-first-sellable observations: P50 **{fs['p50_rust_accepted_to_first_sellable_ms']:.0f} ms**, P75 **{fs['p75_rust_accepted_to_first_sellable_ms']:.0f} ms**, P90 **{fs['p90_rust_accepted_to_first_sellable_ms']:.0f} ms** from Rust acceptance. These observations are from other launches, not 8YCB trades.

| Entry delay | P50 safe result | P75 safe result | P90 safe result |
|---:|---|---|---|
{chr(10).join(safe_rows)}

The safe-sell floor is `max(entry delay, empirical percentile)`. Some short-delay results improve because inability to exit immediately avoids an early stop and captures a later price gap; that path dependence is another reason prospective shadow validation is required.

## 11. Target-sell collision and external-demand survival

- Target has begun selling by follower entry on **{pct(collisions['by_entry_delay_ms']['25']['target_sell_at_or_before_entry_rate'])}** at +25 ms, **{pct(collisions['by_entry_delay_ms']['250']['target_sell_at_or_before_entry_rate'])}** at +250 ms, **{pct(collisions['by_entry_delay_ms']['500']['target_sell_at_or_before_entry_rate'])}** at +500 ms, and **{pct(collisions['by_entry_delay_ms']['1000']['target_sell_at_or_before_entry_rate'])}** at +1 second.
- Target sells before the simulated follower exit on **{pct(collisions['by_entry_delay_ms']['250']['target_sell_at_or_before_exit_rate'])}** at +250 ms and **{pct(collisions['by_entry_delay_ms']['1000']['target_sell_at_or_before_exit_rate'])}** at +1 second.
- From a +25 ms entry, target has sold before P50/P75/P90 safe-sell time on **{pct(collisions['safe_first_sellable_from_25ms_entry']['P50']['target_sell_at_or_before_first_sellable_rate'])} / {pct(collisions['safe_first_sellable_from_25ms_entry']['P75']['target_sell_at_or_before_first_sellable_rate'])} / {pct(collisions['safe_first_sellable_from_25ms_entry']['P90']['target_sell_at_or_before_first_sellable_rate'])}**.
- Among **{survival['target_launches_with_known_sell_within_120s']}** targets with a known sell, **{pct(survival['launch_rate_with_external_buy_after_target_first_sell'])}** still receive an external buy afterward. Post-first-sell buys account for **{pct(survival['post_first_sell_share_of_external_gross_buy_sol'])}** of external gross buy SOL in those 120-second tapes.

Demand survives the first creator sell frequently, but actual PF is far lower than the no-creator-sell counterfactual. Third-party alpha is therefore exposed to creator distribution even when external flow persists.

## 12. Generic-launch benchmark and third-party feasibility

| Delay ms | Generic WR | Generic PF | Generic expectancy | Generic +5% PF | Generic +5% expectancy |
|---:|---:|---:|---:|---:|---:|
{chr(10).join(generic_latency_rows)}

8YCB-tagged events substantially outperform generic CREATEs, especially below 250 ms. At +250/+500 ms, the target result remains positive but becomes marginal under +5% entry stress. At +1 second, stressed target economics are negative. The public event is therefore latency-sensitive and cannot be treated as executable alpha from this replay alone.

## 13. Important limitations

""" + "".join(f"- {item}\n" for item in s["limitations"]) + f"""

## 14. Final classification

`{s['final_classification']}`

Reason: the identity/atomic-action event creates a strong, immediate external-demand differential and positive latency replays after adding native creator sells. However, the sample is only 5.42 days, the +250/+500 ms +5% stress margin is thin, +1 second stress is negative, sell times are only block-second precise, and no prospective landing/account-ready shadow exists. Research stops here; nothing is implemented.
"""
    (base / "report.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
