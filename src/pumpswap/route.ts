import {
  ComputeBudgetProgram,
  Keypair,
  PublicKey,
  SystemProgram,
  TransactionInstruction,
  TransactionMessage,
  VersionedTransaction,
  type AccountMeta,
  type AccountInfo,
} from "@solana/web3.js";
import bs58 from "bs58";
import {
  confirmationRebroadcastRetries,
  computeUnitLimit,
  fastMode,
  pumpSwapDirectMinOutBps,
  pumpSwapDirectPreflight,
  readConnection,
  simulationMode,
} from "../config.js";
import { buildTipInstructions } from "../tipBuilder.js";
import { getDynamicPriorityFee } from "../feeOracle.js";
import { getCachedBlockhash } from "../blockhashCache.js";
import { broadcastTransactionWithSender } from "../sender.js";
import { confirmSignature, isConfirmationTimeoutError } from "../confirm.js";
import { getTokenMeta, type TokenMeta, WRAPPED_SOL_MINT } from "../tokenRegistry.js";
import {
  ASSOCIATED_TOKEN_PROGRAM_ID,
  SYSTEM_PROGRAM_ID,
  TOKEN_PROGRAM_ID,
  deriveAta,
} from "../pumpfun/constants.js";

export const PUMPSWAP_PROGRAM_ID = new PublicKey("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA");
export const PUMPSWAP_BUY_DISCRIMINATOR = Buffer.from([102, 6, 61, 18, 1, 218, 235, 234]);
export const PUMPSWAP_BUY_EXACT_QUOTE_IN_DISCRIMINATOR = Buffer.from([198, 46, 21, 82, 180, 217, 232, 112]);
const SYNC_NATIVE_IX = Buffer.from([17]);
const CLOSE_ACCOUNT_IX = Buffer.from([9]);
const PUMPSWAP_USER_INDEX = 1;
const PUMPSWAP_BASE_MINT_INDEX = 3;
const PUMPSWAP_QUOTE_MINT_INDEX = 4;
const PUMPSWAP_USER_BASE_ATA_INDEX = 5;
const PUMPSWAP_USER_QUOTE_ATA_INDEX = 6;
const PUMPSWAP_USER_VOLUME_ACCUMULATOR_INDEX = 20;
const PUMPSWAP_CANONICAL_WRITABLE_INDEXES = new Set([0, 1, 5, 6, 7, 8, 10, 17, 20]);

export type PumpSwapBuildContext = {
  mint: string;
  pool: string;
  user: string;
  userBaseAta: string;
  userQuoteAta: string;
  sourceSignature: string;
  sourceSolInLamports: number;
  sourceTokensOutRaw: number | null;
  solInLamports: number;
  expectedBaseOut: bigint;
  minBaseOut: bigint;
  slippageBps: number;
  accountCount: number;
  baseTokenProgram: string;
};

export type PumpSwapOutcome =
  | {
      ok: true;
      signature: string;
      isSimulated: boolean;
      baseOut: bigint;
      quoteIn: bigint;
      mintMeta: TokenMeta;
      confirmationPending?: boolean;
    }
  | { ok: false; reason: string; recoverable: boolean };

function encodeBuy(baseAmountOut: bigint, maxQuoteAmountIn: bigint): Buffer {
  const buf = Buffer.alloc(25);
  PUMPSWAP_BUY_DISCRIMINATOR.copy(buf, 0);
  buf.writeBigUInt64LE(baseAmountOut, 8);
  buf.writeBigUInt64LE(maxQuoteAmountIn, 16);
  buf.writeUInt8(0, 24); // OptionBool::None for volume tracking.
  return buf;
}

export function encodeBuyExactQuoteIn(quoteAmountIn: bigint, minBaseAmountOut: bigint): Buffer {
  const buf = Buffer.alloc(25);
  PUMPSWAP_BUY_EXACT_QUOTE_IN_DISCRIMINATOR.copy(buf, 0);
  buf.writeBigUInt64LE(quoteAmountIn, 8);
  buf.writeBigUInt64LE(minBaseAmountOut, 16);
  buf.writeUInt8(0, 24); // OptionBool::None for volume tracking.
  return buf;
}

export function derivePumpSwapUserVolumeAccumulatorPda(user: PublicKey): PublicKey {
  const [pda] = PublicKey.findProgramAddressSync(
    [Buffer.from("user_volume_accumulator"), user.toBuffer()],
    PUMPSWAP_PROGRAM_ID
  );
  return pda;
}

function formatSol(lamports: number): string {
  return `${(lamports / 1e9).toFixed(4)} SOL`;
}

function formatContext(ctx: PumpSwapBuildContext): string {
  return [
    `mint=${ctx.mint}`,
    `pool=${ctx.pool}`,
    `user=${ctx.user}`,
    `spend=${formatSol(ctx.solInLamports)}`,
    `sourceSpend=${formatSol(ctx.sourceSolInLamports)}`,
    `expectedOut=${ctx.expectedBaseOut}`,
    `minOut=${ctx.minBaseOut}`,
    `slippage=${(ctx.slippageBps / 100).toFixed(2)}%`,
    `accounts=${ctx.accountCount}`,
    `baseTokenProgram=${ctx.baseTokenProgram}`,
    `sourceTx=${ctx.sourceSignature}`,
  ].join(" ");
}

export function isRecoverablePumpSwapFailure(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  const lower = msg.toLowerCase();
  return (
    msg.includes("InstructionError") ||
    msg.includes("Custom") ||
    msg.includes("AnchorError") ||
    msg.includes("ConstraintSeeds") ||
    lower.includes("slippage") ||
    lower.includes("insufficient liquidity") ||
    lower.includes("liquidity") ||
    lower.includes("blockhash not found") ||
    lower.includes("transaction expired") ||
    lower.includes("block height exceeded") ||
    lower.includes("sendtransaction") ||
    lower.includes("sender") ||
    lower.includes("timeout") ||
    lower.includes("fetch failed") ||
    lower.includes("network")
  );
}

export function validatePumpSwapPoolAccount(
  pool: PublicKey,
  account: AccountInfo<Buffer> | null
): string | null {
  if (!account) return `PumpSwap pool not found (${pool.toBase58()})`;
  if (!account.owner.equals(PUMPSWAP_PROGRAM_ID)) {
    return `PumpSwap pool owner mismatch (${pool.toBase58()} owner=${account.owner.toBase58()})`;
  }
  if (account.data.length === 0) return `PumpSwap pool is empty/uninitialized (${pool.toBase58()})`;
  return null;
}

async function getTransactionFailureLogs(signature: string): Promise<string[]> {
  try {
    const tx = await readConnection.getTransaction(signature, {
      commitment: "confirmed",
      maxSupportedTransactionVersion: 0,
    });
    return tx?.meta?.logMessages ?? [];
  } catch {
    return [];
  }
}

function summarizeLogs(logs: string[], maxLines = 12): string {
  return logs
    .filter((line) =>
      /AnchorError|Error Code|Error Number|Error Message|custom program error|Program log|failed/i.test(line)
    )
    .slice(-maxLines)
    .join(" | ");
}

function createAtaIdempotentIx(args: {
  payer: PublicKey;
  owner: PublicKey;
  mint: PublicKey;
  tokenProgramId: PublicKey;
}): TransactionInstruction {
  const ata = deriveAta(args.owner, args.mint, args.tokenProgramId);
  return new TransactionInstruction({
    programId: ASSOCIATED_TOKEN_PROGRAM_ID,
    keys: [
      { pubkey: args.payer, isSigner: true, isWritable: true },
      { pubkey: ata, isSigner: false, isWritable: true },
      { pubkey: args.owner, isSigner: false, isWritable: false },
      { pubkey: args.mint, isSigner: false, isWritable: false },
      { pubkey: SYSTEM_PROGRAM_ID, isSigner: false, isWritable: false },
      { pubkey: args.tokenProgramId, isSigner: false, isWritable: false },
    ],
    data: Buffer.from([1]),
  });
}

function syncNativeIx(account: PublicKey): TransactionInstruction {
  return new TransactionInstruction({
    programId: TOKEN_PROGRAM_ID,
    keys: [{ pubkey: account, isSigner: false, isWritable: true }],
    data: SYNC_NATIVE_IX,
  });
}

function closeTokenAccountIx(account: PublicKey, owner: PublicKey): TransactionInstruction {
  return new TransactionInstruction({
    programId: TOKEN_PROGRAM_ID,
    keys: [
      { pubkey: account, isSigner: false, isWritable: true },
      { pubkey: owner, isSigner: false, isWritable: true },
      { pubkey: owner, isSigner: true, isWritable: false },
    ],
    data: CLOSE_ACCOUNT_IX,
  });
}

function pumpSwapMetaFromSourceKey(pubkey: PublicKey, idx: number): AccountMeta {
  return {
    pubkey,
    isSigner: idx === PUMPSWAP_USER_INDEX,
    isWritable: PUMPSWAP_CANONICAL_WRITABLE_INDEXES.has(idx),
  };
}

function compiledInstructionDataToBuffer(data: unknown): Buffer {
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

async function sourcePumpSwapAccounts(sourceSignature: string, mint: PublicKey): Promise<AccountMeta[] | null> {
  const tx = await readConnection.getTransaction(sourceSignature, {
    commitment: "confirmed",
    maxSupportedTransactionVersion: 0,
  });
  if (!tx) return null;

  const message = tx.transaction.message;
  const staticKeys = message.staticAccountKeys;
  const loaded = tx.meta?.loadedAddresses
    ? [...tx.meta.loadedAddresses.writable, ...tx.meta.loadedAddresses.readonly]
    : [];
  const allKeys = [...staticKeys, ...loaded];

  const toAccountMetas = (indexes: number[]): AccountMeta[] =>
    indexes.map((idx) => ({
      pubkey: allKeys[idx]!,
      isSigner: message.isAccountSigner(idx),
      isWritable: message.isAccountWritable(idx),
    }));

  const matchesPumpSwapBuy = (ix: {
    programIdIndex: number;
    accountKeyIndexes?: number[];
    accounts?: number[] | Uint8Array;
    data?: unknown;
  }): number[] | null => {
    if (!allKeys[ix.programIdIndex]?.equals(PUMPSWAP_PROGRAM_ID)) return null;
    const data = compiledInstructionDataToBuffer(ix.data);
    const discriminator = data.subarray(0, 8);
    if (
      !discriminator.equals(PUMPSWAP_BUY_DISCRIMINATOR) &&
      !discriminator.equals(PUMPSWAP_BUY_EXACT_QUOTE_IN_DISCRIMINATOR)
    ) {
      return null;
    }
    const indexes = ix.accountKeyIndexes ?? Array.from(ix.accounts ?? []);
    const keys = indexes.map((idx) => allKeys[idx]);
    if (!keys[PUMPSWAP_BASE_MINT_INDEX]?.equals(mint)) return null;
    return indexes;
  };

  for (const ix of message.compiledInstructions) {
    const indexes = matchesPumpSwapBuy(ix);
    if (indexes) return toAccountMetas(indexes);
  }

  for (const group of tx.meta?.innerInstructions ?? []) {
    for (const ix of group.instructions) {
      const indexes = matchesPumpSwapBuy(ix as unknown as {
        programIdIndex: number;
        accountKeyIndexes?: number[];
        accounts?: number[] | Uint8Array;
        data?: unknown;
      });
      if (indexes) return toAccountMetas(indexes);
    }
  }
  return null;
}

async function sourcePumpSwapAccountsWithShortRetry(
  sourceSignature: string,
  mint: PublicKey
): Promise<AccountMeta[] | null> {
  for (let attempt = 0; attempt < PUMPSWAP_SOURCE_META_ATTEMPTS; attempt++) {
    const accounts = await sourcePumpSwapAccounts(sourceSignature, mint).catch(() => null);
    if (accounts) return accounts;
    if (attempt < PUMPSWAP_SOURCE_META_ATTEMPTS - 1) await sleep(PUMPSWAP_SOURCE_META_RETRY_MS);
  }
  return null;
}

export function replacePumpSwapUserAccounts(args: {
  accounts: AccountMeta[];
  user: PublicKey;
  baseMint: PublicKey;
  baseTokenProgram: PublicKey;
}): { accounts: AccountMeta[]; userBaseAta: PublicKey; userQuoteAta: PublicKey } {
  const accounts = args.accounts.map((a) => ({ ...a }));
  const userBaseAta = deriveAta(args.user, args.baseMint, args.baseTokenProgram);
  const userQuoteAta = deriveAta(args.user, new PublicKey(WRAPPED_SOL_MINT), TOKEN_PROGRAM_ID);

  accounts[PUMPSWAP_USER_INDEX] = { pubkey: args.user, isSigner: true, isWritable: true };
  accounts[PUMPSWAP_USER_BASE_ATA_INDEX] = { pubkey: userBaseAta, isSigner: false, isWritable: true };
  accounts[PUMPSWAP_USER_QUOTE_ATA_INDEX] = { pubkey: userQuoteAta, isSigner: false, isWritable: true };
  if (accounts.length > PUMPSWAP_USER_VOLUME_ACCUMULATOR_INDEX) {
    accounts[PUMPSWAP_USER_VOLUME_ACCUMULATOR_INDEX] = {
      pubkey: derivePumpSwapUserVolumeAccumulatorPda(args.user),
      isSigner: false,
      isWritable: true,
    };
  }

  return { accounts, userBaseAta, userQuoteAta };
}

const POOL_BASE_MINT_OFFSET = 8 + 1 + 2 + 32;
const POOL_QUOTE_MINT_OFFSET = POOL_BASE_MINT_OFFSET + 32;
const POOL_BASE_TOKEN_ACCOUNT_OFFSET = POOL_QUOTE_MINT_OFFSET + 32 + 32;
const POOL_QUOTE_TOKEN_ACCOUNT_OFFSET = POOL_BASE_TOKEN_ACCOUNT_OFFSET + 32;
const POOL_INDEX_OFFSET = 8 + 1;
const TOKEN_ACCOUNT_AMOUNT_OFFSET = 64;
const PUMPSWAP_RISK_QUOTE_HAIRCUT_BPS = 100;
const PUMPSWAP_DIRECT_BUY_QUOTE_HAIRCUT_BPS = 300;
const PUMPSWAP_ACCOUNT_CACHE_TTL_MS = 30_000;
const PUMPSWAP_SOURCE_META_ATTEMPTS = 3;
const PUMPSWAP_SOURCE_META_RETRY_MS = 35;
const pumpSwapPoolCache = new Map<string, { pool: PublicKey; baseVault: PublicKey; quoteVault: PublicKey }>();
const pumpSwapMintOwnerCache = new Map<string, { owner: PublicKey; cachedAt: number }>();
const pumpSwapPoolAccountCache = new Map<string, { account: AccountInfo<Buffer>; cachedAt: number }>();

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function readPubkey(data: Buffer, offset: number): PublicKey {
  return new PublicKey(data.subarray(offset, offset + 32));
}

function readTokenAccountAmount(info: AccountInfo<Buffer> | null): bigint | null {
  if (!info || info.data.length < TOKEN_ACCOUNT_AMOUNT_OFFSET + 8) return null;
  return info.data.readBigUInt64LE(TOKEN_ACCOUNT_AMOUNT_OFFSET);
}

async function getCachedMintOwner(mint: PublicKey): Promise<PublicKey | null> {
  const key = mint.toBase58();
  const cached = pumpSwapMintOwnerCache.get(key);
  const now = Date.now();
  if (cached && now - cached.cachedAt < PUMPSWAP_ACCOUNT_CACHE_TTL_MS) return cached.owner;

  for (const attempt of [
    { commitment: "processed" as const, delayMs: 0 },
    { commitment: "confirmed" as const, delayMs: 0 },
    { commitment: "processed" as const, delayMs: 40 },
    { commitment: "processed" as const, delayMs: 80 },
  ]) {
    if (attempt.delayMs > 0) await sleep(attempt.delayMs);
    const account = await readConnection.getAccountInfo(mint, attempt.commitment);
    if (!account) continue;
    pumpSwapMintOwnerCache.set(key, { owner: account.owner, cachedAt: Date.now() });
    return account.owner;
  }

  return null;
}

async function getCachedPumpSwapPoolAccount(pool: PublicKey): Promise<AccountInfo<Buffer> | null> {
  const key = pool.toBase58();
  const cached = pumpSwapPoolAccountCache.get(key);
  const now = Date.now();
  if (cached && now - cached.cachedAt < PUMPSWAP_ACCOUNT_CACHE_TTL_MS) return cached.account;

  const account = await readConnection.getAccountInfo(pool, "processed");
  if (account) pumpSwapPoolAccountCache.set(key, { account, cachedAt: now });
  return account;
}

async function findPumpSwapPoolForMint(mint: PublicKey): Promise<{
  pool: PublicKey;
  baseVault: PublicKey;
  quoteVault: PublicKey;
} | null> {
  const cached = pumpSwapPoolCache.get(mint.toBase58());
  if (cached) return cached;

  const accounts = await readConnection.getProgramAccounts(PUMPSWAP_PROGRAM_ID, {
    commitment: "confirmed",
    filters: [
      { memcmp: { offset: POOL_BASE_MINT_OFFSET, bytes: mint.toBase58() } },
      { memcmp: { offset: POOL_QUOTE_MINT_OFFSET, bytes: WRAPPED_SOL_MINT } },
    ],
  });
  if (accounts.length === 0) return null;

  const usable = accounts.filter((a) => a.account.data.length >= POOL_QUOTE_TOKEN_ACCOUNT_OFFSET + 32);
  if (usable.length === 0) return null;

  const canonical =
    usable.find((a) => a.account.data.readUInt16LE(POOL_INDEX_OFFSET) === 0) ?? usable[0]!;
  const data = canonical.account.data;

  const pool = {
    pool: canonical.pubkey,
    baseVault: readPubkey(data, POOL_BASE_TOKEN_ACCOUNT_OFFSET),
    quoteVault: readPubkey(data, POOL_QUOTE_TOKEN_ACCOUNT_OFFSET),
  };
  pumpSwapPoolCache.set(mint.toBase58(), pool);
  return pool;
}

function readPoolVaultsFromAccount(
  pool: PublicKey,
  account: AccountInfo<Buffer> | null
): { pool: PublicKey; baseVault: PublicKey; quoteVault: PublicKey } | null {
  if (!account || account.data.length < POOL_QUOTE_TOKEN_ACCOUNT_OFFSET + 32) return null;
  return {
    pool,
    baseVault: readPubkey(account.data, POOL_BASE_TOKEN_ACCOUNT_OFFSET),
    quoteVault: readPubkey(account.data, POOL_QUOTE_TOKEN_ACCOUNT_OFFSET),
  };
}

async function estimatePumpSwapBuyQuoteFromPool(args: {
  pool: PublicKey;
  poolAccount: AccountInfo<Buffer> | null;
  quoteInRaw: number;
}): Promise<bigint | null> {
  if (!Number.isFinite(args.quoteInRaw) || args.quoteInRaw <= 0) return null;
  const vaults = readPoolVaultsFromAccount(args.pool, args.poolAccount);
  if (!vaults) return null;

  const [baseInfo, quoteInfo] = await readConnection.getMultipleAccountsInfo(
    [vaults.baseVault, vaults.quoteVault],
    "processed"
  );
  const baseReserve = readTokenAccountAmount(baseInfo);
  const quoteReserve = readTokenAccountAmount(quoteInfo);
  if (!baseReserve || !quoteReserve || baseReserve <= 0n || quoteReserve <= 0n) return null;

  const quoteIn = BigInt(Math.floor(args.quoteInRaw));
  if (quoteIn <= 0n) return null;

  const rawOut = (baseReserve * quoteIn) / (quoteReserve + quoteIn);
  const conservativeOut =
    (rawOut * BigInt(10_000 - PUMPSWAP_DIRECT_BUY_QUOTE_HAIRCUT_BPS)) / 10_000n;
  return conservativeOut > 0n ? conservativeOut : null;
}

export async function estimatePumpSwapSellQuote(
  mint: string,
  baseAmountRaw: number
): Promise<number | null> {
  if (!Number.isFinite(baseAmountRaw) || baseAmountRaw <= 0) return null;
  const pool = await findPumpSwapPoolForMint(new PublicKey(mint));
  if (!pool) return null;

  const [baseInfo, quoteInfo] = await readConnection.getMultipleAccountsInfo(
    [pool.baseVault, pool.quoteVault],
    "confirmed"
  );
  const baseReserve = readTokenAccountAmount(baseInfo);
  const quoteReserve = readTokenAccountAmount(quoteInfo);
  if (!baseReserve || !quoteReserve || baseReserve <= 0n || quoteReserve <= 0n) return null;

  const amountIn = BigInt(Math.floor(baseAmountRaw));
  if (amountIn <= 0n) return null;
  const rawOut = (quoteReserve * amountIn) / (baseReserve + amountIn);
  // Risk checks need a usable valuation during Jupiter indexing lag; haircut avoids optimistic fee-free exits.
  const conservativeOut = (rawOut * BigInt(10_000 - PUMPSWAP_RISK_QUOTE_HAIRCUT_BPS)) / 10_000n;
  if (conservativeOut <= 0n || conservativeOut > BigInt(Number.MAX_SAFE_INTEGER)) return null;
  return Number(conservativeOut);
}

export async function tryPumpSwapDirectBuy(args: {
  user: Keypair;
  mint: string;
  sourceSignature: string;
  sourceSolInLamports: number;
  sourceTokensOutRaw?: number;
  sourcePumpSwapAccounts?: string[];
  solInLamports: number;
  slippageBps: number;
  startedAt: number;
  log: (msg: string) => void;
  rebroadcastAttempt?: number;
  onConfirmFailure?: (mint: string, tokensOutRaw: number, solLamports: number, reason: string) => void;
  onConfirmSuccess?: (mint: string, tokensOutRaw: number, solLamports: number, signature: string) => void;
}): Promise<PumpSwapOutcome> {
  if (args.sourceSolInLamports <= 0) {
    return { ok: false, recoverable: true, reason: "source PumpSwap spend unavailable" };
  }

  const elapsed = () => `${Date.now() - args.startedAt}ms`;
  const mintKey = new PublicKey(args.mint);
  const baseTokenProgramId = await getCachedMintOwner(mintKey);
  if (!baseTokenProgramId) {
    return { ok: false, recoverable: true, reason: "PumpSwap base mint account not found" };
  }
  const exactSourceAccounts = await sourcePumpSwapAccountsWithShortRetry(args.sourceSignature, mintKey);
  const sourceAccounts = exactSourceAccounts ??
    (args.sourcePumpSwapAccounts?.length
      ? args.sourcePumpSwapAccounts.map((pubkey, idx) =>
          pumpSwapMetaFromSourceKey(new PublicKey(pubkey), idx)
        )
      : null);
  if (!sourceAccounts) {
    return { ok: false, recoverable: true, reason: "source tx has no PumpSwap buy instruction" };
  }
  if (
    !sourceAccounts[PUMPSWAP_BASE_MINT_INDEX]?.pubkey.equals(mintKey) ||
    sourceAccounts[PUMPSWAP_QUOTE_MINT_INDEX]?.pubkey.toBase58() !== WRAPPED_SOL_MINT
  ) {
    return { ok: false, recoverable: true, reason: "source PumpSwap accounts do not match SOL->mint buy layout" };
  }

  const { accounts, userBaseAta, userQuoteAta } = replacePumpSwapUserAccounts({
    accounts: sourceAccounts,
    user: args.user.publicKey,
    baseMint: mintKey,
    baseTokenProgram: baseTokenProgramId,
  });

  if (accounts.length < 17) {
    return { ok: false, recoverable: true, reason: `PumpSwap account list too short: ${accounts.length}` };
  }

  const poolAccount = await getCachedPumpSwapPoolAccount(accounts[0]!.pubkey);
  const poolError = validatePumpSwapPoolAccount(accounts[0]!.pubkey, poolAccount);
  if (poolError) return { ok: false, recoverable: true, reason: poolError };

  const sourceRatioOut =
    args.sourceTokensOutRaw && args.sourceTokensOutRaw > 0
      ? (BigInt(Math.floor(args.sourceTokensOutRaw)) * BigInt(args.solInLamports)) /
        BigInt(Math.floor(args.sourceSolInLamports))
      : null;
  const livePoolOut = await estimatePumpSwapBuyQuoteFromPool({
    pool: accounts[0]!.pubkey,
    poolAccount,
    quoteInRaw: args.solInLamports,
  });
  const expectedBaseOut = livePoolOut ?? sourceRatioOut;
  if (expectedBaseOut == null || expectedBaseOut <= 0n) {
    return { ok: false, recoverable: true, reason: "PumpSwap output quote unavailable" };
  }
  const minOutBps =
    pumpSwapDirectMinOutBps > 0
      ? pumpSwapDirectMinOutBps
      : Math.max(1, 10_000 - Math.min(9_999, Math.max(0, args.slippageBps)));
  const minBaseOut = (expectedBaseOut * BigInt(minOutBps)) / 10_000n;
  if (minBaseOut <= 0n) {
    return { ok: false, recoverable: true, reason: "PumpSwap min output is zero" };
  }

  const ctx: PumpSwapBuildContext = {
    mint: args.mint,
    pool: accounts[0]?.pubkey.toBase58() ?? "unknown",
    user: args.user.publicKey.toBase58(),
    userBaseAta: userBaseAta.toBase58(),
    userQuoteAta: userQuoteAta.toBase58(),
    sourceSignature: args.sourceSignature,
    sourceSolInLamports: args.sourceSolInLamports,
    sourceTokensOutRaw:
      args.sourceTokensOutRaw && args.sourceTokensOutRaw > 0
        ? Math.floor(args.sourceTokensOutRaw)
        : null,
    solInLamports: args.solInLamports,
    expectedBaseOut,
    minBaseOut,
    slippageBps: args.slippageBps,
    accountCount: accounts.length,
    baseTokenProgram: baseTokenProgramId.toBase58(),
  };

  args.log(`PumpSwap direct buy: ${formatContext(ctx)}`);

  if (simulationMode) {
    const mintMeta = await getTokenMeta(args.mint);
    args.log(`PumpSwap simulation mode: not sending tx (${formatContext(ctx)})`);
    return {
      ok: true,
      signature: `sim-pumpswap-${args.mint.slice(0, 8)}-${Date.now()}`,
      isSimulated: true,
      baseOut: expectedBaseOut,
      quoteIn: BigInt(args.solInLamports),
      mintMeta,
    };
  }

  const computeIxs: TransactionInstruction[] = [];
  if (computeUnitLimit > 0) {
    computeIxs.push(
      ComputeBudgetProgram.setComputeUnitLimit({
        units: Math.min(computeUnitLimit, 1_400_000),
      })
    );
  }
  computeIxs.push(ComputeBudgetProgram.setComputeUnitPrice({ microLamports: getDynamicPriorityFee() }));

  const tipIxs = buildTipInstructions(args.user.publicKey);

  const swapIx = new TransactionInstruction({
    programId: PUMPSWAP_PROGRAM_ID,
    keys: accounts,
    data: encodeBuyExactQuoteIn(BigInt(args.solInLamports), minBaseOut),
  });

  const { blockhash, lastValidBlockHeight } = await getCachedBlockhash();
  args.log(`PumpSwap blockhash ready: lastValidBlockHeight=${lastValidBlockHeight}`);
  const message = new TransactionMessage({
    payerKey: args.user.publicKey,
    recentBlockhash: blockhash,
    instructions: [
      ...computeIxs,
      createAtaIdempotentIx({
        payer: args.user.publicKey,
        owner: args.user.publicKey,
        mint: mintKey,
        tokenProgramId: baseTokenProgramId,
      }),
      createAtaIdempotentIx({
        payer: args.user.publicKey,
        owner: args.user.publicKey,
        mint: new PublicKey(WRAPPED_SOL_MINT),
        tokenProgramId: TOKEN_PROGRAM_ID,
      }),
      SystemProgram.transfer({
        fromPubkey: args.user.publicKey,
        toPubkey: userQuoteAta,
        lamports: args.solInLamports,
      }),
      syncNativeIx(userQuoteAta),
      swapIx,
      closeTokenAccountIx(userQuoteAta, args.user.publicKey),
      ...tipIxs,
    ],
  }).compileToV0Message();
  const tx = new VersionedTransaction(message);
  tx.sign([args.user]);

  if (args.log && false) args.log(userBaseAta.toBase58());
  args.log(`PumpSwap tx built (${elapsed()})`);
  if (pumpSwapDirectPreflight) {
    const sim = await readConnection.simulateTransaction(tx, {
      commitment: "processed",
      sigVerify: false,
      replaceRecentBlockhash: true,
    });
    if (sim.value.err) {
      const logSummary = summarizeLogs(sim.value.logs ?? []);
      return {
        ok: false,
        recoverable: true,
        reason: [
          `PumpSwap preflight failed: ${JSON.stringify(sim.value.err)}`,
          formatContext(ctx),
          logSummary ? `logs=${logSummary}` : "",
        ].filter(Boolean).join(" | "),
      };
    }
    args.log(`PumpSwap preflight ok (${elapsed()})`);
  }
  let signature: string | null = null;
  try {
    signature = await broadcastTransactionWithSender(tx, {
      preferHelius: (args.rebroadcastAttempt ?? 0) > 0,
    });
    args.log(`PumpSwap broadcast: ${signature} (${elapsed()})`);
    if (fastMode) {
      // PumpSwap is a hot post-graduation entry path; do not block recording on confirmation.
      confirmSignature(signature)
        .then(() => {
          args.onConfirmSuccess?.(args.mint, Number(expectedBaseOut), args.solInLamports, signature!);
        })
        .catch((err) => {
          const msg = err instanceof Error ? err.message : String(err);
          console.warn(`PumpSwap confirmation failed after broadcast: ${msg}`);
          args.onConfirmFailure?.(args.mint, Number(expectedBaseOut), args.solInLamports, msg);
        });
    } else {
      await confirmSignature(signature);
      args.log(`PumpSwap confirmed (${elapsed()})`);
    }
  } catch (err) {
    const rebroadcastAttempt = args.rebroadcastAttempt ?? 0;
    if (isConfirmationTimeoutError(err) && rebroadcastAttempt < confirmationRebroadcastRetries) {
      const msg = err instanceof Error ? err.message : String(err);
      // PumpSwap direct bypasses Jupiter's retry ladder, so rebuild here on suspected bundle drops.
      args.log(`PumpSwap buy suspected dropped (${msg}); rebuilding once with fresh blockhash...`);
      return tryPumpSwapDirectBuy({
        ...args,
        rebroadcastAttempt: rebroadcastAttempt + 1,
      });
    }
    const msg = err instanceof Error ? err.message : String(err);
    const logs = signature ? await getTransactionFailureLogs(signature) : [];
    const logSummary = summarizeLogs(logs);
    const reason = [
      `PumpSwap send/confirm failed: ${msg}`,
      signature ? `signature=${signature}` : "",
      formatContext(ctx),
      logSummary ? `logs=${logSummary}` : "",
    ]
      .filter(Boolean)
      .join(" | ");
    return { ok: false, recoverable: isRecoverablePumpSwapFailure(err), reason };
  }
  const mintMeta = await getTokenMeta(args.mint);
  return {
    ok: true,
    signature,
    isSimulated: false,
    baseOut: expectedBaseOut,
    quoteIn: BigInt(args.solInLamports),
    mintMeta,
    confirmationPending: fastMode,
  };
}
