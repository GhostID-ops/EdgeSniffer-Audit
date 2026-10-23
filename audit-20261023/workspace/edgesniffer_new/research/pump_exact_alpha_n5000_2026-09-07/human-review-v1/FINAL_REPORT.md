# Pump exact-alpha V1 — authoritative N5000 discovery report

Generated: 2026-09-07  
Scope: corrected exact prospective corpus, chronological ordinals 1–5000 only  
Human-adjudicated verdict: `N5000_PROMISING_ALPHA_LEADS_NEED_MORE_N`

## Executive decision

The N5000 scan found one selective, early, size-robust lead worth freezing for prospective confirmation:

`FIXED_25MS AND firstBuyerShareBps <= 360.40437631955825`

It matched 120 unique mints (2.4% of the N5000 launch prefix, about 4.88/hour). At 0.5 SOL it produced PF 2.941, +5.472% expectancy, and +3.2830 SOL. At 1 SOL it produced PF 2.582, +4.987% expectancy, and +5.7849 SOL. It was positive at both sizes in four of five 1,000-mint blocks and in the combined 3001–5000 segment. It remained strongly positive after the best three trades were removed and at both adjacent frozen aggregate checks.

That is promising, not yet genuinely convincing. The coarse `firstBuyerShareBps` factor shape at 25 ms is `IRREGULAR` and points in the opposite broad direction; the edge lives in an extreme tail inside an otherwise losing lowest quintile. The adjacent broader frozen threshold is aggregate-positive but `EARLY_ONLY`. In ordinals 3001–5000 the lead has only 33 matches and a thin PF of 1.103/1.136, including a losing D block followed by a small E-block recovery. It is also the sole formal survivor among 3,995 tested frozen candidates and belongs to the already-discussed low-first-buyer motif, not an independent new motif.

The automatic predeclared screen classified the result as `N5000_STRONG_SELECTIVE_ALPHA_FOUND` because the candidate passed its mechanical serious-lead gates. Human adjudication downgrades that to `N5000_PROMISING_ALPHA_LEADS_NEED_MORE_N` under the request's explicit anti-threshold-mining and chronological-coherence standard.

No deployment, shadow trading, live trading, feature modification, threshold retuning, or new research lane was performed.

## Scope and integrity gate

| Check | Result |
|---|---:|
| Exact admitted prefix | 5,000 unique mints, ordinals 1–5000 contiguous |
| Prefix ordering | `createReceivedAtUnixNs,mint` |
| Prefix SHA-256 | `b61b4ffdef0b01ad380578b20b35c11329d13a1c4bac3ff88c712a4b784cc741` |
| Prefix file SHA-256 | `785089878175f066f77511f4e9de5fffe673e440addcd86ebe3e00803b6617ee` |
| Rows source | `rows-v2` only |
| Materialized mints | 4,938 |
| Explicitly excluded | 62, all `FEATURE_EVENTS_UNRESOLVED` |
| Unaccounted/replaced | 0 / 0 |
| All opportunity rows | 48,494 unique mint/state rows; zero duplicate mint/state rows |
| Primary exact label cells | 45,864 |
| Primary executable mints | 4,816 |
| Corrected canonical replay | Active and hash-matched |
| Failed transactions mutate state | False |
| Exact integer Pump math | Unchanged |
| Canonical evidence coverage | 20,822/20,822 post-entry 0.5-SOL cells; 20,675/20,675 at 1 SOL |
| Outcome support at 0.5 SOL | TP 1,205; SL 1,976; MAX_HOLD 42,683 |
| Return distribution at 0.5 SOL | 5,851 distinct returns; −57.366% to +323.161% |
| Old zero-post-entry replay bug | No recurrence |
| Old readiness accounting bug | No recurrence |
| Unit tests | `testExactCurveEconomicsV1.py` 9/9; `testPumpExactAlphaResearchV1.py` 9/9 |

Representative manual reconciliations all passed exact arithmetic, timing, entry/exit evidence, and canonical finalized-transaction checks:

| Role | Ordinal | Mint | Activity | Exact outcome |
|---|---:|---|---|---|
| TP | 2931 | `6FaRwfqksgPFP34kMM7bHSWTKwNaKfQWGmmAnNtkpump` | 3 buys, 1.0990 SOL gross | TP, +20.492% |
| SL | 2833 | `AQbFELdt51SVtu4wHyx4CdELrN1ayzno6hf2BHQWgnn3` | 3 buys, 3.6506 SOL gross | SL, −20.138% |
| MAX_HOLD | 2796 | `hcaBdSaJnnnWimWPbd9MNVeqU66EVAED6wA5yS5pump` | 1 buy, 2.2733 SOL gross | MAX_HOLD, −0.483% |
| Low activity | 2329 | `CDW8LU3VZeZEoBXeddpJKHFRhdCkkZey427hL7zXpump` | 1 buy, 1.0022 SOL gross | MAX_HOLD, −2.471% |
| High activity | 2703 | `HVm12fpWdV3HA51ym82afCCcYHAb2X9SnzST3HSzpump` | 13 buys, 12.6000 SOL gross | SL, −49.323% |

No outcomes, features, or economics for ordinal 5001+, validation 10001–20000, or holdout 20001–30000 were retained, evaluated, or reported.

## Frozen search and reproducibility

The authoritative frozen scanner ran unchanged. The reporting harness reproduced the scanner's top ten candidate cohorts and N/PF values exactly before adding requested reporting views.

| Universe item | Count |
|---|---:|
| Coarse state/feature shapes | 341 |
| One-feature candidates | 3,935 |
| Controlled two-feature candidates | 55 |
| Frozen-permitted three-feature candidates | 5 |
| Total distinct rule candidates | 3,995 |

The scanner's raw PF×sqrt(N) top result was a broad three-feature FIRST_IMPULSE cohort with N=1,581, but it lost money: 0.5 SOL PF 0.926/E −0.200%, and 1 SOL PF 0.921/E −0.214%. This illustrates why the requested balanced ranking changes the ordering without changing the search universe.

The best controlled multi-feature rules were both rejected:

| Kind | Exact fingerprint | N | 0.5 PF / E / PnL | 1 SOL PF / E / PnL | Failure |
|---|---|---:|---|---|---|
| Two-feature | FIRST_IMPULSE; recurrent wallet share <=2181.438; buyer HHI >=0.270679 | 1,694 | 0.861 / −0.382% / −3.2394 | 0.854 / −0.401% / −6.7129 | Later-negative, brittle, outlier-driven |
| Three-feature | Above + global trades 5s prior <=481 | 1,581 | 0.926 / −0.200% / −1.5834 | 0.921 / −0.214% / −3.3512 | Later-negative, brittle, outlier-driven |

No controlled two- or three-feature fingerprint beat the single-feature tail.

## Strongest fingerprint

Fingerprint: `FIXED_25MS; firstBuyerShareBps <= 360.40437631955825`  
Candidate ID: `C-4cf71d36f6e15aac`  
Cohort SHA-256: `536df8499a8077ee0f6ff299c64ef0286890fa18aff531afefd2f1d891f49a04`

Plain English: by the fixed 25 ms checkpoint, the launch's first buyer accounts for no more than about 3.604% of observed buyer volume. This is a very early, low-first-buyer-concentration tail. It fires on 24 per 1,000 launches, approximately 4.88 times/hour over the 24.60-hour capture span.

### Size economics

| Size | Match N | Pass N/rate | TP / SL / MAX | WR | PF | E | PnL | Median / worst / best ROI |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.01 | 120 | 120 / 100% | 36 / 7 / 77 | 58.33% | 3.022 | +5.711% | +0.0686 SOL | +2.588% / −47.053% / +45.739% |
| 0.10 | 120 | 120 / 100% | 36 / 7 / 77 | 58.33% | 3.059 | +5.750% | +0.6901 SOL | +2.674% / −47.016% / +45.881% |
| 0.50 | 120 | 120 / 100% | 36 / 7 / 77 | 58.33% | 2.941 | +5.472% | +3.2830 SOL | +2.058% / −47.148% / +46.031% |
| 1.00 | 120 | 116 / 96.67% | 35 / 8 / 73 | 57.76% | 2.582 | +4.987% | +5.7849 SOL | +2.029% / −47.318% / +46.208% |

Capacity classification: `SIZE_ROBUST`.

### Chronological persistence

| Block | Match N | 0.5 pass N / PF / E / PnL | 1 SOL pass N / PF / E / PnL |
|---|---:|---|---|
| A 1–1000 | 12 | 12 / 3.574 / +5.133% / +0.3080 | 11 / 1.286 / +1.276% / +0.1404 |
| B 1001–2000 | 49 | 49 / 6.232 / +8.451% / +2.0705 | 49 / 5.745 / +7.921% / +3.8813 |
| C 2001–3000 | 26 | 26 / 2.930 / +6.367% / +0.8277 | 25 / 2.755 / +6.260% / +1.5651 |
| D 3001–4000 | 20 | 20 / 0.575 / −2.452% / −0.2452 | 18 / 0.615 / −2.382% / −0.4287 |
| E 4001–5000 | 13 | 13 / 2.898 / +4.953% / +0.3220 | 13 / 2.831 / +4.821% / +0.6268 |
| Early 1–3000 | 87 | 87 / 4.396 / +7.371% / +3.2063 | 85 / 3.538 / +6.573% / +5.5868 |
| Later 3001–5000 | 33 | 33 / 1.103 / +0.465% / +0.0767 | 31 / 1.136 / +0.639% / +0.1981 |

Classification: `MOSTLY_CONSISTENT`, with four of five 1k blocks positive at both primary sizes. The negative D block and much weaker later aggregate are the main reasons not to call this confirmed alpha.

### Outlier resistance

| Size | Best trade share | Best 3 share | Best 5 share | PF after best 1 | PF after best 3 | E after best 3 |
|---:|---:|---:|---:|---:|---:|---:|
| 0.5 | 7.01% | 15.19% | 23.04% | 2.805 | 2.646 | +4.759% |
| 1.0 | 7.99% | 17.10% | 25.85% | 2.455 | 2.311 | +4.244% |

Classification: `OUTLIER_RESILIENT`.

### Nearby frozen checks and factor shape

Aggregate nearby classification: `STABLE` (2/2 positive at both primary sizes).

| Frozen comparison | N | 0.5 PF / E | 1 SOL PF / E | Important qualification |
|---|---:|---|---|---|
| Adjacent threshold at 25 ms: <=1985.112 | 160 | 1.664 / +2.941% | 1.518 / +2.454% | Aggregate-positive but `EARLY_ONLY` |
| Adjacent checkpoint at 50 ms: <=360.404 | 129 | 2.131 / +3.758% | 1.897 / +3.282% | Aggregate-positive; chronology not used by the nearby label |

The coarse 25 ms `firstBuyerShareBps` shape is `IRREGULAR`, not coherent. All five quintiles are negative at both primary sizes; even the lowest quintile (N=892, values 0.149–10,000 bps because of heavy ties at 10,000) is PF 0.425/0.408. The positive pocket is an extreme sub-tail inside that losing quintile. The coarse correlation points `HIGHER_BETTER`, opposite the selected `<=` rule. This is threshold-mining risk, not proof of causality.

## Strongest single-feature views

All 341 full factor-shape records are preserved machine-readably. Coarse classifications were 137 `MONOTONIC_OR_COHERENT`, 204 `IRREGULAR`, and 0 `KNIFE_EDGE`; a coherent shape can still be coherently bad, so classification alone is not alpha.

| Rank | State and exact rule | N | Shape | 0.5: pass; TP/SL/MAX; WR; PF; E; PnL; median/worst/best | 1 SOL: pass; TP/SL/MAX; WR; PF; E; PnL; median/worst/best |
|---:|---|---:|---|---|---|
| 1 | FIXED_25MS; firstBuyerShareBps <=360.404 | 120 | IRREGULAR | 120; 36/7/77; 58.33%; 2.941; +5.472%; +3.2830; +2.058/−47.148/+46.031% | 116; 35/8/73; 57.76%; 2.582; +4.987%; +5.7849; +2.029/−47.318/+46.208% |
| 2 | FIRST_IMPULSE; firstBuyerShareBps <=2202.505 | 126 | IRREGULAR | 126; 39/16/71; 58.73%; 2.009; +4.136%; +2.6058; +2.387/−47.148/+32.392% | 126; 38/17/71; 56.35%; 1.809; +3.573%; +4.5026; +1.916/−47.318/+32.522% |
| 3 | FIXED_25MS; firstBuyerShareBps <=1985.112 | 160 | IRREGULAR | 160; 41/19/100; 48.13%; 1.664; +2.941%; +2.3531; −0.569/−47.148/+74.848% | 156; 40/20/96; 47.44%; 1.518; +2.454%; +3.8277; −0.706/−47.318/+75.314% |
| 4 | FIXED_50MS; firstBuyerShareBps <=1177.801 | 143 | MONOTONIC_OR_COHERENT | 143; 37/13/93; 46.85%; 1.683; +2.651%; +1.8953; −0.700/−47.148/+26.041% | 139; 36/14/89; 46.76%; 1.525; +2.208%; +3.0698; −1.278/−47.318/+24.644% |
| 5 | FIXED_100MS; earlyReserveMovementPct >=42.005 | 189 | IRREGULAR | 189; 50/20/119; 42.33%; 1.320; +1.753%; +1.6563; −2.044/−56.589/+62.508% | 185; 49/21/115; 42.70%; 1.251; +1.445%; +2.6732; −2.121/−56.751/+61.648% |

Ranks 2–5 are all `EARLY_ONLY`; they are not serious leads.

## Top 15 distinct N5000 fingerprints

Candidates were greedily collapsed when cohort Jaccard overlap was >=0.90. The table below contains the 15 remaining distinct balanced-rank representatives; `max J` is each row's largest overlap with another listed representative.

| # | Exact fingerprint | N | 0.5 PF / E | 1 SOL PF / E | Chronology; positive blocks; later +? | Stability / outlier | Max J |
|---:|---|---:|---|---|---|---|---:|
| 1 | FIXED_25MS; firstBuyerShareBps <=360.404 | 120 | 2.941 / +5.472% | 2.582 / +4.987% | MOSTLY_CONSISTENT; 4; yes | STABLE / resilient | 0.839 |
| 2 | FIRST_IMPULSE; firstBuyerShareBps <=2202.505 | 126 | 2.009 / +4.136% | 1.809 / +3.573% | EARLY_ONLY; 3; no | BRITTLE / resilient | 0.744 |
| 3 | FIXED_25MS; firstBuyerShareBps <=1985.112 | 160 | 1.664 / +2.941% | 1.518 / +2.454% | EARLY_ONLY; 3; no | MODERATE / resilient | 0.796 |
| 4 | FIXED_50MS; firstBuyerShareBps <=1177.801 | 143 | 1.683 / +2.651% | 1.525 / +2.208% | EARLY_ONLY; 2; no | MODERATE / resilient | 0.839 |
| 5 | FIXED_100MS; earlyReserveMovementPct >=42.005 | 189 | 1.320 / +1.753% | 1.251 / +1.445% | EARLY_ONLY; 3; no | BRITTLE / resilient | 0.880 |
| 6 | FIXED_1000MS; firstBuyerShareBps <=325.457 | 162 | 1.370 / +1.429% | 1.222 / +0.924% | EARLY_ONLY; 2; no | MODERATE / resilient | 0.827 |
| 7 | FIXED_250MS; firstBuyerShareBps <=276.481 | 119 | 1.352 / +1.334% | 1.219 / +0.905% | EARLY_ONLY; 2; no | MODERATE / resilient | 0.888 |
| 8 | FIXED_100MS; firstBuyerShareBps <=1413.428 | 175 | 1.338 / +1.430% | 1.218 / +0.982% | EARLY_ONLY; 2; no | BRITTLE / resilient | 0.817 |
| 9 | FIXED_500MS; firstBuyerShareBps <=287.760 | 134 | 1.268 / +1.026% | 1.148 / +0.617% | EARLY_ONLY; 2; no | MODERATE / resilient | 0.888 |
| 10 | FIXED_50MS; earlyReserveMovementPct >=39.183 | 202 | 1.034 / +0.226% | 0.982 / −0.124% | EARLY_ONLY; 3; no | BRITTLE / driven | 0.880 |
| 11 | FIXED_5000MS; firstBuyerShareBps <=466.864 | 198 | 1.010 / +0.038% | 0.972 / −0.113% | EARLY_ONLY; 2; no | BRITTLE / driven | 0.818 |
| 12 | FIRST_IMPULSE; recurrentShare <=2181.438; globalTrades5s <=481; buyerHhi >=0.270679 | 1,581 | 0.926 / −0.200% | 0.921 / −0.214% | REGIME_SENSITIVE; 3; no | BRITTLE / driven | 0.896 |
| 13 | FIXED_250MS; firstBuyerShareBps <=1669.253 | 232 | 0.988 / −0.054% | 0.913 / −0.415% | EARLY_ONLY; 2; no | BRITTLE / driven | 0.754 |
| 14 | FIXED_50MS; firstBuyerShareBps <=2541.942 | 201 | 0.979 / −0.123% | 0.908 / −0.571% | EARLY_ONLY; 2; no | BRITTLE / driven | 0.796 |
| 15 | FIRST_IMPULSE; recurrentWalletShareBps <=4166.667 | 1,764 | 0.735 / −0.865% | 0.721 / −0.917% | UNSTABLE; 1; no | BRITTLE / driven | 0.896 |

## Formal serious leads and most credible candidates

### Top 5 serious leads

Only one candidate passed the mechanical serious-lead screen: candidate `C-4cf71d36f6e15aac`. There are no ranks 2–5; inventing them would force winners.

### Top 3 most credible candidates

1. **FIXED_25MS; firstBuyerShareBps <=360.404.** The only prospective-confirmation candidate. It fires at 25 ms, 24/1,000 launches, N=120; has strong aggregate 0.5/1 SOL economics, four positive blocks, thin-but-positive later economics, stable aggregate neighbors, and no outlier dependence. Biggest fake risk: a threshold-mined extreme tail of an irregular feature family after 3,995 candidate tests, with only 33 later matches.
2. **FIRST_IMPULSE; firstBuyerShareBps <=2202.505.** N=126; 0.5 PF 2.009/E +4.136%, 1 SOL PF 1.809/E +3.573%. It occurs at the first impulse, but is `EARLY_ONLY`, later-negative, brittle, and another low-first-buyer variant. Rejected as a serious candidate.
3. **FIXED_25MS; firstBuyerShareBps <=1985.112.** N=160; 0.5 PF 1.664/E +2.941%, 1 SOL PF 1.518/E +2.454%. It is the adjacent broader threshold and therefore useful robustness evidence, but it is `EARLY_ONLY`; its broader inclusion destroys later persistence. Rejected as a serious candidate.

There is only one genuinely credible *lead*; #2 and #3 are shown because three explanations were requested, not because they qualify for promotion.

## Frozen N3000 low-first-buyer rules at N5000

No thresholds were retuned.

| Rule | Frozen fingerprint | N | 0.5 PF / E | 1 SOL PF / E | Later 0.5 / 1 PF | Blocks + | Result |
|---|---|---:|---|---|---|---:|---|
| A | FIRST_IMPULSE; firstBuyerShareBps <=338.983 | 94 | 3.897 / +6.909% | 3.244 / +6.193% | 1.617 / 1.581 | 3/5 | Recovered mechanically, but `REGIME_SENSITIVE`; later N=22 and E block N=6 |
| B | FIRST_IMPULSE; firstBuyerShareBps <=2265 | 128 | 1.971 / +4.074% | 1.774 / +3.486% | 0.824 / 0.793 | 3/5 | Remains failed: `EARLY_ONLY` |
| C | FIXED_25MS; firstBuyerShareBps <=2000 | 161 | 1.659 / +2.911% | 1.514 / +2.426% | 0.688 / 0.696 | 3/5 | Remains failed: `EARLY_ONLY` |

Rule A's recovery reinforces that the extreme low-first-buyer tail deserves prospective confirmation, but its small later sample and regime sensitivity do not rescue the broader N3000 thesis. The new strongest candidate is a different frozen N5000 threshold/checkpoint realization of the same motif, not a new independent discovery family.

## Direct answers

1. **Is there genuinely convincing alpha at N5000?** No. There is one genuinely promising lead, not yet convincing alpha.
2. **What is the strongest distinct fingerprint?** `FIXED_25MS AND firstBuyerShareBps <= 360.40437631955825`.
3. **What is its unique mint N?** 120.
4. **Does it work at both 0.5 and 1 SOL?** Yes: PF 2.941/E +5.472% and PF 2.582/E +4.987%, respectively.
5. **Does it remain positive in ordinals 3001–5000?** Yes, but weakly: N=33, PF 1.103/E +0.465% at 0.5; pass N=31, PF 1.136/E +0.639% at 1.
6. **How many of five chronological 1k blocks are positive?** Four at both sizes; block D is negative.
7. **Is the relationship coherent or threshold-mined-looking?** Threshold-mined-looking. The coarse factor shape is `IRREGULAR`, points broadly higher-better, and the selected edge is an extreme sub-tail.
8. **Is it outlier-driven?** No. Removing the best three leaves PF 2.646/E +4.759% at 0.5 and PF 2.311/E +4.244% at 1.
9. **Does it survive nearby checkpoints/thresholds?** It survives both adjacent frozen checks in aggregate. The broader threshold is `EARLY_ONLY`, so full chronological robustness does not survive.
10. **How selective is it?** 24 matches/1,000 launches, about 4.88/hour, fixed 25 ms decision age, 2.4% of N5000.
11. **Does anything look like a rare early opportunity a serious sniffer could exploit?** This one does structurally: selective, 25 ms, size-robust, and outlier-resilient. It still requires untouched prospective confirmation before being treated as exploitable.
12. **Did any new motif beat the previously discussed families?** No. The winner is the low-first-buyer motif itself; all controlled multi-feature motifs and broad historical families lost.
13. **Is the frozen V1 feature universe still worth taking to N10000?** Yes, but only as a confirmation gate for this now-frozen lead and the unchanged universe—not as another retuning pass.
14. **Based on N5000 alone, what should happen next?** Stop for review. If approved later, preregister this exact candidate and evaluate it prospectively on untouched ordinal 5001–10000 when that gate is authorized; do not deploy or retune it now.

## Identities and preserved artifacts

| Identity | SHA-256 |
|---|---|
| Corrected freeze | `5af9661520c4c6e36d87806af36db673f53429da8bc97a4bd924f8b2e0f2f7b2` |
| Frozen scanner code | `46e7336fe34ccdd655940a04d3da2654ac1693cdaf2c13835266c22659672569` |
| Frozen scanner artifact | `c871c96d681e2ea842a1f74c5d2e92801bea0c89ccb8b66e1fdb5e5b2f8c47cd` |
| Analysis harness | `8c53a1fbc193598d4ed5641baa31642495dbc9cdd1f2d39b81951ae9e1ea2ed6` |
| Integrity gate | `68ddff350d8b2b512e9c97447f3db6536965bfe3d9daf6a7193a2d100650dc2e` |
| Exact N5000 prefix | `b61b4ffdef0b01ad380578b20b35c11329d13a1c4bac3ff88c712a4b784cc741` |

The immutable machine outputs include complete candidate results, all factor shapes, the deduplicated candidate table, chronological tables, frozen-rule recheck, code identity, and checksums. The collector remained active after the run with `MainPID=1921840`, `NRestarts=0`; no service was restarted.

## Stop state

Research stopped for human review. No scanner/features/economics were changed. No validation or holdout evaluation occurred. No deployment, shadow, live trade, or follow-on lane was launched.
