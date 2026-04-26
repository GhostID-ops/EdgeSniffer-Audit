import { PublicKey } from "@solana/web3.js";
import {
  wsConnection,
  heliusEnhancedTransactionsUrl,
  heliusAddressTransactionsUrl,
  websocketUrl,
} from "./config.js";
import type { DetectedSwap } from "./types.js";
import { getTokenMeta } from "./tokenRegistry.js";

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const JUPITER_SOURCE = "JUPITER";
const SWAP_TYPES = new Set(["SWAP", "SWAP_EXACT_OUT", "SWAP_WITH_PRICE_IMPACT"]);
const wsParseInFlight = new Set<string>();
const WS_PARSE_ATTEMPTS = 12;
const WS_PARSE_RETRY_MS = 250;
const ASSOCIATED_TOKEN_PROGRAM_ID = new PublicKey(
  "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
);
const TOKEN_PROGRAM_IDS = [
  new PublicKey("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"),
  new PublicKey("TokenzQdBNbLqP5VEhdkAS6EPu8NQkMZ6H4hWzPvyxC"),
];

type HeliusParsedTx = {
  signature: string;
  /** Unix seconds when the tx landed on-chain. */
  timestamp?: number;
  type?: string;
  source?: string;
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

function addTokenNet(
  tokenNet: Map<string, TokenNet>,
  mint: string | undefined,
  amountRaw: number,
  decimals: number | undefined
): void {
  if (!mint || mint === WRAPPED_SOL_MINT) return;
  if (!Number.isFinite(amountRaw) || amountRaw === 0) return;
  const entry = tokenNet.get(mint) ?? { net: 0, decimals: decimals ?? 0 };
  entry.decimals = decimals ?? entry.decimals;
  entry.net += amountRaw;
  tokenNet.set(mint, entry);
}

function getBestTokenNet(tokenNet: Map<string, TokenNet>): {
  mint: string | null;
  net: number;
  abs: number;
} {
  let mint: string | null = null;
  let net = 0;
  let abs = 0;
  for (const [candidateMint, candidate] of tokenNet) {
    const candidateAbs = Math.abs(candidate.net);
    if (candidateAbs > abs) {
      abs = candidateAbs;
      mint = candidateMint;
      net = candidate.net;
    }
  }
  return { mint, net, abs };
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
      const response = await fetch(url, {
        ...init,
        signal: AbortSignal.timeout(8000),
      });
      if (!response.ok) {
        if (response.status === 429 && attempt < MAX_ATTEMPTS) {
          // Rate limited - use exponential backoff with jitter
          const baseDelay = 1000; // 1 second base
          const maxDelay = 10000; // 10 second max
          const exponentialDelay = Math.min(baseDelay * Math.pow(2, attempt - 1), maxDelay);
          const jitter = Math.random() * 1000; // Add up to 1 second jitter
          const delay = exponentialDelay + jitter;
          
          console.warn(`?? Rate limited (429), retrying in ${Math.round(delay)}ms... (attempt ${attempt}/${MAX_ATTEMPTS})`);
          await new Promise((r) => setTimeout(r, delay));
          lastErr = new Error(`Rate limited: 429`);
          continue;
        }
        if (response.status >= 500 && attempt < MAX_ATTEMPTS) {
          lastErr = new Error(`HTTP ${response.status}`);
          await new Promise((r) => setTimeout(r, 300 * attempt));
          continue;
        }
      }
      return response;
    } catch (err) {
      lastErr = err;
      if (attempt >= MAX_ATTEMPTS) break;
      await new Promise((r) => setTimeout(r, 300 * attempt));
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
 * Parse transaction signatures via Helius enhanced API, retrying on transient 5xx / network errors.
 */
async function parseTransactions(signatures: string[]): Promise<HeliusParsedTx[]> {
  if (signatures.length === 0 || !heliusEnhancedTransactionsUrl) return [];

  const MAX_ATTEMPTS = 3;
  let lastErr: unknown;

  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    try {
      const response = await fetch(heliusEnhancedTransactionsUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transactions: signatures }),
        signal: AbortSignal.timeout(8000),
      });
      if (!response.ok) {
        const text = await response.text();
        // Retry on 5xx and 429; fail fast on other 4xx.
        if (response.status === 429 && attempt < MAX_ATTEMPTS) {
          // Rate limited - use exponential backoff with jitter
          const baseDelay = 2000; // 2 second base for enhanced API
          const maxDelay = 15000; // 15 second max
          const exponentialDelay = Math.min(baseDelay * Math.pow(2, attempt - 1), maxDelay);
          const jitter = Math.random() * 2000; // Add up to 2 seconds jitter
          const delay = exponentialDelay + jitter;
          
          console.warn(`?? Enhanced API rate limited (429), retrying in ${Math.round(delay)}ms... (attempt ${attempt}/${MAX_ATTEMPTS})`);
          await new Promise((r) => setTimeout(r, delay));
          lastErr = new Error(`Enhanced API rate limited: 429`);
          continue;
        }
        if (response.status >= 500 && attempt < MAX_ATTEMPTS) {
          lastErr = new Error(`Helius ${response.status}: ${text.slice(0, 120)}`);
          await new Promise((r) => setTimeout(r, 300 * attempt));
          continue;
        }
        throw new Error(`Helius parse transactions failed: ${response.status} ${text}`);
      }
      return (await response.json()) as HeliusParsedTx[];
    } catch (err) {
      lastErr = err;
      if (attempt >= MAX_ATTEMPTS) break;
      await new Promise((r) => setTimeout(r, 300 * attempt));
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error(String(lastErr));
}

async function parseTransactionsSoon(signatures: string[]): Promise<HeliusParsedTx[]> {
  for (let attempt = 1; attempt <= WS_PARSE_ATTEMPTS; attempt++) {
    const parsed = await parseTransactions(signatures);
    if (parsed.length > 0) return parsed;
    if (attempt < WS_PARSE_ATTEMPTS) {
      await new Promise((r) => setTimeout(r, WS_PARSE_RETRY_MS));
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
  const { mint: bestMint, net: bestNet, abs: bestAbs } = getBestTokenNet(tokenNet);
  if (!bestMint || bestAbs === 0) return null;

  // BUY: SOL out, token in.
  if (nativeNet < 0 && bestNet > 0) {
    return {
      signature: tx.signature,
      wallet,
      inputMint: WRAPPED_SOL_MINT,
      outputMint: bestMint,
      amountRaw: Math.abs(nativeNet),
      isNativeInput: true,
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

  const { mint: bestMint, net: bestNet, abs: bestAbs } = getBestTokenNet(tokenNet);
  if (!bestMint || bestAbs === 0) return null;

  // BUY: SOL decreased, token increased.
  if (nativeNet < 0 && bestNet > 0) {
    return {
      signature: tx.signature,
      wallet,
      inputMint: WRAPPED_SOL_MINT,
      outputMint: bestMint,
      amountRaw: Math.abs(nativeNet),
      isNativeInput: true,
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
    };
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
 * Poll fallback: fetch recent parsed txs per wallet (one round-trip each) and return unseen swaps
 * that landed at or after `minTimestampSec`. Used as a safety net in case the WebSocket drops.
 */
export async function getNewSwaps(
  trackedWallets: string[],
  processedSignatures: Set<string>,
  minTimestampSec: number = 0
): Promise<DetectedSwap[]> {
  const perWallet = await Promise.all(
    trackedWallets.map(async (wallet): Promise<DetectedSwap[]> => {
      try {
        const parsedList = await parseAddressHistory(wallet);
        const swaps: DetectedSwap[] = [];
        for (const tx of parsedList) {
          if (!tx?.signature || processedSignatures.has(tx.signature)) continue;
          processedSignatures.add(tx.signature);
          // Ignore anything older than bot startup — fresh track only.
          if (tx.timestamp != null && tx.timestamp < minTimestampSec) continue;
          const swap = extractSwap(tx, wallet);
          if (swap) {
            swaps.push(swap);
          }
        }
        return swaps;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.warn(`⚠️  Watcher skip ${wallet.slice(0, 6)}…: ${msg}`);
        return [];
      }
    })
  );

  return perWallet.flat();
}

export type Unsubscribe = () => Promise<void>;

/**
 * Subscribe via Helius WebSocket to `logsSubscribe` for each tracked wallet. Whenever the wallet
 * appears in a transaction, we fetch the parsed form (single POST) and invoke `onSwap` if it's a
 * SOL↔memecoin swap. Returns an unsubscribe function.
 */
export async function subscribeWalletSwaps(
  trackedWallets: string[],
  processedSignatures: Set<string>,
  onSwap: (swap: DetectedSwap) => void
): Promise<Unsubscribe> {
  if (!websocketUrl) {
    console.warn("⚠️  No WEBSOCKET_URL set — skipping WS subscription (polling only).");
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
          try {
            const parsed = await parseTransactionsSoon([sig]);
            const tx = parsed[0];
            if (!tx) {
              console.log(`🔍 WS: no parsed tx for ${sig.slice(0, 8)}… (Helius returned nothing)`);
              return;
            }
            processedSignatures.add(sig);
            const swap = extractSwap(tx, wallet);
            if (swap) {
              console.log(`⚡ WS detected swap ${sig.slice(0, 8)}…`);
              onSwap(swap);
            } else {
              // Diagnostic: WS caught a tx we couldn't extract. Useful to see why sells are missed.
              const hasSwapEvent = Boolean((tx as { events?: { swap?: unknown } }).events?.swap);
              const tokenXfers = ((tx as { tokenTransfers?: unknown[] }).tokenTransfers ?? []).length;
              const nativeXfers = ((tx as { nativeTransfers?: unknown[] }).nativeTransfers ?? []).length;
              const acctData = ((tx as { accountData?: unknown[] }).accountData ?? []).length;
              console.log(
                `🔍 WS: no swap extracted from ${sig.slice(0, 8)}… ` +
                `(type=${tx.type ?? "?"}, source=${tx.source ?? "?"}, ` +
                `swapEvent=${hasSwapEvent}, tokenXfers=${tokenXfers}, nativeXfers=${nativeXfers}, accountData=${acctData})`
              );
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
