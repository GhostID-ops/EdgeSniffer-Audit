# 2CQ new-wallet lane-wiring and resource audit — 2026-10-22

Audit target: `9qz7cWBqYBP2TnGfzMmxX64G4PQip7HtogjPBBnMhWpw`

VPS checked: `159.195.207.109` at approximately `2026-10-22 16:04Z`.
The audit was read-only with respect to evidence. The only operational changes
were stopping/disabling duplicate or legacy materializers and parity/economics
scans whose files were preserved. Trading remained disabled.

## Finding 1 — the wallet collector is capturing activity

The target-wallet finalized source was advancing. The current derived target
state artifact contained:

| Stage | Observed rows |
|---|---:|
| Exact finalized Pump wallet events | 6,034 |
| Wallet buys | 1,181 |
| Wallet sells | 4,853 |
| Unique mints | 1,145 |
| Events with native receipt clocks | 2,431 |
| Native receipt join signatures | 2,844 |
| Native target transactions | 7,285 |

This disproves the hypothesis that the confirmed wallet is inactive or absent
from the VPS capture path.

## Finding 2 — the active 159-window economics lane is not a 2CQ-wallet lane

The active native-boundary replay service consumes:

`/var/lib/edgesniffer-teacher-impulse-parity-v1/normalized-observations.jsonl`

It does not consume the target-wallet finalized source or the target-wallet
native transaction tape. The resulting market-window file had:

- `159` windows;
- `49` contemporaneous controls;
- route `PUMP_BONDING_CURVE_TOP_LEVEL` for all inspected windows;
- `0` rows containing the confirmed target wallet;
- `teacherWalletMatched=false` on the inspected rows.

This is a legitimate independent market-wide discovery lane, but it is not a
2CQ reverse-engineering result. Its PnL and negative surfaces cannot be used
to conclude that the 2CQ-derived strategy is profitable or unprofitable.

The current lab contract explicitly says that teacher-wallet observations do
not create an independent signal. The problem is therefore lane semantics,
not a missing wallet feed: the independent lab currently tests generic market
filters (`ALL_EXACT_ROUTE`, flow, reserve, and impact filters), rather than a
market-wide implementation of the 2CQ event-driven fingerprint.

## Finding 3 — the target-wallet native state bridge is still clock-only

The target-wallet bridge reports:

- `6,034` exact wallet events materialized;
- `2,431` receipt-clock joins;
- `0` native state rows;
- `native_state_status=NOT_PRESENT_RECEIPT_SOURCE_HAS_CLOCK_ONLY`;
- `parity_status=BLOCKED_NATIVE_STATE_NOT_CARRIED_BY_RECEIPT`;
- `economics_input_allowed=false`.

This is a real state-lineage blocker for target-wallet live-parity economics.
It is separate from wallet capture and separate from the market-wide native
replay lane.

## Finding 4 — resource contention was a real collection threat

At the audit start, overlapping historical jobs caused global OOM pressure.
Observed resident memory included approximately:

- same-source native materializer: `6.9 GiB`;
- causal bridge: `5.1 GiB`;
- older native materializer: `4.1 GiB`;
- native timing telemetry gate: `3.8 GiB` and repeated OOM restarts;
- additional parity/control/economics scans using several more GiB.

The kernel killed Python jobs, the dashboard, and the native timing telemetry
service. The following duplicate/legacy jobs were stopped/disabled with files
preserved: old native materializers, causal replay bridge, mismatch
adjudicator, crash-looping timing telemetry, old Pump controls, generic legacy
strategy refresh, old replay/parity/state bridges, and the old economics gate.

The active five-service lane remained running with zero restarts and exit status
zero. Memory recovered to approximately `14 GiB available` at the final
check.

## Finding 5 — ShredStream is advancing but has reconnect telemetry

The source health file reported:

- upstream status `ADVANCING`;
- upstream entries `53,301`;
- normalized events written `4,842`;
- dropped events `0`;
- writer errors `0`;
- stale timeouts/reconnects `25`;
- latest upstream event age `0 ms`;
- latest normalized event age approximately `86 s` at the check.

The upstream is live, but reconnects and the normalized-event age must remain
monitored. This is not evidence that the target-wallet finalized collector is
missing activity; that collector is a separate RPC source and was advancing.

## Decision and next gate

The current 159-window market-wide result is retained as independent generic
discovery evidence, but is explicitly not a 2CQ strategy result. The next
research build is a separate, named 2CQ fingerprint-to-market lane:

1. use the preserved 2CQ wallet tape only to define the development fingerprint;
2. compute the fingerprint from market-wide state available at native receipt;
3. apply the frozen one-second delay, executable economics, controls, and sizes;
4. keep it separate from target-wallet teacher evidence;
5. validate any candidate only on a fresh prospective cohort.

No strategy is frozen, no evidence is mixed, and no execution is enabled by
this audit.
