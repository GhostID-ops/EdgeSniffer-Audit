# Wallet Cohort Research Guide

Continuation documents:

- `EDGESNIFFER_RESEARCH_HANDOFF_2026-07-05.md` preserves the research history,
  current evidence, decisions, and a ready-to-paste new-chat prompt.
- `WALLET_CAUSAL_AUDIT_RUNBOOK.md` is the one-wallet-at-a-time procedure for
  2CQ, BWAM, FiFaw, and later cohort wallets.

## Objective

Build two independently evaluated research lanes:

- `scalper`: frequent short holds and small exits, represented by the four supplied high-win-rate wallets.
- `runner`: lower hit-rate, fat-tail exits, represented initially by CyA.

Do not combine these lanes into one score or paper profile. The cohort is offline research and does not add HTTP calls to the Rust/gRPC trading hot path.

## Cohort

The tracked addresses live in `data/wallet-audit-wallets.json`:

- `bwam-scalper`: `bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa`
- `fifaw-scalper`: `FiFawHqxeTVBhv6YbqbLwDVuvokRpPUM1bNwAyxhGc6W`
- `2cq-scalper`: `2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG`
- `8ycby-scalper`: `8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ`
- `cya-runner`: `CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o`

The reported 24-hour PnL and win rates are discovery claims, not trusted labels. The audit reconstructs them independently.

## Helius Keys

In `/root/bot/FinalBot-cya/.env`, keep the primary key and comma-separated research pool:

```bash
HELIUS_API_KEY=PRIMARY_KEY
HELIUS_API_KEYS=SECOND_KEY,THIRD_KEY,FOURTH_KEY
HELIUS_API_POOL_INCLUDE_PRIMARY=false
HELIUS_KEY_COOLDOWN_MS=60000
HELIUS_KEY_MAX_ATTEMPTS=20
```

With `HELIUS_API_POOL_INCLUDE_PRIMARY=false`, audit key slots map directly to
the order in `HELIUS_API_KEYS`. The runner logs key slot numbers only. It never
writes key values to reports.

### Replace Slots 1 And 3 And Append A Key On The VPS

The commands below prompt silently, so API keys do not enter shell history.
They replace pool slots 1 and 3, remove any existing copy of the appended key,
append it once, and retain the other slots in their current order.

```bash
ssh root@87.99.145.246
cd /root/bot/FinalBot-cya

read -rsp "New Helius key for slot 1: " NEW_SLOT_1; echo
read -rsp "New Helius key for slot 3: " NEW_SLOT_3; echo
read -rsp "Helius key to append: " APPENDED_KEY; echo

ENV_FILE=/root/bot/FinalBot-cya/.env
POOL_LINE=$(grep -m1 '^HELIUS_API_KEYS=' "$ENV_FILE")
IFS=',' read -r -a KEYS <<< "${POOL_LINE#*=}"

if [ "${#KEYS[@]}" -lt 3 ]; then
  echo "Expected at least three Helius pool keys; no change made." >&2
else
  KEYS[0]="$NEW_SLOT_1"
  KEYS[2]="$NEW_SLOT_3"
  FILTERED_KEYS=()
  for KEY in "${KEYS[@]}"; do
    [ "$KEY" != "$APPENDED_KEY" ] && FILTERED_KEYS+=("$KEY")
  done
  FILTERED_KEYS+=("$APPENDED_KEY")
  JOINED_KEYS=$(IFS=,; echo "${FILTERED_KEYS[*]}")
  sed -i.bak -E "s|^HELIUS_API_KEYS=.*$|HELIUS_API_KEYS=$JOINED_KEYS|" "$ENV_FILE"
  unset NEW_SLOT_1 NEW_SLOT_3 APPENDED_KEY KEY JOINED_KEYS
fi
```

Verify the slot count and key suffixes without printing complete keys:

```bash
awk -F= '/^HELIUS_API_KEYS=/ {
  n=split($2, key, ",")
  printf "slots=%d", n
  for (i=1; i<=n; i++) printf " slot%d=...%s", i, substr(key[i], length(key[i])-3)
  print ""
}' /root/bot/FinalBot-cya/.env
```

Confirm the expected live-testing posture, then restart the CYA service so it
reloads the environment:

```bash
grep -E '^(RUST_ONLY_BUYS|RUST_ONLY_SELLS|JUPITER_FALLBACK_ENABLED)=' \
  /root/bot/FinalBot-cya/.env
systemctl restart finalbot-cya
systemctl status finalbot-cya --no-pager
journalctl -u finalbot-cya -n 100 --no-pager
```

Expected values:

```text
RUST_ONLY_BUYS=true
RUST_ONLY_SELLS=true
JUPITER_FALLBACK_ENABLED=false
```

## Recovered Status: July 4, 2026

The recovered chat establishes this checkpoint:

- The CyA 1,000-transaction audit completed on the VPS.
- It reconstructed 99 closed campaigns from 1,000 transactions.
- Realized PnL was `+36.46 SOL`, win rate was `51.5%`, profit factor was
  `1.75`, and median holding time was about `20 seconds`.
- Median MFE was only `+1.68%`, while median MAE was `-29.63%`.
- A `+50%` path occurred in about `10.2%` of measured entries and a `+100%`
  path in about `5.1%`.
- The first 5,000-transaction attempt failed because a single Helius key
  reached its usage limit. The separate `: command not found` message was a
  paste/line-break error, not an audit bug.

This is evidence for a possible fast-exit plus fat-tail profile, not a finished
strategy. The sample is one wallet, one short window, has material drawdown and
outlier concentration risk, and has not yet been latency/impact stressed.

RabbitStream, gRPC, and ShredStream can collect new observations from now on.
They cannot reconstruct historical wallet balance and token deltas that were
not captured at the time. Use the Helius pool and cache for historical work;
use local streams for forward capture and millisecond replay.

## Next Steps

### 1. Update And Preflight The VPS

```bash
ssh root@87.99.145.246
cd /root/bot/FinalBot-cya
git pull origin main
npm install
npm run build

grep -E '^(HELIUS_API_POOL_INCLUDE_PRIMARY|HELIUS_KEY_COOLDOWN_MS|HELIUS_KEY_MAX_ATTEMPTS|RUST_ONLY_BUYS|RUST_ONLY_SELLS|JUPITER_FALLBACK_ENABLED)=' .env
```

Do not paste a key into the command line. `walletQuantAudit.ts` loads the
rotating pool from `.env`, marks quota-exhausted slots, and saves a partial
history cache after every page.

### 2. Verify Or Finish The CyA 5,000-Transaction Audit

First check whether the 5k report is already complete:

```bash
cd /root/bot/FinalBot-cya
node -e '
const fs=require("fs");
const p="data/cya-quant-5000.json";
if (!fs.existsSync(p)) {
  console.log("missing");
  process.exit(2);
}
const r=JSON.parse(fs.readFileSync(p,"utf8"));
console.log(JSON.stringify(r.sample,null,2));
if ((r.sample?.txsFetched ?? 0) < 4900) process.exit(3);
console.log("CyA 5k is complete; skip the rerun below.");
'
```

If this prints `CyA 5k is complete`, continue at step 3. It is the same report.
Only run the following command when the report is missing, invalid, or clearly
short of 5,000 transactions.

Run it in the background. Standard output goes only to the JSON temporary file;
warnings and key-slot events go to the log. The final JSON name appears only
after a successful run.

```bash
cd /root/bot/FinalBot-cya
nohup bash -lc '
  cd /root/bot/FinalBot-cya
  npx tsx src/walletQuantAudit.ts \
    CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o \
    5000 300 > data/cya-quant-5000.json.tmp &&
  mv data/cya-quant-5000.json.tmp data/cya-quant-5000.json
' > data/cya-quant-5000.log 2>&1 &
echo $! > data/cya-quant-5000.pid
```

Monitor and check completion:

```bash
tail -f /root/bot/FinalBot-cya/data/cya-quant-5000.log
ps -p "$(cat /root/bot/FinalBot-cya/data/cya-quant-5000.pid)" -o pid,etime,cmd
test -s /root/bot/FinalBot-cya/data/cya-quant-5000.json && echo "5k JSON ready"
```

If every pool slot is exhausted, update the pool and run the same command
again. The 5k partial cache resumes that same request. The completed 1k cache
does not seed a differently named 5k request.

Validate and print the useful summary:

```bash
cd /root/bot/FinalBot-cya
node -e '
const fs=require("fs");
const p="data/cya-quant-5000.json";
const r=JSON.parse(fs.readFileSync(p,"utf8"));
console.log(JSON.stringify({
  sample:r.sample,
  walletPerformance:r.walletPerformance,
  entryStats:r.entryStats,
  targetProbabilities:r.targetProbabilities,
  downsideProbabilities:r.downsideProbabilities,
  buckets:r.buckets,
  strategies:r.strategies,
  concentration:r.concentration
},null,2));
'
```

### 3. Run The Five-Wallet Pilot Cohort

The pilot runs each of the four scalper wallets at 2,500 transactions and 250
enriched entries. CyA stays at 5,000 transactions and 300 entries so the batch
can reuse the completed CyA 5k cache while producing one common cohort report.
This is the quota-conscious comparison pass. Do not jump directly to the 10k
config until the pilot proves all wallets parse correctly and share a useful
time window.

```bash
cd /root/bot/FinalBot-cya
bash scripts/startWalletAuditBatch.sh data/wallet-audit-wallets-pilot.json
```

Monitor it:

```bash
RUN_DIR=$(cat /root/bot/FinalBot-cya/data/wallet-audits/latest-run.txt)
tail -f "$RUN_DIR/runner.log"
cat "$RUN_DIR/state.json"
```

When finished:

```bash
RUN_DIR=$(cat /root/bot/FinalBot-cya/data/wallet-audits/latest-run.txt)
cat "$RUN_DIR/cohort-summary.md"
```

Promote to the full config only if the pilot has no parser failures, enough
closed campaigns, and an exact common comparison window:

```bash
cd /root/bot/FinalBot-cya
bash scripts/startWalletAuditBatch.sh data/wallet-audit-wallets.json
```

### 4. Keep Forward Stream Capture Running

Do not add an API call to the trading hot path. Do not change the bot's live or
simulation posture merely to run this research. Verify that the existing
EdgeSniffer event files are present and growing:

```bash
ls -lh \
  /root/bot/EdgeSniffer/data/launch-paper-signals.jsonl \
  /root/bot/EdgeSniffer/data/launch-paper-events.jsonl

wc -l \
  /root/bot/EdgeSniffer/data/launch-paper-signals.jsonl \
  /root/bot/EdgeSniffer/data/launch-paper-events.jsonl
```

Repeat `wc -l` later. Increasing counts confirm forward capture. These local
timestamps are what make the later 100-1,000ms replay possible.

### 5. Join The Cohort To Local Millisecond Events

After the pilot or full cohort finishes:

```bash
cd /root/bot/FinalBot-cya
RUN_DIR=$(cat data/wallet-audits/latest-run.txt)
npm run audit:wallet-latency -- "$RUN_DIR" \
  /root/bot/EdgeSniffer/data/launch-paper-signals.jsonl \
  /root/bot/EdgeSniffer/data/launch-paper-events.jsonl

cat "$RUN_DIR/latency-impact-replay.md"
```

Low match coverage means “insufficient evidence,” not zero slippage or zero
performance loss.

### 6. Decide What To Build Next

Do not tune EdgeSniffer thresholds from the CyA 1k result alone. The next code
change should follow the evidence:

1. If the scalper wallets agree out of sample, build a separate scalper score.
2. If CyA remains fat-tail positive after delay and impact, build a separate
   runner score.
3. If accepted trades look good but no market negatives exist, collect
   contemporaneous launches the cohort rejected before training anything.
4. If latency coverage is weak, improve local event retention/join coverage
   before changing execution.
5. Promote only a frozen paper profile that passes the decision gates below.

## Start A Full Cohort In Background

```bash
cd /root/bot/FinalBot-cya
npm run build
bash scripts/startWalletAuditBatch.sh data/wallet-audit-wallets.json
```

The launcher prints the run directory. Save it, or recover it with:

```bash
RUN_DIR=$(cat /root/bot/FinalBot-cya/data/wallet-audits/latest-run.txt)
echo "$RUN_DIR"
```

## Monitor

```bash
RUN_DIR=$(cat /root/bot/FinalBot-cya/data/wallet-audits/latest-run.txt)
tail -f "$RUN_DIR/runner.log"
```

Status without following the log:

```bash
RUN_DIR=$(cat /root/bot/FinalBot-cya/data/wallet-audits/latest-run.txt)
cat "$RUN_DIR/state.json"
```

Check the process:

```bash
RUN_DIR=$(cat /root/bot/FinalBot-cya/data/wallet-audits/latest-run.txt)
ps -p "$(cat "$RUN_DIR/runner.pid")" -o pid,etime,cmd
```

## Resume An Interrupted Run

The Helius transaction cache is saved after every page. Completed wallets are skipped when the same run directory is resumed:

```bash
cd /root/bot/FinalBot-cya
RUN_DIR=$(cat data/wallet-audits/latest-run.txt)
nohup npm run audit:wallet-cohort -- data/wallet-audit-wallets.json "$RUN_DIR" \
  >> "$RUN_DIR/runner.log" 2>&1 &
echo $! > "$RUN_DIR/runner.pid"
```

## OHLCV Provider Resilience

Entry-path enrichment uses Moralis first. Transient Moralis failures are retried three times. A repeated 5xx response or a permanent 401/403 quota response opens a circuit breaker for the remainder of that wallet audit and automatically falls back to GeckoTerminal. The final JSON records provider counts and the Moralis disable reason under `entryEnrichment`.
## Outputs

Every run writes:

- `runner.log`: complete human-readable process log.
- `events.jsonl`: structured batch/wallet lifecycle log.
- `state.json`: pending/running/completed/failed state.
- `<label>.quant.json`: campaign reconstruction, winners, losers, entry features, path metrics, and strategy summaries.
- `cohort-summary.json`: machine-readable common-window comparison.
- `cohort-summary.md`: readable scalper-versus-runner report.

Inspect the final report:

```bash
RUN_DIR=$(cat data/wallet-audits/latest-run.txt)
cat "$RUN_DIR/cohort-summary.md"
```

## Millisecond Latency And Impact Replay

Helius historical timestamps are not precise enough for a 100-1000ms replay. After the cohort run, join it to EdgeSniffer's millisecond stream events:

```bash
cd /root/bot/FinalBot-cya
RUN_DIR=$(cat data/wallet-audits/latest-run.txt)
npm run audit:wallet-latency -- "$RUN_DIR" \
  /root/bot/EdgeSniffer/data/launch-paper-signals.jsonl \
  /root/bot/EdgeSniffer/data/launch-paper-events.jsonl
```

Outputs:

- `latency-impact-replay.json`
- `latency-impact-replay.md`

The replay tests `100/250/500/1000ms` and one-way impact assumptions of `50/100/250/500bps`. It reports coverage. Low coverage means no conclusion; it must not be interpreted as zero performance.

## Decision Gates

Do not select a strategy from headline win rate. Require all of the following:

1. At least 200 independently reconstructed closed campaigns per wallet where available.
2. A shared comparison window of at least 24 hours.
3. Positive median ROI and profit factor above 1 after stress costs.
4. Acceptable worst-trade and drawdown behavior.
5. Profit not dominated by the top five trades.
6. Positive results at 500ms delay and a realistic impact assumption.
7. Similar performance across multiple wallets, not one wallet alone.
8. A frozen forward paper test on unseen launches.

## Path From Research To Sniffer

1. **Cohort truth set:** finish these five audits and validate the advertised behavior.
2. **Pre-entry feature dataset:** use only information observable before each source entry: route, MC, source size, buy acceleration, unique buyers, net flow, sells, wallet overlap, and launch age.
3. **Market negatives:** sample contemporaneous launches none of the cohort bought. Accepted trades alone cannot reveal the wallets' rejection rules.
4. **Separate models:** train/evaluate a scalper lane and runner lane independently.
5. **Leave-one-wallet-out validation:** learn on several wallets and test on a wallet excluded from training.
6. **Executable replay:** apply local stream timing, Rust-side fill assumptions, impact, priority fees, and failed-transaction costs.
7. **Frozen paper run:** no parameter changes until the predetermined sample is complete.
8. **Promotion:** only promote a lane that remains positive out of sample and under execution stress.

The immediate next milestone is not another EdgeSniffer threshold tweak. It is a completed, common-window cohort report followed by the latency-impact replay.

## Ledger-First Single-Wallet Audit

The ledger bucket audit is intentionally fast when the Helius transaction cache
already exists. It reconstructs fills and campaigns locally; it does not fetch
one candle path per token. Its JSON rows are the outcome-label layer, not the
complete detector audit.

Start with one wallet:

```bash
cd /root/bot/FinalBot-cya
npx tsx src/walletLedgerBucketAudit.ts \
  8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ \
  10000 \
  8ycby-ledger-10000
```

Cross-reference those labeled campaigns against every retained EdgeSniffer
signal and rejection:

```bash
cd /root/bot/FinalBot-cya
npm run audit:wallet-edge-xref -- \
  data/8ycby-ledger-10000.json \
  /root/bot/EdgeSniffer \
  8ycby-edge-xref

cat data/8ycby-edge-xref.md
```

The cross-reference reports:

- Retained EdgeSniffer data-window overlap and honest coverage.
- Strict pre-entry detections, same-second detections, and post-entry detections.
- Winner, scalp-win, runner, and loser pre-entry indicator medians.
- Current, aggregate-runner, and wide-scout profile replays.
- Winner capture, loser rejection, equal-bet PnL, and missed-winner gates.

Helius wallet timestamps are second-level. A detection needs at least one full
second of lead to count as strict pre-entry. Same-second signals may have been
triggered by the tracked wallet itself and are never credited as independent
early detection.

The retained-log cross-reference is valid only when the wallet campaigns and
EdgeSniffer files overlap in time. If `campaignsInsideEdgeWindow=0`, the command
worked but there is no comparable dataset. Do not interpret zero matches as a
failed detector or a failed wallet profile.

## Historical Pre-Entry Audit

When retained EdgeSniffer logs do not overlap the wallet ledger, reconstruct a
bounded historical pre-entry sample directly from mint transaction histories.
This audit:

- Stratifies campaigns across losses, small wins, scalp wins, and runners.
- Fetches the 60 seconds before each wallet entry using the rotating Helius
  pool.
- Excludes the target wallet's entry and all same-second rows from strict
  pre-entry evidence.
- Reconstructs buys, sells, unique wallets, volume, inferred market cap, and
  flow momentum at 1/3/5/10/30/60-second windows.
- Replays the current EdgeSniffer env, runner aggregate, and wide-scout
  profiles.
- Uses no Moralis or Gecko calls and adds nothing to the trading hot path.
- Saves each mint history under `data/wallet-pre-entry-cache`, so an interrupted
  rerun resumes instead of refetching completed mints.

Pull and verify the implementation:

```bash
cd /root/bot/FinalBot-cya
git pull origin main
npm install
npm run build
npm run test:wallet-history
```

Regenerate the ledger first. The current ledger writer records each campaign's
first-buy signature, which lets the historical audit exclude the wallet's own
entry exactly:

```bash
cd /root/bot/FinalBot-cya
npx tsx src/walletLedgerBucketAudit.ts \
  8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ \
  10000 \
  8ycby-ledger-10000
```

Start with a stratified 120-campaign diagnostic sample. Expanding all 984
campaigns before checking historical-flow coverage would waste API quota.

```bash
cd /root/bot/FinalBot-cya
nohup npm run audit:wallet-history -- \
  data/8ycby-ledger-10000.json \
  /root/bot/EdgeSniffer \
  120 \
  8ycby-history-preentry-120 \
  1000 \
  > data/8ycby-history-preentry-120.log 2>&1 &
echo $! > data/8ycby-history-preentry-120.pid
```

Monitor it:

```bash
cd /root/bot/FinalBot-cya
tail -f data/8ycby-history-preentry-120.log
```

In another shell, check whether it is still running:

```bash
cd /root/bot/FinalBot-cya
ps -p "$(cat data/8ycby-history-preentry-120.pid)" -o pid,etime,cmd
```

When the process exits, inspect the report:

```bash
cd /root/bot/FinalBot-cya
test -s data/8ycby-history-preentry-120.json && echo "historical audit ready"
cat data/8ycby-history-preentry-120.md
```

Read coverage before profile results. The diagnostic separates:

- `measured`: strict full-second pre-entry flow with a validated entry anchor.
- `same_second_only`: flow exists, but historical timestamps cannot prove
  whether it preceded the wallet.
- `first_seen_entry`: the target wallet is the first decoded mint transaction
  in the retained window.
- `pre_entry_decode_gap`: raw transactions exist but the historical decoder did
  not reconstruct them.
- `entry_not_found` / `entry_decode_failed`: the campaign anchor itself is not
  trustworthy and is excluded from feature and profile results.

Current precursor membership is disabled in historical scoring by default
because the list can contain future information relative to an old campaign.
Profile PF and PnL use the tracked wallet's source-exit ROI and measure
selection only; they are not an EdgeSniffer TP/SL simulation.

## 2CQ Causal Sweep And Replay

The validated 2CQ checkpoint is an external same-slot launch-burst lane. Keep
it separate from the existing Edge score and CyA runner lane.

```bash
cd /root/bot/FinalBot-cya
git pull origin main
npm run build
npm run test:wallet-causal

npm run audit:wallet-causal-sweep -- \
  data/2cq-ledger-10000.json \
  data/2cq-slot-order-120.json \
  data/2cq-launch-origin-120.json \
  /root/bot/EdgeSniffer \
  2cq-causal-sweep

npm run audit:wallet-causal-replay -- \
  data/2cq-causal-sweep.json \
  data/2cq-history-120.json \
  data/2cq-slot-order-120.json \
  data/wallet-pre-entry-cache \
  2cq-causal-replay
```

For true millisecond replay after a broad forward Edge window:

```bash
npm run audit:wallet-causal-forward-replay -- \
  data/2cq-causal-sweep.json \
  2cq-causal-forward-replay \
  /root/bot/EdgeSniffer/data/launch-paper-signals.jsonl \
  /root/bot/EdgeSniffer/data/launch-paper-stream-events.jsonl
```

Read `WALLET_CAUSAL_AUDIT_RUNBOOK.md` for validity gates, the full creator and
predecessor lists, and interpretation rules.

Do not expand merely because the command completed. If strict coverage is low:

1. Same-second winners dominating means the next audit must establish
   transaction order from slot/block position.
2. First-entry winners dominating means current flow features cannot precede
   the wallet; creation, deployer, bundle, and first-buyer features are needed.
3. Decoder or entry-anchor failures mean fix historical reconstruction before
   interpreting any profile.

Only if strict measured coverage is balanced across winners and losses should
you rerun at 400 campaigns using the same cache:

```bash
cd /root/bot/FinalBot-cya
nohup npm run audit:wallet-history -- \
  data/8ycby-ledger-10000.json \
  /root/bot/EdgeSniffer \
  400 \
  8ycby-history-preentry-400 \
  1500 \
  > data/8ycby-history-preentry-400.log 2>&1 &
echo $! > data/8ycby-history-preentry-400.pid
```

This historical pass discovers candidate indicators. It cannot prove
100-1,000ms ordering because historical Helius timestamps are second-level.
Keep EdgeSniffer collecting forward millisecond events for the final unbiased
validation and execution-delay replay.

## Same-Slot Transaction-Order Audit

When the historical diagnostic reports that most campaigns are
`same_second_only`, do not widen the timestamp window or treat every
same-second transaction as causal. Regenerate the historical artifact with
flow arrays, then order those transactions using their finalized block
positions.

```bash
cd /root/bot/FinalBot-cya
git pull origin main
npm run build
npm run test:wallet-history
npm run test:wallet-slot-order

npm run audit:wallet-history -- \
  data/8ycby-ledger-10000.json \
  /root/bot/EdgeSniffer \
  120 \
  8ycby-history-flow-v3 \
  1000
```

The mint histories are cached, so regenerating the 120-row artifact should be
quick. Then run the block-order pass:

```bash
cd /root/bot/FinalBot-cya
nohup npm run audit:wallet-slot-order -- \
  data/8ycby-history-flow-v3.json \
  /root/bot/EdgeSniffer \
  8ycby-slot-order-120 \
  > data/8ycby-slot-order-120.log 2>&1 &
echo $! > data/8ycby-slot-order-120.pid
```

Monitor and read the result:

```bash
cd /root/bot/FinalBot-cya
tail -f data/8ycby-slot-order-120.log
ps -p "$(cat data/8ycby-slot-order-120.pid)" -o pid,etime,cmd
cat data/8ycby-slot-order-120.md
```

The audit treats a transaction as proven prior only when it is in a lower slot
or has a lower transaction index than the target wallet inside the same
finalized block. Higher indexes are post-entry and excluded. Missing signatures
or blocks are reported as unknown rather than guessed.

Block order establishes causality, but not the millisecond lead available to a
live scanner. Profile PF still uses source-wallet outcomes and is not an
EdgeSniffer exit simulation. Do not expand the campaign sample or tune the live
profile until this report has balanced proven-prior coverage across winners and
losses.

## Launch-Origin Audit

If the slot-order report shows that profitable wallet entries precede nearly
all decoded swaps, prior public buy flow cannot explain the edge. Audit the
transactions that create the mint and immediately precede entry instead.

Regenerate the cached historical artifact with origin metadata:

```bash
cd /root/bot/FinalBot-cya
git pull origin main
npm run build
npm run test:wallet-launch-origin

npm run audit:wallet-history -- \
  data/8ycby-ledger-10000.json \
  /root/bot/EdgeSniffer \
  120 \
  8ycby-history-origin-v4 \
  1000
```

Run the local origin analysis. It reuses the existing mint-history and
slot-order caches and should not perform another broad API scan:

```bash
cd /root/bot/FinalBot-cya
npm run audit:wallet-launch-origin -- \
  data/8ycby-history-origin-v4.json \
  8ycby-launch-origin-120

cat data/8ycby-launch-origin-120.md
```

The report compares losses, small wins, scalp wins, and runners on:

- Whether an explicit creation transaction was proven to occur before entry.
- Creation-to-entry slot, second, and transaction-index distance.
- Whether creation immediately preceded the wallet entry.
- Creator/deployer fee payer recurrence.
- Immediate-predecessor fee payer recurrence.
- Whether the creation transaction funded the target wallet.

Do not promote a creator or predecessor to a live rule from one appearance.
Repeated identities and stable launch timing are candidate features for
forward collection; market-wide rejected launches are still required to
measure false positives.
