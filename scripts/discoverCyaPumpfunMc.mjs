import "dotenv/config";
import fs from "node:fs/promises";
import path from "node:path";

const WALLET = process.argv[2] ?? "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o";
const HOURS = Number(process.argv[3] ?? "168");
const MIN_ENTRY_MC_USD = Number(process.argv[4] ?? "50000");
const MAX_PAGES = Number(process.env.CYA_DISCOVERY_MAX_PAGES ?? "120");
const PAGE_SLEEP_MS = Number(process.env.CYA_DISCOVERY_PAGE_SLEEP_MS ?? "350");
const HELIUS_API_KEY = process.env.HELIUS_API_KEY ?? "";
const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const SUPPLY_APPROX = Number(process.env.PREBUY_INFERRED_TOKEN_SUPPLY ?? "1000000000");
const SOL_USD = Number(process.env.LOCAL_PUMPFUN_SOL_PRICE_USD ?? process.env.SOL_PRICE_USD ?? "69.07");
const OUT_JSON = path.resolve(process.cwd(), "data", `cya-pumpfun-mc-discovery-${HOURS}h.json`);
const OUT_MD = path.resolve(process.cwd(), "data", `cya-pumpfun-mc-discovery-${HOURS}h.md`);

if (!HELIUS_API_KEY) {
  console.error("HELIUS_API_KEY is not set in .env.");
  process.exit(1);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function short(addr) {
  return addr ? `${addr.slice(0, 6)}...${addr.slice(-4)}` : "-";
}

function sol(lamports) {
  return lamports / 1_000_000_000;
}

function fmtUsd(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
  return `$${Math.round(value).toLocaleString("en-US")}`;
}

function fmtSol(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${value.toFixed(3)} SOL`;
}

function median(values) {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!xs.length) return null;
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[mid] : (xs[mid - 1] + xs[mid]) / 2;
}

async function fetchJson(url, options = {}) {
  let lastErr;
  for (let attempt = 1; attempt <= 4; attempt++) {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(options.timeoutMs ?? 20_000) });
      if (res.status === 429 || res.status >= 500) {
        lastErr = new Error(`HTTP ${res.status}: ${(await res.text()).slice(0, 160)}`);
        await sleep(attempt * 1500);
        continue;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${(await res.text()).slice(0, 160)}`);
      return await res.json();
    } catch (err) {
      lastErr = err;
      await sleep(attempt * 1000);
    }
  }
  throw lastErr;
}

function dedupeTxs(txs) {
  const map = new Map();
  for (const tx of txs) if (tx?.signature) map.set(tx.signature, tx);
  return [...map.values()].sort((a, b) => (b.timestamp ?? 0) - (a.timestamp ?? 0));
}

async function fetchWalletTxsSince(wallet, cutoffTs) {
  const all = [];
  let before = null;
  for (let page = 0; page < MAX_PAGES; page++) {
    const url =
      `https://api.helius.xyz/v0/addresses/${wallet}/transactions?api-key=${HELIUS_API_KEY}` +
      `&limit=100${before ? `&before=${before}` : ""}`;
    const batch = await fetchJson(url, { timeoutMs: 20_000 });
    if (!Array.isArray(batch) || batch.length === 0) break;
    all.push(...batch);
    before = batch.at(-1)?.signature;
    const oldest = Math.min(...batch.map((tx) => tx.timestamp ?? Infinity));
    console.log(`Helius page ${page + 1}: total=${all.length}, oldest=${new Date(oldest * 1000).toISOString()}`);
    if (oldest < cutoffTs - 3600) break;
    await sleep(PAGE_SLEEP_MS);
  }
  return dedupeTxs(all).filter((tx) => (tx.timestamp ?? 0) >= cutoffTs - 3600);
}

function extractSwap(tx, wallet) {
  let nativeNet = 0;
  const tokenNet = new Map();
  const decimalsByMint = new Map();

  for (const account of tx.accountData ?? []) {
    if (account.account === wallet) nativeNet += account.nativeBalanceChange ?? 0;
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.userAccount !== wallet) continue;
      const mint = change.mint;
      const raw = Number(change.rawTokenAmount?.tokenAmount ?? "0");
      if (!mint || !Number.isFinite(raw) || raw === 0) continue;
      tokenNet.set(mint, (tokenNet.get(mint) ?? 0) + raw);
      decimalsByMint.set(mint, change.rawTokenAmount?.decimals ?? 0);
    }
  }

  let mint = null;
  let tokenRaw = 0;
  for (const [candidate, raw] of tokenNet) {
    if (candidate === WRAPPED_SOL_MINT) continue;
    if (Math.abs(raw) > Math.abs(tokenRaw)) {
      mint = candidate;
      tokenRaw = raw;
    }
  }

  const wsolNet = tokenNet.get(WRAPPED_SOL_MINT) ?? 0;
  const quoteLamports =
    tokenRaw > 0 && wsolNet < 0
      ? Math.abs(wsolNet)
      : tokenRaw < 0 && wsolNet > 0
        ? wsolNet
        : tokenRaw > 0 && nativeNet < 0
          ? Math.abs(nativeNet)
          : tokenRaw < 0 && nativeNet > 0
            ? nativeNet
            : 0;

  if (!mint || tokenRaw === 0 || quoteLamports <= 0) return null;
  return {
    signature: tx.signature,
    timestamp: tx.timestamp ?? 0,
    side: tokenRaw > 0 ? "buy" : "sell",
    mint,
    solLamports: quoteLamports,
    tokenRaw: Math.abs(tokenRaw),
    decimals: decimalsByMint.get(mint) ?? 0,
    source: tx.source ?? "",
  };
}

function buildCampaigns(swaps) {
  const campaigns = new Map();
  for (const swap of swaps.sort((a, b) => a.timestamp - b.timestamp)) {
    let c = campaigns.get(swap.mint);
    if (!c) {
      if (swap.side !== "buy") continue;
      c = {
        mint: swap.mint,
        firstBuy: swap,
        firstSell: null,
        spentLamports: 0,
        boughtRaw: 0,
        receivedLamports: 0,
        soldRaw: 0,
        decimals: swap.decimals,
        buys: [],
        sells: [],
      };
      campaigns.set(swap.mint, c);
    }
    if (swap.side === "buy") {
      c.spentLamports += swap.solLamports;
      c.boughtRaw += swap.tokenRaw;
      c.buys.push(swap);
    } else {
      c.receivedLamports += swap.solLamports;
      c.soldRaw += swap.tokenRaw;
      c.sells.push(swap);
      c.firstSell ??= swap;
    }
  }
  return [...campaigns.values()].sort((a, b) => b.firstBuy.timestamp - a.firstBuy.timestamp);
}

function sourceFillMarketCapUsd(campaign) {
  const uiTokens = campaign.firstBuy.tokenRaw / 10 ** campaign.decimals;
  if (!SOL_USD || !Number.isFinite(uiTokens) || uiTokens <= 0) return null;
  return (sol(campaign.firstBuy.solLamports) * SOL_USD * SUPPLY_APPROX) / uiTokens;
}

function sourceExitPct(campaign) {
  if (!campaign.firstSell || campaign.spentLamports <= 0 || campaign.boughtRaw <= 0 || campaign.firstSell.tokenRaw <= 0) {
    return null;
  }
  const avgEntry = campaign.spentLamports / campaign.boughtRaw;
  const firstSell = campaign.firstSell.solLamports / campaign.firstSell.tokenRaw;
  return ((firstSell - avgEntry) / avgEntry) * 100;
}

function markdown(output) {
  const rows = output.pumpfunMcRows
    .map(
      (row) =>
        `| \`${short(row.mint)}\` | ${row.entryIso} | ${fmtUsd(row.entryMcUsd)} | ${fmtSol(row.firstBuySol)} | ${row.buyCount} | ${row.sellCount} | ${
          row.firstSellPct == null ? "-" : `${row.firstSellPct >= 0 ? "+" : ""}${row.firstSellPct.toFixed(1)}%`
        } |`
    )
    .join("\n");

  return `# CyA PumpFun/PumpSwap Discovery - Last ${output.hours}h

Generated: ${output.generatedAt}

Assumptions:
- PumpFun/PumpSwap token = mint ending in \`pump\`.
- Entry MC is inferred from CyA's first fill, SOL/USD=${output.solUsd}, supply=${output.supplyApprox.toLocaleString("en-US")}.
- This is a fast discovery report: no candle/MFE simulation, no Gecko/CoinGecko calls.

## Counts

- Wallet txs fetched: ${output.coverage.walletTxs}
- Swaps reconstructed: ${output.coverage.swaps}
- Total campaigns: ${output.coverage.campaigns}
- PumpFun/PumpSwap campaigns: ${output.coverage.pumpfunCampaigns}
- PumpFun/PumpSwap MC >= ${fmtUsd(output.minEntryMcUsd)} campaigns: ${output.coverage.pumpfunMcCampaigns}
- Median PumpFun/PumpSwap entry MC: ${fmtUsd(output.entryMc.medianPumpfun)}
- Median PumpFun/PumpSwap MC>=gate entry MC: ${fmtUsd(output.entryMc.medianPumpfunMc)}

## PumpFun/PumpSwap MC >= ${fmtUsd(output.minEntryMcUsd)}

| Mint | Entry UTC | Entry MC | First Buy | Buys | Sells | CyA First Sell |
|---|---:|---:|---:|---:|---:|---:|
${rows || "None"}
`;
}

async function main() {
  const nowTs = Math.floor(Date.now() / 1000);
  const cutoffTs = nowTs - HOURS * 3600;
  const txs = await fetchWalletTxsSince(WALLET, cutoffTs);
  const swaps = txs.map((tx) => extractSwap(tx, WALLET)).filter(Boolean);
  const campaigns = buildCampaigns(swaps).filter((campaign) => campaign.firstBuy.timestamp >= cutoffTs);
  const pumpfunCampaigns = campaigns.filter((campaign) => campaign.mint.endsWith("pump"));
  const pumpfunRows = pumpfunCampaigns.map((campaign) => ({
    campaign,
    entryMcUsd: sourceFillMarketCapUsd(campaign),
  }));
  const pumpfunMcRows = pumpfunRows
    .filter((row) => (row.entryMcUsd ?? 0) >= MIN_ENTRY_MC_USD)
    .map(({ campaign, entryMcUsd }) => ({
      mint: campaign.mint,
      entryIso: new Date(campaign.firstBuy.timestamp * 1000).toISOString(),
      entryMcUsd,
      firstBuySol: sol(campaign.firstBuy.solLamports),
      buyCount: campaign.buys.length,
      sellCount: campaign.sells.length,
      firstSellPct: sourceExitPct(campaign),
      firstBuySignature: campaign.firstBuy.signature,
      firstSellSignature: campaign.firstSell?.signature ?? null,
    }))
    .sort((a, b) => b.entryMcUsd - a.entryMcUsd);

  const output = {
    generatedAt: new Date().toISOString(),
    wallet: WALLET,
    hours: HOURS,
    minEntryMcUsd: MIN_ENTRY_MC_USD,
    solUsd: SOL_USD,
    supplyApprox: SUPPLY_APPROX,
    coverage: {
      walletTxs: txs.length,
      swaps: swaps.length,
      campaigns: campaigns.length,
      pumpfunCampaigns: pumpfunCampaigns.length,
      pumpfunMcCampaigns: pumpfunMcRows.length,
    },
    entryMc: {
      medianPumpfun: median(pumpfunRows.map((row) => row.entryMcUsd)),
      medianPumpfunMc: median(pumpfunMcRows.map((row) => row.entryMcUsd)),
    },
    pumpfunMcRows,
  };

  await fs.mkdir(path.dirname(OUT_JSON), { recursive: true });
  await fs.writeFile(OUT_JSON, JSON.stringify(output, null, 2), "utf8");
  await fs.writeFile(OUT_MD, markdown(output), "utf8");
  console.log(JSON.stringify({ outputJson: OUT_JSON, outputMd: OUT_MD, ...output.coverage }, null, 2));
}

main().catch((err) => {
  console.error(err instanceof Error ? err.stack ?? err.message : String(err));
  process.exit(1);
});
