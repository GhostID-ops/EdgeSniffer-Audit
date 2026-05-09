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
  TIP_ACCOUNTS,
  tipLamports,
  computeUnitLimit,
} from "./config.js";
import { getDynamicPriorityFee } from "./feeOracle.js";

export type SwapResponse = { swapTransaction: string; [key: string]: unknown };

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

  const tipAccount =
    TIP_ACCOUNTS[Math.floor(Math.random() * TIP_ACCOUNTS.length)]!;
  const priorityFeeIx = ComputeBudgetProgram.setComputeUnitPrice({
    microLamports: getDynamicPriorityFee(),
  });
  const tipIx = SystemProgram.transfer({
    fromPubkey: keypair.publicKey,
    toPubkey: new PublicKey(tipAccount),
    lamports: tipLamports,
  });

  const computeInstructions: TransactionInstruction[] = [];
  if (computeUnitLimit > 0) {
    computeInstructions.push(
      ComputeBudgetProgram.setComputeUnitLimit({
        units: Math.min(computeUnitLimit, 1_400_000),
      })
    );
  } else if (existingLimitIx) {
    computeInstructions.push(existingLimitIx);
  }
  computeInstructions.push(priorityFeeIx);
  if (existingHeapIx) {
    computeInstructions.push(existingHeapIx);
  }

  decompiledMessage.instructions = [
    ...computeInstructions,
    ...nonComputeInstructions,
    tipIx,
  ];

  const v0Message = decompiledMessage.compileToV0Message(altAccounts);
  const transaction = new VersionedTransaction(v0Message);
  transaction.sign([keypair]);

  return transaction;
}

/**
 * Send a serialized transaction to Helius Sender.
 * Uses skipPreflight: true and maxRetries: 0 as required.
 */
export async function broadcastTransactionWithSender(
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
