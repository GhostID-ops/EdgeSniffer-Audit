# Wallet Causal Audit Runbook

Updated: 2026-07-05

## Purpose

Audit one wallet at a time and determine whether its profitable entries are
explainable by information EdgeSniffer could observe before entry.

This runbook deliberately separates:

1. Source-wallet accounting.
2. Historical pre-entry reconstruction.
3. Same-slot transaction causality.
4. Launch creation and predecessor structure.
5. EdgeSniffer selection replay.
6. Market-wide precision and executable validation.

Do not skip directly from advertised wallet win rate to a trading profile.

## Cohort Order

Recommended order:

1. `2CQ`
2. `BWAM`
3. `FiFaw`
4. Compare those three scalper wallets.
5. Keep CyA in the separate runner lane.

8Ycby has already been audited. Its profitable entries predominantly contain
token creation and precede public swap flow, so it is retained as a
creation-time benchmark rather than the next generic sniffer template.

Addresses:

```text
2CQ=2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG
BWAM=bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa
FIFAW=FiFawHqxeTVBhv6YbqbLwDVuvokRpPUM1bNwAyxhGc6W
CYA=CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o
```

## Preflight

Run on the VPS:

```bash
cd /root/bot/FinalBot-cya
git pull origin main
npm install
npm run build
npm run test:wallet-history
npm run test:wallet-slot-order
npm run test:wallet-launch-origin
```

Confirm the Helius pool is configured without printing complete keys:

```bash
awk -F= '/^HELIUS_API_KEYS=/ {
  n=split($2, key, ",")
  printf "slots=%d", n
  for (i=1; i<=n; i++) printf " slot%d=...%s", i, substr(key[i], length(key[i])-3)
  print ""
}' /root/bot/FinalBot-cya/.env
```

Never place API keys directly in commands, reports, Markdown, commits, or chat
handoffs.

## Select One Wallet

Example for 2CQ:

```bash
cd /root/bot/FinalBot-cya

export AUDIT_WALLET=2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG
export AUDIT_LABEL=2cq
export AUDIT_TXS=10000
export AUDIT_SAMPLE=120
export AUDIT_MAX_MINT_TXS=1000
```

Change only `AUDIT_WALLET` and `AUDIT_LABEL` for the next wallet.

## Step 1: Reconstruct The Wallet Ledger

```bash
cd /root/bot/FinalBot-cya
npx tsx src/walletLedgerBucketAudit.ts \
  "$AUDIT_WALLET" \
  "$AUDIT_TXS" \
  "${AUDIT_LABEL}-ledger-${AUDIT_TXS}"
```

Inspect coverage:

```bash
node - <<'NODE'
const fs = require("fs");
const label = process.env.AUDIT_LABEL;
const txs = process.env.AUDIT_TXS;
const file = `data/${label}-ledger-${txs}.json`;
const report = JSON.parse(fs.readFileSync(file, "utf8"));
console.log(JSON.stringify({
  wallet: report.wallet,
  coverage: report.coverage,
  performance: report.performance,
  buckets: report.buckets
}, null, 2));
NODE
```

Do not proceed if:

- The wallet address is wrong.
- Almost no fills reconstruct.
- Closed campaigns are too few for a 120-row stratified sample.
- Entry signatures are missing.
- The ledger obviously merges unrelated inventory cycles.

## Step 2: Historical Pre-Entry Reconstruction

```bash
cd /root/bot/FinalBot-cya
npm run audit:wallet-history -- \
  "data/${AUDIT_LABEL}-ledger-${AUDIT_TXS}.json" \
  /root/bot/EdgeSniffer \
  "$AUDIT_SAMPLE" \
  "${AUDIT_LABEL}-history-${AUDIT_SAMPLE}" \
  "$AUDIT_MAX_MINT_TXS"
```

Read:

```bash
cat "data/${AUDIT_LABEL}-history-${AUDIT_SAMPLE}.md"
```

Required checks:

- Entry-anchor wallet match should be at least `80%`.
- Source-size and inferred-MC calibration should be close to the ledger.
- `entry_not_found`, `entry_decode_failed`, and `pre_entry_decode_gap` must be
  explained before profile interpretation.
- Current precursor scoring stays disabled by default to prevent historical
  look-ahead.
- If most rows are `same_second_only`, continue to slot ordering.
- If balanced winners and losses have strict pre-entry flow, the historical
  feature table is useful.

Do not interpret profile PF when the report marks profile replay invalid.

## Step 3: Resolve Same-Slot Causality

```bash
cd /root/bot/FinalBot-cya
npm run audit:wallet-slot-order -- \
  "data/${AUDIT_LABEL}-history-${AUDIT_SAMPLE}.json" \
  /root/bot/EdgeSniffer \
  "${AUDIT_LABEL}-slot-order-${AUDIT_SAMPLE}"
```

Read:

```bash
cat "data/${AUDIT_LABEL}-slot-order-${AUDIT_SAMPLE}.md"
```

The audit counts only:

- Lower slots.
- Lower transaction indexes inside the same finalized block.

Later same-slot transactions are post-entry and excluded. Unknown signatures
remain unknown.

Decision:

- Balanced proven-prior winners and losses: compare features and profile gates.
- Proven-prior flow concentrated in losses: public confirmation is probably
  late or adverse for this wallet.
- Profitable entries precede all swaps: continue to launch-origin analysis.

## Step 4: Audit Launch Origin

The historical artifact already contains origin metadata.

```bash
cd /root/bot/FinalBot-cya
npm run audit:wallet-launch-origin -- \
  "data/${AUDIT_LABEL}-history-${AUDIT_SAMPLE}.json" \
  "${AUDIT_LABEL}-launch-origin-${AUDIT_SAMPLE}"
```

Read:

```bash
cat "data/${AUDIT_LABEL}-launch-origin-${AUDIT_SAMPLE}.md"
```

Classify the wallet:

- `entry creates` dominant: atomic creator/first-buyer lane.
- Prior creation immediately precedes entry: creation-triggered sniffer lane.
- Repeated profitable creators/predecessors: identity candidate requiring
  forward validation.
- Old/unresolved creation with first burst at entry: possible dormant-token,
  private-alert, or off-chain trigger lane.
- No stable origin pattern: do not invent one from one-off identities.

## Step 5: Optional Retained EdgeSniffer Cross-Reference

Use only when the wallet campaigns overlap retained EdgeSniffer timestamps:

```bash
cd /root/bot/FinalBot-cya
npm run audit:wallet-edge-xref -- \
  "data/${AUDIT_LABEL}-ledger-${AUDIT_TXS}.json" \
  /root/bot/EdgeSniffer \
  "${AUDIT_LABEL}-edge-xref"
```

Read the coverage section first:

```bash
cat "data/${AUDIT_LABEL}-edge-xref.md"
```

If `campaignsInsideEdgeWindow=0`, stop. Zero matches are not detector failure.

## Step 6: Record A Wallet Decision

For every wallet, record:

- Ledger coverage and source outcome distribution.
- Entry-anchor match and decoder calibration.
- Strict versus same-second campaign counts by outcome.
- Proven-prior versus post-entry same-slot flow.
- Creation-contained, prior-creation, and unresolved counts.
- Repeated identities with wins and losses.
- Whether current EdgeSniffer gates operate before or after the wallet.
- Whether the wallet belongs in the scalper, runner, creator, dormant-token, or
  unclassified lane.

Do not merge lanes because two wallets share a headline win rate.

## Step 7: Cross-Wallet Comparison

Only compare wallets after each individual audit is valid.

Look for features repeated across at least two independent wallets:

- Creation-to-entry slot distance.
- Entry transaction index.
- Source buy size.
- Inferred MC.
- Creator/deployer recurrence.
- Shared predecessor/funder identities.
- Buy acceleration before entry.
- Unique prior wallets.
- Sell pressure and net flow.
- Launch age or dormant duration.

Use leave-one-wallet-out validation:

1. Learn candidate rules from two wallets.
2. Test unchanged rules on the third.
3. Reject rules that only explain one wallet.

## Step 8: Add Market Negatives

Wallet-bought tokens measure capture, not precision.

From EdgeSniffer's forward local stream, retain:

- Every supported launch creation.
- First decoded transactions and their millisecond timestamps.
- Creator/deployer.
- Entry MC and source amount.
- First-slot transaction order.
- Unique buyers and buy/sell flow.
- Whether any cohort wallet entered later.
- Outcome paths at fixed horizons.

Sample contemporaneous launches no cohort wallet bought. Without these
negatives, a rule may capture wallet winners while buying thousands of trash
launches.

## Step 9: Executable Replay

Before a paper profile is promoted, include:

- `100/250/500/1000ms` delay.
- Local Rust-decoder availability.
- Price impact and priority/Jito costs.
- Failed and dropped transactions.
- Open-position and session exposure.
- Confirmation/final-entry behavior.
- Independent exits rather than source-wallet exits.

Current historical profile replay measures selection only.

## Step 10: Frozen Paper Test

Requirements:

- A predefined profile and sample target.
- No parameter changes during the window.
- Separate scalper and runner ledgers.
- Actual opens/closes, not only path simulation.
- Market-negative false-positive counts.
- Capped PnL, median ROI, stop-loss rate, drawdown, and concentration.

Do not consider live execution until the frozen paper lane remains positive
across multiple windows under stress assumptions.

## Failure Modes

- `Helius 429`: allow the configured key pool to rotate; do not paste keys into
  commands.
- `history_truncated`: increase `AUDIT_MAX_MINT_TXS` only for targeted mints or
  after the pilot proves value.
- `same_second_only`: run slot ordering; do not guess.
- `entry_decode_failed`: fix the decoder before interpreting outcomes.
- `profile replay valid=false`: do not quote PF or capture as strategy evidence.
- `creationUnresolved` dominant: classify the limitation before fetching deeper
  history.
- Moralis/Gecko failures: irrelevant to the causal pipeline unless candle/path
  enrichment is explicitly required.

## Cleanup Warning

Do not aggressively delete these while the cohort audit is active:

```text
data/wallet-pre-entry-cache
data/slot-order-cache
data/*-ledger-*.json
data/*-history-*.json
data/*-slot-order-*.json
data/*-launch-origin-*.json
```

They make corrected reports fast and prevent repeated API use.
