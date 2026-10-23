#!/usr/bin/env node
import "dotenv/config";
import fs from "node:fs";

const WSOL = "So11111111111111111111111111111111111111112";
const TARGET = process.argv[2] ?? process.env.TRACKED_WALLETS?.split(",")[0]?.trim();
const START_ISO = process.argv[3] ?? "2026-07-18T00:00:00Z";
const END_ISO = process.argv[4] ?? "2026-07-19T00:00:00Z";
const HORIZON_HOURS = Number(process.argv[5] ?? "6");
const MINT_TX_LIMIT = Number(process.argv[6] ?? "1200");
const MIN_POINT_SOL = Number(process.argv[7] ?? "0.02");
const HELIUS_API_KEY = process.env.HELIUS_API_KEY ?? "";

if (!TARGET) throw new Error("usage: node scripts/analyzeCopytradeOnchainAth.mjs WALLET [startIso] [endIso] [horizonHours] [mintTxLimit] [minPointSol]");
if (!HELIUS_API_KEY) throw new Error("HELIUS_API_KEY is not set");

const START = Date.parse(START_ISO);
const END = Date.parse(END_ISO);
const MIN_POINT_LAMPORTS = Math.max(0, Math.round(MIN_POINT_SOL * 1e9));
const OUT = `data/${TARGET.slice(0, 6)}-onchain-ath-${START_ISO.slice(0, 10)}-${new Date().toISOString().replace(/[:.]/g, "-")}.md`;

function short(s) {
  return s ? `${s.slice(0, 6)}...${s.slice(-4)}` : "-";
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function fmtPct(value) {
  return value == null || !Number.isFinite(value) ? "-" : `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function fmtSol(value) {
  return value == null || !Number.isFinite(value) ? "-" : `${value.toFixed(4)} SOL`;
}

function iso(msOrSec) {
  const ms = msOrSec > 1e12 ? msOrSec : msOrSec * 1000;
  return new Date(ms).toISOString().replace("T", " ").slice(0, 16);
}

function pct(a, b) {
  return a > 0 && Number.isFinite(a) && Number.isFinite(b) ? (b / a - 1) * 100 : null;
}

function median(values) {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  return xs.length ? xs[Math.floor(xs.length / 2)] : null;
}

function bestPointForLoggedBuy(points, loggedBuy) {
  const signature = loggedBuy.mirrorSignature;
  if (!signature) return null;
  const expectedSolLamports = Number(loggedBuy.inputAmountRaw ?? 0);
  const candidates = points.filter((point) => point.signature === signature && point.side === "buy");
  if (!candidates.length) return null;
  if (!Number.isFinite(expectedSolLamports) || expectedSolLamports <= 0) {
    return candidates.sort((a, b) => b.solLamports - a.solLamports)[0];
  }
  return candidates.sort(
    (a, b) => Math.abs(a.solLamports - expectedSolLamports) - Math.abs(b.solLamports - expectedSolLamports)
  )[0];
}

function buildOnchainEntry(points, loggedBuys) {
  const matched = [];
  for (const loggedBuy of loggedBuys) {
    const point = bestPointForLoggedBuy(points, loggedBuy);
    if (point) matched.push(point);
  }
  const matchedSol = matched.reduce((sum, point) => sum + point.solLamports, 0);
  const matchedTokenRaw = matched.reduce((sum, point) => sum + point.tokenRaw, 0);
  if (matchedSol > 0 && matchedTokenRaw > 0) {
    return {
      source: matched.length === loggedBuys.length ? "onchain-mirror" : "partial-onchain-mirror",
      matched: matched.length,
      total: loggedBuys.length,
      priceLamportsPerRaw: matchedSol / matchedTokenRaw,
    };
  }

  const loggedSol = loggedBuys.reduce((sum, row) => sum + (row.inputAmountRaw || 0), 0);
  const loggedTokenRaw = loggedBuys.reduce((sum, row) => sum + (row.outputAmountRaw || 0), 0);
  return {
    source: "trade-log-fallback",
    matched: 0,
    total: loggedBuys.length,
    priceLamportsPerRaw: loggedSol > 0 && loggedTokenRaw > 0 ? loggedSol / loggedTokenRaw : null,
  };
}

async function fetchAddressTxs(address, limit, before) {
  const params = new URLSearchParams({
    "api-key": HELIUS_API_KEY,
    limit: String(limit),
    commitment: "confirmed",
  });
  if (before) params.set("before", before);
  const response = await fetch(`https://api.helius.xyz/v0/addresses/${address}/transactions?${params.toString()}`, {
    signal: AbortSignal.timeout(20_000),
  });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`Helius ${response.status}: ${text.slice(0, 220)}`);
  }
  return await response.json();
}

async function fetchParsedTransactions(signatures) {
  const unique = [...new Set(signatures.filter(Boolean))];
  const out = [];
  for (let i = 0; i < unique.length; i += 100) {
    const batch = unique.slice(i, i + 100);
    const response = await fetch(`https://api.helius.xyz/v0/transactions/?api-key=${encodeURIComponent(HELIUS_API_KEY)}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ transactions: batch }),
      signal: AbortSignal.timeout(20_000),
    });
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new Error(`Helius tx parse ${response.status}: ${text.slice(0, 220)}`);
    }
    const parsed = await response.json();
    out.push(...parsed.filter(Boolean));
    await sleep(250);
  }
  return out;
}

async function fetchMintTxsWindow(mint, fromTs, toTs, maxTxs) {
  const all = [];
  let before;
  while (all.length < maxTxs) {
    const batch = await fetchAddressTxs(mint, Math.min(100, maxTxs - all.length), before);
    if (!batch.length) break;
    all.push(...batch);
    const oldest = batch.at(-1)?.timestamp ?? 0;
    before = batch.at(-1)?.signature;
    if (oldest > 0 && oldest < fromTs) break;
    await sleep(350);
  }
  return all.filter((tx) => (tx.timestamp ?? 0) >= fromTs && (tx.timestamp ?? 0) <= toTs);
}

function extractMintPricePoints(tx, mint) {
  const nativeByWallet = new Map();
  const tokenByWallet = new Map();
  const wsolByWallet = new Map();

  for (const account of tx.accountData ?? []) {
    if (typeof account.nativeBalanceChange === "number" && account.nativeBalanceChange !== 0) {
      nativeByWallet.set(account.account, (nativeByWallet.get(account.account) ?? 0) + account.nativeBalanceChange);
    }
    for (const change of account.tokenBalanceChanges ?? []) {
      const owner = change.userAccount;
      const raw = Number(change.rawTokenAmount?.tokenAmount ?? "0");
      if (!owner || !Number.isFinite(raw) || raw === 0) continue;
      if (change.mint === mint) {
        tokenByWallet.set(owner, (tokenByWallet.get(owner) ?? 0) + raw);
      } else if (change.mint === WSOL) {
        wsolByWallet.set(owner, (wsolByWallet.get(owner) ?? 0) + raw);
      }
    }
  }

  const points = [];
  for (const [wallet, tokenNet] of tokenByWallet) {
    const nativeNet = nativeByWallet.get(wallet) ?? 0;
    const wsolNet = wsolByWallet.get(wallet) ?? 0;
    const tokenAbs = Math.abs(tokenNet);
    if (tokenAbs <= 0) continue;
    const quoteLamports =
      tokenNet > 0 && wsolNet < 0
        ? Math.abs(wsolNet)
        : tokenNet < 0 && wsolNet > 0
        ? wsolNet
        : tokenNet > 0 && nativeNet < 0
        ? Math.abs(nativeNet)
        : tokenNet < 0 && nativeNet > 0
        ? nativeNet
        : 0;
    if (quoteLamports < MIN_POINT_LAMPORTS || quoteLamports <= 0) continue;
    points.push({
      signature: tx.signature,
      timestamp: tx.timestamp ?? 0,
      wallet,
      side: tokenNet > 0 ? "buy" : "sell",
      priceLamportsPerRaw: quoteLamports / tokenAbs,
      solLamports: quoteLamports,
      tokenRaw: tokenAbs,
    });
  }
  return points;
}

const trades = fs
  .readFileSync("data/trades.jsonl", "utf8")
  .trim()
  .split(/\n+/)
  .map((line) => JSON.parse(line));

const copiedBuys = trades.filter(
  (row) =>
    row.ts >= START &&
    row.ts < END &&
    row.side === "buy" &&
    row.sourceWallet === TARGET &&
    row.inputMint === WSOL
);

const rows = [];
for (const mint of [...new Set(copiedBuys.map((row) => row.outputMint))]) {
  const mintBuys = copiedBuys.filter((row) => row.outputMint === mint).sort((a, b) => a.ts - b.ts);
  const firstBuy = mintBuys[0];
  const firstBuyTs = Math.floor(firstBuy.ts / 1000);
  const lastHorizonTs = firstBuyTs + HORIZON_HOURS * 3600;
  const sameWindowSells = trades
    .filter((row) => row.side === "sell" && row.inputMint === mint && row.ts >= firstBuy.ts && row.ts < END)
    .sort((a, b) => a.ts - b.ts);
  const finalSellTs = sameWindowSells.at(-1) ? Math.floor(sameWindowSells.at(-1).ts / 1000) : null;
  const spentSol = mintBuys.reduce((sum, row) => sum + (row.inputAmountRaw || 0), 0) / 1e9;
  const receivedSol = sameWindowSells.reduce((sum, row) => sum + (row.outputAmountRaw || 0), 0) / 1e9;
  const realizedPct = sameWindowSells.length ? pct(spentSol, receivedSol) : null;
  const loggedEntryPrice =
    mintBuys.reduce((sum, row) => sum + (row.inputAmountRaw || 0), 0) /
    mintBuys.reduce((sum, row) => sum + (row.outputAmountRaw || 0), 0);

  let row = {
    mint,
    symbol: firstBuy.outputSymbol || short(mint),
    buyAt: firstBuyTs,
    buys: mintBuys.length,
    sells: sameWindowSells.length,
    spentSol,
    receivedSol,
    realizedPct,
    finalSellTs,
  };

  try {
    const txs = await fetchMintTxsWindow(mint, firstBuyTs, lastHorizonTs, MINT_TX_LIMIT);
    const knownSignatures = new Set(txs.map((tx) => tx.signature).filter(Boolean));
    const missingEntrySignatures = mintBuys
      .map((buy) => buy.mirrorSignature)
      .filter((signature) => signature && !knownSignatures.has(signature));
    let entryLookupError = null;
    if (missingEntrySignatures.length) {
      try {
        const entryTxs = await fetchParsedTransactions(missingEntrySignatures);
        for (const tx of entryTxs) {
          if (!tx?.signature || knownSignatures.has(tx.signature)) continue;
          txs.push(tx);
          knownSignatures.add(tx.signature);
        }
      } catch (error) {
        entryLookupError = error instanceof Error ? error.message : String(error);
      }
    }
    const points = txs.flatMap((tx) => extractMintPricePoints(tx, mint)).filter((point) => point.timestamp >= firstBuyTs);
    const entry = buildOnchainEntry(points, mintBuys);
    const entryPrice = entry.priceLamportsPerRaw ?? loggedEntryPrice;
    const buys = points.filter((point) => point.side === "buy");
    const peak = buys.reduce((best, point) => (!best || point.priceLamportsPerRaw > best.priceLamportsPerRaw ? point : best), null);
    const trough = points.reduce((best, point) => (!best || point.priceLamportsPerRaw < best.priceLamportsPerRaw ? point : best), null);
    const afterSell = finalSellTs ? buys.filter((point) => point.timestamp >= finalSellTs) : [];
    const peakAfterSell = afterSell.reduce((best, point) => (!best || point.priceLamportsPerRaw > best.priceLamportsPerRaw ? point : best), null);
    const hit30Point = buys
      .sort((a, b) => a.timestamp - b.timestamp)
      .find((point) => pct(entryPrice, point.priceLamportsPerRaw) >= 30);
    const before30 = hit30Point ? points.filter((point) => point.timestamp <= hit30Point.timestamp) : points;
    const troughBefore30 = before30.reduce((best, point) => (!best || point.priceLamportsPerRaw < best.priceLamportsPerRaw ? point : best), null);

    row = {
      ...row,
      txs: txs.length,
      points: points.length,
      buyPoints: buys.length,
      entrySource: entry.source,
      entryMatchedBuys: entry.matched,
      entryTotalBuys: entry.total,
      entryLookupError,
      athPct: peak ? pct(entryPrice, peak.priceLamportsPerRaw) : null,
      athAt: peak?.timestamp ?? null,
      athSol: peak ? peak.solLamports / 1e9 : null,
      maePct: trough ? pct(entryPrice, trough.priceLamportsPerRaw) : null,
      afterSellAthPct: peakAfterSell ? pct(entryPrice, peakAfterSell.priceLamportsPerRaw) : null,
      afterSellAthAt: peakAfterSell?.timestamp ?? null,
      afterSellAthSol: peakAfterSell ? peakAfterSell.solLamports / 1e9 : null,
      hit30: Boolean(hit30Point),
      hit50: peak ? pct(entryPrice, peak.priceLamportsPerRaw) >= 50 : false,
      hit100: peak ? pct(entryPrice, peak.priceLamportsPerRaw) >= 100 : false,
      timeTo30Sec: hit30Point ? hit30Point.timestamp - firstBuyTs : null,
      drawdownBefore30Pct: troughBefore30 ? pct(entryPrice, troughBefore30.priceLamportsPerRaw) : null,
    };
  } catch (error) {
    row = { ...row, error: error instanceof Error ? error.message : String(error) };
  }

  rows.push(row);
  console.log(`${rows.length} ${short(mint)} ${row.error || fmtPct(row.athPct)} points=${row.points ?? 0}`);
  await sleep(500);
}

const measured = rows.filter((row) => row.athPct != null);
const afterSellMeasured = measured.filter((row) => row.afterSellAthPct != null && row.sells > 0);
const lines = [];
lines.push(`# ${short(TARGET)} On-Chain Swap ATH After Our Cashout`);
lines.push("");
lines.push(`Generated: ${new Date().toISOString()}`);
lines.push(`Window: ${START_ISO} to ${END_ISO}`);
lines.push(`ATH horizon after each buy: ${HORIZON_HOURS}h`);
lines.push(`Minimum included swap point: ${MIN_POINT_SOL} SOL`);
lines.push("");
lines.push("## Summary");
lines.push(`- Tokens copied: ${rows.length}`);
lines.push(`- On-chain measured: ${measured.length}/${rows.length}`);
lines.push(`- Hit +30%: ${measured.filter((row) => row.hit30).length}/${measured.length}`);
lines.push(`- Hit +50%: ${measured.filter((row) => row.hit50).length}/${measured.length}`);
lines.push(`- Hit +100%: ${measured.filter((row) => row.hit100).length}/${measured.length}`);
lines.push(`- Median observed ATH/MFE: ${fmtPct(median(measured.map((row) => row.athPct)))}`);
lines.push(`- Median observed low/MAE: ${fmtPct(median(measured.map((row) => row.maePct)))}`);
lines.push(`- Median ATH after our final cashout: ${fmtPct(median(afterSellMeasured.map((row) => row.afterSellAthPct)))}`);
lines.push("");
lines.push("## Rows");
lines.push("| Token | Mint | Buy UTC | Spent | Sells | Realized | ATH | ATH UTC | ATH After Cashout | MAE | Hit30 | Time30 | DD Before 30 | Points | Notes |");
lines.push("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|");
for (const row of rows) {
  const entryNote = row.entrySource
    ? `${row.entrySource} ${row.entryMatchedBuys ?? 0}/${row.entryTotalBuys ?? 0}, peak ${fmtSol(row.athSol)}`
    : "";
  const lookupNote = row.entryLookupError ? `; entry lookup ${row.entryLookupError}` : "";
  lines.push(
    `| ${row.symbol || short(row.mint)} | \`${short(row.mint)}\` | ${iso(row.buyAt)} | ${fmtSol(row.spentSol)} | ${row.sells} | ${fmtPct(
      row.realizedPct
    )} | ${fmtPct(row.athPct)} | ${row.athAt ? iso(row.athAt) : "-"} | ${fmtPct(row.afterSellAthPct)} | ${fmtPct(row.maePct)} | ${
      row.hit30 ? "yes" : "no"
    } | ${row.timeTo30Sec == null ? "-" : `${row.timeTo30Sec}s`} | ${fmtPct(row.drawdownBefore30Pct)} | ${row.points ?? 0} | ${
      row.error || `${row.txs ?? 0} mint txs; ${entryNote}${lookupNote}`
    } |`
  );
}
lines.push("");
lines.push("## Read");
lines.push("- This report reconstructs price from Helius enhanced on-chain token/SOL balance changes, not chart candles.");
lines.push("- ATH is the highest observed buy-side swap price after our first copied buy, within the configured horizon.");
lines.push("- It can still miss private/unindexed/provider-omitted swaps, but it avoids OHLCV aggregation and chart-provider pool mismatch.");
lines.push("- Percentages compare observed swap price to our confirmed mirrored buy fill when that signature is present in the on-chain data.");
lines.push("- If an entry signature is missing from the provider response, that row is marked as a trade-log fallback and should be treated as lower confidence.");
lines.push("- The minimum swap point filter is intentionally above dust size so weird tiny prints do not become fake ATHs.");

fs.mkdirSync("data", { recursive: true });
fs.writeFileSync(OUT, lines.join("\n"));
fs.writeFileSync(OUT.replace(/\.md$/, ".json"), JSON.stringify({ generatedAt: new Date().toISOString(), target: TARGET, start: START_ISO, end: END_ISO, horizonHours: HORIZON_HOURS, minPointSol: MIN_POINT_SOL, rows }, null, 2));

console.log("");
console.log(`REPORT ${OUT}`);
console.log(lines.slice(0, 80).join("\n"));
