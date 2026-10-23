# EdgeSniffer — current research audit snapshot (2026-10-23)

This is the current source/research snapshot, not a deployment and not a claim of
profitable live trading. The repository's other directories preserve an older audit.

## Start here

1. [Research task register](RESEARCH_TASKS.md): verified work, blockers, next task.
2. [Research decision log](RESEARCH_DECISIONS.md): hypotheses, experiments, results and limitations. Start with RD-016/RD-015.
3. [Current research code and tests](research/edgesniffer/): native transaction capture, opportunity denominator, independent burst/persistence studies, controls, flow diagnostics and raw quote-context work.
4. [Latest verification](research/edgesniffer/evidence/quote-context-verification-20261023-v1.json).
5. [Independent audit](independent-audit/report/) and [portable probes](independent-audit/portable_probes/).
6. [Research reassessment](workspace/edgesniffer_new/research/audit-reassessment-v1/assessment.md).

## Objective and current state

Discover a genuinely profitable, independently executable strategy efficiently.
Research only. Do not enable trading, restart services or run operational scripts.
Teacher-wallet behavior is a source of hypotheses, not a required signal input.

The current independent burst/persistence experiment preserves78 first-mint
opportunities, original controls and0.1/0.5/1 SOL scenarios. Forty-two seeds lack
eligible controls. Forty-five burst exit account observations exceed the original
one-second age threshold; unchanged state has not been independently certified.
Relative executed-flow continuation is an exploratory lead, not strategy PnL.

A separate60-second engineering canary captured7808 transactions,4487 raw account
writes and236 mint bootstraps. It does not replace or validate the original cohort.
A3h remains partial. Next is **A3h3: validate causal raw fee/mint/state context and
integer quote fixtures**, before own-impact/cost accounting and credible net returns.
Do not retune the frozen strategies to obtain positive historical PnL.

## Source layout and provenance

- `src/`, `scripts/`, `public/`, `rust-sidecar/`: current root application's working-tree source, with manifests.
- `research/edgesniffer/`: current canonical research implementation, contracts, tests and small evidence reports.
- `workspace/edgesniffer_new/`: current ignored EdgeSniffer workspace's selected source, research contracts and explanatory records; separate Rust sidecar/exact-ledger code. Original location was `.codex_tmp/edgesniffer_new`.
- `independent-audit/`: extracted supplied audit evidence, separate from current implementation.
- [Snapshot manifest](SNAPSHOT_MANIFEST.json): source/export hashes, exclusions and transformations. Source research commit: `504db40998b555894c1bcabd297949c63d639107`.

The working tree includes pre-existing uncommitted application changes. This
snapshot is **not** represented as source-to-deployed-VPS equivalence. Where
multiple source versions exist, use the decision log and explicit artifact hashes;
do not silently assume the oldest root bot is the current independent research lane.

No source Git history, environment files, wallet keys, dependencies, compiled
outputs, active CI workflows or large raw VPS captures were exported. Preserved
JSON evidence retains original provenance paths, some of which are only accessible
on the owner's machine/VPS. Historical material referring to missing datasets is
not a self-contained replay. Documentation workspace links were relocated in this
copy only. The code's local/VPS assumptions were not silently rewritten.

## Safe verification

From this directory, the current Python research suite uses the standard library:

```sh
python -m unittest discover -s research/edgesniffer -p 'test_*.py'
```

The real PDA derivation test also needs Node and an installed `@solana/web3.js`.
Set `TEST_WEB3_MODULE` to its existing absolute path. Otherwise that test may skip;
do not claim a full test pass without reporting skips. The export was tested with
the owner's existing SDK:76 tests passed, zero skips. The standalone capture crate
previously passed8 Rust tests and a release build on the VPS; these are historical
verification claims linked above, not instructions to begin collection or trading.

## Suggested audit prompt

Read the task register, recent decisions, independent audit and relevant source.
Act as an independent quantitative researcher and data-systems auditor. Our goal
is a repeatable independent after-cost edge, not infrastructure for its own sake.
Challenge our assumptions and identify the cheapest credible falsification tests.
Separate what existing evidence can answer from what needs new capture. Inspect
actual code and tests; do not treat passing tests, observed flow or paper returns
as profitability proof. Prioritize actionable findings with evidence, uncertainty
and minimal implementation changes. You are free to propose a stronger hypothesis
than the teacher fingerprint or burst/persistence. Do not enable trading.
