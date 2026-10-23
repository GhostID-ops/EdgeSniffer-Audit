# Pump Exact Alpha V2 Feature Encyclopedia

Research date: 2026-09-06  
Status: research only; stop for human review  
Scope: canonical, SOL-quoted Pump bonding-curve launches; Mayhem excluded

## Executive conclusion

V1 is a good narrow tape baseline, not a complete feature universe. It measures 36 receive-time features covering event counts, cumulative exact quote flow, wallet-level concentration, one binary wallet-history proxy, rough curve state, and four platform-activity counts. The frozen scanner actually admits 35 of them: every numeric field except `mintAgeMs`.

The largest omissions are not more arbitrary thresholds. They are:

1. value-weighted and unique-participant dynamics at the same short windows;
2. fixed-state curve-path efficiency;
3. current and entity-adjusted ownership;
4. causal wallet/creator histories;
5. funding, co-fire, route, bundle, fee, and network context;
6. later, explicitly asynchronous metadata/narrative context.

The strongest V2 thesis is:

> rapid canonical curve progress + accelerating quote flow + growing independent/entity-adjusted participation, conditioned on route/network regime and vetoed by creator-linked or coordinated concentration.

That state is economically different from “many buys,” “low first buyer share,” or “high net buy” alone.

No V1 feature, threshold, scanner, collector, economic semantic, service, wallet, or outcome file was modified or inspected. No validation/holdout data was opened. No deployment or trading action was taken.

## Direct verdict

### 1. How complete is the current V1 feature universe?

V1 is a strong basic low-latency microstructure baseline, but materially incomplete.

- It is complete enough to describe **raw early flow level**: buy/sell counts, gross/net quote, simple count velocity/acceleration, raw wallet breadth, wallet-level HHI/top share, reserve movement, approximate market cap, failures, and global activity counts.
- It is incomplete for **flow shape**: no value-window velocity, trade-size distribution, inter-arrival/burst structure, unique-buyer acceleration, time/trades to fixed liquidity, or canonical curve-completion feature.
- It is incomplete for **identity and control**: no entity clustering, funding graph, creator linkage, wallet quality, creator history, recurring cohort, or current holder inventory.
- It is incomplete for **execution context**: no direct/CPI route, create-and-buy atomicity, CU price/limit, priority fee percentile, Jito tip/bundle, slot phase, account locks, or feed-health conditioning.
- It is almost empty for **metadata and attention**.

A defensible qualitative estimate is that V1 covers roughly one quarter of the practical feature families in this encyclopedia and less than half of the derivatives directly reconstructible from its raw Pump transaction evidence. That is a taxonomy judgment, not a measured performance percentage.

### 2. What important predictor families are missing?

The main missing families are:

- quote-flow velocity and acceleration;
- unique-buyer growth/acceleration;
- trade-size distribution and arrival-process shape;
- fixed-liquidity curve paths: time/trades to X SOL, reserve move per transaction and per millisecond;
- current token ownership rather than gross buy-flow ownership;
- entity-adjusted concentration and sybil likelihood;
- creator-linked flow, creator prior dump behavior, and creator-linked recurrent cohorts;
- ex-ante wallet quality, recency, holding style, and co-fire cohorts;
- funding provenance and prelaunch hub-and-spoke graphs;
- transaction route, instruction topology, fees/tips, true bundle identity, and slot position;
- relative/cross-sectional token strength and market/network conditioning;
- on-chain metadata fingerprints and prior reuse;
- asynchronous metadata, semantic, image, social, and domain signals for later decisions only;
- explicit data-health, cache-coverage, transport-version, and drift controls.

### 3. Which missing features can be reconstructed from the existing raw ledger?

At least 39 registry candidates are directly available but unfeatured, and many of the 39 `PARTIALLY_PRESENT` extensions can also be derived without changing the collector.

The existing raw evidence is sufficient for:

- value-window flow, unique-wallet windows, size moments/quantiles, inter-arrivals, burstiness, sign runs, and first-sell latency;
- time/trades to fixed cumulative quote and reserve-progress efficiency;
- Pump-trade-derived current inventories, HHI/top-k, early-buyer retention, and round trips, with the explicit limitation that non-Pump SPL transfers are missing;
- create-and-buy same transaction, role equality, signer/instruction/CPI shape, direct invocation, route fingerprints, compute budget, transaction fee, on-chain Jito-tip transfers, account footprint, address-table use, and buy constraint aggressiveness;
- creator/name/symbol/URI/cashback/fee-recipient/shareholder fields already present in create/event/raw transaction evidence;
- richer Pump-local global flow, unique-wallet, active-mint, failure, slot, collector-health, and exact fee-regime controls.

Historical Pump-only wallet/creator caches can be built prospectively from already retained ledger history if coverage is sufficient. Their values must be reconstructed **as of each decision**, never from a final all-time index.

The important qualifier is live parity: the research builder pairs first-observation timestamps with later finalized transaction decoding. That can make a feature causally reconstructible in research without proving that the same decoded value was locally usable at that millisecond in production. Tier A in the registry means “feasible from a direct local stream decode,” not “V1 has proven 0–25 ms online availability.”

### 4. Which require new prospective collection?

The registry marks 23 feature entries `REQUIRES_NEW_PROSPECTIVE_DATA`. The irrecoverable/highest-value inputs are:

- general SOL transfer/funding edges outside Pump transactions;
- mint-specific non-Pump SPL token transfers;
- authenticated Jito bundle membership/order;
- independent multi-feed first-arrival timestamps and transport versions;
- broader account-lock/network fee-market observations;
- precise causal slot/leader/health snapshots where not already persisted;
- immutable metadata JSON/media fetch snapshots;
- launch-time social/domain snapshots;
- timestamped SOL/USD ticks.

These are raw-input recommendations only. They are not authorization to change the collector.

### 5. What are the five most promising new feature families?

1. **Entity-adjusted distributed demand.** Independent buyer count, entity HHI, common-funder share, cluster-adjusted holder concentration, and coverage/confidence.
2. **Fixed-state liquidity efficiency.** Time and trades to fixed exact quote/curve state, reserve movement per transaction, reserve movement per millisecond, and causal relative percentiles.
3. **Creator behavior on every launch.** Prior launch recency/count, prior dump latency, current creator-linked buy/sell/retention, and recurring early-buyer relationships.
4. **Ex-ante wallet/cohort quality.** Prior-only graduation/realized behavior with shrinkage, activity recency, co-fire similarity, quality-weighted flow, and cache coverage.
5. **Transaction/auction fingerprints.** Direct versus CPI route, create-and-buy atomicity, instruction topology, priority fee/tip relative to regime, true bundle membership, and slot/account-lock context.

Metadata/narrative is a sixth family worth preserving, but it belongs primarily to a separate late feature head because web retrieval cannot honestly be Tier A.

### 6. Does research support the N3000 low-first-buyer-concentration observation?

Mechanistically: **yes, conditionally.** Lower first-print share can represent more distributed follow-through and less dependence on one early wallet. Current V1’s definition is the **first buy transaction’s share of cumulative gross buy quote**, not the eventual cumulative share of the first buyer.

Empirically: **indirectly, not yet specifically.** MELT reports substantially higher early-holder concentration in high-risk migrated Pump tokens and an even larger separation after wallets are grouped into bundles/entities; its open-source feature code includes Gini, top-k, sniper-window, early-buyer-retention, and cluster-adjusted definitions ([paper](https://arxiv.org/abs/2602.13480), [feature code](https://github.com/git-disl/MELT/blob/master/src/feat_gen.py)). Practitioner work also reports deployer-funded split-wallet sniping patterns, but that remains grade-C hypothesis evidence ([Pine Analytics](https://pineanalytics.substack.com/p/exit-liquidity-machines)).

There are three reasons not to overclaim:

- Those studies do not validate V1’s exact first-transaction-share definition at 0–25 ms for short scalp PnL.
- Wallet splitting can manufacture a low raw first-buyer share.
- Recent Pump-specific work finds that reaching the same liquidity state quickly with **fewer** trades is strongly associated with graduation, so “more distributed prints is always better” is not supported ([Marino et al.](https://arxiv.org/html/2602.14860)).

The clean synthesis is an interaction, not a retuned threshold:

> low first-print share + low **entity-adjusted** concentration + positive unique-entity growth + fast curve progress.

This research did not inspect N3000 outcomes or retune the frozen low-first-buyer-share fingerprint.

### 7. What would a substantially smarter V2 feature set look like?

A smarter V2 would have separate causal heads rather than one flat brute-force feature soup:

- **Immediate A head (0–25 ms):** canonical curve state, exact quote flow, unique growth, size/arrival structure, raw concentration, transaction roles/route/fees, safety/version/data-health controls.
- **Cached B head (25–100 ms):** entity concentration, creator and wallet histories, co-fire/funding lookups, prior-only relative percentiles, leader/fee/tip regime.
- **Enriched C head (100–500 ms):** authenticated bundle joins, transfer-complete ownership, broader contention, multi-feed latency, heavier motif computations.
- **Late D/E head:** immutable metadata/content/social/domain signals, never required by local buy/sell execution.

The model/search surface should emphasize the ten preregistered economic interactions in `PRIORITIES.json`, checkpoint-specific missingness, and causal cache versions. It should not independently scan hundreds of near-duplicate thresholds and then report the best combination.

Research promotion remains:

> offline fingerprint → prospective paper capture → quote/live-parity paper → dust execution calibration → larger live sizing.

No later gate is implied by this encyclopedia.

### 8. What raw information should be preserved immediately before it is lost?

Yes. The top ten are, in priority order:

1. global SOL funding transfers;
2. mint-specific non-Pump SPL transfers;
3. authenticated Jito bundle membership/order;
4. multi-feed arrival/sequence/transport telemetry;
5. broader writable-account and fee-market observations;
6. slot/leader/collector-health snapshots;
7. immutable metadata JSON responses;
8. metadata media bytes/content/perceptual hashes;
9. launch-time social/domain snapshots;
10. timestamped SOL/USD ticks.

Exact field-level requirements and hot-path restrictions are in `PRIORITIES.json`. Collection should be considered separately by a human; it was not implemented here.

## Research synthesis

### Official protocol and network mechanics

Pump documents a deterministic constant-product bonding curve: every buy moves price up and every sell moves it down; graduation is automatic and irreversible, with migrated liquidity becoming protocol-owned on PumpSwap ([Pump bonding curve](https://pump.fun/docs/bonding-curve)). This makes reserve-path, exact quote-flow, curve progress, and price-impact features more defensible than generic “market cap” approximations.

The official create specification exposes name, symbol, URI, creator, Mayhem mode, and cashback configuration, and supports create plus buy in one transaction ([coin creation](https://github.com/pump-fun/pump-public-docs/blob/main/docs/instructions/COIN_CREATION.md)). The official IDL/event surface also exposes trade roles, reserves, fee fields, creator fields, quote mode, cashback, and shareholder information ([Pump IDL](https://github.com/pump-fun/pump-public-docs/blob/main/idl/pump.ts)). These are local-decode candidates; fetching the URI is not.

PumpSwap has canonical pool identity and reserve semantics, including effective quote reserves based on raw vault balance plus virtual quote reserves ([PumpSwap README](https://github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_SWAP_README.md)). Those fields matter for a later post-graduation sibling, but post-graduation state is future leakage for immediate curve entry.

Solana priority fee is determined by the requested compute-unit limit and compute-unit price; actual priority and scheduling context therefore require both, not just total fee ([Solana fee documentation](https://solana.com/docs/core/fees)). Transactions also expose signers, ordered instructions, account locks, address-table use, and inner-instruction structure ([Solana transactions](https://solana.com/docs/core/transactions)).

Jito bundles contain up to five transactions, execute atomically and in order in one slot, and compete in short account-lock-aware auctions using tip/compute efficiency ([Jito low-latency send](https://docs.jito.wtf/lowlatencytxnsend/)). An on-chain transfer to a Jito tip account is useful but does **not** prove bundle identity. The official low-latency feed documentation now carries a September 5, 2026 retirement notice for the legacy ShredStream feed ([Jito feed documentation](https://docs.jito.wtf/lowlatencytxnfeed/)). That is a transport/version and data-continuity risk; this research does not prescribe or make an infrastructure change.

### Pump-specific empirical work

Marino et al. analyze 655,770 Pump launches from September 2025, of which 4,338 graduated. Their strongest time-consistent result is liquidity accumulation efficiency: reaching the same vSOL state rapidly and in fewer trades predicts graduation, including after excluding the most nearly instantaneous cases. They also evaluate direct-contract share as a bot-like proxy, ex-ante successful-trader presence, prolific creators, and creator sell dynamics ([paper](https://arxiv.org/html/2602.14860)).

Implications:

- fixed-state time/trade features deserve priority over more raw checkpoints;
- direct invocation is a useful route proxy, not a bot ground truth;
- trader success must be frozen ex ante;
- creator history is not monotonically good merely because a creator is prolific;
- creator sell behavior and multiwallet linkage are more relevant than creator count alone.

MELT/MemeTrans provides a large Pump lifecycle corpus and auditable definitions for holder Gini/top-k concentration, sniper-time holdings, early-buyer retention, trade activity, buy/sell pressure, and entity/bundle-adjusted concentration ([paper](https://arxiv.org/abs/2602.13480), [code](https://github.com/git-disl/MELT/blob/master/src/feat_gen.py)). The important limitation is horizon translation: many published features are measured near migration or over a full pre-migration history. V2 may adapt their definitions causally at 100 ms–10 s; it may not treat later values as launch-time features.

SOLMEMES contributes metadata, images, social links, and minute-level trading data for roughly 9,000 migrated Solana meme tokens. Text embeddings add modest value when combined with temporal features, but an embedding-only story is weak, and the graduated-token population creates severe selection bias for universal launch prediction ([paper](https://doi.org/10.1145/3789982.3790023), [dataset](https://huggingface.co/datasets/rucyfer/solmemes)).

Broader Solana rug research supports funding graphs, recurring entities, creator-level aggregation, wash-trading skepticism, and metadata/social unreliability. It is mostly retrospective and crosses protocols, so it is strongest as risk-family evidence, not proof of immediate Pump alpha ([From Hype to Collapse](https://arxiv.org/html/2603.24625)). Generic mint-authority, freeze-authority, custom honeypot, and creator-removable-LP features should not be imported as discriminative exact-Pump predictors when the canonical program standardizes those mechanics.

### Evidence interpretation

The registry uses:

- **A:** repeated credible empirical support and/or unusually direct mechanism;
- **B:** one strong study/open definition or strong mechanism not yet replicated for this exact objective;
- **C:** indirect/practitioner association worth a preregistered test;
- **D:** speculative, hindsight-dependent, cross-protocol mismatch, or too weak for promotion.

An A grade is not a claim of causality. Most market features are predictive associations sitting on plausible mechanisms. Causal correctness here means only that the input could have been known at the decision.

## Exact V1 inventory

### Observation and cutoff semantics

- Fixed checkpoints: `25, 50, 100, 250, 500, 1000, 2000, 5000, 10000 ms`.
- Special state: `FIRST_IMPULSE_EVENT`, timestamped at the first buy receive time.
- A fixed row is emitted only if at least one trade event has been observed. V1 therefore omits zero-flow launch rows and creates checkpoint-dependent selection.
- Per-mint events are included when `arrival_ms <= decision_ms`.
- Trailing event windows are left-open/right-closed: `d-window < arrival <= d`.
- Global prior windows are left-closed/right-open: `d-window <= arrival < d`.
- The clock is collector receive time, not block time.
- `FIRST_IMPULSE_EVENT` is a state label, not a guarantee that only one buy shares that receive timestamp.
- The raw ledger preserves byte-level Yellowstone/RPC evidence, receive nanoseconds, writer sequence, slot, transaction index, signatures, accounts, instructions, inner instructions, logs, status, and decoder evidence. The research builder uses later finalized transaction material to reconstruct exact event values while honoring earlier observation timestamps. That is suitable for causal offline rows only if availability lineage is preserved; it is not itself a live-latency proof.

### Feature table

Every feature below is emitted at every V1 observation state when the row itself exists, except `marketCapSol`, which is null until a causally observed supply snapshot exists. “Scanned” means the generic V1 quantile scanner admits it.

| # | Feature | Exact V1 definition | Raw fields | Scanned | Main limitation |
|---:|---|---|---|:---:|---|
| 1 | `mintAgeMs` | decision receive ms − create receive ms | create/event arrival | No | excluded by scanner; fixed rows without events do not exist |
| 2 | `buyCount` | cumulative buys through decision | trade side/arrival | Yes | washable count |
| 3 | `sellCount` | cumulative sells through decision | trade side/arrival | Yes | no size/identity weighting |
| 4 | `buyCount25ms` | buys in `(d−25,d]` | buy arrivals | Yes | provider batching dominates |
| 5 | `buyCount50ms` | buys in `(d−50,d]` | buy arrivals | Yes | provider batching |
| 6 | `buyCount100ms` | buys in `(d−100,d]` | buy arrivals | Yes | raw wallets/prints |
| 7 | `buyCount250ms` | buys in `(d−250,d]` | buy arrivals | Yes | no unique/value window |
| 8 | `buyCount500ms` | buys in `(d−500,d]` | buy arrivals | Yes | no unique/value window |
| 9 | `buyCount1000ms` | buys in `(d−1000,d]` | buy arrivals | Yes | no unique/value window |
| 10 | `sellCount100ms` | sells in `(d−100,d]` | sell arrivals | Yes | count-only |
| 11 | `sellCount500ms` | sells in `(d−500,d]` | sell arrivals | Yes | count-only |
| 12 | `sellCount1000ms` | sells in `(d−1000,d]` | sell arrivals | Yes | count-only |
| 13 | `uniqueBuyers` | distinct cumulative buy `user` wallets | buy user | Yes | wallet ≠ beneficial entity |
| 14 | `uniqueSellers` | distinct cumulative sell `user` wallets | sell user | Yes | wallet ≠ entity |
| 15 | `grossBuySol` | sum buy immediate quote / 1e9; includes SOL + protocol fee + creator fee + cashback | trade quote/fees | Yes | frozen cash-cost semantics differ from reserve input |
| 16 | `grossSellSol` | sum sell immediate quote / 1e9; cash proceeds net of protocol/creator fees | trade quote/fees | Yes | frozen cash-proceeds semantics |
| 17 | `netBuySol` | exact gross buy − exact gross sell | prior two | Yes | cumulative; no local derivative |
| 18 | `buyVelocity100` | `buyCount100ms × 10` | count | Yes | exact duplicate transform |
| 19 | `buyVelocity500` | `buyCount500ms × 2` | count | Yes | exact duplicate transform |
| 20 | `buyAcceleration100` | buys `(d−100,d]` − buys `(d−200,d−100]` | buy arrivals | Yes | count-only; early older bucket is prelaunch |
| 21 | `sellPressure` | gross sell lamports / `max(gross buy lamports,1)` | exact quote | Yes | tiny-sample saturation; washable |
| 22 | `firstBuyerShareBps` | first buy event quote / cumulative gross buy quote × 10,000 | ordered buy quote | Yes | first transaction, not first wallet cumulative share; sybil/split sensitive |
| 23 | `topBuyerShareBps` | largest cumulative buyer-wallet quote share × 10,000 | user/quote | Yes | sybil sensitive; duplicates dominance |
| 24 | `buyerHhi` | sum squared cumulative buyer-wallet quote shares | user/quote | Yes | wallet clustering absent |
| 25 | `recurrentWalletShareBps` | buy-flow share from wallets with any earlier observation on another mint | prior wallet index | Yes | binary earliest-ever proxy; no recency/frequency/skill |
| 26 | `transactionDensity` | distinct trade signatures observed | signature | Yes | a count, not time density |
| 27 | `sameSlotBuyCount` | buys with slot equal to create slot | create/buy slot | Yes | not same transaction or bundle |
| 28 | `failedTransactionCount` | attributed failed Pump observations through decision | failed index/mint | Yes | failed mint discovery is incomplete |
| 29 | `earlyReserveMovementPct` | `(latest post-vSOL − first event pre-vSOL)/first pre-vSOL × 100` | pre/post reserves | Yes | base is first observed event, not guaranteed canonical initial |
| 30 | `marketCapSol` | `vSOL × supply / vToken / 1e9`, if supply observed by decision | reserves/supply snapshot | Yes when numeric | nullable RPC timing; live Tier A not established |
| 31 | `marketCapAvailable` | 1 if an as-of supply snapshot exists | supply observed time | Yes | mostly data availability/provider load |
| 32 | `dominance` | largest cumulative buyer-wallet quote share | user/quote | Yes | exact scaled duplicate of topBuyerShareBps |
| 33 | `globalTrades1sPrior` | all Pump trades in `[d−1s,d)` | global trade arrivals | Yes | count only |
| 34 | `globalTrades5sPrior` | all Pump trades in `[d−5s,d)` | global trade arrivals | Yes | count only |
| 35 | `globalTrades60sPrior` | all Pump trades in `[d−60s,d)` | global trade arrivals | Yes | count only |
| 36 | `globalLaunches60sPrior` | creates in `[d−60s,d)` | create arrivals | Yes | no first-impulse/quality distinction |

The scanner groups rows by observation state, considers every numeric feature except `mintAgeMs`, derives univariate quantile directions, and ranks limited two-/three-feature combinations. `marketCapSol` participates only where numeric.

The four hard-coded descriptions are:

- `FAST100`: `FIXED_100MS`, `buyCount100ms >= 2`
- `FIRST_IMPULSE`: `FIRST_IMPULSE_EVENT`, `buyCount >= 1`, `sellCount <= 0`
- `FIRST_IMPULSE_FAST100`: `FIXED_100MS`, `buyCount100ms >= 2`, `sellPressure <= 0.1`
- `OLD_RANKER_COMPONENT_PROXY`: `FIXED_1000MS`, `buyCount >= 2`, `netBuySol >= 0`, `firstBuyerShareBps <= 5000`

The scanner checkpoint constants remain N3000, N5000, and N10000. Nothing here changes or evaluates those checkpoints.

## Registry coverage

`FEATURE_REGISTRY.jsonl` contains 179 complete feature records:

| V1 relationship | Count | Meaning |
|---|---:|---|
| `ALREADY_PRESENT` | 36 | exact frozen V1 feature |
| `PARTIALLY_PRESENT` | 39 | V1 has a precursor but not the proposed definition |
| `RAW_DATA_AVAILABLE_BUT_NOT_FEATURED` | 39 | current raw evidence can reconstruct it |
| `REQUIRES_PRECOMPUTED_CACHE` | 36 | causal history/reference state must be maintained as-of |
| `REQUIRES_NEW_PROSPECTIVE_DATA` | 23 | input is absent and can be lost |
| `NOT_CAUSALLY_FEASIBLE` | 6 | future, irrelevant to immediate Pump entry, or forbidden |

Earliest realistic tiers:

| Tier | Count | Interpretation |
|---|---:|---|
| A | 107 | direct local stream decode/rolling state, 0–25 ms in a prospective implementation |
| B | 52 | in-memory/local cache or modest local derivation, 25–100 ms |
| C | 8 | heavier join/side feed, 100–500 ms |
| D | 5 | asynchronous metadata/service, 0.5–5 s |
| E | 7 | too late/unstable/future for immediate entry |

Counts include current V1 features, candidate predictors, risk filters, execution features, and mandatory quality controls. `P0` is deliberately broad for baseline retention, safety, instrumentation, and research value; the ranked top 20 in `PRIORITIES.json` is the actual new-feature order.

## Family-by-family assessment

### Deep microstructure

Highest-value additions:

- exact quote flow at 100/500 ms, not count velocity only;
- value acceleration and unique-buyer acceleration;
- current holder HHI/top-k and early-buyer retention;
- fixed quote-state crossing time and trade count;
- canonical curve completion and curve displacement efficiency;
- first-sell latency and creator/entity-linked sell decomposition;
- inter-arrival/size distribution as secondary diagnostics.

The best formulas are cheap rolling accumulators, counted sets, small order-statistic buffers, or first-crossing timestamps. Their dominant failure mode is not compute; it is spoofability and selection at tiny samples.

Trade-size CV, Gini, entropy, and quantile ratios need minimum-count and checkpoint controls. Zero-flow launches must exist in the V2 research population, even though V1 omits them, or relative features will inherit selection bias.

### Wallet and graph

Persistent wallet presence is not automatically alpha. The same history can identify:

- a skilled early participant;
- a fast extractor;
- a launch service;
- a sybil cluster;
- a popular-market participant;
- simply an old wallet.

Therefore every history feature needs recency, sample count, behavior sign, and coverage. Performance summaries must use only outcomes completed before the current decision and must be shrinkage-adjusted. Entity graphs must be timestamp-versioned; a funding edge or co-fire discovered tomorrow cannot relabel yesterday’s 50 ms row.

Funding provenance is the most important irrecoverable gap. The present ledger follows Pump program traffic, not the full universe of System Program transfers/account histories. It cannot reliably infer general first funding, multi-hop funders, wallet age, or CEX provenance after the fact.

### Creator

Creator information should be attached to every launch, not split into a separate fixed-wallet strategy.

Most promising:

- prior launch count and recency;
- prior dump latency/censoring;
- creator-linked current buy/sell/retention;
- recurring early-buyer cohort;
- prior-only failure/graduation/extraction summaries;
- creator funding and identity coverage.

Recent Pump-specific evidence cautions that prolific creator status alone is not broadly predictive. Creator sell behavior and relationships are stronger mechanistic candidates than a raw “creator win rate.”

### Metadata, social, and attention

Name, symbol, URI, creator, Mayhem, and cashback are create-time local fields. Lexical shape, URI type, exact prior URI reuse, and locally cached brand similarity can be A/B tier.

The URI’s content is different: metadata JSON, image, social, domain, and semantic features require network retrieval. They belong to D/E or a late model unless the exact content hash is already in a causal local cache. SOLMEMES supports incremental metadata/text value, but its graduated-only population and minute horizon make direct 25 ms transfer invalid.

Store immutable snapshots with request start/end, observed time, resolved URL, status, MIME, raw bytes/hash, and error lineage. Mutable content must never be retroactively backfilled.

### Market and network regime: alpha vs risk vs execution

| Role | Candidate variables | Proper use |
|---|---|---|
| Alpha/context | Pump gross/net flow, unique active wallets, active mints, token cross-sectional flow/curve-efficiency percentile | condition token signal on opportunity regime |
| Risk/filter | global failure rate, launch first-impulse rate, recent graduation rate with censoring, feature drift, collector health | fail closed or reduce confidence; not necessarily directional alpha |
| Execution | CU limit/price, priority/tip percentile, leader proximity, slot phase/jitter, account-lock contention, multi-feed dispersion | estimate inclusion/latency and live-parity risk |

UTC/day encodings can be context features, not hard skip-hour rules. Existing v4 evidence in project instructions rejects the previous dead-hour thesis; this report does not revive it.

## Priority outputs

The machine-readable ranked lists are in `PRIORITIES.json`.

### Top 20 missing features

1. `WAL-011` entity-adjusted buyer HHI
2. `MIC-035` canonical curve completion
3. `MIC-023` milliseconds to 0.5 SOL gross buy
4. `MIC-024` buys to 0.5 SOL gross buy
5. `WAL-010` entity-adjusted unique buyers
6. `RISK-002` cluster-adjusted holder-HHI delta
7. `MIC-036` current holder HHI
8. `MIC-025` reserve movement per buy
9. `REG-007` causal curve-efficiency percentile
10. `MIC-005` unique buyers in trailing 100 ms
11. `MIC-001` gross buy SOL in trailing 100 ms
12. `MIC-004` value-weighted 100 ms acceleration
13. `WAL-005` prior-only shrunk buyer graduation rate
14. `WAL-019` buyer-quality-weighted flow
15. `WAL-012` common-funder buy share
16. `WAL-013` creator-linked buy share
17. `CRE-006` prior creator dump latency
18. `WAL-009` early-buyer co-fire similarity
19. `TX-006` direct Pump invocation share
20. `TX-001` create and buy in the same transaction

### Top 10 economic interactions

1. fast curve progress × entity-adjusted distributed demand;
2. gross-flow acceleration × unique-buyer acceleration;
3. first-buyer share × entity concentration × follow-through;
4. creator-linked flow × creator dump history × live first-sell latency;
5. wallet quality × flow share × history coverage;
6. common funder × co-fire × atomic/bundle coordination;
7. holder concentration × cluster-adjustment delta × non-trade transfers;
8. route urgency × causal network auction regime;
9. token relative strength × platform breadth/congestion;
10. metadata reuse × serial creator × recurring cohort.

Each interaction’s hypothesis, falsifier, prerequisites, and economic explanation is recorded in `PRIORITIES.json`.

## Causal and validation safeguards

- Persist event-time and observation-time separately.
- Use the earliest actual availability of each raw input, not the final reconstructed record time.
- Never backfill later RPC, metadata, graph, graduation, sell, or social information into an earlier feature row.
- Version every wallet/entity/creator/trend/service cache and support strict as-of lookup.
- Cross-fit any wallet-quality or entity model; never train a nested score on the same evaluation period.
- Keep UNKNOWN distinct from zero/benign.
- Carry sample count, cache coverage, graph confidence, collector health, and program/feed version.
- Keep checkpoints and feature definitions preregistered before prospective evaluation.
- Do not import later/migration-time paper features without a causal checkpoint adaptation.
- Treat direct invocation, Jito tip, same-slot, common funder, and social metrics as proxies, not ground-truth bot/insider labels.
- Preserve Mayhem as a fail-closed veto outside the alpha surface.
- Keep local Rust execution and do not introduce HTTP/API work to the buy/sell hot path.
- Keep Jupiter fallback off.

## Deliverables

- `FEATURE_REGISTRY.jsonl`: 179 full records, one valid JSON object per line.
- `FEATURE_REGISTRY_SCHEMA.json`: field contract and tier/status vocabulary.
- `PRIORITIES.json`: ranked top 20 features, top 10 interactions, and top 10 prospective raw additions.
- `SOURCES.json`: local-code and public-source catalog with evidence rubric.
- `REPORT.md`: this human synthesis.

Human review is the next gate. No implementation is part of this task.
