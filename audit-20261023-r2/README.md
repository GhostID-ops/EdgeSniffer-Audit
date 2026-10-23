# EdgeSniffer: current app, research history and audit evidence

Updated 2026-10-23, revision 2. Start here, not the older root bot run commands.
Research-only snapshot through source commit `0b9bfa0` and decision RD-021,
plus publication records and explicitly uncommitted application source.
No strategy has established repeatable, independently executable after-cost profit.
Do not run deployment, trading, wallet-management or service scripts during review.

## Reading order

1. [Research task register](RESEARCH_TASKS.md) and [decision log](RESEARCH_DECISIONS.md).
   Read newest decisions first, then RD-001 onward for corrections and failed hypotheses.
2. [Enduring principles](AGENTS.md), [independent audit](independent-audit/report/),
   [audit probes](independent-audit/portable_probes/), and
   [research reassessment](workspace/edgesniffer_new/research/audit-reassessment-v1/assessment.md).
3. [Current research pipeline](research/edgesniffer/), especially
   `burst_persistence_v1.py`, `quote_context_adapter_v1.py`,
   `state_continuity_v1.py`, `synchronized_context_v1.py`, `context_requests_v1.py`,
   their tests and versioned contracts. Resolve exact filenames from that directory.
4. [Current evidence](research/edgesniffer/evidence/): latest synchronized-context,
   verification and transaction reports are `20261023-v2`; retain v1 separately.
5. [Historical workspace research](workspace/edgesniffer_new/research/),
   [reports](workspace/edgesniffer_new/reports/), [scripts](workspace/edgesniffer_new/scripts/),
   [tools](workspace/edgesniffer_new/tools/) and [documentation](workspace/edgesniffer_new/docs/).
   These contain competing leads, negative results, exploratory simulations and
   older implementation assumptions. They are not all current or mutually comparable.
6. [Application source](src/), [root Rust sidecar](rust-sidecar/),
   [workspace Rust sidecar](workspace/edgesniffer_new/rust-sidecar/) and
   [exact-ledger source](workspace/edgesniffer_new/rust-exact-ledger/).

## What we actually know

The objective is useful 2CQ behavioral reverse engineering and an independent
strategy. Teacher annotations must not select the independent universe, signals
or controls. Current teacher address is
`9qz7cWBqYBP2TnGfzMmxX64G4PQip7HtogjPBBnMhWpw`; historical aliases/wallets are not
interchangeable. Keep teacher-selected, market-wide exploratory and confirmation
datasets separate.

The original 78 independent opportunities / 468 size-arm intentions remain
historical evidence. Complete causal fee/mint/state inputs are missing; a later
capture has zero temporal overlap and cannot repair original knowledge. Forty-two
opportunities lack original eligible controls. Do not backfill these into net PnL.

A secondary executed-flow study had 35 matched pairs, mean excess net buying flow
of +0.9365 SOL, versus -0.4809 for 11 persistence pairs. This is other traders'
flow, NOT our profit. It is short-period, exploratory and regime-concentrated.
Old favorable teacher-associated paper surfaces are not independent confirmation.

Quote math now has 16 independently reconstructed successful on-chain fixtures
across six supported variants. Mathematical correctness is separate from causal
availability and executable economics. Unsupported modes remain unresolved.

Latest bounded synchronized canary: 12,869 transactions, 7,354 raw account writes,
9 signal opportunities / 54 intentions; 6 controls and 3 missing. Twelve policy
abstentions, 42 unresolved economics, zero complete matched context chains.
Five targets have holder-reward mode and three have supply discrepancies, with
one overlap: seven of nine outside the current quote scope. The earlier canary's
15 successful teacher event labels are separate; this latest short period has zero.
Observer median lag was about 35 ms, versus 425 ms in another period: not a
controlled throughput benchmark. No large prospective cohort has been enabled.

The next research dependency is A3h4c: qualify holder-reward/supply fee semantics
and instruction-attributed writable touches using retained evidence, then verify
own-state impact, costs and complete outcome windows before expanding capture.
Writable permissions alone do not prove an account changed; an IDL alone does not
prove deployed behavior. Do not relax guards to manufacture a positive result.

## Frozen economic question

Compare the existing burst rule (entry receipt +1s) with the existing persistence
rule (confirmation +2s, entry +3s), same original opportunity population, 15s hold
plus 1s exit delivery, 0.1/0.5/1 SOL. Use contemporaneous eligible non-signal controls.
Preserve threshold/mark-age rules and every rejected, failed/non-executable and
unresolved intention. Quotes are not fills. Include fees, impact, network costs,
execution assumptions and causal input clocks before reporting net returns.
Read the actual frozen contracts rather than reconstructing rules from this summary.

## Evidence completeness and reproducibility

[Snapshot manifest](SNAPSHOT_MANIFEST.json) records included source/export hashes,
relocation, redactions and per-file/directory omissions. Original source workspace
`workspace/edgesniffer_new` is exported as `workspace/edgesniffer_new`.
Only documentation links are relocated; research JSON retains original provenance.
This is not a deployed-source equivalence claim or automatic synchronization.

The snapshot includes retained small historical reports, contracts, per-opportunity
rows and selected transaction fixtures. It is **not literally every byte**:
credentials/environment files, private keys, source Git history, dependencies,
builds, active CI workflows, hidden staging/cache directories, unallowlisted formats,
files over 8 MiB and full VPS raw captures are omitted. Full private chat transcripts
were not exported; persisted decisions are the available research history.
See [data limitations](DATA_AVAILABILITY.md). Missing raw inputs mean not every
historical experiment can be independently replayed from this repository alone.

Safe current test command, from this snapshot directory:

```sh
python -m unittest discover -s research/edgesniffer -p 'test_*.py'
```

Real PDA tests need Node and `@solana/web3.js`; set `TEST_WEB3_MODULE` to its absolute
installed module path. Export verification passed 123 tests with zero skips using
the owner's existing SDK. Dependencies are not bundled; a clean install/full app
build and every historical experiment were not rerun for this publication.

## Open-ended reviewer prompt

Read this guide, the task register, decisions, independent audit and relevant code
and evidence. Act as a skeptical senior quant researcher and systems auditor. Help
us find a genuinely repeatable independent after-cost edge efficiently. Challenge
our assumptions, teacher fingerprint and current priorities; think beyond the
existing hypotheses. What are we missing, what should we stop doing, and what are
the cheapest credible experiments that could disprove or substantiate the best
leads? Ground claims in specific files and evidence. Distinguish known facts,
untested assumptions and missing data. Do not mistake flow prediction, paper PnL,
or passing tests for profitability. You have creative freedom in your research
recommendations. Do not enable trading or silently tune frozen experiments.
