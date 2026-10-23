# KEFI_DECISION_ENGINE_RECONSTRUCTION_V2

Scope: outcome-blind offline reconstruction from opened historical/development evidence. PRE_H3 was not modified, and its sealed actionability outcomes, evaluator output, bootstrap results, and PnL were not accessed.

## Executive Summary

The available evidence does not yet reconstruct the full kEFi decision engine. It does reconstruct a meaningful part of it:

- kEFi's retained historical behavior is stateful: 1,656 directly attributable BUY actions across 150 pools in a 10-hour window, of which only 150 are first observed entries in the retained sample.
- The remaining 1,506 BUYs must be treated as later buys with unknown position state. There are no direct wallet-linked sells in the retained action tape, so it is not scientifically valid to call them confirmed adds, re-entries, or fresh independent trades.
- kEFi sizes purchases in a concentrated fraction of pre-trade liquidity: median 1.269% for first observed entries and 1.199% for later buys. This was absent from H1.
- Historical timestamps cannot establish whether the public fingerprint existed with usable lead time before kEFi acted. The retained action stream has receipt time and slot but no complete durable order plus first-public-state history.
- Comparable non-action opportunities cannot be defined from the selected-centered historical replay controls because those controls are not an independent full-universe candidate stream.
- Wallet exit and position-management reconstruction is blocked: retained directly attributable data has 1,656 BUYs and zero SELLs.

The evidence is consistent with a decision engine that combines public pool/flow state with wallet-state-dependent repeated participation and liquidity-relative sizing. It is not yet possible to decide whether that behavior is autonomously predictable before kEFi acts, reactively followable after kEFi acts, or economically valuable.

## Data Coverage and Censoring

| Item | Result | Interpretation |
|---|---:|---|
| Retained direct wallet actions | 1,656 | All are BUY actions. |
| Distinct pools/mints | 150 | Strong repeated-pool behavior. |
| Historical time span | 2026-10-05 03:00:35 to 12:59:57 UTC | Ten-hour observed window only. |
| Direct wallet SELLs | 0 | Exits cannot be reconstructed. |
| Entries with 120-second market replay | 1,650 | Six incomplete entries stay fail-closed. |
| First observed pool entries | 150 | Left-censored: first observed is not confirmed flat. |
| Later BUYs | 1,506 | Position state remains unknown without direct exits. |
| Full independent candidate universe | Missing | Abstention analysis cannot be completed. |
| Durable source-order history | Missing for this historical tape | Lead-time conclusion cannot be completed. |

All first observations are left-censored because the wallet history before the retained sample is not a complete canonical inventory ledger. The sample also ends without an exit ledger, so it is right-censored for position outcomes.

## Wallet-State Timeline

A reproducible conservative index was generated from the historical direct wallet-action tape:

- Index tool: tools/kefi_decision_engine_reconstruction_v2.py
- Input: .analysis_tmp/kefi-decision-engine-v2/enriched-v1.jsonl
- Output summary: .analysis_tmp/kefi-decision-engine-v2/wallet-state-summary.json
- Classes: 150 FIRST_OBSERVED_ENTRY and 1,506 LATER_BUY_UNKNOWN_POSITION_STATE.
- Confirmed true re-entries after a wallet exit: 0, because no direct wallet exit is present.
- Confirmed active-position adds: 0, because prior observed BUY does not prove current non-flat inventory without a complete exit ledger.

### Wallet-action spacing

| Measure | Seconds |
|---|---:|
| Repeat BUY interval P25 | 55.8 |
| Median | 174.1 |
| P75 | 715.5 |
| P90 | 2,639.5 |
| P99 | 13,962.4 |
| Maximum | 30,950.2 |

Observed timing classes are descriptive only:
- 399 immediate subsequent buys within 60 seconds.
- 775 subsequent buys from 60 seconds to 15 minutes.
- 332 subsequent buys after more than 15 minutes.

These are candidate behavioral categories, not confirmed economic classes. A later BUY after 15 minutes cannot be called a true re-entry until direct wallet-linked selling or a verified flat state is indexed.

## Market-Episode Reconstruction and Revival Analysis

**Result: not reconstructable from the retained historical evidence.**

The historical replay contains selected-centered 120-second market windows. It does not supply a continuous independent public market stream over the entire life of every pool. Therefore it cannot establish pre-action continuous-active, post-lull reactivation, new market episode, or revival labels without using selected-centered coverage as a misleading proxy.

The current wallet-action interval labels must not be mistaken for market episodes. We can say that kEFi returned to the same pool after short or long intervals; we cannot yet say whether the public market was continuously active, dormant, or revived before those later buys.

## Action Versus Abstention Analysis

**Result: not reconstructable from the retained controls.**

Historical context controls contain 256,495 rows, but their own summary marks them selected-centered and not an independent full-universe sample. They describe market events around kEFi-selected entries; they cannot answer why kEFi abstained at otherwise comparable opportunities.

A valid abstention study needs a canonical, chronological, full public candidate stream defined without future outcomes and independent of kEFi action windows. This is not present in the retained historical inputs used here.

## Lead-Time and Source-Order Analysis

The direct action tape has source receipt timestamps, slot, signature, and second-resolution event timestamp. Coarse receipt minus event-time values are:

| Statistic | Seconds |
|---|---:|
| Minimum | -0.657 |
| P25 | 0.615 |
| Median | 0.911 |
| P75 | 1.264 |
| P90 | 1.792 |
| P99 | 41.356 |
| Maximum | 70.581 |

These values are not usable lead time. The event timestamp has one-second resolution and can therefore produce negative apparent lags. More importantly, it does not tell when the relevant public pool/flow condition first became available, and no retained durable output sequence establishes whether that state preceded kEFi.

### Observability verdict

| Question | Answer |
|---|---|
| Did the 13-feature context logically exclude kEFi own trade? | Yes, by frozen strict-pre-entry construction. |
| Did it exist with measurable practical lead time before kEFi? | Unresolved. |
| Can kEFi own transaction strengthen the state visible to a follower? | Plausible, but unmeasured in this tape. |
| Can we distinguish pre-action prediction from reactive following? | Not with this historical source-order record. |
| Can coarse chain/event time substitute for source order? | No. |

## Size and Liquidity Reconstruction

| Measure | First observed entry, n=150 | Later BUY, n=1,506 |
|---|---:|---:|
| Median wallet quote size | 0.814 SOL | 1.036 SOL |
| P25 to P75 quote size | 0.684 to 0.904 SOL | 0.708 to 2.307 SOL |
| P90 quote size | 1.359 SOL | 4.636 SOL |
| Median pre-trade quote reserve | 63.77 SOL | 86.27 SOL |
| Median size / reserve | 1.269% | 1.199% |
| P25 to P75 size / reserve | 1.227% to 1.307% | 1.080% to 1.325% |
| Median immediate quote reserve delta / reserve | 1.253% | 1.188% |

Observed fact: size as a fraction of liquidity is narrowly concentrated for both first observations and later buys, despite later buys having much broader absolute amounts and reserve levels.

Inference: kEFi may use liquidity-relative sizing, or pool mechanics may constrain the observed result. H1 omitted both teacher size and size/reserve ratio. No profitability threshold was searched or selected.

Observability:
- Actual kEFi size is reactive-following information, visible only after its transaction.
- Pre-trade liquidity is public and available before action.
- A pre-action size proxy is possible to study later, but this audit does not establish that it predicts kEFi size or economic value.

## Exit and Position-Management Reconstruction

**Result: critical direct-data gap.**

The direct attributed historical corpus contains no SELL action. Generic PumpSwap market SELL events are explicitly not wallet exits and were not used as such. The available exit-analysis artifacts simulate hypothetical best executable exits through 120-second market data; they are not evidence of kEFi holding duration, scale-out behavior, remaining inventory, exit trigger, realized PnL, MFE, or MAE.

Therefore:
- No entry/exit linkage can be asserted.
- No true flat transition can be asserted.
- No confirmed re-entry can be asserted.
- FIFO/LIFO would be a labelled sensitivity model, not ground truth.

## Broader Context

Supported:
- Pre-trade reserve scale, fee tuple, and recent buy-flow are available in the frozen public recognizer.
- The sample spans ten hours, so broad regime claims, SOL environment dependence, session effects, or day-level capital recycling cannot be established.

Unavailable or untested:
- Concurrent kEFi positions.
- Capital released from exits.
- Creator/deployer identity effects.
- Counterparty/wallet graph effects.
- Congestion and execution conditions.
- Reliable time-of-day dependence beyond the short observed interval.

## Relevant 2CQ Comparison

2CQ provides useful independent mechanism evidence but not a transferable kEFi strategy:
- 2CQ V2 selection was prospectively validated, while timing evidence was weaker and separate.
- 2CQ decision decomposition found conviction/size substantially weaker than selection.
- 2CQ economics forensic work showed degradation with delay and could not reconstruct actual wallet alpha from retained data.
- 2CQ had explicit NEW_FROM_FLAT and FLAT_TO_REENTRY classes, reinforcing that stateful wallet context must be separated from isolated actions.

The shared mechanism suggested by both wallets is that public state can identify a teacher-relevant context, while timing, sizing, inventory, and exits remain distinct layers. kEFi has the stronger repeated-entry signal; 2CQ has the stronger latency warning.

## What H1 Actually Copied

| Decision component | Actual kEFi evidence | Captured by H1? | Prospectively observable? | Economic relevance |
|---|---|---|---|---|
| Public entry context | Strong descriptive evidence | Partly | Potentially before action | Necessary but not sufficient. |
| First entry versus later BUY | Strongly different row counts | No | Wallet-internal, reconstructable with full index | High. |
| Market episode or revival | Missing continuous public history | No | Potentially before action | Unknown. |
| Wallet inventory | Missing exit ledger | No | Internal and partly reconstructable | High. |
| Prior observed exposure | Present in narrow sample | No | Internal reconstruction | High. |
| Exact source order / lead time | Missing durable historical order | No | Potentially before action | High. |
| Teacher size | Present after action | No | Reactive; proxy possible | High. |
| Size/liquidity ratio | Strong descriptive concentration | No | Liquidity before action; actual size after | Medium to high. |
| Abstention decision | No independent universe | No | Potentially before action | High. |
| Position management and partial exits | No direct exits | No | Not currently | High. |
| Holding duration and exit trigger | No direct exits | No | Not currently | High. |
| Broader market regime | Short sample / missing context | No | Potentially before action | Unknown. |

## Copyable, Reactive, and Unobservable Components

### Potentially copyable or predictively testable

- Pool reserve scale, fee tuple, pool age, and recent public buy-flow state.
- A future first-entry versus later-buy state derived from a complete wallet action index.
- Source-order-aware lead-time condition, if future data preserves it.
- Pre-trade liquidity-relative constraints, but not actual kEFi sizing.

### Reactive-following only

- Confirmation that kEFi acted.
- Exact kEFi transaction size.
- Any state change caused by its own BUY.

### Currently unobservable

- Complete inventory prior to the sample.
- Actual exits, partial exits, holding duration, and true re-entry after flat.
- Any private/off-chain information or decision trigger.

## Ranked Mechanism Families for Later H3

| Family | Evidence strength | Data quality now | Prospective observability | Fresh experiment required | Priority |
|---|---|---|---|---|---|
| First-entry versus later-buy wallet state | High | Medium | Partly | Yes | HIGH |
| Source order and true lead time | High for importance | Low | Yes if captured | Yes | HIGH |
| Size/liquidity relation | High descriptive | Medium | Partial | Yes | HIGH |
| Direct exits / position management | High importance | Missing | No with current corpus | Additional wallet index required | HIGH |
| Market episode/revival | Plausible | Missing continuous stream | Yes if captured | Yes | MEDIUM |
| Broad market regime | Low to medium | Low | Yes if predeclared | Yes | MEDIUM |
| Private signal | No direct evidence | Missing | No | Not currently testable | LOW |

## Reconstruction Stopping Rule

1. Can first entries, active adds, revivals, and confirmed re-entries be distinguished? First observed versus later BUY is distinguishable. Active adds, revivals, and confirmed re-entries are not.
2. Can directly wallet-linked exits be reconstructed? No.
3. Can we tell whether public state exists before kEFi? No.
4. Can comparable non-action opportunities be defined? No.
5. Is a mechanistically plausible prospectively observable hypothesis family identifiable? Yes: a source-order-aware first-observed-entry versus later-buy wallet-state family, but it cannot be tested until the required data is collected.

The reconstruction therefore stops at explicit data boundaries rather than inventing proxies.

## Recommended Next Research Step

Additional outcome-blind data is required before bounded H3 discovery:
1. Build a direct kEFi wallet action index that includes wallet-linked BUY, SELL, and transfer actions before and after the observed window.
2. Preserve durable source sequence and first-public-observation time for all candidate market events.
3. Capture a full independent chronological public opportunity universe, not selected-centered windows.
4. Continue PRE_H3 unchanged; its current purpose is still useful but distinct.

## Direct Answers

### WHAT DOES KEFI APPEAR TO KNOW OR REACT TO THAT ORDINARY MARKET PARTICIPANTS DO NOT?

kEFi appears to repeatedly participate in a particular pool-liquidity, fee, age, and recent-buy-flow regime while returning to the same pools in structured bursts. The current evidence does not establish whether this reflects superior early public-state recognition, faster source processing, internal inventory state, or private information. The wallet's exit behavior is absent.

### WHICH PARTS OF THAT BEHAVIOR CAN WE ACTUALLY COPY OR PREDICT?

Public entry-context variables are potentially copyable, but their usable lead time is unproven. Actual transaction size and teacher action are only reactively observable. Wallet state may become partly reconstructable with a complete action index. Exits and private signals are not currently copyable.

**MORE_OUTCOME_BLIND_DATA_NEEDED**

