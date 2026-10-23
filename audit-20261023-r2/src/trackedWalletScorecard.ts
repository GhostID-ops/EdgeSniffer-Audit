import "dotenv/config";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { heliusAddressTransactionsUrl } from "./config.js";

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const DEFAULT_WALLETS = [
  "2CQgjcdNEo7WtbQLpJTAVcC3Ga61pNvRDTgP5grzctFG",
  "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9",
  "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o",
  "4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk",
  "B27vwq89vX3oDa8fFNGiqQWB2wRiSmsmD8Hw2JuRefC8",
  "omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq",
];

type HeliusTx = {
  signature: string;
  timestamp?: number;
  accountData?: Array<{
    account: string;
    nativeBalanceChange?: number;
    tokenBalanceChanges?: Array<{
      userAccount?: string;
      mint?: string;
      rawTokenAmount?: { tokenAmount?: string; decimals?: number };
    }>;
  }>;
};

type Swap = {
  signature: string;
  timestamp: number;
  side: "buy" | "sell";
  mint: string;
  solLamports: number;
  tokenRaw: number;
};

type Campaign = {
  mint: string;
  firstBuy: Swap;
  lastSell?: Swap;
  spent: number;
  boughtRaw: number;
  received: number;
  soldRaw: number;
  buys: number;
  sells: number;
};

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function short(addr: string): string {
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

function sol(lamports: number): number {
  return lamports / 1_000_000_000;
}

function fmtSol(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(3)} SOL`;
}

function fmtPct(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function avg(values: number[]): number {
  return values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);
}

function median(values: number[]): number | null {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (xs.length === 0) return null;
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[mid]! : (xs[mid - 1]! + xs[mid]!) / 2;
}

function std(values: number[]): number {
  if (values.length < 2) return 0;
  const m = avg(values);
  return Math.sqrt(avg(values.map((v) => (v - m) ** 2)));
}

function maxDrawdown(values: number[]): number {
  let equity = 0;
  let peak = 0;
  let dd = 0;
  for (const value of values) {
    equity += value;
    peak = Math.max(peak, equity);
    dd = Math.min(dd, equity - peak);
  }
  return dd;
}

async function fetchWalletTxs(wallet: string, maxTxs: number): Promise<HeliusTx[]> {
  const all: HeliusTx[] = [];
  let before: string | undefined;
  while (all.length < maxTxs) {
    const url = heliusAddressTransactionsUrl(wallet, before, Math.min(100, maxTxs - all.length));
    if (!url) throw new Error("HELIUS_API_KEY is not set");
    const response = await fetch(url, { signal: AbortSignal.timeout(15_000) });
    if (!response.ok) throw new Error(`Helius ${response.status}: ${(await response.text()).slice(0, 180)}`);
    const batch = (await response.json()) as HeliusTx[];
    if (batch.length === 0) break;
    all.push(...batch);
    before = batch[batch.length - 1]?.signature;
    await sleep(650);
  }
  return all;
}

function extractSwap(tx: HeliusTx, wallet: string): Swap | null {
  let nativeNet = 0;
  const tokenNet = new Map<string, number>();
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

  let mint: string | null = null;
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
  };
}

function buildCampaigns(swaps: Swap[]): Campaign[] {
  const campaigns = new Map<string, Campaign>();
  for (const swap of swaps.sort((a, b) => a.timestamp - b.timestamp)) {
    let c = campaigns.get(swap.mint);
    if (!c) {
      if (swap.side !== "buy") continue;
      c = {
        mint: swap.mint,
        firstBuy: swap,
        spent: 0,
        boughtRaw: 0,
        received: 0,
        soldRaw: 0,
        buys: 0,
        sells: 0,
      };
      campaigns.set(swap.mint, c);
    }
    if (swap.side === "buy") {
      c.spent += swap.solLamports;
      c.boughtRaw += swap.tokenRaw;
      c.buys++;
    } else {
      c.received += swap.solLamports;
      c.soldRaw += swap.tokenRaw;
      c.sells++;
      c.lastSell = swap;
    }
  }
  return [...campaigns.values()];
}

function campaignPnl(c: Campaign): { pnl: number; roiPct: number; costBasis: number } | null {
  if (c.spent <= 0 || c.received <= 0 || c.soldRaw <= 0 || c.boughtRaw <= 0) return null;
  const costBasis = c.spent * (Math.min(c.soldRaw, c.boughtRaw) / c.boughtRaw);
  const pnl = c.received - costBasis;
  return { pnl, roiPct: costBasis > 0 ? (pnl / costBasis) * 100 : 0, costBasis };
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function scoreWallet(args: {
  closed: number;
  roiPct: number;
  profitFactor: number;
  winRatePct: number;
  sharpe: number;
  maxDrawdownSol: number;
  top5SharePct: number;
  recentRoiPct: number;
}): number {
  const sample = clamp((args.closed / 100) * 15, 0, 15);
  const roi = clamp(args.roiPct * 2.5, -25, 25);
  const pf = clamp((args.profitFactor - 1) * 20, -20, 25);
  const win = clamp((args.winRatePct - 45) * 0.8, -15, 15);
  const sharpe = clamp(args.sharpe * 10, -10, 15);
  const dd = clamp(args.maxDrawdownSol * 2, -20, 0);
  const concentration = args.top5SharePct > 80 ? -10 : args.top5SharePct > 55 ? -5 : 0;
  const recent = clamp(args.recentRoiPct * 1.5, -15, 15);
  return clamp(50 + sample + roi + pf + win + sharpe + dd + concentration + recent, 0, 100);
}

async function analyzeWallet(wallet: string, maxTxs: number) {
  const txs = await fetchWalletTxs(wallet, maxTxs);
  const swaps = txs.map((tx) => extractSwap(tx, wallet)).filter((swap): swap is Swap => swap != null);
  const campaigns = buildCampaigns(swaps);
  const closedCampaigns = campaigns.filter((c) => campaignPnl(c));
  const rows = closedCampaigns.map((c) => ({ c, ...campaignPnl(c)! }));
  const pnl = rows.map((r) => r.pnl);
  const roi = rows.map((r) => r.roiPct);
  const totalPnl = pnl.reduce((sum, value) => sum + value, 0);
  const totalCost = rows.reduce((sum, row) => sum + row.costBasis, 0);
  const wins = pnl.filter((v) => v > 0);
  const losses = pnl.filter((v) => v < 0);
  const grossProfit = wins.reduce((sum, value) => sum + value, 0);
  const grossLoss = Math.abs(losses.reduce((sum, value) => sum + value, 0));
  const sortedByTime = rows.sort((a, b) => a.c.firstBuy.timestamp - b.c.firstBuy.timestamp);
  const recentRows = sortedByTime.slice(Math.floor(sortedByTime.length / 2));
  const recentPnl = recentRows.reduce((sum, row) => sum + row.pnl, 0);
  const recentCost = recentRows.reduce((sum, row) => sum + row.costBasis, 0);
  const top5Wins = wins.sort((a, b) => b - a).slice(0, 5).reduce((sum, value) => sum + value, 0);
  const holdingMin = rows
    .filter((row) => row.c.lastSell)
    .map((row) => (row.c.lastSell!.timestamp - row.c.firstBuy.timestamp) / 60);
  const roiPct = totalCost > 0 ? (totalPnl / totalCost) * 100 : 0;
  const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0;
  const sharpe = std(roi) > 0 ? avg(roi) / std(roi) : 0;
  const maxDdSol = sol(maxDrawdown(pnl));
  const recentRoiPct = recentCost > 0 ? (recentPnl / recentCost) * 100 : 0;
  const top5SharePct = totalPnl > 0 ? (top5Wins / totalPnl) * 100 : 999;
  const score = scoreWallet({
    closed: rows.length,
    roiPct,
    profitFactor,
    winRatePct: (wins.length / Math.max(1, rows.length)) * 100,
    sharpe,
    maxDrawdownSol: maxDdSol,
    top5SharePct,
    recentRoiPct,
  });

  return {
    wallet,
    short: short(wallet),
    score,
    verdict: score >= 70 ? "FOLLOW" : score >= 55 ? "FOLLOW WITH FILTERS" : "DO NOT FOLLOW",
    txs: txs.length,
    swaps: swaps.length,
    buys: swaps.filter((s) => s.side === "buy").length,
    sells: swaps.filter((s) => s.side === "sell").length,
    campaigns: campaigns.length,
    closed: rows.length,
    realizedSol: sol(totalPnl),
    roiPct,
    winRatePct: (wins.length / Math.max(1, rows.length)) * 100,
    avgRoiPct: avg(roi),
    medianRoiPct: median(roi),
    profitFactor,
    sharpe,
    maxDrawdownSol: maxDdSol,
    avgHoldMin: avg(holdingMin),
    medianHoldMin: median(holdingMin),
    largestWinSol: sol(Math.max(...pnl, 0)),
    largestLossSol: sol(Math.min(...pnl, 0)),
    recentRoiPct,
    top5WinSharePct: top5SharePct,
  };
}

function markdown(results: Awaited<ReturnType<typeof analyzeWallet>>[], maxTxs: number): string {
  const generated = new Date().toISOString();
  const ranked = [...results].sort((a, b) => b.score - a.score);
  const lines = [
    "# Tracked Wallet Scorecard",
    "",
    `Generated: ${generated}`,
    `Method: Helius enhanced transaction reconstruction, latest ${maxTxs} txs per wallet.`,
    "Note: This scorecard is wallet-level on-chain performance only; candle/MFE backtests require Moralis quota and were not rerun here.",
    "",
    "## Ranking",
    "",
    "| Rank | Wallet | Score | Verdict | Realized | ROI | Win | PF | Closed | Median Hold | Recent ROI |",
    "|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|",
  ];
  ranked.forEach((r, i) => {
    lines.push(
      `| ${i + 1} | ${r.short} | ${r.score.toFixed(0)} | ${r.verdict} | ${fmtSol(r.realizedSol)} | ${fmtPct(r.roiPct)} | ${fmtPct(r.winRatePct)} | ${r.profitFactor.toFixed(2)} | ${r.closed} | ${r.medianHoldMin?.toFixed(1) ?? "-"}m | ${fmtPct(r.recentRoiPct)} |`
    );
  });
  lines.push("", "## Details", "");
  for (const r of ranked) {
    lines.push(
      `### ${r.wallet}`,
      "",
      `- Score: ${r.score.toFixed(0)}/100 (${r.verdict})`,
      `- Sample: ${r.txs} txs, ${r.swaps} swaps (${r.buys} buys / ${r.sells} sells), ${r.campaigns} campaigns, ${r.closed} closed`,
      `- Realized PnL: ${fmtSol(r.realizedSol)} (${fmtPct(r.roiPct)} on closed cost basis)`,
      `- Win rate: ${fmtPct(r.winRatePct)}; profit factor: ${r.profitFactor.toFixed(2)}; Sharpe proxy: ${r.sharpe.toFixed(2)}`,
      `- Avg/median ROI: ${fmtPct(r.avgRoiPct)} / ${fmtPct(r.medianRoiPct)}`,
      `- Avg/median hold: ${r.avgHoldMin.toFixed(1)}m / ${r.medianHoldMin?.toFixed(1) ?? "-"}m`,
      `- Largest win/loss: ${fmtSol(r.largestWinSol)} / ${fmtSol(r.largestLossSol)}`,
      `- Max drawdown: ${fmtSol(r.maxDrawdownSol)}; recent-half ROI: ${fmtPct(r.recentRoiPct)}; top-5 win concentration: ${fmtPct(r.top5WinSharePct)}`,
      ""
    );
  }
  return `${lines.join("\n")}\n`;
}

async function main(): Promise<void> {
  const walletsArg = process.argv[2] ?? "";
  const wallets = (walletsArg ? walletsArg.split(",") : DEFAULT_WALLETS)
    .map((w) => w.trim())
    .filter(Boolean);
  const maxTxs = Number(process.argv[3] ?? "3000");
  const results = [];
  for (const wallet of wallets) {
    console.log(`Analyzing ${short(wallet)}...`);
    results.push(await analyzeWallet(wallet, maxTxs));
  }
  const report = markdown(results, maxTxs);
  await mkdir(path.resolve(process.cwd(), "data"), { recursive: true });
  await writeFile(path.resolve(process.cwd(), "data", "tracked-wallet-scorecard.md"), report, "utf8");
  await writeFile(path.resolve(process.cwd(), "data", "tracked-wallet-scorecard.json"), JSON.stringify(results, null, 2), "utf8");
  console.log(report);
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
