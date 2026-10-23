# 2CQ Storage Tiering V1

## Purpose

The local research volume is the hot tier, not the permanent warehouse. The
NFS volume is the cold tier. A full-meta capture may resume only while the hot
tier has enough headroom for open partitions, compression, promoted windows,
SQLite journaling, and recovery.

## Classes

| Class | Location | Release rule |
| --- | --- | --- |
| Open hourly ledger and current partial partition | Local only | Never moved or released while open. |
| Unresolved 120-second windows and active promoted evidence | Local only | Never moved or released until every dependency is resolved. |
| Closed immutable raw hourly partition | Local then NFS | Copy, SHA-256 verify against its manifest, write immutable receipt, atomically replace local payload with an NFS symlink. |
| Canonical compact state, manifests, and final admitted-row evidence | Permanent protected evidence | Never generic-cleaned. Any archive requires an explicit receipt and reproducibility policy. |
| Old caches, aborted lanes, and legacy research trees | Explicit cold review | Each tree requires an inactivity/dependency check and a tree-level SHA-256 receipt before local release. |

## Watermarks

- Target operating headroom: 150 GiB local free.
- Warning: 100 GiB local free; dispatch bounded verified raw archive work.
- Emergency: 50 GiB local free; the separate capacity guard pauses the full-meta collector rather than risking evidence loss.

## Non-Negotiable Safety

- NFS is never used for open or latency-sensitive canonical processing.
- No source deletion occurs before exact verification and a durable receipt.
- A symlinked cold raw partition retains its canonical local path while releasing local bytes.
- `promoted-windows` is not a generic cleanup target. Its lifecycle must be governed by unresolved-work and scientific-cohort evidence references.
- The storage supervisor is outcome-blind and makes no scientific admission decision.
