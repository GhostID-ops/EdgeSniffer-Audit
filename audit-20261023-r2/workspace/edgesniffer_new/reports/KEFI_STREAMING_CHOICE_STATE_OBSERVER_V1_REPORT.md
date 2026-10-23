# kEFi Streaming Choice-State Observer V1

Status: `CHOICESTATE_CAPTURE_INVALID`

## Scope and isolation

This implementation uses a separate low-priority, one-shot systemd unit. It
does not read promotion windows, PRE_H3 artifacts, outcome/PnL data, or the V8
capture root. V8 and the PRE_H3 timer remained active during both probes.

The observer receives the all-PumpSwap stream directly, keeps compact pool
state in memory, and writes only compressed decision-time choice snapshots. A
kEFi transaction is only a snapshot trigger: it does not alter observation,
state updates, or candidate membership.

## Implemented safeguards

- Strictly pre-action snapshots: the selected transaction is snapshotted before
  its pool state is updated.
- No continuity bridging: each invocation is a fresh observer epoch; it has no
  restart checkpoint and `Restart=no`.
- Bounded validation: 300 seconds, 100 MiB derived-output cap, 1.5 GiB memory
  cap, idle I/O, CPU weight 10.
- Deterministic state tests passed locally and on the VPS:
  - same input state produces identical vectors;
  - changing teacher identity does not change the public state vector;
  - future events cannot mutate an already serialized pre-action vector.

## Decoder-gap result

Two bounded live attempts were intentionally fail-closed:

| Attempt | Source frames | Pools reached | Failure |
| --- | ---: | ---: | --- |
| 1 | 17 | 14 | no deterministic wallet delta or instruction mint |
| 2 | 367 | 68 | no unique pool-instruction, wallet, or transaction balance-delta mint join |

The first repair added canonical token-balance owner/mint/raw-delta joins.
The second added a transaction-wide unique non-SOL delta fallback. Both were
insufficient for a routed multi-mint PumpSwap layout. The implementation did
not skip or sample the event; it stopped before claiming all-supported
coverage.

Failed validation receipts are retained on the hot volume under:

`/mnt/edgesniffer-kefi-hot/kefi-streaming-choice-state-observer-v1/validation-*`

## Required next engineering gate

Decode the remaining route using an explicit PumpSwap account-layout mapping
or a deterministic event-to-token-account relationship that identifies the
base mint per event in a multi-mint transaction. It must be regression-tested
against the failed layout before the observer is restarted. Until then, no
choice-set capture or selection modeling is scientifically valid.

## Result

`CHOICESTATE_CAPTURE_INVALID`
