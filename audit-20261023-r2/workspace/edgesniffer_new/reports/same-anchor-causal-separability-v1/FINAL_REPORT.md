# SAME_ANCHOR_CAUSAL_SEPARABILITY_V1

## Final Verdict

`CAUSAL_SEPARABILITY_FOUND_MODEL_JUSTIFIED`

Strictly causal, live-available information at the fixed >=2 SOL lifecycle anchor separates profitable frozen-policy outcomes from losses. The effect is not a trading rule, does not make the blind anchor profitable by itself, and does not authorize shadow or live trading. It is sufficient evidence to justify one later constrained multivariate model using non-redundant frozen inputs.

## Population

Primary economics are frozen at 0.5 SOL, empirical P50 execution, exact Pump/creator fees and curve impact, strict no-resubmission replay, 0.001 SOL standardized delivery overlay, and TP20/SL20/MAX120.

| Anchor | Unique mints | Policy-labeled | Policy >=10 | Policy positive | Opportunity >=10 |
|---|---:|---:|---:|---:|---:|
| >=1 SOL broad control | 1,518 | 1,451 | 179 (12.34%) | 342 (23.57%) | 242 / 1,488 (16.26%) |
| >=2 SOL primary | 1,209 | 1,146 | 159 (13.87%) | 300 (26.18%) | 217 / 1,179 (18.41%) |
| >=5 SOL secondary | 662 | 607 | 114 (18.78%) | 203 (33.44%) | 151 / 632 (23.89%) |

The anchor populations overlap by design: all 662 >=5 SOL mints are also in >=2 and >=1; all 1,209 >=2 mints are in >=1. They were never pooled as independent observations.

## Strongest Primary Evidence

| Causal feature at >=2 SOL | Coverage | POLICY_GE_10 AUC (95% CI) | Top-tail enrichment | Stable blocks | FDR q | Non-divergent AUC (95% CI) |
|---|---:|---:|---:|---:|---:|---:|
| Net external flow | 100% | 0.664 (0.615-0.714) | 2.51x | 5/5 | 6.17e-10 | 0.621 (0.546-0.698) |
| Crossing overshoot | 100% | 0.662 (0.614-0.713) | 2.51x | 5/5 | 6.17e-10 | 0.610 (0.532-0.688) |
| Last buy / prior median buy | 69.8% | 0.622 (0.565-0.687) | 2.39x | 4/5 | 0.00138 | 0.644 (0.545-0.733) |
| Later/earlier buy-size progression | 69.8% | 0.619 (0.556-0.689) | 2.61x | 5/5 | 0.00166 | 0.636 (0.523-0.744) |
| Gross buy SOL / successful tx | 100% | 0.608 (0.553-0.659) | 2.32x | 5/5 | 0.000111 | 0.534 (0.456-0.621) |

`actual_cumulative_external_gross_buy_sol` and `overshoot_sol` are mathematically equivalent rankings at a fixed anchor and now produce the same AUC. They are one construct, not two independent signals.

The primary base rate was 13.87%. The top decile reached 34.2% for net flow/overshoot, 26.3% for last-buy acceleration, 28.7% for buy-size progression, and 32.5% for buy SOL per transaction.

## Interpretation

The strongest evidence sits in two related but distinct families:

1. **Path-to-anchor strength:** the crossing arrives with more net external demand and a larger overshoot.
2. **Buy-size acceleration:** later buys, especially the crossing buy, are larger relative to earlier buys.

Participation concentration is weaker. Buyer HHI reached AUC 0.570 and FDR q 0.0219, but its non-divergent AUC fell to 0.514 with a confidence interval crossing chance. Exact economic-state variables did not survive primary-anchor FDR. Trailing market context had insufficient coverage for a serious claim.

The sequence evidence is nuanced. Last-buy acceleration reversed in one chronological block, while buy-size progression stayed directionally positive in all five. Net flow was the cleanest feature: block AUCs were 0.652, 0.709, 0.566, 0.638, and 0.693.

## Cross-Anchor Behavior

| Feature | >=1 AUC | >=2 AUC | >=5 AUC | Read |
|---|---:|---:|---:|---|
| Net external flow | 0.651 | 0.664 | 0.648 | Persistent across lifecycle maturity |
| Overshoot | 0.645 | 0.662 | 0.641 | Persistent across lifecycle maturity |
| Last-buy acceleration | 0.593 | 0.622 | 0.518 | Most useful around the 2 SOL crossing; disappears by 5 SOL |
| Buy-size progression | 0.578 | 0.619 | 0.608 | Strengthens after early formation and persists |
| Buy SOL / successful tx | 0.624 | 0.608 | 0.638 | Persistent, but divergence-sensitive |

The >=2 result is therefore not an anchor-shopping artifact. Flow strength appears earlier and remains later; the clearest incremental 2 SOL information is buy-size acceleration.

## Capture Vs Opportunity

The leading features predict both policy capture and the opportunity ceiling:

| Feature | POLICY_GE_10 AUC | POLICY_POSITIVE AUC | OPPORTUNITY_GE_10 AUC |
|---|---:|---:|---:|
| Net external flow | 0.664 | 0.632 | 0.633 |
| Overshoot | 0.662 | 0.631 | 0.630 |
| Last-buy acceleration | 0.622 | 0.610 | 0.645 |
| Buy-size progression | 0.619 | 0.577 | 0.598 |

This matters: the evidence is not solely predicting that future upside existed, nor solely that TP20 happened to capture it. It contains information about both.

## Divergence Sensitivity

Replay divergence affected 468 / 1,209 primary mints (38.71%). It was more common among POLICY_GE_10 positives (61.0%) than negatives (36.8%), so the all-row effect must not be taken at face value.

However, the serious leads survived the non-divergent-only check: net flow 0.621, overshoot 0.610, last-buy acceleration 0.644, and buy-size progression 0.636. Divergence weakens confidence in magnitude, but does not erase the conclusion that causal separability exists.

## Causality Correction

The first technical run was invalidated before interpretation. Its extractor admitted later transactions sharing the anchor's millisecond. That affected 459 / 1,209 primary rows. The corrected extractor stops at the exact trigger signature and slot, excluding 2,813 post-trigger rows across all anchors.

Post-correction checks:

- cumulative external buy equals `anchor + overshoot` on all 3,389 rows
- mismatches: 0
- causal availability failures: 0
- duplicate mint-anchor rows: 0
- validation rows inspected: 0
- holdout rows inspected: 0
- thresholds searched: 0
- feature pairs tested: 0
- models trained: 0
- live/shadow changes or submissions: 0

## Scope And Provenance

- Features tested: 39 (sequence 10, participation 9, path 7, exact state 9, trailing context 4)
- Aggressiveness features excluded: 4 because causal provenance/coverage was not proven
- Contract SHA256: `b5637c1031f787efcd868af6c4391939f6ec03a84846a4ad6df962849801b082`
- Feature registry SHA256: `1819f2f5be5df04f3f08b882dacdbc55f98cf183313675b4b10ccafd6415bff5`
- Corrected code SHA256: `1e56012a7dea5f380fb276885ebaae8de959be0b9bb4d0c59a68c3d2c12c9bb4`
- Discovery prefix SHA256: `d85533d6c246161bc5992f1d61cae4273d50d8c67fca8d4b38a65c23ad994fdd`
- Corrected report SHA256: `4fc9fa06e8308a054298340f925c280f609ccf69d7eec2e7567421707eeaabe0`
- Corrected analysis rows SHA256: `331a5239bb2ae595976b5ba92b080ce2f92452ca1bbe20a099ff9f9006c7dba7`

The next phase is justified, but was intentionally not performed: one constrained, preregistered multivariate model using non-redundant representatives of crossing strength and buy-size progression. No threshold, strategy, shadow lane, or live profile was produced here.
