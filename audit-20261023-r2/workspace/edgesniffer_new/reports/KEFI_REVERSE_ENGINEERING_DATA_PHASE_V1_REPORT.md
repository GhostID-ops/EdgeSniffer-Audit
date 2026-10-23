# KEFI_REVERSE_ENGINEERING_DATA_PHASE_V1_REPORT

Status: active outcome-blind reconstruction. This report does not inspect or
modify PRE_H3 qualification outcomes, actionability results, bootstrap output,
or PnL. It does not open H3.

## Scope And Storage Contract

The retained source evidence is expensive: closed hourly raw full-meta
partitions are multi-gigabyte compressed objects. This phase therefore adds
small, reproducible derived records instead of a second market-data lake.

| Data product | Retention | Scientific purpose | Storage behaviour |
|---|---|---|---|
| Direct wallet action ledger | Direct finalized transaction summaries only | Wallet state, exits, transfers, size | Bounded 2,500 signatures/run; compact JSONL only |
| Wallet-state index | Derived from direct ledger | First entry/add/re-entry/exit classification | Rebuildable compact JSONL |
| Independent candidate universe | Only with an explicit independent/outcome-blind manifest | Action versus abstention | Refuses selected-centred inputs |
| Existing full-meta evidence | Existing capture retention policy | Exact replay where already required | Not duplicated by this phase |

The operational outputs are isolated under:
`/mnt/edgesniffer-kefi-hot/kefi-reverse-engineering-data-v1/`.

## Current Data Coverage

Historical retained direct-action evidence used by the preceding reconstruction:

| Item | Current known value | Interpretation |
|---|---:|---|
| Direct attributed historical actions | 1,656 | All were BUYs in the retained ten-hour sample. |
| Distinct pools/mints | 150 | Strong repeated-pool behaviour. |
| Historical direct wallet sells | 0 | Historical exits cannot be inferred from generic market sells. |
| Replay-complete selected entries | 1,650 / 1,656 | Six stay excluded. |
| First observed entries | 150 | Left-censored, not necessarily true wallet-flat entries. |
| Later BUYs | 1,506 | Position state was previously unknown. |

The new direct wallet action index uses finalized wallet transactions, not
selected windows. It stores token-balance deltas, direct transaction identity,
slot, block time, program set, and SOL-balance context. A transaction with a
token-out delta is only a wallet exit when it is directly attributable to kEFi.
Generic market SELL rows are never used.

## Wallet-State Reconstruction

The new V1 state builder emits only conservative classes:

- `FIRST_OBSERVED_ENTRY`
- `CONFIRMED_POSITION_ADD` is intentionally **not** emitted until full direct
  inventory coverage permits it.
- `LATER_BUY_UNKNOWN_POSITION_STATE`
- `TRUE_REENTRY_AFTER_CONFIRMED_FLAT`, only after a direct wallet balance
  transition to zero.
- `PARTIAL_EXIT`
- `CONFIRMED_FLAT_EXIT`
- `UNKNOWN_EXIT_STATE` and `TRANSFER_UNCERTAINTY`

It cannot silently treat two buys in one mint as an add, nor a long gap as a
re-entry. The direct ledger is the prerequisite for resolving those questions.

## Market Episodes And Action Versus Abstention

The historical selected-centred 120-second windows do **not** form a valid
non-action universe. They are useful for reconstructing local pre-entry state
but cannot answer why kEFi abstained elsewhere.

The V1 candidate builder therefore has a hard input contract:

1. The event stream must be chronological and public-state based.
2. Its manifest must explicitly assert `independent_candidate_universe=true`.
3. Its manifest must explicitly assert `uses_future_outcomes=false`.
4. Candidate admission uses only the 20-second pre-action bucket state.

Absent that proof it exits fail-closed and writes no candidate universe. This
is deliberate: treating current promoted windows as broad controls would
recreate the selected-centred bias that blocked the prior reconstruction.

When such a stream becomes available, the compact records will describe:

- transaction cadence and buy/sell count;
- pre-event liquidity/reserve state;
- pool age and fee tuple;
- acceleration/deceleration within a 20-second bucket;
- source instance, continuity epoch, first public receipt, and durable order;
- separate later linkage to direct kEFi action, without using that linkage to
  admit the candidate.

This is the required basis for continuous-active, cooling, dormant,
post-lull-reactivation, and new-episode labels. No revival label will be
derived from a later price move or a later kEFi action.

## Source Order And Lead Time

The live full-meta schema already defines local ordering as
`received_at_unix_ns, writer_sequence`. The historical ten-hour action tape
lacked a retained full public state history and cannot establish usable lead
time. The new phase preserves direct transaction receipt/source metadata, but
does not yet claim a pre-kEFi lead-time result until an independent public
candidate stream includes durable writer sequence and first-public observation.

Interpretation remains strictly separated:

| Class | Present status |
|---|---|
| PRE_ACTION_PREDICTIVE | Not established yet; requires independent public stream plus source order. |
| REACTIVE_COPYABLE | kEFi action and exact size are visible only after the wallet acts. |
| WALLET_INTERNAL_RECONSTRUCTABLE | Now being built from direct wallet actions, exits and transfers. |
| UNOBSERVABLE_PRIVATE | Still unknown; no claim is made. |

## Liquidity-Relative Sizing

Prior descriptive evidence remains useful but is not a rule:

| Metric | First observed | Later BUY |
|---|---:|---:|
| Median quote size | 0.814 SOL | 1.036 SOL |
| Median pre-trade quote reserve | 63.77 SOL | 86.27 SOL |
| Median size / reserve | 1.269% | 1.199% |

The V1 direct ledger retains direct size and wallet-state class so that these
can later be described by confirmed entry/add/re-entry state without fitting a
profit rule.

## Implementation And Verification

| Component | Purpose | Verification |
|---|---|---|
| `tools/kefi_wallet_action_index_v1.py` | Bounded, resumable finalized direct-wallet ledger | Direct extraction regression passes. |
| `tools/kefi_reverse_engineering_data_phase_v1.py` | Conservative wallet state and fail-closed candidate universe | Deterministic rerun and no-post-entry-field regression pass. |
| `edgesniffer-kefi-wallet-action-index-v1.timer` | 30-minute, low-priority bounded action-index refresh | Separate mount-identity guarded service. |
| `edgesniffer-kefi-decision-reconstruction-v1.timer` | Rebuilds compact state only after indexer is inactive | Separate mount-identity guarded service. |

Both timers write only to the dedicated kEFi hot volume and do not alter the
live collector, watchdog, PRE_H3 runner, matcher, controls, threshold,
evaluator, boundary, cohort, or timer.

## Ranked Mechanism Families

1. **Wallet state / first entry versus true add or re-entry.** Strongly
   motivated by repeated-pool behaviour; direct index is now collecting the
   required proof.
2. **Source order and usable public lead time.** Essential to distinguish a
   pre-action signal from reactive following; still needs independent stream
   capture.
3. **Liquidity-relative sizing.** Strong descriptive concentration; actual
   kEFi size remains reactive, but public liquidity is pre-action observable.
4. **Market episode or revival after a lull.** Plausible but unmeasured until
   the independent candidate stream exists.
5. **Exit/position management.** Direct action index will expose only directly
   attributable exits and transfers; no generic market proxy is permitted.

## Direct Answers

### WHAT OBSERVABLE CONDITIONS APPEAR TO MAKE KEFI ACT?

The evidence so far supports repeated participation in specific pool/liquidity,
fee, age and local buy-flow contexts, with purchase sizes concentrated relative
to pre-trade liquidity. It does not yet establish which of those conditions
causes action rather than merely accompanies it.

### WHAT CONDITIONS ARE PRESENT WHEN HE DOES NOT ACT?

Not yet known. The retained historical controls are selected-centred, so they
cannot truthfully represent abstentions. This phase refuses to overstate them.

### WHAT APPEARS TO DISTINGUISH THE TWO?

The most plausible missing layers are direct wallet state, public source order,
market episode/revival state and liquidity-relative sizing. Those are now the
specific bounded data targets, not generic market research.

### WHICH OF THOSE DISTINCTIONS COULD WE REALISTICALLY COPY?

Public liquidity, fee, age and transaction-flow context may be copyable if
they exist before kEFi with enough source-order lead time. Direct wallet state
can become partly reconstructable. kEFi's own action, actual size, exits and
any private information are not pre-action-copyable today.

**MORE_TARGETED_OUTCOME_BLIND_DATA_NEEDED**
