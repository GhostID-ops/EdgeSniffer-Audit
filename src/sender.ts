import {
  Keypair,
  PublicKey,
  SystemProgram,
  TransactionMessage,
  VersionedTransaction,
  AddressLookupTableAccount,
  ComputeBudgetProgram,
  ComputeBudgetInstruction,
  type TransactionInstruction,
} from "@solana/web3.js";
import {
  readConnection,
  senderEndpointUrl,
  computeUnitLimit,
  useJitoDirect,
  useJitoFallback,
  broadcastRaceEnabled,
} from "./config.js";
import { getDynamicPriorityFee } from "./feeOracle.js";
import { broadcastViaJito } from "./jitoSender.js";
import { invalidateBlockhashCache } from "./blockhashCache.js";
import { buildTipInstructions } from "./tipBuilder.js";

export type SwapResponse = { swapTransaction: string; [key: string]: unknown };
export type BroadcastOptions = {
  preferHelius?: boolean;
};

/**
 * Address-lookup-table cache. Jupiter reuses a small set of hot ALTs across thousands of swaps,
 * so warm-cache hit rate is near 100%. Saves 50-150ms per transaction build.
 *
 * 30-minute TTL is a safety net — ALTs can technically be extended, so we re-validate periodically.
 */
const ALT_CACHE_TTL_MS = 30 * 60 * 1000;
const ALT_CACHE_MAX = 64;
const altCache = new Map<string, { account: AddressLookupTableAccount; cachedAt: number }>();

async function fetchAlt(accountKey: PublicKey): Promise<AddressLookupTableAccount> {
  const key = accountKey.toBase58();
  const cached = altCache.get(key);
  const now = Date.now();
  if (cached && now - cached.cachedAt < ALT_CACHE_TTL_MS) {
    // Refresh recency for LRU semantics: re-insert moves the entry to the end.
    altCache.delete(key);
    altCache.set(key, cached);
    return cached.account;
  }
  const resp = await readConnection.getAddressLookupTable(accountKey);
  if (resp.value == null) throw new Error("Address lookup table is null");
  // Evict oldest (first) entry when at capacity.
  if (altCache.size >= ALT_CACHE_MAX) {
    const oldestKey = altCache.keys().next().value;
    if (oldestKey != null) altCache.delete(oldestKey);
  }
  altCache.set(key, { account: resp.value, cachedAt: now });
  return resp.value;
}

function getComputeInstructionType(
  instruction: TransactionInstruction
): string | null {
  if (!instruction.programId.equals(ComputeBudgetProgram.programId)) {
    return null;
  }
  try {
    return ComputeBudgetInstruction.decodeInstructionType(instruction);
  } catch {
    return null;
  }
}

/**
 * Build a Sender-ready transaction from Jupiter's swap response:
 * deserialize, fetch ALTs from readConnection, decompile, normalize compute budget,
 * add tip transfer, recompile, sign.
 */
export async function createSenderTransactionFromSwapResponse(
  swapResponse: SwapResponse,
  keypair: Keypair
): Promise<VersionedTransaction> {
  const transactionBase64 = swapResponse.swapTransaction as string;
  const jupiterTransaction = VersionedTransaction.deserialize(
    Buffer.from(transactionBase64, "base64")
  );

  const altAccounts: AddressLookupTableAccount[] = await Promise.all(
    jupiterTransaction.message.addressTableLookups.map((lookup) => fetchAlt(lookup.accountKey))
  );

  const decompiledMessage = TransactionMessage.decompile(
    jupiterTransaction.message,
    { addressLookupTableAccounts: altAccounts }
  );

  const originalInstructions = decompiledMessage.instructions;
  const nonComputeInstructions = originalInstructions.filter(
    (ix) => !ix.programId.equals(ComputeBudgetProgram.programId)
  );
  const existingLimitIx = originalInstructions.find(
    (ix) => getComputeInstructionType(ix) === "SetComputeUnitLimit"
  );
  const existingHeapIx = originalInstructions.find(
    (ix) => getComputeInstructionType(ix) === "RequestHeapFrame"
  );

  const priorityFeeIx = ComputeBudgetProgram.setComputeUnitPrice({
    microLamports: getDynamicPriorityFee(),
  });
  const tipIxs = buildTipInstructions(keypair.publicKey);

  const mandatoryComputeInstructions: TransactionInstruction[] = [];
  if (computeUnitLimit > 0) {
    mandatoryComputeInstructions.push(
      ComputeBudgetProgram.setComputeUnitLimit({
        units: Math.min(computeUnitLimit, 1_400_000),
      })
    );
  } else if (existingLimitIx) {
    mandatoryComputeInstructions.push(existingLimitIx);
  }
  mandatoryComputeInstructions.push(priorityFeeIx);

  const baseInstructionsWithHeap = existingHeapIx
    ? [...mandatoryComputeInstructions, existingHeapIx, ...nonComputeInstructions]
    : [...mandatoryComputeInstructions, ...nonComputeInstructions];
  const baseInstructionsWithoutHeap = [...mandatoryComputeInstructions, ...nonComputeInstructions];

  const buildWith = (
    baseInstructions: TransactionInstruction[],
    selectedTips: TransactionInstruction[]
  ): VersionedTransaction => {
    decompiledMessage.instructions = [...baseInstructions, ...selectedTips];
    const v0Message = decompiledMessage.compileToV0Message(altAccounts);
    const signed = new VersionedTransaction(v0Message);
    signed.sign([keypair]);
    return signed;
  };

  // Jupiter multi-hop routes (typically post-graduation tokens) can come back near the 1232-byte
  // packet limit. Adding our compute-budget + dual tip (Jito + Helius) ixs on top can blow past
  // it — manifesting either as web3.js "encoding overruns Uint8Array" at compile/sign, or as
  // Helius's "base64 encoded too large" at broadcast. Keep both tips when possible; if not,
  // preserve the tip required by the sender path most likely to rescue this attempt.
  const tryBuild = (
    baseInstructions: TransactionInstruction[],
    selectedTips: TransactionInstruction[]
  ): VersionedTransaction | null => {
    try {
      const tx = buildWith(baseInstructions, selectedTips);
      return tx.serialize().length <= PACKET_DATA_SIZE ? tx : null;
    } catch (err) {
      if (isOversizeError(err)) return null;
      throw err;
    }
  };

  let transaction = tryBuild(baseInstructionsWithHeap, tipIxs);
  if (!transaction && existingHeapIx) {
    // Jupiter's heap frame is useful for some routes, but dual sender tips are more important in the Jito-to-Helius path.
    console.warn("Tx oversize; dropping Jupiter heap frame and retrying with all configured tips.");
    transaction = tryBuild(baseInstructionsWithoutHeap, tipIxs);
  }
  if (!transaction && tipIxs.length > 1) {
    const fallbackTip =
      useJitoDirect && useJitoFallback ? tipIxs[tipIxs.length - 1]! : tipIxs[0]!;
    const fallbackName = useJitoDirect && useJitoFallback ? "Helius" : "primary";
    // Last resort: if Jito may hand off to Helius, keep the Helius-required tip instead of stranding fallback.
    console.warn(`Tx still exceeds ${PACKET_DATA_SIZE}B; rebuilding with ${fallbackName} tip only.`);
    transaction = tryBuild(baseInstructionsWithoutHeap, [fallbackTip]);
  }
  if (!transaction) {
    throw new Error(`Transaction exceeds ${PACKET_DATA_SIZE}B packet limit after size fallbacks`);
  }

  return transaction;
}

const PACKET_DATA_SIZE = 1232;

function isOversizeError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return /encoding overruns|too large|exceeds.*size|packet/i.test(msg);
}

function maybeInvalidateBlockhash(err: unknown): void {
  const msg = err instanceof Error ? err.message : String(err);
  if (/blockhash|expired|block height/i.test(msg)) invalidateBlockhashCache();
}

async function raceJitoAndHelius(transaction: VersionedTransaction): Promise<string> {
  const errors: string[] = [];
  const wrap = async (
    name: string,
    fn: () => Promise<string>
  ): Promise<string> => {
    try {
      return await fn();
    } catch (err) {
      maybeInvalidateBlockhash(err);
      const msg = err instanceof Error ? err.message : String(err);
      errors.push(`${name}: ${msg}`);
      throw err;
    }
  };

  try {
    return await Promise.any([
      wrap("Jito", () => broadcastViaJito(transaction)),
      wrap("Helius", () => broadcastViaHeliusSender(transaction)),
    ]);
  } catch {
    throw new Error(`Parallel broadcast failed: ${errors.join(" | ")}`);
  }
}

/**
 * Send a signed transaction. Routes via Jito BlockEngine directly when USE_JITO_DIRECT=true
 * (saves the Helius → Jito relay hop, ~50-100ms on hot launches), falling back to Helius
 * Sender on Jito error if USE_JITO_FALLBACK=true. Otherwise sends straight to Helius Sender.
 */
export async function broadcastTransactionWithSender(
  transaction: VersionedTransaction,
  options: BroadcastOptions = {}
): Promise<string> {
  const heliusPathPossible = !useJitoDirect || useJitoFallback;
  if (options.preferHelius && heliusPathPossible) {
    // Suspected Jito drops should retry through the alternate ingress when the tx carries a Helius tip.
    console.warn("Broadcast retry: preferring Helius Sender path.");
  }
  if (useJitoDirect && !(options.preferHelius && heliusPathPossible)) {
    if (broadcastRaceEnabled && heliusPathPossible) {
      return raceJitoAndHelius(transaction);
    }
    try {
      return await broadcastViaJito(transaction);
    } catch (jitoErr) {
      if (!useJitoFallback) {
        maybeInvalidateBlockhash(jitoErr);
        throw jitoErr;
      }
      const msg = jitoErr instanceof Error ? jitoErr.message : String(jitoErr);
      maybeInvalidateBlockhash(jitoErr);
      console.warn(`Jito direct broadcast failed; falling back to Helius Sender: ${msg}`);
    }
  }
  try {
    return await broadcastViaHeliusSender(transaction);
  } catch (err) {
    maybeInvalidateBlockhash(err);
    throw err;
  }
}

async function broadcastViaHeliusSender(
  transaction: VersionedTransaction
): Promise<string> {
  const body = JSON.stringify({
    jsonrpc: "2.0",
    id: Date.now().toString(),
    method: "sendTransaction",
    params: [
      Buffer.from(transaction.serialize()).toString("base64"),
      {
        encoding: "base64",
        skipPreflight: true,
        maxRetries: 0,
      },
    ],
  });

  const response = await fetch(senderEndpointUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });

  const json = (await response.json()) as {
    result?: string;
    error?: { message?: string; code?: number };
  };

  if (json.error) {
    const msg = json.error.message ?? "Sender sendTransaction failed";
    const code = json.error.code != null ? ` (code ${json.error.code})` : "";
    throw new Error(`${msg}${code}`);
  }

  if (typeof json.result !== "string") {
    const raw = JSON.stringify(json);
    console.error("Sender raw response:", raw);
    throw new Error(
      `Sender did not return a transaction signature. Response: ${raw}`
    );
  }

  return json.result;
}

/**
 * Optional: ping Sender endpoint to keep connection warm during idle periods.
 * Failures are cosmetic (transient Cloudflare/Helius edge) and don't affect real sends — silenced.
 */
export async function warmConnection(endpoint: string = senderEndpointUrl): Promise<void> {
  const base = endpoint.replace(/\/fast$/, "");
  try {
    await fetch(`${base}/ping`);
  } catch {
    // ignore
  }
}
