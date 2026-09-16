# Teacher Impulse Source Binding

Date: 2026-08-26

Strategy: `teacher-impulse-parity-v1`

## Current State

The reset parity binaries are ready to consume candidate capsules, but no live
prospective source has been confirmed under `/var/lib/edgesniffer*`.

The expected source path is:

`/var/lib/edgesniffer-teacher-impulse-parity-v1/candidate-source.jsonl`

The producer converts this into:

`/var/lib/edgesniffer-teacher-impulse-parity-v1/decision-inputs.jsonl`

The receipt process converts that into:

`/var/lib/edgesniffer-teacher-impulse-parity-v1/decision-receipts.jsonl`

The epoch report process reads all three files and writes scoped Markdown/JSON
reports under:

`/var/lib/edgesniffer-teacher-impulse-parity-v1/reports/`

## Source Rules

- The source must emit candidate rows only, not broad firehose rows.
- Each row should include all decision-boundary fields needed by Rust.
- Missing Mayhem evidence remains unavailable and fail-closed.
- Source slot is provenance only.
- Canonical curve/pool state must be observed no later than candidate-ready time.
- Reports must consume `DecisionReceipt` rows, not legacy summaries.

## Known Legacy Shapes Now Parsed

- `data/2cq-frozen-signals.jsonl` style nested `signal` plus `context` rows.
- `data/2cq-cya-distributed-pocket-parity.jsonl` style `contextSnapshot` rows.

These old rows are fixtures/hypothesis material only. If they lack source
instruction data, canonical state, Mayhem proof, quote/min-out, or required
features, the Rust evaluator emits explicit unavailable receipts instead of
pretending they were accepted.

## Next Required Build

Build or select one prospective candidate emitter that writes complete candidate
rows to the expected source path. After that:

1. Run producer and receipt in no-submit mode.
2. Compare local and VPS receipt hashes for identical inputs.
3. Generate only receipt-based lane reports.
4. Promote nothing to dust/live until the receipt sample is large enough and
   positive under executable timing.
