# 2CQ Policy Archetype Decomposition V1

Generated: `2026-09-08T18:37:00.764382Z`
Source clean episodes: **4685**
Verdict: **2CQ_SINGLE_DOMINANT_POLICY**

## Size Modes

| Mode | Range SOL | N | Share | WR | PF | Exp SOL | Net SOL | Hold P50/P90 | Staged |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| SIZE_MODE_1_1.222_SOL | [0.0000, 1.4908) | 1096 | 23.39% | 34.85% | 1.678 | 0.0258 | 28.248 | 2/7s | 37.04% |
| SIZE_MODE_2_1.885_SOL | [1.4908, 2.3847) | 1646 | 35.13% | 43.50% | 1.871 | 0.0754 | 124.090 | 3/16s | 59.30% |
| SIZE_MODE_3_3.093_SOL | [2.3847, 3.8560) | 1395 | 29.78% | 60.22% | 3.055 | 0.2470 | 344.497 | 12/21s | 86.67% |
| SIZE_MODE_4_4.279_SOL | [3.8560, 5.5590) | 348 | 7.43% | 62.93% | 4.526 | 0.5313 | 184.880 | 14/22s | 91.09% |
| SIZE_MODE_5_6.993_SOL | [5.5590, inf) | 200 | 4.27% | 61.00% | 3.900 | 1.1142 | 222.843 | 10/22s | 87.00% |

## Exit Archetypes

| Archetype | N | Share | WR | PF | Exp SOL | Net SOL | Profit share | Loss share | Hold P50/P90 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| RAPID_SINGLE_FULL_EXIT | 1603 | 34.22% | 23.64% | 0.247 | -0.0705 | -112.941 | 2.68% | 31.18% | 2/2s |
| TWO_STAGE_BACK_LOADED_35_40_FIRST | 719 | 15.35% | 53.69% | 1.888 | 0.0926 | 66.547 | 10.21% | 15.57% | 3/5s |
| TWO_STAGE_FRONT_LOADED_60_FIRST | 235 | 5.02% | 70.21% | 5.329 | 0.2855 | 67.088 | 5.96% | 3.22% | 5/7s |
| THREE_TO_FIVE_STAGE_LADDER | 1226 | 26.17% | 67.54% | 6.096 | 0.4240 | 519.811 | 44.87% | 21.20% | 9/16s |
| SIX_STEP_30_17_RESIDUAL_LADDER | 702 | 14.98% | 49.72% | 1.072 | 0.0130 | 9.151 | 9.82% | 26.39% | 19/22s |
| OTHER_SIX_PLUS_COMPLEX_LADDER | 200 | 4.27% | 86.00% | 31.346 | 1.7745 | 354.903 | 26.46% | 2.43% | 23/34s |

## Size x Exit

Cramer's V: **0.2598**
Global-majority exit predictability: **34.22%**
Size-conditioned modal predictability: **43.82%**

## Recommendation

Use all 4,685 clean, reconciled 2CQ entry episodes as one causal population. Preserve the outcome-blind entry-size mode as a teacher action/conviction label and stratification variable. Do not condition inclusion on the eventual exit archetype.

## Interpretation Boundary

- Groups are formed from entry size and realized sell structure only; no pre-entry alpha features were read.
- Failed-attempt fee overhead is not allocated to archetypes because it cannot be linked reliably to a successful episode.
- Right-censored retained inventory is excluded by the clean-episode contract, so it is not mislabeled as a closed exit archetype.
