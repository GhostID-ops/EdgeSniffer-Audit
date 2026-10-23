# EdgeSniffer research task register

Updated: 2026-10-23

Persistent instructions: [Git-root AGENTS.md](AGENTS.md), with a local
[workspace entry point](workspace/edgesniffer_new/AGENTS.md). Research reasoning and decisions:
[RESEARCH_DECISIONS.md](RESEARCH_DECISIONS.md). Read these on continuation;
update this register and the decision log before handoff. Standing governance
was established on 2026-10-22; see RD-003. Current actions and results are below.

This is the canonical register for the independent audit integration. It
separates implementation defects from research hypotheses and records the
evidence needed before a task can be called verified. The supplied audit is
read-only evidence; it does not itself prove production incidence or PnL.

## Current stage

Continuity migration completed: this register and RESEARCH_DECISIONS.md now live
at the Git root. Workspace-local files forward here. Historical evidence paths
remain unchanged; references into the ignored workspace require that workspace.

The current program is in measurement repair. No strategy is validated and no
policy is promoted. Existing collectors, frozen artifacts, cohorts, and
execution restrictions remain preserved. The exact native scorer lane is
blocked by missing launch lineage for the current native replay, and the
broader audit found additional state, timing, decoder, denominator, and
execution-model defects that must be repaired before strategy selection.

Audit package: `C:\Users\Cheet\Downloads\EdgeSniffer_Audit_Evidence.zip`

Audit evidence ZIP SHA-256: `284EEDE0FC3864D4631592CACE076951065BFF71586F498BEEFF65DAC91D217A`.
The previously listed `F6FF8036...` identifies the audited SOURCE archive, not this evidence ZIP.

Initial portable reproduction output:
`C:\Users\Cheet\AppData\Local\Temp\EdgeSniffer-current-audit-20261022-203402\summary.json`

Result: governance 5/5, scorer 9/9, native 11/11, economics 13/13 result
groups matched; 38 groups are overlapping counterexamples, not 38 independent
defects.

After the AUD-F01 correction, the rerun is at:
`C:\Users\Cheet\AppData\Local\Temp\edgesniffer-audit-rerun-20261022203640\summary.json`

Governance, native, and economics probes still match. The scorer probe now
fails at its old `scorer_value` call because that probe intentionally encoded
the raw-logit defect; this is expected evidence that the old defect-reproduction
probe has not yet been translated into an external correct-behavior probe.

Only one implementation task may be `IN_PROGRESS` at a time.

Current narrow experiment: A3c independent burst/persistence price diagnostic,
T07a failure-population diagnostic, A3d bounded additional capture, and A3e
secondary activity diagnostic are complete and verified within their stated scope.
A3f/A3g account audit and offline canonical-identity guard are also complete.
A3h1 is VERIFIED: retained quote context, original-control exclusion reasons,
state-continuity evidence and exploratory flow concentration (RD-015).
A3h remains PARTIAL; A3h2 bounded raw-context capture is now VERIFIED within
its engineering-canary scope. A3h3 adapter/diagnostic is VERIFIED (RD-018):
92 tests local/VPS,16 independently finalized transaction reconstructions across
six supported variants, and byte-identical full replay. The causal economics gate
has NOT passed: original78 have zero complete entry/exit quote contexts and all468
intentions remain preserved/unresolved. New60s capture has zero temporal overlap.
A3h4 remains IN_PROGRESS / PARTIAL, not a completed economics gate. Its serial
acquisition/observation and nonblocking-request slices are verified (RD-020/021):
123 tests local/VPS, three fixed75s engineering canaries, exact scheduled-seed
replay and byte-identical audit reruns. Observer lag improved from median425ms
to35ms in separate bounded runs; response/publication clocks remain distinct.
Latest sample:9 signal-period opportunities/54 intentions,2 drain-only seeds,
6 control assignments/3 unmatched.12 abstentions/42 unresolved economics, no
complete matched context chains or net returns. Previous11/66 sample remains
separate and contains15 successful2CQ event labels; latest short sample has0.
Exact next A3h4c: independently reconstruct holder-reward/supply fee semantics
from retained raw transactions, and classify unmatched writable privileges at
instruction level. Current math scope excludes7/9 latest targets; more capture
alone is not the repair. Full own-state/cost/transition acceptance still precedes
the predetermined prospective cohort. Original78/468 unchanged. All three bounded
runs finished; existing collectors untouched; no trading or expansion enabled.
The bounded capture is finished, not a permanent new service. These tests do not
require completing every historical infrastructure task; executable net economics
still has separate blockers. See RD-009/RD-010 and tracked evidence reports.

## Status summary

User-directed publication task AUD-PUB02 is the sole active implementation task;
A3h4c research work is awaiting completion of this publication, not abandoned.
Scope: refresh the existing public audit repository with current app/research
source, retained experiment history, canonical records and small evidence.
Acceptance: explicit omissions/provenance, secret review, exported test run,
staged-byte verification and remote commit/visibility verification. No trading,
collector changes, source-history publication or credential upload.
Exact action: expand the sanitized snapshot and reviewer navigation, then verify
before pushing to the existing public repository without rewriting its history.

| AUD-PUB01 | public GitHub audit snapshot | User request | VERIFIED |2026-10-23 uploaded source/research snapshot to GhostID-ops/EdgeSniffer-Audit, existing default branch audit/edgesniffer-throughput-20260916, commit a08aeeb32e614bf40a3f211a55b26f9e59e20192. Start at audit-20261023/README.md.1351 source/evidence files, staged byte hashes verified;76 exported Python tests passed/0skips using existing SDK. Gitleaks8.30.1 scanned;5 reviewed false positives (public keys/column names), no known-env secret matches. No source Git history/env/keys/build outputs/raw VPS captures uploaded. Old destination audit preserved. This is a dated working-tree snapshot, not automatic synchronization or deployed-source equivalence. |

| AUD-L01A3h1 | retained quote-context and bias diagnostics | P0 | VERIFIED |73 tests local/VPS; identical full-input replay report SHA139bc62a2e8a4d8f781b8f55676ea98fdfe9cc63bfc9874d4cd83efe499a11a3. Original78 seeds,234 size scenarios per arm/468 intentions preserved.42 missing controls:22 no eligible nonburst features,20 only other liquidity buckets.45 stale exits:6 cross tx-session boundary,4 equal later writes,0 continuity-certified. Scanned both sealed04/05 archives:23 raw mint snapshots,0 selected mints; account-update records absent. No net economics. |
| AUD-L01A3h2 | bounded raw quote-context capture | P0 | VERIFIED (bounded canary) |76 Python tests local/VPS,8 Rust tests/release build VPS. Separate60s canary:7808tx,4487raw account writes,236/236 mint bootstraps (235within1s of discovery),11each fee/global/clock snapshots,0RPC errors,0 recorded gaps/sequence errors.79/79 expected tx in4 sampled finalized blocks with metadata parity. Identical context-quality replay hashcd138b2896b8ff73ccc4c5d9da9220296efd1f8a003ac2c79092317b0b6e8dc3. Original collectors untouched; bounded run finished. No claim of full write continuity or quote validity. |
| AUD-L01A3h3 | validate causal raw quote-context adapter | P0 | VERIFIED (bounded adapter/math; causal gate NOT PASSED) |92 tests local/VPS; report SHA c686e219dcb9d61ac9213e68384f15915df0de91c18c24e5f09c6a974dd6dd5e identical on full-input replay.164 conditional arithmetic matches;16 finalized same-block reconstructions/6 mints/6 legacy-v2 variants verify independent prior raw reserves, requested args, post reserves, protocol/creator fees and actual cash/token deltas.0 complete original causal entries/exits;468 intentions preserved. Capture06:26-06:27Z does not overlap original boundaries04:51:53-05:10:33Z. Separate6-signal canary:4 burst entries/1 exit have supported fresh raw context, but continuity uncertified;5 lack frozen controls. Pinned layouts, Token2022 fee epoch/cap tests, raw-normalized lineage for7808tx and future-data guards verified. v3/partial/special-fee/transfer-fee execution remain unsupported. Report: research/edgesniffer/evidence/quote-adapter-20261023-v1.json; RD-018. |
| AUD-L01A3h4 | prospective causal state/configuration continuity | P0 | IN_PROGRESS |User reprioritized fresh evidence after5d045d5. Reuse h3 math/decoder and bounded canary to diagnose native writes, late inputs, fork status and configuration read consistency; later reference checks never become features. Implement minimal synchronized acquisition/qualification for fresh fixed-rule experiments. Acceptance: per-boundary causal versus later-audit accounting, adversarial dropped/late/fork tests, deterministic real replay and bounded fresh capture-to-quote-to-outcome verification. Keep strict1s age, frozen controls, all size/arm intentions; preserve original78/468 without further reconstruction. Only after end-to-end acceptance run a separately predetermined continuous cohort, with independent signals and separate 2CQ labels. Own-state intervention, network/fee assumptions and unknown outcomes must be explicit before net returns. No trading. |
| AUD-L01A3h4a | synchronized acquisition and native observation replay slice | P0 | VERIFIED (bounded observation scope only) |120 Python tests local/VPS; repaired mixed RPC/native same-slot comparison, missing-metadata handling, scheduler/replay gap divergence and known-transition/branch false-readiness. Two fixed75s canaries, second50s signal+25s drain:22363tx,8751native account writes,383 raw mint bootstraps,73 synchronized read sets,0RPC errors/recorded gaps.11 opportunities/66 intentions:15 policy abstentions,51 unresolved economics; no orders.259/259 expected signatures in4 preselected finalized blocks with metadata equality. Full replay report SHA c8cf501263eaa3cf9b1f372ed89c1b6df80d5600cb67a650715b3e62105d82df, all6 output artifacts byte-identical. Source/contracts/small reports tracked; raw data on VPS h4-sync-canary1/2, final audit h4-sync-native-audit2 and replay. Snapshot-only and native-advanced observation results separate. Does NOT certify full write/fork continuity, quote fills, whole economic pipeline or profitability. |
| AUD-L01A3h4b | bounded nonblocking context acquisition and touch diagnostic | P0 | VERIFIED (acquisition; semantic qualification remains h4c) |Two request workers, one outstanding request per lane, no stale queue; later observer publication governs admission.123 tests local/VPS, sealed real replay identical SHA5a99422225d7388ccb06ef36c3cf4edddf172222ec4725b105d2fff0d3dfa587. Third fixed75s canary:12869tx,7354native account writes,334mint bootstraps,127synchronized requests/279total/0errors. Median observer lag35.397ms,max119.301ms versus425.147/1082.275ms in preceding separate period; not a controlled throughput benchmark.9 opportunities/54 intentions plus2 drain-only seeds;12 abstentions/42 unresolved economics;0quotes,0complete matched chains.192/192 expected signatures in4 finalized sample blocks with metadata equality. Bounded touch inspection on canary2:15 fee getter transactions had transaction-level writable privilege but IDL-readonly roles; one global router case remains unknown. No mutation-absence proof or quote promotion. RD-021; all further semantic qualification explicitly carried into h4c. |
| AUD-L01A3h4c | observed fee/mint semantics and transition qualification | P0 | TODO / exact next within active A3h4 |Use retained canary3 raw evidence first. Five of9 targets have holder-reward mode, two additional targets differ in raw mint versus nominal curve supply; current math guard excludes7/9 and all selected controls lack full supported context. Independently reconstruct holder-reward/protocol/creator cash flows, supply-dependent fee choice and integer reserves against successful native transactions plus later reference validation. Add instruction-level attribution for unmatched writable touches: reproduce15 IDL-readonly fee-getter cases and retain the unknown router case; never equate privilege with a mutation or IDL with deployed-code proof. Acceptance: versioned raw on-chain fixtures, causal-input/prefix tests, unchanged full denominator/control assignments and explicit unsupported cases. No new broad capture until coverage/own-state/cost outcome pipeline can actually evaluate the frozen strategies. |

| AUD-L01A1 | opportunity/attempt denominator | P0 | VERIFIED | Six tests; VPS read-only census: 1065 seeds / 3195 attempts, all evidence-blocked. Source and output hashes retained. |
| AUD-L01A2 | source contract inventory | P0 | VERIFIED | Bounded VPS tail samples and local receipt-normalizer inspection completed; account clocks/reserves exist, normalized receipts lack execution amounts/status. |
| AUD-L01A3 | processed evidence adapter | P0 | PARTIAL | Native transaction capture now verified on bounded real data. Causal ledger/controls integration is dependency-ready; current-state continuity, complete fee schedule and own-state net economics remain unverified. |
| AUD-L01A3a | retained processed-evidence join | P0 | VERIFIED | 20 local/VPS tests; sealed real-input replay; all 10364 receipts unresolved, no identifiable buy seeds. Binary/config manifest retained; source-to-binary build equivalence unproven. |
| AUD-L01A3b | independent processed-transaction evidence | P0 | VERIFIED | Recovery checked first; Pump-only standalone receive-only capture built/tested and 120s VPS canary audited. Bounded verification, not full-market completeness or a running permanent service. |
| AUD-L01A3b1 | historical receipt recovery | P0 | VERIFIED | 10364 receipts = 9988 signatures; 32/32 hash-selected RPC sample recovered (30 success, 2 failed). One archived hour: 213 matches, 5 with metadata, 208 without. Six added tests local/VPS; historical availability never backdated. Not an exhaustive recovery. |
| AUD-L01A3b2 | minimal Pump capture and completeness | P0 | VERIFIED | 45825 native tx frames, 5056 SOL-flow tx, 5033 timely account matches. 923/923 expected tx in 16 sampled finalized blocks; metadata parity matched. 33 Python tests local/VPS, 5 Rust tests, release build, deterministic native-view replay. |
| AUD-L01A3c | native burst/persistence feasibility | P0 | VERIFIED | 44 local tests, 11 new VPS tests, two independent normalized-input replays with identical report SHA ef27b99bc82a0ffdabca7734d932c601ed98dd06508f974a48e2b0f4decf1b9c. 18 seeds/108 intents; 3 complete burst/control pairs; persistence 3 confirmed/12 rejected/3 unknown, zero admitted matched outcomes. Price diagnostic only; not strategy validation. |
| AUD-T07a | failed-transaction population diagnostic | P1 | VERIFIED | 48 local tests total,4 diagnostic tests on VPS; full45825-row diagnostic. 38775 failures,0 repeated signatures; top10 payers25079 failures;25317 in repeated payload groups. 9675 buy/15933 sell intent/13167 unknown;12923 no observed Pump instruction. Raw error breakdown and717 later reference labels preserved. No predictive claim. |
| AUD-L01A3d | additional fixed-rule observation | P0 | VERIFIED (bounded run) |8x120s finished;157657 tx,78 first-mint seeds plus50 repeats retained;38 time blocks. All8 current-code opportunity/attempt replays identical.32 sampled blocks/1178 expected tx captured with metadata parity; no recorded within-session gaps.100-mint target NOT reached. |
| AUD-L01A3e | missing-mark mechanism / event-continuation falsifier | P0 | VERIFIED |59 tests local/VPS; two full-input runs have identical report hash06a9eeba9f5bc3bf6d721ff65ab4c17b5c5343d9827917301ba5887d91b04c59.35 burst flow pairs, mean target-minus-control+0.9365 SOL flow (NOT profit);11 persistence pairs mean-0.4809.39 stale exits:30 with intervening events,9 without new decoded Pump SOL events. Original78 seeds/controls unchanged. |
| AUD-L01A3f | native-account boundary-mark coverage audit | P0 | VERIFIED |256MiB sealed snapshot/321911 rows;43927 loose identity matches:15503 four-reserve matches,28424 mismatches; all78 mappings ambiguous under loose signature/key association.63 tests local/VPS; frozen-snapshot replay reproduces boundary/comparison hashes. No prices repaired or quotes certified. |
| AUD-L01A3g | canonical curve identity guard | P0 | VERIFIED (offline) |Canonical PDA excludes28424 unrelated rows;15503/15503 unique selected event transactions have matching canonical account state and all four reserves. All78 targets/36 existing controls have observed boundary state, not certified quotes.65 local/VPS tests including SDK derivation and wrong-account integration; deterministic replay.45/78 burst exit states remain older than1s; no primary price result replaced. |
| AUD-L01A3h | native quote-context closure | P0 | PARTIAL |h1 retained-input/bias diagnostic, h2 bounded context capture, h3 narrow arithmetic reconstruction and causal coverage adapter VERIFIED within scope. Original78/36 controls/468 intentions preserved. Arithmetic agreement is not causal quote coverage: zero original complete quote contexts; new canary6 signals is separate. h4 continuity/eligibility proof and subsequent own-state/cost accounting remain before net economics. No historical backdating, stale-state fill assumptions, rematching or strategy optimization. |
| AUD-L01A3d2 | additional statistical evidence | P1 | TODO | Current78 unique seeds/7 burst price pairs/4 admitted persistence pairs are insufficient. At least22 new mints to meet original100-mint engineering floor; that alone cannot cure missing-mark bias or establish edge. Choose next fixed capture budget only after A3e explains coverage; no return-dependent stopping or threshold optimization. |
| AUD-T07b | causal failure-pressure hypothesis | P2 | BLOCKED | Depends on T07a identifying interpretable coverage. Freeze feature/rules before outcomes; same-clock controls, full delay, failures never executed flow; additional native periods required before predictive validation. |
| AUD-T07a2 | failure mechanism and market-state attribution | P2 | TODO | T07a follow-up, not completed: map error codes to responsible programs/validated semantics; validate instruction-to-mint roles; examine native-time volatility/liquidity and successful-burst clustering with payer/retry adjustment. Account mentions and aggregate correlations are insufficient. Keep behind primary experiment. |

| ID | Type | Priority | Status | Current conclusion |
|---|---|---:|---|---|
| AUD-F01 | scorer correctness | P0 | TODO | Score-domain/post-event subfixes tested; shared full-feature parity and composite policy acceptance remain unmet. Benchmark only. |
| AUD-F02 | scorer metrics | P1 | VERIFIED | Tie-aware AP and strict unique-top ranking now prevent constant/tied scores from receiving perfect credit. |
| AUD-F03 | native decoder/economics | P0 | TODO | Event-driven missing-sell subfix tested; scorer source_event and frozen-window quote_sol still zero missing sell quotes. Full flow acceptance unmet. |
| AUD-F04A | native decoder variants | P0 | VERIFIED | Official Pump buy_v3/sell_v3 variants are now in the native selector and pinned decoder matrix. |
| AUD-F04B1 | execution/state semantics | P0 | SUPERSEDED | Local status repair preserved; end-to-end certification claim superseded by I01. |
| AUD-F04B2a | execution/state semantics | P0 | TODO | Max-cost fixture passes but explicit semantic labels override raw bytes; realized field provenance/availability unverified. |
| AUD-F04B2 | execution/state semantics | P0 | TODO | Split into bounded work; complete executed-transition coverage remains incomplete. |
| AUD-I01 | integration containment | P0 | VERIFIED | Known conditional replay rejected at scorer conversion, window joining, state parsing and quotes; 41 focused tests including actual replay integration. Local only. |
| AUD-R01 | existing-artifact diagnostic | P1 | VERIFIED | Recomputed 76 rows/67 mints; 0 selected and 25 first crossings lead >=1s. Descriptive only. |
| AUD-G01 | cohort governance | P1 | TODO | Cohort IDs, viewed trials, overwrite protection, split vocabulary and overlap need explicit lineage audit. |
| AUD-D01 | deployment provenance | P0 | TODO | Source snapshots differ; identify running binary/source/config hashes before deployment claims. |
| AUD-X13 | checkpoint integration | P1 | TODO | Checkpoint reader excludes event-driven and control record types (audit E13). |
| AUD-H01 | historical engine scope | P1 | TODO | Empty future may certify EXACT; paired-sell inventory and linear sizing labels need bounded claims. |
| AUD-F05 | lineage/state validity | P0 | BLOCKED | Launch bridge exists and fails closed; current source still lacks exact CREATE fields. |
| AUD-F06 | ordering/replay | P0 | TODO | Dedup keys and late-arriving seeds can erase valid transitions. |
| AUD-F07 | causal availability | P0 | TODO | Boolean/native-boundary labels do not prove state and fee availability by decision time. |
| AUD-F08 | economics denominator | P0 | TODO | Failed entries and unresolved inventory disappear from headline economics. |
| AUD-F09 | checkpoint/replay | P1 | TODO | Incremental checkpoint cursor can manufacture delay and lose early marks. |
| AUD-F10 | information-set/exit | P0 | TODO | Duplicate suppression, 300-second truncation, carry, and dynamic exits use wrong clocks. |
| AUD-F11 | own-state/economics | P0 | TODO | Historical marks omit own reserve intervention and some costs/fee semantics. |
| AUD-F12 | controls/registration | P0 | TODO | Controls, common-clock ranking, and contracts are not fully enforced. |
| AUD-L01 | measurement architecture | P0 | TODO | Build shared causal opportunity/attempt/position ledger and deterministic replay. |
| AUD-T01 | research experiment | P0 | BLOCKED | One-second residual-edge falsification; requires ledger and complete native inputs. |
| AUD-T02 | research experiment | P1 | BLOCKED | Simple independent burst benchmark; requires repaired signed flow and causal ledger. |
| AUD-T03 | research experiment | P1 | BLOCKED | Two-second persistence challenger; depends on T02 seed population. |
| AUD-T04 | research experiment | P2 | BLOCKED | Sell-shock recovery; depends on complete sell decoder/state parity. |
| AUD-T05 | research experiment | P2 | BLOCKED | Established-pool slower signal; conditional venue pivot only. |
| AUD-T06 | research experiment | P2 | BLOCKED | Adaptive exit comparison; entry family must survive first. |

## Implementation task cards

### AUD-F01 — Shared frozen-score domain and event-boundary parity

- Objective: make the native adapter either exactly reproduce the preserved
  scorer or explicitly become a separately named independent model. This task
  covers score domain and triggering-event boundary; remaining composite-policy
  behavior is explicitly separated rather than silently claimed as parity. It
  must not tune the threshold to preserve PnL.
- Evidence: audit report F01; detailed scorer finding 1 and 2; portable scorer
  `score_units` and native feature-boundary probes; current files
  `scripts/adapt2CQFrozenScorerNativeV1.py`,
  `scripts/twoCqCausalEntryTimingFingerprintV1.py`,
  `research/2cq-event-driven-fingerprint-freeze-v1/frozen-scorer.json`.
- Assessment before repair: VALID and reproduced. The adapter compared a raw
  linear predictor to probability threshold `0.4622968637150163`; correct
  comparison is probability-to-probability (equivalent logit cutoff is
  approximately `-0.15109936969630694`). It also scored pre-state/excluded the
  current event while training used post-state/included the event, and it did
  not implement the complete frozen composite policy.
- Assessment after repair: score domain and boundary are corrected. The adapter
  is now explicitly `timing_model_benchmark_only`; `score_probability`,
  `score_logit`, `threshold_domain`, `decision_boundary`, and composite-policy
  flags are recorded. The frozen JSON and historical outputs were not changed.
- Priority/dependencies: P0; no data dependency. Verified locally and by a
  read-only offline invocation path; not live-tested.
- Acceptance criteria: one shared score function with explicit `logit` and
  `probability` domains; parity tests at threshold, below/above threshold,
  extreme values, ties, and representative frozen vectors; explicit event
  boundary contract; no accidental threshold/model refit; adapter report shows
  exact-versus-independent mode and hashes.
- Verification: `PYTHONPATH=scripts python -m unittest` focused adapter,
  materializer, independent-lab, and lineage tests passed 22/22 after the
  repair; adapter unit tests passed 5/5. `py_compile` and `git diff --check`
  passed. The original portable scorer probe no longer runs because it calls
  the removed defect-only `scorer_value`; this is recorded rather than treated
  as a failed correctness fix. No current complete-lineage VPS fixture exists,
  so no live/native-data score run can be claimed.
- Artifacts: `research/2cq-native-frozen-scorer-adapter-v1/contract.json`,
  `scripts/adapt2CQFrozenScorerNativeV1.py`,
  `scripts/testAdapt2CQFrozenScorerNativeV1.py`, and the V2 output schema.
- Limitations: no historical PnL inference; complete CREATE lineage remains a
  separate blocker; cross-mint ranking, age gating, and lifecycle policy are
  not represented by this timing-only benchmark and must not be called exact
  composite parity.

### AUD-F02 — Tie-safe ranking and average precision

- Objective: prevent tied scores from receiving optimistic top-1/AP credit.
- Evidence: audit F02; `scripts/twoCqCausalEntryTimingFingerprintV1.py`;
  `twoCqPrimaryPolicySealedValidationV1.py`;
  `twoCqN350FrozenPolicyEconomicsSandboxV1.py`.
- Assessment: VALID and reproduces in the portable scorer probe. Actual
  historical impact is unknown because full score arrays are absent.
- Priority/dependencies: P1; depends on F01 score-domain contract. F01 is now verified.
- Acceptance criteria: unique-top-1, tie-rejected top-1, randomized tie credit,
  threshold-grouped AP, AUC, and tie-mass reporting agree on fixtures.
- Verification/artifacts: scorer metric tests and versioned metric report.
- Verification: `scripts.testTwoCqCausalTieMetricsV1` and the existing sealed
  validation tests passed 4/4. `py_compile` passed. The old portable scorer
  probe still targets the removed defect-only API and is preserved as historical
  reproduction evidence; a new correct-behavior regression is now in
  `scripts/testTwoCqCausalTieMetricsV1.py`.
- Artifacts: tie-aware `average_precision` and `ranking_metrics` in
  `scripts/twoCqCausalEntryTimingFingerprintV1.py`, strict unique-top gate in
  `scripts/twoCqPrimaryPolicySealedValidationV1.py`, and the new regression
  test file.
- Status: VERIFIED. No historical PnL or frozen policy was rewritten.

### AUD-F03 — Signed executed flow and sell decoding

- Objective: prevent missing sell quote amounts from becoming zero signed flow.
- Evidence: audit F03; native detailed N1; `rust-sidecar/src/main.rs` native
  decoder; `scripts/materializeNativePumpCurveStateV1.py`;
  `scripts/materialize2CQEventDrivenIndependentWindowsV1.py`.
- Assessment: VALID and reproduces: synthetic `-0.277008311 SOL` sell plus
  two `+0.1 SOL` buys is reported as `+0.2 SOL` flow.
- Priority/dependencies: P0; requires versioned executed-event semantics and
  fixtures, independent of scorer repair. F02 is verified; the bounded
  downstream accounting repair is verified and the remaining execution decoder
  work is tracked under AUD-F04B2.
- Acceptance criteria: buys, sells, failed intents, and unknown amounts have
  typed fields; signed economic flow uses executed semantics; unknown is not
  zero; decoder coverage denominator is reported.
- Verification/artifacts: Rust/Python fixtures, signed-flow parity report,
  source/build hash, and current native replay audit.
- Assessment after repair: the independent event-driven materializer now reads
  `sell_quote_lamports`/`sellQuoteLamports` when present, never converts an
  unknown buy/sell quote to zero, emits `unknown_quote_count_*`, and propagates
  incomplete signed flow as `None`, which fails the positive-flow gate.
- Verification: affected local suite passed 16/16; VPS-compatible read-only
  suite passed 4/4; the exact audit counterexample is now -0.077 SOL rather
  than +0.2 SOL. A read-only scan of the current native replay found 4,361
  sells and 0 exact sell-quote fields, so the source remains incomplete for
  those sells and they are correctly not treated as executed quote flow. No
  collector was restarted and no service was changed.
- Status: VERIFIED for downstream fail-closed accounting; upstream exact sell
  execution telemetry remains a limitation carried into AUD-F04.

### AUD-F04A — Versioned Pump variant matrix and V3 selector coverage

- Objective: pin the supported Pump instruction variants and prevent official
  `buy_v3`/`sell_v3` trades from being silently excluded by the native selector.
- Evidence: audit F04/N2; official Pump IDL and trade-v3 documentation; current
  `rust-sidecar/src/main.rs` decoder matrix.
- Implementation: added official V3 discriminators to the native selectors and
  `pumpfun_sell_line_from_parts`; added
  `research/native-decoder-matrix-v1/decoder-matrix.json` and
  `scripts/validateNativeDecoderMatrixV1.py`.
- Verification: matrix tests passed 2/2; default `cargo check --bin
  pumpfun-sidecar` passed; default `cargo test --bin pumpfun-sidecar` passed
  173/173. The feature-enabled Windows build could not start because
  `openssl-sys` lacks a local OpenSSL installation. The current VPS service
  was not restarted, so this is source/build verification only, not VPS-live
  deployment verification.
- Limitation: this adds recognition only. It does not certify successful
  execution, resolve CPI/ALT accounts, or produce executed quote amounts.
- Status: VERIFIED; no collector or frozen evidence changed.

### AUD-F04B1 — Explicit execution-status quarantine

- Objective: prevent failed or status-unknown native observations from being
  silently treated as authoritative executed state or complete economics.
- Implementation: `scripts/materializeNativePumpCurveStateV1.py` now emits
  `execution_status` and `state_transition_authority`. Explicit failed rows are
  rejected and counted in `rejected_failed_execution`; status-unknown rows may
  remain as conditional intent replay but are marked `UNKNOWN` and cannot be
  `native_economics_state_complete`.
- Verification: local materializer/lab tests passed 18/18 in the affected
  suite and the VPS-compatible read-only materializer tests passed 6/6 on
  `root@159.195.207.109`. No service was restarted, no collector changed, and
  this was not live trading or a deployed VPS decoder test.
- Limitation: unknown intent replay is retained only for conditional state
  reconstruction; realized max-cost versus executed amount, CPI/ALT account
  resolution, migration, and full processed/finalized fixtures remain open.
- Status: VERIFIED as a bounded downstream fail-closed repair.

### AUD-F04B2a — Quote-semantics quarantine

- Objective: prevent a Pump buy's `maxSolCost` limit from being replayed as
  executed SOL input.
- Implementation: `scripts/materializeNativePumpCurveStateV1.py` now decodes
  the known native buy discriminator from `source_ix_data_base64`, labels
  `MAX_SOL_COST_INTENT` versus exact-input intent, accepts an explicit realized
  quote only from execution fields, and quarantines max-cost/unknown buys when
  no realized quote is available. Exact-input intents remain explicitly
  conditional and are never marked authoritative economics without success.
- Verification: local focused suite passed 20/20 and the full audit-focused
  suite passed 38/38; VPS-compatible materializer tests passed 8/8. No
  collector or service was restarted and no live execution was enabled.
- Limitation: this does not create realized amounts that the native stream does
  not carry. CPI/ALT resolution, migration, processed/finalized success joins,
  and exact sell proceeds remain under AUD-F04B2.
- Status: VERIFIED as a bounded decoder/economics repair.

### AUD-F04B2 — Versioned executed-transition decoder

- Objective: separate intent from successful execution and cover supported
  Pump instruction variants, inner instructions, lookup tables, migration, and
  CPI/state-changing activity.
- Evidence: audit F04; native detailed N2/N3; official Pump references P1-P4
  in the audit; current `rust-sidecar/src/main.rs` decoder matrix.
- Assessment: VALID at source level; production incidence unknown. V3 selector
  recognition is now covered by AUD-F04A, failed/unknown status handling by
  AUD-F04B1, and max-cost quote quarantine by AUD-F04B2a. Realized execution
  joins, CPI/ALT resolution, migration, and complete processed/finalized
  execution coverage remain open.
- Priority/dependencies: P0; foundational for F03, F06, F07, F11, and all
  economics experiments. Parent task split; AUD-I01 is the active bounded repair.
- Acceptance criteria: mode matrix and fixtures for every admitted variant;
  successful execution required for authoritative state; unsupported/failed
  transitions quarantined with reasons; migration/completion state explicit.
- Verification/artifacts: decoder coverage report, golden processed/finalized
  fixtures, and replay parity checks.
- Status: TODO; parent decoder task, not a second active implementation.

### AUD-I01 — Prevent conditional replay from becoming economic evidence

- Objective: reject known conditional/incomplete replay at shared state consumers,
  native scorer conversion, entry quoting and window joining.
- Evidence: current lab event_state ignores authority; frozen window builder
  overwrites completeness with true; replay labels SUCCESS as EXECUTED despite
  fixed-fee approximation and uncertain predecessor transitions.
- Priority: P0; no new market data needed. Depends only on current source.
- Acceptance: SUCCESS alone never certifies approximate replay; conditional rows
  cannot yield scorer events, entry quotes, exit quotes or frozen windows;
  valid legacy untagged fixtures retain their exploratory parsing behavior.
- Verification: adversarial replay-to-window integration plus affected unit suite;
  no claim of full causal certification or deployment from this containment fix.
- Status: VERIFIED locally, 41/41 affected tests including testReplayEvidenceContainmentV1.
  Explicit SUCCESS replay remains conditional; legacy replay provenance overrides
  claimed completeness. Negative-evidence containment does not certify untagged
  data, fee math, continuity, or past windows whose lineage was already stripped.
  Source diff reviewed at changed functions. No VPS deployment/live test this turn.

### AUD-R01 — Recompute the teacher timing premise

- Objective: determine which teacher timing claims are supported by preserved
  assessment rows without requiring new capture or assuming executable prices.
- Input: research/2cq-event-driven-fingerprint-freeze-v1/report.json; audit
  scorer findings and reference outputs. No new cohort or threshold selection.
- Acceptance: hash input; report all 76 rows, distinct mints, missing crossings,
  median selected/first-crossing receipt leads, >=1s counts, alert concentration;
  explicitly prohibit PnL, chain-order or market-wide precision inference.
- Priority/dependencies: P1, independent of missing native execution evidence.
- Status: VERIFIED. scripts/auditTeacherTimingPremiseV1.py and
  research/audit-reassessment-v1/teacher-timing-premise-v1.json. Row aggregates
  checked against preserved summaries: 76/67, median 36.274923ms, 0/76 >=1s;
  64 crossings, median 108.9274025ms, 25/76 >=1s, 3732 states, max episode 1156.
  Exclusive output creation prevents overwriting an earlier diagnostic.

## Reassessment correction and coverage map (2026-10-22)

This section overrides older task-card VERIFIED wording where the summary table
reopens a task. Earlier command results remain evidence of their narrow fixtures,
not proof of whole-pipeline correctness. A broken old probe API is not evidence
that its economic invariant was fixed. F04A is discriminator recognition only;
the feature-enabled build and current venue layout parity remain unverified.

All headline F01-F12 are represented. Detailed findings map as follows:

| Detailed audit items | Accountable tasks |
|---|---|
| Native N1 / scorer missing sell | F03, F04B2 |
| Native N2-N3, N13: variants, amounts, ALT/CPI | F04A, F04B2a, F04B2 |
| Native N4: assumed fees and false exactness | I01, F11 |
| Native N5,N9,N12: CREATE, owner/discriminator, phase, provenance | F05 |
| Native N6-N7: identities, late seeds | F06 |
| Native N8,N10: continuity, availability | F06,F07 |
| Native N11 / scorer 1-2: features and score parity | F01 |
| Scorer 3: ties | F02 |
| Scorer 4,8: future CREATE and finalized inputs | F07,F05 |
| Scorer 5 and cohort appendix: all-mint overlap / split / consumed holdout | G01,F12 |
| Scorer 6-7: controls and common clocks, teacher removal | F12 |
| Economics E01-E02: missing attempts/inventory | F08,L01 |
| Economics E03,E07: cursor and exit clocks | F09,F10 |
| Economics E04-E06: deleted events, horizon, future carry | F10 |
| Economics E08-E09,E11-E12: own impact, fees, liquidity, costs | F11 |
| Economics E10: native guard | F07,I01 |
| Economics E13: incompatible checkpoint record types | X13 |
| Historical engine appendices | H01,F08,F11 |
| Native snapshot drift/build evidence | D01 |
| All six proposed strategy experiments | T01-T06; reassessment ranks first three |

### Additional task acceptance and dependencies

- AUD-G01 (TODO): inspect allocation manifests and source consumers; registry of
  distinct N350 populations, inspected trials, trainer/input/output hashes;
  test VALIDATION_SEALED versus UNTOUCHED_VALIDATION translation if connected;
  prevent overwriting frozen artifacts; distinguish chronological from mint-disjoint
  validation. Evidence: scorer cohort appendix, split scripts, freeze writer.
  Missing manifests block historical certification, not source investigation.
- AUD-D01 (TODO): read-only deployed binary/source/service-filter comparison;
  record hashes with secrets excluded and map deployed snapshot to tested code.
  Evidence: native snapshot table. No restart required. Acceptance: a reproducible
  runtime provenance manifest; local tests alone cannot close this task.
- AUD-X13 (TODO): checkpoint reader schema at lines 115-117 excludes new windows;
  test candidate/control parity through checkpoint reader. Depends on choosing
  active window schema; do not fix vocabulary and imply cursor F09 is resolved.
- AUD-H01 (TODO): existing historical helpers may fabricate EXACT endpoints with
  empty future; require coverage watermark, verify paired exit inventory equals
  owned inventory, label linear normalized PnL as arithmetic only. Evidence:
  economics historical appendix and empty-future portable probe. Acceptance:
  quiet-covered versus missing-tape tests, inventory conservation, honest labels.

### Next dependency-ready implementation

AUD-L01A (PARTIAL, P0): immutable opportunity admission ledger for ONE supported
processed-state route. Enumerate all independent seeds before inspecting exits;
emit one status per seed and each of 0.1/0.5/1 SOL, preserving unsupported,
unknown-state, failed-quote, open and closed outcomes. Preserve input references,
decision-ready/state/fee clocks and gap reasons. First acceptance: identical
seed IDs across sizes, no lost failures, future append cannot alter decisions.
Reuse integer quote helpers only after mode-specific fixtures; no full universal
decoder requirement. Implementation starts with synthetic fixtures and source
inventory; historical/native validity requires the corresponding tape evidence.
AUD-L01A is split: A1 admission and attempt denominator; A2 retained-source
contract inventory; A3 processed evidence adapter followed by model quote/result
transitions and delayed fixed-exit experiment. A1/A2/A3a complete; none active.
Acceptance for A3a: identify actual observer binary/source scope read-only;
join receipt to account evidence by exact transaction signature, slot and curve;
retain absent/ambiguous/late evidence; never infer execution success or flow from
the join; test clock boundaries, duplicates, conflicting writes, prefix invariance
and ledger denominator integration; run against an immutable bounded real sample.
Acceptance for A1: deterministic IDs,
dedup conflicts exposed, every seed has 0.1/0.5/1 SOL attempt rows, failed data
qualification never removes seeds, teacher labels do not affect admission,
future append cannot alter prior decisions. Existing native replay can be
inventoried as untrusted input; no source flag alone certifies economics.
Artifact: research/edgesniffer/ledger_v1.py at Git root. Verification: adversarial
fixtures then read-only native-source census. No collector modification.

A1 verification: `python -m unittest discover -s research/edgesniffer -p test_ledger_v1.py`
passed 6/6. Real CLI ran on VPS against a byte-bounded prefix of native-boundary
replay: 9571 rows, 1065 seeds, 3195 attempts; all conditional/unproven; 254 seeds
nonmonotonic, 2 incomplete reserves. Results/hashes:
research/edgesniffer/evidence/ledger-a1-20261022-v1.json. Remote ledger preserved
at /var/tmp/edgesniffer-ledger-a1-20261022-v1/ledger.jsonl. No strategy economics
computed. Source review confirms streaming prefix reads, immutable run directory,
three-size conservation and no teacher admission. Late observations are flagged,
not sorted into prior decisions. This is a first-observed-buy coverage population,
not a burst signal, and does not estimate market-wide opportunity rate.

A2 acceptance: bounded read-only inventory of existing transaction receipts,
account states and fee snapshots; identify usable fields, clocks and gaps without
printing credentials or mutating sources. Preserve report and make an explicit
adapter/capture decision. No protocol claim from field names alone.

A2 result: research/edgesniffer/evidence/source-inventory-a2-20261022-v1.json.
200 account sample rows have reserve/phase/writeVersion/signature/receive/decode
keys; 200 normalized receipts have clocks/identity but no execution amounts or
success. One fee config snapshot has capturedAtMs and raw data. Bounded tails
are schema evidence, not full coverage/non-null/accuracy proof. Local receipt
normalizer selects only identity/clock fields. Therefore reuse existing account
capture; inspect original metadata before concluding new capture is required.

Historical next-action checkpoint after A3a (completed by A3b below): implement A3b's minimal independent
processed-transaction evidence sink, reusing existing full-meta capture code.
Retain raw transaction/meta, explicit success/error, resolved accounts/inner
instructions, native receive/decode clocks and subscription identity. Include
failed transactions rather than filtering them out. Use a separate receive-only
artifact; do not alter existing cohorts or restart collectors as an incidental
step. Verify fixtures and bounded capture before wiring executed-flow features.
Do not invent success or backdate finalized enrichment. Economic admission remains
BLOCKED; missing fee validity, current-state and own-state quotes are not repaired.
No strategy simulation or live execution completed by this census.

Source-trace note: local main.remote.current.rs contains a target-wallet-only
receipt emitter, while the normalizer accepts both target and Pump-curve receipt
kinds. Searching the VPS /root/bot/EdgeSniffer/rust-sidecar/src/main.rs found no
Pump-curve receipt emitter string. Therefore that source path is not sufficient
to identify the actual emitter; D01 runtime binary/source attribution is needed.
Do not assume the full normalized receipt population is teacher-only or market-wide
from one snapshot. This remains an explicit A3 source-scope gap.

A3a resolution (supersedes that source-trace uncertainty for the sampled files):
actual observer executable is /root/bot/FinalBot-cya/rust-sidecar/target/release/pumpfun-sidecar.
Running/on-disk hashes match; effective submit=false, owner-wide account and
transaction flags=true. Adjacent main.rs emits target-wallet receipts, with no
Pump-curve receipt emitter. Hashes/config in observer-provenance-a3a-20261022-v1.json;
reproducible source-to-binary mapping remains unproven (D01 still open).
Full 10364-row normalized receipt prefix contains only target receipt kinds;
curve/mint/side/instruction_index are null in every row, despite keys existing.
The 64MiB account-file sample contains 80680 account states, 94 target receipts,
27 stats rows. Raw sampled receipts also lack execution metadata. Another
200-row target tape sample retains raw instructions but not execution metadata;
200 market-wide normalized observations have no execution object/status evidence.
These are bounded source findings, not a claim that no metadata exists anywhere.

Artifacts at Git root: research/edgesniffer/processed_evidence_join_v1.py,
observer_provenance_v1.py, test_processed_evidence_join_v1.py and evidence/*a3a*.
Verification: 20 tests locally and on VPS; compile check; actual sealed-input
replay yields identical joins and ledger hashes. Synthetic integration preserves
each identifiable buy seed across three sizes, rejects future/ambiguous/invalid
states, and never promotes a match to economics. Real integration creates zero
buy opportunities because side/mint are missing, NOT because the wallet did not
trade. All 10364 receipt results remain in joins.jsonl; 376 repeated-identity
different-content rows are explicit ledger conflicts, not inferred extra trades.
All economics remain uncomputed. Verified code is offline only, not deployed in
a running collector or live-tested with execution.

Preserved source samples/code/results:
/mnt/HC_Volume_106775198/edgesniffer-research/offline-ledger-a3a-20261022-v1
(run1 initial sealed input, run2 identical-output replay, final-code/run3 final
implementation and replay; final report processed-join-a3a-20261022-v2.json).
Small reports are version controlled; raw snapshots remain on VPS.
No original files, services, cohorts, frozen policies or trading settings changed.

### A3b completion and current exact next action (2026-10-23)

This supersedes the prior next-action text above; older findings stay preserved.

- Historical recovery: sealed 10364 receipts contain 9988 distinct signatures.
  Lowest-SHA256 outcome-blind 32-signature RPC sample recovered 32 transactions:
  30 success, 2 failed; all instruction identities resolved. 9956 were not queried.
  One 422MB retained archive hour scanned/checksummed 1005393 frames, matched
  213 signatures. Existing protobuf normalizer found meta in 5 and absent in 208.
  Not an exhaustive archive search or full recovery of 9988 transactions.
  Recovered finalized meta has recovery response availability; the original
  receipt clock is preserved separately. No native decision-time certification.
- Scope decision: all Pump-program-account transactions (including CPI and failed
  execution), not all Solana venues; no teacher/mint shortlist. Needed so candidate
  and non-candidate controls share an observable universe. Runtime post-filtering
  identifies actual Pump instructions and known SOL quote trades, retaining other
  records in the coverage denominator. The older teacher receipts remain teacher
  evidence even after enrichment, not independent controls.
- Implementation: tracked research/edgesniffer/pump_capture standalone Rust crate
  reuses existing prost/Yellowstone transport; raw full SubscribeUpdate plus
  transaction view, outer/inner indices, stack heights, static/loaded ALT keys,
  status/error, balances, fees, logs and return data preserved. No execution API.
  Slot lifecycle, process/session/connection identity, sequence and clocks retained.
  Python decoder recognizes known event quote layout, retains unknown tails and
  does not confuse instruction limits/transaction balance deltas with swap fills.
- Timing: received_at is application dequeue AFTER transport decode, not NIC time;
  normalized_at follows Rust view decode. Provider envelope time, chain event time,
  recovery and finality are separate. Monotonic time is session-local. Finalized
  RPC is offline validation only. No HTTP added to any existing execution hot path.
- Canary: canary1 failed before connecting due to missing explicit Rustls crypto
  provider; preserved. Added explicit ring provider and regression test. Canary2
  completed 120 seconds successfully with no existing service changes:
  45825 tx frames = 7050 successful + 38775 failed + 0 unknown in this sample.
  These are observed market executions, not our submitted trades or our fill rate.
  45825 instruction identity views resolved; 5908 trade-event payloads retained;
  5820 executed known-layout events, 5058 SOL-flow events in 5056 transactions.
  No unknown quote layouts in this sample; unknowns still handled fail-closed.
- Joins: 5813/7050 success tx have matching account writes; not every successful
  transaction is a curve swap. SOL-flow-specific: 5056/5056 identity joins,
  5033/5056 by native metadata availability +1 second (23 late, retained).
  This is NOT the old global 99% parity gate or proof of executable entry state.
- Completeness: no recorded sequence discontinuities, reconnect gaps, raw-hash
  mismatches or numeric slot gaps. Sixteen hash-selected interior finalized blocks
  had 923 expected Pump signatures; 923 captured, zero missing. Status, resolved
  keys, lamport balances, fee and event bytes matched for all 923. Sample-only;
  global completeness UNKNOWN; unresolved reference blocks would remain unknown.
- Verification: 33 Python tests local and VPS; 5 Rust tests, including TLS, failed
  and missing meta, ALT/CPI and null inner context; offline locked release build.
  Final decoder replay preserved hashes: transactions 9b725daf06ba54c371bef928614701c0da494ddb694099b7a7ecdd23d55f66b5;
  account joins 565343ad17862338ad2170d1d1d63287b3cb7bcf131424a3962844731dfc4a08.
  Actual binary 5be69002c0d76722ec2d7c267b3d6230654321cf7cd16cf53c3ec8ed21d2d88f;
  rustc 1.99.1; Cargo.lock retained. No live trading test performed.
- Evidence: research/edgesniffer/evidence/receipt-recovery-20261023-v1.json,
  retained-metadata-20261023-v1.json, pump-capture-quality-20261023-v2.json,
  pump-capture-manifest-20261023-v1.json. Full inputs/recovery/canary/raw blocks/code
  and preserved binary under /mnt/HC_Volume_106775198/edgesniffer-research/transaction-evidence-v1-qvxRqmv3.
- Runtime: new canary is FINISHED, not a permanent background collector. Existing
  collectors untouched. Default capture duration 120s; fresh bounded runs are
  available without restarting original collectors. No trading enabled.
- Historical next (now completed in A3c/RD-009): native burst vs persistence feasibility, using current development
  canary and same-period non-signal Pump SOL risk-set controls; preserve every seed
  and three size intents. Test signal/flow/price continuation at +1s/+2s without
  selecting new thresholds. Current data can answer this narrower question now.
  Net-PnL remains blocked on delayed current-state/fee/own-impact/attempt integration;
  it is not established by event matching. New prospective data after policy
  selection is mandatory for validation; canary is not prospective strategy proof.

### AUD-F05 — Exact CREATE/launch lineage and active-curve validity

- Objective: provide exact create clock, creator, initial reserves, original
  instruction evidence, and active-curve status without first-observation
  substitution.
- Evidence: audit F05; native detailed N5; current
  `scripts/materialize2CQNativeLaunchLineageV1.py`,
  `scripts/adapt2CQFrozenScorerNativeV1.py`, and
  `research/2cq-native-frozen-scorer-adapter-v1/launch-lineage-contract.json`.
- Assessment: PARTIALLY BUILT / BLOCKED. The bridge is fail-closed and tested;
  VPS audit found 1,301 current mints, 0 complete lineage rows. The registry
  is an older CREATE clock registry and lacks creator/initial reserves.
- Priority/dependencies: P0; requires a current exact CREATE decoder/evidence
  source. The exact teacher scorer remains blocked; simpler recent-flow tests
  may omit launch features but must use a separate contract.
- Acceptance criteria: current native mints receive exact CREATE lineage or a
  recorded missing reason; creation vocabulary is normalized; completed curves
  are rejected; availability clocks and evidence IDs survive downstream.
- Verification: local 5/5 bridge tests, broader 17/17 focused suite, VPS
  read-only reports already recorded; remaining acceptance requires nonzero
  current complete rows and source coverage evidence.
- Artifacts: launch-lineage output/report and adapter reports under
  `/mnt/HC_Volume_106775198/edgesniffer-research/2cq-native-frozen-scorer-adapter-v1/`.
- Status: BLOCKED pending current exact CREATE evidence.

### AUD-F06 — Canonical ordering, identity, and late-seed replay

- Objective: preserve signature/instruction/write-version identity and make
  clean, delayed, duplicate, and late-seed replay deterministic.
- Evidence: audit F06; native detailed N6/N7/N8;
  `scripts/materializeNativePumpCurveStateV1.py`; Rust Jito batch indexing.
- Assessment: VALID and reproduces. Sorting after tuple-based deletion cannot
  recover dropped events; late seeds can erase transitions.
- Priority/dependencies: P0; depends on F04 event identities, feeds F07/F11.
- Acceptance criteria: canonical and arrival order separate; dedup includes
  signature plus instruction identity; late snapshots rebase/replay or
  quarantine; write-version/signature retained; restart schedule invariant.
- Verification/artifacts: adversarial replay fixture matrix and deterministic
  parity report.
- Status: TODO.

### AUD-F07 — Bitemporal causal availability guard

- Objective: distinguish chain order, ingress, decode, feature-ready, execution
  status, finality, and fetched-at clocks; reject future state at decision time.
- Evidence: audit F07; native/scorer detailed availability findings;
  `runIndependentStrategyLabV1.py` boundary guard and finalized bridges.
- Assessment: VALID and reproduces; current boolean/source guard is too weak.
- Priority/dependencies: P0; depends on F04/F06, required by ledger L01.
- Acceptance criteria: state/fee available-at <= decision boundary is enforced;
  future CREATE/state fixture rejects; whole-transaction boundary recorded;
  finalized labels cannot overwrite decision-time inputs.
- Verification/artifacts: bitemporal fixtures, boundary report, and ledger
  schema checks.
- Status: TODO.

### AUD-F08 — Complete opportunity/attempt/inventory denominator

- Objective: retain every eligible opportunity at every size, including guard
  rejects, failed entries, open inventory, unresolved exits, and capital time.
- Evidence: audit F08; economics detailed E01/E02;
  `scripts/runIndependentStrategyLabV1.py`.
- Assessment: VALID and reproduces: failed quotes disappear and headline PnL
  sums only actionable exits.
- Priority/dependencies: P0; requires F11 quote/state semantics; core of L01.
- Acceptance criteria: one opportunity population shared by 0.1/0.5/1 SOL;
  typed attempt statuses; unresolved-value intervals; realized/cash/inventory
  and opportunity-level net SOL; no silent skip.
- Verification/artifacts: synthetic insufficient-inventory and unresolved-exit
  regressions; causal opportunity ledger report.
- Status: TODO.

### AUD-F09 — Incremental checkpoint and forward-coverage correctness

- Objective: make incremental and clean checkpoint replay agree and distinguish
  timer exit intent from next account mutation.
- Evidence: audit F09; economics detailed E03/E07;
  `materializeNativeReserveHorizonCheckpointsV1.py` and refresh service.
- Assessment: VALID and reproduces on synthetic identical-file schedules.
- Priority/dependencies: P1; depends on F06/F07 and opportunity registration.
- Acceptance criteria: per-curve retained history; late window publication can
  recover earliest qualifying mark; incomplete lines are retried; exit timer
  emits intent from verified current state rather than waiting for mutation.
- Verification/artifacts: clean/incremental randomized schedule parity report.
- Status: TODO.

### AUD-F10 — Decision-time windows and exit information set

- Objective: prevent duplicate event deletion, structural 300-second truncation,
  future-dependent carry, and same-mark dynamic fills.
- Evidence: audit F10; economics detailed E04-E07;
  materializers and strategy lab.
- Assessment: VALID in broad/exit paths; event-driven duplicate append behavior
  is better but does not resolve all information-set issues.
- Priority/dependencies: P0; depends on F06/F07/F08/F11.
- Acceptance criteria: all market events retained; windows include delayed entry
  plus maximum hold/delivery/retry; exit decision immutable at timer; future
  append cannot change prior exit intent; dynamic exit has delivery delay.
- Verification/artifacts: prefix-invariance and horizon-boundary tests.
- Status: TODO.

### AUD-F11 — Own-state reserve replay and typed costs

- Objective: quote each size against a separate own-state path, propagate buys,
  partial sells, fees, min-out, priority/tips/rent, and failed submissions.
- Evidence: audit F11; economics detailed E08-E12; `exactCurveEconomicsV1.py`
  useful components; current lab/quote helpers.
- Assessment: VALID and reproduces. Current lab is a mark approximation, not a
  self-consistent execution replay; fee defaults and virtual-reserve filter are
  misleading.
- Priority/dependencies: P0; depends on F04/F06/F07; core of L01.
- Acceptance criteria: integer state/inventory conservation; three sizes share
  opportunities; partial legs mutate state; unknown fees fail closed; real and
  virtual reserves separated; cost bridge records every leg.
- Verification/artifacts: flat round-trip, partial-leg, fee-typing, min-out,
  and size-capacity fixtures plus economics bridge report.
- Status: TODO.

### AUD-F12 — Controls, common clocks, and contract enforcement

- Objective: build clean same-clock controls and make experiment contracts
  enforce runtime parameters instead of serving as decorative metadata.
- Evidence: audit F12; scorer detailed findings 6-7; current adapter/control
  bridge and `runNativeLatencyBoundedStudyV1.py`.
- Assessment: VALID. Current control bridge emits non-crossing events but can
  reuse the signal mint/path; current lab grids are hardcoded and contracts are
  not enforced.
- Priority/dependencies: P0; depends on F01/F06/F07/F08; required before T02.
- Acceptance criteria: controls are risk-set defined, non-signal at common clock,
  no future selection, reuse/dependence recorded; runtime fails on contract
  mismatch; common-clock features use the declared tick.
- Verification/artifacts: control contamination, common-clock staleness, and
  contract-mismatch regressions; versioned control report.
- Status: TODO.

## Architecture and research tasks

### AUD-L01 — Versioned causal opportunity/attempt/position ledger

- Objective: implement the audit's single best build: append-only observation,
  state, opportunity, attempt, position, and liquidation records with shared
  features, integer quotes, exact hashes, and deterministic replay.
- Evidence: audit sections 5-6 and acceptance tests; useful existing components
  in `exactCurveEconomicsV1.py`, native telemetry, and current materializers.
- Assessment: not implemented as one coherent ledger. Existing artifacts are
  split and use incompatible semantics.
- Priority/dependencies: P0; depends on F03/F04/F06/F07/F08/F10/F11, although
  a minimal schema/fixture slice may be developed after F01.
- Acceptance criteria: causal prefix invariance, replay determinism, conservation,
  forward-coverage, all-attempt denominator, shared feature/quote functions,
  and a one-page funnel report. It must falsify a simple delayed strategy.
- Verification/artifacts: ledger contract, golden fixtures, replay checksums,
  `quote_and_capture_parity_report.json`, and data-quality/economics funnel.
- Status: TODO.

### AUD-T01 — One-second residual-edge falsification

- Hypothesis: an independently observable signal retains worthwhile net edge at
  decision-ready time +1,000 ms.
- Decision/entry/exit: independent decision `t_d`; modeled entry >= `t_d+1000ms`;
  primary 0.1 SOL; full exit at entry+15s plus 1s delivery; 0.5/1 SOL capacity.
- Costs/controls: exact versioned fees, own-state quote, constraints, all attempts;
  same-clock eligible matched controls; delay diagnostics 0/250/500/1000/2000/5000ms.
- Primary outcome/stopping: net SOL per intended opportunity and matched excess;
  stop/demote if valid generous ceiling is inadequate or futility bound excludes
  worthwhile effect. Target 300-500 development episodes, then power-sized fresh
  confirmation.
- Dependencies/status: L01 and native capture; BLOCKED. No current PnL validates it.

### AUD-T02 — Simple independent burst benchmark

- Hypothesis: recent external burst plus breadth/price continuation has residual
  edge after one-second delay.
- Rule: successful whole-transaction post-state; last two external swaps buys;
  >=2 distinct buyers in 250ms; positive signed executed flow and price change;
  first qualifying opportunity per mint/policy after warmup.
- Economics: 0.1 primary, 0.5/1 capacity; fixed 15s exit plus 1s delivery;
  common risk-set controls; failures remain denominator.
- Stopping: 100 engineering, 500 development, then >=2,000 fresh opportunities
  across >=14 days or a preregistered power calculation; no grid winner search.
- Dependencies/status: first native price diagnostic implemented in A3c;
  executable net economics and prospective strategy validation remain BLOCKED
  on F03/F04/F07/F08/F11/F12/L01 acceptance. Do not conflate these scopes.

### AUD-T03 — Two-second persistence challenger

- Hypothesis: waiting for persistence improves delayed executable economics.
- Rule: from T02 seeds, at seed+2s retain >=50% initial positive price move,
  positive flow, >=2 new buyers, no buyer >60% of confirmation buy SOL; decide
  then and enter one second later. Compare all seeds, not only confirmations.
- Status: first same-seed native price diagnostic implemented in A3c;
  strategy validation BLOCKED on adequate matched outcomes and net economics.

### AUD-T04 — Sell-shock recovery

- Hypothesis: observable selling followed by a confirmed recovery can be a
  separate independent entry family.
- Rule: two-second negative sell shock/price drawdown, then one-second no-new-low,
  positive flow, two new buyers; enter one second after confirmation.
- Status: BLOCKED until sell decoder/state parity (F03/F04/F11); no teacher trigger.

### AUD-T05 — Established PumpSwap slow-flow pivot

- Hypothesis: deeper, properly modeled pools improve capacity/latency economics
  if early-curve residual edge fails.
- Rule: prospective migrated pools, common-clock positive 5s flow, >=5 buyers,
  positive price change, 1 SOL average impact <=0.5%; enter +1s, exit +60s +1s.
- Status: BLOCKED/conditional; requires canonical pool reserves, fees, retained
  fees, route coverage, and a fresh cohort. Do not mix with Pump curve arms.

### AUD-T06 — Single adaptive exit comparison

- Hypothesis: one predeclared adaptive exit improves a surviving entry family.
- Rule: after entry survives, exit when after-cost liquidation return crosses
  +5% or -5%, otherwise fixed maximum hold; apply 1s delivery and constraints.
- Status: BLOCKED until an entry family has positive delayed fixed-hold evidence.

## Existing completed or superseded work

- The preserved teacher scorer, frozen reports, and prior cohort outputs remain
  intact. They are hypothesis/development evidence, not independent validation.
- The native launch-lineage bridge is implemented and verified but remains
  blocked on current exact CREATE evidence; it must not be marked complete as a
  data-coverage task.
- The earlier broad/event-driven exploratory PnL surfaces remain preserved and
  must not be relabeled as exact frozen-scorer or live-parity results.
- The external audit's portable probes are reproduction evidence. After a fix,
  an original probe failing may be correct; every repaired invariant needs a new
  correct-behavior regression.

## Continuation rule

At every continuation, read AGENTS.md, this register and recent research decisions.
Keep at most one implementation task `IN_PROGRESS`, complete its acceptance criteria, run affected tests and
integration checks, review the diff, and only then advance to the next
dependency-ready task. Research tasks may remain BLOCKED while independent
read-only health checks continue.
