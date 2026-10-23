# kEFi Choice-State Collection V1 Final Report

Collection-only report. No ranking, feature comparison, model, outcome, PnL,
or selection-audit result was read or computed.

## Final Cohort

- Cohort path:
  `/mnt/edgesniffer-kefi-hot/kefi-streaming-choice-state-observer-v1/live/validation-choice-state-v1-1791915517686940846-pid939965`
- Seal reason: `STORAGE_CAP_REACHED`
- Valid durable snapshots: `49`
- Snapshot target: `60`
- Atomic output at seal: below the 90 MiB cap; the next complete snapshot was
  not written.
- Warm-up: confirmed before every admitted snapshot: 60 seconds of fresh
  public-source history, with rolling-window and prior-transition eligibility
  enforced.
- Continuity: one unchanged source instance and continuity epoch `1`.
- Decoder rejects: `0`
- Pool identity conflicts: `0`
- V8 capture service: `ACTIVE`
- PRE_H3 timer: `ACTIVE`

## Provenance

- Cohort manifest:
  `validation-choice-state-v1-1791915517686940846-pid939965/manifest.json`
- Cohort manifest SHA-256:
  `73191a75265bae60fd29eb18e8d6da92287161ad940d1aa94a3d1c1d48ea3588`
- Cohort validation receipt:
  `validation-choice-state-v1-1791915517686940846-pid939965/validation.json`
- Cohort validation receipt SHA-256:
  `0dc63bc4e706f57ba5eae4e5b64dbf467ca2bc4f3742525767e4f9cdb117b2fb`

The earlier 63-snapshot run and all pre-warm-up runs remain permanently
quarantined from this cohort.

CHOICESTATE_COHORT_INSUFFICIENT_CLEAN_SELECTIONS
