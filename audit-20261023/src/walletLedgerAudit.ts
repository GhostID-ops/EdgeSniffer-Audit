import "dotenv/config";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { heliusAddressTransactionsUrl } from "./config.js";
import { getTokenPairInfo } from "./tokenRegistry.js";

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const CACHE_DIR = path.resolve(process.cwd(), "data", "wallet-ledger-cache");

type HeliusTx = {
  signature: string;
  timestamp?: number;
  type?: string;
  source?: string;
  fee?: number;
  feePayer?: string;
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

type TxFlow = {
  signature: string;
  timestamp: number;
  nativeLamports: number;
  wsolRaw: number;
  usdcRaw: number;
  tokenNet: Map<string, { raw: number; decimals: number }>;
};

type Lot = { raw: number; costLamports: number; timestamp: number };
type Position = {
  mint: string;
  decimals: number;
  lots: Lot[];
  boughtRaw: number;
  soldRaw: number;
  spentLamports: number;
  receivedLamports: number;
  realizedLamports: number;
  buys: number;
  sells: number;
  firstBuyTs: number;
  lastSellTs: number | null;
  transferInRaw: number;
  transferOutRaw: number;
};

type RealizedTrade = {
  mint: string;
  timestamp: number;
  pnlLamports: number;
  roiPct: number;
  proceedsLamports: number;
  costLamports: number;
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
  return `${value >= 0 ? "+" : ""}${value.toFixed(4)} SOL`;
}

function fmtUsd(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${value >= 0 ? "+" : ""}$${value.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function fmtPct(value: number | null): string {
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

async function cacheGet<T>(key: string): Promise<T | null> {
  try {
    return JSON.parse(await readFile(path.join(CACHE_DIR, key), "utf8")) as T;
  } catch {
    return null;
  }
}

async function cacheSet(key: string, value: unknown): Promise<void> {
  await mkdir(CACHE_DIR, { recursive: true });
  await writeFile(path.join(CACHE_DIR, key), JSON.stringify(value), "utf8");
}

async function fetchAddressTxs(address: string, limit: number, before?: string): Promise<HeliusTx[]> {
  const url = heliusAddressTransactionsUrl(address, before, limit);
  if (!url) throw new Error("HELIUS_API_KEY is not set");
  const response = await fetch(url, { signal: AbortSignal.timeout(15_000) });
  if (!response.ok) throw new Error(`Helius ${response.status}: ${(await response.text()).slice(0, 180)}`);
  return (await response.json()) as HeliusTx[];
}

async function fetchPagedWalletTxs(wallet: string, maxTxs: number, useCache: boolean): Promise<HeliusTx[]> {
  const key = `wallet-${wallet}-${maxTxs}.json`;
  if (useCache) {
    const cached = await cacheGet<HeliusTx[]>(key);
    if (cached) return cached;
  }
  const all: HeliusTx[] = [];
  let before: string | undefined;
  while (all.length < maxTxs) {
    const batch = await fetchAddressTxs(wallet, Math.min(100, maxTxs - all.length), before);
    if (batch.length === 0) break;
    all.push(...batch);
    before = batch[batch.length - 1]?.signature;
    await sleep(650);
  }
  await cacheSet(key, all);
  return all;
}

function extractFlow(tx: HeliusTx, wallet: string): TxFlow {
  const tokenNet = new Map<string, { raw: number; decimals: number }>();
  let nativeLamports = 0;
  let wsolRaw = 0;
  let usdcRaw = 0;

  for (const account of tx.accountData ?? []) {
    if (account.account === wallet) nativeLamports += account.nativeBalanceChange ?? 0;
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.userAccount !== wallet) continue;
      const mint = change.mint;
      const raw = Number(change.rawTokenAmount?.tokenAmount ?? "0");
      const decimals = change.rawTokenAmount?.decimals ?? 0;
      if (!mint || !Number.isFinite(raw) || raw === 0) continue;
      if (mint === WRAPPED_SOL_MINT) {
        wsolRaw += raw;
      } else if (mint === USDC_MINT) {
        usdcRaw += raw;
      } else {
        const entry = tokenNet.get(mint) ?? { raw: 0, decimals };
        entry.raw += raw;
        entry.decimals = decimals;
        tokenNet.set(mint, entry);
      }
    }
  }

  return {
    signature: tx.signature,
    timestamp: tx.timestamp ?? 0,
    nativeLamports,
    wsolRaw,
    usdcRaw,
    tokenNet,
  };
}

function getPosition(positions: Map<string, Position>, mint: string, decimals: number, timestamp: number): Position {
  let pos = positions.get(mint);
  if (!pos) {
    pos = {
      mint,
      decimals,
      lots: [],
      boughtRaw: 0,
      soldRaw: 0,
      spentLamports: 0,
      receivedLamports: 0,
      realizedLamports: 0,
      buys: 0,
      sells: 0,
      firstBuyTs: timestamp,
      lastSellTs: null,
      transferInRaw: 0,
      transferOutRaw: 0,
    };
    positions.set(mint, pos);
  }
  return pos;
}

function consumeLots(pos: Position, rawToSell: number): { cost: number; rawCosted: number } {
  let remaining = rawToSell;
  let cost = 0;
  let rawCosted = 0;
  while (remaining > 0 && pos.lots.length > 0) {
    const lot = pos.lots[0]!;
    const take = Math.min(remaining, lot.raw);
    const lotCost = lot.costLamports * (take / lot.raw);
    cost += lotCost;
    rawCosted += take;
    lot.raw -= take;
    lot.costLamports -= lotCost;
    remaining -= take;
    if (lot.raw <= 0.000001) pos.lots.shift();
  }
  return { cost, rawCosted };
}

function applyFlow(
  flow: TxFlow,
  positions: Map<string, Position>,
  realizedTrades: RealizedTrade[]
): { classified: boolean; note: string } {
  const quoteLamports = flow.nativeLamports + flow.wsolRaw;
  const tokenEntries = [...flow.tokenNet.entries()].filter(([, net]) => net.raw !== 0);
  if (tokenEntries.length === 0) return { classified: false, note: "no non-quote token delta" };

  const [mint, net] = tokenEntries.sort((a, b) => Math.abs(b[1].raw) - Math.abs(a[1].raw))[0]!;
  const pos = getPosition(positions, mint, net.decimals, flow.timestamp);
  pos.firstBuyTs = Math.min(pos.firstBuyTs, flow.timestamp || pos.firstBuyTs);

  if (net.raw > 0 && quoteLamports < 0) {
    const cost = Math.abs(quoteLamports);
    pos.lots.push({ raw: net.raw, costLamports: cost, timestamp: flow.timestamp });
    pos.boughtRaw += net.raw;
    pos.spentLamports += cost;
    pos.buys++;
    return { classified: true, note: "buy" };
  }

  if (net.raw < 0 && quoteLamports > 0) {
    const soldRaw = Math.abs(net.raw);
    const { cost, rawCosted } = consumeLots(pos, soldRaw);
    const proceeds = quoteLamports * (rawCosted > 0 ? rawCosted / soldRaw : 1);
    const pnl = proceeds - cost;
    pos.soldRaw += soldRaw;
    pos.receivedLamports += quoteLamports;
    pos.realizedLamports += pnl;
    pos.sells++;
    pos.lastSellTs = flow.timestamp;
    if (cost > 0) {
      realizedTrades.push({
        mint,
        timestamp: flow.timestamp,
        pnlLamports: pnl,
        roiPct: (pnl / cost) * 100,
        proceedsLamports: proceeds,
        costLamports: cost,
      });
    }
    if (rawCosted < soldRaw) pos.transferOutRaw += soldRaw - rawCosted;
    return { classified: true, note: "sell" };
  }

  if (net.raw > 0) pos.transferInRaw += net.raw;
  if (net.raw < 0) pos.transferOutRaw += Math.abs(net.raw);
  return { classified: false, note: "token transfer or unpriced route" };
}

async function estimateOpenValueLamports(pos: Position, solUsd: number): Promise<{
  valueLamports: number;
  valueUsd: number;
  symbol: string;
  priceSource: string;
} | null> {
  const heldRaw = pos.lots.reduce((sum, lot) => sum + lot.raw, 0);
  if (heldRaw <= 0) return null;
  const pair = await getTokenPairInfo(pos.mint);
  if (!pair) return null;
  const res = await fetch(`https://api.dexscreener.com/latest/dex/tokens/${pos.mint}`, {
    signal: AbortSignal.timeout(10_000),
  });
  if (!res.ok) return null;
  const json = (await res.json()) as {
    pairs?: Array<{
      chainId?: string;
      priceUsd?: string;
      baseToken?: { address?: string; symbol?: string };
      quoteToken?: { address?: string; symbol?: string };
      liquidity?: { usd?: number };
    }>;
  };
  const pairs = (json.pairs ?? []).filter((p) => p.chainId === "solana");
  if (pairs.length === 0) return null;
  const best = pairs.reduce((a, b) => (b.liquidity?.usd ?? 0) > (a.liquidity?.usd ?? 0) ? b : a);
  const priceUsd = Number(best.priceUsd ?? "0");
  if (!Number.isFinite(priceUsd) || priceUsd <= 0 || solUsd <= 0) return null;
  const amount = heldRaw / 10 ** pos.decimals;
  const valueUsd = amount * priceUsd;
  return {
    valueLamports: Math.round((valueUsd / solUsd) * 1_000_000_000),
    valueUsd,
    symbol: best.baseToken?.address === pos.mint ? best.baseToken?.symbol ?? short(pos.mint) : short(pos.mint),
    priceSource: "DexScreener current",
  };
}

async function getSolUsd(): Promise<number> {
  const cached = await cacheGet<{ ts: number; price: number }>("sol-usd.json");
  if (cached && Date.now() - cached.ts < 10 * 60_000) return cached.price;
  const res = await fetch("https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd", {
    signal: AbortSignal.timeout(10_000),
  });
  if (!res.ok) return cached?.price ?? 150;
  const json = (await res.json()) as { solana?: { usd?: number } };
  const price = json.solana?.usd ?? cached?.price ?? 150;
  await cacheSet("sol-usd.json", { ts: Date.now(), price });
  return price;
}

async function main(): Promise<void> {
  const wallet = process.argv[2] ?? "";
  const maxTxs = Number(process.argv[3] ?? "10000");
  const useCache = process.argv.includes("--cache");
  if (!wallet) throw new Error("usage: tsx src/walletLedgerAudit.ts WALLET [maxTxs] [--cache]");

  const txs = await fetchPagedWalletTxs(wallet, maxTxs, useCache);
  const flows = txs.map((tx) => extractFlow(tx, wallet)).sort((a, b) => a.timestamp - b.timestamp);
  const positions = new Map<string, Position>();
  const realizedTrades: RealizedTrade[] = [];
  let classified = 0;
  let unclassifiedWithToken = 0;
  let nativeNet = 0;
  let wsolNet = 0;
  let usdcNet = 0;

  for (const flow of flows) {
    nativeNet += flow.nativeLamports;
    wsolNet += flow.wsolRaw;
    usdcNet += flow.usdcRaw;
    const result = applyFlow(flow, positions, realizedTrades);
    if (result.classified) classified++;
    else if (flow.tokenNet.size > 0) unclassifiedWithToken++;
  }

  const solUsd = await getSolUsd();
  const openValues = [];
  for (const pos of positions.values()) {
    const heldRaw = pos.lots.reduce((sum, lot) => sum + lot.raw, 0);
    if (heldRaw <= 0) continue;
    const value = await estimateOpenValueLamports(pos, solUsd).catch(() => null);
    if (value) openValues.push({ pos, ...value, heldRaw });
    await sleep(150);
  }

  const realizedLamports = realizedTrades.reduce((sum, trade) => sum + trade.pnlLamports, 0);
  const realizedCost = realizedTrades.reduce((sum, trade) => sum + trade.costLamports, 0);
  const unrealizedValueLamports = openValues.reduce((sum, value) => sum + value.valueLamports, 0);
  const unrealizedCostLamports = [...positions.values()].reduce(
    (sum, pos) => sum + pos.lots.reduce((lotSum, lot) => lotSum + lot.costLamports, 0),
    0
  );
  const unrealizedPnlLamports = unrealizedValueLamports - unrealizedCostLamports;
  const rois = realizedTrades.map((trade) => trade.roiPct);
  const wins = realizedTrades.filter((trade) => trade.pnlLamports > 0);
  const losses = realizedTrades.filter((trade) => trade.pnlLamports < 0);
  const grossProfit = wins.reduce((sum, trade) => sum + trade.pnlLamports, 0);
  const grossLoss = Math.abs(losses.reduce((sum, trade) => sum + trade.pnlLamports, 0));
  const holdingMin = [...positions.values()]
    .filter((pos) => pos.lastSellTs && pos.firstBuyTs)
    .map((pos) => (pos.lastSellTs! - pos.firstBuyTs) / 60);
  const largestWin = realizedTrades.reduce((best, trade) => Math.max(best, trade.pnlLamports), 0);
  const largestLoss = realizedTrades.reduce((best, trade) => Math.min(best, trade.pnlLamports), 0);
  const topOpen = openValues.sort((a, b) => b.valueLamports - a.valueLamports).slice(0, 10);

  const report = [
    `# Corrected Wallet Ledger Audit`,
    ``,
    `Wallet: \`${wallet}\``,
    `Generated: ${new Date().toISOString()}`,
    ``,
    `## Methodology`,
    ``,
    `This is a FIFO token ledger from Helius enhanced tx account deltas. It includes native SOL plus WSOL quote flows, partial sells, and current DexScreener mark-to-market for open lots where available.`,
    `It separates realized swap PnL from unrealized PnL and flags unclassified token-flow transactions. It is still not tax-grade accounting because transfer pricing and missing current prices can affect totals.`,
    ``,
    `## Coverage`,
    ``,
    `- Transactions fetched: ${txs.length}`,
    `- Time range: ${new Date(Math.min(...flows.map((f) => f.timestamp)) * 1000).toISOString()} to ${new Date(Math.max(...flows.map((f) => f.timestamp)) * 1000).toISOString()}`,
    `- Classified buy/sell token-flow txs: ${classified}`,
    `- Unclassified token-flow txs: ${unclassifiedWithToken}`,
    `- Positions touched: ${positions.size}`,
    `- SOL/USD used: $${solUsd.toFixed(2)}`,
    ``,
    `## PnL`,
    ``,
    `- Realized PnL: ${fmtSol(sol(realizedLamports))} (${fmtUsd(sol(realizedLamports) * solUsd)})`,
    `- Realized ROI on sold cost basis: ${fmtPct(realizedCost > 0 ? (realizedLamports / realizedCost) * 100 : null)}`,
    `- Unrealized PnL, priced open lots: ${fmtSol(sol(unrealizedPnlLamports))} (${fmtUsd(sol(unrealizedPnlLamports) * solUsd)})`,
    `- Open mark value priced: ${fmtSol(sol(unrealizedValueLamports))} (${fmtUsd(sol(unrealizedValueLamports) * solUsd)})`,
    `- Open cost basis priced: ${fmtSol(sol(unrealizedCostLamports))}`,
    `- Combined realized + priced unrealized: ${fmtSol(sol(realizedLamports + unrealizedPnlLamports))} (${fmtUsd(sol(realizedLamports + unrealizedPnlLamports) * solUsd)})`,
    ``,
    `## Trading Statistics`,
    ``,
    `- Realized trades: ${realizedTrades.length}`,
    `- Win rate: ${fmtPct((wins.length / Math.max(1, realizedTrades.length)) * 100)}`,
    `- Average ROI/trade: ${fmtPct(avg(rois))}`,
    `- Median ROI/trade: ${fmtPct(median(rois))}`,
    `- Profit factor: ${grossLoss > 0 ? (grossProfit / grossLoss).toFixed(2) : "-"}`,
    `- Sharpe proxy: ${std(rois) > 0 ? (avg(rois) / std(rois)).toFixed(2) : "-"}`,
    `- Max drawdown, realized sequence: ${fmtSol(sol(maxDrawdown(realizedTrades.map((t) => t.pnlLamports))))}`,
    `- Average holding duration: ${avg(holdingMin).toFixed(1)}m`,
    `- Median holding duration: ${median(holdingMin)?.toFixed(1) ?? "-"}m`,
    `- Largest win: ${fmtSol(sol(largestWin))} (${fmtUsd(sol(largestWin) * solUsd)})`,
    `- Largest loss: ${fmtSol(sol(largestLoss))} (${fmtUsd(sol(largestLoss) * solUsd)})`,
    ``,
    `## Net Flow Diagnostics`,
    ``,
    `- Native SOL net over fetched txs: ${fmtSol(sol(nativeNet))}`,
    `- WSOL token net over fetched txs: ${fmtSol(sol(wsolNet))}`,
    `- USDC raw net over fetched txs: ${(usdcNet / 1_000_000).toFixed(2)} USDC`,
    ``,
    `## Largest Open Lots By Current Mark`,
    ``,
    `| Token | Held Raw | Current Value | Current Value USD | Source |`,
    `|---|---:|---:|---:|---|`,
    ...topOpen.map((row) =>
      `| ${row.symbol} (${short(row.pos.mint)}) | ${row.heldRaw.toFixed(0)} | ${fmtSol(sol(row.valueLamports))} | ${fmtUsd(row.valueUsd)} | ${row.priceSource} |`
    ),
    ``,
  ].join("\n");

  await mkdir(path.resolve(process.cwd(), "data"), { recursive: true });
  const mdPath = path.resolve(process.cwd(), "data", `wallet-ledger-audit-${wallet}.md`);
  const jsonPath = path.resolve(process.cwd(), "data", `wallet-ledger-audit-${wallet}.json`);
  await writeFile(mdPath, `${report}\n`, "utf8");
  await writeFile(
    jsonPath,
    JSON.stringify({
      wallet,
      txs: txs.length,
      classified,
      unclassifiedWithToken,
      positions: positions.size,
      solUsd,
      realizedPnlSol: sol(realizedLamports),
      realizedPnlUsd: sol(realizedLamports) * solUsd,
      realizedRoiPct: realizedCost > 0 ? (realizedLamports / realizedCost) * 100 : null,
      unrealizedPnlSol: sol(unrealizedPnlLamports),
      unrealizedPnlUsd: sol(unrealizedPnlLamports) * solUsd,
      combinedPnlSol: sol(realizedLamports + unrealizedPnlLamports),
      combinedPnlUsd: sol(realizedLamports + unrealizedPnlLamports) * solUsd,
      realizedTrades: realizedTrades.length,
      winRatePct: (wins.length / Math.max(1, realizedTrades.length)) * 100,
      avgRoiPct: avg(rois),
      medianRoiPct: median(rois),
      profitFactor: grossLoss > 0 ? grossProfit / grossLoss : null,
      sharpeProxy: std(rois) > 0 ? avg(rois) / std(rois) : null,
      maxDrawdownSol: sol(maxDrawdown(realizedTrades.map((t) => t.pnlLamports))),
      largestWinSol: sol(largestWin),
      largestLossSol: sol(largestLoss),
      nativeNetSol: sol(nativeNet),
      wsolNetSol: sol(wsolNet),
      usdcNet: usdcNet / 1_000_000,
      topOpen: topOpen.map((row) => ({
        mint: row.pos.mint,
        symbol: row.symbol,
        heldRaw: row.heldRaw,
        valueSol: sol(row.valueLamports),
        valueUsd: row.valueUsd,
      })),
    }, null, 2),
    "utf8"
  );
  console.log(report);
  console.log(`\nWrote ${mdPath}`);
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
