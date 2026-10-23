# EXACT_CURVE_ECONOMICS_V1 Validation

Generated from the frozen replay completed at `2026-09-06T00:28:05.616459Z`.

## Safety State

| Item | Verified state |
|---|---|
| N25-v3 emitter/new-entry path | stopped at `2026-09-05T22:57:42Z` |
| N25-v3 receiver/exit/reconciliation | active, PID `1357511`, zero restarts |
| Canonical Ranker collector | active, PID `1352469`, zero restarts |
| Source feed | active and left unchanged |
| V3 counter | preserved at `4 / 25` |
| V3 history | preserved |
| Finalized wallet inventory | no nonzero SPL or Token-2022 balances at slot `444656847` |

Only the emitter was stopped. No counter reset, live deployment, strategy change,
or new buy was performed.

## Evaluator Contract

`EXACT_CURVE_ECONOMICS_V1` is a standalone, no-submit evaluator. It:

- obtains the latest causal Pump reserve state before the intended entry;
- injects the hypothetical exact-quote buy with integer curve math and its own
  reserve impact;
- replays successful external transactions chronologically;
- treats finalized failed transactions as zero-mutation events;
- validates supported Pump buy/sell instruction semantics against emitted
  `TradeEvent` amounts and pre/post reserves;
- reads the exact on-chain `FeeConfig` and applies protocol, creator, cashback,
  and buyback-allocation semantics;
- applies known buy/sell output constraints;
- models frozen `TP +20% / SL -20% / true 120s max hold` outcomes using P50,
  P75, and P90 first-sellable delays of `964 / 1476 / 1989 ms`;
- excludes missing, stale, unsupported, or causally ambiguous evidence rather
  than substituting market-cap-ratio economics.

The normalized `PnL @1 SOL` fields are linear scaling of returns measured at the
actual dust size. They are not a nonlinear exact-curve replay of a 1 SOL order.

## Validation Evidence

| Check | Result |
|---|---:|
| Deterministic tests | 9 / 9 pass |
| RPC transactions requested / available | 7,994 / 7,994 |
| Authoritative trade transitions reproduced | 346 |
| Inventory mismatches admitted | 0 |
| Reserve-continuity mismatches admitted | 0 |
| Fee/instruction mismatches admitted | 0 |
| Market-cap fallback rows | 0 |
| Finalized failed transactions zeroed | 2,251 |

Failed-transaction constraint replay found `1` definite output-constraint
failure, `1,566` failures whose output constraint would have passed but which
failed for another on-chain reason, and `684` explicitly ambiguous failures.
The definite failure was a finalized `sell_v2` Custom 6003 whose exact output
was `24,494,750` lamports against a `28,517,037` lamport minimum.

## Full Matched Cohort

`33` live buys were discovered. `20` had complete, non-ambiguous evidence and
were admitted. `13` were explicitly excluded.

| Model | N | W/L | WR | PF | Expectancy | Linear PnL @1 SOL |
|---|---:|---:|---:|---:|---:|---:|
| Exact curve P50 | 20 | 2 / 18 | 10.00% | 0.121 | -5.258% | -1.0516 SOL |
| Exact curve P75 | 20 | 2 / 18 | 10.00% | 0.121 | -5.258% | -1.0516 SOL |
| Exact curve P90 | 20 | 2 / 18 | 10.00% | 0.121 | -5.258% | -1.0516 SOL |
| Actual curve economics | 20 | 2 / 18 | 10.00% | 0.109 | -5.379% | -1.0759 SOL |
| Actual network-adjusted | 20 | 2 / 18 | 10.00% | 0.106 | -5.477% | -1.0953 SOL |
| Old canonical MC model | 20 | 0 / 20 | 0.00% | 0.000 | -4.622% | -0.9243 SOL |

P50/P75/P90 are identical in this sample because no admitted trade crossed TP
or SL in the interval between those first-sellable boundaries. That is a sample
property, not evidence that sellability latency is generally irrelevant.

Outcomes were `19` true max-hold and `1` SL, with no TP outcome.

## Accuracy Versus Live

For exact P75 versus actual curve economics:

- median absolute return difference: `0.000 percentage points`;
- mean absolute return difference: `0.121 percentage points`;
- worst absolute return difference: `1.318 percentage points`;
- 16 of 20 trades matched actual curve return exactly;
- the four differences were caused by frozen T+120 state versus the actual sell
  timing/state, not approximate entry arithmetic.

Actual network costs reduced expectancy by a further `0.097 percentage points`
relative to actual curve economics.

The old canonical model differed from exact P75 by `5.074 percentage points`
mean absolute and `26.346 percentage points` worst. It also misclassified two
exact winners as losses:

- `CbSBZUSKeHvJwgA3WFSqTowHRJ8DA43Hs3FP363Tpump`: old `-7.77%`, exact `+6.32%`.
- `6eR5sym3PniNf84fMPdjmEfV9PeRXXdCECKBfnX1pump`: old `-1.77%`, exact `+8.13%`.

The old MC-ratio model is therefore retired as executable truth.

## N25-v3 Translation

The four completed V3 trades are tiny-N descriptive evidence only:

| Model | N | W/L | PF | Expectancy | Linear PnL @1 SOL |
|---|---:|---:|---:|---:|---:|
| Exact P75 | 4 | 1 / 3 | 0.916 | -0.187% | -0.0075 SOL |
| Actual curve | 4 | 1 / 3 | 0.916 | -0.187% | -0.0075 SOL |
| Actual network-adjusted | 4 | 1 / 3 | 0.875 | -0.287% | -0.0115 SOL |
| Old canonical | 4 | 0 / 4 | 0.000 | -3.605% | -0.1442 SOL |

Exact replay and actual curve economics are identical for all four V3 trades.
For those trades, entry/exit translation does not explain the observed curve
losses; network costs account for about another `0.100 percentage points` of
expectancy. The sample is too small to judge Ranker alpha.

## Explicit Exclusions

| Reason | Count |
|---|---:|
| Finalized buy failed on-chain | 2 |
| Completed finalized sell unavailable | 3 |
| Actual sell truncated the tape before T+120 with no earlier TP/SL | 4 |
| No authoritative causal reserve observation at intended entry | 4 |

These 13 rows remain blockers and are not silently estimated.

## Artifacts

Immutable VPS output:

`/mnt/HC_Volume_106775198/edgesniffer-derived/exact-curve-economics-v1/validated-v1`

The output includes `aggregate.json`, `trades.jsonl`, `excluded.jsonl`,
`report.md`, P50/P75/P90 event tapes, and the exact evaluator source snapshot.
The frozen evaluator SHA-256 is:

`badf8d417a155931498422300ffadb5ba1b3e3574bbcad9fd62d52161144ff10`

## Verdict

`EXACT_CURVE_ECONOMICS_V1_VALIDATED`

This validates the evaluator, not Ranker profitability. The admitted cohort is
unprofitable under exact executable economics, while 13 discovered buys remain
properly excluded for incomplete or non-causal evidence. No new alpha or live
deployment is authorized by this result.
