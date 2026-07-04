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

## Start In Background

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
