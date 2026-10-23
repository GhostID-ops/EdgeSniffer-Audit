# Proposed Pre-H3 Actionability Qualification V1

Status: `APPROVED_CANONICAL_INPUT_REMEDIATED_NO_BOUNDARY`
Boundary/cohort/result created: `NO`

## Scope and Hypothesis

This is explicitly **not H3** and is not an economic, profitability, fill, or
live-trading test.

**Primary hypothesis:** among all pre-entry-eligible events selected by the
already frozen base kEfi fingerprint, the probability of a *complete
mechanically actionable trajectory* is greater than for deterministic,
contemporaneous, context-matched fingerprint non-matches.

It addresses H1's failure by testing the missing operational premise directly:
whether a selection match remains observable and mechanically usable after a
conservative delay.  It does not claim that an actionable match is profitable.

## Frozen Inputs

- Base fingerprint artifact SHA-256:
  `db08899d92f4d69d8c0234cfff9cbd05bd27dfb1365aed88d08b85f88cfc1b4a`.
- Its exact feature-name/order schema is frozen and checked before assignment;
  the verification receipt is
  `KEFI_PRE_H3_FROZEN_INPUT_VERIFICATION.json`.
- No refit, threshold adjustment, feature selection, overlay, or model-family
  comparison.
- Fresh evidence begins only after this proposal is independently approved and
  a new boundary is written.
- Historical, H1-opened, H2-quarantined, operational, and pre-boundary data
  are rejected by the mandatory epoch registry.

## Cohort and Denominator

The primary denominator is fixed **before** delayed-entry or exit availability
is known:

1. After a 120-second boundary warm-up, admit every unique event that is
   pre-entry eligible under the frozen base fingerprint and has the required
   source order, exact PumpSwap instruction/event transition, canonical SOL
   quote layout, non-SOL base mint, and reconstructable pre-event state.
2. One target per `(source_instance_id, continuity_epoch, pool,
   20-second availability bucket)` is retained by deterministic first source
   order.  This de-duplicates exposure; it does **not** condition on later
   tradeability.
3. Every admitted target remains in the denominator.  A missing supported
   state at delay, a non-tradeable later state, an incomplete horizon, or a
   hard wall is a predefined outcome, never a row deletion or re-sampling
   opportunity.

An admitted target's primary binary outcome is `COMPLETE_ACTIONABLE_TRAJECTORY`:

- `1` only when both are present within the same source instance and epoch:
  - first supported exact state for the same `(pool,mint)` at or after the
    fixed 1-second entry delay; and
  - first supported exact state at or after 120 seconds following that entry.
- `0` otherwise, classified as exactly one of:
  `NO_POST_DELAY_ENTRY_STATE`, `POST_DELAY_NON_TRADEABLE_<reason>`,
  `NO_120S_MARK`, `POST_ENTRY_NON_TRADEABLE_<reason>`, or
  `HARD_WALL_INCOMPLETE_HORIZON`.

No paper PnL, inferred fill, or later outcome is read to define this endpoint.

## Frozen Contemporary Controls

For each target, choose up to four fingerprint **non-matches** using only data
available strictly before their own candidate event.  Candidates are eligible
only if they match every frozen stratum below:

- same `source_instance_id` and `continuity_epoch`;
- same 20-second `received_at_unix_ns` availability bucket;
- canonical SOL quote and non-SOL base layout;
- exact PumpSwap transition validation and same `amount_mode`;
- same fee-layout tuple: `lp_fee_bps`, `protocol_fee_bps`,
  `coin_creator_bps`, `cashback_bps`, and `buyback_bps`;
- same fixed pool-age bucket: `[0,30)`, `[30,120)`, `[120,300)`,
  `[300,900)`, `[900,+inf)` seconds, where age is the observed elapsed time
  since the first source-sequenced event for that pool in the same source
  instance/epoch.  It is deliberately an *observed age*, not an invented
  chain-history age;
- same fixed pre-event quote-liquidity log10 bucket of width `0.5`, computed
  from reconstructed `pre_state.pool_quote_raw` only;
- different mint from the target.

Observed pool age is derived from the **complete ordered scientific row
stream** for the source instance/epoch, before warm-up, target, or non-match
filtering. Earlier ineligible or warm-up rows therefore remain provenance for
age matching even though they cannot become a target/control themselves.

The control matcher reads no post-entry trajectory, later mark, PnL, return,
or result field.  Its complete frozen input is the source order, availability
time, pre-event reconstructed state, immutable PumpSwap layout/fee fields, and
mint/pool identifiers listed above.

Within that stratum, control identities are selected in ascending SHA-256 of
`frozen-control-seed || target-event-key || control-event-key`.  No control
may use post-entry state, a later mark, PnL, or a result field during selection.
Targets with zero available controls remain in the target denominator and are
reported in the primary cohort; they are omitted only from paired comparison,
with the omission count and strata reported.

Dependence clusters are
`(source_instance_id, continuity_epoch, pool, 20-second availability bucket)`.

## Primary Endpoint and Decision Rule

Let `A_T_all` be the target complete-actionability proportion over **all
admitted targets**, including targets with zero matched controls.  It is always
reported as the absolute operational rate.

For the comparative estimand, retain only the fixed subset `P` of targets with
at least one frozen matched control.  Let `A_C_paired` be the equal-target-
weighted mean actionability of controls within each target's control set.
The sole comparative primary endpoint is
`Delta_A_paired = A_T_paired - A_C_paired`, calculated only over `P`.

Cluster bootstrap resampling retains each target with its entire frozen control
set.  It never lets unmatched targets dilute the paired comparison and never
lets the paired restriction erase them from `A_T_all`.

The reported intervals are two-sided 95% cluster-bootstrap intervals for both
`A_T_all` and `Delta_A_paired`, resampling the frozen dependence clusters and
retaining each target/control set together.  The bootstrap seed, replicate
count (`10,000`), and cluster identities are frozen in the boundary receipt.

The following operationally meaningful thresholds are frozen before fresh
outcomes: a lower 95% bound of at least `70%` for `A_T_all`, and a lower 95%
bound of at least `+10 percentage points` for `Delta_A_paired`.  These are
qualification floors, not fitted thresholds: a tool that cannot preserve a
complete delayed/120-second trajectory for at least 70% of all signals, or
cannot beat matched contemporaneous controls by a material margin, is not a
useful foundation for a later economic hypothesis.

The qualification result is:

- **PASS** only if all data-quality gates pass, at least 300 targets and 40
  distinct target pools/mints occur across at least 8 chronological blocks,
  no one pool supplies more than 10% of targets, at least 70% of targets have
  one or more matched controls, the lower bound of the 95% cluster interval
  for `A_T_all` is at least 70%, and the lower bound for
  `Delta_A_paired` is at least +10 percentage points.
- **REJECT** if all data-quality gates and minimum representation floors pass
  and either the upper bound for `A_T_all` is below 70% or the upper bound for
  `Delta_A_paired` is less than or equal to zero.
- **INCONCLUSIVE** for every other outcome: insufficient target/control
  representation, interval spanning zero, or any predeclared quality gate
  failing.  An inconclusive result does not authorize a rerun or tuning on the
  same fresh cohort.

Chronological-block estimates and the full outcome-reason distribution are
descriptive integrity diagnostics, not additional endpoints and not tuning
inputs.

## Frozen Evaluator

`scripts/kefi_pre_h3_actionability_evaluator_v1.py` is the only supported
decision evaluator.  It accepts a pre-built assignment file; it cannot create
a cohort, choose controls, or inspect PnL.  Each assignment contains one
target, its frozen cluster/pool/block identity, the all-target binary outcome,
and its zero-to-four already selected control outcomes.  It emits exactly
`PASS`, `REJECT`, or `INCONCLUSIVE` using the rules above.  The public
regression suite proves all-target versus paired-population accounting,
positive PASS, conclusive REJECT, and INCONCLUSIVE coverage behavior.
The machine-readable frozen candidate contract is
`KEFI_PRE_H3_ACTIONABILITY_EVALUATOR_CONTRACT_V1.json`; its source hashes bind
the reviewed matcher and evaluator implementation.  Neither artifact creates a
boundary or a cohort.

## Deterministic Assignment Builder

`scripts/kefi_pre_h3_assignment_builder_v1.py` is the only supported upstream
publisher for evaluator assignments. It requires a hash-matching frozen base
fingerprint, an already-written boundary receipt, the canonical intrinsic-event
JSONL, its hash-bound qualification-input manifest, and a verified append-only
epoch registry. It does not create any of those artifacts. Concatenating a
list of scientific receipts is not an accepted assignment-input path.

The canonical index publishes exactly one intrinsic record for each
`(source_frame_sha256, event_index)` identity in `canonical-events.jsonl`.
`window-exposures.jsonl` preserves sealed-window multiplicity as provenance
only. The manifest uses schema
`KEFI_INCREMENTAL_EVENT_INDEX_V2_QUALIFICATION_INPUT_V1`, hashes both files,
and declares the frozen policy: `canonical intrinsic events exactly once;
window multiplicity is provenance only`. Derived/outcome annotations never
participate in intrinsic identity.

Before delayed entry, non-tradeability, marks, or a horizon are inspected, it
scores only the frozen pre-entry feature state, admits every match after the
boundary warm-up, and retains deterministic first source order within each
`(source instance, epoch, pool, 20-second bucket)`. It constructs the entire
fingerprint non-match universe from the same pre-entry state, invokes the
frozen control matcher, and retains every target with zero controls in
`A_T_all`.

It derives entry/120-second/hard-wall outcomes only after target/control
identity is frozen; derives dependency clusters from the frozen source/order
fields; and assigns eight contiguous equal-event-count chronological blocks.
The builder publishes a manifest hash-binding the fingerprint, boundary
receipt, canonical-input manifest/events/exposure provenance, epoch registry,
matcher, builder, evaluator, control seed, and assignment JSONL. The builder
does not invoke the evaluator and records `outcomes_evaluated: false`.

## Evidence and Continuity Gates

Every row must have one and only one `FUTURE_UNOPENED`/`UNOPENED` epoch after
the frozen boundary, plus source instance, continuity epoch, durable output
sequence, event index, and source-frame SHA-256.  Exact source order is
`(source_instance_id, continuity_epoch, durable_output_sequence, event_index,
source_frame_sha256)`.

Any reconnect, continuity gap, late-earlier ordering violation, decoder
rejection, watermark regression, missing scheduled tick, or source hard wall
invalidates the active candidate cohort.  No interval is bridged.  All such
events are reported rather than silently omitted.

An active qualification requires exactly one source instance and continuity
epoch. A source-instance or continuity-epoch break is a cohort-level hard wall:
the builder fails closed and does not produce assignments from either side in a
combined cohort. A later clean run requires a separate approved boundary; no
cross-break aggregation is permitted.

A voided qualification epoch is permanently ineligible for every timestamp in
its recorded membership interval through its durable closure. Evidence after
that closure is a gap until a later separately approved, non-overlapping
`FUTURE_UNOPENED`/`UNOPENED` epoch begins at a fresh boundary. The old append-
only record is never deleted, reused, or bridged; it does not prevent the later
epoch from being eligible on its own timestamps.

## Explicit Non-Claims and Next Step

PASS would qualify only the operational/actionability premise.  It does not
rescue H1, establish PnL, validate a paper entry/exit fill, or authorize H3 or
live execution.

After this study, the remaining route to a genuine H3 is a bounded offline
discovery protocol that selects **one** novel hypothesis intended to explain
why H1's broad selection rule had poor economics, then an independently
reviewed fresh H3 economic paper/control specification.  That later proposal
must be new; it may not relabel this cohort as H3.
