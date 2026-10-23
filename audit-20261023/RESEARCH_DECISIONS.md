# EdgeSniffer research decision log

## RD-016 — 2026-10-23 — Capture raw context, not assumed static configuration

- After h1 verification, A3h2 added opt-in context to the isolated receive-only
  Rust recorder. Pump-owned accounts plus exact fee/clock accounts; mint filters
  grow from all Pump-scope transaction token balances, not signals or a wallet.
  Failed transactions may identify mint context but never become executed flow.
  Raw bytes, owner, write version, signature, receipt/decode times, native slot
  lifecycle and requested subscription changes are retained. Off-path RPC
  bootstrap preserves request/response clocks; no HTTP in the Rust receive path.
- Separate fixed60s engineering canary completed, not a replacement strategy
  cohort:7808tx (3364success/4444failed),4487raw account writes,236 discovered
  mints with236 raw mint bootstraps.235 snapshots available within1s of discovery,
  median0.481s,max1.185s. Eleven snapshots each of candidate fee/global/clock;
  125RPC requests,0errors. Nine discovered mints also emitted native mint writes;
  absence of a mint write does not by itself prove unchanged state.
- Verification:76 Python tests locally/VPS,8 Rust tests and release build VPS;
  canary exited0,one connection,no recorded gaps/sequence errors. Four outcome-blind
  selected finalized blocks:79/79 expected signatures,metadata equal. Global
  completeness remains unknown. Context-quality replay is byte-identical, SHA
  cd138b2896b8ff73ccc4c5d9da9220296efd1f8a003ac2c79092317b0b6e8dc3.
  Binary SHA2709cf053c77bae19dad44ca605b292e34b766e370c5448a2bd0ca4ce3eeb419.
  [Context capture](research/edgesniffer/evidence/quote-context-capture-20261023-v1.json),
  [transaction audit](research/edgesniffer/evidence/quote-context-transactions-20261023-v1.json).
  That transaction audit's zero account-join counts mean its optional legacy
  account-file join was not supplied, NOT that the new raw account stream was empty.
- Limits: requested dynamic subscription changes are not server activation
  acknowledgments. Fee/global account candidates, raw layouts, mint extensions,
  epoch effects, fork/write continuity and integer quotes still need validation.
  No observed fee-account update proves neither an update nor static validity.
  Native capture and later RPC response clocks are kept separate. Existing
  collectors were not restarted or changed; the bounded canary has finished.
- Decision: h2 is verified as capture capability; A3h overall remains partial.
  Next h3 validates the raw-to-causal-quote adapter on this sealed canary before
  requesting additional capture or calculating net economics. No trading enabled.
  The [separate future-control proposal](research/edgesniffer/continuity-control-proposal-v2.json)
  explicitly retains matching buckets and requires verifiable non-signal/state
  eligibility; it is not applied retrospectively or activated as validation.

## RD-015 — 2026-10-23 — Control missingness and quote context are distinct blockers

- A3h1 replays the original78 seeds and hash-selected controls without rematching.
  Of42 unmatched seeds,22 have no feature-eligible nonburst mint;20 have eligible
  nonburst mints only outside the original liquidity bucket. Per-candidate first
  exclusion reasons, token identities and20s/session distributions are preserved
  in [control diagnostics](research/edgesniffer/evidence/quote-context-controls-20261023-v2.jsonl).
  Candidate exclusions are repeated seed/mint comparisons, not unique-token counts.
- All45 stale burst exits remain unresolved for continuity. Six intervals cross
  a transaction-session boundary; four next observed writes have equal reserves.
  Neither absence of observed changes nor equal endpoint values proves no writes
  in between. No stale state was silently promoted and no original result changed.
- Flow sensitivity:35 pairs,21 controls,max reuse6; equal-control-weight mean
  +0.8975 SOL flow. Removing the three largest positive contributions leaves
  +0.5938; leave-one-mint-out in either role ranges+0.5502 to+1.0769. However32/35
  pairs occupy one virtual-reserve bucket (34.36–68.72 virtual SOL), not actual
  spendable liquidity. The lead is not solely one mint/control, but remains
  market-period/regime limited and is not profit. No thresholds were selected.
- Retained context:157985 mint-state records,only8/456 boundary legs have a prior
  retained mint row; these are not certified raw/current extension state. One
  old fee snapshot lacks update continuity. Two sealed04/05 ledger archives have
  1520110 frames,23 raw RPC mint snapshots,none for the80 selected mints, and no
  account-update records. This is a scoped inventory, not an exhaustive claim
  about every archive/source. All234 size scenarios per arm (468 total intentions)
  remain unresolved; zero submitted orders, not468 failed trades.
- Verification:73 tests local/VPS, including full pipeline/prefix/future-clock
  containment and immutable replay; two full-input reports hash identically:
  139bc62a2e8a4d8f781b8f55676ea98fdfe9cc63bfc9874d4cd83efe499a11a3.
  [Context report](research/edgesniffer/evidence/quote-context-20261023-v2.json),
  [archive inventory](research/edgesniffer/evidence/quote-archive-inventory-20261023-v1.json).
- Decision: h1 complete, A3h overall partial. Build h2 bounded raw quote-context
  capture, preserving existing collectors. No current RPC can repair earlier
  knowledge. Only a separately declared future comparison may replace the250ms
  recent-trade control requirement with continuity-certified state eligibility.
  The original one-second freshness rule stays frozen.
- Exact economic experiment remains burst receipt+1s versus unchanged persistence
  receipt+3s; hold15s plus1s exit delivery; original same-clock controls; sizes
  0.1/0.5/1 SOL. Require verified fee/mint/state, integer quote fixtures, own-state
  intervention and complete failed/unresolved accounting before computing net
  returns. New raw capture unlocks those checks, not immediate profitability claims.

## RD-014 — 2026-10-23 — Canonical identity recovered usable state without new capture

- A3g reused the identical sealed account snapshot and original78 signals/36
  available controls. Canonical PDA derivation used installed web3.js1.98.4;
  no outcome-dependent matching, no remote lookup in the decision path.
- Result:28424 noncanonical account rows excluded;15503/15503 unique selected
  successful one-event transactions match canonical account state, with all four
  reserves equal. This is scoped event/account parity, NOT global completeness,
  finality of every account update, or live-execution parity.
- Native-observed state exists at all requested boundaries for78 targets/36
  controls. It does not cure the42 seeds without original eligible controls.
  45/78 burst exit observations are older than1s (maximum~17s); none of the
  selected boundary rows flags curve complete. Do not silently relax the frozen
  mark-age rule, forward-fill prices as fills, or replace the primary results.
- Verification:65 tests locally/VPS, including actual SDK derivation and an
  adversarial integration fixture where a wrong account has matching reserves.
  Canonical replay report hash9f9ee944c4a80a90fa5cc9e907df98901c8f61eb79748156133c44b3529a5fab.
  Final coverage-report additions leave boundary/comparison artifacts identical:
  04d8c750331c0698b9971c6b2e7977da9e985f713b8833fd8729f39030885e23 and
  3b7bf44163b046a3cb51402267b3c9df623a9cd09d97283fcceddc79b38aec5f.
  [Canonical audit](research/edgesniffer/evidence/account-boundary-canonical-20261023-v2.json).
- Decision: the immediate evidence problem was loose account identity, not loss
  of all state data. The offline guard is fixed; running collectors remain
  unchanged and may still emit non-curve rows that consumers must exclude.
  The normalized account file does not retain raw account data/owner/discriminator
  or complete fee/mint-extension context, and lacks its own gap/session ledger.
- Exact next task A3h: close native quote-context coverage using retained raw
  evidence first; only then capture the missing minimal fee/mint/state context.
  Keep all original opportunities and frozen rules. Cost-input provenance,
  state age/continuity and quote fixtures must precede net economics claims.
  The positive relative-flow finding remains a research lead, not profit.

## RD-013 — 2026-10-23 — Account identity must precede state interpretation

- A3f checked a sealed256MiB account tail containing321911 rows, including the
  fresh capture period. Broad signature/slot/account-key association found43927
  rows:15503 matched all four trade-event reserves;28424 did not. It produced
  ambiguous curve mappings for all78 seeds. This is not proof that every account
  decoder is wrong: unrelated account roles can share the same transaction.
- 154615 rows fail real<=virtual reserve inequalities; raw account bytes, owner,
  discriminator and fee context are not in this normalized file. Never interpret
  field names or signature matches as certified curve state.
- Verification:63 tests local/VPS; a frozen-snapshot replay reproduced identical
  comparison hash3cd9491d9919bbb11921a42911f749fd2f3892cd7268879aeac712c5f441b70b
  and boundary hashd1fb6c0f958fefa64388aaa702af7a08e650ba9c540e8965e17b574a66b1d194.
  [Initial audit](research/edgesniffer/evidence/account-boundary-audit-20261023-v1.json).
- Decision: A3g restricts the offline join to the deterministic canonical curve
  PDA, not whichever account happens to match reserves. Official Pump IDL declares
  seeds `bonding-curve` and mint; reuse the installed Solana SDK, no HTTP hot path,
  no collector/service changes. Reuse the identical sealed input and fixed seeds.
  No executable quote or PnL claim follows from an identity repair alone.

## RD-012 — 2026-10-23 — Burst flow survives the delay; profitability remains unresolved

- A3e secondary endpoint used unchanged78 first-mint seeds and original controls,
  successful native Pump SOL events only, fixed2s windows after delayed entry.
  Retrospective explanatory test, not prospective validation or policy optimization.
- Burst:35 complete matched flow intervals, mean target-minus-control net executed
  SOL flow+0.9365, median+0.4945, positive23/35. Descriptive20s-block bootstrap
  interval[+0.4049,+1.5706] SOL flow;21 blocks, reused controls, one short market
  period. Mean event-count difference-0.943: more events alone is not the clue.
  These amounts describe other traders' buy-minus-sell activity, NOT our PnL.
- Persistence:11 admitted matched intervals, mean excess flow-0.4809 SOL,
  descriptive interval[-1.7898,+1.0653]. Neither this nor its negative observed
  price returns justifies promotion. Do not adjust its frozen thresholds to rescue it.
- Coverage:75/78 burst target intervals observed,1 ambiguous,2 censored;36
  controls observed,42 seeds had no eligible control. Of39 stale primary exits,
  30 had intervening events but no fresh endpoint mark;9 had no new decoded Pump
  SOL event in the holding window. This does not prove no activity elsewhere,
  unchanged executable prices, or universal capture completeness.
- Interpretation/decision: relative signed-flow continuation is a credible narrow
  research clue worth testing further; positive price means alone are not.
  Do not declare a profitable independent strategy. Preserve both negative and
  positive results, all missing observations, fixed rules and distinct cohorts.
- Verification:59 Python tests locally/VPS; two full-input replays have identical
  report hash06a9eeba9f5bc3bf6d721ff65ab4c17b5c5343d9827917301ba5887d91b04c59.
  [Secondary report](research/edgesniffer/evidence/event-continuation-fresh-20261023-v1.json).
- Exact next: A3f native-account boundary-mark coverage audit on these same seeds
  and controls, starting with retained evidence rather than broader capture.
  Establish what state/fee information actually existed at unchanged decision,
  delayed entry and exit boundaries; preserve unsupported/ambiguous/missing cases.
  Completion requires causal join tests, immutable provenance and all-seed coverage
  accounting. It unlocks the fixed-exit quote/cost experiment without changing
  strategy rules or pretending event-flow prediction establishes net profit.

## RD-011 — 2026-10-23 — More transactions did not create complete price comparisons

- Fixed additional run completed:8x120s,157657 transactions;78 first-mint seeds,
  50 later same-mint seeds retained separately,38 seed-time blocks. One asset
  overlaps the original18-mint canary;77 new assets. Cohorts remain separate.
  There are151.88s of explicit intersession gaps, not continuous observation.
- Verification:54 tests local/VPS; every admitted session reproduced identical
  opportunity/attempt files under the final native-source guard.32 preselected
  finalized blocks contained1178 expected signatures, all captured with matching
  metadata. Sampled completeness only; no global completeness claim.
- Returns are observed reserve-price changes, not PnL: burst28 observed of78,
  mean+6.35%, median-0.98%;7 matched pairs, mean excess+5.73 percentage points,
  median-4.94pp. Descriptive block-bootstrap excess interval[-31.29,+56.97]pp.
  Persistence23 confirmed/18 rejected/37 unknown:18 observed admitted returns,
  mean-5.23%, median-6.94%;only4 admitted matched pairs. No winner or net edge.
- Coverage matters:42/78 original seeds had no eligible same-clock control;
  burst39 stale exits and11 right-censored exits. Persistence uncertainty is
  often stale confirmation state, not evidence of a rejected trading condition.
  The100-mint engineering floor remains unmet; meeting it would not solve this bias.
- Decision: do not tune thresholds/exits or keep collecting blindly. Complete
  A3e on these same seeds/controls: fixed2s future executed-event/flow intervals
  after each arm's +1s-delayed entry, distinguish no new decoded Pump event from
  ambiguous/missing evidence. This secondary endpoint is declared now, before
  computing it, but remains retrospective exploratory research. It cannot infer
  fills, prices during quiet intervals, other-venue activity or after-cost profit.
- Evidence: [verified fresh study](research/edgesniffer/evidence/burst-persistence-fresh-20261023-v1.json).
  Standalone bounded capture has finished. Existing collectors were not changed;
  no trading or permanent service was enabled.

## RD-010 — 2026-10-23 — Failure counts mostly measure concentrated attempts

- Cheap T07a diagnostic completed on all45825 native records:38775 failed,
  no repeated signatures. 314 payers; top10 contribute25079 (64.7%) failures.
  25317 (65.3%) failures belong to466 repeated payer/instruction-payload groups,
  largest3359 distinct signatures. This is retry-like concentration, not proof
  of shared ownership or identical economic intent.
- Decodable intent:9675 buys,15933 sells,13167 unknown. Routes:25667 direct,
  185 CPI,12923 no observed Pump instruction. Provider account-inclusion scope
  includes transactions that mention Pump but never visibly invoke it. This
  population is not a strategy's submitted-order failure rate.
- Raw error fingerprints retained;717 failed signatures have later finalized
  human-readable error labels (including InstructionError/Custom codes). Do not
  invent semantic meanings for custom codes without the responsible program.
  Only88 failures carry Pump TradeEvent context;16 event-identified mints.
  Most have a native-known mint account mention, which is NOT proven trade intent.
- Temporal association: same-second failure/success-event count correlation0.294;
  next-second0.120 over120 bins. Descriptive, not controlled predictive evidence;
  no volatility/liquidity or return claim. Failed events contribute no executed flow.
- Decision: retain T07b, but require validated instruction-to-mint attribution,
  payer/retry-normalized features and same-period controls before predictive tests.
  Raw failed-count pressure is not currently a strong reason to displace T02/T03.
- Verification:48 tests locally (4 new),4 diagnostic tests VPS; full real-input
  run and hashed report: [diagnostic](research/edgesniffer/evidence/failure-diagnostic-20261023-v1.json).
- Next:8 fixed120s sessions for additional independent observation; initial
  engineering floor100 unique seed mints/30 periods is not a validation sample.
  Keep strategy unchanged and stop only at fixed duration or operational safety
  limits, never because returns look good/bad. Preserve existing collectors.

## RD-009 — 2026-10-23 — Run the independent test before expanding infrastructure

- Hypothesis: T02 burst continuation survives +1s; T03 persistence improves it.
  Fixed prior rule cards plus explicit warmup/control/mark conventions are in
  `research/edgesniffer/burst-persistence-contract-v1.json`. No teacher fields.
- Implementation: first-seed ledger, six size/arm intentions per seed, same-clock
  non-burst controls, confirmation rejects/unknowns retained, +1s entry and
  15s hold +1s exit delivery. Native post-event reserve prices are observation
  proxies, NOT execution prices; no PnL or size-scaled profits calculated.
- Result: 18 seeds/18 mints, 9 distinct controls, 5 unmatched seeds; 7 observed
  burst returns but just3 complete matched pairs. Their mean excess +2257.8bps
  is an extremely small, coverage-selected subset, not an edge estimate.
  Burst observed mean +137.1bps; persistence confirms3/rejects12/unknown3,
  only2 observed admitted returns (mean -29.9bps), zero admitted matched pairs.
  Abstention zeros do not establish performance. No strategy selected.
- Missingness: burst9 stale exits and2 right-censored exits. Native transaction
  metadata proves events occurred, not that last event reserves stay current.
  Do not forward-fill stale quotes to manufacture complete outcomes.
- Verification: 44 Python tests locally;11 new VPS tests; real sealed data and
  independent normalized replay yield identical report hash
  `ef27b99bc82a0ffdabca7734d932c601ed98dd06508f974a48e2b0f4decf1b9c`.
  [Report](research/edgesniffer/evidence/burst-persistence-canary-20261023-v1.json).
  Full rows preserved at VPS `burst-persistence-v1-WmlstZu7/canary-results2`
  under `/mnt/HC_Volume_106775198/edgesniffer-research`.
- Decision: complete the cheap failed-population diagnostic, then additional
  fixed-duration observation (engineering floor100 unique seeds/30 time blocks;
  initially15min bounded capture). This is a coverage/predictive diagnostic,
  not prospective after-cost validation. Do not optimize thresholds/exits.
- Failure-activity research registered as T07a/T07b: native error bytes and
  instruction intent may be information; failures are never executed flow.
  Start with population explanation before funding a predictive feature study.

## RD-008 — 2026-10-23 — Recover history, then capture only the Pump research universe

- Question: can old sparse receipts be enriched, and what is the smallest new
  capture that supports independent burst/persistence tests with valid controls?
- Historical experiment: 10364 rows / 9988 signatures; 32 lowest-hash signatures
  recovered via finalized getTransaction (30 success, 2 failed). One retained
  archive hour matched 213 signatures; 5 protobufs contain metadata, 208 do not.
  [Recovery report](research/edgesniffer/evidence/receipt-recovery-20261023-v1.json),
  [retained-frame check](research/edgesniffer/evidence/retained-metadata-20261023-v1.json).
- Interpretation: historical evidence is useful, not lost; the pilot does NOT
  establish full recovery or native-time availability. Enrichment remains later
  knowledge. Teacher-conditioned history does not become independent coverage.
- Decision: one-program Pump scope, including failed/unknown executions and CPI,
  with no wallet/mint shortlist. All-venue capture is unnecessary; selecting only
  later winning/burst tokens would destroy the same-period control population.
- Implementation/experiment: separate receive-only Rust capture, raw protobuf and
  typed metadata, explicit clocks, offline decoder/account joins and sampled
  finalized-reference audit. First startup failed on TLS provider selection;
  repaired/tested, failure retained. Second 120s canary completed.
- Result: 45825 native tx records; 7050 success / 38775 failed; 5056 decoded SOL-flow
  tx with matching account writes, 5033 by +1s. All 923 expected Pump tx in sixteen
  outcome-blind selected finalized blocks captured; metadata checks matched.
  No recorded gap/sequence errors. [Quality report](research/edgesniffer/evidence/pump-capture-quality-20261023-v2.json).
- Verification: 33 Python tests locally/VPS, 5 Rust tests and release build, real
  bounded capture, frozen-input decoder replay with identical view/join hashes.
  Full raw evidence and executable preserved on VPS; code/lock/small reports and
  canonical continuity records tracked in repository root. No collector restarts.
- Limits: observed failure count is market activity, not our order failure rate.
  Reference coverage is sampled, not global 100% completeness. Account joins are
  not current-state continuity or quote parity. Unknown fee extensions stay opaque;
  transaction deltas are not relabeled as swap fills. No strategy PnL computed.
- Earliest valid experiment: independent burst vs two-second persistence diagnostic
  from native known-at-receipt executed SOL flow, matched to non-signal Pump SOL
  controls at the same clock. Measure +1s/+2s continuation, retained/rejected seeds
  and evidence coverage. This can cheaply weaken the entry hypothesis before full
  economics. Do not optimize the rule on this canary or call it validation.
- Next: A3c ledger/control integration and that diagnostic. Only then use validated
  fee/current-state/own-impact accounting for 0.1/0.5/1 SOL net economics; select
  and freeze a candidate before collecting its fresh prospective validation.
  The new bounded collector is currently finished; original collectors remain
  unchanged. No trading, permanent service, or new frozen strategy enabled.

## RD-007 — 2026-10-22 — Receipt schema presence was not trade-data coverage

- Hypothesis: retained processed receipts plus account writes could supply a
  narrow independent signed-flow experiment without new transaction capture.
- Experiment: AUD-L01A3a exact signature/slot/curve/availability join, integrated
  with the three-size denominator ledger. Sealed 64MiB account sample and complete
  10364-row normalized receipt prefix; read-only observer runtime attribution.
- Result: all normalized receipts are target-wallet receipt records; every mint,
  side, curve and instruction index is null. The fresh raw sample has 80680
  account states and 94 target receipts with no success/amount metadata. Adjacent
  source emits precisely sparse target receipts. Prior A2 key-presence findings
  did not establish usable values. See [join report](research/edgesniffer/evidence/processed-join-a3a-20261022-v2.json)
  and [runtime provenance](research/edgesniffer/evidence/observer-provenance-a3a-20261022-v1.json).
- Verification: 20 adversarial/integration tests locally and on VPS; immutable
  real-input replay has identical join/ledger hashes. The original observer's
  running and on-disk binary hashes match; source-to-binary build equivalence
  and subscription completeness remain unproven. No running service changed.
- Interpretation: zero identified buy seeds means missing trade identification,
  not wallet inactivity or evidence against a strategy. Account clocks/reserves
  cannot establish executed signed flow. Matching a post-state would not establish
  current state at entry, fees, execution success or profitable economics either.
- Decision: reject the sufficiency of THESE retained receipts for T02; retain
  A3a as a verified diagnostic, not a completed economic adapter. Advance A3b:
  minimal market-wide processed transaction/meta retention, independently of
  teacher matching, reusing recorder code and existing account evidence.
- Scope/rationale: no universal decoder rewrite and no waiting for more sparse
  receipts. Additional bounded inspections found raw teacher instructions but no
  execution metadata, and no execution object in the sampled normalized market
  feed. This is not an exhaustive claim about every archived source. Price-only
  descriptive studies remain possible; they cannot answer the signed-flow/net
  economics experiment. Preserve this negative infrastructure result and all data.
- Exact next action: build/test separate receive-only full-meta evidence sink
  with explicit subscription identity, receipt/decode clocks, success/failure and
  resolved instruction identities; then verify same-source account joins before
  enabling T02 features. Do not enable trading or alter frozen tests.

## RD-006 — 2026-10-22 — Reuse account capture; inspect discarded metadata

- Evidence: [bounded source inventory](research/edgesniffer/evidence/source-inventory-a2-20261022-v1.json)
  and local receipt-normalizer source inspection, AUD-L01A2.
- Result: retained account samples already expose real reserves, phase, write
  identity and native clocks. Derived transaction receipts omit amounts/status;
  a single fee snapshot does not prove ongoing fee validity. Field presence is
  not economic validation and sample tails are not coverage estimates.
- Decision: no blanket new native capture or universal replay rewrite. Reuse
  account evidence; trace original metadata and subscription, then build a
  constrained processed-state join. New capture is necessary only for fields
  demonstrably not retained. Keep economic attempts unresolved in the meantime.
- Next action: A3 original-source inspection and validated availability join.

## RD-005 — 2026-10-22 — Preserve the denominator before quoting

- Experiment: independent first-observed-buy census on retained native replay,
  with one opportunity and 0.1/0.5/1 SOL attempt scenarios before data filtering.
- Result: 1065 seeds, 3195 attempts from 9571 rows; all blocked on conditional
  replay/unproven execution and absent availability/coverage references. 254
  seeds have nonmonotonic receipts. See [report](research/edgesniffer/evidence/ledger-a1-20261022-v1.json).
- Decision: A1 denominator slice verified (six adversarial tests plus read-only
  VPS CLI). No PnL, trade success or full attempt lifecycle claim. Source fields
  cannot authorize admission until a supported adapter has validated provenance.
- Next: inventory retained processed receipts/account/fee captures (A2) before
  requesting new collection or writing another approximate replay engine.

## RD-004 — 2026-10-22 — Version-controlled continuity

- Decision: relocate the canonical register and decision log to the Git root;
  workspace copies become forwarding pointers. Preserve historical content and
  evidence locations. This addresses dependence on ignored temporary files.
- Verification: inspect Git tracking and commit only these two records; preserve
  unrelated working-tree changes. Evidence links into the old workspace remain
  local dependencies, not claims of a self-contained archive.
- Next action: bounded AUD-L01A admission ledger, followed by attempt economics.

This is the canonical chronological decision index. Preserve prior entries;
append corrections with the superseded decision ID. Detailed experiments and
outputs stay in their existing reports. Task status belongs in
[RESEARCH_TASKS.md](RESEARCH_TASKS.md); enduring instructions belong in the
repository root [AGENTS.md](AGENTS.md).

For each substantive research decision record: stable ID/date; hypothesis;
experiment and evidence links; result and limitations; interpretation;
decision (accept for testing, reject, defer, inconclusive, or supported within
stated scope); rationale; associated task and exact next action. Distinguish
proposed, implemented, collecting, analyzed and prospectively validated work.
Do not infer historical decisions that the evidence does not document.

## RD-001 — 2026-10-22 — Reassessed teacher timing and research priority

- Hypothesis: teacher-associated conditions may identify an independently
  observable opportunity that retains net edge after a one-second delay.
- Experiment/evidence: completed descriptive recalculation of preserved timing
  rows in [assessment](workspace/edgesniffer_new/research/audit-reassessment-v1/assessment.md) and
  [timing output](workspace/edgesniffer_new/research/audit-reassessment-v1/teacher-timing-premise-v1.json).
- Result: 76 episodes/67 mints; median selected-state receipt lead 36.274923ms;
  0/76 selected states and 25/76 first crossings lead by at least one second.
  Teacher-selected histories cannot establish market-wide false-alert rates or PnL.
- Interpretation: final-state imitation does not establish a one-second entry
  advantage. Earlier crossings and persistent continuation remain hypotheses.
- Decision: keep teacher scorer as benchmark; accept simple independent burst
  and persistence comparison for testing, not as supported profitable strategies.
- Rationale: these experiments directly test residual edge with fewer missing
  launch features and lower engineering cost. See the assessment for ranked
  specifications and deferred sell-shock/exit research.
- Tasks/next action: AUD-R01 completed; AUD-L01A is the next bounded build in
  the task register. This log does not authorize execution or deployment.

## RD-002 — 2026-10-22 — Reopened broad repair-completion claims

- Hypothesis: previous local replay protections prevented conditional reserves
  from becoming economic evidence downstream.
- Experiment/evidence: actual replay-to-consumer regression in
  [testReplayEvidenceContainmentV1.py](workspace/edgesniffer_new/scripts/testReplayEvidenceContainmentV1.py);
  detailed diagnosis in the assessment linked above.
- Result: review found downstream recertification and approximate SUCCESS replay;
  AUD-I01 corrected those paths. The affected 41-test local suite passed.
- Interpretation: bounded containment is verified locally; full causal state,
  fee, flow, historical artifact and VPS deployment validity remain unproven.
- Decision: reject the earlier broad completion interpretation; reopen full
  F01/F03 and quote-provenance acceptance as recorded in RESEARCH_TASKS.md.
- Rationale: unit counts cannot substitute for affected integration invariants.
- Next action: preserve old evidence, retain known limitations, and implement
  the narrow opportunity ledger before interpreting new economic surfaces.

## RD-003 — 2026-10-22 — Persistent research governance

- Hypothesis: explicit repository instructions and linked continuity records
  reduce research drift across sessions and model changes.
- Action/result: added enduring principles to the existing Git-root AGENTS.md,
  a workspace entry point, this log and task-register navigation. Documentation
  paths and contents are checked; this is not an empirical strategy experiment.
- Decision: adopt these as the standing workflow at the user's explicit request.
- Rationale: preserve reasoning as well as task status; avoid repeated work,
  premature completion and engineering that does not unlock useful experiments.
- Next action: resume from RESEARCH_TASKS.md only on the research task; no
  unrelated implementation was undertaken during this documentation request.
