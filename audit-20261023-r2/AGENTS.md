# AGENTS.md

## Enduring research principles

- Objective: discover a genuinely profitable, independently executable strategy
  using trustworthy market evidence. Work is research-only; do not enable trading.
  Historical live-testing notes below are constraints, not authorization.
- Think as an independent senior quantitative researcher, market-microstructure
  specialist, and production-data auditor. Challenge architecture, assumptions,
  audit findings and previous conclusions, including your own. The teacher
  fingerprint is a hypothesis, not a mandatory winning strategy family.
- Prioritize research progress over complexity. Each major engineering task must
  name the concrete blocker it removes or experiment it unlocks. Always ask:
  "What is the cheapest, fastest credible experiment that could disprove our current hypothesis?"
- Distinguish essential correctness from optional improvements. Run narrower valid
  experiments with existing evidence when possible; state what requires new capture.
  Do not postpone all research until infrastructure is perfect.
- Treat positive PnL as untrusted until causal timing, complete trade accounting,
  costs, independent execution assumptions and prospective validation support it.
  Local test passes alone do not establish pipeline correctness or economic validity.
- Never optimize historical experiments merely to obtain positive PnL. Preserve
  failed experiments, negative results, rejected hypotheses and their reasoning.
- Keep at most ONE implementation task IN_PROGRESS. Define scope and acceptance,
  implement, integrate, run relevant tests, verify behavior and review the diff
  before marking VERIFIED. Record unfinished work; never silently abandon it.
- Maintain RESEARCH_TASKS.md with stable IDs, dependencies, acceptance criteria,
  verification commands/results/artifacts, blockers and the exact next action.
  Blocked work stays visible; proceed to independent work that can be completed.
- Maintain RESEARCH_DECISIONS.md as the research decision log: hypothesis,
  experiment, result, interpretation, decision and rationale for acceptance,
  rejection or uncertainty. Link existing evidence instead of duplicating reports.
- Preserve original evidence and frozen artifacts. Version results when methodology
  or implementation changes; retain superseded decisions and link their replacements.
- At session start or context recovery, read these instructions, the task register
  and recent decision entries, then inspect the relevant current source/evidence.
  Before handoff, update both records with progress, uncertainty and next actions.
- Every completion report must include **Completed**, **Verified** (concrete
  evidence), **Findings** (strategy implications), **Blockers**, and **NEXT
  ENGINEERING TASK** (exact action, rationale, what it unlocks, and completion
  criteria). Keep reports concise; never omit the next actionable step.

Continuity records for the current EdgeSniffer workspace are
`RESEARCH_TASKS.md` and `RESEARCH_DECISIONS.md` at this Git repository root.
Workspace-local files with those names are forwarding pointers. Keep changing task details and
results there, not here. Older wallet, VPS, strategy and service checkpoints below
are historical context to verify, not reliable declarations of current runtime.
These enduring principles supersede conflicting historical strategy preferences.

## Trading Bot Rules

- Never add or rely on Jupiter fallback unless the user explicitly asks for it.
- Prefer Rust executor paths over TypeScript transaction builders.
- Keep buy and sell execution local: Rust sidecar, ShredStream, gRPC, local inferred market-cap filters.
- Preserve latency. Do not add HTTP/API calls to the hot path without explicit approval.
- For PumpSwap, use canonical pool-state account layouts when available.
- Do not trust detector/source account metas for PumpSwap sells.
- RUST_ONLY_BUYS=true and RUST_ONLY_SELLS=true are the expected live-testing posture.
- JUPITER_FALLBACK_ENABLED=false is the expected live-testing posture.
- Before saying a bug is fixed, verify with build/tests and state whether it has been live-tested on VPS.

## EdgeSniffer 2CQ Research Context

- EdgeSniffer VPS path: `/root/bot/EdgeSniffer`; VPS SSH: `root@87.99.145.246`.
- Current paper lane under systemd: `edgesniffer-2cqv4.service`.
- v3 paper result checkpoint: `1015` closes, `+22.3594 SOL`, `PF 1.36`.
- v4 current core profile is `2cq-flow-projection-v4` in name/files, but as of the latest verified code it is effectively the core v3-style entry profile: `entryMarketCapUsd < 50000`, `entryContext.netBuySol60 >= 1`, runner-only. The dead-hour skip rule was defined in config but not enforced in `twoCqEntryFilterReason`.
- Do not claim the dead-hour filter is working unless `skip_utc_hour` enforcement is present in `src/lib/twoCqFrozenLane.ts`, the service has been restarted, and logs/events prove skipped hours are blocked.
- Dead-hour simulation on v4 data showed skipping `02,06,09,14 UTC` would have removed profitable trades: official baseline around `467` closes, `+14.6872 SOL`, `PF 1.53`; simulated kept non-skip hours around `384` closes, `+11.7000 SOL`, `PF 1.52`; removed skip hours around `83` closes, `+2.9872 SOL`, `PF 1.61`. Treat the original dead-hour thesis as rejected for current v4 data.
- Current v4 observed checkpoint from the latest pasted MC simulation: `580` observed closes, `+19.5353 SOL`, `PF 1.57`; official clean: `500` closes, `+17.1997 SOL`, `PF 1.59`.
- Best next filter thesis is market-cap tightening, not dead hours. `MC < 10k` kept about `447/500` official clean closes with `+16.7043 SOL`, `PF 1.64`; `MC 2k-3k` has higher PF but much lower volume and higher overfit risk.
- Keep v4 paper running unchanged while collecting more data unless explicitly asked to build a separate v5 profile. Prefer analysis scripts before changing the running scanner.
- Any live trading experiment should be treated only as dust-size execution calibration, ideally on a separate branch, with a separate wallet, `maxOpen=1`, `maxSession=1`, hard loss/session caps, Rust-only buys/sells, and Jupiter fallback off.

## EdgeSniffer Strategy North Star

- Primary goal: build a sniffer that identifies early tokens with positive expected scalp/runner opportunity. Do not drift into unrelated profile stacking unless the user explicitly changes the objective.
- The current primary research anchor is teacher-wallet reverse engineering, starting with the 2CQ low-MC organic impulse fingerprint. Treat other wallets or cohorts as separate named siblings, not replacements for the core thesis.
- The sniffer objective is token identification. Live profitability comes only after paper/live-parity evidence, Rust-only execution telemetry, and dust-size validation.
- Mayhem tokens are excluded. Mayhem logic is a safety veto, not an alpha source. Keep Mayhem checks fail-closed and outside the hot path when retrying or reclassifying unavailable data.
- Preserve latency. Do not add HTTP/API calls, slow lookups, or remote dependencies to the buy/sell hot path without explicit user approval.
- Expected live-testing posture remains `RUST_ONLY_BUYS=true`, `RUST_ONLY_SELLS=true`, and `JUPITER_FALLBACK_ENABLED=false`.

## Anti-Drift Rules

- Before proposing or building a new trading profile, state the active hypothesis, the evidence that supports it, and the evidence that would invalidate it.
- Do not blend 2CQ, token2022, missing-MC, broad flow, or other wallet leads into one unnamed strategy. Use explicit profile names and keep side-by-side reports.
- If a profile fails due to translation/execution reality, mark it as research-only or rejected instead of patching layer by layer.
- Reports should include sample size, PnL, profit factor, win rate, drawdown/loss concentration when available, and the specific next gate.
- Promotion order is: offline fingerprint -> prospective paper capture -> quote/live-parity paper -> dust execution calibration -> larger live sizing. Skipping gates requires explicit user approval.

## August 4 In-Place Reset

- Continue in this repo. Do not start a new workspace unless the current repo becomes technically blocked.
- Keep the execution/data infra; clean the strategy surface. The mess is strategy sprawl, not the Rust/ShredStream/gRPC foundation.
- Current active VPS service surface should stay small: 2CQ organic attribution, unavailable retry/promoter, unavailable recovered direct-entry shadow, corrected lowdom shadow, low-MC reset quote/paper, 6Fy research shadows/stream, the program-wide source feed, and dashboard.
- Do not restart old winner-bridge, broad/token2022, CyA sidequest, canonical/layer, alpha-forward, or live dust services unless explicitly requested.
- No live dust service should be armed from reset leads until the matching live-parity shadow has a meaningful fresh sample and positive PF.
- Main active proof lane is `/var/lib/edgesniffer-2cq-unavailable-recovered-direct-entry-shadow-v1`: clean-after-retry candidates, forced direct curve entry, `trail20_12_sl20_120s`, shadow-only.
- Secondary scalper proof lane is `/var/lib/edgesniffer-2cq-corrected-mayhem-lowdom-55-7-net8-tp8-v1-shadow`: MC 5.5k-7k, net 8-12, low sell pressure, dominance 0.20-0.42, shadow-only.
