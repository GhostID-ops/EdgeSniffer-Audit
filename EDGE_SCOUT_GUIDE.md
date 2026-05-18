# Edge Scout Guide

This guide explains how to use the optional edge scout without interfering with the main copytrading bot.

The goal is simple:

- Keep the main bot fast and unchanged.
- Run edge discovery in a separate terminal only when you choose.
- Collect new-token and source-wallet pattern data.
- Use that data to decide whether there is an earlier signal than the wallet we currently copy.

## What Edge Scout Is

`edge-scout` is a separate process that connects to PumpPortal's real-time data stream and watches new Pump.fun token launches.

It does not trade.

It does not start the main bot.

It does not share the main bot's gRPC watcher, RabbitStream watcher, Jupiter quote loop, sender path, or risk-exit loop.

That separation matters because the copytrader's speed should not depend on an experimental research process.

## Files And Commands

Main files:

- `src/edgeScout.ts` - live new-token scout
- `src/profileSourceEdge.ts` - offline/on-chain source-wallet pattern profiler
- `data/edge-scout.jsonl` - default live scout event log

Commands:

```bash
npm run edge-scout
npm run profile-edge -- <wallet> <wallet_tx_limit> <mint_limit>
```

Example:

```bash
npm run profile-edge -- 4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk 100 25
```

## Safe Default Settings

The scout is disabled by default.

These optional settings are documented in `.env.example`:

```env
EDGE_SCOUT_ENABLED=false
PUMPPORTAL_API_KEY=
EDGE_SCOUT_LOG_FILE=data/edge-scout.jsonl
EDGE_SCOUT_RECONNECT_MS=1500
EDGE_SCOUT_SUBSCRIBE_SOURCE_TRADES=false
EDGE_SCOUT_ACCOUNTS=
```

Do not turn on `EDGE_SCOUT_SUBSCRIBE_SOURCE_TRADES` casually. PumpPortal's account/token trade streams are metered. New-token and migration streams are free according to PumpPortal's docs, but account and token trade streams cost per message.

## How To Run It

Terminal 1: run the normal bot exactly as usual.

```bash
npm run bot
```

Terminal 2: run the edge scout when you want to collect new-token data.

```bash
npm run edge-scout -- --force
```

Using `--force` starts the scout even if `EDGE_SCOUT_ENABLED=false`.

If you prefer an env toggle instead, set:

```env
EDGE_SCOUT_ENABLED=true
```

Then run:

```bash
npm run edge-scout
```

Stop the scout anytime with `Ctrl+C`.

The main bot keeps running independently.

## What You Should See

When the scout starts, it should print:

```text
Edge scout starting as a separate process. It does not start or modify the copytrading bot.
Edge scout connected to PumpPortal.
Edge scout: subscribed to new token stream.
```

Then new token launches should print like:

```text
NEW $TOKEN AbCd12...pump creator=Wallet...Here 0.5000 SOL devbuy · mc=32.1 SOL
```

Events are appended to:

```text
data/edge-scout.jsonl
```

Each line is JSON so we can analyze it later.

## First Phase: Observe Only

For now, use the scout as a data collector.

Do not auto-buy from it yet.

The point of phase one is to answer:

- What kinds of launches are happening before our source wallet buys?
- Are the profitable source-wallet buys connected to repeated creators?
- Are there repeated early buyers before him?
- Does he tend to buy within the first few seconds, or after confirmation from other wallets?
- Are his wins clustered around certain dev-buy sizes, liquidity shapes, token programs, or early trade patterns?

## On-Chain Investigation

Yes, we can investigate what wallets he may be sniping or reacting to.

Use:

```bash
npm run profile-edge -- 4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk 100 25
```

Arguments:

- `4BdK...EFUk` - source wallet to investigate
- `100` - how many recent wallet transactions to scan
- `25` - how many recent bought mints to profile

This profiler pulls recent source-wallet buys, then checks each mint's early on-chain history.

It reports:

- source wallet PnL per mint
- source buy size
- source delay from early mint history
- source buy order when detectable
- number of earlier buys
- total SOL that entered before him
- creator wallet
- top earlier buyer
- repeated creators
- repeated early buyers

The key thing to look for is repetition.

One random earlier buyer means little.

The same earlier buyer appearing before multiple profitable source-wallet buys is interesting.

The same creator appearing in multiple profitable source-wallet buys is also interesting.

## How We Turn This Into An Edge

The clean strategy is staged:

1. Collect live new-token events with `edge-scout`.
2. Run `profile-edge` against the source wallet regularly.
3. Identify repeated early buyers, creators, or launch patterns.
4. Add those wallets/patterns to a scoring list.
5. Have the scout print high-confidence alerts.
6. Only after alerts are clearly useful, consider optional auto-buy handoff.

The first real edge is probably not "buy everything new."

The first real edge is more likely:

- creator appeared in prior source-wallet wins
- known early buyer entered
- dev buy is within the source wallet's winning range
- early SOL flow is strong but not already too crowded
- token is still on the bonding curve
- source wallet has not bought yet

That would give us a chance to enter before the copy signal.

## What Not To Do Yet

Do not wire the scout directly into live buys immediately.

Do not subscribe to thousands of accounts.

Do not open multiple PumpPortal websocket connections for every token.

Do not let the scout call Jupiter quotes or compete with the main bot's hot path.

Do not leave metered account-trade streams on unless you intentionally want that cost.

## Optional Metered Source-Trade Stream

If you want to watch the source wallet through PumpPortal too, set:

```env
EDGE_SCOUT_SUBSCRIBE_SOURCE_TRADES=true
EDGE_SCOUT_ACCOUNTS=4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk
```

Then run:

```bash
npm run edge-scout
```

This is optional because we already track the source wallet through the main bot. The only reason to enable it is to compare feed timing or collect separate research logs.

## Recommended Starting Routine

1. Start the main bot:

```bash
npm run bot
```

2. In a second terminal, start the scout:

```bash
npm run edge-scout -- --force
```

3. Let it collect data during an active trading window.

4. Stop it with `Ctrl+C`.

5. Run the source-wallet profiler:

```bash
npm run profile-edge -- 4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk 100 25
```

6. Review repeated creators and repeated top early buyers.

7. If patterns show up, promote them into scout scoring rules.

## Current Status

Implemented:

- Separate scout process
- Disabled-by-default toggle
- New-token stream subscription
- JSONL event logging
- Offline source-wallet edge profiler

Not implemented yet:

- Pattern scoring
- Alert thresholds
- Telegram scout alerts
- Auto-buy handoff

That is intentional. The scout should earn trust as a research tool before it is allowed anywhere near execution.
