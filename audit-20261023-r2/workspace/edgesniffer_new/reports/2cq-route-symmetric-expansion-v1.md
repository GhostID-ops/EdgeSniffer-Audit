# 2CQ Route-Specific Symmetric Expansion V1

Snapshot: 2026-09-12 16:26 UTC

## Scope

- Research-only, outcome-blind, no-submit.
- Teacher wallet: `2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG` only.
- Other teacher wallets must use separate source and output cohorts.
- Existing SOL-Pump qualification is unchanged.

## Adapter Recovery

- Raydium LaunchLab: 33 previously pending selected entries recovered.
- Raydium CPMM: 26 previously pending selected entries recovered.
- Original pending population reduced by 59 entries.
- Current moving snapshot: 644 exact selected transitions and 74 pending adapters
  across 718 total decisions. The pending count includes new decisions received
  while the work was running.

## Symmetric Cohorts

| Cohort | Total | Eligible | Deferred | Excluded |
|---|---:|---:|---:|---:|
| Non-SOL Pump V2, same quote, launched at 120s | 188 | 0 | 0 | 188 |
| PumpSwap, same spend asset, active within 5s | 26 | 1 | 0 | 25 |
| LaunchLab, same spend asset, active within 5s | 404 | 40 | 13 | 351 |

The PumpSwap eligible decision contains 119 contemporaneous controls. Historical
rows without a trustworthy AllenHark receipt timestamp remain excluded.

## PumpSwap Architecture

The attempted full-program RPC mirror was retired because PumpSwap traffic
arrived faster than finalized RPC reconstruction could sustainably consume it.
Its evidence is preserved but it is not an active cohort input.

The active source reconstructs a canonical 21-slot window ending at every 2CQ
PumpSwap decision. It persists exact events and explicit inexact-gap records,
joins AllenHark receipt timestamps by signature, and excludes affected pools
fail-closed. Temporary finalized blocks are deleted after materialization.

Current bounded source:

- 26/26 decision windows complete
- 65,541 canonical PumpSwap transactions
- 56,840 exact transitions
- 10,841 exact transitions with AllenHark realtime timing
- zero outcome, validation, or submission reads

## Remaining Adapter Backlog

| Venue | Pending |
|---|---:|
| Unresolved route | 28 |
| Raydium CLMM | 12 |
| Meteora DBC | 9 |
| Orca Whirlpool | 8 |
| Meteora DLMM | 7 |
| PumpSwap unsupported selected variant | 4 |
| Pump bonding curve edge case | 3 |
| Raydium AMM V4 | 3 |

These remain fail-closed. The next adapter priority is Raydium CLMM, followed by
Meteora DBC and Orca Whirlpool, once their sample sizes justify the work.
