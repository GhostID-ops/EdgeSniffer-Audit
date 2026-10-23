# 2CQ Priority Roadmap Status

Generated: 2026-09-11 UTC

## Decision

The cumulative reconciliation job is now memory-bounded and durably incremental. The old timeout-only change was insufficient because it removed the 30-minute deadline without correcting cumulative heap growth. The repaired V3 materializer completed at 500/500 (350 development, 150 sealed validation) without reading outcomes. The development-only transition study is complete. The frozen representativeness gate failed, so the historical LAUNCHED_120S cross-sectional component is retired. A new forward-only symmetric choice-universe cohort began at `2026-09-11T07:15:24.437413402Z` using the already-running raw collectors.

## Priority Results

| Priority | Status | Result |
| --- | --- | --- |
| 1. Incremental reconciliation | Complete | Disk-backed transaction reads, new-episode-only reconciliation, one-episode durable checkpoints, bounded arrival joins, and fail-fast unsupported routes. V3 reached 500/500 with 0 outcomes read. |
| 2. LAUNCHED_120S coverage | Existing-evidence blocker | 211/350 comparable before and after exhaustive V2 scan; 0 recovered, 0 regressed, 139 still missing. |
| 3. Transition/hazard study | Complete | `BASE_PLUS_TRANSITION` selected development-only. Assessment AUC 0.829; teacher-state recall 80.26%; first rising crossing equals teacher state 34.21%. |
| 4. Freeze primary policy | Historical component retired | The representativeness gate failed on chronological coverage range and structural chronology shift. Same-mint timing evidence is preserved. |
| 5. Sealed N150 validation | Protected / not run | Validator requires N500 and explicit `sealedValidationAuthorized=true`; current policy is false. Validation remains sealed and outcomes unread. |
| 6. Prospective symmetric evidence | Active from frozen timestamp | Existing capture-first, compact AllenHark, and exact-ledger streams preserve the raw evidence. No extra high-load service was added. |
| 7. Exact profitability | Pending | Remains downstream of prospective behavioral parity. |

## Coverage Finding

The V2 repair scanned every stored successful finalized transaction in the relevant decision windows, including CPI and loaded-address cases, before Pump classification. It found the same 211 comparable episodes as the original analysis. The stored corpus lacks canonical CREATE and/or selected-decision transactions for much of the missing population.

Repairing this symmetrically would require a large historical canonical block reconstruction across roughly 88,000 relevant slots. Seeding only teacher-selected mints would bias the choice sets and is forbidden.

## Transition Finding

Transition features contain useful signal: assessment AUC improved from the static model's 0.703 to 0.829 for the selected combined family. They did not solve trigger timing. Only 34.21% of first rising crossings were the final teacher-selected state, and P75 lead remained 13.1 seconds.

This supports continued work on causal state transitions, but it does not justify opening sealed validation for the current combined policy.

## Representativeness Gate

The outcome-blind gate compared the 211 covered episodes with the 139 missing episodes using only development-side causal metadata.

- Overall coverage: 60.29%
- Chronological blocks: 82.86%, 60.00%, 57.14%, 45.71%, 55.71%
- Chronological range: 37.14 percentage points; frozen maximum was 25
- Maximum structural SMD: 0.394; frozen maximum was 0.35
- Maximum categorical total variation: 0.112; passed
- P90 causal-feature absolute SMD: 0.241; passed
- Missingness classifier direction-agnostic AUC: 0.503; passed

The causal market states are broadly similar, but evidence survival is materially time-dependent. Under the predeclared all-gates rule, this is a blocker and the historical cross-sectional component cannot consume sealed validation.

## Forward Replacement

`2CQ_PROSPECTIVE_SYMMETRIC_CHOICE_CENSUS_V1` starts at N=0 with a 350-development / 150-sealed-validation split. Selected mints and controls must satisfy identical CREATE, realtime-arrival, canonical-order, reserve-continuity, and global-continuity requirements. It fails closed and reads no outcomes.

Raw capture is already active; checkpoint materialization is deferred so no additional cumulative writer competes with the official census.

## Storage And Recovery

Four rebuildable build/cache directories were removed after proving no open files and preserving deployed binary hashes. The attached `/dev/sdb` device was then expanded online from its stale approximately 98 GiB ext4 boundary to the full 591 GiB filesystem. At the post-recovery check it had approximately 517 GiB free. No corpus, audit evidence, active journal, exact ledger, source, or 8YCB data was removed.

The volume-full outage from `2026-09-11T09:44:53.915446303Z` through `2026-09-11T15:40:05.438000000Z` is explicitly invalid for both the existing Tier-A census and the forward symmetric cohort. Capture-first, the continuous AllenHark observer, and the exact ledger were restored after the resize. The AllenHark observer is now enabled for reboot persistence without restarting the current process.

## Frozen Cross-Sectional Sequence

Dormant fail-closed tooling now enforces the next sequence:

1. Materialize the first 350 fully symmetric forward choice decisions.
2. Fit exactly one predeclared `FULL_CAUSAL` cross-sectional policy; no family tournament.
3. Freeze that policy before reading its own subsequent ordinals 351-500.
4. Validate exactly once with no refit.
5. Assemble it with the preserved `BASE_PLUS_TRANSITION` timing component.
6. Run prospective no-submit imitation before any exact-profitability study.

The fitter is intentionally blocked now because the replacement cohort has not materialized development N350. The old 350 and its old sealed N150 are not eligible inputs.

## Next Required Gate

Continue the existing Tier-A census and the new symmetric raw-evidence cohort. At symmetric development N350, fit one cross-sectional policy and freeze it. Then consume only that cohort's subsequent sealed N150 once. The old sealed N150 remains untouched and must not be repurposed for the retired historical cross-sectional model.
