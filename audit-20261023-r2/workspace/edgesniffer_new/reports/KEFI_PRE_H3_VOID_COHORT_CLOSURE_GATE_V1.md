# PRE_H3 Actionability Void-Cohort Closure Gate

Status: `REMEDIATED_NO_BOUNDARY_CREATED`  
Outcome evaluation: `NOT RUN`; PnL access: `NO`

## Root Cause

Two sealed scientific windows overlapped. The former assignment builder
concatenated their per-window JSONL files directly, so it observed the same
canonical frame/event twice before canonical ingestion. The source evidence was
not conflicting and the source continuity contract did not break.

The void-cohort forensic artifact records `67,038` row occurrences,
`50,882` unique canonical identities, `16,156` identical cross-window overlap
identities, zero within-receipt duplicates, and zero conflicting intrinsic
identities. It retains each window exposure separately.

## Architectural Closure

- `kefi_incremental_event_index_v1.py` now exports one deterministic canonical
  intrinsic-event stream plus a separate window-exposure provenance stream.
- `kefi_pre_h3_assignment_builder_v1.py` accepts only that hash-verified
  canonical export; receipt-list input is removed.
- Derived/PnL-like annotations cannot alter canonical identity.
- The voided cohort now has an immutable registry closure. Its membership
  interval through closure is permanently ineligible; after closure there is a
  gap until a later separately approved non-overlapping future epoch begins.
  The old append-only record is never deleted, reused, or bridged.

## Offline Replay Proof

Canonical stream, rerun A/B SHA-256:
`187bb55ba608e0c99b58ccec0a6559e22bf388a4818cdde5dcf4f3e4d39295a4`

Exposure-provenance SHA-256:
`aaac2a26469efee901c5e59d631fa0616128515031f28ccd54169a20f6da8685`

Assignment output, rerun A/B SHA-256:
`d63d9d8e85f12c8942a7789d8d2544ad450df56f9426f410c86a400204dfb448`

No evaluator was invoked in this proof.

## Verified Toolchain

- Canonical index: `e8139b71c585e0058b7083ce63e712faa85e2961bbfa422f9b6c07c9603577ed`
- Assignment builder: `1b78bb03f54a323174ca4c2740197dab2b7e0eba8efa63344c2f389f7e1cc39b`
- Epoch registry: `bff3ed4d5f7e085d0ae4ec72a743a64cdcfa416c11fc02de0e026e69f86499f1`
- Frozen fingerprint: `db08899d92f4d69d8c0234cfff9cbd05bd27dfb1365aed88d08b85f88cfc1b4a`
- Final non-bridging-void deployment manifest:
  `f036e37581c481f148a58e825601ff6493683aaaee49bd3b966241a661cb24a4`

The targeted qualification suite passed `35` tests; the complete kEfi suite
passed `75` tests. The invalidated cohort timer is disabled. No new boundary
has been created.
