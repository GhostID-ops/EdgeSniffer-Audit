# 2CQ independent fingerprint lane

Updated: 2026-10-22 19:07 UTC

This lane is receive-only and paper-only. It does not submit, build, or arm
trading transactions. The historical 2CQ evidence and the existing cohorts
remain preserved and are not rewritten.

## Gates

- [x] Derive an outcome-blind fingerprint from preserved 2CQ evidence.
  - Source: `canonical-route-events-current-20261020.jsonl`
  - Source SHA-256: `3259cbb6514a7f9d43abd7af13b3b3bbf026d5283031704c0cebba027cf621e4`
  - Artifact: `2cq-fingerprint-independent-v1.json`
  - Fingerprint SHA-256: `7a3f29e6614eb77fe867373750b13e7eac06dd092cf55ddaa39750a02634d6aa`
  - Evidence used: 4,583 buys; 3,223 SOL/WSOL-economic buys; dominant route share 96.31%.
  - Contract: top-level Pump curve, SOL/WSOL quote, native early curve state at or below 46.171004438 virtual SOL, one signal per mint per 300 seconds.
  - `outcome_blind=true`, `selection_performed=false`, `policy_frozen=false`.

- [x] Repair and test native detection for the current Pump exact-quote V3 layout.
  - Layout assertion: 17 accounts; mint index 1; quote mint index 2; curve index 5; wallet index 8; Pump program index 16.
  - V3 discriminator: `e1f7501ed5b38488`.
  - VPS binary: `pumpfun-sidecar-teacher-source-jito-watchdog-v3-bf4a1415f112b295`.
  - Binary SHA-256: `bf4a1415f112b295edd61354f75f01b1910a478fb39014e3bfa5efeae7b35bce`.
  - VPS test result: targeted V3 parser test passed; full `pumpfun-sidecar` test target passed, 179 passed / 0 failed.
  - Live receive-only verification: current 9qz7 wallet rows appeared as `source=rust-sidecar`; source health was `ADVANCING`, dropped events 0, writer errors 0.

- [x] Detect the fingerprint independently in the market-wide native replay.
  - Admission does not use the teacher-wallet label.
  - Input: `native-boundary-replay-v1.jsonl`.
  - Causal boundary: signal receive clock plus 1,000 ms.
  - Missing native state or explicit fee fields is rejected or marked incomplete; it is not imputed.
  - Source-scope audit: 6,116 native rows across 1,015 mints and many wallets; the target wallet contributed 0 rows to this independent replay, confirming this is not a teacher-conditioned input stream.

- [x] Start a separate prospective cohort at the decoder-repair cutoff.
  - Cohort ID: `2cq-independent-fingerprint-v1 / 20261022T164227Z`.
  - Fixed cutoff: `1792687347000` (2026-10-22 16:42:27 UTC).
  - Window artifact: `prospective-market-windows-20261022T164227Z.jsonl`.
  - Strategy artifact: `prospective-strategy-report-20261022T164227Z.json`.
  - Services:
    - `edgesniffer-2cq-independent-fingerprint-market-windows-20261022T164227Z.service`
    - `edgesniffer-2cq-independent-fingerprint-strategy-lab-20261022T164227Z.service`
  - Current snapshot: 37 completed windows, 36 unique mints, 37,179 populated strategy rows. The strict 1 SOL / fixed 5 s surface has 9 attempts, 6 actionable trades, 2 wins, 3 missed/failed trades, and `-0.456272304 SOL`; it remains too small and exploratory for promotion.
  - Current output is exploratory only; `policy_frozen=false`, `promotion_blocked=true`.

- [x] Separate immature horizons from genuine missing/failed exits and add a bounded carry-forward hypothesis.
  - Every current window now carries `observation_as_of_ts_ms`.
  - Horizons that have not elapsed are reported as `IMMATURE_WINDOW`, not as losses or missed exits.
  - Added separate `CARRY_5S` through `CARRY_300S` research rules. These use the last verified native state only after the market-wide tape reaches the deadline and never replace strict fixed-exit results.
  - Regression coverage: 28 tests passed.
  - Current strict 1 SOL flow/price results remain unchanged: fixed 5 s `-0.456272304 SOL / PF 0.8344`; fixed 15–60 s `+2.192002151 SOL` from 1 actionable trade each, with 8 genuine missing exit marks each.
  - Current carry-forward 1 SOL flow/price results: 15/30/60 s each 9 actionable, 5 wins, `+1.031056919 SOL / PF 1.6705`; 120 s `+1.048859 SOL / PF 1.6821`; 300 s `+0.874542 SOL / PF 1.5688` with one immature row. This is a hypothesis only, not validation or a frozen rule.
  - VPS-tested read-only; no trading or execution path changed.

- [ ] Accumulate a sufficiently large fresh cohort and contemporaneous controls.
  - Do not select a winner from the current small cohort.
  - Controls must be same-period, route-compatible, non-overlapping, and built without future outcomes.

- [x] Correct and re-audit the economics fee plumbing before treating PnL as final.
  - The materializer now copies the causal entry row's `fee_basis_points`, `protocol_fee_basis_points`, `creator_fee_basis_points`, and entry quote amount onto each window.
  - The evaluator now interprets the native fee schedule as protocol 95 + creator 30 = total 125 bps. If only total plus creator is present, protocol is calculated as the remainder; the creator fee is not double-counted.
  - Regression coverage: 26 tests passed at the fee-fix checkpoint; the full combined suite later passed 28 tests after the maturity/carry additions.
  - Local script SHA-256: `materialize2CQFingerprintMarketWindowsV1.py` = `7355b34d98f5c9a34771937c1975f527165e32e0571834ab596a96ac8a2fe3e9`; fee-fix lab version = `300501194ba140a9ce6a77ac7f3d3ab3b007618f04fb4bfc13a650be619dc8ec`; current maturity/carry lab version = `4c43625066020d4ae598b20bf83b097f9e12dc492a470966bc87d5c9aa6b37d1`.
  - Pre-fix outputs were preserved under `pre-fee-fix-20261022T174700Z/`.
  - Controlled same-sample comparison (27 windows, same input): strict `FLOW_005_AND_PRICE_10BPS`, fixed 5 s, stress 0, changed from `+1.106873633 SOL / PF 1.8997` at 1 SOL to `+1.122435584 SOL / PF 1.9141`; at 0.5 SOL, `+0.612840941` to `+0.620802998`; at 0.1 SOL, `+0.132506397` to `+0.134129189`.
  - The broad `ALL_EXACT_ROUTE`, fixed 5 s, 1 SOL, same sample changed from `+0.206195518 SOL / PF 1.0542` to `+0.276910881 SOL / PF 1.0735`. The current live-refresh report contains 28 windows and is separately preserved as `fee-corrected-current-20261022T174700Z.json`; it is exploratory, not a replacement sealed cohort.
  - VPS verification: both read-only services restarted with the patched scripts, four required source/replay/materializer/lab services were active, no error/traceback lines appeared in the recent materializer/lab journals, and the current 28 windows had 0 incomplete economics rows, 0 missing receive-lineage rows, and fee split `{125,95,30}`.
  - This was VPS-tested in read-only paper mode; it was not live-trading tested and no execution was enabled.

- [ ] Pre-register a candidate rule, then validate it once on a later untouched prospective segment.
  - Test sizes: 0.1, 0.5, and 1.0 SOL.
  - Test exits: fixed horizons plus pre-registered dynamic/partial/profit-protection hypotheses.
  - Economics: one-second delay, exact native state, fees, impact, slippage, and failed/missed trades.
  - No freeze or promotion before the fresh-cohort gate passes.

- [x] Build the latency-bounded native checkpoint study without changing existing PnL artifacts.
  - Contract: `research/native-latency-bounded-study-v1/contract.json`.
  - The study uses only the checkpoint whose `horizon_seconds` equals the tested 15/30/60-second exit. It reports `<=1s`, `<=5s`, `<=15s`, `<=30s`, `<=60s`, `<=120s`, and unbounded checkpoint-delay buckets.
  - It tests the three pre-registered discovery arms `FLOW_005_AND_PRICE_10BPS`, `FLOW_AND_PRICE_CONTINUATION`, and `RESERVE_GE_2_SOL` at 0.1/0.5/1.0 SOL and 0/25/50 bps stress. No winner is selected and no policy is frozen.
  - Current cohort snapshot: 61 windows, 25,137 outcome rows, 567 surfaces. Service: `edgesniffer-2cq-independent-native-latency-bounded-study-20261022T164227Z.service`.
  - Diagnostic result: for `FLOW_AND_PRICE_CONTINUATION`, 1 SOL / 60 s / stress 0, the `<=1s` bucket is `+1.2101 SOL / PF 1.465` but only 13/60 rows are actionable and 47 are excluded for late checkpoints; the unbounded result is `-10.1378 SOL / PF 0.273`. This is not validation: post-hoc checkpoint speed is not an entry-time feature, so fast-bucket PnL cannot be promoted as a strategy.
  - The more selective `FLOW_005_AND_PRICE_10BPS`, 1 SOL / 60 s / stress 0, is `+0.2576 SOL / PF 1.150` in the `<=1s` bucket with only 3/13 actionable rows, and `-4.3320 SOL / PF 0.313` unbounded. This remains a coverage/execution hypothesis only.
  - Next gate: pre-register an actual exit-time timeout/fallback behavior that is observable prospectively, then test it on a fresh cohort. Do not filter trades after the fact by future checkpoint speed.
  - Script SHA-256: evaluator `4A21C598FAED9D763E3BAE7EA647B1A515E8DC29C05BD32F2753649ED2563B41`; refresh service `091838577179F6C1F6FC3CD37D14CC6C43CD66D8C7BD6253252817E277488A7D`; contract `F117DA4EE7329FCBDD377D0268758124B32EA2BEFA85EA39E18B4D30AF201428`.

- [x] Start a separate fresh prospective validation cohort under the latency contract.
  - Cohort ID: `2cq-native-latency-validation-v1 / 20261022T211514Z`.
  - Fixed cutoff: `1792703669063` (2026-10-22 21:14:29 UTC), after the prior cohort watermark. It does not backfill the prior 61 windows.
  - Services: fresh market-window materializer, native checkpoint materializer, checkpoint-window joiner, checkpoint strategy lab, and latency-bounded study.
  - Current startup state: 1 fingerprint signal matched, 0 completed windows yet; the first signal is still waiting for its post-entry market window to mature. The normalized source and native replay advanced together, account seeds advanced, and all five fresh services were active with no execution enabled.
  - Validation remains blocked until this cohort produces enough pre-registered observations and controls; no policy is frozen or promoted.

- [x] Add a separate native reserve checkpoint lane for missing 5/10/15/30/60/120/300-second marks.
  - Native source: `native-curve-account-seeds-live-v1.jsonl`, containing the native account receive timestamp, virtual reserves, real reserves, slot, write version, and account transaction signature.
  - Checkpoint semantics: first complete native account update at or after the fixed entry-plus-horizon deadline. The actual checkpoint receive timestamp and delay after the deadline are preserved.
  - This is not a strict transaction-event mark and not carry-forward: every row has `strict_mark=false`, `carry_forward=false`, and `mark_semantics=first_complete_native_account_update_at_or_after_deadline`.
  - Separate artifacts: `native-reserve-horizon-checkpoints-20261022T164227Z.jsonl`, `prospective-market-windows-native-checkpoints-20261022T164227Z.jsonl`, and `native-checkpoint-strategy-report-20261022T164227Z.json`.
  - Coverage at 2026-10-22 19:07 UTC: 41/41 windows joined; 285 native checkpoint rows; 41/41 marks at 5, 10, 15, 30, 60, and 120 seconds; 39/41 at 300 seconds. Two 300-second account checkpoints remain pending because no qualifying native account update has arrived yet.
  - The original `marks` and carry-forward surfaces are unchanged. The new economics report uses only `native_checkpoint_marks`, remains exploratory, has `policy_frozen=false`, and has `promotion_blocked=true`.
  - VPS status: all three new services active; no execution or live trading enabled. The focused native/independent regression set passed 60 tests; the checkpoint-specific subset was 20/20. The repository-wide discovery attempt still has two unrelated Windows-only collection failures (`C:\\tmp\\twoCqAutonomousWhenV2.py` missing and Python `fcntl` unavailable), so those were not counted as passes.
  - Script SHA-256: checkpoint materializer `B636E7F414F21DFF63AF9B9AFC4583BEDD2DF5AADA7B874C71384218769119CF`; window joiner `B9E00C0A6D0418F4EACE6F0618A72C4D7229892066278D3FB45801E6191A486C`; checkpoint lab refresh `EF2057A317FE449E0C5971121F3CB8F0C68A2CDB4B1BAF2630F187968F34D7D8`; lab mark-field extension `36CB1C1892E0F1B42BB64E383C3D60DC7ECEB7CE743650E802FCAC677D10C25F`.
  - Exploratory checkpoint economics at the current 10-window admitted filter (`FLOW_005_AND_PRICE_10BPS`, stress 0) are negative at all tested sizes: at 1 SOL, fixed 15/30/60 s are `-4.0643 / -3.1279 / -2.0688 SOL` with PF `0.262 / 0.338 / 0.489`; at 0.1 SOL they are `-0.3912 / -0.2969 / -0.1891 SOL`. This is a diagnostic result from the new native-account mark source, not a frozen policy or validation claim.

- [x] Audit the independent trigger against the preserved 2CQ fingerprint and build a separate event-driven lane.
  - Finding: the prior independent materializer admitted an early Pump-curve buy using only the `virtual_sol` phase threshold. Its later flow/price filters did not reproduce the preserved 2CQ event-driven trigger, and its materialized windows did not retain the pre-signal rolling context needed to test that trigger.
  - The preserved 2CQ evidence describes a compact burst before selection: recent 250 ms buying flow and price response, consecutive buys, recency, and lower concentration. The new lane reconstructs the pre-signal context from the native stream and applies a fixed discovery gate: at least 2 buys, positive net flow, positive log price change, and at least 2 consecutive buys in the preceding 250 ms, while retaining the frozen early-curve phase boundary.
  - New contract: `research/2cq-event-driven-independent-v1/contract.json`. New materializer: `scripts/materialize2CQEventDrivenIndependentWindowsV1.py`. New rows use `INDEPENDENT_EVENT_DRIVEN_MARKET_WINDOW`; teacher-wallet matching is forced false and never used for admission.
  - The materializer now sorts the preserved native replay by native receive time before deriving rolling features. The source contains 7,793 parsed rows and 2 backward time jumps; 2,395 rows change position under the stable receive-time sort. This is a real lineage correction for the new lane. Existing cohorts are not rewritten.
  - VPS replay audit result: 165 signals, 85 complete windows, 0 incomplete economics admissions, and 80 signals without a complete post-entry window. Economics output: 97,686 exploratory rows across 1,215 surfaces, zero controls, `policy_frozen=false`, and `promotion_blocked=true`.
  - The event-driven replay is currently negative under the existing native executable evaluator: at 0.1 SOL / stress 0, fixed 15/30/60 s on all event-driven windows are `-1.4412 / -1.2145 / -0.9952 SOL`, with PF `0.072 / 0.148 / 0.117`. This does not prove the trigger is invalid because the replay audit has no contemporaneous controls and the marks are still historical research marks, but it does rule out claiming that the prior broad-lane PnL was evidence for the actual event-driven fingerprint.
  - Focused tests: 16 passed. Local SHA-256: materializer `B3535C6C8AB33693992BCDF89924FFEC4F7C1FEC9BD2EE8BD0F7E85F36F87A17`; lab admission extension `080D4AFF44760D371F3ECF8797179CE2982E585CE605B86933BE7519F16FB222`; contract `AB66CA589809678E7F41E840970F6B5140D61761E47D0AD21AC7DAF320718D80`.
  - This is VPS-tested as a read-only offline replay audit only. No active cohort, frozen policy, or execution service was changed.

- [ ] Add same-period native controls to the event-driven lane and run a fresh prospective event-driven cohort.
  - Do not select a winner from the 85-window replay audit. First attach route-compatible non-teacher controls, then validate only a pre-registered event-driven candidate on untouched receive-time data.
  - The next decision is whether the event-driven trigger has positive candidate-vs-control economics after the one-second delay. If it does not, the 2CQ Pump-curve lead should be marked research-only rather than repeatedly optimized.

- [x] Build the feature-complete native adapter for the preserved 2CQ scorer.
  - Adapter: `scripts/adapt2CQFrozenScorerNativeV1.py`.
  - It applies the preserved timing-model coefficients and threshold only when exact create receive time, creator identity, launch reserve state, native event state, and native fee fields are present.
  - It fail-closes missing lineage and does not substitute first-seen time, first-observed reserves, or a wallet label.
  - Audit task `AUD-F01` correction: the adapter now compares the model's sigmoid probability to the frozen probability threshold, retains both `score_probability` and `score_logit`, and defaults to an explicit `post_event` boundary matching the preserved scorer's `POST_EXTERNAL_EVENT` training semantics.
  - The adapter is explicitly labeled `timing_model_benchmark_only`; it does not claim cross-mint selection, age gating, or full composite-policy parity. The frozen scorer JSON and prior outputs were not rewritten.
  - Independent scoring uses the preserved feature formula over the market-wide prior tape without excluding the historical teacher wallet; this is explicit source-population behavior, not a hidden change to the model coefficients.
  - Focused adapter/materializer/lineage tests passed 22/22; adapter-specific tests passed 5/5. The supplied scorer probe now stops at its old defect-only `scorer_value` call, which is recorded in `RESEARCH_TASKS.md` as expected after correction.
  - This is locally verified and read-only/offline verified; it has not been live-tested on the VPS.

- [x] Repair tie-sensitive scorer metrics and strict top-rank admission.
  - Audit task `AUD-F02` is verified. `average_precision` now aggregates equal-score groups instead of relying on stable sort order; ranking reports distinguish unique top-1, inclusive top-1, conservative worst rank, randomized tie credit, and tie mass.
  - The sealed-validation cross-mint gate now requires a strict unique top score; a tied teacher state is not counted as a top-1 win.
  - Regression tests: tie metrics plus existing sealed-validation tests passed 4/4. No frozen model, threshold, cohort, or PnL output was rewritten.
  - The original portable scorer probe remains a historical defect reproduction and is expected to stop at its removed raw-score helper; the correct behavior is covered by `scripts/testTwoCqCausalTieMetricsV1.py`.

- [x] Repair signed-flow handling for missing sell quote amounts.
  - Audit task `AUD-F03` is verified for the downstream research lane. `scripts/materialize2CQEventDrivenIndependentWindowsV1.py` now checks both buy and sell quote fields, preserves unknown amounts as `None`, exposes `unknown_quote_count_*`, and prevents incomplete signed flow from passing the positive-flow gate.
  - The audit counterexample (−0.277 SOL sell plus two +0.1 SOL buys) now yields −0.077 SOL and is rejected, not +0.2 SOL.
  - Local affected tests passed 16/16; VPS-compatible read-only tests passed 4/4. A current replay shape audit found 4,361 sells with 0 exact sell-quote fields. That is a remaining upstream telemetry limitation, not permission to infer executed proceeds from the sell instruction's minimum-output argument.
  - No live service was restarted and no trading path changed. Exact sell execution telemetry is now the next decoder/state task.

- [x] Pin the native Pump decoder matrix and add official V3 variant coverage.
  - Audit subtask `AUD-F04A` is verified. `rust-sidecar/src/main.rs` now recognizes official `buy_v3` and `sell_v3` discriminators in the native Pump selectors and sell-line helper.
  - Matrix: `research/native-decoder-matrix-v1/decoder-matrix.json`; validator: `scripts/validateNativeDecoderMatrixV1.py`.
  - Matrix tests passed 2/2. Default `cargo check --bin pumpfun-sidecar` passed and default Rust tests passed 173/173. The feature-enabled Windows build is blocked by missing OpenSSL; no VPS service was restarted, so no deployment/live-capture claim is made.
  - This is recognition coverage only. Execution success, CPI/ALT resolution, max-cost semantics, migration, and exact sell proceeds remain the active `AUD-F04B2` task; failed/unknown status quarantine is verified under `AUD-F04B1`.

- [x] Add explicit execution-status quarantine to the native Pump state materializer.
  - Audit subtask `AUD-F04B1` is verified. `scripts/materializeNativePumpCurveStateV1.py` now distinguishes explicit `SUCCESS`, explicit `FAILED`, and `UNKNOWN` execution status.
  - Failed rows are rejected and counted; unknown rows are labeled `CONDITIONAL_INTENT_REPLAY` and cannot be marked `native_economics_state_complete`. This prevents failed intent and unknown status from becoming authoritative economics.
  - Local affected materializer/lab tests passed 18/18. VPS-compatible read-only tests passed 6/6 on `159.195.207.109`. No collector or service was restarted, no frozen evidence was rewritten, and no trading was enabled.
  - This is a bounded downstream repair, not full executed-transition parity. Realized amount semantics, CPI/ALT resolution, migration, and processed/finalized golden fixtures remain active under `AUD-F04B2`.

- [x] Quarantine max-cost Pump buys from executed economics.
  - Bounded subtask `AUD-F04B2a` is verified. The materializer decodes the known native buy discriminator from `source_ix_data_base64`, distinguishes `MAX_SOL_COST_INTENT` from exact-input intent, and refuses to use a max-cost limit as spent SOL without an explicit realized quote field.
  - Exact-input intents may remain in conditional replay, but they are not authoritative economics unless execution status and complete reserve state are present. The output records `native_instruction_variant`, `quote_semantics`, and `quote_amount_source`.
  - Local focused tests passed 20/20; the full audit-focused suite passed 38/38; VPS-compatible tests passed 8/8. No collector/service was restarted and no execution was enabled.
  - This does not manufacture missing realized amounts. CPI/ALT resolution, migration, processed/finalized success joins, and exact sell proceeds remain active under `AUD-F04B2`.

- [x] Implement same-period non-teacher control construction and native causal window joining.
  - Controls are non-crossing, feature-complete Pump buys from the same sorted native tape and are written as `INDEPENDENT_MARKET_CONTROL_WINDOW`.
  - Bridge: `scripts/materialize2CQFrozenScorerWindowsV1.py`.
  - Candidate and control windows use the same one-second delayed entry, exact native entry state, fee fields, and post-entry marks. Empty marks are retained so mature horizons can be reported as missed exits rather than silently dropped.
  - Contract: `research/2cq-native-frozen-scorer-adapter-v1/window-contract.json`.

- [ ] Run the exact frozen scorer and causal evaluator on complete native lineage.
  - Historical native replay audit must first show non-zero exact feature coverage and both candidate and control windows. If it does not, the evaluator run is a blocked coverage audit, not a negative strategy result.
  - Only after that gate may the exact event-driven trigger be evaluated at 0.1, 0.5, and 1.0 SOL across the pre-registered exits and realistic fee/impact/failure handling.
  - No fresh prospective cohort, policy freeze, or execution step is justified until this comparison is populated.
  - Current read-only VPS audit (`20261022T223500Z`): 7,826 rows across 1,293 mints; 0 mints had the complete exact launch lineage, because all incomplete mints lacked create receive clock, creator identity, and exact launch reserve state. Result: 0 exact signals, 0 same-period controls, 0 candidate/control windows, and 0 causal economics outcomes. This is a coverage block, not a negative PnL result.
  - The evaluator was nevertheless invoked with `--require-native-receipt-boundary`; it returned `admitted_windows=0`, `outcome_rows=0`, `surface_rows=0`, `promotion_blocked=true`.
  - The native replay was still advancing while the audit ran; the preserved source and all prior cohorts were not rewritten. No collector was restarted and no trading was enabled.

- [x] Build the upstream native launch-lineage enrichment bridge.
  - Bridge: `scripts/materialize2CQNativeLaunchLineageV1.py`.
  - It joins native mints to the exact CREATE registry and an optional exact CREATE-decoder evidence stream. It requires create receive clock, creator identity, and launch reserve state; it rejects first-observation substitutions and create clocks after the first native observation.
  - Contract: `research/2cq-native-frozen-scorer-adapter-v1/launch-lineage-contract.json`.
  - Current VPS audit (`20261022T230000Z`): 7,839 native rows across 1,301 mints; registry contains 80,980 historical CREATE rows but 0 complete current launch-lineage rows. The current registry ends before this native replay period, and it carries no creator or launch-reserve fields. Output is therefore correctly empty with `proxy_substitution=false`.
  - Integrated adapter rerun (`20261022T230500Z`) remained fail-closed: 0 exact-lineage mints, 0 signals, 0 controls. This is an upstream evidence-coverage result, not a strategy PnL result.
  - Focused local tests for the bridge, adapter, and window join passed 5/5; the broader focused suite remains 17/17.

- [x] Prepare an independent external code/strategy audit bundle.
  - Bundle: `research/external-audit-bundle-v1/EdgeSniffer-external-audit-bundle-20261022-183234.zip`.
  - Prompt: `research/external-audit-bundle-v1/AUDIT_PROMPT.txt`.
  - Scope includes current research/evaluator scripts, Rust sidecar source, relevant contracts, frozen scorer, tests, and roadmap. Secrets, environment files, private keys, raw bulk evidence, caches, generated reports, binaries, and build directories were excluded.
  - The external review is advisory only; it must not be treated as validation or authority over the sealed evidence.

## Audit reassessment correction — 2026-10-22

- Previous local repairs did not establish downstream evidence integrity. The
  frozen window builder could recertify incomplete replay; shared consumers
  ignored conditional state. AUD-I01 now rejects known conditional/legacy replay
  in scorer conversion, windows, state parsing and quotes. Successful intent
  replay stays approximate; 41 local tests pass including actual replay integration.
- Full F01/F03 and quote-provenance acceptance remain open in RESEARCH_TASKS.md.
  Earlier checked boxes document narrow implementation milestones, not certified
  economic evidence. No service deployment or live testing occurred in this review.
- AUD-R01 timing diagnostic completed: 76 episodes/67 mints, median selected
  receipt lead 36.274923ms, 0 selected versus 25 first crossings lead >=1s.
- Detailed decisions and experiment specifications: ../audit-reassessment-v1/assessment.md.
  Next bounded build: AUD-L01A opportunity/admission ledger; full teacher launch
  reconstruction is not a prerequisite for recent-flow research.

## Operational boundary

The original receive-only source service remains the same service and output
namespace, but now runs the versioned V3 decoder. Its prior unit file is kept
as `edgesniffer-teacher-impulse-parity-v1-source-feed.service.pre-v3-20261022`.
The new fingerprint materializer and strategy lab use separate output paths;
they do not alter the preserved historical cohorts.
