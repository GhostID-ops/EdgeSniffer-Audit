import "dotenv/config";
import fs from "node:fs/promises";
import path from "node:path";

const HELIUS_API_KEY = process.env.HELIUS_API_KEY ?? "";
const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const CACHE_DIR = path.resolve(process.cwd(), "data", "cya-lead-cache");
const OUT_JSON = path.resolve(process.cwd(), "data", "cya-lead-signals.json");
const OUT_MD = path.resolve(process.cwd(), "data", "cya-lead-signals.md");

const targetWallet = process.argv[2] ?? "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o";
const maxTxs = Number(process.argv[3] ?? "3000");
const closedLimit = Number(process.argv[4] ?? "120");
const historyLimit = Number(process.argv[5] ?? "500");
const lookbackSeconds = Number(process.argv[6] ?? "240");

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function short(value) {
  if (!value) return "-";
  return `${value.slice(0, 6)}...${value.slice(-4)}`;
}

function sol(lamports) {
  return lamports / 1_000_000_000;
}

function fmtSol(value) {
  return `${value >= 0 ? "+" : ""}${value.toFixed(3)} SOL`;
}

function fmtPct(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function avg(values) {
  return values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);
}

function median(values) {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!xs.length) return null;
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[mid] : (xs[mid - 1] + xs[mid]) / 2;
}

async function cacheGet(key) {
  try {
    return JSON.parse(await fs.readFile(path.join(CACHE_DIR, key), "utf8"));
  } catch {
    return null;
  }
}

async function cacheSet(key, value) {
  await fs.mkdir(CACHE_DIR, { recursive: true });
  await fs.writeFile(path.join(CACHE_DIR, key), JSON.stringify(value), "utf8");
}

async function fetchJsonCached(key, fetcher) {
  const cached = await cacheGet(key);
  if (cached) return cached;
  const value = await fetcher();
  await cacheSet(key, value);
  return value;
}

function heliusAddressTransactionsUrl(address, before, limit) {
  if (!HELIUS_API_KEY) throw new Error("HELIUS_API_KEY is not set");
  const params = new URLSearchParams({
    "api-key": HELIUS_API_KEY,
    limit: String(limit),
  });
  if (before) params.set("before", before);
  return `https://api-mainnet.helius-rpc.com/v0/addresses/${address}/transactions?${params}`;
}

async function fetchAddressTxs(address, limit, before) {
  const response = await fetch(heliusAddressTransactionsUrl(address, before, limit), {
    signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) throw new Error(`Helius ${response.status}: ${(await response.text()).slice(0, 220)}`);
  return response.json();
}

async function fetchPagedAddressTxs(address, limit) {
  return fetchJsonCached(`wallet-${address}-${limit}.json`, async () => {
    const all = [];
    let before;
    while (all.length < limit) {
      const batch = await fetchAddressTxs(address, Math.min(100, limit - all.length), before);
      if (!batch.length) break;
      all.push(...batch);
      before = batch.at(-1)?.signature;
      await sleep(650);
    }
    return all;
  });
}

function extractSwap(tx, wallet) {
  if (!tx) return null;
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
  };
}

function buildCampaigns(swaps) {
  const byMint = new Map();
  for (const swap of swaps.sort((a, b) => a.timestamp - b.timestamp)) {
    let campaign = byMint.get(swap.mint);
    if (!campaign) {
      if (swap.side !== "buy") continue;
      campaign = {
        mint: swap.mint,
        firstBuy: swap,
        buys: [],
        sells: [],
        spent: 0,
        boughtRaw: 0,
        received: 0,
        soldRaw: 0,
      };
      byMint.set(swap.mint, campaign);
    }
    if (swap.side === "buy") {
      campaign.buys.push(swap);
      campaign.spent += swap.solLamports;
      campaign.boughtRaw += swap.tokenRaw;
    } else {
      campaign.sells.push(swap);
      campaign.received += swap.solLamports;
      campaign.soldRaw += swap.tokenRaw;
    }
  }
  return [...byMint.values()].sort((a, b) => b.firstBuy.timestamp - a.firstBuy.timestamp);
}

function campaignPnl(campaign) {
  if (campaign.spent <= 0 || campaign.received <= 0 || campaign.boughtRaw <= 0 || campaign.soldRaw <= 0) {
    return null;
  }
  const costBasis = campaign.spent * (Math.min(campaign.soldRaw, campaign.boughtRaw) / campaign.boughtRaw);
  const pnl = campaign.received - costBasis;
  return {
    costBasis,
    pnl,
    roiPct: costBasis > 0 ? (pnl / costBasis) * 100 : 0,
  };
}

function extractTokenWallets(tx, mint) {
  const wallets = new Set();
  for (const transfer of tx.tokenTransfers ?? []) {
    if (transfer.mint !== mint) continue;
    if (transfer.fromUserAccount) wallets.add(transfer.fromUserAccount);
    if (transfer.toUserAccount) wallets.add(transfer.toUserAccount);
  }
  for (const account of tx.accountData ?? []) {
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.mint === mint && change.userAccount) wallets.add(change.userAccount);
    }
  }
  return [...wallets];
}

function inferCreator(txs, mint) {
  const createTx =
    txs.find((tx) => tx.type === "CREATE" || tx.type === "CREATE_POOL") ??
    txs.find((tx) => extractTokenWallets(tx, mint).length > 0);
  return createTx?.feePayer ?? null;
}

async function fetchMintHistoryUntilSource(mint, sourceBuy) {
  return fetchJsonCached(`mintlead-${mint}-${sourceBuy.signature}-${historyLimit}-${lookbackSeconds}.json`, async () => {
    const all = [];
    let before;
    while (all.length < historyLimit) {
      const batch = await fetchAddressTxs(mint, Math.min(100, historyLimit - all.length), before);
      if (!batch.length) break;
      all.push(...batch);
      const oldest = batch.at(-1)?.timestamp ?? 0;
      if (all.some((tx) => tx.signature === sourceBuy.signature) || oldest < sourceBuy.timestamp - lookbackSeconds) break;
      before = batch.at(-1)?.signature;
      await sleep(650);
    }
    return all.sort((a, b) => (a.timestamp ?? 0) - (b.timestamp ?? 0));
  });
}

function profileLead(mint, txs, sourceWallet, sourceBuy) {
  const firstSeenAt = txs[0]?.timestamp ?? sourceBuy.timestamp;
  const buys = [];
  for (const tx of txs) {
    if ((tx.timestamp ?? 0) > sourceBuy.timestamp) continue;
    for (const wallet of extractTokenWallets(tx, mint)) {
      if (wallet === sourceWallet) continue;
      const swap = extractSwap(tx, wallet);
      if (swap?.mint === mint && swap.side === "buy") {
        buys.push({
          wallet,
          signature: swap.signature,
          timestamp: swap.timestamp,
          leadSeconds: sourceBuy.timestamp - swap.timestamp,
          solLamports: swap.solLamports,
        });
      }
    }
  }
  buys.sort((a, b) => a.timestamp - b.timestamp || b.solLamports - a.solLamports);
  const sourceSwap = extractSwap(txs.find((tx) => tx.signature === sourceBuy.signature), sourceWallet);
  const sourceOrder =
    sourceSwap == null
      ? null
      : buys.filter((buy) => buy.timestamp <= sourceBuy.timestamp).length + 1;
  const byWallet = new Map();
  for (const buy of buys) {
    const current = byWallet.get(buy.wallet) ?? {
      wallet: buy.wallet,
      buys: 0,
      solLamports: 0,
      minLeadSeconds: buy.leadSeconds,
    };
    current.buys++;
    current.solLamports += buy.solLamports;
    current.minLeadSeconds = Math.min(current.minLeadSeconds, buy.leadSeconds);
    byWallet.set(buy.wallet, current);
  }
  return {
    creator: inferCreator(txs, mint),
    sourceDelaySec: Math.max(0, sourceBuy.timestamp - firstSeenAt),
    sourceOrder,
    earlierBuys: buys.length,
    earlierBuySol: sol(buys.reduce((sum, buy) => sum + buy.solLamports, 0)),
    earlierWallets: [...byWallet.values()],
  };
}

function addAgg(map, key, campaign, leadExtra = {}) {
  if (!key) return;
  const stats = map.get(key) ?? {
    id: key,
    appearances: 0,
    winnerAppearances: 0,
    loserAppearances: 0,
    pnlSol: 0,
    roiPcts: [],
    examples: [],
    leadSeconds: [],
    earlierBuySol: 0,
  };
  stats.appearances++;
  if (campaign.roiPct > 0) stats.winnerAppearances++;
  else stats.loserAppearances++;
  stats.pnlSol += sol(campaign.pnl);
  stats.roiPcts.push(campaign.roiPct);
  if (leadExtra.minLeadSeconds != null) stats.leadSeconds.push(leadExtra.minLeadSeconds);
  if (leadExtra.solLamports != null) stats.earlierBuySol += sol(leadExtra.solLamports);
  if (stats.examples.length < 5) {
    stats.examples.push({
      mint: campaign.mint,
      roiPct: campaign.roiPct,
      pnlSol: sol(campaign.pnl),
      sourceBuySol: sol(campaign.sourceBuy.solLamports),
    });
  }
  map.set(key, stats);
}

function rankAgg(map) {
  return [...map.values()]
    .map((stats) => ({
      ...stats,
      winRatePct: (stats.winnerAppearances / Math.max(1, stats.appearances)) * 100,
      avgRoiPct: avg(stats.roiPcts),
      medianRoiPct: median(stats.roiPcts),
      medianLeadSeconds: median(stats.leadSeconds),
      score:
        stats.winnerAppearances * 8 -
        stats.loserAppearances * 6 +
        avg(stats.roiPcts) * 0.4 +
        Math.min(stats.pnlSol, 50),
    }))
    .filter((stats) => stats.appearances >= 2 || stats.winnerAppearances >= 2)
    .sort((a, b) => b.score - a.score);
}

function markdown(output) {
  const lines = [
    "# CyA Lead Signals",
    "",
    `Generated: ${output.generatedAt}`,
    `Target: \`${output.targetWallet}\``,
    `Campaigns profiled: ${output.profiledCampaigns}/${output.closedCampaigns}`,
    "",
    "## Summary",
    "",
    `- Winners profiled: ${output.winners}`,
    `- Losers profiled: ${output.losers}`,
    `- Median CyA ROI: ${fmtPct(output.medianRoiPct)}`,
    `- Median source order: ${output.medianSourceOrder ?? "-"}`,
    `- Median earlier buys: ${output.medianEarlierBuys ?? "-"}`,
    "",
    "## Earlier Wallet Candidates",
    "",
    "| Wallet | Seen | W/L | Win | Avg ROI | Median Lead | Earlier Buy SOL | Examples |",
    "|---|---:|---:|---:|---:|---:|---:|---|",
    ...output.topEarlierWallets.slice(0, 25).map((row) =>
      `| \`${row.id}\` | ${row.appearances} | ${row.winnerAppearances}/${row.loserAppearances} | ${fmtPct(row.winRatePct)} | ${fmtPct(row.avgRoiPct)} | ${row.medianLeadSeconds == null ? "-" : `${Math.round(row.medianLeadSeconds)}s`} | ${row.earlierBuySol.toFixed(3)} | ${row.examples.map((ex) => `${short(ex.mint)} ${fmtPct(ex.roiPct)}`).join(", ")} |`
    ),
    "",
    "## Creator / Deployer Candidates",
    "",
    "| Creator | Seen | W/L | Win | Avg ROI | Examples |",
    "|---|---:|---:|---:|---:|---|",
    ...output.topCreators.slice(0, 25).map((row) =>
      `| \`${row.id}\` | ${row.appearances} | ${row.winnerAppearances}/${row.loserAppearances} | ${fmtPct(row.winRatePct)} | ${fmtPct(row.avgRoiPct)} | ${row.examples.map((ex) => `${short(ex.mint)} ${fmtPct(ex.roiPct)}`).join(", ")} |`
    ),
    "",
  ];
  return `${lines.join("\n")}\n`;
}

async function main() {
  const walletTxs = await fetchPagedAddressTxs(targetWallet, maxTxs);
  const swaps = walletTxs.map((tx) => extractSwap(tx, targetWallet)).filter(Boolean);
  const campaigns = buildCampaigns(swaps)
    .map((campaign) => ({ campaign, pnl: campaignPnl(campaign) }))
    .filter((row) => row.pnl)
    .map((row) => ({
      mint: row.campaign.mint,
      sourceBuy: row.campaign.firstBuy,
      pnl: row.pnl.pnl,
      roiPct: row.pnl.roiPct,
      costBasis: row.pnl.costBasis,
      sells: row.campaign.sells.length,
    }))
    .sort((a, b) => b.sourceBuy.timestamp - a.sourceBuy.timestamp);

  const selected = campaigns.slice(0, closedLimit);
  const walletAgg = new Map();
  const creatorAgg = new Map();
  const profiled = [];

  for (const [index, campaign] of selected.entries()) {
    const txs = await fetchMintHistoryUntilSource(campaign.mint, campaign.sourceBuy);
    const lead = profileLead(campaign.mint, txs, targetWallet, campaign.sourceBuy);
    for (const earlier of lead.earlierWallets) {
      addAgg(walletAgg, earlier.wallet, campaign, earlier);
    }
    addAgg(creatorAgg, lead.creator, campaign);
    profiled.push({ ...campaign, lead });
    console.log(
      `${index + 1}/${selected.length} ${short(campaign.mint)} roi=${fmtPct(campaign.roiPct)} ` +
        `earlier=${lead.earlierBuys} creator=${short(lead.creator)}`
    );
    await sleep(150);
  }

  const output = {
    generatedAt: new Date().toISOString(),
    targetWallet,
    maxTxs,
    closedCampaigns: campaigns.length,
    profiledCampaigns: profiled.length,
    winners: profiled.filter((row) => row.roiPct > 0).length,
    losers: profiled.filter((row) => row.roiPct <= 0).length,
    medianRoiPct: median(profiled.map((row) => row.roiPct)),
    medianSourceOrder: median(profiled.map((row) => row.lead.sourceOrder ?? NaN)),
    medianEarlierBuys: median(profiled.map((row) => row.lead.earlierBuys)),
    topEarlierWallets: rankAgg(walletAgg),
    topCreators: rankAgg(creatorAgg),
    profiled: profiled.map((row) => ({
      mint: row.mint,
      sourceBuySignature: row.sourceBuy.signature,
      sourceBuyAt: new Date(row.sourceBuy.timestamp * 1000).toISOString(),
      sourceBuySol: sol(row.sourceBuy.solLamports),
      pnlSol: sol(row.pnl),
      roiPct: row.roiPct,
      lead: {
        creator: row.lead.creator,
        sourceOrder: row.lead.sourceOrder,
        sourceDelaySec: row.lead.sourceDelaySec,
        earlierBuys: row.lead.earlierBuys,
        earlierBuySol: row.lead.earlierBuySol,
        earlierWallets: row.lead.earlierWallets.slice(0, 20),
      },
    })),
  };

  await fs.mkdir(path.dirname(OUT_JSON), { recursive: true });
  await fs.writeFile(OUT_JSON, JSON.stringify(output, null, 2), "utf8");
  await fs.writeFile(OUT_MD, markdown(output), "utf8");
  console.log("");
  console.log(markdown(output));
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
