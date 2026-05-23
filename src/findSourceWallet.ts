import "dotenv/config";
import { heliusAddressTransactionsUrl } from "./config.js";

const DEFAULT_TARGET = "B27vwq89vX3oDa8fFNGiqQWB2wRiSmsmD8Hw2JuRefC8";
const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const REQUEST_GAP_MS = 650;

type HeliusTx = {
  signature: string;
  timestamp?: number;
  type?: string;
  source?: string;
  feePayer?: string;
  accountData?: Array<{
    account: string;
    nativeBalanceChange?: number;
    tokenBalanceChanges?: Array<{
      userAccount?: string;
      tokenAccount?: string;
      mint?: string;
      rawTokenAmount?: { tokenAmount?: string; decimals?: number };
    }>;
  }>;
  tokenTransfers?: Array<{
    mint?: string;
    fromUserAccount?: string;
    toUserAccount?: string;
    rawTokenAmount?: { tokenAmount?: string; decimals?: number };
  }>;
};

type Swap = {
  wallet: string;
  signature: string;
  timestamp: number;
  side: "buy" | "sell";
  mint: string;
  quoteMint: string;
  quoteRaw: number;
  tokenRaw: number;
};

type LeadHit = {
  mint: string;
  candidate: string;
  candidateBuy: Swap;
  targetBuy: Swap;
  leadSeconds: number;
};

type CandidateScore = {
  wallet: string;
  hits: LeadHit[];
  totalQuoteSolEquivalent: number;
  avgLeadSeconds: number;
  medianLeadSeconds: number;
  score: number;
};

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function short(value: string | null | undefined): string {
  if (!value) return "-";
  return `${value.slice(0, 6)}...${value.slice(-4)}`;
}

function sol(raw: number): number {
  return raw / 1_000_000_000;
}

function quoteToSolEquivalent(raw: number, mint: string): number {
  if (mint === USDC_MINT) return raw / 1_000_000 / 150;
  return raw / 1_000_000_000;
}

function fmtAge(seconds: number): string {
  if (!Number.isFinite(seconds)) return "-";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

function fmtQuote(raw: number, mint: string): string {
  if (mint === USDC_MINT) return `${(raw / 1_000_000).toFixed(2)} USDC`;
  return `${sol(raw).toFixed(4)} SOL`;
}

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1]! + sorted[mid]!) / 2 : sorted[mid]!;
}

function addTokenNet(
  tokenNet: Map<string, { raw: number; decimals: number }>,
  mint: string | undefined,
  raw: number,
  decimals: number | undefined
): void {
  if (!mint || !Number.isFinite(raw) || raw === 0) return;
  const current = tokenNet.get(mint) ?? { raw: 0, decimals: decimals ?? 0 };
  current.raw += raw;
  current.decimals = decimals ?? current.decimals;
  tokenNet.set(mint, current);
}

function bestTokenNet(tokenNet: Map<string, { raw: number; decimals: number }>): {
  mint: string | null;
  raw: number;
} {
  let mint: string | null = null;
  let raw = 0;
  for (const [candidate, net] of tokenNet) {
    if (candidate === WRAPPED_SOL_MINT || candidate === USDC_MINT) continue;
    if (Math.abs(net.raw) > Math.abs(raw)) {
      mint = candidate;
      raw = net.raw;
    }
  }
  return { mint, raw };
}

function bestQuoteOutflow(tokenNet: Map<string, { raw: number; decimals: number }>): {
  mint: string | null;
  raw: number;
} {
  let mint: string | null = null;
  let raw = 0;
  for (const quoteMint of [WRAPPED_SOL_MINT, USDC_MINT]) {
    const net = tokenNet.get(quoteMint);
    if (!net || net.raw >= 0) continue;
    if (Math.abs(net.raw) > Math.abs(raw)) {
      mint = quoteMint;
      raw = net.raw;
    }
  }
  return { mint, raw };
}

function extractSwap(tx: HeliusTx, wallet: string): Swap | null {
  if (!tx.accountData?.length) return null;

  let nativeNet = 0;
  const tokenNet = new Map<string, { raw: number; decimals: number }>();

  for (const account of tx.accountData) {
    if (account.account === wallet) nativeNet += account.nativeBalanceChange ?? 0;
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.userAccount !== wallet) continue;
      const raw = Number(change.rawTokenAmount?.tokenAmount ?? "0");
      addTokenNet(tokenNet, change.mint, raw, change.rawTokenAmount?.decimals);
    }
  }

  const token = bestTokenNet(tokenNet);
  if (!token.mint || token.raw === 0) return null;

  const quote = bestQuoteOutflow(tokenNet);
  const quoteMint = quote.mint ?? WRAPPED_SOL_MINT;
  const quoteRaw = quote.mint ? Math.abs(quote.raw) : Math.abs(nativeNet);

  if (token.raw > 0 && quoteRaw > 0) {
    return {
      wallet,
      signature: tx.signature,
      timestamp: tx.timestamp ?? 0,
      side: "buy",
      mint: token.mint,
      quoteMint,
      quoteRaw,
      tokenRaw: Math.abs(token.raw),
    };
  }

  if (token.raw < 0 && nativeNet > 0) {
    return {
      wallet,
      signature: tx.signature,
      timestamp: tx.timestamp ?? 0,
      side: "sell",
      mint: token.mint,
      quoteMint: WRAPPED_SOL_MINT,
      quoteRaw: nativeNet,
      tokenRaw: Math.abs(token.raw),
    };
  }

  return null;
}

function walletsTouchedByMint(tx: HeliusTx, mint: string): string[] {
  const wallets = new Set<string>();
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

async function fetchAddressTxs(address: string, limit: number, before?: string): Promise<HeliusTx[]> {
  const url = heliusAddressTransactionsUrl(address, before, limit);
  if (!url) throw new Error("HELIUS_API_KEY is not set");
  let lastError: Error | null = null;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(12_000) });
      if (!response.ok) {
        const text = await response.text();
        lastError = new Error(`Helius ${response.status}: ${text.slice(0, 220)}`);
        if ((response.status === 429 || response.status >= 500) && attempt < 3) {
          await sleep(REQUEST_GAP_MS * attempt);
          continue;
        }
        throw lastError;
      }
      return (await response.json()) as HeliusTx[];
    } catch (err) {
      lastError = err instanceof Error ? err : new Error(String(err));
      if (attempt < 3) {
        await sleep(REQUEST_GAP_MS * attempt);
        continue;
      }
    }
  }
  throw lastError ?? new Error("Helius request failed");
}

async function fetchPagedAddressTxs(address: string, limit: number): Promise<HeliusTx[]> {
  const all: HeliusTx[] = [];
  let before: string | undefined;
  while (all.length < limit) {
    const batch = await fetchAddressTxs(address, Math.min(100, limit - all.length), before);
    if (batch.length === 0) break;
    all.push(...batch);
    before = batch[batch.length - 1]?.signature;
    await sleep(REQUEST_GAP_MS);
  }
  return all;
}

async function fetchMintHistoryAroundBuy(mint: string, targetBuy: Swap, maxTxs: number): Promise<HeliusTx[]> {
  const all: HeliusTx[] = [];
  let before: string | undefined;
  while (all.length < maxTxs) {
    const batch = await fetchAddressTxs(mint, Math.min(100, maxTxs - all.length), before);
    if (batch.length === 0) break;
    all.push(...batch);
    const oldest = batch[batch.length - 1]?.timestamp ?? 0;
    if (oldest > 0 && oldest < targetBuy.timestamp - 10 * 60) break;
    before = batch[batch.length - 1]?.signature;
    await sleep(REQUEST_GAP_MS);
  }
  return all;
}

function candidateBuysBeforeTarget(
  txs: HeliusTx[],
  targetBuy: Swap,
  maxLeadSeconds: number
): LeadHit[] {
  const hits: LeadHit[] = [];
  for (const tx of txs) {
    const timestamp = tx.timestamp ?? 0;
    if (timestamp <= 0) continue;
    if (timestamp > targetBuy.timestamp) continue;
    if (timestamp < targetBuy.timestamp - maxLeadSeconds) continue;

    for (const wallet of walletsTouchedByMint(tx, targetBuy.mint)) {
      if (wallet === targetBuy.wallet) continue;
      const swap = extractSwap(tx, wallet);
      if (!swap || swap.side !== "buy" || swap.mint !== targetBuy.mint) continue;
      hits.push({
        mint: targetBuy.mint,
        candidate: wallet,
        candidateBuy: swap,
        targetBuy,
        leadSeconds: targetBuy.timestamp - swap.timestamp,
      });
    }
  }
  return hits.sort((a, b) => a.leadSeconds - b.leadSeconds);
}

function scoreCandidates(hits: LeadHit[], minRepeats: number): CandidateScore[] {
  const byWallet = new Map<string, LeadHit[]>();
  for (const hit of hits) {
    const list = byWallet.get(hit.candidate) ?? [];
    list.push(hit);
    byWallet.set(hit.candidate, list);
  }

  const scores: CandidateScore[] = [];
  for (const [wallet, walletHits] of byWallet) {
    const uniqueMintHits = new Map<string, LeadHit>();
    for (const hit of walletHits) {
      const existing = uniqueMintHits.get(hit.mint);
      if (!existing || hit.leadSeconds < existing.leadSeconds) uniqueMintHits.set(hit.mint, hit);
    }
    const uniqueHits = [...uniqueMintHits.values()];
    if (uniqueHits.length < minRepeats) continue;
    const leads = uniqueHits.map((hit) => hit.leadSeconds);
    const totalQuoteSolEquivalent = uniqueHits.reduce(
      (sum, hit) => sum + quoteToSolEquivalent(hit.candidateBuy.quoteRaw, hit.candidateBuy.quoteMint),
      0
    );
    const avgLeadSeconds = leads.reduce((sum, lead) => sum + lead, 0) / leads.length;
    const medianLeadSeconds = median(leads);
    const repeatScore = uniqueHits.length * 10;
    const speedScore = Math.max(0, 10 - medianLeadSeconds / 12);
    const sizeScore = Math.min(10, totalQuoteSolEquivalent);
    scores.push({
      wallet,
      hits: uniqueHits.sort((a, b) => b.targetBuy.timestamp - a.targetBuy.timestamp),
      totalQuoteSolEquivalent,
      avgLeadSeconds,
      medianLeadSeconds,
      score: repeatScore + speedScore + sizeScore,
    });
  }
  return scores.sort((a, b) => b.score - a.score);
}

function printReport(args: {
  targetWallet: string;
  targetBuys: Swap[];
  hits: LeadHit[];
  scores: CandidateScore[];
}): void {
  console.log(`Source-wallet hunt for ${short(args.targetWallet)}`);
  console.log(`Target buys analyzed: ${args.targetBuys.length}`);
  console.log(`Earlier same-mint buy hits: ${args.hits.length}`);
  console.log("");

  console.log("Top candidate source wallets:");
  if (args.scores.length === 0) {
    console.log("  No repeated earlier buyers found in this sample.");
  }
  for (const candidate of args.scores.slice(0, 15)) {
    const mints = new Set(candidate.hits.map((hit) => hit.mint)).size;
    console.log(
      [
        `  ${short(candidate.wallet)}`,
        `score=${candidate.score.toFixed(1)}`,
        `tokens=${mints}`,
        `medianLead=${fmtAge(candidate.medianLeadSeconds)}`,
        `avgLead=${fmtAge(candidate.avgLeadSeconds)}`,
        `size~${candidate.totalQuoteSolEquivalent.toFixed(3)} SOL`,
        `wallet=${candidate.wallet}`,
      ].join(" | ")
    );
  }

  console.log("");
  console.log("Per-token evidence:");
  for (const buy of args.targetBuys) {
    const hits = args.hits
      .filter((hit) => hit.mint === buy.mint)
      .sort((a, b) => a.leadSeconds - b.leadSeconds)
      .slice(0, 5);
    console.log(
      `  ${short(buy.mint)} target=${fmtQuote(buy.quoteRaw, buy.quoteMint)} tx=${short(buy.signature)}`
    );
    if (hits.length === 0) {
      console.log("    no earlier same-mint buys in window");
      continue;
    }
    for (const hit of hits) {
      console.log(
        [
          `    ${short(hit.candidate)}`,
          `lead=${fmtAge(hit.leadSeconds)}`,
          `size=${fmtQuote(hit.candidateBuy.quoteRaw, hit.candidateBuy.quoteMint)}`,
          `tx=${short(hit.candidateBuy.signature)}`,
        ].join(" | ")
      );
    }
  }

  console.log("");
  console.log("Notes:");
  console.log("  Repeated wallets with low medianLead are strongest source-wallet candidates.");
  console.log("  One-off earlier buyers are usually noise unless they repeatedly show up across mints.");
  console.log("  USDC sizes are shown as USDC; score uses a rough 150 USDC/SOL conversion only for ranking.");
}

async function main(): Promise<void> {
  const targetWallet = (process.argv[2] ?? DEFAULT_TARGET).trim();
  const walletTxLimit = Number(process.argv[3] ?? "160");
  const mintLimit = Number(process.argv[4] ?? "20");
  const maxLeadSeconds = Number(process.argv[5] ?? "180");
  const minRepeats = Number(process.argv[6] ?? "2");
  const mintTxLimit = Number(process.env.SOURCE_HUNT_MINT_TX_LIMIT ?? "350");

  if (!targetWallet) throw new Error("Pass a target wallet address");

  console.log(
    `Fetching ${walletTxLimit} txs for ${short(targetWallet)}; profiling ${mintLimit} buys; lead window ${maxLeadSeconds}s...`
  );
  const targetTxs = await fetchPagedAddressTxs(targetWallet, walletTxLimit);
  const targetBuys = targetTxs
    .map((tx) => extractSwap(tx, targetWallet))
    .filter((swap): swap is Swap => swap != null && swap.side === "buy")
    .sort((a, b) => b.timestamp - a.timestamp);

  const firstRecentBuyByMint = new Map<string, Swap>();
  for (const buy of targetBuys) {
    if (!firstRecentBuyByMint.has(buy.mint)) firstRecentBuyByMint.set(buy.mint, buy);
    if (firstRecentBuyByMint.size >= mintLimit) break;
  }
  const sampledBuys = [...firstRecentBuyByMint.values()];

  const allHits: LeadHit[] = [];
  for (const [index, buy] of sampledBuys.entries()) {
    process.stdout.write(`  [${index + 1}/${sampledBuys.length}] ${short(buy.mint)}... `);
    try {
      const mintTxs = await fetchMintHistoryAroundBuy(buy.mint, buy, mintTxLimit);
      const hits = candidateBuysBeforeTarget(mintTxs, buy, maxLeadSeconds);
      allHits.push(...hits);
      console.log(`${hits.length} earlier buy hit(s)`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.log(`skipped: ${msg}`);
    }
    await sleep(REQUEST_GAP_MS);
  }

  printReport({
    targetWallet,
    targetBuys: sampledBuys,
    hits: allHits,
    scores: scoreCandidates(allHits, minRepeats),
  });
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
