import fs from "node:fs";
import path from "node:path";

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";

function arg(name, fallback = null) {
  const prefix = `--${name}=`;
  const hit = process.argv.find((value) => value.startsWith(prefix));
  return hit ? hit.slice(prefix.length) : fallback;
}

const wallet = arg("wallet", process.argv[2]);
if (!wallet) {
  console.error("Usage: node scripts/offlineWalletCopyResearch.mjs --wallet=WALLET [--cache=FILE] [--edge-root=/root/bot/EdgeSniffer] [--out-dir=data/wallet-audits/tag]");
  process.exit(1);
}

const cachePath = path.resolve(arg("cache", `data/wallet-quant-audit-cache/wallet-${wallet}-10000.json`));
const edgeRoot = arg("edge-root", "/root/bot/EdgeSniffer");
const outDir = path.resolve(arg("out-dir", path.join("data", "wallet-audits", `offline-${wallet.slice(0, 8)}-${stamp()}`)));
const label = arg("label", wallet.slice(0, 8));

function stamp() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

function sol(lamports) {
  return lamports / 1_000_000_000;
}

function pct(numer, denom) {
  return denom ? (numer / denom) * 100 : 0;
}

function fmtSol(value) {
  return `${value >= 0 ? "+" : ""}${value.toFixed(4)} SOL`;
}

function fmtPct(value) {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function median(values) {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!xs.length) return null;
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[mid] : (xs[mid - 1] + xs[mid]) / 2;
}

function quantile(values, q) {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!xs.length) return null;
  return xs[Math.min(xs.length - 1, Math.max(0, Math.floor((xs.length - 1) * q)))];
}

function avg(values) {
  return values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function readJsonl(file) {
  if (!fs.existsSync(file)) return [];
  return fs.readFileSync(file, "utf8")
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    })
    .filter(Boolean);
}

function extractSwap(tx) {
  let nativeNet = 0;
  const tokenNet = new Map();
  for (const account of tx.accountData ?? []) {
    if (account.account === wallet) nativeNet += account.nativeBalanceChange ?? 0;
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.userAccount !== wallet) continue;
      const mint = change.mint;
      const raw = Number(change.rawTokenAmount?.tokenAmount ?? "0");
      if (!mint || !Number.isFinite(raw) || raw === 0) continue;
      tokenNet.set(mint, (tokenNet.get(mint) ?? 0) + raw);
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
    sol: sol(quoteLamports),
    tokenRaw: Math.abs(tokenRaw),
  };
}

function buildCampaigns(swaps) {
  const byMint = new Map();
  for (const swap of [...swaps].sort((a, b) => a.timestamp - b.timestamp)) {
    let campaign = byMint.get(swap.mint);
    if (!campaign) {
      if (swap.side !== "buy") continue;
      campaign = {
        mint: swap.mint,
        firstTs: swap.timestamp,
        lastTs: swap.timestamp,
        buys: [],
        sells: [],
        spentSol: 0,
        receivedSol: 0,
        boughtRaw: 0,
        soldRaw: 0,
      };
      byMint.set(swap.mint, campaign);
    }
    campaign.lastTs = Math.max(campaign.lastTs, swap.timestamp);
    if (swap.side === "buy") {
      campaign.buys.push(swap);
      campaign.spentSol += swap.sol;
      campaign.boughtRaw += swap.tokenRaw;
    } else {
      campaign.sells.push(swap);
      campaign.receivedSol += swap.sol;
      campaign.soldRaw += swap.tokenRaw;
    }
  }

  return [...byMint.values()].map((campaign) => {
    const firstSellTs = campaign.sells[0]?.timestamp ?? null;
    const pnlSol = campaign.receivedSol - campaign.spentSol;
    return {
      ...campaign,
      firstSellTs,
      closed: campaign.receivedSol > 0,
      pnlSol,
      roiPct: campaign.spentSol > 0 ? (pnlSol / campaign.spentSol) * 100 : null,
      holdSec: firstSellTs ? firstSellTs - campaign.buys[0].timestamp : null,
      buyCount: campaign.buys.length,
      sellCount: campaign.sells.length,
      firstBuySol: campaign.buys[0]?.sol ?? 0,
      avgBuySol: avg(campaign.buys.map((buy) => buy.sol)),
      buyWindowSec: campaign.buys.length > 1 ? campaign.buys.at(-1).timestamp - campaign.buys[0].timestamp : 0,
      sellWindowSec: campaign.sells.length > 1 ? campaign.sells.at(-1).timestamp - campaign.sells[0].timestamp : 0,
      soldFraction: campaign.boughtRaw > 0 ? campaign.soldRaw / campaign.boughtRaw : 0,
    };
  });
}

function summarize(rows) {
  const closed = rows.filter((row) => row.closed);
  const wins = closed.filter((row) => row.pnlSol > 0);
  const grossWin = closed.filter((row) => row.pnlSol > 0).reduce((sum, row) => sum + row.pnlSol, 0);
  const grossLoss = Math.abs(closed.filter((row) => row.pnlSol < 0).reduce((sum, row) => sum + row.pnlSol, 0));
  return {
    campaigns: rows.length,
    closed: closed.length,
    open: rows.length - closed.length,
    winRatePct: pct(wins.length, closed.length),
    pnlSol: closed.reduce((sum, row) => sum + row.pnlSol, 0),
    grossWinSol: grossWin,
    grossLossSol: grossLoss,
    profitFactor: grossLoss ? grossWin / grossLoss : null,
    avgRoiPct: avg(closed.map((row) => row.roiPct ?? 0)),
    medianRoiPct: median(closed.map((row) => row.roiPct ?? NaN)),
    p10RoiPct: quantile(closed.map((row) => row.roiPct ?? NaN), 0.1),
    p90RoiPct: quantile(closed.map((row) => row.roiPct ?? NaN), 0.9),
    medianHoldSec: median(closed.map((row) => row.holdSec ?? NaN)),
    p90HoldSec: quantile(closed.map((row) => row.holdSec ?? NaN), 0.9),
    medianSpentSol: median(closed.map((row) => row.spentSol)),
    medianFirstBuySol: median(closed.map((row) => row.firstBuySol)),
    medianBuyCount: median(closed.map((row) => row.buyCount)),
    medianSellCount: median(closed.map((row) => row.sellCount)),
  };
}

function bucket(rows, name, classify) {
  const groups = new Map();
  for (const row of rows) {
    const key = classify(row);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  return [...groups.entries()]
    .map(([key, group]) => ({ name, bucket: key, ...summarize(group) }))
    .sort((a, b) => b.closed - a.closed);
}

function copySim(rows, { copyPct, maxBuySol, maxCampaignSol, maxBuys, mirrorSells, dragPctPerLeg, tipSolPerLeg }) {
  const resultRows = [];
  for (const row of rows.filter((candidate) => candidate.closed)) {
    const buys = row.buys.slice(0, maxBuys);
    let copiedSpent = 0;
    for (const buy of buys) {
      copiedSpent += Math.min(buy.sol * copyPct, maxBuySol);
      copiedSpent = Math.min(copiedSpent, maxCampaignSol);
      if (copiedSpent >= maxCampaignSol) break;
    }
    if (copiedSpent <= 0) continue;
    const sourceRoi = row.roiPct / 100;
    const exitMultiplier = mirrorSells ? 1 + sourceRoi : 1 + sourceRoi;
    const grossReceived = copiedSpent * exitMultiplier;
    const drag = (copiedSpent + Math.max(0, grossReceived)) * dragPctPerLeg;
    const tips = tipSolPerLeg * 2;
    const pnl = grossReceived - copiedSpent - drag - tips;
    resultRows.push({ mint: row.mint, copiedSpent, pnl, roiPct: copiedSpent ? (pnl / copiedSpent) * 100 : 0, sourceRoiPct: row.roiPct });
  }
  const wins = resultRows.filter((row) => row.pnl > 0);
  const grossWin = wins.reduce((sum, row) => sum + row.pnl, 0);
  const grossLoss = Math.abs(resultRows.filter((row) => row.pnl < 0).reduce((sum, row) => sum + row.pnl, 0));
  return {
    copyPct,
    maxBuySol,
    maxCampaignSol,
    maxBuys,
    mirrorSells,
    dragPctPerLeg,
    tipSolPerLeg,
    trades: resultRows.length,
    pnlSol: resultRows.reduce((sum, row) => sum + row.pnl, 0),
    winRatePct: pct(wins.length, resultRows.length),
    profitFactor: grossLoss ? grossWin / grossLoss : null,
    medianRoiPct: median(resultRows.map((row) => row.roiPct)),
    p10RoiPct: quantile(resultRows.map((row) => row.roiPct), 0.1),
    p90RoiPct: quantile(resultRows.map((row) => row.roiPct), 0.9),
    avgCopiedSpentSol: avg(resultRows.map((row) => row.copiedSpent)),
  };
}

function findEdgeRows(campaigns) {
  const files = [
    "data/2cq-live-dust-ledger-velocity7-wide-tagged.jsonl",
    "data/2cq-live-dust-ledger-broad-velocity7.jsonl",
    "data/2cq-live-dust-ledger.jsonl",
    "data/2cq-v4-frozen-runner-closed.jsonl",
    "data/2cq-v3-frozen-runner-closed.jsonl",
  ];
  const mintToCampaign = new Map(campaigns.map((campaign) => [campaign.mint, campaign]));
  const rows = [];
  const seen = new Set();
  const readTs = (row) => {
    const raw =
      row.openedAtMs ??
      row.entryAtMs ??
      row.buyAtMs ??
      row.position?.openedAtMs ??
      row.position?.entryAtMs ??
      row.openedAt ??
      row.entryTs ??
      row.buyTs ??
      row.position?.openedAt ??
      row.ts ??
      row.at ??
      row.time ??
      row.createdAt;
    if (typeof raw === "number") return raw > 10_000_000_000 ? Math.floor(raw / 1000) : raw;
    const parsed = Date.parse(raw ?? "");
    return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : null;
  };
  const isTradeRow = (row) => {
    if (row.status === "closed" || row.status === "open") return true;
    if (["trade_closed", "position_closed", "trade_completed", "buy_result", "live_trade_closed"].includes(row.event)) return true;
    if (row.closeReason || row.exitReason || row.closeSignature || row.sellSignature || row.buySignature) return true;
    return false;
  };
  for (const rel of files) {
    const full = path.join(edgeRoot, rel);
    for (const row of readJsonl(full)) {
      if (!isTradeRow(row)) continue;
      const mint = row.mint ?? row.ca ?? row.tokenMint ?? row.address;
      if (!mint || !mintToCampaign.has(mint)) continue;
      const campaign = mintToCampaign.get(mint);
      const ts = readTs(row);
      const key = `${rel}:${mint}:${ts ?? "na"}:${row.status ?? row.event ?? "trade"}`;
      if (seen.has(key)) continue;
      seen.add(key);
      rows.push({
        file: rel,
        mint,
        edgeTs: Number.isFinite(ts) ? ts : null,
        walletFirstTs: campaign.firstTs,
        edgeVsWalletSec: Number.isFinite(ts) ? ts - campaign.firstTs : null,
        walletRoiPct: campaign.roiPct,
        walletHoldSec: campaign.holdSec,
        edgeExit: row.exitReason ?? row.exit ?? row.closeReason ?? row.reason ?? null,
        edgeRoiPct: row.roiPct ?? row.curveRoiPct ?? row.liveCurveRoiPct ?? row.pnlPct ?? null,
      });
    }
  }
  return rows;
}

const txs = readJson(cachePath);
const swaps = txs.map(extractSwap).filter(Boolean).filter((swap) => swap.timestamp > 0);
const campaigns = buildCampaigns(swaps);
const closed = campaigns.filter((campaign) => campaign.closed);

const nowSec = Math.floor(Date.now() / 1000);
const windows = [
  ["all", () => true],
  ["last_24h", (row) => row.firstTs >= nowSec - 24 * 3600],
  ["last_72h", (row) => row.firstTs >= nowSec - 72 * 3600],
  ["last_7d", (row) => row.firstTs >= nowSec - 7 * 24 * 3600],
];

const bucketRows = [
  ...bucket(closed, "spentSol", (row) => row.spentSol < 1 ? "<1" : row.spentSol < 3 ? "1-3" : row.spentSol < 7 ? "3-7" : "7+"),
  ...bucket(closed, "buyCount", (row) => row.buyCount === 1 ? "1" : row.buyCount <= 3 ? "2-3" : row.buyCount <= 8 ? "4-8" : "9+"),
  ...bucket(closed, "holdSec", (row) => row.holdSec < 10 ? "<10s" : row.holdSec < 60 ? "10-60s" : row.holdSec < 180 ? "60-180s" : "180s+"),
  ...bucket(closed, "firstBuySol", (row) => row.firstBuySol < 0.5 ? "<0.5" : row.firstBuySol < 1 ? "0.5-1" : row.firstBuySol < 2 ? "1-2" : "2+"),
];

const copyScenarios = [];
for (const copyPct of [0.05, 0.1, 0.15, 0.2]) {
  for (const maxBuySol of [0.05, 0.1, 0.25, 0.5]) {
    for (const maxBuys of [1, 3, 8]) {
      copyScenarios.push(copySim(closed, {
        copyPct,
        maxBuySol,
        maxCampaignSol: maxBuySol * Math.max(1, maxBuys),
        maxBuys,
        mirrorSells: true,
        dragPctPerLeg: 0.006,
        tipSolPerLeg: 0.0005,
      }));
    }
  }
}
copyScenarios.sort((a, b) => b.pnlSol - a.pnlSol);

const edgeOverlaps = findEdgeRows(campaigns);

const report = {
  wallet,
  label,
  generatedAt: new Date().toISOString(),
  input: { cachePath, edgeRoot },
  coverage: {
    transactions: txs.length,
    swaps: swaps.length,
    buys: swaps.filter((swap) => swap.side === "buy").length,
    sells: swaps.filter((swap) => swap.side === "sell").length,
    campaigns: campaigns.length,
    closed: closed.length,
    firstTx: new Date(Math.min(...txs.map((tx) => tx.timestamp ?? Infinity)) * 1000).toISOString(),
    lastTx: new Date(Math.max(...txs.map((tx) => tx.timestamp ?? 0)) * 1000).toISOString(),
  },
  windows: Object.fromEntries(windows.map(([name, filter]) => [name, summarize(campaigns.filter(filter))])),
  buckets: bucketRows,
  copyScenarios: copyScenarios.slice(0, 20),
  recentCampaigns: [...campaigns].sort((a, b) => b.firstTs - a.firstTs).slice(0, 40),
  edgeOverlaps,
  recommendations: {
    posture: "shadow-first or dust/live small until same-slot latency and mirror-sell reliability are proven",
    copyMode: "mirror source buys and source sells; fixed TP/SL alone is mismatched to this wallet's multi-buy/multi-sell scalping behavior",
    initialCopyPct: 0.1,
    initialMaxBuySol: 0.1,
    initialMaxOpen: 1,
    initialMaxCampaignSol: 0.3,
    allowMultipleBuysPerMint: true,
    maxBuysPerMint: 3,
    mirrorSourceSells: true,
    fallbackStopLossPct: -12,
    fallbackMaxHoldSec: Math.ceil((quantile(closed.map((row) => row.holdSec ?? NaN), 0.9) ?? 180) * 1.25),
    notes: [
      "Wallet-level PnL is not enough; use this as candidate qualification and then verify copy latency on live shadow events.",
      "If copy-sell events are late or missed, this wallet's edge will not reproduce because exits are active and fast.",
      "Increase size only after same-slot/next-slot entry and sell mirror tests are clean.",
    ],
  },
};

fs.mkdirSync(outDir, { recursive: true });
const jsonPath = path.join(outDir, `${label}-offline-copy-research.json`);
const mdPath = path.join(outDir, `${label}-offline-copy-research.md`);
fs.writeFileSync(jsonPath, JSON.stringify(report, null, 2), "utf8");

const topBuckets = bucketRows
  .filter((row) => row.closed >= 20)
  .sort((a, b) => (b.profitFactor ?? 0) - (a.profitFactor ?? 0))
  .slice(0, 12);

const md = [
  `# Offline Wallet Copy Research - ${label}`,
  "",
  `Wallet: \`${wallet}\``,
  `Generated: ${report.generatedAt}`,
  "",
  "## Coverage",
  `- Transactions: ${report.coverage.transactions.toLocaleString("en-US")}`,
  `- Swaps: ${report.coverage.swaps.toLocaleString("en-US")} (${report.coverage.buys.toLocaleString("en-US")} buys / ${report.coverage.sells.toLocaleString("en-US")} sells)`,
  `- Campaigns: ${report.coverage.campaigns.toLocaleString("en-US")} (${report.coverage.closed.toLocaleString("en-US")} closed)`,
  `- Window: ${report.coverage.firstTx} -> ${report.coverage.lastTx}`,
  "",
  "## Summary",
  ...Object.entries(report.windows).map(([name, row]) => `- ${name}: closed=${row.closed}, pnl=${fmtSol(row.pnlSol)}, PF=${row.profitFactor?.toFixed(2) ?? "n/a"}, win=${row.winRatePct.toFixed(1)}%, medianROI=${fmtPct(row.medianRoiPct ?? 0)}, medianHold=${row.medianHoldSec?.toFixed(0) ?? "-"}s, medianSpent=${row.medianSpentSol?.toFixed(3) ?? "-"} SOL`),
  "",
  "## Strongest Buckets",
  "| Feature | Bucket | Closed | PnL | PF | Win | Median ROI | Median Hold |",
  "|---|---:|---:|---:|---:|---:|---:|---:|",
  ...topBuckets.map((row) => `| ${row.name} | ${row.bucket} | ${row.closed} | ${fmtSol(row.pnlSol)} | ${row.profitFactor?.toFixed(2) ?? "n/a"} | ${row.winRatePct.toFixed(1)}% | ${fmtPct(row.medianRoiPct ?? 0)} | ${row.medianHoldSec?.toFixed(0) ?? "-"}s |`),
  "",
  "## Best Copy Scenarios",
  "| Copy % | Max Buy | Max Buys | Trades | PnL | PF | Win | Median ROI | Avg Copied |",
  "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
  ...report.copyScenarios.slice(0, 12).map((row) => `| ${(row.copyPct * 100).toFixed(0)}% | ${row.maxBuySol.toFixed(3)} | ${row.maxBuys} | ${row.trades} | ${fmtSol(row.pnlSol)} | ${row.profitFactor?.toFixed(2) ?? "n/a"} | ${row.winRatePct.toFixed(1)}% | ${fmtPct(row.medianRoiPct ?? 0)} | ${row.avgCopiedSpentSol.toFixed(3)} |`),
  "",
  "## EdgeSniffer Overlaps",
  `- Overlap rows: ${edgeOverlaps.length}`,
  ...edgeOverlaps.slice(0, 20).map((row) => `- ${row.mint}: ${row.file}, edgeVsWallet=${row.edgeVsWalletSec?.toFixed(0) ?? "-"}s, walletROI=${fmtPct(row.walletRoiPct ?? 0)}, walletHold=${row.walletHoldSec ?? "-"}s, edgeExit=${row.edgeExit ?? "-"}`),
  "",
  "## Recommended Starting Settings",
  `- Copy mode: ${report.recommendations.copyMode}`,
  `- Initial copy: ${(report.recommendations.initialCopyPct * 100).toFixed(0)}% of source buy`,
  `- Max buy: ${report.recommendations.initialMaxBuySol} SOL`,
  `- Max open: ${report.recommendations.initialMaxOpen}`,
  `- Max campaign: ${report.recommendations.initialMaxCampaignSol} SOL`,
  `- Multi-buys: ${report.recommendations.allowMultipleBuysPerMint ? "yes" : "no"}, max ${report.recommendations.maxBuysPerMint}`,
  `- Mirror source sells: ${report.recommendations.mirrorSourceSells ? "yes" : "no"}`,
  `- Fallback SL: ${report.recommendations.fallbackStopLossPct}%`,
  `- Fallback max hold: ${report.recommendations.fallbackMaxHoldSec}s`,
  "",
  "## Caveats",
  ...report.recommendations.notes.map((note) => `- ${note}`),
  "",
].join("\n");
fs.writeFileSync(mdPath, md, "utf8");

console.log(`offline wallet copy research exported`);
console.log(`json=${jsonPath}`);
console.log(`md=${mdPath}`);
