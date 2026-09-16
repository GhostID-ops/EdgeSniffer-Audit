# SOME_FOLLOW_THROUGH_SIGNAL_BUT_WEAK

## Scope And Integrity

- Sandbox identity: `OUTCOME_FIRST_FOLLOW_THROUGH_ATLAS_N3920`
- Frozen population: discovery ordinals 1-3920 only.
- Usable decoded mints: 3861; 59 were unresolved and were not imputed.
- Atlas rows: 34,749 (3861 mints x 9 causal checkpoints).
- Sizes: 0.5 SOL and 1.0 SOL.
- Entry: decision age +25ms.
- First sellable: decision age +500ms.
- Exit policy: TP +20%, SL -20%, true max hold at decision +120s.
- Economics: exact integer Pump curve replay, exact fees, own impact, and atomic zero-state handling when a historical transaction fails under the counterfactual curve.
- Validation rows inspected: 0.
- Holdout rows inspected: 0.
- Official V1 modified: no.
- Ranking model trained: no.
- Live submissions: 0.

The row-level partition artifacts contain exact inventory, own reserve impact,
MFE, MAE, times to +/-5/10/20%, exact ROI at 1/2/5/15/120 seconds,
first-sellable ROI, TP/SL/MAX_HOLD result, and profitable-exit duration.

## Information Versus Price

| Decision age | Exact N, 0.5 | PF, 0.5 | Expectancy, 0.5 | PF, 1.0 | Expectancy, 1.0 | P90 entry deterioration vs 100ms | P95 deterioration |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 100ms | 3670 | 0.270 | -2.60% | 0.262 | -2.63% | 0.00% | 0.00% |
| 250ms | 3684 | 0.237 | -2.85% | 0.229 | -2.88% | 0.00% | 1.39% |
| 500ms | 3694 | 0.226 | -2.93% | 0.220 | -2.96% | 0.68% | 2.67% |
| 750ms | 3697 | 0.217 | -3.03% | 0.210 | -3.06% | 1.06% | 3.73% |
| 1000ms | 3701 | 0.206 | -3.17% | 0.199 | -3.20% | 1.35% | 4.84% |
| 1500ms | 3708 | 0.191 | -3.41% | 0.185 | -3.44% | 2.04% | 9.70% |
| 2000ms | 3709 | 0.183 | -3.48% | 0.178 | -3.51% | 2.35% | 14.36% |
| 3000ms | 3711 | 0.165 | -3.66% | 0.160 | -3.68% | 3.50% | 21.12% |
| 5000ms | 3716 | 0.163 | -3.57% | 0.157 | -3.61% | 4.20% | 22.18% |

The median deterioration is zero even at 5s because most launches have no
additional successful curve trade between the two entry states. That median
must not be mistaken for universal safety: the active tail deteriorates
quickly. By 2s the P95 entry has lost 14.36% of the 100ms inventory; by 3s it
has lost 21.12%.

The broad population is not a strategy and is strongly negative at every
checkpoint. Waiting does not improve unconditional economics. It only creates
some additional information useful for separating the rare winners.

## Matched Winner/Loser Evidence

Winners were matched to losers in the same chronological quarter using only
causal controls: curve reserves, cumulative gross buy SOL, buy count,
remaining capacity, and global Pump activity. Outcomes and transition fields
were not used to choose the match.

| Causal distinction | Age known | Standardized effect | Distribution overlap | Consistent blocks |
|---|---:|---:|---:|---:|
| Independent follower SOL during 2-5s | 5s | +0.449 | 0.740 | 4/4 |
| Cumulative independent follower SOL | 100ms | +0.347 | 0.806 | 4/4 |
| New follower wallets during 1-2s | 3s analysis | +0.335 | 0.775 | 4/4 |
| New follower wallets during 100-250ms | 750ms analysis | +0.307 | 0.895 | 4/4 |
| Capital per independent follower | 100ms | +0.300 | 0.806 | 4/4 |

There is also a regime effect at 5s: winners occurred under lower aggregate
Pump activity (`globalTrades5sPrior`, effect -0.349, overlap 0.740, 4/4).
That is contextual, not a follow-through fingerprint.

These effects are real enough to investigate because their signs persist
chronologically. They are not strong enough to trade or rank from this result:
overlap is 0.74-0.90 for the useful transition measures, so many winners and
losers remain structurally indistinguishable.

## What Did Not Separate Cleanly

- Consecutive positive-flow windows: small effects, generally below 0.17.
- Reacceleration after slowdown: inconsistent signs and near-total overlap.
- Sell absorption/recovery: weak and inconsistent; strongest matched effect
  was only about -0.26 and did not hold in all four chronological blocks.
- New-high-after-sell: similarly weak and highly overlapping.
- Broad follower entropy: winners often had lower entropy, but the effect was
  modest (roughly -0.29) and does not support a simple "more distributed is
  always better" story.

## Move Archetypes

At the 750ms descriptive focus checkpoint, the 95 exact 0.5 SOL TP outcomes
split into:

| Archetype | N | Share of all 3861 states | Description |
|---|---:|---:|---|
| Slow build | 57 | 1.48% | Reached +20% after 5s. |
| Recovery/absorption | 30 | 0.78% | Had at least -5% adversity or a recovered sell event before TP. |
| Continuation | 8 | 0.21% | Reached TP without meaningful reversal. |
| Transient spike | 0 | 0.00% | No TP winner had a profitable window shorter than 500ms under this definition. |

The median cumulative profitable-exit availability among TP winners was about
105 seconds. In this sample, successful moves were generally not one-tick
phantoms; slow builds and recoveries dominated.

## 2CQ Overlay

The authoritative 2CQ wallet was observed on 26 atlas mints, with median first
entry age 42ms. It was used only as an overlay, never as the winner label.

At a hypothetical 100ms decision, 22 were exact-executable at 0.5 SOL:

- 2 TP, 0 SL, 20 MAX_HOLD
- 27.3% positive final returns
- PF 0.95
- expectancy -0.18%
- archetypes: 1 continuation, 1 slow build, 20 other

At 750ms, all 26 were exact-executable, but only one was a TP and it was a
slow build; PF was 0.64 and expectancy -1.34%. This does not show that 2CQ has
no edge. It shows that our frozen TP20/SL20/120s hypothetical entry, delayed
from the wallet's observed median 42ms entry to 100-750ms, does not reproduce
its behavior. The overlay therefore provides no support for using 2CQ as a
label or for claiming one dominant 2CQ archetype here.

## Direct Answers

1. Winner/loser separation is weakly detectable by 100ms through independent
   follower capital. The strongest measured separation arrives only at 5s.

2. Deterioration becomes materially expensive in the active tail around
   1.5-2s: P95 is 9.70% at 1.5s and 14.36% at 2s. At 3s it exceeds 20%.

3. There is no proven practical sweet spot. The best hypothesis is 100-250ms:
   it preserves almost all entries while exposing early follower capital, but
   separation is still modest. Waiting to 5s buys more information after too
   much of the fast tail has moved.

4. Yes, independent follower capital distinguishes winners modestly. It is the
   clearest recurring family, but overlap remains high.

5. No strong evidence yet. Persistent multi-window flow is directionally
   useful later, but consecutive-positive-window and reacceleration features
   do not separate cleanly.

6. No. Sell absorption is a useful move-type description, not yet a reliable
   winner/loser discriminator.

7. The five strongest non-duplicate causal transitions are 2-5s follower SOL,
   100ms cumulative follower SOL, 1-2s new follower wallets, 100-250ms new
   follower wallets, and 100ms capital per follower.

8. Yes. Slow builds, recovery/absorption moves, and clean continuations are
   distinct successful archetypes. Slow builds dominate this TP population.

9. No defensible disproportion was established for 2CQ. There were only two
   TP archetypes at 100ms and one at 750ms, while its actual entries were much
   earlier.

10. Not yet. There is enough coherent structure to repeat and pre-register the
    transition analysis at N5000, but not enough separation to justify a
    flexible chronological ranker. A model now would mostly optimize overlap
    and rediscover timing/regime noise.

## Next Gate

At N5000, rerun this same frozen atlas without changing features or
definitions. Require the independent-follower effects to retain sign across
chronological blocks and show materially lower overlap before allowing a
ranking model. Official N5000/N10000 collection and boundaries remain
unchanged.

