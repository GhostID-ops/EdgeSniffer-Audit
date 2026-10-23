# Main Lane Storage Reclamation + Future Exact Evidence Ledger V1

Generated from the final verified VPS state at 2026-09-06 06:17 UTC.

## Classification

`PUMP_EXACT_EVIDENCE_LEDGER_V1_LIVE_COLLECTION_READY`

Operational warning: `RETENTION_7_DAY_WARNING`. Evidence correctness is ready; current
capacity is not long-term sufficient.

## Storage

| Metric | Before | Final |
|---|---:|---:|
| Root used | 45,215,473,664 B | 40,012,865,536 B |
| Root free | 35,058,241,536 B | 40,260,849,664 B |
| Root usage | 57% | 50% |
| Volume free | 15,821,234,176 B | 15,763,824,640 B |

The allowlisted cleanup deleted 6,280,336,064 bytes. The final rebuild-only ledger Cargo
target deleted another 568,536,101 transient bytes after the deployed and archived binary
hashes matched. No unique corpus, immutable audit, active runtime, wallet/key/config, or 8YCB
data was deleted.

Deletion and preservation manifests:

`/var/lib/edgesniffer-storage-offload-v3/manifests/main-lane-storage-ledger-v1/`

## Collector

- Future evidence boundary: 2026-09-06T05:51:02.166853006Z, slot 444717456.
- First evidence ID: `72b1fc3d4bfe5f0c4905cdb92cc2bdb5b57563e252d828806f28c33b5f037844`.
- First complete partition: `2026/09/06/05`.
- First partition: 20,967 observed and 20,967 finalized; 9,272 successful and 11,695 failed.
- First partition gaps: zero finality, raw transaction, TradeEvent, reserve, canonical order,
  fee, decode, and success/failure mismatches.
- Collector service: active, observation-only, no signer or submit code.
- Hourly reconciler timer: active; low CPU/I/O scheduling priority.
- Final queue high-water: 46 / 8,192; reconnects: 0; finality failures: 0.

## Identity

- Schema: `PUMP_EXACT_EVIDENCE_LEDGER_V1`.
- Schema SHA-256: `16f182ad1dc7e3b2a1a1613e9ac52a897c563e1d7a7fc22eae10331ea137df3f`.
- Binary SHA-256: `8de9ec9713cb25ab2fd3e14d6123b47eb2bbb16fba36c4dc377537c338c0ca21`.
- Reconciliation schema: `PUMP_EXACT_EVIDENCE_LEDGER_V1_RECONCILIATION_V1`.

## Validation

Rust tests: 11 passed. Release build and strict Clippy passed. Restart/reopen, rollover,
dedupe, crash-tail recovery, checksums, bounded queue, failed-transaction zero-state, and
retention estimation were exercised.

Fresh canary replay retained 19/19 successful mint snapshots and 948 admitted normal
transitions, with zero admitted reserve/order/status mismatch. A second restart canary retained
3/3 fresh mint snapshots and 342 admitted transitions with the same zero-mismatch result.

## Scope And Gaps

The source subscription covers the full Pump-program transaction universe and includes failed
transactions. Raw observations, finalized transaction truth, finalized block signature order,
TradeEvents, instruction data/accounts, and Create/CreateV2 mint snapshots are retained.
Completed, exact-reconciled normal Pump lifecycles can be replayed without later historical RPC.

The RabbitStream backend did not deliver requested account-write/slot/block-meta updates.
Canonical ordering and finality are therefore captured asynchronously through finalized raw RPC
evidence. Mayhem and newer unsupported zero-virtual event variants are retained losslessly but
explicitly excluded from admitted exact replay until their evaluator semantics are supported.

## Capacity

- Measured compressed growth: 11.79 GB/day, about 0.49 GB/hour or 354 GB/30 days.
- Root retention: about 3.41 days.
- Additional currently free volume capacity: about 1.34 days at the same rate.
- Combined current free capacity: about 4.75 days, before other corpus growth.
- Recommended durable capacity: at least 500 GB additional for a practical 30-day runway.

At the first production rate, roughly 16,000 successful fresh mints/day are observed. One day is
enough for descriptive checks; useful initial discovery should target roughly 50,000-100,000
fresh mints over 3-7 days, with longer collection needed for regime diversity. No alpha search or
Ranker tuning was performed in this task.

## Safety State

- Ranker direct emitter: inactive.
- N25 V3 receiver/safety process: active; the direct emitter is inactive, so no Ranker
  candidate source is feeding new entries.
- Wallet: managed 0, positions 0, unmanaged 0.
- Canonical Ranker collector: active and unchanged.
- 8YCB: active and untouched.
- Exact evaluator and independent red-team checksums: verified unchanged.
