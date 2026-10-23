EDGESNIFFER INDEPENDENT AUDIT — EVIDENCE PACKAGE
Audit date: 22 October 2026

START HERE
report/EdgeSniffer_Independent_Audit.html is a readable offline copy of the
17-page PDF delivered separately. report/EdgeSniffer_Independent_Audit.txt is
plain text. The structured report, findings register, and proposed research
protocol are provided for traceability. The proposed protocol is NOT a
registered experiment, production configuration, or authorization to trade.

The main conclusion is that no supplied result establishes independent profit.
The highest-value next build is a causal opportunity/attempt ledger with shared
features, correct protocol/state semantics, complete attempts and inventory,
timer exits, and deterministic replay, followed by a one-second residual-edge
falsification and a small frozen native experiment.

CONTENTS
  detailed_workstreams/  Expanded native, scorer and economics source audits.
  portable_probes/       Offline scripts, references, exact path-only changes.
  verification/         Recorded portable-run outputs and test evidence.
  provenance/           Source hashes, inventory and snapshot comparison.
  report/               Consolidated report and proposed research protocol.
  package_manifest.json SHA-256 for each other file in this package.

REPRODUCE
Use the original EdgeSniffer source archive. It is not duplicated here. Extract
it so the chosen source-root contains scripts/ and research/. The archive uses
Windows-style path separators; use an extractor that normalizes backslashes
into directory separators if extracting on a POSIX system.

  python3 portable_probes/run_all.py \
    --source-root /absolute/path/to/extracted-source \
    --output-dir /absolute/path/to/audit-results

Read portable_probes/README.txt before running. No credentials, RPC, wallet,
funded account or network access is required. The supplied-source probes use
Python standard library modules. Outputs must be outside source and runner
directories. Python 3.12.14 was used for verification.

INTERPRETATION
All four portable probes reproduced exactly: governance 5, scorer 9, native 11,
economics 13 result groups. These 38 groups overlap and are NOT 38 independent
defects. Successful reproduction demonstrates original behavior, often an
asserted defect. After a correct repair, some assertions SHOULD fail or outputs
change: this is an evidence harness, not a pass-to-deploy test suite.

The original source remained byte-for-byte unchanged across all 573 manifest
files. Existing focused unittest suites passed 53 cases, plus a standalone
Yellowstone bridge integration check. These tests did not cover the newly
demonstrated invariants. Rust could not be fully built due to omitted local
modules/dependencies. Detailed records distinguish code facts, preserved report
facts, roadmap-only results, synthetic illustrations and proposed tests.

LIMITATIONS
Raw market tapes, deployed binaries/configuration, full economic outputs and
later cohort allocation manifests were not supplied. No historical PnL was
independently recomputed. No statement here estimates a defect's frequency or
realized PnL effect. Some bugs are optimistic; others are pessimistic or merely
invalidate the stated measurement. Size-impact examples are reference-only
illustrations using the internal quote helper, not version-specific protocol
certification. See each workstream for scope qualifications.

No source was patched, no secrets were requested/read, and no trades were sent.
Positive shadow results would still require independent execution evidence;
this audit does not recommend enabling live trading.
