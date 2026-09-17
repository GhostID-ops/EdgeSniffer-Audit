
# Evidence unavailable

- No continuously sampled historical CPU, RSS, swap, disk-latency, or I/O-wait
  time series exists for the full requested seven-day period. The bundle
  contains current snapshots and systemd lifecycle/resource accounting where
  retained by journald.
- The services do not persist a dedicated per-run structured timing ledger for
  every historical reconciler/materializer invocation. Safe systemd lifecycle
  and resource messages are included instead.
- The current database does not persist separate timestamps for every internal
  materializer substage. Acquisition, first decision-version creation, current
  decision update, partition close, and reconciliation completion are available.
- Exact lock-acquisition wait duration is not persisted as a structured metric.
  Timer schedules, service starts/finishes, and current locking configuration
  are included so contention can be bounded but not reconstructed perfectly.
- No transaction payloads, signatures, mints, wallet addresses, PnL, outcomes,
  winner/loss labels, sealed validation rows, or strategy features were exported.
