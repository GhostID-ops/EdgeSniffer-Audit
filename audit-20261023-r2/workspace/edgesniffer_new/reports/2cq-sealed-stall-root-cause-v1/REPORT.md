# 2CQ Sealed Stall Root-Cause Audit V1

## Verdict

The two-day sealed-observation stall was real. It was not caused by low 2CQ
activity, insufficient CPU/RAM, or an unreconciled raw backlog.

The primary cause was a causal-source coupling defect:

1. AllenHark exact-signature arrival coverage deteriorated sharply.
2. The symmetric choice-universe builder still required AllenHark timing for
   CREATE and prior same-mint transactions.
3. Yellowstone exact-signature timestamps were already stored, but fallback
   was used only for the selected teacher buy rather than the full symmetric
   universe.
4. Consequently, otherwise reconstructable decisions were rejected as having
   a missing selected mint or no contemporaneous alternative.

Two secondary defects amplified the stall:

- capture-all recovery was capped per run, but older rows could be terminalized
  even when their exact-signature lookup had never been attempted;
- the wallet signature ledger assumed finalized RPC history was strictly
  append-only and aborted on legitimate late-indexed same-slot signatures.

## Evidence

### AllenHark coverage collapse

Deterministic canonical samples showed approximate AllenHark coverage of:

| UTC day | Coverage |
| --- | ---: |
| 2026-09-11 | 100% |
| 2026-09-12 | 100% |
| 2026-09-13 | 100% |
| 2026-09-14 | 100% |
| 2026-09-15 | 47.25% |
| 2026-09-16 | 15.58% |
| 2026-09-17 | 8.56% |
| 2026-09-18 | about 5% |

For the post-Netcup interval:

- canonical successful Pump transactions: 23,748
- AllenHark exact-signature matches: 1,108
- AllenHark coverage: 4.6657%
- canonical CREATE transactions: 252
- AllenHark CREATE matches: 13
- CREATE coverage: 5.1587%

The collapse began before the VPS migration. Netcup was therefore not the
cause. Proxy configuration matched the provider's documented endpoint and
region. The remaining provider path showed severe FEC/reconstruction errors.

### Exact evidence was already available

The exact evidence ledger retained Yellowstone protobuf transaction receipts
with:

- exact transaction signature;
- exact observed slot;
- local receive timestamp;
- canonical finalized transaction/state evidence kept separately.

Same-signature calibration contained:

- 1,438 valid anchors in the old-VPS clock epoch;
- 36 valid anchors in the Netcup clock epoch;
- an explicit invalid migration gap between clock domains.

This made a causal, non-inferred fallback possible without weakening exact
signature or exact slot requirements.

## Repairs

### Full-universe calibrated source fallback

Arrival-source precedence is now:

1. AllenHark exact-signature arrival;
2. Yellowstone exact-signature local arrival inside an independently calibrated
   host clock epoch.

Every Yellowstone fallback still requires:

- exact signature equality;
- observed slot equal to canonical finalized slot;
- receipt timestamp inside a calibrated clock domain;
- canonical success, ordering, reserve state and event decoding from finalized
  evidence, not from the realtime source;
- explicit source provenance on CREATE and state events.

No timestamp is inferred or synthesized.

### Candidate aging

A row can no longer become terminal merely because it is old while waiting
behind the bounded capture-all lookup cap. Terminal missing-candidate status is
allowed only after the exact lookup was actually attempted.

### Late finalized signature backfill

New finalized signatures surfaced inside an already-seen dense slot are merged
with explicit `historicalBackfillN` provenance. Existing-signature slot or
block-time contradictions still fail closed.

## Verification

All affected unit/integration suites passed locally and on the VPS:

- signature population tests: 11/11;
- indexed causal state-plane test: PASS;
- prospective symmetric materializer test: PASS;
- Python compile validation: PASS.

The deployed materializer progressed as follows:

| Stage | Sealed validation N |
| --- | ---: |
| Before repair | 133 |
| First calibrated recovery batch | 139 |
| Second calibrated recovery batch | 148 |
| Subsequent backlog batch | 156 |

The N150 gate is therefore complete. The 150th validation observation occurred
at 2026-09-16T07:07:36Z and remains unread.

Latest integrity state:

- online eligible: 506 = 350 development + 156 validation;
- database quick check: `ok`;
- outcomes read: 0;
- validation rows read: 0;
- submissions: 0;
- validation sealed: true;
- capture-first collector active, zero restarts;
- exact ledger active, zero restarts;
- materializer timer active.

In one recovered batch, Yellowstone supplied 108,242 exact-signature universe
arrivals while AllenHark supplied 17,019. That is why the repaired path can
materialize valid symmetric universes again despite the provider degradation.

## Residual Issues

AllenHark's underlying FEC/reconstruction coverage remains degraded. It should
be treated as a provider/transport issue and reported separately, but it no
longer blocks this research where calibrated Yellowstone evidence exists.

The exact reconciler is also expensive: one observed four-partition run held
the shared lock for roughly 18 minutes and performed very large temporary I/O.
That is a real throughput optimization target, but it was not the cause of the
two-day zero-sealed condition. It delayed lock handoff; it did not erase the
causal evidence.

## Final Classification

`2CQ_SEALED_STALL_ROOT_CAUSE_FIXED_N150_COMPLETE`

