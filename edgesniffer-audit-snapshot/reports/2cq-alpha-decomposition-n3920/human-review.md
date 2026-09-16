# 2CQ Alpha Decomposition: Human Review

## Direct verdict

`2CQ_ALPHA_NOT_REPRODUCIBLE_FROM_AVAILABLE_DATA`

This is not a finding that 2CQ has no edge. The larger authoritative wallet
history proves that the wallet was profitable. It means the current exact N3920
ledger can strongly test entry timing, but it cannot reconstruct any 2CQ sell
for the 26 atlas-compatible mints. Exit attribution and actual-wallet
reproduction are therefore unidentified.

Evidence ranking:

1. Entry timing: strongest exact evidence and economically material.
2. Launch selection: weak-to-moderate hints, with high control overlap.
3. Exit policy: historically suggestive, but not exact-compatible in this cohort.

## Identity and datasets

Authoritative wallet:

`2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG`

Identity source: `data/wallet-audit-wallets.json`, label `2cq-scalper`.

Exact-compatible dataset:

- Frozen discovery ordinals: 1 through 3920 only.
- Scope: 2CQ buy at or before the atlas final 5000ms causal checkpoint.
- Unique bought mints: 26.
- Successful entries reconstructed: 26.
- Exact 2CQ exits reconstructed: 0.
- Validation/holdout rows inspected: 0.

Separate historical authoritative datasets, not mixed into exact replay:

- `wallet-ledger-audit-2CQ....json`: 5,000 transactions, 904 positions,
  3,416 realized trades, +201.8427 SOL, 54.19% WR, PF 2.29.
- `path-wallet-audit-2CQ....json`: 50 measured entries; source-exit mirror
  had 47 outcomes, 59.57% WR, PF 3.44, mean +28.05%, median +2.80%.
- `wallet-pnl-windows-2CQ....json`: 21,000 transactions from May 30 through
  July 29; 30-day net +187.6618 SOL, PF 1.75 after 62.1129 SOL fees.

These historical results establish profitability, but they do not have the
same exact causal curve replay semantics as the September N3920 corpus.

## Actual 2CQ entry profile

| Measure | N | P25 | Median | P75 | Range |
|---|---:|---:|---:|---:|---:|
| Entry age, ms | 26 | 26.25 | 42.00 | 68.50 | 5 to 194 |
| Canonical rank | 26 | 2 | 2 | 4 | 1 to 5 |
| Successful buys ahead | 26 | 1 | 1 | 3 | 0 to 4 |
| SOL ahead | 26 | 0.0475 | 1.3859 | 6.5458 | 0 to 11.0 |
| Fee-aware buy SOL | 26 | 1.2106 | 1.8362 | 1.9742 | 1.2073 to 7.9575 |
| Own curve impact | 26 | 3.94% | 5.11% | 6.42% | 3.13% to 17.93% |
| Curve capacity used | 26 | 5.13% | 6.97% | 8.16% | 4.53% to 22.87% |
| Pre-entry virtual SOL | 26 | 30.0469 | 32.1096 | 36.4650 | 30.0 to 40.8642 |

All 26 contain one observed 2CQ buy. No observed 2CQ sells are present in the
exact evidence frames. Therefore first-sell latency, partial fractions, full
exit latency, realized ROI/PnL, and pre-first-sell MFE/MAE are unavailable for
this exact cohort. Zero reconstructed closes means unavailable, not zero PnL.

The historical 50-entry audit argues against a simple fixed exit: mirroring the
wallet's source exit had PF 3.44, while tested fixed/trailing alternatives ranged
from PF 0.26 to 1.58. That is suggestive of adaptive exits, but cannot be used as
an exact exit decomposition here.

## Entry timing: 0.5 SOL

Frozen policy: exact entry, 500ms first-sellable, TP +20%, SL -20%, true 120s
max hold.

| Entry age | Exact N | TP/SL/Hold | WR | PF | Expectancy | PnL | Median ROI | Worst ROI |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 25ms | 22 | 4/0/18 | 77.27% | 6.66 | +8.09% | +0.8898 SOL | +7.41% | -11.28% |
| 42ms | 22 | 3/0/19 | 54.55% | 2.96 | +4.65% | +0.5120 SOL | +2.26% | -17.30% |
| 50ms | 22 | 2/0/20 | 40.91% | 1.55 | +1.62% | +0.1781 SOL | -2.01% | -17.30% |
| 75ms | 23 | 2/0/21 | 34.78% | 1.22 | +0.78% | +0.0901 SOL | -2.47% | -17.30% |
| 100ms | 24 | 2/0/22 | 29.17% | 0.95 | -0.16% | -0.0191 SOL | -2.47% | -17.30% |
| 150ms | 25 | 2/0/23 | 28.00% | 0.84 | -0.64% | -0.0797 SOL | -2.47% | -17.30% |
| 250ms | 26 | 2/0/24 | 19.23% | 0.66 | -1.48% | -0.1918 SOL | -2.47% | -17.30% |

At 1.0 SOL the pattern survives own impact: PF is 6.39 at 25ms, 2.87 at
42ms, 0.95 at 100ms, and 0.65 at 250ms. Expectancy is +7.86%, +4.52%,
-0.20%, and -1.49%, respectively.

Paired 0.5 SOL marginal mean ROI loss:

- 25 to 42ms: 3.43 percentage points, N=22.
- 42 to 50ms: 3.04 points, N=22.
- 50 to 75ms: 1.20 points, N=22.
- 75 to 100ms: 0.84 points, N=23.
- 100 to 150ms: 0.40 points, N=24.
- 150 to 250ms: 0.81 points, N=25.

On the common 42ms/100ms cohort, 42ms adds 5.12 mean ROI points. The median
difference is zero because 14/22 mints had no intervening state change; eight
improved, seven by more than two points and five by more than ten. The timing
edge is concentrated in fast bursts rather than uniformly spread.

## Exact transaction-position test

Inserting our hypothetical buy immediately before 2CQ's actual buy transaction,
then applying our frozen exits, gives:

| Size | Exact N | WR | PF | Expectancy | PnL | Median ROI | Worst ROI |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.5 SOL | 26 | 65.38% | 7.64 | +9.20% | +1.1958 SOL | +6.41% | -11.28% |
| 1.0 SOL | 26 | 61.54% | 6.40 | +8.25% | +2.1445 SOL | +6.21% | -11.51% |

For the 24 mints exact at both actual position and 100ms, actual positioning
improves mean ROI by 9.99 points and median ROI by 6.76 points at 0.5 SOL.
Sixteen improve, fifteen by more than two points, and eight by more than ten.

This test combines exact within-transaction timing with 2CQ's already-observed
selection. It proves that 100ms generic imitation discards substantial economics.
It does not independently prove that timing alone explains the wallet's actual
realized PnL.

## Exit decomposition

| Comparison | Result |
|---|---|
| Actual 2CQ realized outcome | 0 exact closes available |
| Actual 2CQ entry position + frozen exits | PF 7.64 at 0.5 SOL; PF 6.40 at 1 SOL |
| Actual 2CQ entry position + observed 2CQ sell schedule | 0 exact schedules; `NO_OBSERVED_SELL` for all 26 |

All 26 row-level attributions are `INSUFFICIENT_EVIDENCE`, because the required
actual and schedule-replay legs are absent. No trade was force-labeled timing,
exit, both, or selector-dominant.

## Taken versus ignored controls

Twenty-six contemporary ignored launches were matched without outcomes, using
time/regime, launch age, curve state, cumulative SOL, and Pump activity.

| Causal feature | Standardized effect | Distribution overlap | Taken mean | Ignored mean | Stability |
|---|---:|---:|---:|---:|---|
| Follower entropy | +0.33 | 76.92% | 0.275 | 0.146 | positive 3/4 blocks |
| Follower wallets | +0.28 | 84.62% | 0.808 | 0.500 | positive 3/4 blocks |
| Unique buyers | +0.13 | 69.23% | 1.654 | 1.500 | mixed final block |
| Lower repeat-follower ratio | -0.26 | 88.46% | 0.654 | 0.769 | only 1 informative block |

There is no magic selector feature here. The top two are hypotheses, not robust
separators, and their high overlap implies high false-positive rates under any
simple presence rule. The final chronological block is flat for the two leading
features. No threshold was optimized.

No exact-compatible mediocre early-wallet negative control was pre-labeled in
this frozen window, so uniqueness to profitable sniper behavior is unproven.

## Answers

1. Authoritative wallet: `2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG`.
2. Median entry: 42ms; median canonical rank: 2.
3. 42ms to 100ms loses 5.12 paired mean ROI points at 0.5 SOL; aggregate PF
   falls from 2.96 to 0.95 and expectancy from +4.65% to -0.16%.
4. Exact answer unavailable. Historical non-exact evidence suggests 2CQ exits
   are materially better than simple generic alternatives.
5. Yes in counterfactual economics: 2CQ's exact entry position plus our exits
   has PF 7.64/+9.20% expectancy at 0.5 SOL. This is not actual-PnL reproduction.
6. Unknown: all 26 exact sell schedules are unavailable.
7. No launch feature clearly distinguishes taken launches after causal matching.
8. Best candidate families: follower entropy, independent follower-wallet count,
   and early buyer diversity. All remain weak hypotheses.
9. Missing: target-associated 2CQ sells, exact sell-time curve states, full
   wallet inventory evolution, and richer causal prior-wallet identity/history
   for taken and ignored launches below 100ms.
10. Next task: complete a 2CQ exact exit-evidence join for these fixed 26 mints,
    then rerun this exact frozen decomposition. Do not scan or tune new rules
    until actual sell schedules and realized outcomes are reconstructable.

## Integrity and artifacts

- Report SHA256: `f418407264c7e1181671b84475afdbc18a0ad1df3ff83e3cde13c3068379d9ca`
- Row dataset SHA256: `08f1ec53a45ef50886769e831b55479d60b8976f960a73e9636ee45dd7b71f04`
- Official V1 modified: no.
- Collector restarted/stopped: no; active with NRestarts=0 after completion.
- Live submissions: 0.
- Ranker/shadow/live strategy created: no.

The full per-mint decomposition is in `decomposition-rows.jsonl.gz`; the complete
aggregates, selector distributions, manifests, and historical compatibility
notes are in `report.json`.
