# KEFI 13-arm program audit

## Current single-shot strategy discovery contract - read-only - 2026-10-19

- Artifact: `single-shot-strategy-discovery-contract-current-v1.json`; implementation: `scripts/buildKefiSingleShotStrategyContractV1.py`.
- The contract tests one hypothesis—venue-native early wallet impulse with liquidity/impact-capped size—without mixing arms or reusing ARM07 results as another arm's evidence.
- Its bounded discovery grid covers 0.1/0.5/1 SOL, 5/10/15/30/60/120/300 second exits, explicit dynamic exits and filters, exact venue costs, and 0/25/50 bps stress.
- Selection requires 50 actionable trades, positive net PnL under 50 bps stress, PF > 1, controls not beaten, both chronological halves positive, and bounded outlier concentration. Prospective validation is a separate fresh-cohort gate.
- No strategy was selected or frozen by this contract. No live/dust execution occurred.

## Current ARM07 single-shot discovery run - read-only - 2026-10-19

- Artifact: `single-shot-arm07-discovery-current-v1.json`; implementation: `scripts/runKefiSingleShotArm07DiscoveryV1.py`.
- The run uses every causal post-delay entry with a usable entry state: 175 historical and 46 later-cohort entries. It evaluates 72 surfaces per cohort: seven fixed horizons, five dynamic policies, three notionals, and 0/50 bps stress.
- No policy was selected. The engine hard-disables selection, because this is same-cohort discovery and matched controls are not available in this surface. A later-cohort fixed-300 result is positive only on 38 complete paths, below the 50-trade gate.
- This confirms the discovery engine is separating promising-looking conditional results from promotable evidence. It does not change ARM07's frozen policy or cohorts and does not establish profitability.

## Current ARM08 genuine-purchase admission gate - read-only - 2026-10-19

- Artifact: `arm08-admission-audit-current-v1.json`; implementation: `scripts/auditKefiArm08AdmissionV1.py`.
- The admission contract requires finalized success, exactly one CPI-invoked PumpSwap venue, exactly one canonical tracked-wallet BUY, positive wallet settlement, and distinct signature/event identity from ARM07. Top-level/direct PumpSwap is rejected before economics.
- The latest raw snapshot has 478 ARM08-labelled candidates. All 478 resolve to top-level/direct PumpSwap; zero are admitted to ARM08. This proves the current sample is ARM07-class evidence, not that ARM08 has negative economics.
- ARM08 remains independent and fail-closed under `ARM08_PUMPSWAP_CPI_FAIL_CLOSED_UNTIL_CPI_ATTRIBUTION_V1`. No ARM07 policy, result, or cohort was reused.
- The admission rule is now enforced in the canonical finalized-RPC materializer, not only in a report. Replaying the latest source produced `60` ARM07 observations and `0` ARM08 observations under the gated materializer.
- No cohort was created, no collector was restarted, no frozen policy was modified, and no execution was enabled.

## Current venue coverage priority audit - read-only - 2026-10-19

- Artifact: `arm-coverage-priority-audit-current-v1.json`; implementation: `scripts/auditKefi13ArmCoveragePriorityV1.py`. No collector, frozen policy, cohort, or execution state was changed.
- The arms are fingerprint/venue/evaluator partitions. Zero admitted observations are an unmeasured-contract state, not a negative PnL result.
- ARM08 is the top coverage priority: it has 95 raw candidate references on the PumpSwap tape but zero admitted economics because its CPI-linked route adapter is incomplete.
- ARM01/02/03 each have five raw references and exact Pump modules but zero admitted entries in the current boundary. ARM09/10/11 each have two router references and unresolved underlying venue execution. ARM12 has two references and a blocked exact adapter.
- ARM04/05/06 have no raw references in this window and partial adapters. ARM13 has no raw reference and remains deliberately fail-closed. ARM07 remains the benchmark and is not modified.
- Coverage priority is ARM08 -> ARM01/02/03 -> ARM09/10/11 -> ARM12 -> ARM04/05/06 -> ARM13. This is not an alpha ranking.
- Required arm gate: canonical venue resolution, exact decoder, causal tape, realistic economics, then fresh frozen prospective validation. No arm may be promoted from module presence or raw references alone.

## Current ARM07 notional surface - read-only - 2026-10-19

- Artifact: `arm07-notional-surface-current-v1.json`; implementation: `scripts/studyKefiArm07NotionalSurfaceV1.py`. This is a separate offline study and leaves the frozen policy, both cohorts, and receive-only collectors unchanged.
- It models the causal one-second observed entry delay, exact PumpSwap LP/protocol/creator fees, constant-product AMM price impact, effective quote-reserve utilization, observed fixed-30-second exit marks, missing entry/exit evidence, and 0/25/50 bps token-output stress. Network/priority fees and submitted-transaction fill uncertainty remain excluded.
- Later cohort fixed-30 at 0 bps / 50 bps: `0.1 SOL` `-0.146391916 / -0.168134152 SOL`, `0.5 SOL` `-0.935844649 / -1.043035704 SOL`, `1.0 SOL` `-2.368044480 / -2.578758120 SOL`; PF at 0 bps is `0.6291`, `0.5541`, and `0.4749`.
- Mean entry AMM impact is `11.87`, `59.34`, and `118.67 bps` at those sizes; mean notional/effective-liquidity utilization is `12.01`, `60.07`, and `120.13 bps`; mean two-sided pool fees are `0.002360639`, `0.011746825`, and `0.023356488 SOL`.
- Conclusion: fixed-size scaling worsens realistic net economics in both preserved cohorts. This supports researching a liquidity/impact-capped size policy, but does not select one. Fingerprint strength, fee burden, recent flow, and portfolio risk should be inputs to a later offline sizing hypothesis. No runtime sizing change was made.
- Dynamic exits are still hypotheses. Later-cohort TP +100 is positive only on `31` complete paths with `14` unavailable/failed paths; historical TP +100 is positive only on `53` with `64` unavailable/failed paths. The conditional results do not establish a profitable policy, and no policy was promoted.
- Verification: focused tests `17 passed`; supported suite `309 passed`. No live/dust execution or transaction submission occurred.

## Current causal quote/live-parity study - read-only - 2026-10-19

- The separate causal artifact is `arm07-causal-quote-parity-study-current-v1.json`. It waits for the first usable observed PumpSwap state at or after target plus one second, quotes 0.1 SOL against that state, uses exact observed LP/protocol/creator fees and effective reserves, exits only at observed same-pool marks, and records missing entry/exit evidence.
- Strict signal signature joins: historical `5`, later cohort `4`. Coverage-qualified timestamp/pool causal fills: historical `117`, later cohort `45`. The latter tier is explicitly not a live-fill proof when the signal signature is absent.
- Causal fixed-30 economics are negative in both windows: historical `-0.451340889 SOL`, PF `0.6492`; later `-0.146391916 SOL`, PF `0.6291`. Mean observed entry delays are `542.38s` and `78.40s`; the original event-state evaluator did not expose this latency.
- The study models exact pool fees and AMM notional impact. Network fees, priority fees, transaction inclusion, and a measured external slippage distribution are unavailable; 25/50 bps are stress cases only. The old tape has 1,538 rows without the required causal metadata/layout, and 58 causal candidates lack a usable 30-second exit mark.
- Later-cohort conditional surfaces show positive TP totals under 50 bps stress (`+0.4916 SOL` at +100 bps on 31 complete paths; `+0.4892 SOL` at +200 bps on 26), but they exclude 14 and 19 unavailable/failed paths respectively. Protection is negative (`-0.0930 SOL` on 15 complete paths with 30 unavailable/failed), and partial take-profit remains negative. Historical TP totals are positive only on 53 complete paths with 64 unavailable/failed paths, so these results are not out-of-sample or complete-cohort validation.
- This study is not a validation or promotion event. No new cohort, policy change, collector restart, live execution, dust execution, or transaction submission occurred.
- Verification: causal-study tests `2 passed`; supported script suite `306 passed`. No live or dust execution test was run.

## Current ARM07 cohort comparison - read-only - 2026-10-19

- The preserved positive cohort `kefi-13-arm-multivenue-prospective-20261019T025634Z` and the later registry-boundary cohort `kefi-13-arm-prospective-20261019T041500Z` both replay exactly under the current ARM07 decoder/evaluator. The comparison artifact is `arm07-cohort-comparison-current-v1.json`.
- The policy hash is identical in both cohorts: `b981d869f652294ecdf2c5e3fda1e07ba8830cbe57a9d6361643f2f85c03ffc0`. ARM07's policy ID, 0.1 SOL notional, canonical PumpSwap mechanics, fixed 30-second minimum exit horizon, and no-Jupiter posture are unchanged. Only the registry hash changed (`c1d04e...` to `ebf49f...`) because ARM13's fail-closed resolver changed the registry binding.
- Historical: 161 actionable, `+1.617275190 SOL`, PF `2.2384`, 59.63% wins; new cohort: 46 actionable, `-0.211736966 SOL`, PF `0.5731`, 54.35% wins. New controls were also negative (`-0.154496202 SOL`, PF `0.0630`, 9 actionable), so the result is not explained by the KEFI selection signal alone.
- The new window had lower mean pre-entry effective liquidity (`124.47` versus `135.92 SOL`), slightly higher entry fee burden (`118.68` versus `117.48 bps`), negative prior 30-second same-pool net flow (`-0.0948` versus `+0.0216 SOL`), and negative first future mark (`-0.00585` versus `+0.01056 SOL`). This supports a hostile market/selection period as the primary explanation, with only a small sample of controls.
- Fixed exit still matters for a subset: 13/21 new fixed-exit losers had positive MFE and 11/21 reached at least `+0.01 SOL` before the fixed exit. This supports testing dynamic selling and partial take-profit rules separately, but MFE is an offline upper-bound diagnostic and does not authorize changing the frozen policy.
- Methodology caveat: the current fresh-economics script quotes from the candidate event state rather than separately simulating the policy's named plus-one-second delayed entry. Both cohorts are still directly comparable under the same evaluator, but neither result isolates that one-second delay. The evaluator also exits at the first observed mark at or after 30 seconds, not necessarily exactly at 30.00 seconds.
- No service was restarted, no cohort was reset, no frozen strategy was modified, no trade was enabled, and no transaction was submitted.
- Verification: comparison integration status `PASS_CURRENT_REPLAY`; focused tests `14 passed`; supported script suite `304 passed`. A repository-wide pytest invocation still has six unrelated legacy collection errors outside this audit surface.

## Current superseding audit - 2026-10-19T04:49Z

- Historical ARM07 replay audit is `PASS_CURRENT_EVALUATOR_REPLAY`: the preserved `kefi-13-arm-multivenue-prospective-20261019T025634Z` partition replays exactly under the current decoder/evaluator bundle at 177 observations, 161 actionable outcomes, `+1.617275190 SOL`, PF `2.2384`, and 27 actionable matched controls.
- The historical registry hash is `c1d04e11171ce64cfc9e0b3262219b40a494ed3e6e001a5362a023683e93bab8`; the current registry hash is `ebf49fb316527a55f02343dd9370f70662b20ed430c4e58da05cc8fadd4a4a2c`. The frozen policy hash is unchanged at `b981d869f652294ecdf2c5e3fda1e07ba8830cbe57a9d6361643f2f85c03ffc0`.
- The receive-only VPS source is active/enabled. The immutable remote snapshot captured 494 raw lines at `2026-10-19T04:42:46Z`; source audit and prospective-source gate both pass with zero source-state errors and clean transport.
- Strict materialization of the snapshot produced 59 ARM07 direct observations, zero observations for the other 12 arms, and 375 quarantined rows. The fresh-cohort audit passed with 59 accepted rows, zero errors, 48 equivalent overlaps dropped, and 11 newly appended rows.
- The latest ARM07 fresh report has 58 actionable outcomes, `-0.238957814 SOL`, PF `0.6346`, 56.90% win rate, max drawdown `-0.380306838 SOL`, and 19 actionable controls. Controls did not outperform.
- Fresh-only ranking is `NO_WINNER_DATA_OR_GATE_PASS`. The latest versioned final review is `DO_NOT_PROMOTE`; it records ARM07 as `KILL_RESEARCH_ONLY`, twelve arms as deferred/no-data or adapter-gated, no global winner, and no live/dust authorization. This research-only kill preserves all raw data, reports, and frozen policy artifacts.
- Requirement-level completion remains `7/10`, `INCOMPLETE_GLOBAL_COMPARISON`: registry, bindings, discovery, frozen policies, receive-only collection, fresh audit, and structural ranking pass; comparable executable economics for all 13 arms and a global winner decision remain unproven.
- No cohort was reset, no frozen policy was modified, no service was restarted, and no transaction was submitted.

## Latest receive-only checkpoint - 2026-10-19T04:51Z

- The active source advanced to 498 raw rows. The latest snapshot passed source audit with 131 post-boundary rows, 367 pre-boundary rows ignored, and 131 unique signatures.
- Materialization and fresh ingest passed with 60 ARM07 observations, one newly appended row, 59 equivalent overlaps dropped, zero cross-arm admitted observations, and zero fresh-cohort audit errors.
- ARM07's latest 60-actionable report is negative (`-0.227779830 SOL`, PF `0.6517`) and controls do not outperform. Fresh-only ranking has no winner; final review is `DO_NOT_PROMOTE` and records `ARM07_PUMPSWAP_DIRECT` as `KILL_RESEARCH_ONLY`.
- Candidate coverage now includes ARM09-ARM12 references. ARM09 is rejected as a tracked-wallet non-acquisition, while the Meteora route has no canonical `Swap2Evt` event in the captured transaction and remains fail-closed. ARM04, ARM05, ARM06, and ARM13 have no admitted evidence in this window.
- The completion audit remains `7/10`, `INCOMPLETE_GLOBAL_COMPARISON`; no global promotion is authorized.

## Historical audit checkpoint — 2026-10-19T03:45Z (superseded by the current audit below)

The offline registry/decoder/policy build is PASS. The new prospective 13-arm comparison is still open. The previous 53-row ARM07 report is a separate preserved cohort and is not combined with the new cohort.

Verified in the new cohort:

- `edgesniffer-kefi-13arm-finalized-rpc-source-v1.service` is active on the VPS and has produced 155 unique finalized, wallet-filtered raw rows with zero source-state errors.
- `edgesniffer-kefi-13arm-pumpswap-tape-v1.service` is active as a separate receive-only same-pool tape/control source. Its v2 rows contain exact LP/protocol/creator fee basis fields; its decoder records explicit error samples and fails closed on unsupported event joins.
- The current PumpSwap decoder now accepts the published `buy_v2`/`sell_v2` instruction discriminators and the published `creator_fee_unclaimed` event extension. Local tests cover the old extension, the new extension, v2 instructions, JSON-parsed account metas, and current fresh rows.
- Finalized-RPC raw audit passed: no duplicate signatures, no transaction-hash mismatch, no pre-boundary rows, no non-finalized rows, and no economics fields.
- Canonical materialization and strict ingest passed for 77 ARM07 direct observations; four Pump-only fee-distribution rows were quarantined after exact decoder inspection proved they were not trades. The one Raydium CLMM transaction was exact-decoded as a sell/no-wallet-acquisition and was not admitted. Fresh-cohort audit passed with 77 rows and zero cross-arm contamination.
- The new ARM07 report has 68 actionable rows, 13 actionable matched controls, +0.332769116 SOL paper PnL, PF 1.4169, 58.82% wins, MFE +1.788121317 SOL, MAE -0.536642757 SOL, and max drawdown -0.355336690 SOL. It passes the frozen paper gates.
- Fresh-only ranking selects ARM07 among the one evaluated arm. Final review records `PROMOTE_ARM07_TO_QUOTE_LIVE_PARITY_PAPER`, with no losing-arm kills and no live/dust authorization. `global_winner_claim=false` because 12 arms lack comparable fresh economics.
- The first isolated parity-tape attempt (v3) failed its new durable-order audit because multi-event pool filtering created a sequence gap/collision; that artifact is preserved and explicitly rejected.
- The corrected v5 parity tape passed: latest sealed pull 236 rows, zero direct-tape decoder errors, zero schema errors, complete deterministic source/continuity/sequence/raw-instruction fields, and contiguous durable sequence. Mixed Pump-curve/router transactions are separately quarantined rather than counted as direct decoder failures. The quote/live-parity readiness audit is `READY_FOR_QUOTE_LIVE_PARITY_PAPER`.
- This readiness result is still paper-only. `live_trading_authorized=false` and `dust_execution_authorized=false`; it does not promote ARM07 to execution.
- The latest finalized source audit passes at 318 unique rows with zero source errors. Strict materialization admits 157 ARM07 direct PumpSwap rows and zero rows for the other 12 arms; eight Pump-only non-trade rows are quarantined. Strict ingest appended six fresh rows after dropping 151 equivalent overlaps. The complete exact-tape ARM07 recomputation has 139 actionable outcomes, +0.487115145 SOL, PF 1.3181, 56.83% wins, and 24 actionable controls out of 24 with controls outperformed.
- The consolidated program-status audit is `PASS_STRUCTURAL_AUDIT_GLOBAL_COMPARISON_INCOMPLETE`: it reconciles all 13 arms, records ARM07 as the only promotion candidate, records 12 explicit `DEFER_NO_DATA` decisions, records zero losing-arm kills, and rejects any global-winner claim while the comparison is incomplete.
- The finalized materializer now invokes the exact LaunchLab and protocol-native CPMM/DBC adapters when those real venue programs are proven, and invokes the exact quote-native Pump V2 adapter only for a unique non-SOL tracked-wallet trade. Current raw evidence did not produce an admitted row for any of those arms; unchanged results are therefore evidence, not a code-path omission.
- The post-change supported test sweep passes 293 tests and Python compilation.
- Focused final verification passed 36 tests and Python bytecode compilation. The supported repository test sweep passed 285 tests. Three legacy tests remain excluded because they declare an unavailable `root` fixture; the separate missing-`C:\tmp` and Windows-`fcntl` collection blockers remain documented.
- PumpSwap tape audit passed schema and uniqueness checks for 1,226 rows, recorded 46 exact-decoder join errors, and correctly set `economics_ready=false` until those unsupported transactions are resolved.
- The Raydium CLMM adapter now accepts normalized string account metas as well as compiled account indices. The focused CLMM/materializer regression suite passed 10/10; no unproven router row is admitted as an entry.
- The evaluator hash guard was updated to the audited SHA-256 of the intentional Pump decoder extension; the frozen-hash regression and all newly affected tests pass.

This is a collection/evaluator progress checkpoint, not a completed global strategy comparison: only ARM07 has fresh canonical observations, and the other 12 arms remain deferred or fail-closed until their real-venue evidence and exact evaluators produce comparable prospective samples.

Audit status: PASS for the offline program build and ARM07 prospective paper gate; NOT READY for live or dust execution.

## Current superseding audit — 2026-10-19T04:33Z

- The current source gate is `PASS_PROSPECTIVE_SOURCE_GATE`: raw source, exact fail-closed materialization, and fresh-cohort audit all pass with clean transport. The new registry-hash cohort records 466 cumulative raw rows, a 99-row post-boundary window, and 44 accepted fresh observations.
- The current cumulative PumpSwap tape merge is `PASS_CUMULATIVE_TAPE_MERGE`: 2,763 unique rows from six source segments, 273 identical overlaps removed, and zero conflicting duplicates. Legacy rows without parity fields are counted separately and are not presented as current quote/live parity evidence.
- The new ARM07 prospective economics window has 41 actionable 30-second outcomes out of 44 observations: -0.210504438 SOL, PF 0.5583, 53.66% win rate, and controls did not outperform. The evaluator correctly reports `SAMPLE_BELOW_GATE`; this is not enough evidence to kill or promote ARM07.
- Final review is `DO_NOT_PROMOTE` for the new cohort because the frozen paper gate is not met. It records `global_winner_claim=false`, no losing-arm kills, twelve `DEFER_NO_DATA` arms, and live/dust authorization false. The previous positive cumulative ARM07 checkpoint remains historical and is not reused as fresh validation for this new registry hash.
- Final-review gating was tightened and tested: a blocked/stale source artifact now causes `DO_NOT_PROMOTE`; a null ranking winner is treated as an explicit no-winner state; the current pass uses the cohort-specific prospective-source gate.
- Focused verification passes the new ARM13 resolver, source-windowing, collection, and registry tests. The new registry audit passes; discovery remains outcome-blind for all 13 arms; exact module coverage is now 13/13, with runtime gates still open.
- Verification passes 303 supported Python tests and Python bytecode compilation; four documented environment/fixture tests remain excluded.
- The requirement-level completion audit remains 7/10 and reports `INCOMPLETE_GLOBAL_COMPARISON`: registry, venue/component binding, outcome-blind discovery, frozen policies, receive-only collection, fresh cohort, and structural ranking pass; executable paper-gate passage, global comparison, and global kill/promote decisions remain incomplete.

## Verified offline program build

- Registry contains exactly 13 unique arm IDs.
- Every arm has a real venue class, decoder contract, evaluator contract, discovery entry, frozen-policy entry, prospective partition, and promotion gate.
- Exact-labeled component paths exist locally.
- Discovery analysis emitted 13 rows and read no outcome/PnL fields.
- Frozen-policy file contains exactly one policy per arm.
- All 13 registry frozen-policy slots resolve to those immutable policy IDs: four research contracts and nine fail-closed contracts.
- Prospective manifest contains 13 distinct partitions and forbids PnL reads, submissions, policy changes, and cross-arm pooling.
- Strict prospective ingest was unit-tested for accepted rows, duplicate IDs, outcome-field contamination, cross-arm metadata, and pre-cohort timestamps; rejected batches append zero lines.
- Prospective ranking is fresh-only and exact-evaluator-only.
- Current ranking result: one fresh ARM07 exact economics report exists and passes the frozen paper gate at 68 actionable outcomes and 13 actionable matched controls. It is the evaluated-arm winner; a global 13-arm winner is not claimed because the other 12 arms have no comparable fresh cohort.
- Final review result: `PROMOTE_ARM07_TO_QUOTE_LIVE_PARITY_PAPER`. This is a research-stage promotion only; live trading and dust execution are not authorized.
- Existing relevant tests passed: multi-archetype research, route evidence, LaunchLab, PumpSwap, Raydium CLMM/CPMM, Meteora DBC, registry, discovery, collection preparation, strict ingest, ranking, final review, Orca Whirlpool, and Meteora DLMM; 44 tests passed.
- Exact adapter import audit passed for 12/13 arms. Orca Whirlpool and Meteora DLMM now have strict canonical event decoder/evaluator modules; they still require router resolution and runtime fixtures. This is module coverage only; runtime fixture parity is 0/13 and promotion-ready economics is 0/13.
- VPS checks confirmed SSH access, an active full-meta source, a healthy freshness watchdog, fresh outcome-blind shared windows, and no transaction submission. The promotion destination was found at 100% NFS usage; the receive-only service was restarted with a local promotion root, after which complete raw windows appeared with zero promotion failures. Historical NFS data was not deleted.
- Thirteen overlap-aware post-freeze batches were materialized using the VPS exact decoder. Finalized transaction program-log evidence resolved the admitted tracked PumpSwap events to the direct PumpSwap arm; overlap-aware deduplication admitted 53 unique observations, dropped 26 equivalent repeats, found zero conflicts, and the fresh-cohort audit passed.

## Deliberately not claimed for the current cohort

- No arm is promoted to live execution from this build. ARM07's current fresh report is positive on 68 actionable rows (`+0.332769116 SOL`, PF `1.4169`, 58.82% wins) with 13 actionable matched controls and controls weaker in aggregate. It passes the paper gate for quote/live-parity paper review only.
- The PumpSwap 30-second result is not treated as a universal policy or fresh validation.
- No no-data or blocked arm is called a losing strategy; those 12 arms remain deferred because they lack a comparable fresh executable cohort. No arm met the evidence threshold for a research-only kill.
- No trading service was started or restarted, no transaction was submitted, and no live execution test was performed. The receive-only full-meta capture service was restarted solely to repair its full-disk promotion destination.
- Final verification passed 44 relevant unit tests and Python compilation. The receive-only source remains active on the VPS; subsequent windows are counted only after independent materialization, venue resolution, overlap deduplication, and strict ingest. The source compatibility audit remains fail-closed for its cumulative `framesMetaIncomplete=1` transport counter, even though the admitted sealed windows passed the cohort audit.

## Current decision

`DO_NOT_PROMOTE` for the current registry-hash cohort. The 13-arm registry and frozen-policy contracts are built and audited, but only ARM07 has fresh canonical economics and that sample is below gate with negative early marks. ARM01–ARM06 and ARM08–ARM13 have no comparable fresh economics; they remain separate named deferred/fail-closed arms. No arm is killed as a loser, no global winner is claimed, and no live or dust execution is authorized.
