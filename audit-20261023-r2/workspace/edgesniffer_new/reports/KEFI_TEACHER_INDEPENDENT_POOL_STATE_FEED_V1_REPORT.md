# kEFi Teacher-Independent Pool State Feed V1

## Result

The requested all-supported-pool, teacher-independent feed was **not enabled
persistently**. A bounded, isolated live probe failed closed before it could
qualify as a canonical choice-set source.

The live V8 collector is intentionally unchanged. The probe was a separate
PumpSwap-only gRPC subscription with no tracked-wallet input, no promotion
input, no PRE_H3 reads, no outcome fields, and no raw transaction retention.

## Source Position And Contract

The probe subscribed directly to confirmed PumpSwap transactions, before the
V8 collector's tracked-wallet promotion logic. Its only durable payload was a
Zstd JSONL row per successfully decoded public transition:

- source instance, continuity epoch, durable receive sequence and receive time;
- raw source-frame SHA-256, exact log/event index, slot and signature;
- deterministic intrinsic event identity;
- pool, mint, action side, reserves, quote delta, fee tuple and layout;
- first public observation identity/time for that pool in the probe interval.

It contains no kEFi label, selection flag, future outcome, price, PnL, or
PRE_H3 material. Duplicate intrinsic identities are discarded only if their
canonical serialized rows agree; a conflict fails closed.

## Bounded Live Evidence

The decisive probe was:

`/mnt/edgesniffer-kefi-hot/kefi-teacher-independent-pool-state-v1/validation-pool-state-v1-1791870739164153174-pid750475`

| Measure | Observed |
|---|---:|
| Source interval | 0.236007952 seconds |
| Decoded rows before fail-closed stop | 89 |
| Distinct pools | 26 |
| Compressed bytes | 19,903 |
| Observed rows/second | 377.11 |
| Projected compressed bytes/day | 7,286,276,523 |
| Projected GiB/day | 6.786 |
| Target compact-storage budget | preferably under 100 MB/day |

The probe then encountered a PumpSwap event with no unique non-wrapped-SOL
mint account under the currently proven account/event join. It sealed a
`FAIL_CLOSED` validation manifest rather than inventing a mint.

This is a supported-layout decoder gap, but it is not the deciding storage
result: even the successfully decoded compact rows exceed the stated storage
budget by roughly two orders of magnitude at the observed burst rate. The
sample is short and burst-sensitive, so it is not a long-run capacity estimate;
it is nevertheless already far too large to justify enabling this design.

## Safety And Isolation

During all probe/build attempts the V8 source remained on source instance
`2026-10-12T14:59:43.875661353Z-pid533030`, continuity epoch `1`, with zero
reconnects, receive errors and continuity gaps. The collector, watchdog and
PRE_H3 timer remained active. No active service was restarted or reconfigured.

The deployed probe binary SHA-256 was:

`78948fabd85622663c76c413fe35d68c99491bab822d6d65da8499b60ab91772`

The probe source SHA-256 was:

`fce4ef9911a0b191935f17a9e7b0edf5664d4c58c36a1e7d2513ce5732394a58`

The choice-set materializer was upgraded to accept a Zstd canonical input via
the host `zstd` decoder, and its deterministic/fail-closed public test suite
passed. No real materializer integration was run because the feed never passed
its all-supported-pool provenance gate and there is no synchronized,
teacher-independent valid feed interval to link to a wallet action.

## Smallest Required Change

Do **not** turn this probe into a persistent JSONL stream. The smallest honest
next design decision is external to the materializer:

1. either provision a separately budgeted global canonical transition lake
   capable of several GiB/day, with a compact binary/columnar codec and
   independent archival policy; or
2. preregister a materially smaller, teacher-independent public candidate
   universe before collection, accepting that it is no longer a claim of
   `all_supported_pools_covered`.

A lossy sampling or a kEFi-centred capture cannot answer the present
"why A rather than other eligible tokens" question and must not be relabeled as
the requested choice-set feed.

LIVE_SOURCE_REQUIRES_MINIMAL_CHANGE
