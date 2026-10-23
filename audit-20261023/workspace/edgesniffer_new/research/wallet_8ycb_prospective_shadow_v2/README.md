# 8YCB Prospective No-Submit Shadow V2

This service records a prospective observational cohort for the frozen public
8YCB CREATE plus same-transaction atomic initial-buy event. It does not build,
sign, simulate, submit, or broadcast transactions.

The cohort begins at the immutable UTC boundary in `frozen_spec.json`. On first
start, both that specification and `cohort_manifest.json` are published to the
dedicated output directory and checked on every restart. The existing normalized
observation feed supplies CREATE and market-flow rows. A separate account-filtered
Shyft Yellowstone subscription supplies target-wallet buy/sell balance deltas,
including inner-instruction activity. It imports only filesystem/path helpers and
the Yellowstone subscription client.

The primary score is `ACTUAL_SHADOW_SIGNAL_READY` at 0.1 SOL with TP +20%, SL
-20%, a 120-second maximum hold, and the externally sourced 178 ms first-sellable
reference. Fixed +25/+50/+100/+250/+500/+1000 ms lanes, 122/178/346 ms sellability
references, +5% entry stress, generic controls, target-sell collisions, recurrent
wallet diagnostics, RAW signals, and MAX_OPEN1 are recorded without tuning.

The append-only `cohort_ledger.jsonl` is the recovery source of truth. Required
gzip datasets and JSON summaries are reproducible snapshots. Transaction
construction is explicitly `OMITTED_V2`.
