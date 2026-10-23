# kEFi Choice-Set Materializer V1 Report

## Scope

This task implemented and regression-tested a compact, deterministic
decision-time choice-set materializer. It performs no ranking, model fitting,
feature search, PnL/economic work, re-entry analysis, or PRE_H3 access.

## Implemented materializer contract

`tools/kefi_choiceset_materializer_v1.py` accepts only a canonical input
manifest that proves all of the following:

- the source is teacher-independent;
- it is not selected-centred;
- it covers all supported pools; and
- its JSONL hash and absolute input path match the manifest.

For each referenced teacher event, it snapshots compact state before applying
the teacher event itself. Candidate admission is mechanical: a previously
observed supported pool with nonzero pre-action reserves and valid canonical
order/provenance. It emits `ALL_ELIGIBLE_POOLS` and
`NEW_TO_OBSERVED_WALLET`, retains first-public-observation and last-event
provenance, and limits the output to 150 teacher selections and 100 MiB.

## Regression proof

`tools/test_kefi_choiceset_materializer_v1.py` passes and proves:

- deterministic rerun produces byte-identical choice-set JSONL;
- future events cannot change an earlier emitted set;
- changing the teacher transaction itself cannot alter its pre-action set;
- selected-centred input is rejected before materialization.

The earlier synchronized wallet snapshot test also passes:
`tools/test_kefi_choiceset_snapshot_audit_v1.py`.

## Real-input preflight

The available current market file remains:

`/mnt/edgesniffer-kefi-hot/kefi-capture-first-control-v8/market-deduplicated.jsonl`

It is explicitly promoted-window / selected-centred materialization. The real
preflight therefore correctly rejected it before reading teacher events or
emitting any candidate rows.

- Preflight root:
  `/mnt/edgesniffer-kefi-hot/kefi-choice-set-audit-v1/materializer-preflight-20261013T051500Z`
- Rejected source-manifest SHA-256:
  `bedd49ebe6737a343776ca7a00484097387d068bc92b162b0a8de8262c131d65`
- Rejection stderr SHA-256:
  `5da564f2d55544e55a40977fd9082ac7417380cb9d37a959c0d2b7454b143d8f`
- Output candidate rows: `0`
- New raw bytes copied: `0`
- Derived choice-set bytes: `0`

## Coverage and limitation

The frozen wallet snapshot has 220 `FIRST_OBSERVED_ENTRY` rows, enough to
respect the maximum-150-event bound after a valid independent intersection.
However, no retained non-PRE_H3 input supplies both:

1. every supported contemporaneous pool; and
2. its ordered pre-action state at the kEfi decision moment.

The live V8 source currently persists its compact health/candidate records and
kEfi-centred promotions. It does not retain the required teacher-independent
all-pool canonical event stream. PRE_H3 has a canonical export but is sealed
and explicitly excluded from this task.

Consequently, zero teacher selections currently have valid canonical public
coverage for this materializer, zero have valid choice sets, and no candidate
set-size distribution or chosen-token admission coverage can be reported.

## Exact next capability

The smallest missing capability is a compact, append-only,
teacher-independent per-supported-pool canonical event/state stream produced
from the live source before any kEfi-specific promotion decision. It must
carry source instance, continuity epoch, durable writer sequence, event/frame
identity, first public observation, and supported pre-action reserve/layout
state. It should feed this materializer directly and must not copy raw
partitions or use PRE_H3 artifacts.

No such writer was launched here because the requested bounded task required
using existing canonical evidence and forbade expanding into a new data lake.

CHOICESET_SOURCE_COVERAGE_INSUFFICIENT
