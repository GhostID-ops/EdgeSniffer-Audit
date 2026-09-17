# EdgeSniffer runtime throughput audit bundle

Read-only snapshot collected on 2026-09-17 UTC. It is designed to support an
independent infrastructure audit without exposing transaction identities,
strategy features, economic results, or sealed labels.

## Pipeline evidence

- `pipeline/decision-summary.json`: current status/reason counts plus latency
  distributions from acquisition time to first and latest materialized state.
- `pipeline/pseudonymous-decision-timeline.csv`: one row per current decision;
  signatures are replaced with non-reversible 16-character SHA-256 aliases.
- `pipeline/exact-not-ready-pseudonymous.json`: current reconciliation backlog,
  including age and partition hour, with pseudonymous identifiers only.
- `pipeline/reconciliation-partitions-7d.csv`: closed partition timestamps,
  completion state, input sizes, counts, unresolved counts, and close-to-
  reconciliation latency for the retained seven-day window.
- `pipeline/reconciliation-summary.json`: aggregate reconciliation coverage,
  volume, and latency percentiles.
- `pipeline/current-materializer-status-sanitized.json`: allowlisted operational
  fields from the latest materializer status. Attempted signatures and sealed
  labels are omitted.

`ONLINE_ELIGIBLE` means the observation passed the frozen infrastructure/data
completeness gate. It is not an economic outcome label. `DEFERRED` records are
temporarily pending; reason codes explain why. `TERMINAL` records are structural
or permanent exclusions and carry no winner/loss information.

## Database and runtime evidence

- `sqlite/`: schema, indexes, table cardinalities, SQLite pragmas, and query
  plans. Database contents and transaction payloads are not copied.
- `systemd/`: deployed unit/timer definitions and runtime accounting for the
  collector, reconciler, materializer, capture observer, and timer units.
- `logs/`: bounded sanitized operational lifecycle/resource/error messages for
  one-day and seven-day views. Routine payload-bearing logs are excluded.
- `system/`: current CPU, memory, swap, PSI, disk, mount, block-device, I/O,
  process-resource, uptime, and kernel OOM snapshots.

## Boundaries

This export performed no replay, repair, restart, configuration change, or
instrumentation. See `UNAVAILABLE.md` for requested metrics that the deployed
pipeline does not persist and therefore cannot be reconstructed honestly.
