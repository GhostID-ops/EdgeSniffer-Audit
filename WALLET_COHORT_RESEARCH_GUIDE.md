# Wallet Cohort Research Guide

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
