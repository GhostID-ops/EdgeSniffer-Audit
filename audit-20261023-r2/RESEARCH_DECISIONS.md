# EdgeSniffer research decision log

## RD-022 — 2026-10-23 — Expand public research context without exposing private runtime

- At the user's explicit request, published a broader public audit snapshot to
  [GhostID-ops/EdgeSniffer-Audit](https://github.com/GhostID-ops/EdgeSniffer-Audit),
  directory `audit-20261023-r2`, commit `463eb9487565af0cdc873d621a6ca1ffce81c8d4`.
  Default branch remains `audit/edgesniffer-throughput-20260916`; old snapshot and
  destination history are preserved. Original repository remotes unchanged.
- Source research HEAD `0b9bfa0`, plus explicitly labelled pre-existing uncommitted
  application files. Expanded retained history includes workspace research/report
  artifacts, contracts, scripts, tools and small transaction fixtures, canonical
  evidence through RD-021, app/Rust source and the supplied independent audit.
  Reviewer guide distinguishes teacher-selected evidence, independent exploratory
  studies, engineering canaries and still-uncollected confirmation evidence.
- Verification:2440 inventoried files (~144.4MB), all staged export SHA256 hashes
  equal;123 current research tests pass/0skips from exported source with existing
  local SDK. Not a clean dependency-install, full app build or rerun of all historic
  studies. Entry-point links resolve. Remote HEAD matches publication commit and
  unauthenticated repository API reports public visibility.
- Gitleaks8.30.1 publisher archive checksum reverified.2162 initial findings:
  2158 public account/authentication address matches,3 feature-column lists,
  1 operational arming value redacted only in the export. Every flagged JSON
  key/accountKey field was checked as a32-byte public address; final scan has2161
  reviewed nonsecret matches. Additional private-key/credential URL patterns and
  known-local-env matching found none. This bounded new-snapshot review is not
  a guarantee against every secret format or a rescan of old destination history.
- Exclusions are explicit: environment/credentials/private keys, source Git history,
  dependencies/builds/active workflows, hidden staging/cache directories, nonallowed
  formats, files over8MiB and complete VPS raw captures. Full private conversations
  were not exported. Selected small transaction fixtures are included; the public
  snapshot is not a self-contained reproduction of every historic experiment.
  Manifest records190 scoped file/directory exclusions, source/export hashes and
  documentation relocation. DATA_AVAILABILITY.md describes missing raw inputs.
- Decision: use the latest README as external-review entry point; persisted logs
  and specific evidence, not an indiscriminate private-runtime dump, provide the
  research context. Publication is a dated snapshot, not automatic synchronization.
  No collector changed, no research result promoted, and no trading enabled.
- Next: A3h4c remains the dependency-ready research task: independently reconstruct
  holder-reward fees/supply semantics and qualify instruction-attributed writable
  touches on retained evidence, then verify full causal own-state/cost outcomes
  before requesting a larger prospective cohort. AUD-PUB02 verified.

## RD-021 — 2026-10-23 — Remove observer RPC blocking; narrow the remaining semantic gap

- After verifying A3h4a, continued directly with bounded nonblocking requests:
  two off-path workers, one checkpoint and one bootstrap in flight, no stale
  queue, cyclic batching at100accounts. Raw Rust receiver unchanged. RPC completion
  and later observer publication are retained separately; the latter governs
  admission. Delayed-worker/future-publication tests establish these invariants.
- Third predeclared75s engineering canary completed:12869tx (4242success,
  8627failed),7354native account writes,334mint bootstraps,127synchronized read
  sets,279totalRPC,0errors/recorded gaps. Median observer lag35.397ms,max119.301ms,
  versus425.147/1082.275ms in the earlier period. These are different market
  periods, not a controlled throughput benchmark. No original rules changed.
- Nine signal-period opportunities/54 intentions,2 tail-only seeds,6 original
  controls/3missing.12 policy abstentions and42 unresolved economics. Zero
  entry-observation quotes or complete matched context chains; the two persistence
  targets with complete observation context are not confirmed executable trades.
  No native2CQ event-user label in this short period; the previous15 observed
  successful events across2mints remain separate, not pooled into the signals.
- Inspecting actual seed-time raw states shows5/9 targets have holder-reward
  mode; none has Mayhem/cashback enabled. Three have raw mint supply different
  from curve nominal supply, including one holder-reward token. The union is7/9.
  These are explicit unsupported quote contexts, not evidence of unprofitability,
  and not justification to waive the guards. This is now a more valuable target
  than collecting a larger economically unevaluable sample.
- Inexpensive retained-data touch diagnostic (canary2, not a new experiment):
  all16 unique pending fee/global signatures succeeded with complete instruction
  identity.15 fee transactions (53 boundary occurrences/23boundaries) have static
  writable fee keys, no ALT, and exactly one get_fees_with_quote_mint invocation;
  its fee_config and enclosing Pump role are readonly in pinned IDLs. Pump variants
  are10buy,2buy_exact_quote_in_v2,2sell,1buy_exact_sol_in. No matching retained fee
  writes. One global router case (3occurrences) uses an ALT-writable global account
  and an unvalidated router, then an IDL-readonly Pump role. Preserve that unknown.
  Writable privileges are not mutation evidence; IDLs are not deployed-code proof.
  Do not promote quotes merely because the getter explanation is plausible.
- Verification:123 tests local/VPS, full raw replay reproduces actual scheduled
  IDs/controls and54 intentions. Two complete audits byte-identical, report SHA
  `5a99422225d7388ccb06ef36c3cf4edddf172222ec4725b105d2fff0d3dfa587`.
  Four preselected finalized blocks:192/192 expected signatures/metadata equal.
  [Latest report](research/edgesniffer/evidence/synchronized-context-20261023-v2.json),
  [verification](research/edgesniffer/evidence/synchronized-verification-20261023-v2.json).
  Full raw evidence remains in h4-sync-canary3; code h4-code5; audits
  h4-sync-native-audit3 and h4-sync-native-audit3-replay on the same VPS research root.
- Decision: A3h4b acquisition repair verified; carry semantic qualification
  explicitly to A3h4c. Next use retained evidence to reconstruct holder-reward
  fees/credits and supply semantics, plus instruction-attributed writable touches.
  Preserve all unsupported cases and frozen controls. Own-state intervention,
  complete hold transitions, costs and causal coverage still precede net returns.
  No larger prospective cohort or trading enabled; A3h4 overall remains partial.


## RD-020 — 2026-10-23 — Fresh capture exposes an acquisition bottleneck, not an edge

- A3h4 moved to fresh synchronized evidence. Original78/468 are unchanged. Reused
  the existing pinned Rust recorder, native decoder, frozen Study and integer
  quotes; no service restart or trading. Added canonical same-bank curve/mint/
  fee/global/Clock read sets off the Rust receive path, with explicit request and
  response clocks, minContextSlot and strict age/known-conflict checks.
- First predeclared75s canary produced5 signal-period seeds/30 intentions plus1
  drain-only seed. It revealed that signal-triggered context arrives too late
  for the original burst decision. First replay crashed on RPC write_version=None
  versus native integer ordering; repaired and regression-tested. Failed output
  h4-sync-audit1 remains preserved. No outcome or threshold optimization occurred.
- Second predeclared75s canary acquired context for all recently active native
  mints before signals and retained selected targets/controls through the tail:
  50s signal period,25s drain,22363tx (5243success/17120failed),8751native account
  writes,383/383 mint bootstraps,73 synchronized requests,168totalRPC/0errors.
  Eleven signal opportunities/66 size-arm intentions;8 drain-only seeds retained
  separately. Seven original control assignments/four missing. Native replay
  reproduces scheduled seed IDs, mint/control identities and period eligibility.
- Snapshot-only qualification rejects every burst decision: the triggering trade
  itself changes state after the read. A separately declared native-advancement
  observation specification applies available raw canonical writes with matching
  observed touches, common parent lineage, dead-ancestor/order/unknown guards.
  It never certifies global completeness or invents fills. All snapshot results
  remain preserved; strict age, rules, controls, sizes and deadlines unchanged.
- Latest advanced coverage: burst1/11 decision,2/11 entry,3/11 exit target
  observations qualify; persistence2/11 decision,2/11 entry,3/11 exit. Six size-
  specific entry-observation quotes, not trades. No burst target has all three
  boundaries; one persistence target does but its frozen confirmation is UNKNOWN.
  Zero complete matched target/control chains. All66 intentions retained:
  15 policy abstentions,51 unresolved economics; zero submitted orders/net returns.
- Separate teacher annotation observes15 successful2CQ event-user transactions
  across2 mints. These labels never feed independent decisions/control selection.
  The sample adds no profitable-strategy evidence and is not confirmation data.
- Actual timing identifies the next repair: median checkpointRPC389.134ms,
  median observer signal lag425.147ms,max1082.275ms. Serial bootstrap/checkpoint
  waits delay the off-path observer; the Rust capture is separate and unaffected.
  Some fee/mint writable touches have no corresponding retained native write;
  this is unresolved coverage/semantics, NOT proof of a mutation or dropped write.
- Verification:120 tests locally/VPS, including real16-transaction quote fixtures,
  mixed-source ordering, future inputs, missing metadata, gaps, dropped writes,
  startup identity, sibling forks/dead ancestors and pre-request newer touches.
  Independent read-only review reproduced and verified guard repairs. Two full
  sealed replays produce identical report SHA
  `c8cf501263eaa3cf9b1f372ed89c1b6df80d5600cb67a650715b3e62105d82df`
  and identical five row artifacts. Four preselected finalized blocks contain
  259/259 expected signatures with metadata equality; sampled, not global proof.
  [Observation report](research/edgesniffer/evidence/synchronized-context-20261023-v1.json),
  [transaction audit](research/edgesniffer/evidence/synchronized-transactions-20261023-v1.json).
  Optional legacy account join was not supplied to that transaction audit; its
  zero account-join counters do not mean the8751raw writes were absent.
- Decision: A3h4a bounded acquisition/observation slice verified; A3h4 itself
  remains active/PARTIAL. Do not expand the prospective cohort: the full economic
  pipeline has not passed. First make the observer nonblocking with bounded
  request queues and response/publication clocks, then qualify writable-touch
  continuity and own-state/fee/network-cost hold replay. Accept only after fixed
  bounded end-to-end evidence, preserving all missing/failed/non-executable cases.
  No TP/SL/entry tuning, no historical backfill, no trading or automatic GitHub push.


## RD-019 — 2026-10-23 — Prioritize fresh synchronized causal evidence

- User-directed change after A3h3: preserve original78 signals/468 intentions as
  historical evidence, not a target for further net-PnL reconstruction. New raw
  context cannot repair their original knowledge. Reuse verified arithmetic and
  capture rather than rerun completed historical experiments.
- A3h4 is active: distinguish actual decision-time inputs/coverage from later
  finalized reconciliation. Diagnose the bounded canary only to resolve concrete
  prospective acquisition/modeling gaps; then verify a bounded fresh end-to-end
  sample before expanding collection. No quiet-state carry without evidence.
- Frozen burst/persistence, full delay and0.1/0.5/1 SOL remain unchanged; missing
  controls and unknown/non-executable intentions remain in the denominator.
  Teacher-wallet behavior remains a separate contemporaneous research label,
  never an independent signal/universe selection input.
- A predetermined continuous prospective cohort is conditional on a verified
  capture-to-quote-to-outcome pipeline. Exploratory and untouched confirmation
  evidence must remain separate. This decision is not a completion or profit
  claim; task evidence and exact next action will be recorded after verification.


## RD-018 — 2026-10-23 — Quote arithmetic is supported narrowly; original causal coverage is zero

- A3h3 implemented an offline raw-to-context adapter and separate mathematical
  reconstruction/availability gates. Public Pump/fee IDLs pinned to639b87f;
  canonical global/fee PDA identities verified with the existing Solana SDK.
  All7808 normalized transactions reproduced from raw capture with identical
  identity, amount, status and receipt/decode fields. Raw layouts decode2280 curve
  writes,11 fee/11 global snapshots,537 clock rows and322 mint observations.
  No recorded sequence/clock/write-order gap;139 subscription requests still lack
  activation acknowledgments. This does NOT certify full write/fork coverage.
- Mathematical experiment:164 conditional arithmetic matches across6 legacy/v2
  buy/sell variants.16 transactions/6 mints independently reconstructed using a
  prior raw canonical account write, raw instruction arguments and independently
  decoded fee/mint context. Preserved finalized full-block brackets establish
  predecessor ordering/no intervening successful or unknown curve touch; actual
  curve SOL/token deltas and protocol/creator cash credits match integer quotes,
  fee rounding and all four post reserves. The tested event amounts are NOT used
  to reconstruct their own pre-state. One additional bracket has an intervening
  successful curve touch and remains unqualified, despite numerical agreement.
- Actual fixture curve amounts span0.004711794–0.609747088 SOL.0.1/0.5/1 SOL
  scenarios are492 nonlinear mathematical fixtures, NOT executed orders at those
  sizes. v3, partial fills, non-SOL, special fee modes, changed supply and
  unsupported extensions remain unresolved. Token2022 transfer-fee layout,
  older/newer epoch, ceiling/cap arithmetic have tests, not on-chain route proof.
  Of3364 successful captured transactions,3200 are outside this arithmetic
  reconstruction's admissible scope;4444 captured failures never become flow/fills.
- Reserve joins:2260/2267 successful events match canonical final account reserves.
  The other7 are intermediate events in3 multi-swap transactions; their last
  same-mint events match. Do not mistake intermediate states for final post-state
  or silently admit multi-swap transactions into the single-trade fixtures.
- Temporal result: original entry/exit boundaries04:51:53.147–05:10:33.703 UTC;
  new native observations06:26:00.523–06:27:00.122 UTC. ZERO overlap; new capture
  starts4526.820s after the latest old boundary. Original burst and persistence
  each have0/78 complete causal entry inputs and0/78 complete exit inputs. All468
  original intentions remain unresolved for quotes;0 submitted orders, not468
  failed trades. Original normalized reserves and8 prior decoded mint rows are
  retained, but they lack validated complete raw mint/fee/state continuity.
  Burst target age failures remain1 entry/45 exit; persistence37 entry/47 exit.
  Original42 missing controls and36 assignments unchanged; no retrospective fill.
- Separate60s canary coverage, not a replacement or prospective strategy cohort:
 6 fixed-rule signals/36 size-arm intentions;5 lack eligible original controls.
  Burst4/6 entries and1/6 exits have present, supported, fresh raw inputs;
  persistence3/6 entries and2/6 exits. No causal quotes certified because write,
  fork and dynamic-context continuity remain unresolved.2 signals involve an
  unsupported special fee mode. More capture alone is not yet the next step.
- Verification:92 tests locally/VPS, including16 retained real on-chain fixtures,
  SDK identities, integer/fee/impact/epoch tests, malformed layouts, unknown
  executions, future-data/prefix guards, raw-to-boundary integration and original
 468-row preservation. Two full-input reports byte-identical, SHA
  `c686e219dcb9d61ac9213e68384f15915df0de91c18c24e5f09c6a974dd6dd5e`.
  [Report](research/edgesniffer/evidence/quote-adapter-20261023-v1.json),
  [fixtures](research/edgesniffer/evidence/quote-onchain-fixtures-20261023-v1.jsonl),
  [method/scope](research/edgesniffer/quote-adapter-contract-v1.json).
  Full outputs remain in VPS `burst-persistence-v1-WmlstZu7/quote-adapter7` and
  `quote-adapter7-replay`. Initial reference recovery failed on transaction-version
  support; retained under `quote-fixture-reference1`; corrected version1 request
  recovered11 fixed selected blocks in `quote-fixture-reference2`. Later data is
  never backdated. Earlier development outputs preserved; collectors unchanged.
- Decision: A3h3 adapter/diagnostic is VERIFIED within the stated scope, not
  completion of the causal economics gate. Original positive flow remains a
  predictive research clue, not profit; this engineering experiment adds no net
  edge evidence. No trading or new capture enabled. A3h overall remains PARTIAL.
- Exact next A3h4: bounded native write/context continuity reconciliation for
  these6 canary signals and existing control, retaining frozen1s age and missing
  cases. Establish whether any older state can independently be shown unchanged;
  do not apply an alternative control/freshness rule to existing results. This
  unlocks a separately specified future causal quote cohort and then the fixed
  burst+1s versus persistence+3s,15s hold+1s exit,0.1/0.5/1 SOL own-state/cost
  experiment. Keep failed/non-executable and unresolved intentions distinct.

## RD-017 — 2026-10-23 — Publish a sanitized source snapshot for external audit

- At the user's request, published the bot source, ignored-workspace research
  source, canonical task/decision records, tests/contracts/small reports and
  supplied independent audit to the public repository
  [GhostID-ops/EdgeSniffer-Audit](https://github.com/GhostID-ops/EdgeSniffer-Audit).
- Existing default branch is `audit/edgesniffer-throughput-20260916`; new snapshot
  commit `a08aeeb32e614bf40a3f211a55b26f9e59e20192`, directory `audit-20261023`.
  Root README routes reviewers to the new snapshot. Older audit files/history
  preserved; no force push or original repository remote changes.
- Snapshot source research commit504db40 plus explicitly labeled pre-existing
  uncommitted application source. No assertion that every source file matches a
  deployed VPS binary. Original ignored workspace relocated to
  `workspace/edgesniffer_new` in the export; documentation links adjusted only
  in the export. Manifest records1351 source/evidence files with original and
  export hashes. Git attributes preserve file bytes, including evidence hashes.
- Verification: all1351 staged file hashes matched; exported Python suite76/76,
  zero skips using the existing local Solana SDK. Gitleaks8.30.1 binary verified
  against publisher checksum; five findings manually identified as two public
  authentication pubkeys and three feature-name lists. Additional known-local-env
  secret matching and private-key/credential-URL pattern checks found no matches.
  This is a bounded secret review, not a guarantee against every secret format.
- Excluded source Git history, environment files, wallet keys/credentials,
  installed dependencies, builds, active workflows and raw VPS datasets.
  Remote default branch commit verified after successful push. Publication
  changes neither research evidence nor strategy conclusions and enables no trading.
- Decision/next action: external reviewers should start at
  [current audit README](https://github.com/GhostID-ops/EdgeSniffer-Audit/tree/a08aeeb32e614bf40a3f211a55b26f9e59e20192/audit-20261023).
  This is a dated snapshot, not automatic sync; A3h3 remains the next research
  implementation task. Task AUD-PUB01 verified.

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
