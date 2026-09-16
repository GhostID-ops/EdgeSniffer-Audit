# PUMP_EXACT_EVIDENCE_LEDGER_V1

Standalone, observation-only Rust collector for finalized Pump evidence. It has no wallet,
signer, transaction builder, or submission code and is not imported by the trading sidecar.

It stores the original Yellowstone `SubscribeUpdate` protobuf once, with a compact JSON
metadata envelope and per-frame SHA-256. UTC receive-hour partitions are temporally closed
through a verified zstd temporary file and atomic rename. Exact replay completion is proven
separately by an immutable cross-partition reconciliation manifest after the finality grace
window. Failed transactions are retained but explicitly
classified as zero-state mutations for later replay.

Sources:

- full-universe Pump-program observations from RabbitStream, followed by a bounded
  finalized `getTransaction` reconciliation for success/failure and full metadata
- finalized Pump-program and Pump-fee-program account writes
- finalized slot and block metadata
- finalized mint-account snapshots discovered from Create/CreateV2, for SPL/Token-2022 data

Coverage gaps are counters, never silent fallbacks. `completeUtcHour` and
`exactReplayComplete` remain false in the raw partition manifest; only
`reconciliation-manifest.json` can establish exact replay completion. It joins every observed
signature to finalized transaction truth and canonical finalized-block order, including
evidence written in either of the next two receive-hour partitions.

Commands:

```text
cargo test
cargo clippy --all-targets -- -D warnings
pump-exact-evidence-ledger schema-hash
pump-exact-evidence-ledger verify /var/lib/pump-exact-evidence-ledger-v1
python3 scripts/reconcilePumpExactEvidenceLedgerV1.py /var/lib/pump-exact-evidence-ledger-v1
```

The service writes `health.json`, `schema-v1.json`, append-only evidence IDs, active frame
data, immutable compressed partitions, and per-partition manifests. It alerts at estimated
30/14/7-day retention boundaries and never prunes evidence automatically.
