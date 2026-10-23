# kEFi Initial Token Selection - Reverse-Engineering Audit V1

## Status

**FAIL CLOSED BEFORE A VALID HOLDOUT RESULT.** The frozen 60-snapshot master
cohort passed its capture-integrity checks, but cannot support the frozen
primary `NEW_TO_OBSERVED_WALLET` universe across the Cohort A/B epoch boundary.
The one-shot holdout opening marker was written before evaluation, as required.
The evaluator then rejected a chosen holdout mint that had already been chosen
in Cohort A but was re-labelled `FIRST_OBSERVED_SELECTION` after Cohort B's
observer restart. No result artifact was written, no partial metric was
published, and the cohort must not be retried or bridged.

This is a cohort-accounting limitation, not evidence loss and not a claim about
kEfi's profitability, exits, or PRE_H3.

## Master Integrity

| Check | Result |
|---|---|
| Master manifest | `aa287917553c2035f46378bc72f3de6dd5d76e7fa0f0677c3e96022e64a2e106` |
| Cohort A / B composition | 49 / 11, exactly 60 chronological snapshots |
| Snapshot and receipt verification | All 60 SHA-256 identities matched |
| Snapshot contract | All receipts confirm warm-up, strict pre-action state, teacher-event exclusion, authoritative Pool identity, eligible chosen pool, and no outcome/PnL access |
| Decoder / identity integrity | Cohort A and B validation records both report zero identity conflicts; no validation failure |
| Quarantined runs | Not referenced by the master manifest |
| Integrity receipt | `master-integrity-v1.json`, SHA-256 `be006de3a4ab9e0f02d3274a6fadaf37966968b3a52a503a27ed2b3eb90f757d` |

## Frozen Split

The chronological split was sealed before reading any selection values:

- Development: first 40 teacher decisions.
- Untouched selection holdout: final 20 teacher decisions.
- Split receipt: `initial-selection-split-v1.json`.
- Split SHA-256: `1c2ace37897ae076ab76d192a4641fdd0a25360c89abb48c52db4996e6e45526`.

The existing frozen strict-pre-entry baseline was verified unchanged:

- Artifact SHA-256: `db08899d92f4d69d8c0234cfff9cbd05bd27dfb1365aed88d08b85f88cfc1b4a`.
- Schema: 13 ordered pre-action features.

## Development-Only Finding

On the 40 development decisions only, the frozen baseline ranked the selected
token first in all 40 choice sets. Candidate set size ranged from 311 to 4,329,
with median 2,350. The corresponding mean random Top-1 baseline was about
0.054% per actual choice set.

This is a very strong *development* selection signal. It is **not** a valid
generalization claim because the intended holdout evaluation was invalidated
before completion.

The bounded clue audit examined eight strictly pre-action, observer-native
variables. Sell-side flow, recent-event recency, and short-versus-long buy-flow
acceleration showed development-only within-choice discrimination. Four
mechanistically plausible, non-duplicative additions were frozen for one
regularized grouped choice model:

1. `log_sell_quote_flow_60s`
2. `seconds_since_last_public_event`
3. `buy_count_acceleration_5s_over_60s`
4. `buy_quote_acceleration_5s_over_60s`

No interaction search was conducted. The expanded model and development freeze
were produced before the holdout was opened:

- Expanded model SHA-256: `f379813a947c90a79a2ff64a5d2dfb4b54dd263e589ac7a4e3104e01b73a91f2`
- Development-freeze SHA-256: `0cd032e4ab7128afd12175e24906e01bb2b8a23875ac9d4fd246b0d709d8f4e2`

## Holdout Disposition

The frozen evaluator plan was published before opening the holdout:

- Plan SHA-256: `067eb8d9139007615a59865b6be140ebc8e91adddb5982f33858b3b6559057c5`
- Opened marker SHA-256: `3c107727410aaa5c12268d4de37f3ad815d5df8eb5bb257b2c3056ebd25aed95`

It failed closed because a holdout target was already in the observed wallet
history from Cohort A. Treating it as new after Cohort B's restart would have
silently changed the primary universe; dropping it would have silently changed
the frozen denominator. Neither is permitted.

- Evaluation result artifact: **not created**.
- Interim metrics: **not emitted**.
- Fail-closed receipt SHA-256: `df1c471110cd331756ed3919a3a88bb59d88be5cef31ad3e1129080877419c29`.

## What This Means

The live choice-state observer has proven that it can capture large,
teacher-independent, strictly pre-action choice sets. The remaining missing
piece is a durable wallet-observation ledger shared across observer epochs.
That ledger must be established before a *new* independently collected cohort
can claim `NEW_TO_OBSERVED_WALLET`; it must never be inferred retroactively
from an already-opened holdout.

The next scientifically valid step is therefore operational, not analytical:
persist and hash-bind the observed-wallet mint history across epochs, verify it
with restart regressions, then collect a wholly fresh master cohort and repeat
the same preregistered 40/20 process. The current master may remain as sealed
evidence of capture quality, but not as a selection-performance cohort.

KEFI_SELECTION_SIGNAL_PRESENT_BUT_FILTERS_UNRESOLVED
