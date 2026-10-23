# 2CQ Reconciliation Throughput Remediation V1

Generated: 2026-09-17

## Scope

Outcome-blind infrastructure work only:

- classify deferred symmetric-choice observations
- add bounded reconciliation telemetry
- isolate offline research from realtime acquisition
- measure stage latency and resource pressure
- optimize only measured reconciliation bottlenecks

No strategy, eligibility, validation, outcome, or submission semantics changed.

## Deferred Population

Auditor snapshot:

- ONLINE_ELIGIBLE: 412
- DEFERRED: 264

Authoritative post-remediation SQLite state:

- ONLINE_ELIGIBLE: 417
- DEFERRED: 138
- TERMINAL: 6,018

Remaining deferred classification:

- FRESH_WAITING_SUPPORT_OR_FINALITY: 56
- STALE_RECONCILIATION_INCOMPLETE_RETRYABLE: 50
- WAITING_RECONCILIATION_STATUS: 32

The reduction was intentionally conservative. Five observations became eligible;
stale rows with proven exhausted or structurally incomplete exact evidence were
made terminal instead of being presented as recoverable backlog.

## Root Causes Proven

1. Unchanged incomplete reconciliation statuses could be cached indefinitely.
2. Noncanonical/fork observations were not separated cleanly from canonical gaps.
3. Old canonical signatures could outlive signature-status history even though an
   archival finalized transaction remained recoverable.
4. The 10-minute materializer override produced nearly continuous 11-15 minute
   full scans and delayed reconciliation behind a shared lock.
5. A route-specific offline batch could overlap reconciliation, causing memory and
   swap pressure.
6. Support reconciliation JSON-decoded millions of irrelevant evidence frames.

## Changes

- Added append-only per-stage reconciliation telemetry.
- Added deterministic transient retry, canonical-fork classification, archival
  transaction fallback, and evidence-fingerprint-aware retry exhaustion.
- Added bounded deferred classification and retry workers.
- Added lightweight terminal-state cleanup for exhausted/structural rows.
- Moved offline jobs into `edgesniffer-offline-research.slice` and retained
  realtime CPU/IO/memory priority.
- Changed the materializer cadence to 20 minutes after completion.
- Serialized route-specific materialization behind the shared research lock.
- Preserved framing and SHA-256 validation for every support frame while decoding
  JSON only for exact target signatures and slots.

## Measurements

Initial representative support scan:

- 2,223,644 frames
- 112.63 seconds
- about 19.7k frames/second

Final optimized support scan:

- 1,925,286 frames
- 49.53 seconds
- about 38.9k frames/second

Support-frame throughput improved by about 97 percent. The final bounded run took
146.40 seconds total, including 27.02 seconds for target scanning and 12.58 seconds
for canonical block RPC.

## Verification

- Reconciler deterministic tests: 11/11 pass locally and on VPS.
- Materializer tests: pass.
- Modified systemd units: `systemd-analyze verify` passes.
- Exact collector, capture-first observer, AllenHark observer, and shred proxy:
  active with zero restarts at final verification.
- Validation remained sealed.
- Outcomes read: 0.
- Submissions: 0.

## Storage and Resource Note

- Exact corpus is on the Hetzner volume, not root.
- Volume: 591 GB total, about 232 GB free.
- Root: 75 GB total, about 5 GB free.
- Existing 8 GB swap remains fully allocated from prior memory pressure. No swap,
  live-memory, or collector configuration was changed in this task.

## Verdict

The shortest defensible path was also the recommended path. It recovered valid
observations, removed false backlog, exposed the actual expensive stage, nearly
doubled that stage's throughput, and protected realtime acquisition without
weakening the research contract.

## Auditor Recommendation Disposition

| Audit recommendation | Disposition | Evidence / limitation |
| --- | --- | --- |
| Protect realtime acquisition | IMPLEMENTED ON SAME HOST | Realtime collector/observer/proxy have higher CPU/IO weights and reserved memory. No core service restart was required. This is cgroup isolation, not physical host separation. |
| Isolate offline research | IMPLEMENTED | Heavy research jobs run in `edgesniffer-offline-research.slice`; reconciliation/materialization use shared serialization. |
| Classify deferred observations | IMPLEMENTED WITH SNAPSHOT CAVEAT | Current population was classified and stale rows transitioned. The auditor's original 264 row identities were not preserved as an immutable manifest while live collection continued, so this is not a literal frozen 264-row join. |
| Add reconciler stage telemetry | IMPLEMENTED AND LIVE-PROVEN | Append-only telemetry records dispatch delay, runtime, CPU, RSS, paths/bytes, frame counts, scan/RPC/write stages, completion, and unresolved coverage. |
| Optimize only after measurement | IMPLEMENTED | Canonical RPC was parallelized only after it measured dominant; support JSON parsing was reduced only after support scans became dominant. |
| Trigger materialization after reconciliation | NOT IMPLEMENTED | Still a P3 improvement. Existing timer remains the fallback/publication mechanism. |
| Buy larger hardware immediately | NOT DONE | Evidence still does not prove CPU/RAM is the fundamental valid-N constraint. Current consolidated 16 GB host retains operational memory risk. |
| Rewrite SQLite / add Redis / Postgres | REJECTED | No query-plan evidence justified it. |

## Detailed Deferred Classification

The live database changed while remediation was running because collection was
intentionally left active. The sequence was:

1. Auditor snapshot: 412 eligible, 264 deferred, 5,493 terminal.
2. Current live population initially grew beyond that snapshot as new decisions
   arrived.
3. A first exact recovery of a 26-row blocked group produced 5 eligible and 21
   terminal decisions.
4. Changed-evidence retries reopened recoverable partitions.
5. Unchanged, old, exact-incomplete partitions became retry-exhausted only after
   deterministic attempts.
6. Canonical fork/nonfinalized observations were separated from missing canonical
   evidence.
7. Proven exhausted and structural rows were swept from deferred to terminal.

Final authoritative SQLite state at 2026-09-17 06:20 UTC:

| Status | N |
| --- | ---: |
| ONLINE_ELIGIBLE | 417 |
| DEFERRED | 138 |
| TERMINAL | 6,018 |
| Total | 6,573 |

Final deferred classes:

| Class | N | Interpretation |
| --- | ---: | --- |
| FRESH_WAITING_SUPPORT_OR_FINALITY | 56 | Normal fresh pipeline latency. |
| STALE_RECONCILIATION_INCOMPLETE_RETRYABLE | 50 | Existing evidence changed or has not exhausted deterministic recovery. |
| WAITING_RECONCILIATION_STATUS | 32 | Partition status has not yet been materialized. |

No terminal row was promoted to eligible by relaxing evidence requirements.

## Reconciliation Root Causes and Repairs

### 1. Unchanged incomplete cache starvation

Problem: an input fingerprint and compatible reconciliation version could cause an
old incomplete status to be reused forever.

Repair: incomplete statuses now distinguish retryable changed evidence from proven
unchanged retry exhaustion. A changed input fingerprint reopens reconciliation.

### 2. Canonical fork observations

Problem: realtime signatures absent from the finalized canonical block could remain
counted as unresolved finality gaps.

Repair: the reconciler checks canonical finalized block membership. A signature
absent from the canonical block and not finalized is recorded as a noncanonical
observation and never mutates canonical state.

Observed example: one partition separated 722 noncanonical observations and reduced
the unresolved population to 96.

### 3. Expired signature-status history

Problem: old signatures can disappear from `getSignatureStatuses` history even when
the finalized transaction is still available.

Repair: bounded archival `getTransaction(finalized)` fallback was added for exact
canonical signatures only.

Observed historical recovery examples:

- 2026/09/11/09: 357 of 511 recovered; 154 remained fail-closed.
- 2026/09/11/21: 1,824 attempted; 548 remained fail-closed.

### 4. Canonical block RPC serialization

Problem: fresh missing block slots were fetched serially and dominated an initial
measured run.

Initial representative run:

- runtime: 365.8 seconds
- canonical block RPC: 244.5 seconds
- finality RPC: 48.5 seconds
- peak RSS: approximately 2.24 GiB

Repair: bounded eight-worker canonical block recovery with the same persisted proof
and endpoint semantics.

### 5. Support-frame JSON parsing

Problem: every support frame was checksum-validated and JSON-decoded even though
only finalized transaction/block rows matching target signatures/slots mattered.

Repair: every frame is still read, length-checked, and SHA-256 checked, but JSON is
decoded only after exact target-signature/slot byte prefiltering.

Before:

- 2,223,644 support frames
- 112.63 seconds
- approximately 19.7k frames/second

After:

- 1,925,286 support frames
- 49.53 seconds
- approximately 38.9k frames/second

This is approximately a 97 percent support-frame throughput increase.

The final measured full invocation was 146.40 seconds and included:

- target scan: 27.02 seconds
- support scan: 49.53 seconds
- canonical block RPC: 12.58 seconds
- finality RPC: 0.02 seconds
- dispatch wait behind the materializer: 612.84 seconds (reported separately from
  process runtime)

## Scheduling and Isolation

### Realtime plane priorities

| Unit | CPUWeight | IOWeight | MemoryMin |
| --- | ---: | ---: | ---: |
| Exact evidence collector | 1000 | 1000 | 6 GiB |
| 2CQ capture-first observer | 900 | 900 | 64 MiB |
| AllenHark compact observer | 900 | 900 | 128 MiB |
| Shred proxy | 1000 | 900 | 256 MiB |

All four were active with zero restarts at final verification.

### Offline plane

Heavy jobs were moved into `edgesniffer-offline-research.slice` with aggregate
memory pressure controls. Reconciliation and symmetric materialization retain their
shared lock. The route-specific materializer now also takes that lock.

The materializer timer was changed from 10 minutes after completion to 20 minutes
after completion because full passes measured approximately 11-15 minutes and were
repeatedly delaying exact reconciliation.

One already-running route job overlapped the old reconciliation process, reached its
existing 15-minute timeout, and was terminated by systemd. Its future configuration
now:

- takes the shared research lock;
- allows 45 minutes including lock wait;
- remains outcome-blind/no-submit;
- was reset from failed state;
- passed `systemd-analyze verify`.

The new route scheduling configuration has not yet completed its next scheduled live
run. That is an explicit pending operational proof, not a claimed live-tested fix.

## Materializer State After Reconciliation Recovery

Latest completed pass:

- runtime: 762.98 seconds
- processed exact generations: 144
- ready work before hour batching: 1,050
- deferred by existing hour batch: 906
- online eligible: 417
- sealed validation eligible: 67 / 150
- validation sealed: true
- outcomes read: 0
- submissions: 0

This differs from the auditor's earlier empty-queue snapshot because reconciliation
recovery created new downstream work. The materializer is draining that work under
its existing one-hour-batch semantics. Those semantics were not changed in this
repair.

The lightweight terminal sweep ran after the status file was written, so
`status.json` temporarily reports 187 deferred while authoritative SQLite reports
138. The next normal materializer pass will republish the status file.

## Verification Matrix

| Check | Result |
| --- | --- |
| Reconciler deterministic suite | 11/11 pass locally and VPS |
| Materializer suite | pass |
| Systemd unit verification | pass |
| Optimized reconciler live invocation | pass |
| Core realtime service restarts | 0 |
| Exact eligibility weakened | no |
| Inferred timestamps introduced | no |
| Validation rows read | 0 |
| Outcomes read | 0 |
| Transactions submitted | 0 |
| Core realtime services restarted | no |

## Remaining Risks and Next Gates

### Necessary to monitor

1. The consolidated host still has 15 GiB RAM and 8 GiB swap fully allocated from
   previous memory pressure. The short post-change window is insufficient to claim
   OOM risk is permanently eliminated.
2. Root has only about 5 GB free. The unique exact corpus is correctly on the 591 GB
   volume with about 232 GB free, but root cleanup remains separate maintenance.
3. The next route-specific scheduled run must prove the new shared-lock/45-minute
   configuration in production.
4. The 50 retryable and 32 status-waiting deferred decisions should converge through
   normal bounded retries; they must not be assumed eligible.
5. After the recovered 906-item hour-batched queue drains, materializer service time
   and publication latency should be measured again before any event-driven trigger
   or batch redesign.

### Not justified now

- weakening exact evidence or symmetric-choice requirements;
- adding inferred timestamps;
- merging route cohorts;
- replacing SQLite/Postgres migration;
- adding broad parallel reconciliation workers;
- changing strategy/model/thresholds;
- buying hardware on the claim it will multiply scientifically valid N.

## Updated Bottleneck Assessment

Primary research-yield constraint:

`STRUCTURAL_ELIGIBILITY_LIMITED`

Confirmed infrastructure defect addressed:

`OFFLINE_WORKLOAD_CONTENTION_AND_STALE_RECONCILIATION_STATE`

Confirmed latency stage improved:

`RECONCILIATION_SUPPORT_SCAN_AND_CANONICAL_RPC`

Current downstream state:

`MATERIALIZER_DRAINING_RECOVERED_HOUR_BATCHES`

Current operational caveat:

`CONSOLIDATED_HOST_MEMORY_AND_ROOT_DISK_RISK_REMAINS`

The work should be judged as a successful narrow remediation, not as proof that the
entire pipeline is now latency-optimal or that valid N will grow dramatically faster.
