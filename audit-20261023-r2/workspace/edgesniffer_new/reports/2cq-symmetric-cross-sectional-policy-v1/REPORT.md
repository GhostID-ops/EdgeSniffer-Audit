# 2CQ Symmetric Cross-Sectional Policy V1 - N350 Freeze

## Verdict

`POLICY_FROZEN_AWAITING_N500`

The first 350 forward, fully symmetric SOL-Pump choice decisions are frozen.
Exactly one predeclared cross-sectional teacher-imitation policy was fitted.
Ordinals 351-500 remain sealed for one later validation run with no refit.

## Integrity

| Check | Result |
|---|---:|
| Development rows | 350 |
| Development ordinals | 1-350 |
| Freeze membership matches artifact | PASS |
| Artifact matches current first 350 database rows | PASS |
| Choice-set SHA-256 matches freeze | PASS |
| Policy families fitted | 1 |
| Frozen causal features | 48 |
| Coefficients / means / scales | 48 / 48 / 48 |
| Outcomes read | 0 |
| Validation rows read | 0 |
| Validation exported | NO |
| Validation consumed | NO |
| Trade submissions | 0 |

The checkpoint gate was narrowed before this run so an N350 freeze reads only
the first 350 eligible rows. A regression test proves that already-arrived
ordinals 351+ cannot be queried or exported before the policy is frozen.

## Frozen Policy

- Family: `FULL_CAUSAL_PREDECLARED_NO_TOURNAMENT`
- Universe: `PROSPECTIVE_SYMMETRIC_LAUNCHED_120S_V2`
- Rule: rank every complete contemporaneous candidate and select the unique top score
- Exact top-score ties: reject
- Validation refit: forbidden
- Permitted sealed validation runs: one
- Policy SHA-256: `bedb0ec347876948937b92c25c2f9df9a3644ce2bb73f3ac258b956dd4d9d1cc`
- Development choice-set SHA-256: `2e1f1f14e996b1e13e9b00ce9c1cded750c93edac3fb80de4fcf359c6a1c548e`

## Development Descriptive Results

These are in-sample teacher-imitation metrics, not profitability results and
not evidence from the sealed validation set.

| View | N | Top-1 | Top-3 | Pairwise concordance | MRR |
|---|---:|---:|---:|---:|---:|
| All development | 350 | 91.14% | 98.57% | 98.43% | 0.9499 |
| Ordinals 1-175 | 175 | 92.57% | 97.71% | 98.28% | 0.9535 |
| Ordinals 176-350 | 175 | 89.71% | 99.43% | 98.58% | 0.9462 |

The all-development chance Top-1 baseline is 9.31%. The frozen scorer's stored
metrics exactly match an independent deterministic replay.

## What Happens Next

1. Continue the same forward collection unchanged.
2. Keep ordinals 351-500 sealed; do not inspect them incrementally.
3. At total eligible N500, export exactly ordinals 351-500 and run the frozen
   WHAT policy once, with no refit or threshold change.
4. If that one-time validation is credible, combine it with the preserved
   `BASE_PLUS_TRANSITION` WHEN component.
5. Run prospective no-submit imitation before exact-curve profitability.

No economic replay, PnL analysis, or live submission is permitted at this checkpoint.
