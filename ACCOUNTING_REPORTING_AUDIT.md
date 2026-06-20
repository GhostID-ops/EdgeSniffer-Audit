# Accounting And Reporting Audit

Last updated: 2026-06-20

## What Was Wrong

- Older strategy tables labeled cumulative return drawdown as `Max drawdown %`.
- That number was calculated from a running sum of per-trade ROI values.
- A running sum of repeated trade ROIs can fall below `-100`; that is a percentage-point drawdown, not bankroll drawdown.
- Reading it as capital loss was misleading, especially on high-frequency sources like Omego.

Example:

- Ten sequential `-30%` strategy outcomes produce `-300 percentage points` of cumulative return.
- That does not mean one position lost `300%`.
- It means the strategy had repeated losses when every trade was normalized to its own stake.

## Current Report Definitions

- `Realized PnL`: SOL proceeds minus cost basis for sold tokens.
- `Realized ROI`: realized PnL divided by sold cost basis.
- `Avg ROI`: arithmetic mean of per-trade ROI values.
- `Median ROI`: median per-trade ROI.
- `Profit Factor`: gross winning ROI divided by absolute gross losing ROI for strategy simulations, or gross winning SOL divided by gross losing SOL for ledger audits.
- `Cumulative ROI`: sum of per-trade ROI values, expressed as percentage points.
- `Cumulative DD`: worst peak-to-trough drop in cumulative per-trade ROI, expressed as percentage points (`pp`), not bankroll percent.
- `Max drawdown, realized sequence` in wallet ledger audits: worst realized SOL peak-to-trough drop from the chronological realized PnL sequence.

## Live PnL Ledger

The live bot ledger in `src/pnl.ts` uses average-cost accounting:

- Buys add token raw amount and SOL cost.
- Sells add token raw sold and SOL proceeds.
- Sell-fill PnL is the incremental PnL for that specific sell.
- Position PnL is the cumulative realized PnL for that token after all recorded sells.
- Session realized PnL is the sum of cumulative token realized PnL across tracked positions.
- Circuit breaker outcomes are fed sell-fill PnL, not cumulative position PnL.
- `trades.jsonl` sell rows use `realizedPnlLamports` for fill PnL and `positionRealizedPnlLamports` for cumulative token PnL.
- Realized PnL uses cost basis only for tokens that were actually recorded as bought.
- If a sell exceeds recorded inventory, the excess sold amount is tracked as `soldRawUncosted` and does not fabricate extra cost basis.

This keeps optimistic logs, missed buys, dust cleanup, or transfer-driven drift from creating impossible accounting.

Live session reports now show `Open cost basis` instead of pretending to know unrealized PnL without quoting open positions.

## Remaining Limitations

- The in-memory live ledger is session-local unless reconstructed from logs.
- Wallet audits are not tax-grade accounting.
- Current open-position marks can be stale or missing when DexScreener has no usable pair.
- Strategy backtests normalize every trade to the same stake unless a script explicitly models size.
- Bankroll-level drawdown requires a defined capital model: starting bankroll, fixed stake, max open positions, and position overlap.

## Recommended Next Metric

For live strategy selection, prefer a capital-aware simulation:

- fixed stake, for example `0.25 SOL` or `1 SOL`
- `MAX_OPEN_POSITIONS`
- `MAX_SOL_PER_TOKEN`
- chronological entries/exits
- skipped trades when exposure is full
- realized SOL PnL curve
- bankroll drawdown in SOL and percent of starting bankroll

That is the correct way to decide caps for Omego.
