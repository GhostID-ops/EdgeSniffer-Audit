# EdgeSniffer Infrastructure Audit Snapshot

This directory is a source-only snapshot prepared on 2026-09-16 for a
read-only infrastructure and research-throughput audit.

## Scope

- Active Python research, reconciliation, and materialization scripts
- Rust source and manifests for the sidecar and exact-ledger components
- Systemd units, timers, deployment helpers, and operational definitions
- Research contracts and compact report/documentation artifacts

## Intentionally excluded

- `.env` files and credentials
- API keys, private keys, tokens, and wallet secrets
- Raw canonical corpora, JSONL journals, RPC caches, databases, and live logs
- Rust `target` directories and other rebuildable artifacts
- Vendor trees, temporary repair workspaces, binaries, PDBs, and archives
- Large generated reports and historical VPS exports

The snapshot does not alter or restart any running service. It is intended for
code and architecture review, not for reproducing private datasets locally.

## Current operational context

The current 2CQ symmetric-choice pipeline has a frozen N350 development set
and is collecting a sealed N150 validation set. At snapshot preparation, the
committed status showed 412 online-eligible observations and 62 sealed
validation observations. Outcomes remained unread and submissions remained
zero.

Recent repairs represented in this snapshot include bounded incremental exact
reconciliation, indexed AllenHark arrival lookup, exact-signature Yellowstone
fallback with explicit same-host clock-domain calibration, and memory-bounded
hourly materialization.
