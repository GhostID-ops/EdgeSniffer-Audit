# KEFI_MISSING_MECHANISM_RECONSTRUCTION_V1

Scope: offline reconstruction from opened historical and development evidence only. The live PRE_H3 qualification was not read beyond boundary/configuration and was not changed. No PRE_H3 actionability outcome, PnL, bootstrap, or evaluator result is included.

## Executive Summary

The retained kEFi evidence supports three concrete statements.

1. The historical kEFi sample is not 1,656 independent token decisions. It is 1,656 wallet BUY entries across 150 pools during 10 hours, comprising 150 first observed pool entries and 1,506 subsequent entries. There are 399 subsequent entries within 60 seconds of the previous entry in that pool, 775 between one minute and 15 minutes, and 332 after more than 15 minutes. H1 treated the broad public context as the decision object and did not represent this position evolution.

2. kEFi size is structured relative to liquidity. Median first entry was 0.814 SOL against 63.77 SOL of pre-trade quote reserves, or 1.269% of pool quote. Median repeat entry was 1.036 SOL against 86.27 SOL, or 1.199%. This is descriptive, not an economic threshold, but it shows that size and pool impact were real wallet behavior omitted from H1.

3. The retained source has a receipt timestamp and slot for kEFi entries, but no complete durable source sequence, public-state-first-observed record, or independently recorded kEFi-action arrival reference for this historical sample. It cannot answer whether the fingerprint existed before kEFi acted or became recognizable only because of his transaction. Historical apparent receipt lag relative to second-resolution event time is not a lead-time measurement.

Therefore the evidence supports continuing PRE_H3 unchanged as an infrastructure/actionability prerequisite, while the next offline task should be outcome-blind wallet transaction indexing and first-entry versus add/re-entry reconstruction. A true H3 should not be designed until that distinction and the source-timing limitation are resolved.

## Observability Taxonomy

| Mechanism | Classification | Reason |
|---|---|---|
| Pre-trade pool reserves, fee tuple, observed pool age, public buy flow | AVAILABLE_BEFORE_KEFI_ACTS in principle | These are public pool/flow variables, provided the source had them durably before the action. |
| Historical fingerprint score at kEFi entry | AVAILABLE_BEFORE_KEFI_ACTS logically, but timing sufficiency unproven | Features are strict pre-entry by construction; retained historical timing cannot prove usable lead time. |
| kEFi signature, exact wallet size, own transaction source receipt | AVAILABLE_ONLY_AFTER_KEFI_TX_IS_OBSERVED_BUT_BEFORE_OUR_DELAYED_ENTRY | A follower learns this only after seeing the teacher transaction. |
| Actual post-entry price path, 120-second replay, hypothetical exits | AVAILABLE_ONLY_POST_ENTRY | Cannot justify a pre-entry decision. |
| Wallet inventory, prior observed entries, repeat/add state | Wallet-private/internal but partly reconstructable | Requires wallet history, not pool state alone. |
| Wallet holdings outside retained history, private signals, off-chain execution intent | UNAVAILABLE / NOT RECONSTRUCTABLE | Not present in retained sources. |
| Wallet-linked exits / partial exits | UNAVAILABLE / NOT RECONSTRUCTABLE in the current entry corpus | The cited kEFi tape contains only BUY records. |

## Historical Input and Integrity

- Historical tape: enriched V1, 1,656 BUY rows.
- Period: 2026-10-05 03:00:35 UTC through 12:59:57 UTC.
- Pools/mints: 150.
- Replay integrity: 1,650 entries with retained 120-second replay; six without replay remain fail-closed.
- Integrity findings: zero duplicate signatures, duplicate replay keys, ordering violations, malformed entries, or out-of-window rows in the indexed V1 report.
- The 120-second exit-analysis artifact models hypothetical executable PumpSwap transitions. It is not a kEFi wallet-exit ledger.

## Lead-Time and Source-Order Reconstruction

### What can be measured

The entry tape includes source receipt timestamp, slot, signature, event index, and second-resolution event timestamp. Its coarse receipt-minus-event-time distribution over 1,656 entries is:

| Statistic | Seconds |
|---|---:|
| Minimum | -0.657 |
| P25 | 0.615 |
| Median | 0.911 |
| P75 | 1.264 |
| P90 | 1.792 |
| P99 | 41.356 |
| Maximum | 70.581 |

This is **not** a public-signal lead-time distribution. Event timestamp is second resolution, can create negative values, and does not identify when a relevant pool/flow state first became observable. The long tail can represent source delay, event-time coarseness, or source behavior; it is not evidence of a usable trading delay.

### What is missing

- No durable output sequence in the retained historical enriched rows.
- No full ordered public-state stream from which the first relevant state change can be measured.
- No independent timestamp of when kEFi itself first observed the opportunity.
- No follower receipt/execution latency trace for the historical rows.

### Lead-Time Verdict

**UNRESOLVED.** The current evidence cannot tell whether kEFi is predictably acting after a public signal, at the same time as it becomes public, or before an ordinary observer can see it. The broad fingerprint can be logically pre-entry while still being practically too late. A later study must preserve event-level durable order and first-public-observation timestamps before making any autonomous-prediction claim.

## Size Relative to Liquidity

| Measure | First entry, n=150 | Repeat entry, n=1,506 |
|---|---:|---:|
| Median transaction quote size | 0.814 SOL | 1.036 SOL |
| P25 to P75 transaction quote size | 0.684 to 0.904 SOL | 0.708 to 2.307 SOL |
| P90 transaction quote size | 1.359 SOL | 4.636 SOL |
| Median pre-trade quote reserve | 63.77 SOL | 86.27 SOL |
| Median transaction / quote reserve | 1.269% | 1.199% |
| P25 to P75 transaction / quote reserve | 1.227% to 1.307% | 1.080% to 1.325% |
| Median immediate quote-reserve delta / reserve | 1.253% | 1.188% |

Observed fact: first and repeat entries have similarly tight percentage-of-reserve behavior despite repeat entries having much wider absolute size and liquidity distributions.

Inference: size may encode a wallet rule tied to liquidity rather than a single fixed nominal amount. H1 did not include wallet size or size/liquidity ratio.

Prospective observability: teacher size is reactive-following information; a public proxy based on liquidity is available before action, but no evidence currently proves it predicts teacher size or economics. This is a reconstruction hypothesis, not a strategy rule.

## Wallet-State and Re-entry Reconstruction

### Episode structure

| Episode class | Count | Definition |
|---|---:|---|
| First entry | 150 | First observed kEFi BUY in a pool during the retained sample. |
| Immediate add | 399 | Subsequent BUY within 60 seconds of prior observed pool BUY. |
| Staged accumulation | 775 | Subsequent BUY after 60 seconds and within 15 minutes. |
| Later re-entry | 332 | Subsequent BUY after more than 15 minutes. |

Additional descriptive facts:
- Median entries per pool: 6.
- P75 entries per pool: 14.5.
- P90 entries per pool: 22.1.
- Maximum entries in one pool: 128.
- Median interval between repeat entries: 174.1 seconds.
- P25 and P75 repeat intervals: 55.8 and 715.5 seconds.
- P90 and P99: 2,639.5 and 13,962.4 seconds.

### Interpretation

H1 treated scoreable events as context opportunities. The historical tape shows that most kEFi BUY rows are not first entries. They may be adds, staged accumulation, or re-entry into a changed position. That makes any direct comparison between a public context match and an independent external trade fundamentally incomplete unless the future contract distinguishes first entries from additions and carries reconstructed wallet state.

The retained data can reconstruct **observed** prior entries in the ten-hour sample, cumulative observed purchased quote, and time since prior observed entry. It cannot prove zero pre-existing inventory at sample start, link all wallet transfers, or determine whether a later buy is economically an add versus a new position after an unobserved full exit.

## Wallet-Exit Reconstruction

Result: **not reconstructable from the cited historical entry corpus.**

Evidence:
- Both discovery V1 and enriched V1 contain 1,656 BUY rows and zero SELL rows.
- The 120-second replay includes market BUY/SELL events, but those are not wallet-linked kEFi exits.
- Exit-analysis V4 computes hypothetical best executable exits through retained PumpSwap events. It explicitly remains outcome blind and must not be treated as actual wallet behavior.

Consequences:
- Entry/exit linkage, partial exits, holding time, scale-out behavior, realized PnL, MFE/MAE, and remaining inventory cannot be reported as facts.
- FIFO/LIFO reconstruction would be a sensitivity model only, not wallet ground truth.
- H1 fixed exit may have been unlike kEFi, but current data cannot quantify how unlike.

## Relevant 2CQ Mechanism Evidence

2CQ provides independent support for separating mechanisms:
- V2 prospectively validated teacher mint selection, not economic execution.
- Timing research was weak relative to selection. A causal entry-timing report was labelled weak, while a separate development timing-hazard candidate passed only as development work.
- The 2CQ decision decomposition found size/conviction materially weaker and separate from selection.
- Alpha decomposition showed exact economic replay degraded sharply with delay in a small forensic slice and classified available evidence insufficient to reproduce actual 2CQ alpha.
- 2CQ also had 333 NEW_FROM_FLAT and 17 FLAT_TO_REENTRY development classes, confirming that entry state matters for teacher decisions.

Common mechanism supported by both wallets: public state may identify an opportunity context, but teacher timing, position state, and exits remain separate causal layers. kEFi supplies much stronger evidence for repeated-position structure; 2CQ supplies stronger evidence for latency sensitivity.

## H1 Assumption-Gap Table

| Actual or plausible behavior | Captured by H1? | Evidence strength | Prospectively observable? | Possible economic relevance |
|---|---|---|---|---|
| Public entry context | Yes, partially | High | Before action in principle | Necessary but not sufficient. |
| Exact lead time | No | Medium for importance, low for kEFi measurement | Needs event-level source order | May determine whether context is actionable. |
| Queue/source order | No | Medium | Yes with improved retained metadata | Distinguishes autonomous prediction from following. |
| Teacher transaction size | No | High that it exists | After teacher action; proxy possible before | Could encode conviction/impact regime. |
| Size/liquidity ratio | No | High descriptive | Liquidity before action; teacher size after | Could separate adds and first entries. |
| Prior inventory | No | High descriptive | Wallet-internal, partly reconstructable | Critical for independent-opportunity accounting. |
| First entry versus add/re-entry | No | High descriptive | Reconstructable from wallet index | Likely changes decision semantics. |
| Entry sequencing | No | Medium | Needs durable source order | May reveal whether score follows teacher impact. |
| Partial exits | No | Missing | Requires wallet-linked sells | Can dominate economics. |
| Holding duration | No | Missing | Requires wallet-linked exits | Can invalidate fixed H1 horizon. |
| Exit trigger | No | Missing | Requires wallet-linked exits | Can invalidate fixed H1 exit. |
| Regime dependence | No | Low to medium | Possible with frozen pre-entry regime data | Potential modifier, not established. |

## Ranked Mechanisms for Later H3 Discovery

| Mechanism | Evidence | Data quality | Prospective observability | Fresh study required? | Priority |
|---|---|---|---|---|---|
| First-entry versus add/re-entry with observed wallet state | Strong repeat-pool evidence | Medium; ten-hour left-censored sample | Partly | Yes for economic claim | HIGH |
| Source order and exact lead time | 2CQ latency evidence; kEFi measurement missing | Low currently | Yes if captured correctly | Yes | HIGH |
| Size/liquidity relationship | Strong descriptive concentration | Medium | Proxy before action, teacher size after | Yes | HIGH |
| Wallet-linked exits and position management | Central gap | Missing | Not with current entry-only corpus | Requires transaction index / dataset | HIGH |
| Regime segmentation | Plausible but not isolated | Low | Yes if predeclared | Yes | MEDIUM |
| Cross-pool portfolio constraints | Plausible | Low | Partly | Yes | MEDIUM |
| Private/off-chain information | No direct evidence | Missing | No | Not currently testable | LOW |

## Additional Data Collection Requirements

| High-priority mechanism | Retained data sufficient? | Required next data |
|---|---|---|
| First versus repeat/add | Partly | Wallet transaction index extending before/after sample; inventory provenance. |
| Exact lead time | No | Durable sequence, source first-seen timestamps, arrival epochs, kEFi action receipt marker. |
| Size/liquidity | Descriptive only | Same data plus predeclared source-order context for future testing. |
| Wallet exits | No | Direct kEFi wallet-linked SELL/transfer index and reliable position linkage. |
| Actionability controls | PRE_H3 is collecting this layer | Continue unchanged; do not use interim sealed outcomes. |

## Recommended Next Research Step

Do not design H3 yet. Complete an outcome-blind historical **wallet transaction index** for kEFi that:
1. separates first observed entry, immediate adds, staged adds, and later re-entries;
2. records prior observed exposure and cumulative observed buy amount;
3. records durable source order and first-public-observation timing when available;
4. finds direct wallet-linked sells without substituting market SELL events;
5. marks incomplete/left-censored inventory and missing exits fail-closed.

That reconstruction answers whether any later hypothesis should be autonomous pre-entry, reactive following, or rejected as non-copyable.

## Direct Answers

### WHAT DOES KEFI APPEAR TO KNOW OR REACT TO THAT ORDINARY MARKET PARTICIPANTS DO NOT?

The evidence supports that kEFi repeatedly acts in a specific mature PumpSwap liquidity, fee, age, and recent-buy-flow regime, and it manages repeated entries within pools. It does **not** yet show whether this reflects earlier access to public information, faster processing, wallet-private inventory state, or an unobservable private signal. Its actual exits are not recoverable from the current corpus.

### WHICH PARTS OF THAT BEHAVIOR CAN WE ACTUALLY COPY OR PREDICT?

Potentially copyable or predictable: public pre-entry reserve, fee, pool-age, and recent-flow context; after reconstruction, possibly first-entry versus repeat-entry state and a source-order-aware timing condition.

Only reactively followable: kEFi transaction size and the fact of the kEFi transaction itself.

Not currently copyable: true wallet inventory outside the retained window, actual exits/partials, and any private/off-chain signal.

**MORE_OUTCOME_BLIND_RECONSTRUCTION_NEEDED**

