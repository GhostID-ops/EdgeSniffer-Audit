# EVIDENCE_UNIVERSE_AND_STATIC_COST_BRIDGE_V1

## Final Verdicts

**Coverage:** `EVIDENCE_UNIVERSE_SELECTION_BIAS_BLOCKER`

**Economic deficit:** `EXECUTION_COSTS_DOMINANT`

**Next branch:** `evidence-pipeline repair` (identified only; not run)

The exact universe is not currently representative enough for unrestricted alpha conclusions. Although 69.2% of non-evaluable mints were inert or provably outside the frozen decision window, 1,031 potentially eligible active mints lacked causal arrival evidence. Among mints that had or plausibly had an eligible state, exact coverage was only 76.09%.

The already-selected static top-10 cohort contained substantial idealized external movement on average, but the frozen executable path lost almost all of it to decision-to-landing ordering and smaller execution/fee effects. Its final modeled result remains negative; this is a diagnosis, not a signal rescue.

## Boundary And Integrity

| Check | Result |
|---|---:|
| Discovery ordinals | 1-6,848 only |
| Unique census mints | 6,848 |
| Exact-evaluable | 3,377 |
| Non-evaluable | 3,471 |
| Validation rows inspected | 0 |
| Holdout rows inspected | 0 |
| Ordinals above 6,848 inspected | 0 |
| Top-10 bridge | 194/194 exact |
| Top-5 bridge | 96/96 exact |
| Maximum bridge reconciliation error | 0.0 SOL |
| Collector | active/running, NRestarts=0 |

## Part A: Evaluability Census

| Mutually exclusive category | N | % all | % non-evaluable | Blocks 1/2/3/4/5 | Risk interpretation |
|---|---:|---:|---:|---:|---|
| INERT_OR_NO_EXTERNAL_ACTIVITY | 1,802 | 26.31% | 51.92% | 357/351/327/440/327 | Economically benign for the intended active-state question |
| ACTIVITY_EXISTED_BUT_NO_ELIGIBLE_DECISION_STATE | 600 | 8.76% | 17.29% | 104/108/209/107/72 | Benign for the frozen <=30s state universe; limits generalization to all active launches |
| RELEVANT_ACTIVITY_BUT_SOURCE_DATA_INCOMPLETE | 1,031 | 15.06% | 29.70% | 223/228/217/179/184 | Selection-bias dangerous |
| EXACT_REPLAY_OR_DIVERGENCE_FAILURE | 30 | 0.44% | 0.86% | 3/6/7/7/7 | Selection-bias dangerous |
| UNSUPPORTED_PROGRAM_TRANSACTION_OR_PAIR_FORMAT | 6 | 0.09% | 0.17% | 5/0/0/1/0 | Selection-bias dangerous |
| PARSER_COLLECTOR_OR_SOURCE_GAP | 2 | 0.03% | 0.06% | 1/0/0/1/0 | Selection-bias dangerous |
| OTHER_DOCUMENTED_REASON | 0 | 0.00% | 0.00% | 0/0/0/0/0 | None observed |

The 600 no-eligible-state mints include 538 with missing raw arrival evidence whose finalized block time proves the relevant external SOL activity occurred after the frozen 30-second window. Block time was used only to prove lateness; it was never substituted for causal arrival time.

### Coverage Denominators

| Pre-existing population | N | Exact N | Coverage |
|---|---:|---:|---:|
| All launches | 6,848 | 3,377 | 49.31% |
| Any successful external Pump economic activity | 5,040 | 3,377 | 67.00% |
| Had or plausibly had a frozen eligible state | 4,438 | 3,377 | 76.09% |
| All required source evidence present, including inert/late | 5,809 | 3,377 | 58.13% |
| Eligible state plus required source evidence present | 3,407 | 3,377 | 99.12% |

The last row explains the failure mode precisely. Once an eligible state and its required evidence exist, exact replay succeeds for 99.12%. The blocker is upstream coverage: 1,031 additional mints plausibly crossed the eligible state window but lack the causal arrival evidence needed to decide that safely.

### Is 49.31% Mostly Dead/Inert?

Numerically, most non-evaluable mints were benign for this frozen state universe: 1,802 inert plus 600 definitely ineligible equals 2,402/3,471, or 69.20%.

But the correct answer is **not “mostly dead, therefore fine.”** The 1,031 potentially eligible source-incomplete mints are 15.06% of all launches and 23.23% of the intended eligible-state population. That is material, non-random loss among active launches, so unrestricted alpha research is blocked until the source path is repaired or the missing population is otherwise causally resolved.

### Chronology And Regime

| Block | Ordinals | Raw exact | Active-launch exact | Source-incomplete N | No-eligible-state N |
|---:|---:|---:|---:|---:|---:|
| 1 | 1-1,370 | 49.42% | 67.16% | 223 | 104 |
| 2 | 1,371-2,740 | 49.42% | 66.44% | 228 | 108 |
| 3 | 2,741-4,110 | 44.53% | 58.49% | 217 | 209 |
| 4 | 4,111-5,480 | 46.35% | 68.35% | 179 | 107 |
| 5 | 5,481-6,848 | 56.87% | 74.74% | 184 | 72 |

Dangerous loss does not increase monotonically. Block 3 is the worst regime and the latest block is the best, but every block still contains material active missingness.

Higher-activity partitions had 66.51% active coverage versus 67.63% in lower-activity partitions, a 1.12 percentage-point difference. The loss is not primarily a congestion-only phenomenon.

Of the 1,031 source-incomplete mints, 951 (92.24%) contain mixed legacy/V2 instruction traffic, 43 are V2-only, and 37 are legacy-only. The six truly unsupported rows are legacy-only. Missingness is therefore broad across mixed transaction histories rather than isolated to one modern V2 layout.

## Part B: Static Top-10 Economic Bridge

Frozen parent result: N=194, PF=0.807, expectancy=-0.006822 SOL/trade, PnL=-1.323562 SOL.

| Bridge component | Mean SOL/trade | Median SOL/trade | Total SOL |
|---|---:|---:|---:|
| Idealized external market opportunity | +0.395821 | +0.079499 | +76.789212 |
| Decision-to-landing movement | -0.368431 | 0.000000 | -71.475523 |
| Own entry impact | -0.004753 | -0.004803 | -0.922092 |
| Buy Pump fee economic effect | -0.006395 | -0.006337 | -1.240612 |
| Counterfactual path interaction | -0.011159 | +0.003973 | -2.164803 |
| Own exit impact | -0.004640 | -0.004805 | -0.900229 |
| Sell Pump fee economic effect | -0.006256 | -0.006302 | -1.213575 |
| Base transaction fees | -0.000010 | -0.000010 | -0.001940 |
| Standardized delivery overlay | -0.001000 | -0.001000 | -0.194000 |
| **Final modeled PnL** | **-0.006822** | n/a | **-1.323562** |

The decision-to-landing component is tail-driven: median 0, P10 approximately -1.973 SOL, minimum -2.126 SOL. Most selected states had no intervening state movement during the modeled 37ms; a minority encountered very large ordering changes. The gross-opportunity total is not one-outlier-driven: its largest mint contributes about 2.83% of the total.

Ordinary own impact, Pump fee effects, base fees, and delivery sum to -0.023054 SOL/trade (-4.472448 SOL total). Decision-to-landing plus counterfactual path interaction sum to -0.379589 SOL/trade (-73.640326 SOL total). Removing ordinary fees alone would not address the dominant loss mechanism.

Raw fee charges per top-10 trade were approximately:

- Buy protocol: 0.004691 SOL; buy creator: 0.001481 SOL.
- Sell protocol: 0.004754 SOL; sell creator: 0.001501 SOL.
- Base transactions: 0.000010 SOL total.
- Priority/tip: 0.
- Delivery overlay: 0.001000 SOL.

Seventy-three of 194 rows had at least one counterfactual transaction constraint failure. Failed historical transactions mutate zero state and receive no invented wallet fee; their reserve-path consequence is included in counterfactual path interaction.

### Top-5 Compact Bridge

Frozen parent result: N=96, PF=0.808, expectancy=-0.007115 SOL/trade, PnL=-0.683070 SOL.

| Component | Mean SOL/trade | Median SOL/trade | Total SOL |
|---|---:|---:|---:|
| Idealized external market opportunity | -0.025448 | +0.064842 | -2.442978 |
| Decision-to-landing movement | +0.007454 | 0.000000 | +0.715564 |
| Own entry impact | -0.004862 | -0.004875 | -0.466741 |
| Buy Pump fee economic effect | -0.005832 | -0.006822 | -0.559871 |
| Counterfactual path interaction | +0.033957 | +0.004781 | +3.259884 |
| Own exit impact | -0.005123 | -0.005107 | -0.491792 |
| Sell Pump fee economic effect | -0.006252 | -0.006769 | -0.600176 |
| Base transaction fees | -0.000010 | -0.000010 | -0.000960 |
| Standardized delivery overlay | -0.001000 | -0.001000 | -0.096000 |
| **Final modeled PnL** | **-0.007115** | n/a | **-0.683070** |

Top-5 does not preserve the top-10 gross-opportunity structure: its mean idealized opportunity is negative even though its median is positive. It is not evidence that a tighter static tail is better.

## Interpretation

`EXECUTION_COSTS_DOMINANT` is the frozen top-10 diagnosis because idealized opportunity averages +0.395821 SOL/trade, well above the preregistered +0.01 threshold, while final expectancy is negative. Here “execution costs” is broad: the dominant term is causal ordering/state movement before landing, followed by counterfactual path interaction and ordinary impact/fee drag. It does not mean transaction fees alone caused the loss.

This does not prove that faster execution will make the cohort profitable, nor does it authorize tuning. The source-coverage blocker comes first because alpha conclusions drawn from only 76.09% of the potentially eligible population could be biased.

## Next Branch

The appropriate next branch is **evidence-pipeline repair**: recover or prospectively prevent missing causal raw-arrival evidence for active Pump transactions, then rebuild the eligible-state universe under the same frozen semantics. That branch was not run in this task.

No model, feature, threshold, exit, strategy, service, collector, validation set, holdout set, prospective lane, or live trading configuration was changed.
