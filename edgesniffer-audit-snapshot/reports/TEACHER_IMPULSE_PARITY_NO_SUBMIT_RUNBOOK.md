# Teacher Impulse Parity No-Submit Runbook

Date: 2026-08-26

Strategy: `teacher-impulse-parity-v1`

## Purpose

This path is for execution-parity proof only. It does not trade.

The only accepted input is immutable `DecisionInput` JSONL consumed by the Rust
`teacher_impulse_receipt` binary. Reports must consume `DecisionReceipt` output,
not legacy shadow summaries.

## VPS Paths

| Item | Path |
|---|---|
| Binary source | `/root/bot/EdgeSniffer/rust-sidecar` |
| Proposed producer unit | `/root/bot/EdgeSniffer/systemd/edgesniffer-teacher-impulse-parity-v1-producer.service` |
| Proposed receipt unit | `/root/bot/EdgeSniffer/systemd/edgesniffer-teacher-impulse-parity-v1-no-submit.service` |
| Candidate source | `/var/lib/edgesniffer-teacher-impulse-parity-v1/candidate-source.jsonl` |
| Decision inputs | `/var/lib/edgesniffer-teacher-impulse-parity-v1/decision-inputs.jsonl` |
| Decision receipts | `/var/lib/edgesniffer-teacher-impulse-parity-v1/decision-receipts.jsonl` |
| Epoch reports | `/var/lib/edgesniffer-teacher-impulse-parity-v1/reports/` |

## Candidate Capsule Contract

The producer must write one complete `DecisionInput` per candidate. It must not
write broad firehose traffic.

Required decision-boundary evidence:

- provider/source and raw record identity/hash;
- source slot as provenance only;
- local receive, decode-complete, and candidate-ready timestamps;
- transaction signature and source instruction data;
- candidate features available at candidate-ready time;
- canonical curve/pool state observed no later than candidate-ready time;
- explicit unavailable/unproven fields;
- Mayhem evidence, fail-closed if unavailable/unknown;
- modeled entry size, min-out, fees, tips, and slippage.

## Producer Command

The producer converts candidate rows into immutable `DecisionInput` capsules. It
does not evaluate profitability and it does not submit transactions.

```bash
cd /root/bot/EdgeSniffer/rust-sidecar
source /root/.cargo/env
cargo build --release --bin teacher_impulse_candidate_producer
target/release/teacher_impulse_candidate_producer \
  --input /var/lib/edgesniffer-teacher-impulse-parity-v1/candidate-source.jsonl \
  --output /var/lib/edgesniffer-teacher-impulse-parity-v1/decision-inputs.jsonl \
  --follow \
  --start-at-end \
  --poll-ms 25 \
  --create-output-dir
```

## Receipt Command

```bash
cd /root/bot/EdgeSniffer/rust-sidecar
source /root/.cargo/env
cargo build --release --bin teacher_impulse_receipt
target/release/teacher_impulse_receipt \
  --input /var/lib/edgesniffer-teacher-impulse-parity-v1/decision-inputs.jsonl \
  --output /var/lib/edgesniffer-teacher-impulse-parity-v1/decision-receipts.jsonl \
  --follow \
  --start-at-end \
  --poll-ms 25 \
  --create-input-if-missing
```

## Receipt-Only Report

```bash
cd /root/bot/EdgeSniffer/rust-sidecar
target/release/teacher_impulse_receipt_report \
  --input /var/lib/edgesniffer-teacher-impulse-parity-v1/decision-receipts.jsonl
```

## Epoch-Scoped Report

This is the replacement for mixed historical watchdog reports. It reads only the
current source/input/receipt spools and can tolerate missing files before the
source is connected.

```bash
cd /root/bot/EdgeSniffer/rust-sidecar
target/release/teacher_impulse_epoch_report \
  --candidate-source /var/lib/edgesniffer-teacher-impulse-parity-v1/candidate-source.jsonl \
  --decision-inputs /var/lib/edgesniffer-teacher-impulse-parity-v1/decision-inputs.jsonl \
  --decision-receipts /var/lib/edgesniffer-teacher-impulse-parity-v1/decision-receipts.jsonl \
  --config-epoch teacher-impulse-parity-v1 \
  --allow-missing
```

Proposed timer:

`edgesniffer-teacher-impulse-parity-v1-epoch-report.timer`

## Deployment Gate

Do not start the proposed unit until:

1. VPS build and tests pass.
2. Fixture receipt hash matches local.
3. A trusted raw replay fixture produces the same receipt hash locally and on VPS.
4. A prospective no-submit producer writes candidate capsules without enabling
   submit/live flags.
5. Receipts show Mayhem unavailable is fail-closed and source slot is provenance
   only.
