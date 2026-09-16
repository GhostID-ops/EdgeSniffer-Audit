# 2CQ Multi-Archetype Research V1

Generated from the prospective, outcome-blind 2CQ decision tape on 2026-09-12.

## Direct verdict

The project is not back at the drawing board. The causal SOL Pump data plane is
working and the first current-data imitation test is strongly positive, but it
is still tiny-N and says nothing yet about profitability.

The largest coverage gap is not PumpSwap. It is Raydium LaunchLab, which
accounts for 246 of 278 decisions previously grouped as `OTHER_DEX`.

## Current population

| Economic family | Decisions | Current research support |
|---|---:|---|
| SOL Pump curve | 204 | Causal state, symmetric controls, exact economics, local execution |
| SOL Pump CPI | 88 | Causal state, symmetric controls, exact economics, local execution |
| Non-SOL Pump V2 | 121 | Exact quote-native transitions; same-quote controls and SOL conversion still required |
| Raydium LaunchLab | 246 | Identified; dedicated causal state/economics adapter required |
| Other non-router DEX | 32 | Protocol-specific adapters required |
| Router | 50 | Underlying venue resolution required before state/economics |
| PumpSwap | 17 | Local execution exists; canonical research pool-state adapter required |
| Ambiguous | 1 | Fail closed |

## Fingerprint feasibility

### Current LAUNCHED_120S population

- Exact symmetric decisions: 49
- Chronological train: 34
- Chronological assessment: 15
- ROC AUC: 0.9925
- Teacher mint ranked first: 14/15 (93.33%)
- Teacher mint ranked top three: 15/15 (100%)
- Chance top-one baseline: 9.93%

Leading causal contributors in this tiny sample:

1. lower mint age
2. more buys in the prior 250ms
3. higher gross and net buy flow in the prior 250ms
4. tighter recent inter-arrival timing
5. stronger 1s buy/sell imbalance

Classification: `TINY_N_DEVELOPMENT_ONLY_DESCRIPTIVE`. This is evidence that
the teacher's cross-sectional choice is learnable. It is not a frozen rule,
profitability result, validation result, or promotion result.

### ACTIVE_SOL_PUMP_5000MS sibling

- Mature compatible source decisions: 124
- Valid symmetric all-age decisions: 23
- Fingerprint gate: 25
- Current result: `TOO_FEW_INDEPENDENT_DECISIONS`

The sibling removes realtime CREATE and launch-age dependence. It still fails
closed on reserve continuity, requires recent external activity, excludes the
teacher action, and compares the selected mint with contemporaneous executable
SOL Pump alternatives.

## Route findings

The 278 `OTHER_DEX` decisions resolve primarily to:

- Raydium LaunchLab: 246
- unknown program `GsaJ...`: 27
- Raydium CPMM: 19
- Meteora DBC: 9
- isolated other venues: low single digits

The 50 router decisions contain mixed underlying venues, led by Orca Whirlpool,
Raydium CLMM, and Meteora DLMM. Router itself must not be treated as an economic
venue.

The 121 non-SOL Pump V2 decisions span 18 quote mints. The largest quote-mint
groups have 36 and 19 decisions, so pooling raw quote amounts would be invalid.

## What was built

- Immutable all-route teacher decision tape.
- Explicit route capability registry with fail-closed unsupported families.
- Separate `ACTIVE_SOL_PUMP_5000MS` all-age symmetric choice materializer.
- Reduced all-age feature set without launch-dependent fields.
- Outcome-blind chronological imitation feasibility test.
- Finalized transaction protocol inventory for all current teacher decisions.
- Hourly low-priority analytics-only refresh under systemd.

The existing launched cohort, sealed validation data, exact collector, live
trading, and 8YCB were not modified.

## Correct adapter order

1. Continue the current SOL Pump prospective cohort and freeze a policy only at
   its declared development boundary.
2. Build Raydium LaunchLab as the first new full causal/economic sibling because
   it contains 246 teacher decisions.
3. Build non-SOL Pump V2 by quote-mint family with dimensionless causal features
   and explicit causal conversion before SOL PnL is permitted.
4. Build PumpSwap from the canonical pool layout and effective quote reserves.
5. Resolve router transactions to their underlying venues, then reuse each
   venue adapter. Never create a generic router economics model.

Official protocol references:

- Pump Buy V2: https://github.com/pump-fun/pump-public-docs/blob/main/docs/instructions/BUY.md
- PumpSwap state and effective reserves: https://github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_SWAP_README.md
- Raydium program references: https://github.com/raydium-io/raydium-library

## Gate to actual live-tradeable alpha

The current result clears only the first selector-feasibility gate. Remaining:

1. Reach a defensible development N and freeze one teacher-imitation policy.
2. Test it once on untouched chronological teacher choices.
3. Join exact executable entry/exit economics without changing the policy.
4. Run prospective no-submit shadow.
5. Only after those pass, run dust execution calibration.

No outcome labels, PnL, sealed validation rows, or trade submissions were read
or produced by this build.
