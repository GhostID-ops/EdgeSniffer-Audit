# EdgeSniffer Research Handoff

Updated: 2026-07-05

## Purpose

This document preserves the research conversation, evidence, decisions, failed
paths, implemented tooling, and next actions so the work can continue in a new
chat or account without restarting the investigation.

No API keys, private keys, wallet secrets, or full environment files belong in
this document or in Git.

## Objective

Build an independent, stream-first Solana launch detector that:

1. Detects promising Pump.fun/PumpSwap launches before or around high-quality
   wallets.
2. Uses only information observable before entry.
3. Rejects market-wide launch noise.
4. Survives realistic latency, impact, fees, failed transactions, and exits.
5. Keeps first-slot scalpers and fat-tail runner wallets in separate research
   lanes.

The goal is not ordinary copytrading. Source wallets are labels and research
subjects; EdgeSniffer must eventually identify opportunities independently.

## Engineering Constraints

- Keep HTTP/API requests out of the trading hot path.
- Use ShredStream/gRPC/local decoding for forward detection.
- Prefer the Rust executor for buys and sells.
- Keep `RUST_ONLY_BUYS=true`, `RUST_ONLY_SELLS=true`, and
  `JUPITER_FALLBACK_ENABLED=false` for live-testing posture.
- Historical Helius calls are allowed in offline audits.
- Never infer causality from second-level timestamps when same-slot order is
  available.
- Never use current precursor membership on old campaigns without explicitly
  labeling the resulting look-ahead.
- Do not interpret source-wallet exits as an EdgeSniffer TP/SL backtest.
- Do not tune from accepted wallet tokens alone; market negatives are required
  to measure false positives.

## EdgeSniffer Research Before The Wallet Cohort

EdgeSniffer's stream intake and local decode were working. Reports repeatedly
found scanner runners, but paper opens often missed them or spent exposure on
weak candidates.

Important observations:

- A score floor of `10` flooded the ledger: `1,597` closes and approximately
  `-33.22 SOL`. Very low scores were rejected as a strategy.
- A bounded score-60 probe prevented another ledger flood.
- One positive paper session produced `24` closes and `+0.6000 SOL`, but stop
  losses remained the largest drag.
- A later 16-close session produced approximately `-0.2000 SOL` capped PnL,
  `25%` wins, and `75%` stop losses.
- Exposure repeatedly blocked viable runner candidates, but loosening exposure
  was unsafe while open quality remained negative.
- A parameter sweep found an in-sample lane around score `85-94`, MC
  `$1.5k-$5k`, source `0.2-1.5 SOL`, and `TP50/SL20/120s`.
- Confirmation-aware sweeps produced almost no entries. Drawdown and final score
  checks starved the lane.
- Entry-timing analysis showed first-entry simulation materially outperforming
  confirmed entry. Waiting for public follow-through appeared to destroy much
  of the measured edge.

Those historical sweeps were useful diagnostics, not proof of an executable
strategy.

## CyA Research

CyA remains the secondary fat-tail runner lane.

The 1,000-transaction quant audit reconstructed:

- `597` swaps.
- `99` closed campaigns.
- Approximately `+36.46 SOL` source realized PnL.
- `51.5%` source win rate.
- Profit factor around `1.75`.
- Median hold around `20 seconds`.
- Median MFE around `+1.68%`.
- Median MAE around `-29.63%`.
- Approximately `10.2%` of measured entries reached `+50%`.
- Approximately `5.1%` reached `+100%`.

A 5,000-transaction audit was subsequently completed. The detailed artifact
remains on the VPS. CyA should not be merged with short-hold scalpers because
its source-exit distribution is more fat-tailed and path-dependent.

A 500-close CyA/latest audit had poor EdgeSniffer historical match coverage.
Within that small matched set, the existing score did not separate CyA winners
from losers. This motivated direct cohort and causal-entry work.

## Wallet Cohort

Research addresses:

- BWAM: `bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa`
- FiFaw: `FiFawHqxeTVBhv6YbqbLwDVuvokRpPUM1bNwAyxhGc6W`
- 2CQ: `2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG`
- 8Ycby: `8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ`
- CyA: `CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o`

Advertised PnL and win rates were treated only as discovery claims. Every
wallet must be reconstructed independently.

The original multi-wallet OHLCV batch was blocked by Moralis quota/server
errors. Provider retries and Gecko fallback were added, but that path was
deprioritized because the immediate research question is causal pre-entry
selection, not candle enrichment.

## 8Ycby Audit: Final Evidence

### Ledger Reconstruction

The 10,000-transaction ledger audit produced:

- `2,409` reconstructed fills.
- `1,084` campaigns.
- `984` closed measured campaigns.
- `61.9%` source win rate.
- Median source ROI around `+14.8%`.
- Equal-size source-exit profit factor around `14.5`.
- `547` campaigns at or above `+10%`.
- `232` campaigns at or above `+50%`.

These are source-wallet outcomes and do not include our execution latency,
impact, fees, or exits.

### Retained EdgeSniffer Cross-Reference

The initial cross-reference returned zero because the datasets had no temporal
overlap:

- 8Y ledger history ended around `2026-07-04T03:12Z`.
- Retained EdgeSniffer data began around `2026-07-04T15:42Z`.

That was a data-window mismatch, not a detector result.

### Historical Pre-Entry Audit

A stratified 120-campaign historical reconstruction found:

- `114` entry signatures were found, and all `114` decoded to the target
  wallet.
- Source-size and inferred-MC calibration delta around `-0.034%`.
- `0` decoder gaps.
- `102` same-second-only campaigns.
- `11` campaigns with full-second prior flow.
- `7` histories truncated at 1,000 mint transactions.

The strict sample was badly biased:

- Only `1` winning campaign had strict prior flow.
- `10` losing campaigns had strict prior flow.

Therefore the ordinary profile replay was correctly marked invalid.

### Same-Slot Order Audit

Finalized block transaction ordering resolved the same-second ambiguity:

- `111` entry blocks ordered.
- `0` unknown-order events.
- `33` decoded same-slot swaps before entry.
- `724` decoded same-slot swaps after entry.
- Only `11` campaigns had any proven-prior flow.
- Those `11` contained `10` losses and only `1` small winner.
- No scalp winner or runner had a proven earlier swap.

This proves that public prior-buy clusters do not explain 8Y's profitable
entries. The wallet generally enters before the flow that EdgeSniffer's current
confirmation rules require.

### Launch-Origin Audit

The final origin classification found:

- `114` rows with retained origin transactions.
- `7` explicit prior creation transactions.
- `0` prior same-slot creations.
- `0` creations immediately preceding entry.
- `0` creation transactions funding the target wallet.
- `102` target entry transactions containing creation.
- All `102` entry-creation transactions were fee-paid by 8Ycby itself.
- `11` unresolved creation origins.

By outcome:

- Losses: `19/30` entry transactions contained creation.
- Small wins: `29/30` entry transactions contained creation.
- Scalp wins: `30/30` entry transactions contained creation.
- Runners: `24/30` entry transactions contained creation; six were truncated.

### 8Ycby Decision

8Ycby is not a suitable primary template for an independent public-flow
sniffer. In this sample it behaves predominantly as an atomic
creation-plus-entry wallet, especially on profitable campaigns. The target
wallet fee-paid every one of the `102` entry transactions that contained token
creation, confirming this is self-originated activity rather than an external
sniper reacting to a public launch.

Keep 8Ycby as:

- A first-slot execution benchmark.
- A label for creation-time launch outcomes.
- A possible source for studying atomic launch structure.

Do not:

- Train the current buy-cluster score around it.
- Treat its later buyers as pre-entry confirmation.
- Expand its historical campaign sample before another wallet is audited.
- Infer that copying its source returns is executable.

## Current Immediate Action

The initial 2CQ causal audit is complete and validates an external same-slot
launch-burst lane. Do not lower the current EdgeSniffer score or widen its
source-buy range globally. Run the separate 2CQ causal sweep, historical
transaction-lag replay, and forward millisecond replay against contemporaneous
market negatives.

## 2CQ Audit: Validated External Burst Lane

Ledger coverage:

- `10,000` transactions.
- `2,437` closed measured campaigns.
- `+274.5530 SOL` source-wallet realized PnL.
- `1,401` losses, `492` small wins, `453` scalp wins, and `91` runners.
- Top five winners supplied only `11.5%` of gross positive PnL, so the result
  was not concentrated in a handful of jackpots.

Historical and finalized block-order evidence:

- `93/93` found entry signatures decoded to the target wallet.
- Median source-size and market-cap calibration delta was about `-1.1%`.
- `92/120` sampled campaigns had transactions proven to precede 2CQ's entry.
- Finalized ordering found `318` same-slot events before entry and `305` after,
  with `0` unknown-order events.
- Median proven-prior buy volume increased from `7.12 SOL` for losses to
  `11.86 SOL` for scalp wins and `12.59 SOL` for runners.
- Median proven-prior wallet count increased from `3` for losses to `4` for
  scalp wins and runners.

Launch-origin evidence:

- `83` explicit prior creation transactions.
- `58` prior creations occurred in the same slot as 2CQ's entry.
- `0` target entries contained token creation.
- `0` creation transactions were fee-paid by 2CQ.
- `0` creators funded the target wallet.
- The result is eligible for comparison and establishes that 2CQ is reacting
  to externally created launches rather than manufacturing the setup.

Important limitations:

- `37` origins remain unresolved, heavily concentrated in runners, because
  retained mint history was truncated.
- Current EdgeSniffer profiles passed `0` comparable rows. The dominant missed
  gates were `score_low` and `source_buy_high`.
- This does not justify lowering the global score. The current score models a
  different lane.
- The accepted-wallet cohort cannot measure market-wide false positives.
- Source-wallet exits do not prove executable EdgeSniffer exits after latency,
  fees, and price impact.

2CQ decision:

- Promote 2CQ to the primary external scalper research template.
- Model a separate same-slot launch-burst lane using causal features such as
  prior buy count, unique wallets, prior SOL volume, transaction-index lead,
  creator history, and predecessor fee-payer history.
- Compute identity reputation walk-forward only. Never score an old campaign
  using creator outcomes learned in the future.
- Validate against launches 2CQ did not buy before paper-opening anything.
- Keep CyA's runner lane and the existing EdgeSniffer profile separate.

## Recommended Research Order

1. Build market-negative controls and a causal feature sweep for 2CQ.
2. Recover truncated 2CQ runner origins without changing the live profile.
3. Audit `BWAM`.
4. Audit `FiFaw`.
5. Compare the three independently audited external scalper wallets.
6. Keep CyA as the separate runner lane.
7. Keep 8Y as a creation-time benchmark, not a generic external sniper.
8. Build contemporaneous market negatives from EdgeSniffer's forward launch
   stream.
9. Design separate creation-time scalper and runner scores only after
   cross-wallet evidence exists.
10. Run latency, impact, confirmation, exposure, and exit replay.
11. Freeze a paper profile before any live consideration.

## Relevant Commands And Scripts

Package scripts:

- `audit:wallet-edge-xref`
- `audit:wallet-history`
- `audit:wallet-slot-order`
- `audit:wallet-launch-origin`
- `audit:wallet-causal-sweep`
- `audit:wallet-causal-replay`
- `audit:wallet-causal-forward-replay`
- `audit:wallet-latency`
- `test:wallet-history`
- `test:wallet-slot-order`
- `test:wallet-causal`
- `test:wallet-causal-sweep`
- `test:wallet-launch-origin`

Primary source files:

- `src/walletLedgerBucketAudit.ts`
- `src/walletEdgeCrossReference.ts`
- `src/walletHistoricalPreEntryAudit.ts`
- `src/walletSlotOrderAudit.ts`
- `src/walletLaunchOriginAudit.ts`
- `src/walletLatencyReplay.ts`

Important reports on the VPS:

- `data/8ycby-ledger-10000.json`
- `data/8ycby-history-diagnostic-v2.json`
- `data/8ycby-history-flow-v3.json`
- `data/8ycby-slot-order-120.json`
- `data/8ycby-history-origin-v4.json`
- `data/8ycby-launch-origin-v2.json`

Important caches:

- `data/wallet-pre-entry-cache`
- `data/slot-order-cache`

Preserve these caches until the cohort methodology is stable.

## Implemented Git Checkpoints

- `4a0e95a`: wallet-to-EdgeSniffer retained-log cross-reference.
- `3dba591`: historical wallet pre-entry reconstruction.
- `cddad6a`: stop cleanly when Helius keys are exhausted.
- `2a03ec4`: entry-anchor, same-second, calibration, and validity diagnostics.
- `3a1e1cd`: finalized same-slot transaction-order audit.
- `adcdd31`: launch-origin, creator, predecessor, and funding audit.
- `5109ca1`: distinguish entry-contained creation from unresolved origins.

## What A New Chat Must Not Repeat

- Do not treat non-overlapping logs as zero detector coverage.
- Do not credit same-second transactions without block ordering.
- Do not tune from a sample containing almost only losing comparable rows.
- Do not use current precursor lists on old history without a leakage warning.
- Do not equate source-wallet ROI with our executable ROI.
- Do not mix first-slot scalpers with CyA-style runners.
- Do not add HTTP calls to the live trading hot path.
- Do not keep adding 8Y audits unless a specific unresolved causal question
  justifies one.

## Ready-To-Paste New Chat Prompt

```text
Read AGENTS.md, EDGESNIFFER_RESEARCH_HANDOFF_2026-07-05.md, and
WALLET_CAUSAL_AUDIT_RUNBOOK.md before acting.

Objective: build an independent stream-first Solana launch detector, not a
copytrader. Keep scalper and runner lanes separate. Do not add HTTP calls to the
hot path. Prefer Rust execution and preserve RUST_ONLY_BUYS/SELLS with Jupiter
fallback disabled.

Current checkpoint: 8Y is a self-originated atomic creation benchmark and is
excluded as a generic external-sniper model. The 2CQ 10k/120-campaign causal
audit is now complete. It found 92 campaigns with proven-prior flow, 318
same-slot events before 2CQ, 83 explicit prior creations, no entry-contained
creations, no 2CQ-paid creations, and no creator funding of 2CQ. Prior buy
volume and wallet diversity were higher for scalp wins and runners than losses.

Treat 2CQ as the primary external same-slot burst/scalper template. Do not
lower the existing Edge score or widen global source-buy limits. Next build
market-negative controls and a separate causal feature sweep using only
observable prior flow and walk-forward identity history. Recover the truncated
runner rows, then audit BWAM and FiFaw independently. Only after cross-wallet,
market-negative, latency, fee, impact, and exit validation should a frozen
paper profile be created.
```
