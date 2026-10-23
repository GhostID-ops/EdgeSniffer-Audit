import { PublicKey, type VersionedTransactionResponse } from "@solana/web3.js";
import bs58 from "bs58";
import {
  wsConnection,
  readConnection,
  heliusEnhancedTransactionsUrl,
  heliusAddressTransactionsUrl,
  websocketUrl,
} from "./config.js";
import type { DetectedSwap, SourcePumpSwapAccountMeta } from "./types.js";
import { getTokenMeta } from "./tokenRegistry.js";
import {
  PUMPSWAP_BUY_DISCRIMINATOR,
  PUMPSWAP_BUY_EXACT_QUOTE_IN_DISCRIMINATOR,
  PUMPSWAP_PROGRAM_ID,
  PUMPSWAP_SELL_DISCRIMINATOR,
} from "./pumpswap/route.js";

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const PUMPSWAP_PROGRAM_ID_BASE58 = PUMPSWAP_PROGRAM_ID.toBase58();
const JUPITER_SOURCE = "JUPITER";
const SWAP_TYPES = new Set(["SWAP", "SWAP_EXACT_OUT", "SWAP_WITH_PRICE_IMPACT"]);
const wsParseInFlight = new Set<string>();
const WS_PARSE_ATTEMPTS = 12;
const WS_PARSE_RETRY_MS = 250;
const HELIUS_MIN_REQUEST_GAP_MS = 600;
const HELIUS_RATE_LIMIT_COOLDOWN_MS = 60_000;
const HELIUS_FETCH_TIMEOUT_MS = 12_000;
const WATCHER_TIMEOUT_WARNING_GAP_MS = 60_000;
let heliusNextRequestAt = 0;
let heliusCooldownUntil = 0;
let lastWatcherTimeoutWarningAt = 0;
let suppressedWatcherTimeoutWarnings = 0;
const ASSOCIATED_TOKEN_PROGRAM_ID = new PublicKey(
  "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
);
const TOKEN_PROGRAM_IDS = [
  new PublicKey("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"),
  new PublicKey("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"),
];

async function sleep(ms: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForHeliusSlot(): Promise<void> {
  const now = Date.now();
  const waitMs = Math.max(heliusCooldownUntil - now, heliusNextRequestAt - now, 0);
  if (waitMs > 0) await sleep(waitMs);
  heliusNextRequestAt = Date.now() + HELIUS_MIN_REQUEST_GAP_MS;
}

function noteHeliusRateLimit(): void {
  heliusCooldownUntil = Math.max(heliusCooldownUntil, Date.now() + HELIUS_RATE_LIMIT_COOLDOWN_MS);
}

type HeliusParsedTx = {
  signature: string;
  /** Unix seconds when the tx landed on-chain. */
  timestamp?: number;
  type?: string;
  source?: string;
  tokenTransfers?: unknown[];
  nativeTransfers?: unknown[];
  accountData?: AccountDataEntry[];
  events?: {
    swap?: {
      nativeInput?: { account: string; amount: number };
      nativeOutput?: { account: string; amount: number };
      tokenInputs?: Array<{
        mint: string;
        rawTokenAmount?: { tokenAmount: string; decimals: number };
      }>;
      tokenOutputs?: Array<{
        mint: string;
        rawTokenAmount?: { tokenAmount: string; decimals: number };
      }>;
    };
  };
};

type AccountDataEntry = {
  account: string;
  nativeBalanceChange?: number;
  tokenBalanceChanges?: Array<{
    userAccount?: string;
    tokenAccount?: string;
    mint?: string;
    rawTokenAmount?: { tokenAmount?: string; decimals?: number };
  }>;
};

type TokenNet = { net: number; decimals: number };
type NonSwapSummary = {
  walletNativeChangeLamports: number;
  walletTokenMints: string[];
  walletTokenNet: Array<{ mint: string; net: number; decimals: number }>;
};

function isMirrorQuoteMint(mint: string | undefined): boolean {
  return mint === WRAPPED_SOL_MINT || mint === USDC_MINT;
}

function addTokenNet(
  tokenNet: Map<string, TokenNet>,
  mint: string | undefined,
  amountRaw: number,
  decimals: number | undefined
): void {
  if (!mint) return;
  if (!Number.isFinite(amountRaw) || amountRaw === 0) return;
  const entry = tokenNet.get(mint) ?? { net: 0, decimals: decimals ?? 0 };
  entry.decimals = decimals ?? entry.decimals;
  entry.net += amountRaw;
  tokenNet.set(mint, entry);
}

function getBestTokenNet(
  tokenNet: Map<string, TokenNet>,
  options: { excludeQuoteMints?: boolean } = {}
): {
  mint: string | null;
  net: number;
  abs: number;
} {
  let mint: string | null = null;
  let net = 0;
  let abs = 0;
  for (const [candidateMint, candidate] of tokenNet) {
    if (options.excludeQuoteMints && isMirrorQuoteMint(candidateMint)) continue;
    const candidateAbs = Math.abs(candidate.net);
    if (candidateAbs > abs) {
      abs = candidateAbs;
      mint = candidateMint;
      net = candidate.net;
    }
  }
  return { mint, net, abs };
}

function getBestQuoteNet(tokenNet: Map<string, TokenNet>, wantPositive: boolean): {
  mint: string | null;
  net: number;
  abs: number;
} {
  let mint: string | null = null;
  let net = 0;
  let abs = 0;
  for (const candidateMint of [WRAPPED_SOL_MINT, USDC_MINT]) {
    const candidate = tokenNet.get(candidateMint);
    if (!candidate) continue;
    if (wantPositive && candidate.net <= 0) continue;
    if (!wantPositive && candidate.net >= 0) continue;
    const candidateAbs = Math.abs(candidate.net);
    if (candidateAbs > abs) {
      mint = candidateMint;
      net = candidate.net;
      abs = candidateAbs;
    }
  }
  return { mint, net, abs };
}

function summarizeWalletActivity(tx: HeliusParsedTx, wallet: string): NonSwapSummary {
  let walletNativeChangeLamports = 0;
  const tokenNet = new Map<string, TokenNet>();

  for (const entry of tx.accountData ?? []) {
    if (entry.account !== wallet) continue;
    walletNativeChangeLamports += entry.nativeBalanceChange ?? 0;
    for (const change of entry.tokenBalanceChanges ?? []) {
      const raw = Number(change.rawTokenAmount?.tokenAmount ?? "0");
      addTokenNet(tokenNet, change.mint, raw, change.rawTokenAmount?.decimals);
    }
  }

  const walletTokenNet = Array.from(tokenNet.entries())
    .map(([mint, net]) => ({ mint, net: net.net, decimals: net.decimals }))
    .sort((a, b) => Math.abs(b.net) - Math.abs(a.net));
  return {
    walletNativeChangeLamports,
    walletTokenMints: walletTokenNet.map((t) => t.mint),
    walletTokenNet,
  };
}

function formatNonSwapSummary(tx: HeliusParsedTx, wallet: string): string | null {
  if (tx.type !== "CREATE_POOL" && tx.source !== "PUMP_AMM") return null;
  const summary = summarizeWalletActivity(tx, wallet);
  const sol = summary.walletNativeChangeLamports / 1_000_000_000;
  const solPart = `walletSOL=${sol >= 0 ? "+" : ""}${sol.toFixed(4)}`;
  const mintPart =
    summary.walletTokenNet.length > 0
      ? `walletMints=${summary.walletTokenNet.map((t) => `${t.mint.slice(0, 6)}…${t.mint.slice(-4)}`).join(",")}`
      : "walletMints=none";
  const role =
    Math.abs(summary.walletNativeChangeLamports) > 0 || summary.walletTokenNet.length > 0
      ? "wallet balance changed"
      : "wallet only referenced";
  return `${role}; ${solPart}; ${mintPart}; sig=${tx.signature}`;
}

function isWalletTokenAccount(
  owner: string,
  mint: string | undefined,
  tokenAccount: string | undefined
): boolean {
  if (!mint || !tokenAccount) return false;
  try {
    const ownerKey = new PublicKey(owner);
    const mintKey = new PublicKey(mint);
    for (const tokenProgramId of TOKEN_PROGRAM_IDS) {
      const [ata] = PublicKey.findProgramAddressSync(
        [ownerKey.toBuffer(), tokenProgramId.toBuffer(), mintKey.toBuffer()],
        ASSOCIATED_TOKEN_PROGRAM_ID
      );
      if (ata.toBase58() === tokenAccount) return true;
    }
  } catch {
    return false;
  }
  return false;
}

async function fetchWithRetry(url: string, init?: RequestInit): Promise<Response> {
  const MAX_ATTEMPTS = 3;
  let lastErr: unknown;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    try {
      await waitForHeliusSlot();
      const response = await fetch(url, {
        ...init,
        signal: AbortSignal.timeout(HELIUS_FETCH_TIMEOUT_MS),
      });
      if (!response.ok) {
        if (response.status === 429 && attempt < MAX_ATTEMPTS) {
          noteHeliusRateLimit();
          // Rate limited - use exponential backoff with jitter
          const baseDelay = 1000; // 1 second base
          const maxDelay = 10000; // 10 second max
          const exponentialDelay = Math.min(baseDelay * Math.pow(2, attempt - 1), maxDelay);
          const jitter = Math.random() * 1000; // Add up to 1 second jitter
          const delay = exponentialDelay + jitter;
          
          console.warn(`?? Rate limited (429), retrying in ${Math.round(delay)}ms... (attempt ${attempt}/${MAX_ATTEMPTS})`);
          await sleep(delay);
          lastErr = new Error(`Rate limited: 429`);
          continue;
        }
        if (response.status >= 500 && attempt < MAX_ATTEMPTS) {
          lastErr = new Error(`HTTP ${response.status}`);
          await sleep(300 * attempt);
          continue;
        }
      }
      return response;
    } catch (err) {
      lastErr = err;
      if (attempt >= MAX_ATTEMPTS) break;
      await sleep(300 * attempt);
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error(String(lastErr));
}

/**
 * Fetch recent parsed transactions for an address via Helius Parse Transaction History (single round-trip).
 */
async function parseAddressHistory(
  address: string,
  limit: number = 20
): Promise<HeliusParsedTx[]> {
  const url = heliusAddressTransactionsUrl(address, undefined, limit);
  if (!url) return [];
  const response = await fetchWithRetry(url);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Helius address history failed: ${response.status} ${text}`);
  }
  return (await response.json()) as HeliusParsedTx[];
}

/**
 * Startup helper: collect mints this wallet already bought in recent parsed history. This is not
 * used by live detection, so it can spend Helius API time without touching the mirror hot path.
 */
export async function getRecentWalletBuyMints(
  wallet: string,
  limit: number,
  maxTimestampSec: number = 0
): Promise<Set<string>> {
  const mints = new Set<string>();
  if (limit <= 0) return mints;
  const parsedList = await parseAddressHistory(wallet, limit);
  for (const tx of parsedList) {
    if (maxTimestampSec > 0 && tx.timestamp != null && tx.timestamp >= maxTimestampSec) continue;
    const swap = extractSwap(tx, wallet);
    if (!swap) continue;
    if (isMirrorQuoteMint(swap.inputMint) && !isMirrorQuoteMint(swap.outputMint)) {
      mints.add(swap.outputMint);
    }
  }
  return mints;
}

/**
 * Parse transaction signatures via Helius enhanced API, retrying on transient 5xx / network errors.
 */
async function parseTransactions(signatures: string[]): Promise<HeliusParsedTx[]> {
  if (signatures.length === 0 || !heliusEnhancedTransactionsUrl) return [];

  const MAX_ATTEMPTS = 3;
  let lastErr: unknown;

  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    try {
      await waitForHeliusSlot();
      const response = await fetch(heliusEnhancedTransactionsUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transactions: signatures }),
        signal: AbortSignal.timeout(HELIUS_FETCH_TIMEOUT_MS),
      });
      if (!response.ok) {
        const text = await response.text();
        // Retry on 5xx and 429; fail fast on other 4xx.
        if (response.status === 429 && attempt < MAX_ATTEMPTS) {
          noteHeliusRateLimit();
          // Rate limited - use exponential backoff with jitter
          const baseDelay = 2000; // 2 second base for enhanced API
          const maxDelay = 15000; // 15 second max
          const exponentialDelay = Math.min(baseDelay * Math.pow(2, attempt - 1), maxDelay);
          const jitter = Math.random() * 2000; // Add up to 2 seconds jitter
          const delay = exponentialDelay + jitter;
          
          console.warn(`?? Enhanced API rate limited (429), retrying in ${Math.round(delay)}ms... (attempt ${attempt}/${MAX_ATTEMPTS})`);
          await sleep(delay);
          lastErr = new Error(`Enhanced API rate limited: 429`);
          continue;
        }
        if (response.status >= 500 && attempt < MAX_ATTEMPTS) {
          lastErr = new Error(`Helius ${response.status}: ${text.slice(0, 120)}`);
          await sleep(300 * attempt);
          continue;
        }
        throw new Error(`Helius parse transactions failed: ${response.status} ${text}`);
      }
      return (await response.json()) as HeliusParsedTx[];
    } catch (err) {
      lastErr = err;
      if (attempt >= MAX_ATTEMPTS) break;
      await sleep(300 * attempt);
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error(String(lastErr));
}

async function parseTransactionsSoon(signatures: string[]): Promise<HeliusParsedTx[]> {
  for (let attempt = 1; attempt <= WS_PARSE_ATTEMPTS; attempt++) {
    const parsed = await parseTransactions(signatures);
    if (parsed.length > 0) return parsed;
    if (attempt < WS_PARSE_ATTEMPTS) {
      await sleep(WS_PARSE_RETRY_MS);
    }
  }
  return [];
}

/**
 * Extract a Jupiter SOL → token buy or token → SOL sell. Token→token swaps are ignored.
 */
function extractJupiterSwap(tx: HeliusParsedTx, wallet: string): DetectedSwap | null {
  if (tx.source !== JUPITER_SOURCE || !SWAP_TYPES.has(tx.type ?? "")) return null;
  const swap = tx.events?.swap;
  if (!swap) return null;

  const nativeIn = swap.nativeInput;
  const nativeOut = swap.nativeOutput;
  const tokenInputs = swap.tokenInputs ?? [];
  const tokenOutputs = swap.tokenOutputs ?? [];

  // BUY: SOL in → token out
  if (nativeIn && nativeIn.amount > 0 && tokenOutputs.length > 0) {
    const outputMint = tokenOutputs[0]!.mint;
    if (outputMint === WRAPPED_SOL_MINT) return null;
    return {
      signature: tx.signature,
      wallet,
      inputMint: WRAPPED_SOL_MINT,
      outputMint,
      amountRaw: nativeIn.amount,
      isNativeInput: true,
    };
  }

  // SELL: token in → SOL out (full exit; amount here is source's sell, overridden by mirror.ts)
  if (nativeOut && nativeOut.amount > 0 && tokenInputs.length > 0) {
    const inputMint = tokenInputs[0]!.mint;
    if (inputMint === WRAPPED_SOL_MINT) return null;
    const raw = tokenInputs[0]!.rawTokenAmount;
    const amountRaw = raw
      ? Math.floor(parseFloat(raw.tokenAmount) * Math.pow(10, raw.decimals))
      : 0;
    if (amountRaw <= 0) return null;
    return {
      signature: tx.signature,
      wallet,
      inputMint,
      outputMint: WRAPPED_SOL_MINT,
      amountRaw,
      isNativeInput: false,
    };
  }

  return null;
}

/**
 * Generic SOL↔memecoin extractor for any DEX source (PumpSwap, Raydium, Meteora, Pump.fun, etc.).
 * Uses tokenTransfers / nativeTransfers so it works regardless of the `source` tag.
 * Sums per-wallet SOL net flow against aggregated token net flow to survive multi-hop routes.
 */
function extractTransferSwap(tx: HeliusParsedTx, wallet: string): DetectedSwap | null {
  if (!SWAP_TYPES.has(tx.type ?? "")) return null;

  const rawTx = tx as {
    tokenTransfers?: Array<{
      fromUserAccount?: string;
      toUserAccount?: string;
      fromTokenAccount?: string;
      toTokenAccount?: string;
      mint?: string;
      tokenAmount?: string;
      decimals?: number;
    }>;
    nativeTransfers?: Array<{
      fromUserAccount?: string;
      toUserAccount?: string;
      amount?: number;
    }>;
  };
  const tokenTransfers = rawTx.tokenTransfers ?? [];
  const nativeTransfers = rawTx.nativeTransfers ?? [];

  // Net SOL flow for wallet (positive = incoming, negative = outgoing).
  let nativeNet = 0;
  for (const nt of nativeTransfers) {
    const amt = nt.amount ?? 0;
    if (nt.toUserAccount === wallet) nativeNet += amt;
    if (nt.fromUserAccount === wallet) nativeNet -= amt;
  }

  // Net token flow for wallet, per mint.
  const tokenNet = new Map<string, TokenNet>();
  for (const tt of tokenTransfers) {
    if (!tt.mint || tt.mint === WRAPPED_SOL_MINT) continue;
    const decimals = typeof tt.decimals === "number" ? tt.decimals : 0;
    const amtStr = tt.tokenAmount;
    if (amtStr == null) continue;
    const raw = Math.floor(parseFloat(amtStr) * Math.pow(10, decimals));
    if (!Number.isFinite(raw) || raw === 0) continue;
    if (tt.toUserAccount === wallet) addTokenNet(tokenNet, tt.mint, raw, decimals);
    if (tt.fromUserAccount === wallet) addTokenNet(tokenNet, tt.mint, -raw, decimals);
    if (!tt.toUserAccount && isWalletTokenAccount(wallet, tt.mint, tt.toTokenAccount)) {
      addTokenNet(tokenNet, tt.mint, raw, decimals);
    }
    if (!tt.fromUserAccount && isWalletTokenAccount(wallet, tt.mint, tt.fromTokenAccount)) {
      addTokenNet(tokenNet, tt.mint, -raw, decimals);
    }
  }

  // Pick the token with the largest |net| for this wallet.
  const { mint: bestMint, net: bestNet, abs: bestAbs } = getBestTokenNet(tokenNet, {
    excludeQuoteMints: true,
  });
  if (!bestMint || bestAbs === 0) return null;

  // BUY: SOL out, token in.
  if (nativeNet < 0 && bestNet > 0) {
    const quote = getBestQuoteNet(tokenNet, false);
    const inputMint = quote.mint ?? WRAPPED_SOL_MINT;
    const amountRaw = quote.mint ? quote.abs : Math.abs(nativeNet);
    return {
      signature: tx.signature,
      wallet,
      inputMint,
      outputMint: bestMint,
      amountRaw,
      isNativeInput: inputMint === WRAPPED_SOL_MINT,
      sourceQuoteMint: inputMint,
      sourceQuoteAmountRaw: amountRaw,
    };
  }

  // SELL: token out, SOL in.
  if (nativeNet > 0 && bestNet < 0) {
    return {
      signature: tx.signature,
      wallet,
      inputMint: bestMint,
      outputMint: WRAPPED_SOL_MINT,
      amountRaw: Math.abs(bestNet),
      isNativeInput: false,
    };
  }

  if (nativeNet === 0 && bestNet > 0) {
    const quote = getBestQuoteNet(tokenNet, false);
    if (quote.mint) {
      return {
        signature: tx.signature,
        wallet,
        inputMint: quote.mint,
        outputMint: bestMint,
        amountRaw: Math.abs(quote.net),
        isNativeInput: quote.mint === WRAPPED_SOL_MINT,
        sourceQuoteMint: quote.mint,
        sourceQuoteAmountRaw: Math.abs(quote.net),
      };
    }
  }

  if (nativeNet === 0 && bestNet < 0) {
    const quote = getBestQuoteNet(tokenNet, true);
    if (quote.mint) {
      return {
        signature: tx.signature,
        wallet,
        inputMint: bestMint,
        outputMint: WRAPPED_SOL_MINT,
        amountRaw: Math.abs(bestNet),
        isNativeInput: false,
        counterpartyAmountRaw: quote.net,
        counterpartyDecimals: quote.mint === USDC_MINT ? 6 : 9,
      };
    }
  }

  return null;
}

/**
 * Extract using Helius's authoritative `accountData` field, which reports actual before/after
 * balance changes per account. Required for pump.fun direct-curve trades where tokens move via
 * a PDA signer (so tokenTransfers don't have fromUserAccount === wallet).
 */
function extractFromAccountData(tx: HeliusParsedTx, wallet: string): DetectedSwap | null {
  if (!SWAP_TYPES.has(tx.type ?? "")) return null;

  const accountData = (tx as { accountData?: AccountDataEntry[] }).accountData;
  if (!accountData || accountData.length === 0) return null;

  // Sum wallet's native lamport change (can appear on the wallet row only).
  let nativeNet = 0;
  for (const a of accountData) {
    if (a.account === wallet) nativeNet += a.nativeBalanceChange ?? 0;
  }

  // Sum token balance changes where the ATA's owner (userAccount) is our wallet, grouped by mint.
  const tokenNet = new Map<string, TokenNet>();
  for (const a of accountData) {
    for (const tbc of a.tokenBalanceChanges ?? []) {
      const belongsToWallet =
        tbc.userAccount === wallet ||
        isWalletTokenAccount(wallet, tbc.mint, tbc.tokenAccount ?? a.account);
      if (!belongsToWallet) continue;
      if (!tbc.mint || tbc.mint === WRAPPED_SOL_MINT) continue;
      const raw = parseFloat(tbc.rawTokenAmount?.tokenAmount ?? "0");
      if (!Number.isFinite(raw) || raw === 0) continue;
      const decimals = tbc.rawTokenAmount?.decimals ?? 0;
      addTokenNet(tokenNet, tbc.mint, raw, decimals);
    }
  }

  const { mint: bestMint, net: bestNet, abs: bestAbs } = getBestTokenNet(tokenNet, {
    excludeQuoteMints: true,
  });
  if (!bestMint || bestAbs === 0) return null;

  // BUY: SOL decreased, token increased.
  if (nativeNet < 0 && bestNet > 0) {
    const decimals = tokenNet.get(bestMint)?.decimals ?? 0;
    const quote = getBestQuoteNet(tokenNet, false);
    const inputMint = quote.mint ?? WRAPPED_SOL_MINT;
    const amountRaw = quote.mint ? quote.abs : Math.abs(nativeNet);
    return {
      signature: tx.signature,
      wallet,
      inputMint,
      outputMint: bestMint,
      amountRaw,
      isNativeInput: inputMint === WRAPPED_SOL_MINT,
      counterpartyAmountRaw: bestNet,
      counterpartyDecimals: decimals,
      sourceQuoteMint: inputMint,
      sourceQuoteAmountRaw: amountRaw,
    };
  }

  // SELL: SOL increased, token decreased.
  if (nativeNet > 0 && bestNet < 0) {
    return {
      signature: tx.signature,
      wallet,
      inputMint: bestMint,
      outputMint: WRAPPED_SOL_MINT,
      amountRaw: Math.abs(bestNet),
      isNativeInput: false,
      counterpartyAmountRaw: nativeNet,
      counterpartyDecimals: 9,
    };
  }

  if (nativeNet === 0 && bestNet > 0) {
    const quote = getBestQuoteNet(tokenNet, false);
    if (quote.mint) {
      return {
        signature: tx.signature,
        wallet,
        inputMint: quote.mint,
        outputMint: bestMint,
        amountRaw: Math.abs(quote.net),
        isNativeInput: quote.mint === WRAPPED_SOL_MINT,
        sourceQuoteMint: quote.mint,
        sourceQuoteAmountRaw: Math.abs(quote.net),
      };
    }
  }

  if (nativeNet === 0 && bestNet < 0) {
    const quote = getBestQuoteNet(tokenNet, true);
    if (quote.mint) {
      return {
        signature: tx.signature,
        wallet,
        inputMint: bestMint,
        outputMint: WRAPPED_SOL_MINT,
        amountRaw: Math.abs(bestNet),
        isNativeInput: false,
        counterpartyAmountRaw: quote.net,
        counterpartyDecimals: quote.mint === USDC_MINT ? 6 : 9,
      };
    }
  }

  return null;
}

function extractSwap(tx: HeliusParsedTx, wallet: string): DetectedSwap | null {
  return (
    extractJupiterSwap(tx, wallet) ??
    extractFromAccountData(tx, wallet) ??
    extractTransferSwap(tx, wallet)
  );
}

/**
 * Resolve every account key referenced by a versioned transaction (static + ALT-loaded).
 * Returns base58 strings so we can match against the wallet address by string equality.
 */
function resolveAccountKeys(tx: VersionedTransactionResponse): string[] {
  const message = tx.transaction.message;
  const staticKeys = (
    "staticAccountKeys" in message
      ? message.staticAccountKeys
      : (message as unknown as { accountKeys: PublicKey[] }).accountKeys ?? []
  ) as PublicKey[];
  const loaded = tx.meta?.loadedAddresses;
  const writable = loaded?.writable ?? [];
  const readonly = loaded?.readonly ?? [];
  return [...staticKeys, ...writable, ...readonly].map((k) =>
    typeof k === "string" ? k : k.toBase58()
  );
}


function collectSourceProgramIdsFromRawTx(
  tx: VersionedTransactionResponse,
  accountKeys: string[]
): string[] {
  const ids = new Set<string>();
  const message = tx.transaction.message as unknown as {
    compiledInstructions?: Array<{ programIdIndex: number }>;
    instructions?: Array<{ programIdIndex: number }>;
  };
  const add = (programIdIndex: number | undefined) => {
    if (typeof programIdIndex !== "number") return;
    const id = accountKeys[programIdIndex];
    if (id) ids.add(id);
  };
  for (const ix of message.compiledInstructions ?? message.instructions ?? []) add(ix.programIdIndex);
  for (const group of tx.meta?.innerInstructions ?? []) {
    for (const ix of group.instructions ?? []) add((ix as unknown as { programIdIndex?: number }).programIdIndex);
  }
  return [...ids];
}

function instructionDataToBuffer(data: unknown): Buffer {
  if (data == null) return Buffer.alloc(0);
  if (Buffer.isBuffer(data)) return data;
  if (data instanceof Uint8Array) return Buffer.from(data);
  if (Array.isArray(data)) return Buffer.from(data);
  if (typeof data === "string") {
    try {
      return Buffer.from(bs58.decode(data));
    } catch {
      try {
        return Buffer.from(data, "base64");
      } catch {
        return Buffer.alloc(0);
      }
    }
  }
  return Buffer.alloc(0);
}

function matchesDiscriminator(data: Buffer, discriminator: Buffer): boolean {
  return data.length >= discriminator.length && data.subarray(0, discriminator.length).equals(discriminator);
}

function extractPumpSwapAccountsFromRawTx(
  tx: VersionedTransactionResponse,
  accountKeys: string[],
  mint: string
): SourcePumpSwapAccountMeta[] | undefined {
  const sourceMessage = tx.transaction.message;
  const message = tx.transaction.message as unknown as {
    compiledInstructions?: Array<{
      programIdIndex: number;
      accountKeyIndexes?: number[];
      accounts?: number[] | Uint8Array;
      data?: unknown;
    }>;
    instructions?: Array<{
      programIdIndex: number;
      accountKeyIndexes?: number[];
      accounts?: number[] | Uint8Array;
      data?: unknown;
    }>;
  };

  const matchesPumpSwapIx = (ix: {
    programIdIndex: number;
    accountKeyIndexes?: number[];
    accounts?: number[] | Uint8Array;
    data?: unknown;
  }): SourcePumpSwapAccountMeta[] | undefined => {
    if (accountKeys[ix.programIdIndex] !== PUMPSWAP_PROGRAM_ID_BASE58) return undefined;
    const data = instructionDataToBuffer(ix.data);
    if (
      !matchesDiscriminator(data, PUMPSWAP_BUY_DISCRIMINATOR) &&
      !matchesDiscriminator(data, PUMPSWAP_BUY_EXACT_QUOTE_IN_DISCRIMINATOR) &&
      !matchesDiscriminator(data, PUMPSWAP_SELL_DISCRIMINATOR)
    ) {
      return undefined;
    }
    const indexes = ix.accountKeyIndexes ?? Array.from(ix.accounts ?? []);
    const keys = indexes.map((idx) => accountKeys[idx]).filter((key): key is string => Boolean(key));
    if (keys[3] !== mint) return undefined;
    const accounts = indexes.map((idx) => {
      const pubkey = accountKeys[idx];
      if (!pubkey) return null;
      return {
        pubkey,
        isSigner: sourceMessage.isAccountSigner(idx),
        isWritable: sourceMessage.isAccountWritable(idx),
      };
    });
    if (accounts.some((account) => account == null)) return undefined;
    return accounts as SourcePumpSwapAccountMeta[];
  };

  const instructions = message.compiledInstructions ?? message.instructions ?? [];
  for (const ix of instructions) {
    const keys = matchesPumpSwapIx(ix);
    if (keys) return keys;
  }

  for (const group of tx.meta?.innerInstructions ?? []) {
    for (const ix of group.instructions) {
      const keys = matchesPumpSwapIx(ix as unknown as {
        programIdIndex: number;
        accountKeyIndexes?: number[];
        accounts?: number[] | Uint8Array;
        data?: unknown;
      });
      if (keys) return keys;
    }
  }
  return undefined;
}

/**
 * Direct extraction from a raw `getTransaction` response — uses balance deltas (preBalances/postBalances
 * and preTokenBalances/postTokenBalances) to classify a SOL↔memecoin swap. This is the fast path; it
 * bypasses the Helius enhanced-parse round-trip and its 600-1500ms indexing lag.
 */
function extractSwapFromRawTx(
  tx: VersionedTransactionResponse,
  wallet: string,
  signature: string
): DetectedSwap | null {
  const meta = tx.meta;
  if (!meta || meta.err) return null;

  const accountKeys = resolveAccountKeys(tx);
  const sourceProgramIds = collectSourceProgramIdsFromRawTx(tx, accountKeys);
  const walletIdx = accountKeys.indexOf(wallet);
  if (walletIdx < 0) return null;

  const preLamports = meta.preBalances?.[walletIdx] ?? 0;
  const postLamports = meta.postBalances?.[walletIdx] ?? 0;
  const fee = meta.fee ?? 0;
  // Add fee back so amountRaw reflects swap size, not gross outflow (only the signer pays the fee).
  const isSigner = walletIdx === 0;
  const nativeNet = postLamports - preLamports + (isSigner ? fee : 0);

  // Build pre and post token snapshots for our wallet only, indexed by accountIndex.
  type TokenSnap = { mint: string; amount: bigint; decimals: number };
  const preMap = new Map<number, TokenSnap>();
  for (const ptb of meta.preTokenBalances ?? []) {
    if (ptb.owner !== wallet) continue;
    preMap.set(ptb.accountIndex, {
      mint: ptb.mint,
      amount: BigInt(ptb.uiTokenAmount.amount),
      decimals: ptb.uiTokenAmount.decimals,
    });
  }

  const tokenNet = new Map<string, TokenNet>();
  const handledIndices = new Set<number>();
  for (const ptb of meta.postTokenBalances ?? []) {
    if (ptb.owner !== wallet) continue;
    handledIndices.add(ptb.accountIndex);
    const pre = preMap.get(ptb.accountIndex);
    const preAmount = pre?.amount ?? 0n;
    const postAmount = BigInt(ptb.uiTokenAmount.amount);
    const delta = Number(postAmount - preAmount);
    if (delta === 0) continue;
    addTokenNet(tokenNet, ptb.mint, delta, ptb.uiTokenAmount.decimals);
  }
  // Account closed during the tx (existed pre, gone post) — count its full balance as outflow.
  for (const [idx, pre] of preMap) {
    if (handledIndices.has(idx)) continue;
    addTokenNet(tokenNet, pre.mint, -Number(pre.amount), pre.decimals);
  }

  const { mint: bestMint, net: bestNet, abs: bestAbs } = getBestTokenNet(tokenNet, {
    excludeQuoteMints: true,
  });
  if (!bestMint || bestAbs === 0) return null;

  // BUY: SOL out, token in.
  if (nativeNet < 0 && bestNet > 0) {
    const decimals = tokenNet.get(bestMint)?.decimals ?? 0;
    const quote = getBestQuoteNet(tokenNet, false);
    const inputMint = quote.mint ?? WRAPPED_SOL_MINT;
    const amountRaw = quote.mint ? quote.abs : Math.abs(nativeNet);
    const sourcePumpSwapAccounts = extractPumpSwapAccountsFromRawTx(tx, accountKeys, bestMint);
    return {
      signature,
      wallet,
      sourceProgramIds,
      inputMint,
      outputMint: bestMint,
      amountRaw,
      isNativeInput: inputMint === WRAPPED_SOL_MINT,
      counterpartyAmountRaw: bestNet, // tokens received
      counterpartyDecimals: decimals,
      sourceQuoteMint: inputMint,
      sourceQuoteAmountRaw: amountRaw,
      sourcePumpSwapAccounts,
    };
  }

  // SELL: token out, SOL in.
  if (nativeNet > 0 && bestNet < 0) {
    const sourcePumpSwapAccounts = extractPumpSwapAccountsFromRawTx(tx, accountKeys, bestMint);
    return {
      signature,
      wallet,
      sourceProgramIds,
      inputMint: bestMint,
      outputMint: WRAPPED_SOL_MINT,
      amountRaw: Math.abs(bestNet),
      isNativeInput: false,
      counterpartyAmountRaw: nativeNet, // SOL received
      counterpartyDecimals: 9,
      sourcePumpSwapAccounts,
    };
  }

  if (nativeNet === 0 && bestNet > 0) {
    const quote = getBestQuoteNet(tokenNet, false);
    if (quote.mint) {
      const decimals = tokenNet.get(bestMint)?.decimals ?? 0;
      const sourcePumpSwapAccounts = extractPumpSwapAccountsFromRawTx(tx, accountKeys, bestMint);
      return {
        signature,
        wallet,
        sourceProgramIds,
        inputMint: quote.mint,
        outputMint: bestMint,
        amountRaw: Math.abs(quote.net),
        isNativeInput: quote.mint === WRAPPED_SOL_MINT,
        counterpartyAmountRaw: bestNet,
        counterpartyDecimals: decimals,
        sourceQuoteMint: quote.mint,
        sourceQuoteAmountRaw: Math.abs(quote.net),
        sourcePumpSwapAccounts,
      };
    }
  }

  if (nativeNet === 0 && bestNet < 0) {
    const quote = getBestQuoteNet(tokenNet, true);
    if (quote.mint) {
      const sourcePumpSwapAccounts = extractPumpSwapAccountsFromRawTx(tx, accountKeys, bestMint);
      return {
        signature,
        wallet,
        sourceProgramIds,
        inputMint: bestMint,
        outputMint: WRAPPED_SOL_MINT,
        amountRaw: Math.abs(bestNet),
        isNativeInput: false,
        counterpartyAmountRaw: quote.net,
        counterpartyDecimals: quote.mint === USDC_MINT ? 6 : 9,
        sourcePumpSwapAccounts,
      };
    }
  }

  return null;
}

/**
 * Try to fetch and extract a swap directly from the chain via getTransaction. Retries briefly while
 * the tx is still propagating; returns null if it never appears or isn't a SOL↔memecoin swap.
 *
 * This is the WS fast path. On hit, saves ~600-1500ms vs the Helius enhanced-parse round-trip.
 */
const RAW_TX_ATTEMPTS = 6;
const RAW_TX_RETRY_MS = 75;

export async function getRawTxSwap(
  signature: string,
  wallet: string
): Promise<DetectedSwap | null> {
  for (let attempt = 0; attempt < RAW_TX_ATTEMPTS; attempt++) {
    try {
      const tx = await readConnection.getTransaction(signature, {
        maxSupportedTransactionVersion: 0,
        commitment: "confirmed",
      });
      if (tx) {
        // Tx is visible — extract or give up immediately (no further retries).
        return extractSwapFromRawTx(tx, wallet, signature);
      }
    } catch {
      // Transient RPC error — retry.
    }
    if (attempt < RAW_TX_ATTEMPTS - 1) await sleep(RAW_TX_RETRY_MS);
  }
  return null;
}

/** Minimal surface used by watcher — accepts either a Set or a TTL-bounded wrapper. */
export interface SignatureDedupe {
  has(sig: string): boolean;
  add(sig: string): void;
}

/**
 * Poll fallback: fetch recent parsed txs per wallet (one round-trip each) and return unseen swaps
 * that landed at or after `minTimestampSec`. Used as a safety net in case the WebSocket drops.
 */
export async function getNewSwaps(
  trackedWallets: string[],
  processedSignatures: SignatureDedupe,
  minTimestampSec: number = 0
): Promise<DetectedSwap[]> {
  const allSwaps: DetectedSwap[] = [];
  for (const wallet of trackedWallets) {
    try {
      const parsedList = await parseAddressHistory(wallet, 5);
      for (const tx of parsedList) {
        if (!tx?.signature || processedSignatures.has(tx.signature)) continue;
        processedSignatures.add(tx.signature);
        if (tx.timestamp != null && tx.timestamp < minTimestampSec) continue;
        const swap = extractSwap(tx, wallet);
        if (swap) {
          const detectedAtMs = Date.now();
          swap.detector = "poll-helius";
          swap.providerCreatedAtMs = tx.timestamp != null ? tx.timestamp * 1000 : undefined;
          swap.receivedAtMs = detectedAtMs;
          swap.detectedAtMs = detectedAtMs;
          allSwaps.push(swap);
        }
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      const isTimeout =
        err instanceof Error &&
        (err.name === "TimeoutError" || err.name === "AbortError" || msg.toLowerCase().includes("aborted"));
      if (isTimeout) {
        suppressedWatcherTimeoutWarnings += 1;
        const now = Date.now();
        if (now - lastWatcherTimeoutWarningAt >= WATCHER_TIMEOUT_WARNING_GAP_MS) {
          const suffix =
            suppressedWatcherTimeoutWarnings > 1
              ? ` (${suppressedWatcherTimeoutWarnings - 1} similar timeout(s) suppressed)`
              : "";
          console.warn(
            `Watcher poll fallback timeout for ${wallet.slice(0, 6)}...; gRPC/Rabbit/WS remain active${suffix}`
          );
          lastWatcherTimeoutWarningAt = now;
          suppressedWatcherTimeoutWarnings = 0;
        }
      } else {
        console.warn(`Watcher skip ${wallet.slice(0, 6)}...: ${msg}`);
      }
    }
  }

  return allSwaps;
}

export type Unsubscribe = () => Promise<void>;

/**
 * Subscribe via Helius WebSocket to `logsSubscribe` for each tracked wallet. Whenever the wallet
 * appears in a transaction, we fetch the parsed form (single POST) and invoke `onSwap` if it's a
 * SOL↔memecoin swap. Returns an unsubscribe function.
 */
export async function subscribeWalletSwaps(
  trackedWallets: string[],
  processedSignatures: SignatureDedupe,
  onSwap: (swap: DetectedSwap) => void
): Promise<Unsubscribe> {
  if (!websocketUrl) {
    console.warn("⚠️  No WEBSOCKET_URL set — skipping legacy WS subscription.");
    return async () => {};
  }

  const subIds: number[] = [];

  for (const wallet of trackedWallets) {
    try {
      const pubkey = new PublicKey(wallet);
      const subId = wsConnection.onLogs(
        pubkey,
        async (logs) => {
          const sig = logs.signature;
          if (!sig || logs.err || processedSignatures.has(sig) || wsParseInFlight.has(sig)) return;
          wsParseInFlight.add(sig);
          const detectedAt = Date.now();
          try {
            // Run both paths in parallel. Fast path = raw getTransaction (~150-300ms typical).
            // Slow path = Helius enhanced parse (~600-1500ms typical, rich diagnostic output).
            // If fast yields a swap we go immediately; otherwise we fall back to slow path.
            const fastPromise = getRawTxSwap(sig, wallet).catch(() => null);
            const slowPromise = parseTransactionsSoon([sig])
              .then((parsed) => {
                const tx = parsed[0];
                return { tx, swap: tx ? extractSwap(tx, wallet) : null };
              })
              .catch(() => ({ tx: undefined as HeliusParsedTx | undefined, swap: null as DetectedSwap | null }));

            let swap = await fastPromise;
            let source: "raw" | "enhanced" = "raw";
            let slowTx: HeliusParsedTx | undefined;

            if (!swap) {
              const slow = await slowPromise;
              swap = slow.swap;
              slowTx = slow.tx;
              source = "enhanced";
            }

            processedSignatures.add(sig);

            if (swap) {
              const detectedAtMs = Date.now();
              const ms = detectedAtMs - detectedAt;
              swap.detector = source === "raw" ? "ws-raw-rpc" : "ws-helius-enhanced";
              swap.providerCreatedAtMs = slowTx?.timestamp != null ? slowTx.timestamp * 1000 : undefined;
              swap.receivedAtMs = detectedAt;
              swap.detectedAtMs = detectedAtMs;
              console.log(`⚡ WS detected swap ${sig.slice(0, 8)}… (${source}, ${ms}ms)`);
              onSwap(swap);
              return;
            }

            // Neither path found a swap. Emit diagnostic from the enhanced tx if we got one.
            if (!slowTx) {
              console.log(`🔍 WS: no parsed tx for ${sig.slice(0, 8)}… (raw + Helius both empty)`);
              return;
            }
            const hasSwapEvent = Boolean((slowTx as { events?: { swap?: unknown } }).events?.swap);
            const tokenXfers = ((slowTx as { tokenTransfers?: unknown[] }).tokenTransfers ?? []).length;
            const nativeXfers = ((slowTx as { nativeTransfers?: unknown[] }).nativeTransfers ?? []).length;
            const acctData = ((slowTx as { accountData?: unknown[] }).accountData ?? []).length;
            console.log(
              `🔍 WS: no swap extracted from ${sig.slice(0, 8)}… ` +
                `(type=${slowTx.type ?? "?"}, source=${slowTx.source ?? "?"}, ` +
                `swapEvent=${hasSwapEvent}, tokenXfers=${tokenXfers}, nativeXfers=${nativeXfers}, accountData=${acctData})`
            );
            const nonSwapSummary = formatNonSwapSummary(slowTx, wallet);
            if (nonSwapSummary) {
              console.log(`   ↳ ${nonSwapSummary}`);
            }
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            console.warn(`⚠️  WS parse skip ${sig.slice(0, 8)}…: ${msg}`);
          } finally {
            wsParseInFlight.delete(sig);
          }
        },
        "processed"
      );
      subIds.push(subId);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`⚠️  WS subscribe failed for ${wallet.slice(0, 6)}…: ${msg}`);
    }
  }

  console.log(`🔔 WebSocket: subscribed to ${subIds.length}/${trackedWallets.length} wallet(s)`);

  return async () => {
    for (const id of subIds) {
      try {
        await wsConnection.removeOnLogsListener(id);
      } catch {
        // best-effort
      }
    }
  };
}
