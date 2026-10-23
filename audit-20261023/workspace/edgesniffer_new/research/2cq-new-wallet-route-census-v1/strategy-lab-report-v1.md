# 2CQ new-wallet strategy lab V1

Status: built and verified; discovery-only; no policy frozen; no trading enabled.

## Objective

Reverse-engineer the confirmed 2CQ wallet `9qz7cWBqYBP2TnGfzMmxX64G4PQip7HtogjPBBnMhWpw` and search for route-specific fingerprints and executable economics. The lab evaluates a bounded pre-registered matrix in one offline pass so entry filters, exit rules, trade sizes, fees, slippage, and impact are compared on identical causal inputs.

## Matrix

- 9 entry filters: exact PumpSwap route, direct/CPI route, reserve thresholds, and entry-impact ceilings.
- 11 exits: 5/10/15/30/60/120/300-second fixed exits, take-profit/stop-loss, trailing protection, and partial take-profit.
- 3 notionals: 0.1, 0.5, and 1.0 SOL.
- 3 execution stresses: 0, 25, and 50 bps.
- One-second entry delay, venue fees, constant-product impact, failed entries, missed exits, and no price imputation.

The immutable matrix contract is `strategy-matrix-v1.json`. Its SHA-256 in the local discovery run was:

`ba3d0b8ddc827dc9405e7961409cc07f497459c18cf73d4c579845891d02cc2d`

## Local discovery run

Inputs were the locally preserved post-repair source/window copies. The source loader skipped one malformed source line without changing the evidence files; the remote repaired source audit remains the authoritative clean collector checkpoint.

| Measure | Result |
|---|---:|
| Source anchors loaded | 11,548 |
| Exact PumpSwap anchor windows prepared | 11 |
| Window records loaded | 11 |
| Candidate outcomes | 5,148 |
| Strategy combinations | 495 |
| Controls loaded | 0 |
| Policy frozen | No |
| Promotion allowed | No |
| Trading enabled | No |

The output is a coverage/discovery artifact, not a profitability result. The lack of controls and the small number of prepared windows prevent a valid winner decision. Missed or unavailable exit marks remain explicit misses; no price is invented to make a strategy look complete.

## Implementation

- Runner: `scripts/runWalletStrategyMatrixV1.py`
- Contract: `strategy-matrix-v1.json`
- Focused tests: `scripts/testRunWalletStrategyMatrixV1.py`
- Current local output: `.analysis_tmp/strategy-matrix-result-v1.json`

Verification completed:

- 7 focused tests passed.
- Python compilation passed.
- `git diff --check` passed.

## Next gate

Keep the existing receive-only collectors active and accumulate a larger clean selective-window cohort with contemporaneous controls. Re-run the same matrix against a sealed discovery snapshot, then freeze at most one route-specific candidate before a separate fresh prospective validation cohort. No result from this discovery run may change the frozen strategy or enable execution.
