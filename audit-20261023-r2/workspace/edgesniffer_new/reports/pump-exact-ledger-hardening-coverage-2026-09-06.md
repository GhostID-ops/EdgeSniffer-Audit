# Pump Exact Ledger Operational Hardening and Coverage Audit

Generated: 2026-09-06 16:20 UTC

## Verdict

`BLOCKED_FOR_EXACT_ALPHA_DISCOVERY`

The observation ledger is healthy and protected against the original silent
writer failure. Exact alpha discovery remains blocked by N, source-chain time
diversity, exact Mayhem replay coverage, material admission bias, and N30k
volume runway.

Ranker live remains paused. No alpha scan was run. 8YCB was not modified.

## 1. Silent writer root cause

The 07:18 UTC failure was a live HTTP/2 RabbitStream session that stopped
yielding updates. The process and systemd unit remained alive. The original
collector waited indefinitely on `updates.next().await`, and health was only
rewritten after an ingest item reached the writer.

The missing interval was not exactly recovered:

| Boundary | Value |
|---|---|
| Last raw transaction | 2026-09-06 07:18:24.958 UTC, slot 444734005 |
| Last durable auxiliary record | 2026-09-06 07:18:42.758 UTC, slot 444734028 |
| First recovered partition | 2026-09-06 14:12:46.959 UTC, slot 444812521 |
| Unrecovered raw slot range | 444734006 through 444812520 |
| Missing slots | 78,515 |

The interval is immutable-audited as
`UNRECOVERED_AUTHORITATIVE_EVIDENCE_INTERVAL` and is excluded from continuity
claims.

## 2. Operational hardening

The Rust collector now separates `PROCESS_ALIVE` from
`EVIDENCE_FLOW_HEALTHY` and records:

- last raw stream update time and raw stream slot
- last finalized reconciliation time
- last durable partition append time
- last exact-complete partition time
- stream-connected state
- reconciliation work in flight
- independent age thresholds

The stream is forced to reconnect after 30 seconds with no raw update. Health
becomes stale/unhealthy at 90 seconds. The independent systemd watchdog checks
once per minute, requires a sustained 60-second failure, uses a 300-second
cooldown, permits at most three automatic restarts per hour, and writes an
immutable incident boundary before restart.

Two accounting defects found during live validation were fixed:

1. finality jobs incremented but never decremented the backlog;
2. snapshot jobs decremented without incrementing it.

RAII guards now decrement every accepted finality/snapshot job exactly once on
success, failure, duplicate, early return, or cancellation. The resulting
backlog oscillates with the intentional 12-second finality delay instead of
growing forever or underflowing.

Restart continuity is now keyed only to the last durably appended raw stream
slot. Auxiliary RPC snapshots/finality records can no longer advance the
resume cursor and cause skipped raw evidence. The legacy cursor remains synced
for external readers. Current raw and legacy cursor: slot 444836616.

## 3. Admission breakdown

The 119-mint reconciled sample is mutually exclusive:

| Class | Detail | N | Share of all |
|---|---|---:|---:|
| EXACT_ADMITTED | exact V1 replay | 64 | 53.78% |
| A inherently irrelevant | failed/unresolved creation | 11 | 9.24% |
| A inherently irrelevant | no successful trade transition in window | 16 | 13.45% |
| A inherently irrelevant | non-SOL quote launch | 2 | 1.68% |
| C semantic gap | Mayhem dynamic state missing | 24 | 20.17% |
| C semantic gap | reserve/fee semantics mismatch | 2 | 1.68% |
| B evidence collection failure | current sample | 0 | 0.00% |
| D temporary reconciliation | current sample | 0 | 0.00% |

Overall exact admission remains 64/119 = 53.78%. Among the 90 observations
not classified inherently irrelevant, current exact coverage is 64/90 =
71.11%. No historical semantic exclusion is currently recoverable into the
SOL exact population, so the post-fix admitted count remains 64.

## 4. Mayhem and newer schemas

### Mayhem

The immutable transaction ledger contains 5,126 finalized Mayhem trade events
across all 24 excluded Mayhem mints. It exactly reproduces 2,215 individual
ordinary-compatible transitions, but 2,911 successful transitions cannot be
reproduced by the ordinary constant-product reserve model. Failed transactions
remain zero-state mutations.

The missing evidence is the time-ordered Mayhem-owned dynamic virtual-parameter
account state (including the Mayhem state/token-vault semantics) at each
transition. Current RabbitStream account subscriptions have yielded zero
account updates. Historical exact replay therefore cannot be recovered from
the current ledger alone.

A separately versioned `EXACT_CURVE_ECONOMICS_MAYHEM_V2` fail-closed extension
and cross-check audit were added without mutating V1. The collector now also
captures Mayhem-program transactions prospectively (over 1,200 filtered rows
observed after deployment), but exact Mayhem admission remains blocked until a
true Yellowstone account-state stream supplies the dynamic state writes.

### Non-SOL quote schema

The two unsupported-schema rows are valid USDC-quoted `CreateV2` launches, not
malformed records. A separately versioned
`EXACT_CURVE_ECONOMICS_QUOTE_V2` validator reproduces both finalized 27-account
`BuyV2` transitions exactly with zero failures. It records native USDC curve
economics and refuses SOL normalization without causal conversion evidence.
They remain outside the SOL-denominated discovery population.

## 5. Coverage bias

Classification: `MATERIALLY_BIASED`.

- Exact admitted: 64/64 Token-2022, 0 Mayhem.
- Excluded: 45 Token-2022, 10 unknown due to failed creation snapshots, 24 Mayhem.
- Median admitted first-event MC: 27.96 SOL; excluded: 55.92 SOL.
- Median admitted gross buy at 1 second: 0.2869 SOL; excluded: 0.
- Median admitted buyers at 1 second: 1; excluded: 0.
- Only one partial source hour is represented.

These are causal/pre-entry descriptions only. No outcome was used and no alpha
filter was created.

## 6. Storage cutover

The ledger was migrated to:

`/mnt/HC_Volume_106775198/pump-exact-evidence-ledger-v1`

The application path remains:

`/var/lib/pump-exact-evidence-ledger-v1`

via a persistent bind mount. The cutover used a live pre-copy, controlled
collector drain, final checksum rsync, zero-difference checksum dry run, atomic
bind mount, and health-verified restart. Immutable evidence IDs and files were
preserved. The root copy was removed only after successful post-start flow.

| Storage | Used | Free |
|---|---:|---:|
| Root | 27 GB | 49 GB |
| Hetzner volume | 28 GB | 66 GB |

The ledger currently uses 3.6 GB. No deleted-open ledger files exist. The
current conservative growth estimate is 25.65 GiB/day, based on very short and
irregular initial partitions. Volume-only runway is about 2.56 days at that
estimate. N30k needs an estimated 90.09 GiB with a 25% margin, so volume-only
N30k runway is not yet proven even though combined free capacity exceeds that.

## 7. N10k gate

The hourly gate is installed and cannot start research automatically. Current:

| Gate | Current | Required | Status |
|---|---:|---:|---|
| Exact admitted N | 64 | 10,000 | BLOCKED |
| Represented hours | 1 | 24 | BLOCKED |
| First-to-last create span | 0.146 h | 24 h | BLOCKED |
| Operational flow | healthy | healthy | PASS |
| Reconciliation | clean/current | clean | PASS |
| Admission bias | materially biased | understood and broad | BLOCKED |
| Mayhem exact replay | blocked | supported | BLOCKED |
| Quote-native validation | supported | supported | PASS |
| Volume runway to N30k | 66.04 GiB vs 90.09 GiB | sufficient | BLOCKED |

Observed early admission rate is 443.93 exact mints/hour, implying a provisional
22.38 hours to N10k. The 24-hour diversity gate means the earliest credible
decision is after at least 24 healthy hours plus reconciliation lag. This ETA
must be revised after multiple complete hours.

## 8. Verification

- Rust release tests: 16/16 pass on the VPS.
- Watchdog deterministic tests: 2/2 pass.
- Mayhem V2 deterministic tests: 3/3 pass.
- Quote V2 deterministic tests: 3/3 pass.
- N10k gate tests: 2/2 pass.
- Deployed binary SHA-256:
  `032c7a8fccf2b58405e670796c1c7a902fa73cbbc5c9c1bfbf1199a7eb3a8eab`
- Collector, watchdog, reconciler, and readiness timers: active.
- Ranker live: paused.
- Alpha scans started: zero.
- 8YCB changes: zero.

## Final classification

`PUMP_EXACT_LEDGER_OPERATIONALLY_HARDENED_ALPHA_DISCOVERY_BLOCKED`
