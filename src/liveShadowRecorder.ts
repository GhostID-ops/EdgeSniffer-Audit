import "dotenv/config";
import { appendFile, mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { PublicKey } from "@solana/web3.js";
import {
  heliusEnhancedTransactionsUrl,
  pollIntervalMs,
  readConnection,
} from "./config.js";
import { getNewSwaps, subscribeWalletSwaps, type Unsubscribe } from "./watcher.js";
import type { DetectedSwap } from "./types.js";

type ParsedTx = {
  signature: string;
  timestamp?: number;
  type?: string;
  source?: string;
  feePayer?: string;
  accountData?: Array<{
    account?: string;
    nativeBalanceChange?: number;
    tokenBalanceChanges?: Array<{
      userAccount?: string;
      tokenAccount?: string;
      mint?: string;
      rawTokenAmount?: {
        tokenAmount?: string;
        decimals?: number;
      };
    }>;
  }>;
};

type TradeRow = {
  time: string;
  ts: number;
  signature: string;
  wallet: string;
  side: "buy" | "sell" | "unknown";
  mint: string;
  source?: string;
  type?: string;
  nativeSol: number;
  tokenNet: number;
  feeSol: number;
  entryAgeSec?: number;
  txRankApprox?: number;
  firstSeenForMint: boolean;
};

type MintSignal = {
  mint: string;
  firstBuyTs: number;
  firstBuySignature: string;
  searchedAddresses?: string[];
  entryAgeSec?: number;
  txRankApprox?: number;
  earliestSeenTs?: number;
  historyQuality: "good" | "partial" | "poor";
};

type Position = {
  sourceHeld: number;
  copiedHeld: number;
  spent: number;
  proceeds: number;
  costs: number;
  buysCopied: number;
  sellsCopied: number;
  realizedNet: number;
};

type CampaignState = {
  mint: string;
  firstBuyTs?: number;
  buyCount: number;
  secondBuyDelaySec?: number;
  thirdBuyDelaySec?: number;
  signal?: MintSignal;
};

type Policy = {
  name: string;
  description: string;
  copyBuySol: (ctx: PolicyContext) => number;
};

type PolicyContext = {
  row: TradeRow;
  campaign: CampaignState;
  sourceBuySol: number;
  buyIndex: number;
  assumptions: ShadowAssumptions;
};

type ShadowAssumptions = {
  buyTxCostSol: number;
  sellTxCostSol: number;
  entryPenaltyBps: number;
  exitPenaltyBps: number;
  sourceSizeMultiplier: number;
  maxBuySol?: number;
};

type PolicyEvent = {
  policy: string;
  mint: string;
  signature: string;
  side: TradeRow["side"];
  copied: boolean;
  copiedBuySol?: number;
  sellFraction?: number;
  spent: number;
  proceeds: number;
  costs: number;
  realizedNet: number;
  copiedHeld: number;
};

const ROOT = process.cwd();
const OUT_DIR = path.join(ROOT, "shadow-recordings");
const JOURNAL_PATH = path.join(OUT_DIR, "live-shadow-journal.jsonl");
const SUMMARY_PATH = path.join(OUT_DIR, "live-shadow-summary.json");
const SIGNAL_CACHE_DIR = path.join(ROOT, ".cache", "live-shadow-recorder");
const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const DEFAULT_WALLET = "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o";

function numberArg(name: string, fallback: number): number {
  const prefix = `${name}=`;
  const raw = process.argv.find((arg) => arg.startsWith(prefix))?.slice(prefix.length);
  if (!raw) return fallback;
  const n = Number(raw);
  return Number.isFinite(n) ? n : fallback;
}

function optionalNumberArg(name: string): number | undefined {
  const prefix = `${name}=`;
  const raw = process.argv.find((arg) => arg.startsWith(prefix))?.slice(prefix.length);
  if (!raw || raw.toLowerCase() === "none") return undefined;
  const n = Number(raw);
  return Number.isFinite(n) ? n : undefined;
}

function stringArg(name: string, fallback: string): string {
  const prefix = `${name}=`;
  return process.argv.find((arg) => arg.startsWith(prefix))?.slice(prefix.length) ?? fallback;
}

function hasFlag(name: string): boolean {
  return process.argv.includes(name);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function sol(n: number): string {
  return `${n.toFixed(4)} SOL`;
}

function short(value: string, head = 6, tail = 4): string {
  return value.length <= head + tail ? value : `${value.slice(0, head)}...${value.slice(-tail)}`;
}

async function appendJsonl(value: unknown): Promise<void> {
  await mkdir(OUT_DIR, { recursive: true });
  await appendFile(JOURNAL_PATH, `${JSON.stringify(value)}\n`);
}

async function parseTransaction(signature: string): Promise<ParsedTx | undefined> {
  if (!heliusEnhancedTransactionsUrl) {
    throw new Error("HELIUS_API_KEY is required for live shadow transaction parsing.");
  }

  const response = await fetch(heliusEnhancedTransactionsUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transactions: [signature] }),
    signal: AbortSignal.timeout(20_000),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Helius parse failed: ${response.status} ${text.slice(0, 200)}`);
  }
  const parsed = (await response.json()) as ParsedTx[];
  return parsed[0];
}

function tokenDelta(change: { rawTokenAmount?: { tokenAmount?: string; decimals?: number } }): number {
  const raw = Number(change.rawTokenAmount?.tokenAmount ?? 0);
  const decimals = Number(change.rawTokenAmount?.decimals ?? 0);
  if (!Number.isFinite(raw)) return 0;
  return raw / 10 ** decimals;
}

function extractTradeRow(tx: ParsedTx, wallet: string): TradeRow | undefined {
  const tokenNetByMint = new Map<string, number>();
  let nativeLamports = 0;

  for (const account of tx.accountData ?? []) {
    if (account.account === wallet) nativeLamports += account.nativeBalanceChange ?? 0;
    for (const change of account.tokenBalanceChanges ?? []) {
      if (!change.mint || change.mint === WRAPPED_SOL_MINT) continue;
      if (change.userAccount !== wallet) continue;
      tokenNetByMint.set(change.mint, (tokenNetByMint.get(change.mint) ?? 0) + tokenDelta(change));
    }
  }

  let mint = "";
  let tokenNet = 0;
  for (const [candidateMint, candidateNet] of tokenNetByMint.entries()) {
    if (Math.abs(candidateNet) > Math.abs(tokenNet)) {
      mint = candidateMint;
      tokenNet = candidateNet;
    }
  }
  if (!mint || tokenNet === 0) return undefined;

  const nativeSol = nativeLamports / 1e9;
  const side =
    nativeSol < 0 && tokenNet > 0 ? "buy" : nativeSol > 0 && tokenNet < 0 ? "sell" : "unknown";

  return {
    time: new Date((tx.timestamp ?? Math.floor(Date.now() / 1000)) * 1000).toISOString(),
    ts: tx.timestamp ?? Math.floor(Date.now() / 1000),
    signature: tx.signature,
    wallet,
    side,
    mint,
    source: tx.source,
    type: tx.type,
    nativeSol,
    tokenNet,
    feeSol: 0,
    firstSeenForMint: false,
  };
}

function isPublicKey(value: string | undefined): value is string {
  if (!value || value === WRAPPED_SOL_MINT) return false;
  try {
    new PublicKey(value);
    return true;
  } catch {
    return false;
  }
}

function extractRelatedAddresses(mint: string, wallet: string, tx: ParsedTx | undefined): string[] {
  const addresses = new Set<string>();
  if (!tx) return [];

  for (const account of tx.accountData ?? []) {
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.mint !== mint) continue;
      if (isPublicKey(account.account) && account.account !== wallet) addresses.add(account.account);
      if (isPublicKey(change.tokenAccount) && change.tokenAccount !== wallet) addresses.add(change.tokenAccount);
    }
  }

  return [...addresses];
}

async function classifyMintEntry(
  mint: string,
  wallet: string,
  firstBuyTs: number,
  signature: string,
  firstTx: ParsedTx | undefined
): Promise<MintSignal> {
  await mkdir(SIGNAL_CACHE_DIR, { recursive: true });
  const cachePath = path.join(SIGNAL_CACHE_DIR, `${mint}.json`);
  try {
    const cached = JSON.parse(await readFile(cachePath, "utf8")) as MintSignal;
    if (cached.firstBuySignature === signature) return cached;
  } catch {
    // cache miss
  }

  const searchedAddresses = [mint, ...extractRelatedAddresses(mint, wallet, firstTx)].slice(0, 8);
  const seen = new Map<string, { signature: string; blockTime?: number | null }>();
  for (const address of searchedAddresses) {
    const signatures = await readConnection.getSignaturesForAddress(new PublicKey(address), { limit: 500 });
    for (const item of signatures) {
      seen.set(item.signature, { signature: item.signature, blockTime: item.blockTime });
    }
    await sleep(100);
  }

  const withTime = [...seen.values()].filter((item) => item.blockTime !== null && item.blockTime !== undefined);
  const beforeOrAt = withTime.filter((item) => (item.blockTime ?? 0) <= firstBuyTs);
  const earliest = beforeOrAt.reduce<number | undefined>((min, item) => {
    const ts = item.blockTime ?? undefined;
    if (ts === undefined) return min;
    return min === undefined ? ts : Math.min(min, ts);
  }, undefined);

  const signal: MintSignal = {
    mint,
    firstBuyTs,
    firstBuySignature: signature,
    searchedAddresses,
    earliestSeenTs: earliest,
    entryAgeSec: earliest === undefined ? undefined : firstBuyTs - earliest,
    txRankApprox: beforeOrAt.length > 0 ? beforeOrAt.length : undefined,
    historyQuality:
      beforeOrAt.some((item) => item.signature === signature) && beforeOrAt.length > 0
        ? "good"
        : withTime.length > 0
        ? "partial"
        : "poor",
  };

  await writeFile(cachePath, JSON.stringify(signal, null, 2));
  return signal;
}

function copySize(sourceBuySol: number, assumptions: ShadowAssumptions): number {
  const scaled = sourceBuySol * assumptions.sourceSizeMultiplier;
  return assumptions.maxBuySol === undefined ? scaled : Math.min(scaled, assumptions.maxBuySol);
}

function policies(): Policy[] {
  return [
    {
      name: "copy_all",
      description: "Paper copy every source buy and mirror source sell fractions.",
      copyBuySol: ({ sourceBuySol, assumptions }) => copySize(sourceBuySol, assumptions),
    },
    {
      name: "conviction_buys_only",
      description: "Copy only source buy legs of at least 3 SOL.",
      copyBuySol: ({ sourceBuySol, assumptions }) =>
        sourceBuySol >= 3 ? copySize(sourceBuySol, assumptions) : 0,
    },
    {
      name: "first_wave_approx_only",
      description: "Copy only if mint-address transaction rank approximation is <= 2.",
      copyBuySol: ({ row, sourceBuySol, assumptions }) =>
        (row.txRankApprox ?? 9999) <= 2 ? copySize(sourceBuySol, assumptions) : 0,
    },
    {
      name: "first_wave_conviction_approx",
      description: "Copy 3+ SOL buy legs only when approximate rank is <= 2.",
      copyBuySol: ({ row, sourceBuySol, assumptions }) =>
        (row.txRankApprox ?? 9999) <= 2 && sourceBuySol >= 3 ? copySize(sourceBuySol, assumptions) : 0,
    },
    {
      name: "skip_crowded_rank_3_25_approx",
      description: "Copy all ranked campaigns except approximate rank 3-25. Unknown rank is skipped.",
      copyBuySol: ({ row, sourceBuySol, assumptions }) => {
        const rank = row.txRankApprox;
        if (rank === undefined) return 0;
        if (rank !== undefined && rank >= 3 && rank <= 25) return 0;
        return copySize(sourceBuySol, assumptions);
      },
    },
    {
      name: "fast_second_buy_confirm",
      description: "Wait for a second buy within 10 seconds, then copy from second buy onward.",
      copyBuySol: ({ campaign, buyIndex, sourceBuySol, assumptions }) =>
        buyIndex >= 1 && (campaign.secondBuyDelaySec ?? 999999) <= 10
          ? copySize(sourceBuySol, assumptions)
          : 0,
    },
    {
      name: "fast_third_buy_confirm",
      description: "Wait for three buys within 10 seconds, then copy from third buy onward.",
      copyBuySol: ({ campaign, buyIndex, sourceBuySol, assumptions }) =>
        buyIndex >= 2 && (campaign.thirdBuyDelaySec ?? 999999) <= 10
          ? copySize(sourceBuySol, assumptions)
          : 0,
    },
  ];
}

class ShadowRecorder {
  private readonly campaigns = new Map<string, CampaignState>();
  private readonly positions = new Map<string, Map<string, Position>>();
  private readonly policyList = policies();
  private readonly processed = new Set<string>();
  private readonly assumptions: ShadowAssumptions;

  constructor(assumptions: ShadowAssumptions) {
    this.assumptions = assumptions;
    for (const policy of this.policyList) this.positions.set(policy.name, new Map());
  }

  async handleSwap(swap: DetectedSwap): Promise<void> {
    if (this.processed.has(swap.signature)) return;
    this.processed.add(swap.signature);

    const parsed = await parseTransaction(swap.signature);
    if (!parsed) return;
    const row = extractTradeRow(parsed, swap.wallet);
    if (!row || row.side === "unknown") return;

    let campaign = this.campaigns.get(row.mint);
    if (!campaign) {
      campaign = { mint: row.mint, buyCount: 0 };
      this.campaigns.set(row.mint, campaign);
    }

    if (row.side === "buy" && campaign.firstBuyTs === undefined) {
      campaign.firstBuyTs = row.ts;
      row.firstSeenForMint = true;
      try {
        campaign.signal = await classifyMintEntry(row.mint, row.wallet, row.ts, row.signature, parsed);
        row.entryAgeSec = campaign.signal.entryAgeSec;
        row.txRankApprox = campaign.signal.txRankApprox;
      } catch (err) {
        console.warn(`Signal classify failed for ${short(row.mint)}: ${err instanceof Error ? err.message : String(err)}`);
      }
    } else if (campaign.signal) {
      row.entryAgeSec = campaign.signal.entryAgeSec;
      row.txRankApprox = campaign.signal.txRankApprox;
    }

    await this.applyTrade(row, campaign);
  }

  private async applyTrade(row: TradeRow, campaign: CampaignState): Promise<void> {
    const policyEvents: PolicyEvent[] = [];

    if (row.side === "buy") {
      const buyIndex = campaign.buyCount;
      const sourceBuySol = Math.abs(row.nativeSol);
      if (campaign.buyCount === 1 && campaign.firstBuyTs !== undefined) {
        campaign.secondBuyDelaySec = row.ts - campaign.firstBuyTs;
      }
      if (campaign.buyCount === 2 && campaign.firstBuyTs !== undefined) {
        campaign.thirdBuyDelaySec = row.ts - campaign.firstBuyTs;
      }

      for (const policy of this.policyList) {
        const position = this.getPosition(policy.name, row.mint);
        const copiedBuySol = policy.copyBuySol({
          row,
          campaign,
          sourceBuySol,
          buyIndex,
          assumptions: this.assumptions,
        });
        const beforeSpent = position.spent;
        if (copiedBuySol > 0 && sourceBuySol > 0 && row.tokenNet > 0) {
          const entryAdjustedSol = copiedBuySol * (1 + this.assumptions.entryPenaltyBps / 10_000);
          position.sourceHeld += row.tokenNet;
          position.copiedHeld += row.tokenNet * (copiedBuySol / sourceBuySol);
          position.spent += entryAdjustedSol;
          position.costs += this.assumptions.buyTxCostSol;
          position.buysCopied += 1;
        } else {
          position.sourceHeld += row.tokenNet;
        }
        position.realizedNet = position.proceeds - position.spent - position.costs;
        policyEvents.push(this.policyEvent(policy.name, row, copiedBuySol > 0, position, { copiedBuySol: copiedBuySol || undefined }));
        if (beforeSpent === 0 && position.spent > 0) {
          console.log(`${policy.name}: opened ${short(row.mint)} with ${sol(copiedBuySol)}`);
        }
      }

      campaign.buyCount += 1;
    }

    if (row.side === "sell") {
      for (const policy of this.policyList) {
        const position = this.getPosition(policy.name, row.mint);
        const soldTokens = Math.abs(row.tokenNet);
        const sourceReceiveSol = row.nativeSol;
        let sellFraction = 0;
        if (soldTokens > 0 && sourceReceiveSol > 0 && position.sourceHeld > 0 && position.copiedHeld > 0) {
          sellFraction = Math.min(1, soldTokens / position.sourceHeld);
          const copiedSellTokens = position.copiedHeld * sellFraction;
          const sourcePrice = sourceReceiveSol / soldTokens;
          const copiedReceive = copiedSellTokens * sourcePrice * (1 - this.assumptions.exitPenaltyBps / 10_000);
          position.copiedHeld -= copiedSellTokens;
          position.proceeds += copiedReceive;
          position.costs += this.assumptions.sellTxCostSol;
          position.sellsCopied += 1;
        }
        position.sourceHeld = Math.max(0, position.sourceHeld - soldTokens);
        position.realizedNet = position.proceeds - position.spent - position.costs;
        policyEvents.push(this.policyEvent(policy.name, row, sellFraction > 0, position, { sellFraction }));
      }
    }

    const record = {
      recordedAt: new Date().toISOString(),
      trade: row,
      campaign,
      policyEvents,
      summary: this.summary(),
    };
    await appendJsonl(record);
    await this.writeSummary();
    console.log(
      `${row.side.toUpperCase()} ${short(row.mint)} ${row.side === "buy" ? sol(Math.abs(row.nativeSol)) : sol(row.nativeSol)} ` +
        `rank=${row.txRankApprox ?? "?"} age=${row.entryAgeSec ?? "?"}s sig=${short(row.signature)}`
    );
  }

  private getPosition(policy: string, mint: string): Position {
    const byMint = this.positions.get(policy);
    if (!byMint) throw new Error(`Unknown policy ${policy}`);
    const existing = byMint.get(mint);
    if (existing) return existing;
    const fresh: Position = {
      sourceHeld: 0,
      copiedHeld: 0,
      spent: 0,
      proceeds: 0,
      costs: 0,
      buysCopied: 0,
      sellsCopied: 0,
      realizedNet: 0,
    };
    byMint.set(mint, fresh);
    return fresh;
  }

  private policyEvent(
    policy: string,
    row: TradeRow,
    copied: boolean,
    position: Position,
    extra: { copiedBuySol?: number; sellFraction?: number }
  ): PolicyEvent {
    return {
      policy,
      mint: row.mint,
      signature: row.signature,
      side: row.side,
      copied,
      copiedBuySol: extra.copiedBuySol,
      sellFraction: extra.sellFraction,
      spent: position.spent,
      proceeds: position.proceeds,
      costs: position.costs,
      realizedNet: position.realizedNet,
      copiedHeld: position.copiedHeld,
    };
  }

  summary(): unknown {
    return {
      generatedAt: new Date().toISOString(),
      assumptions: this.assumptions,
      campaigns: this.campaigns.size,
      policies: this.policyList.map((policy) => {
        const positions = [...(this.positions.get(policy.name)?.values() ?? [])];
        const touched = positions.filter((position) => position.buysCopied > 0);
        const net = touched.reduce((sum, position) => sum + position.realizedNet, 0);
        const spent = touched.reduce((sum, position) => sum + position.spent, 0);
        const wins = touched.filter((position) => position.realizedNet > 0).length;
        return {
          name: policy.name,
          description: policy.description,
          campaignsTouched: touched.length,
          buysCopied: touched.reduce((sum, position) => sum + position.buysCopied, 0),
          sellsCopied: touched.reduce((sum, position) => sum + position.sellsCopied, 0),
          spent,
          proceeds: touched.reduce((sum, position) => sum + position.proceeds, 0),
          costs: touched.reduce((sum, position) => sum + position.costs, 0),
          realizedNet: net,
          roi: spent > 0 ? net / spent : 0,
          winRate: touched.length > 0 ? wins / touched.length : 0,
          openPositions: touched.filter((position) => Math.abs(position.copiedHeld) > 0.001).length,
        };
      }),
    };
  }

  async writeSummary(): Promise<void> {
    await mkdir(OUT_DIR, { recursive: true });
    await writeFile(SUMMARY_PATH, JSON.stringify(this.summary(), null, 2));
  }
}

async function main(): Promise<void> {
  const wallet = stringArg("--wallet", DEFAULT_WALLET);
  const assumptions: ShadowAssumptions = {
    buyTxCostSol: numberArg("--buy-tx-cost-sol", 0.006),
    sellTxCostSol: numberArg("--sell-tx-cost-sol", 0.0035),
    entryPenaltyBps: numberArg("--entry-penalty-bps", 150),
    exitPenaltyBps: numberArg("--exit-penalty-bps", 150),
    sourceSizeMultiplier: numberArg("--source-size-multiplier", 1),
    maxBuySol: optionalNumberArg("--max-buy-sol"),
  };
  const recorder = new ShadowRecorder(assumptions);
  const processedSignatures = new Set<string>();
  const startedAtSec = Math.floor(Date.now() / 1000);
  const once = hasFlag("--once");
  const durationSec = optionalNumberArg("--duration-sec");
  let stopped = false;
  let unsubscribe: Unsubscribe | undefined;

  await mkdir(OUT_DIR, { recursive: true });
  await recorder.writeSummary();

  console.log("Live shadow recorder started.");
  console.log(`Wallet: ${wallet}`);
  console.log("Mode: PAPER ONLY. No keypair, no swaps, no real funds.");
  console.log(`Journal: ${JOURNAL_PATH}`);
  console.log(`Summary: ${SUMMARY_PATH}`);

  const handleSwap = async (swap: DetectedSwap): Promise<void> => {
    try {
      await recorder.handleSwap(swap);
    } catch (err) {
      console.warn(`Shadow handle failed ${short(swap.signature)}: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  unsubscribe = await subscribeWalletSwaps([wallet], processedSignatures, startedAtSec, (swap) => {
    void handleSwap(swap);
  });

  const stop = async (): Promise<void> => {
    stopped = true;
    if (unsubscribe) await unsubscribe();
    await recorder.writeSummary();
    console.log("Live shadow recorder stopped.");
  };

  process.on("SIGINT", () => {
    void stop().finally(() => process.exit(0));
  });

  const deadline = durationSec === undefined ? undefined : Date.now() + durationSec * 1000;
  while (!stopped) {
    const swaps = await getNewSwaps([wallet], processedSignatures, startedAtSec);
    for (const swap of swaps) await handleSwap(swap);
    if (once) break;
    if (deadline !== undefined && Date.now() >= deadline) break;
    await sleep(pollIntervalMs);
  }

  await stop();
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exitCode = 1;
});
