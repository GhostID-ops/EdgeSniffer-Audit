#!/usr/bin/env node
// For a target wallet, pair up buys and sells by mint to measure realized PnL distribution.
// Outputs bucketed win/loss outcomes and simulated TP/SL hit rates so we can pick a sweet spot.

import "dotenv/config";

const TARGET = process.argv[2] || "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o";
const HELIUS = process.env.HELIUS_API_KEY;
if (!HELIUS) {
  console.error("HELIUS_API_KEY missing");
  process.exit(1);
}
const RPC = `https://mainnet.helius-rpc.com/?api-key=${HELIUS}`;
const SIG_LIMIT = parseInt(process.argv[3] || "1500", 10);
const TARGET_PAIRS = 50;

const WSOL = "So11111111111111111111111111111111111111112";

async function rpc(method, params, retries = 5) {
  for (let i = 0; i < retries; i++) {
    const r = await fetch(RPC, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
    });
    if (r.status === 429) {
      await new Promise((res) => setTimeout(res, 1500 * (i + 1)));
      continue;
    }
    const j = await r.json();
    if (j.error) {
      if (j.error.code === -32005 || j.error.code === -32429) {
        await new Promise((res) => setTimeout(res, 1500 * (i + 1)));
        continue;
      }
      throw new Error(`${method}: ${JSON.stringify(j.error)}`);
    }
    return j.result;
  }
  throw new Error(`${method}: retries exhausted`);
}

async function getAllSigs(addr, max) {
  const all = [];
  let before;
  while (all.length < max) {
    const batch = await rpc("getSignaturesForAddress", [
      addr,
      { limit: Math.min(1000, max - all.length), before },
    ]);
    if (!batch || batch.length === 0) break;
    all.push(...batch);
    before = batch[batch.length - 1].signature;
    if (batch.length < 1000) break;
  }
  return all;
}

// Compute target's net SOL delta and per-mint token deltas for a tx.
function extractTrade(tx, target) {
  const keys = (tx.transaction.message.accountKeys || []).map((k) =>
    typeof k === "string" ? k : k.pubkey
  );
  const loaded = tx.meta?.loadedAddresses
    ? [...(tx.meta.loadedAddresses.writable || []), ...(tx.meta.loadedAddresses.readonly || [])]
    : [];
  const allKeys = [...keys, ...loaded];

  const targetIdx = allKeys.indexOf(target);
  if (targetIdx < 0) return null;

  const pre = tx.meta?.preBalances || [];
  const post = tx.meta?.postBalances || [];
  const fee = tx.meta?.fee || 0;
  if (post.length <= targetIdx) return null;
  // Net target SOL delta (excluding fee if target is fee payer at index 0)
  const solDelta = post[targetIdx] - pre[targetIdx] + (targetIdx === 0 ? fee : 0);

  // Per-mint tokens delta for the target's owned ATAs
  const preTb = tx.meta?.preTokenBalances || [];
  const postTb = tx.meta?.postTokenBalances || [];
  const idxMap = new Map();
  for (const b of preTb) {
    idxMap.set(b.accountIndex, {
      owner: b.owner,
      mint: b.mint,
      pre: BigInt(b.uiTokenAmount.amount),
      post: 0n,
      decimals: b.uiTokenAmount.decimals,
    });
  }
  for (const b of postTb) {
    const cur = idxMap.get(b.accountIndex) || {
      owner: b.owner,
      mint: b.mint,
      pre: 0n,
      post: 0n,
      decimals: b.uiTokenAmount.decimals,
    };
    cur.post = BigInt(b.uiTokenAmount.amount);
    idxMap.set(b.accountIndex, cur);
  }
  const mintDeltas = new Map(); // mint -> { rawDelta, decimals }
  for (const v of idxMap.values()) {
    if (v.owner !== target) continue;
    if (v.mint === WSOL) continue;
    const delta = v.post - v.pre;
    if (delta === 0n) continue;
    const cur = mintDeltas.get(v.mint) || { rawDelta: 0n, decimals: v.decimals };
    cur.rawDelta += delta;
    mintDeltas.set(v.mint, cur);
  }

  return { solDelta, mintDeltas, blockTime: tx.blockTime, slot: tx.slot };
}

async function main() {
  console.log(`Pulling up to ${SIG_LIMIT} sigs for ${TARGET}...`);
  const sigs = await getAllSigs(TARGET, SIG_LIMIT);
  console.log(`Got ${sigs.length}. Latest: ${sigs[0]?.blockTime ? new Date(sigs[0].blockTime * 1000).toISOString() : "?"}`);
  console.log(`Earliest: ${sigs[sigs.length - 1]?.blockTime ? new Date(sigs[sigs.length - 1].blockTime * 1000).toISOString() : "?"}`);

  // Walk newest -> oldest, accumulate per-mint runs until we have enough closed pairs
  // (closed = position opens then closes back to ~0).
  const perMint = new Map(); // mint -> { trades: [{solDelta, tokenDelta, ts, sig}], events }
  let processed = 0;

  for (const s of sigs) {
    if (s.err) continue;
    try {
      const tx = await rpc("getTransaction", [
        s.signature,
        { commitment: "confirmed", maxSupportedTransactionVersion: 0 },
      ]);
      if (!tx?.meta) continue;
      const trade = extractTrade(tx, TARGET);
      if (!trade) continue;

      for (const [mint, { rawDelta }] of trade.mintDeltas) {
        // Skip dust
        if (rawDelta === 0n) continue;
        if (!perMint.has(mint)) perMint.set(mint, []);
        perMint.get(mint).push({
          ts: trade.blockTime,
          slot: trade.slot,
          sig: s.signature,
          solDelta: trade.solDelta, // negative on buy, positive on sell (approximate)
          tokenDelta: rawDelta,
        });
      }
      processed++;
      if (processed % 100 === 0) {
        process.stdout.write(`\r  ...processed ${processed}/${sigs.length}`);
      }
      await new Promise((r) => setTimeout(r, 20));
    } catch {
      // skip
    }
  }
  console.log(`\nProcessed ${processed} txs. Distinct mints touched: ${perMint.size}`);

  // Build closed cycles per mint: position rises from 0 to >0 (entry) then back to 0 (exit).
  // Some tokens might have multiple entry/exit cycles — handle each.
  const cycles = []; // {mint, entryTs, exitTs, totalSolIn, totalSolOut, entryEvents, exitEvents}
  for (const [mint, events] of perMint) {
    // Oldest first
    events.sort((a, b) => (a.ts || 0) - (b.ts || 0));
    let openTokens = 0n;
    let cycleSolIn = 0n;
    let cycleSolOut = 0n;
    let entryTs = null;
    let entryEvents = 0;
    let exitEvents = 0;
    for (const e of events) {
      const prevOpen = openTokens;
      openTokens += e.tokenDelta;
      // Negative solDelta with positive tokenDelta = buy
      // Positive solDelta with negative tokenDelta = sell
      if (e.tokenDelta > 0n) {
        cycleSolIn += BigInt(Math.max(0, -e.solDelta));
        entryEvents++;
        if (prevOpen === 0n && entryTs === null) entryTs = e.ts;
      } else if (e.tokenDelta < 0n) {
        cycleSolOut += BigInt(Math.max(0, e.solDelta));
        exitEvents++;
      }
      if (openTokens <= 0n && prevOpen > 0n) {
        // Cycle closed
        cycles.push({
          mint,
          entryTs,
          exitTs: e.ts,
          solIn: cycleSolIn,
          solOut: cycleSolOut,
          entryEvents,
          exitEvents,
          durationSec: entryTs && e.ts ? e.ts - entryTs : null,
        });
        // reset for potential next cycle
        openTokens = 0n;
        cycleSolIn = 0n;
        cycleSolOut = 0n;
        entryTs = null;
        entryEvents = 0;
        exitEvents = 0;
      }
    }
  }

  cycles.sort((a, b) => (b.exitTs || 0) - (a.exitTs || 0));
  const recent = cycles.slice(0, TARGET_PAIRS);

  console.log(`\nClosed buy→sell cycles found: ${cycles.length} (using most recent ${recent.length})\n`);

  let wins = 0, losses = 0;
  let totalIn = 0n, totalOut = 0n;
  const buckets = {
    rug_under_minus50: 0,
    loss_minus20_to_minus50: 0,
    loss_minus10_to_minus20: 0,
    flat_minus10_to_plus10: 0,
    win_10_to_20: 0,
    win_20_to_50: 0,
    win_50_to_100: 0,
    win_100_to_300: 0,
    moon_300_plus: 0,
  };
  // For TP/SL simulation, we need PEAK and TROUGH between entry and exit, not just exit price.
  // We don't have that easily — so we use FINAL pct as proxy. This understates TP hit rate
  // (since price often spiked higher than the exit price) but it's the closest we get from
  // signatures alone without quoting historical prices.

  const tpThresholds = [10, 15, 20, 30, 50];
  const tpHits = Object.fromEntries(tpThresholds.map((t) => [t, 0]));
  const durations = [];

  for (const c of recent) {
    const inSol = Number(c.solIn) / 1e9;
    const outSol = Number(c.solOut) / 1e9;
    if (inSol === 0) continue;
    const pnlPct = ((outSol - inSol) / inSol) * 100;
    totalIn += c.solIn;
    totalOut += c.solOut;
    if (pnlPct > 0) wins++; else losses++;
    if (pnlPct < -50) buckets.rug_under_minus50++;
    else if (pnlPct < -20) buckets.loss_minus20_to_minus50++;
    else if (pnlPct < -10) buckets.loss_minus10_to_minus20++;
    else if (pnlPct < 10) buckets.flat_minus10_to_plus10++;
    else if (pnlPct < 20) buckets.win_10_to_20++;
    else if (pnlPct < 50) buckets.win_20_to_50++;
    else if (pnlPct < 100) buckets.win_50_to_100++;
    else if (pnlPct < 300) buckets.win_100_to_300++;
    else buckets.moon_300_plus++;
    for (const t of tpThresholds) if (pnlPct >= t) tpHits[t]++;
    if (c.durationSec) durations.push(c.durationSec);
  }

  console.log("=== Outcome distribution (final exit pnl) ===");
  for (const [k, v] of Object.entries(buckets)) {
    const pct = recent.length ? ((v / recent.length) * 100).toFixed(1) : "0";
    console.log(`  ${k.padEnd(28)} ${v.toString().padStart(3)}  (${pct}%)`);
  }

  console.log(`\nTotal IN: ${(Number(totalIn) / 1e9).toFixed(3)} SOL  OUT: ${(Number(totalOut) / 1e9).toFixed(3)} SOL`);
  const netPnl = Number(totalOut - totalIn) / 1e9;
  console.log(`Net PnL across these ${recent.length} cycles: ${netPnl >= 0 ? "+" : ""}${netPnl.toFixed(3)} SOL`);
  console.log(`Win rate (final exit > break-even): ${wins} / ${recent.length} = ${((wins / recent.length) * 100).toFixed(1)}%`);

  console.log(`\n=== Simulated TP hit rates (using FINAL exit pct — UNDERSTATES true peak-hit rate) ===`);
  for (const t of tpThresholds) {
    const pct = recent.length ? ((tpHits[t] / recent.length) * 100).toFixed(1) : "0";
    console.log(`  TP=${t}%   hits in ${tpHits[t]}/${recent.length} (${pct}%) of cycles`);
  }
  console.log("\nNOTE: real TP hit rate is HIGHER than these numbers, because TP fires on peak");
  console.log("price during the hold, not at final exit price. Source often rides past TP then");
  console.log("dumps. A TP=15 simulation here only counts cycles whose EXIT was >+15%, but");
  console.log("the source frequently peaks >+15% on tokens he ultimately exits at -5% or 0%.");
  console.log("Treat these as a CONSERVATIVE LOWER BOUND.");

  if (durations.length) {
    durations.sort((a, b) => a - b);
    const median = durations[Math.floor(durations.length / 2)];
    const p25 = durations[Math.floor(durations.length * 0.25)];
    const p75 = durations[Math.floor(durations.length * 0.75)];
    console.log(`\nHold duration: p25=${p25}s  median=${median}s  p75=${p75}s`);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
