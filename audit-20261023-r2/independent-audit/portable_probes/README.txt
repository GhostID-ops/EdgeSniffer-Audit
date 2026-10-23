EDGESNIFFER OFFLINE AUDIT EVIDENCE RUNNER
======================================

Purpose
-------
This directory reproduces four independently written audit probes against the
supplied EdgeSniffer source archive. It contains audit scripts and reference
outputs, not EdgeSniffer source, raw market data, credentials, or trading tools.

The scripts construct synthetic fixtures and call inspected local helpers.
The scorer probe also recalculates fields in the archive's preserved frozen
scorer and report. The outputs demonstrate implementation behavior. They do not
estimate defect frequency in production, validate actual market PnL, prove
profitability, certify current Pump protocol compatibility, or authorize trading.

Successful execution means the original counterexamples reproduced. It does
NOT mean the trading system passed correctness checks. Many assertions explicitly
assert the defective behavior found during the audit. Once that behavior is
repaired, an assertion failure or changed JSON result can be the expected outcome.
Investigate changed results instead of treating this runner as a deployment gate.

Requirements and command
------------------------
Use Python 3.10 or newer; the audit verification used Python 3.12.14. The runner
and invoked probes require Python's standard library only for this supplied
source. No package installation, network access, RPC endpoint, wallet, signer,
environment file, or funded account is required.

Extract the audited source archive first. --source-root must identify the
directory containing scripts/ and research/, including:

  research/2cq-event-driven-fingerprint-freeze-v1/frozen-scorer.json
  research/2cq-event-driven-fingerprint-freeze-v1/report.json

From any working directory, run:

  python3 /path/portable_probes/run_all.py \
    --source-root /path/extracted-source \
    --output-dir /path/audit-results

On Windows, the equivalent single line is:

  py -3 C:\path\portable_probes\run_all.py --source-root C:\path\extracted-source --output-dir C:\path\audit-results

Quote paths containing spaces. The output directory must be outside both the
supplied source tree and this runner directory. Running again replaces the
runner's named output and log files in the chosen output directory.

Exit status 0 means all four probes completed and their parsed JSON outputs
exactly matched the preserved audit references. Exit status 1 means at least
one failed, wrote no valid result, or differed. Missing inputs and invalid
command arguments produce argparse errors. All four probes are attempted even
if an earlier probe fails. Read summary.json and each probe's stderr.txt.

Outputs
-------
  summary.json
      Return codes, exact JSON comparisons, changed result-group names, and
      references to each probe result and log. Result groups overlap in scope;
      their sum is not a count of independent defects or independent tests.

  source_file_sha256.json
      SHA-256 of source/scripts/**/*.py and the two required research JSON files.
      This records source provenance; it does not read runtime configuration,
      credentials, private keys, or market tapes.

  governance/probe_results.json, stdout.txt, stderr.txt
      Window truncation, contaminated controls, unenforced contract, search
      surface counts, and illustrative sample-size arithmetic.

  scorer/probe_results.json, stdout.txt, stderr.txt
      Scoring units, ranking ties, split overlap, feature clocks, control
      routing/overlap, and recalculation of the preserved assessment report.

  native/probe_results.json, stdout.txt, stderr.txt
      Replay inputs, failed transactions, deduplication/order, stale account
      state, missing sell flow, completed curves, temporal/provenance guards,
      and training-versus-adapter feature timing.

  economics/probe_results.json, stdout.txt, stderr.txt
      Missing attempts/inventory, quote boundaries, fees, own-state impact,
      partial sells, future-dependent exits, checkpoint scheduling, and
      unsupported exactness in the historical economics core.

Temporary synthetic JSONL fixtures are created using tempfile and removed when
each probe finishes. Python bytecode generation is disabled in child processes;
the runner does not alter the supplied source. The normal probes do not invoke
the production execution path, start the native sidecar, build Rust, contact a
network, or read deployment credentials. This is not a security sandbox: run it
only against the inspected source bundle or source you independently trust.
Arbitrarily changed imported Python modules could have new side effects.

Contents and preservation
-------------------------
probes/ contains copies of the four original audit scripts. Only source/output
path plumbing and corresponding docstrings changed. Fixtures, calculations,
and assertions were preserved. path_plumbing.patch shows every change, and
probe_provenance.json records original and copied script SHA-256 values.

reference_outputs/{governance,scorer,native,economics}.json are the preserved
outputs used for exact parsed-JSON comparisons, not newly generated outcomes.

reference_outputs/historical_economics_reference.json is an earlier separately
saved historical-core counterexample. It is reproduced by the economics result
group named historical_core_empty_future_still_exact; it is included to preserve
the audit trail and is not an additional independent probe.

reference_outputs/size_impact_examples_reference.json contains illustrative
internal-quote-helper calculations from an earlier ad hoc audit command. No
original standalone script was preserved for that calculation. This file is
REFERENCE ONLY: run_all.py neither regenerates nor validates it, and it is not
included in the reproduced-result count. It is not an observed trade result or
an independent certification of current protocol quotes.

Scope limits
------------
No raw native tapes or historical complete transaction logs are supplied to this
runner. It cannot establish historical capture coverage, real fill probability,
transaction ordering on chain, true fees paid, live-capacity bounds, incidence
rates, or prospective performance. Quote comparisons use the repository's own
integer helper where stated; they isolate internal inconsistency and do not
substitute for differential validation against version-matched on-chain events.
Fixtures do not exhaust decoder routes or protocol versions. Preserved research
report totals are recalculated from the bundled report, not independently rebuilt
from the absent original market evidence. These limitations also apply when all
references reproduce exactly.
