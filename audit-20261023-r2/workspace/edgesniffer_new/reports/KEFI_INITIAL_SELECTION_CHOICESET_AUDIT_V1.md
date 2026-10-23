# kEFi Initial Token Selection - Choice-Set Audit V1

## Scope and guardrails

This bounded, outcome-blind audit asked whether the already retained evidence
can rank kEfi's newly observed token selections against the legitimate pools
available at the same pre-action decision moment. It did not read PRE_H3
artifacts, actionability outcomes, evaluator output, PnL, or exit outcomes.
No model, feature search, economic analysis, or new long-running service was
started.

## Frozen synchronized snapshot

- Snapshot root: `/mnt/edgesniffer-kefi-hot/kefi-choice-set-audit-v1/snapshot-20261013T044820Z`
- Snapshot receipt: `snapshot-audit.json`
- Snapshot receipt SHA-256: `07b7754d65cc0cf8f4d13060f33b8fc02be65a03deddec2c73ea586b3c254666`
- Wallet ledger rows: `5,227`
- Wallet ledger SHA-256: `b67ac214697a624c72ee25571b938bcc7e6d6e1982076b0c04f6755cbcc9376d`
- Wallet state SHA-256: `b85993cea0b46a65669b9faf8e0bec23977a7a3cdd2863752be7b34adecdd6a4`
- Wallet action block-time interval: `1791797650` through `1791865565`
- Source instance: `2026-10-12T14:59:43.875661353Z-pid533030`
- Continuity epoch: `1`
- Snapshot-time reconnects / receive errors / continuity gaps: `0 / 0 / 0`

The matching wallet-state snapshot contains 220 `FIRST_OBSERVED_ENTRY` rows.
This is sufficient teacher-side history for a bounded sample, but it does not
by itself make a selection comparison valid.

## Choice-set provenance finding

The available compact market input is
`kefi-capture-first-control-v8/market-deduplicated.jsonl`.

- Materialized market rows: `224,368`
- Processed promotion windows: `36`
- Checkpoint schema: `KEFI_CAPTURE_FIRST_CONTROL_MATERIALIZER_V1_CHECKPOINT_V1`
- Checkpoint declares outcome-blind processing: `true`

The associated state lists only tracked-wallet promotion-window roots. Thus
these rows describe public market activity near windows that kEFi had already
caused to be promoted; they are not a mechanically admitted, teacher-
independent universe of every supported pool available before each teacher
selection.

This is a selection-bias problem, not an outcome-leakage claim. An
outcome-blind extract can still be unsuitable as a choice set when its time
windows were selected around the teacher's actions.

## Required questions

1. **Legitimate alternatives at a decision moment:** not reconstructable from
   the selected-centred input. No defensible typical choice-set size is
   available.
2. **Existing 13-feature rank:** not run. A rank against selected-centred
   windows would not measure token selection among the contemporaneous
   alternatives.
3. **New pre-action distinguishing variables:** not evaluated; their paired
   comparison requires valid non-chosen alternatives.
4. **Conditions common among ignored tokens:** not evaluable from this input,
   because ignored-token prevalence is unknown outside promoted windows.
5. **Additional sell-side, flow, liquidity, participant, or lifecycle value:**
   not evaluated.
6. **True pre-action status:** the raw source carries ordered provenance, but
   the compact materialization lacks a valid all-pool decision-time universe;
   this audit cannot certify individual selection clues.
7. **Expanded chronological model:** not run. The valid-choice-set gate fails
   before the required 60 clean selection moments/model gate.
8. **Best current evidence for kEfi's initial filtering:** existing work can
   describe activity near chosen pools, but this snapshot cannot distinguish
   that behavior from the alternatives kEfi ignored.
9. **Unexplained:** the teacher-independent contemporaneous candidate set.
10. **Selection mechanism ready for bounded H3 discovery:** no. The next
    scientific decision must wait for a valid choice-set input, not a broader
    feature tournament.

## Smallest missing capability

Produce a compact, teacher-independent chronological per-pool state export
from the existing canonical source: supported pools with source instance,
continuity epoch, durable writer sequence, first public observation, and only
pre-action supported state. It must be admitted independently of kEfi,
without duplicating raw partitions. That export would allow both
`ALL_ELIGIBLE_POOLS` and `NEW_TO_OBSERVED_WALLET` to be reconstructed at each
selection time. It is a recommendation only; this audit intentionally did not
build it or launch new infrastructure.

## Integrity conclusion

The direct-wallet snapshot is hash-bound and the source health recorded at
snapshot time is clean. PRE_H3 remained untouched and sealed. The early stop
is required because the candidate universe is selected-centred, not because a
fingerprint has been disproved.

CHOICESET_RECONSTRUCTION_NOT_YET_VALID
