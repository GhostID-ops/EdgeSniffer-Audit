import {
  ComputeBudgetProgram,
  Keypair,
  PublicKey,
  SystemProgram,
  TransactionInstruction,
  type AccountMeta,
  type AccountInfo,
} from "@solana/web3.js";
import bs58 from "bs58";
import {
  confirmationRebroadcastRetries,
  computeUnitLimit,
  fastMode,
  keepWsolAtaOpen,
  pumpSwapDirectMinOutBps,
  pumpSwapDirectPreflight,
  readConnection,
  rustOnlyBuys,
  rustOnlySells,
  simulationMode,
  warmWalletMode,
} from "../config.js";
import { getDynamicPriorityFee } from "../feeOracle.js";
import { getCachedBlockhash } from "../blockhashCache.js";
import { broadcastTransactionWithSender } from "../sender.js";
import { buildSizeSafeV0Transaction } from "../transactionSize.js";
import { confirmSignature, isConfirmationTimeoutError } from "../confirm.js";
import { getTokenMeta, type TokenMeta, WRAPPED_SOL_MINT } from "../tokenRegistry.js";
import { accountExistsCached, knownAccountExists } from "../walletWarmup.js";
import {
  assertNativeIdlInstruction,
  buildNativeIdlInstructionFromOrderedAccounts,
  getNativeIdlDiscriminator,
} from "../idlNativeBuilder.js";
import { recordExecutionStage } from "../executionTelemetry.js";
import { classifyExecutionError } from "../errorClassifier.js";
import { executeRustPumpSwapBuy, executeRustPumpSwapSell, releaseRustBoughtMint } from "../rustSidecar.js";
import type { SourcePumpSwapAccountMeta } from "../types.js";
import {
  ASSOCIATED_TOKEN_PROGRAM_ID,
  PUMP_FUN_FEE_PROGRAM_ID,
  SYSTEM_PROGRAM_ID,
  TOKEN_PROGRAM_ID,
  deriveAta,
} from "../pumpfun/constants.js";

export const PUMPSWAP_PROGRAM_ID = new PublicKey("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA");
export const PUMPSWAP_GLOBAL_CONFIG = new PublicKey("ADyA8hBd1ckwkgdC5fC4bKBS32sNeuHuJecF8GWqV7Zb");
export const PUMPSWAP_BUY_DISCRIMINATOR = getNativeIdlDiscriminator("pump_amm", "buy");
export const PUMPSWAP_BUY_EXACT_QUOTE_IN_DISCRIMINATOR = getNativeIdlDiscriminator("pump_amm", "buy_exact_quote_in");
export const PUMPSWAP_SELL_DISCRIMINATOR = getNativeIdlDiscriminator("pump_amm", "sell");
const SYNC_NATIVE_IX = Buffer.from([17]);
const CLOSE_ACCOUNT_IX = Buffer.from([9]);
const PUMPSWAP_USER_INDEX = 1;
const PUMPSWAP_BASE_MINT_INDEX = 3;
const PUMPSWAP_QUOTE_MINT_INDEX = 4;
const PUMPSWAP_USER_BASE_ATA_INDEX = 5;
const PUMPSWAP_USER_QUOTE_ATA_INDEX = 6;
const PUMPSWAP_USER_VOLUME_ACCUMULATOR_INDEX = 20;
const PUMPSWAP_MIN_BUY_ACCOUNT_COUNT = 23;
const PUMPSWAP_MIN_SELL_ACCOUNT_COUNT = 21;
const PUMPSWAP_CANONICAL_WRITABLE_INDEXES = new Set([0, 1, 5, 6, 7, 8, 10, 17, 20, 23]);

type PumpSwapAccountPlan = {
  accounts: AccountMeta[];
  source: "confirmed-source-metas" | "detector-pubkey-hints" | "canonical-pool-state";
  diagnostics?: Record<string, unknown>;
};

type PumpSwapBuyInstruction = "buy" | "buy_exact_quote_in";

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
  buyInstruction: PumpSwapBuyInstruction;
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

export type PumpSwapSellOutcome =
  | {
      ok: true;
      signature: string;
      isSimulated: boolean;
      baseIn: bigint;
      quoteOut: bigint;
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

export function encodeSell(baseAmountIn: bigint, minQuoteAmountOut: bigint): Buffer {
  const buf = Buffer.alloc(24);
  PUMPSWAP_SELL_DISCRIMINATOR.copy(buf, 0);
  buf.writeBigUInt64LE(baseAmountIn, 8);
  buf.writeBigUInt64LE(minQuoteAmountOut, 16);
  return buf;
}

export function derivePumpSwapUserVolumeAccumulatorPda(user: PublicKey): PublicKey {
  const [pda] = PublicKey.findProgramAddressSync(
    [Buffer.from("user_volume_accumulator"), user.toBuffer()],
    PUMPSWAP_PROGRAM_ID
  );
  return pda;
}

export function derivePumpSwapEventAuthorityPda(): PublicKey {
  const [pda] = PublicKey.findProgramAddressSync(
    [Buffer.from("__event_authority")],
    PUMPSWAP_PROGRAM_ID
  );
  return pda;
}

export function derivePumpSwapGlobalVolumeAccumulatorPda(): PublicKey {
  const [pda] = PublicKey.findProgramAddressSync(
    [Buffer.from("global_volume_accumulator")],
    PUMPSWAP_PROGRAM_ID
  );
  return pda;
}

export function derivePumpSwapCoinCreatorVaultAuthorityPda(coinCreator: PublicKey): PublicKey {
  const [pda] = PublicKey.findProgramAddressSync(
    [Buffer.from("creator_vault"), coinCreator.toBuffer()],
    PUMPSWAP_PROGRAM_ID
  );
  return pda;
}

export function derivePumpSwapFeeConfigPda(): PublicKey {
  const [pda] = PublicKey.findProgramAddressSync(
    [Buffer.from("fee_config"), PUMPSWAP_PROGRAM_ID.toBuffer()],
    PUMP_FUN_FEE_PROGRAM_ID
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
    `ix=${ctx.buyInstruction}`,
    `expectedOut=${ctx.expectedBaseOut}`,
    `minOut=${ctx.minBaseOut}`,
    `slippage=${(ctx.slippageBps / 100).toFixed(2)}%`,
    `accounts=${ctx.accountCount}`,
    `baseTokenProgram=${ctx.baseTokenProgram}`,
    `sourceTx=${ctx.sourceSignature}`,
  ].join(" ");
}

function recordPumpSwapConfirmStage(args: {
  route: "pumpswap-direct-buy" | "pumpswap-direct-sell";
  side: "buy" | "sell";
  mint: string;
  sourceSignature?: string;
  signature?: string;
  startedAt: number;
  success: boolean;
  error?: string;
  details?: Record<string, unknown>;
}): void {
  recordExecutionStage({
    stage: "confirm",
    route: args.route,
    side: args.side,
    mint: args.mint,
    sourceSignature: args.sourceSignature,
    signature: args.signature,
    elapsedMs: Date.now() - args.startedAt,
    success: args.success,
    error: args.error,
    details: args.details,
  });
}

export function isRecoverablePumpSwapFailure(err: unknown): boolean {
  const classified = classifyExecutionError(err);
  if (
    classified.category === "liquidity-overflow" ||
    classified.category === "liquidity-no-liquidity" ||
    classified.category === "not-tradable" ||
    classified.category === "no-route" ||
    classified.category === "insufficient-funds"
  ) {
    return false;
  }
  if (classified.category !== "unknown" && classified.recoverable) return true;

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

export function pumpSwapPubkeyHintsToMetas(pubkeys: PublicKey[]): AccountMeta[] {
  return pubkeys.map((pubkey, idx) => pumpSwapMetaFromSourceKey(pubkey, idx));
}

function isSourcePumpSwapAccountMeta(value: unknown): value is SourcePumpSwapAccountMeta {
  if (value == null || typeof value !== "object") return false;
  const maybe = value as Partial<SourcePumpSwapAccountMeta>;
  return (
    typeof maybe.pubkey === "string" &&
    typeof maybe.isSigner === "boolean" &&
    typeof maybe.isWritable === "boolean"
  );
}

export function sourcePumpSwapHintAccountsToMetas(
  sourcePumpSwapAccounts: SourcePumpSwapAccountMeta[] | string[] | undefined
): AccountMeta[] | null {
  if (!sourcePumpSwapAccounts?.length) return null;

  if (sourcePumpSwapAccounts.every(isSourcePumpSwapAccountMeta)) {
    const accounts: AccountMeta[] = [];
    for (const account of sourcePumpSwapAccounts) {
      try {
        accounts.push({
          pubkey: new PublicKey(account.pubkey),
          isSigner: account.isSigner,
          isWritable: account.isWritable,
        });
      } catch {
        return null;
      }
    }
    return accounts;
  }

  const pubkeys: PublicKey[] = [];
  for (const value of sourcePumpSwapAccounts) {
    if (typeof value !== "string") return null;
    try {
      pubkeys.push(new PublicKey(value));
    } catch {
      return null;
    }
  }
  return pubkeys.length ? pumpSwapPubkeyHintsToMetas(pubkeys) : null;
}

function validatePumpSwapBuyAccountLayout(accounts: AccountMeta[], mint: PublicKey): string | null {
  if (accounts.length < PUMPSWAP_MIN_BUY_ACCOUNT_COUNT) {
    return `PumpSwap account list too short: ${accounts.length}`;
  }
  if (!accounts[PUMPSWAP_BASE_MINT_INDEX]?.pubkey.equals(mint)) {
    return "source PumpSwap accounts do not match target base mint";
  }
  if (accounts[PUMPSWAP_QUOTE_MINT_INDEX]?.pubkey.toBase58() !== WRAPPED_SOL_MINT) {
    return "source PumpSwap accounts do not match SOL quote mint";
  }
  return null;
}

function validatePumpSwapSellAccountLayout(accounts: AccountMeta[], mint: PublicKey): string | null {
  if (accounts.length < PUMPSWAP_MIN_SELL_ACCOUNT_COUNT) {
    return `PumpSwap sell account list too short: ${accounts.length}`;
  }
  if (!accounts[PUMPSWAP_BASE_MINT_INDEX]?.pubkey.equals(mint)) {
    return "source PumpSwap sell accounts do not match target base mint";
  }
  if (accounts[PUMPSWAP_QUOTE_MINT_INDEX]?.pubkey.toBase58() !== WRAPPED_SOL_MINT) {
    return "source PumpSwap sell accounts do not match SOL quote mint";
  }
  return null;
}

function comparePumpSwapAccounts(
  baseline: AccountMeta[],
  candidate: AccountMeta[],
  candidateLabel: string
): Record<string, unknown> {
  const max = Math.max(baseline.length, candidate.length);
  const pubkeyMismatches: number[] = [];
  const signerMismatches: number[] = [];
  const writableMismatches: number[] = [];
  for (let i = 0; i < max; i++) {
    const a = baseline[i];
    const b = candidate[i];
    if (!a || !b) {
      pubkeyMismatches.push(i);
      continue;
    }
    if (!a.pubkey.equals(b.pubkey)) pubkeyMismatches.push(i);
    if (a.isSigner !== b.isSigner) signerMismatches.push(i);
    if (a.isWritable !== b.isWritable) writableMismatches.push(i);
  }
  return {
    comparedTo: candidateLabel,
    baselineAccounts: baseline.length,
    candidateAccounts: candidate.length,
    pubkeyMismatchCount: pubkeyMismatches.length,
    signerMismatchCount: signerMismatches.length,
    writableMismatchCount: writableMismatches.length,
    pubkeyMismatchIndexes: pubkeyMismatches.slice(0, 16),
    signerMismatchIndexes: signerMismatches.slice(0, 16),
    writableMismatchIndexes: writableMismatches.slice(0, 16),
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
      !discriminator.equals(PUMPSWAP_BUY_EXACT_QUOTE_IN_DISCRIMINATOR) &&
      !discriminator.equals(PUMPSWAP_SELL_DISCRIMINATOR)
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

async function resolvePumpSwapSellAccountPlan(args: {
  mint: PublicKey;
  user: PublicKey;
  baseTokenProgram: PublicKey;
  sourcePumpSwapAccounts?: SourcePumpSwapAccountMeta[] | string[];
}): Promise<PumpSwapAccountPlan | null> {
  const hintedAccounts = sourcePumpSwapHintAccountsToMetas(args.sourcePumpSwapAccounts);

  const canonicalAccounts = await buildCanonicalPumpSwapSellAccounts({
    user: args.user,
    mint: args.mint,
    baseTokenProgram: args.baseTokenProgram,
  });
  if (canonicalAccounts) {
    return {
      accounts: canonicalAccounts,
      source: "canonical-pool-state",
      diagnostics: hintedAccounts
        ? comparePumpSwapAccounts(canonicalAccounts, hintedAccounts, "detector-pubkey-hints")
        : { comparedTo: "detector-pubkey-hints", candidateAccounts: 0 },
    };
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

async function resolvePumpSwapBuyAccountPlan(args: {
  sourceSignature: string;
  mint: PublicKey;
  user: PublicKey;
  baseTokenProgram: PublicKey;
  sourcePumpSwapAccounts?: SourcePumpSwapAccountMeta[] | string[];
  canonicalOnly?: boolean;
}): Promise<PumpSwapAccountPlan | null> {
  const hintedAccounts = sourcePumpSwapHintAccountsToMetas(args.sourcePumpSwapAccounts);

  const canonicalAccounts = await buildCanonicalPumpSwapBuyAccounts({
    user: args.user,
    mint: args.mint,
    baseTokenProgram: args.baseTokenProgram,
  });
  if (canonicalAccounts) {
    return {
      accounts: canonicalAccounts,
      source: "canonical-pool-state",
      diagnostics: hintedAccounts
        ? comparePumpSwapAccounts(canonicalAccounts, hintedAccounts, "detector-pubkey-hints")
        : { comparedTo: "detector-pubkey-hints", candidateAccounts: 0 },
    };
  }

  if (args.canonicalOnly) return null;

  if (hintedAccounts?.length) {
    if (!validatePumpSwapBuyAccountLayout(hintedAccounts, args.mint)) {
      return {
        accounts: hintedAccounts,
        source: "detector-pubkey-hints",
        diagnostics: { canonicalUnavailable: true, detectorHintAccounts: hintedAccounts.length },
      };
    }
  }

  const confirmedAccounts = await sourcePumpSwapAccountsWithShortRetry(args.sourceSignature, args.mint);
  if (confirmedAccounts && !validatePumpSwapBuyAccountLayout(confirmedAccounts, args.mint)) {
    return {
      accounts: confirmedAccounts,
      source: "confirmed-source-metas",
      diagnostics: { canonicalUnavailable: true },
    };
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
const POOL_COIN_CREATOR_OFFSET = POOL_QUOTE_TOKEN_ACCOUNT_OFFSET + 32 + 8;
const POOL_INDEX_OFFSET = 8 + 1;
const GLOBAL_CONFIG_PROTOCOL_FEE_RECIPIENTS_OFFSET = 8 + 32 + 8 + 8 + 8;
const GLOBAL_CONFIG_PROTOCOL_FEE_RECIPIENT_COUNT = 8;
const TOKEN_ACCOUNT_AMOUNT_OFFSET = 64;
const PUMPSWAP_RISK_QUOTE_HAIRCUT_BPS = 100;
const PUMPSWAP_DIRECT_BUY_QUOTE_HAIRCUT_BPS = 300;
const PUMPSWAP_ACCOUNT_CACHE_TTL_MS = 30_000;
const PUMPSWAP_SOURCE_META_ATTEMPTS = 3;
const PUMPSWAP_SOURCE_META_RETRY_MS = 35;
const pumpSwapPoolCache = new Map<string, { pool: PublicKey; baseVault: PublicKey; quoteVault: PublicKey }>();
const pumpSwapMintOwnerCache = new Map<string, { owner: PublicKey; cachedAt: number }>();
const pumpSwapPoolAccountCache = new Map<string, { account: AccountInfo<Buffer>; cachedAt: number }>();
let pumpSwapGlobalConfigCache: { account: AccountInfo<Buffer>; cachedAt: number } | null = null;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function readPubkey(data: Buffer, offset: number): PublicKey {
  return new PublicKey(data.subarray(offset, offset + 32));
}

function isDefaultPubkey(pubkey: PublicKey): boolean {
  return pubkey.equals(PublicKey.default);
}

function readTokenAccountAmount(info: AccountInfo<Buffer> | null): bigint | null {
  if (!info || info.data.length < TOKEN_ACCOUNT_AMOUNT_OFFSET + 8) return null;
  return info.data.readBigUInt64LE(TOKEN_ACCOUNT_AMOUNT_OFFSET);
}

async function getCachedPumpSwapGlobalConfigAccount(): Promise<AccountInfo<Buffer> | null> {
  const now = Date.now();
  if (
    pumpSwapGlobalConfigCache &&
    now - pumpSwapGlobalConfigCache.cachedAt < PUMPSWAP_ACCOUNT_CACHE_TTL_MS
  ) {
    return pumpSwapGlobalConfigCache.account;
  }

  const account = await readConnection.getAccountInfo(PUMPSWAP_GLOBAL_CONFIG, "processed");
  if (account) pumpSwapGlobalConfigCache = { account, cachedAt: now };
  return account;
}

function readProtocolFeeRecipientFromGlobalConfig(account: AccountInfo<Buffer> | null): PublicKey | null {
  if (
    !account ||
    account.data.length <
      GLOBAL_CONFIG_PROTOCOL_FEE_RECIPIENTS_OFFSET + GLOBAL_CONFIG_PROTOCOL_FEE_RECIPIENT_COUNT * 32
  ) {
    return null;
  }

  for (let i = 0; i < GLOBAL_CONFIG_PROTOCOL_FEE_RECIPIENT_COUNT; i++) {
    const recipient = readPubkey(
      account.data,
      GLOBAL_CONFIG_PROTOCOL_FEE_RECIPIENTS_OFFSET + i * 32
    );
    if (!isDefaultPubkey(recipient)) return recipient;
  }
  return null;
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

function invalidatePumpSwapPoolState(pool?: PublicKey): void {
  if (pool) {
    pumpSwapPoolAccountCache.delete(pool.toBase58());
    return;
  }
  pumpSwapPoolAccountCache.clear();
}

async function getCachedPumpSwapPoolAccount(
  pool: PublicKey,
  forceRefresh = false
): Promise<AccountInfo<Buffer> | null> {
  const key = pool.toBase58();
  const cached = pumpSwapPoolAccountCache.get(key);
  const now = Date.now();
  if (!forceRefresh && cached && now - cached.cachedAt < PUMPSWAP_ACCOUNT_CACHE_TTL_MS) return cached.account;

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

function readPoolStateFromAccount(
  pool: PublicKey,
  account: AccountInfo<Buffer> | null
): {
  pool: PublicKey;
  baseMint: PublicKey;
  quoteMint: PublicKey;
  baseVault: PublicKey;
  quoteVault: PublicKey;
  coinCreator: PublicKey;
} | null {
  if (!account || account.data.length < POOL_COIN_CREATOR_OFFSET + 32) return null;
  return {
    pool,
    baseMint: readPubkey(account.data, POOL_BASE_MINT_OFFSET),
    quoteMint: readPubkey(account.data, POOL_QUOTE_MINT_OFFSET),
    baseVault: readPubkey(account.data, POOL_BASE_TOKEN_ACCOUNT_OFFSET),
    quoteVault: readPubkey(account.data, POOL_QUOTE_TOKEN_ACCOUNT_OFFSET),
    coinCreator: readPubkey(account.data, POOL_COIN_CREATOR_OFFSET),
  };
}

export async function buildCanonicalPumpSwapBuyAccounts(args: {
  user: PublicKey;
  mint: PublicKey;
  baseTokenProgram: PublicKey;
}): Promise<AccountMeta[] | null> {
  const pool = await findPumpSwapPoolForMint(args.mint);
  if (!pool) return null;

  const [poolAccount, globalConfigAccount] = await Promise.all([
    getCachedPumpSwapPoolAccount(pool.pool),
    getCachedPumpSwapGlobalConfigAccount(),
  ]);
  if (validatePumpSwapPoolAccount(pool.pool, poolAccount)) return null;

  const poolState = readPoolStateFromAccount(pool.pool, poolAccount);
  if (!poolState) return null;
  if (!poolState.baseMint.equals(args.mint) || poolState.quoteMint.toBase58() !== WRAPPED_SOL_MINT) {
    return null;
  }

  const protocolFeeRecipient = readProtocolFeeRecipientFromGlobalConfig(globalConfigAccount);
  if (!protocolFeeRecipient) return null;

  const quoteMint = new PublicKey(WRAPPED_SOL_MINT);
  const quoteTokenProgram = TOKEN_PROGRAM_ID;
  const userBaseAta = deriveAta(args.user, args.mint, args.baseTokenProgram);
  const userQuoteAta = deriveAta(args.user, quoteMint, quoteTokenProgram);
  const protocolFeeRecipientAta = deriveAta(protocolFeeRecipient, quoteMint, quoteTokenProgram);
  const coinCreatorVaultAuthority = derivePumpSwapCoinCreatorVaultAuthorityPda(poolState.coinCreator);
  const coinCreatorVaultAta = deriveAta(coinCreatorVaultAuthority, quoteMint, quoteTokenProgram);

  return [
    { pubkey: poolState.pool, isSigner: false, isWritable: true },
    { pubkey: args.user, isSigner: true, isWritable: true },
    { pubkey: PUMPSWAP_GLOBAL_CONFIG, isSigner: false, isWritable: false },
    { pubkey: args.mint, isSigner: false, isWritable: false },
    { pubkey: quoteMint, isSigner: false, isWritable: false },
    { pubkey: userBaseAta, isSigner: false, isWritable: true },
    { pubkey: userQuoteAta, isSigner: false, isWritable: true },
    { pubkey: poolState.baseVault, isSigner: false, isWritable: true },
    { pubkey: poolState.quoteVault, isSigner: false, isWritable: true },
    { pubkey: protocolFeeRecipient, isSigner: false, isWritable: false },
    { pubkey: protocolFeeRecipientAta, isSigner: false, isWritable: true },
    { pubkey: args.baseTokenProgram, isSigner: false, isWritable: false },
    { pubkey: quoteTokenProgram, isSigner: false, isWritable: false },
    { pubkey: SYSTEM_PROGRAM_ID, isSigner: false, isWritable: false },
    { pubkey: ASSOCIATED_TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
    { pubkey: derivePumpSwapEventAuthorityPda(), isSigner: false, isWritable: false },
    { pubkey: PUMPSWAP_PROGRAM_ID, isSigner: false, isWritable: false },
    { pubkey: coinCreatorVaultAta, isSigner: false, isWritable: true },
    { pubkey: coinCreatorVaultAuthority, isSigner: false, isWritable: false },
    { pubkey: derivePumpSwapGlobalVolumeAccumulatorPda(), isSigner: false, isWritable: false },
    { pubkey: derivePumpSwapUserVolumeAccumulatorPda(args.user), isSigner: false, isWritable: true },
    { pubkey: derivePumpSwapFeeConfigPda(), isSigner: false, isWritable: false },
    { pubkey: PUMP_FUN_FEE_PROGRAM_ID, isSigner: false, isWritable: false },
  ];
}

function isPumpSwapAccountPlanError(err: unknown): boolean {
  const category = classifyExecutionError(err).category;
  return (
    category === "account-constraint-seeds" ||
    category === "account-constraint-mut" ||
    category === "account-invalid-program" ||
    category === "account-privilege" ||
    category === "account-stale-source" ||
    category === "account-layout"
  );
}

function isPumpSwapOverflowError(err: unknown): boolean {
  const classified = classifyExecutionError(err);
  return classified.category === "liquidity-overflow" || classified.code === "6023";
}

export async function buildCanonicalPumpSwapSellAccounts(args: {
  user: PublicKey;
  mint: PublicKey;
  baseTokenProgram: PublicKey;
}): Promise<AccountMeta[] | null> {
  const pool = await findPumpSwapPoolForMint(args.mint);
  if (!pool) return null;

  const [poolAccount, globalConfigAccount] = await Promise.all([
    getCachedPumpSwapPoolAccount(pool.pool),
    getCachedPumpSwapGlobalConfigAccount(),
  ]);
  if (validatePumpSwapPoolAccount(pool.pool, poolAccount)) return null;

  const poolState = readPoolStateFromAccount(pool.pool, poolAccount);
  if (!poolState) return null;
  if (!poolState.baseMint.equals(args.mint) || poolState.quoteMint.toBase58() !== WRAPPED_SOL_MINT) {
    return null;
  }

  const protocolFeeRecipient = readProtocolFeeRecipientFromGlobalConfig(globalConfigAccount);
  if (!protocolFeeRecipient) return null;

  const quoteMint = new PublicKey(WRAPPED_SOL_MINT);
  const quoteTokenProgram = TOKEN_PROGRAM_ID;
  const userBaseAta = deriveAta(args.user, args.mint, args.baseTokenProgram);
  const userQuoteAta = deriveAta(args.user, quoteMint, quoteTokenProgram);
  const protocolFeeRecipientAta = deriveAta(protocolFeeRecipient, quoteMint, quoteTokenProgram);
  const coinCreatorVaultAuthority = derivePumpSwapCoinCreatorVaultAuthorityPda(poolState.coinCreator);
  const coinCreatorVaultAta = deriveAta(coinCreatorVaultAuthority, quoteMint, quoteTokenProgram);

  return [
    { pubkey: poolState.pool, isSigner: false, isWritable: true },
    { pubkey: args.user, isSigner: true, isWritable: true },
    { pubkey: PUMPSWAP_GLOBAL_CONFIG, isSigner: false, isWritable: false },
    { pubkey: args.mint, isSigner: false, isWritable: false },
    { pubkey: quoteMint, isSigner: false, isWritable: false },
    { pubkey: userBaseAta, isSigner: false, isWritable: true },
    { pubkey: userQuoteAta, isSigner: false, isWritable: true },
    { pubkey: poolState.baseVault, isSigner: false, isWritable: true },
    { pubkey: poolState.quoteVault, isSigner: false, isWritable: true },
    { pubkey: protocolFeeRecipient, isSigner: false, isWritable: false },
    { pubkey: protocolFeeRecipientAta, isSigner: false, isWritable: true },
    { pubkey: args.baseTokenProgram, isSigner: false, isWritable: false },
    { pubkey: quoteTokenProgram, isSigner: false, isWritable: false },
    { pubkey: SYSTEM_PROGRAM_ID, isSigner: false, isWritable: false },
    { pubkey: ASSOCIATED_TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
    { pubkey: derivePumpSwapEventAuthorityPda(), isSigner: false, isWritable: false },
    { pubkey: PUMPSWAP_PROGRAM_ID, isSigner: false, isWritable: false },
    { pubkey: coinCreatorVaultAta, isSigner: false, isWritable: true },
    { pubkey: coinCreatorVaultAuthority, isSigner: false, isWritable: false },
    { pubkey: derivePumpSwapFeeConfigPda(), isSigner: false, isWritable: false },
    { pubkey: PUMP_FUN_FEE_PROGRAM_ID, isSigner: false, isWritable: false },
  ];
}

export async function warmPumpSwapStaticAccounts(): Promise<void> {
  await getCachedPumpSwapGlobalConfigAccount();
  derivePumpSwapEventAuthorityPda();
  derivePumpSwapGlobalVolumeAccumulatorPda();
  derivePumpSwapFeeConfigPda();
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

async function estimatePumpSwapSellQuoteFromPool(args: {
  pool: PublicKey;
  poolAccount: AccountInfo<Buffer> | null;
  baseAmountRaw: number;
}): Promise<bigint | null> {
  if (!Number.isFinite(args.baseAmountRaw) || args.baseAmountRaw <= 0) return null;
  const vaults = readPoolVaultsFromAccount(args.pool, args.poolAccount);
  if (!vaults) return null;

  const [baseInfo, quoteInfo] = await readConnection.getMultipleAccountsInfo(
    [vaults.baseVault, vaults.quoteVault],
    "processed"
  );
  const baseReserve = readTokenAccountAmount(baseInfo);
  const quoteReserve = readTokenAccountAmount(quoteInfo);
  if (!baseReserve || !quoteReserve || baseReserve <= 0n || quoteReserve <= 0n) return null;

  const baseIn = BigInt(Math.floor(args.baseAmountRaw));
  if (baseIn <= 0n) return null;
  const rawOut = (quoteReserve * baseIn) / (baseReserve + baseIn);
  return rawOut > 0n ? rawOut : null;
}

export async function estimatePumpSwapSellQuote(
  mint: string,
  baseAmountRaw: number
): Promise<number | null> {
  if (!Number.isFinite(baseAmountRaw) || baseAmountRaw <= 0) return null;
  const pool = await findPumpSwapPoolForMint(new PublicKey(mint));
  if (!pool) return null;

  const rawOut = await estimatePumpSwapSellQuoteFromPool({
    pool: pool.pool,
    poolAccount: await getCachedPumpSwapPoolAccount(pool.pool),
    baseAmountRaw,
  });
  if (rawOut == null) return null;
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
  sourcePumpSwapInstruction?: PumpSwapBuyInstruction;
  sourcePumpSwapAccounts?: SourcePumpSwapAccountMeta[] | string[];
  solInLamports: number;
  slippageBps: number;
  startedAt: number;
  log: (msg: string) => void;
  rebroadcastAttempt?: number;
  canonicalRetryAttempt?: number;
  poolRefreshAttempt?: number;
  buyInstruction?: PumpSwapBuyInstruction;
  alternateBuyAttempt?: number;
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
  const accountPlan = await resolvePumpSwapBuyAccountPlan({
    sourceSignature: args.sourceSignature,
    mint: mintKey,
    user: args.user.publicKey,
    baseTokenProgram: baseTokenProgramId,
    sourcePumpSwapAccounts: args.sourcePumpSwapAccounts,
    canonicalOnly: (args.canonicalRetryAttempt ?? 0) > 0,
  });
  if (!accountPlan) {
    return {
      ok: false,
      recoverable: true,
      reason:
        "PumpSwap account plan unavailable; exact source metas and detector hints missing or invalid",
    };
  }
  args.log(`PumpSwap account plan: ${accountPlan.source}`);
  if (
    accountPlan.source === "canonical-pool-state" &&
    accountPlan.diagnostics?.comparedTo === "detector-pubkey-hints" &&
    typeof accountPlan.diagnostics.pubkeyMismatchCount === "number" &&
    accountPlan.diagnostics.pubkeyMismatchCount > 0
  ) {
    const writableMismatchCount =
      typeof accountPlan.diagnostics.writableMismatchCount === "number"
        ? accountPlan.diagnostics.writableMismatchCount
        : 0;
    args.log(
      `PumpSwap detector hints differed from canonical accounts: ` +
        `${accountPlan.diagnostics.pubkeyMismatchCount} pubkey mismatch(es), ` +
        `${writableMismatchCount} writable mismatch(es).`
    );
  }

  const { accounts, userBaseAta, userQuoteAta } = replacePumpSwapUserAccounts({
    accounts: accountPlan.accounts,
    user: args.user.publicKey,
    baseMint: mintKey,
    baseTokenProgram: baseTokenProgramId,
  });

  if (accounts.length < PUMPSWAP_MIN_BUY_ACCOUNT_COUNT) {
    return { ok: false, recoverable: true, reason: `PumpSwap account list too short: ${accounts.length}` };
  }

  const poolAccount = await getCachedPumpSwapPoolAccount(
    accounts[0]!.pubkey,
    (args.poolRefreshAttempt ?? 0) > 0
  );
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
  const buyInstruction = args.buyInstruction ?? args.sourcePumpSwapInstruction ?? "buy_exact_quote_in";

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
    buyInstruction,
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

  const rustSourcePumpSwapAccounts = accountPlan.accounts.map((account) => ({
      pubkey: account.pubkey.toBase58(),
      isSigner: account.isSigner,
      isWritable: account.isWritable,
  }));
  const alternateBuyInstruction: PumpSwapBuyInstruction =
    buyInstruction === "buy" ? "buy_exact_quote_in" : "buy";
  const executeRustAttempt = async (
    attemptInstruction: PumpSwapBuyInstruction,
    attempt: "primary" | "overflow-retry"
  ) => {
    const rustBuildStartedAt = Date.now();
    const rust = await executeRustPumpSwapBuy({
      route: "pumpswap-direct-buy",
      mint: args.mint,
      solInLamports: args.solInLamports,
      expectedBaseOutRaw: Number(expectedBaseOut),
      minBaseOutRaw: Number(minBaseOut),
      slippageBps: args.slippageBps,
      buyInstruction: attemptInstruction,
      sourcePumpSwapAccounts: rustSourcePumpSwapAccounts,
      baseTokenProgramId: baseTokenProgramId.toBase58(),
    });
    if (rust?.ok) {
      recordExecutionStage({
        stage: "build",
        route: "pumpswap-direct-buy",
        side: "buy",
        mint: args.mint,
        sourceSignature: args.sourceSignature,
        elapsedMs: rust.buildMs ?? Date.now() - rustBuildStartedAt,
        success: true,
        details: {
          executor: "rust",
          senderPath: rust.senderPath,
          txBytes: rust.txBytes,
          broadcastMs: rust.broadcastMs,
          accountPlan: accountPlan.source,
          buyInstruction: attemptInstruction,
          attempt,
        },
      });
      recordExecutionStage({
        stage: "broadcast",
        route: "pumpswap-direct-buy",
        side: "buy",
        mint: args.mint,
        sourceSignature: args.sourceSignature,
        signature: rust.signature,
        elapsedMs: rust.broadcastMs,
        success: true,
        details: {
          executor: "rust",
          senderPath: rust.senderPath,
          accountPlan: accountPlan.source,
          buyInstruction: attemptInstruction,
          attempt,
        },
      });
      args.log(
        `Rust executor PumpSwap buy broadcast${attempt === "overflow-retry" ? " (overflow retry)" : ""}: ` +
          `${rust.signature}` +
          (rust.elapsedMs != null ? ` (${rust.elapsedMs}ms)` : "")
      );
    }
    return rust;
  };
  const confirmRustAttempt = async (
    signature: string,
    mode: "background" | "blocking",
    attemptInstruction: PumpSwapBuyInstruction,
    attempt: "primary" | "overflow-retry"
  ): Promise<{ ok: true } | { ok: false; message: string }> => {
    const confirmStartedAt = Date.now();
    try {
      await confirmSignature(signature);
      recordPumpSwapConfirmStage({
        route: "pumpswap-direct-buy",
        side: "buy",
        mint: args.mint,
        sourceSignature: args.sourceSignature,
        signature,
        startedAt: confirmStartedAt,
        success: true,
        details: {
          mode,
          accountPlan: accountPlan.source,
          executor: "rust",
          buyInstruction: attemptInstruction,
          attempt,
        },
      });
      args.onConfirmSuccess?.(args.mint, Number(expectedBaseOut), args.solInLamports, signature);
      return { ok: true };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      recordPumpSwapConfirmStage({
        route: "pumpswap-direct-buy",
        side: "buy",
        mint: args.mint,
        sourceSignature: args.sourceSignature,
        signature,
        startedAt: confirmStartedAt,
        success: false,
        error: msg,
        details: {
          mode,
          accountPlan: accountPlan.source,
          executor: "rust",
          buyInstruction: attemptInstruction,
          attempt,
        },
      });
      console.warn(
        `PumpSwap confirmation failed after Rust broadcast` +
          (attempt === "overflow-retry" ? " overflow retry" : "") +
          `: ${msg}`
      );
      return { ok: false, message: msg };
    }
  };
  const retryRustOverflow = async (mode: "background" | "blocking", originalMsg: string): Promise<string | null> => {
    if (!isPumpSwapOverflowError(originalMsg)) return null;
    releaseRustBoughtMint(args.mint, `PumpSwap overflow retry after ${buyInstruction} failed: ${originalMsg}`);
    args.log(
      `PumpSwap Rust overflow retry: ${buyInstruction} failed with 6023; ` +
        `retrying ${alternateBuyInstruction}.`
    );
    const retry = await executeRustAttempt(alternateBuyInstruction, "overflow-retry");
    if (!retry?.ok) {
      args.log(
        `PumpSwap Rust overflow retry failed before confirmation: ` +
          `${retry && !retry.ok ? retry.error : "Rust PumpSwap executor unavailable"}`
      );
      return null;
    }
    const confirmed = await confirmRustAttempt(
      retry.signature,
      mode,
      alternateBuyInstruction,
      "overflow-retry"
    );
    return confirmed.ok ? retry.signature : null;
  };

  const rust = await executeRustAttempt(buyInstruction, "primary");
  if (rust?.ok) {
    const signature = rust.signature;
    if (fastMode) {
      const confirmStartedAt = Date.now();
      confirmSignature(signature)
        .then(() => {
          recordPumpSwapConfirmStage({
            route: "pumpswap-direct-buy",
            side: "buy",
            mint: args.mint,
            sourceSignature: args.sourceSignature,
            signature,
            startedAt: confirmStartedAt,
            success: true,
            details: {
              mode: "background",
              accountPlan: accountPlan.source,
              executor: "rust",
              buyInstruction,
              attempt: "primary",
            },
          });
          args.onConfirmSuccess?.(args.mint, Number(expectedBaseOut), args.solInLamports, signature);
        })
        .catch(async (err) => {
          const msg = err instanceof Error ? err.message : String(err);
          recordPumpSwapConfirmStage({
            route: "pumpswap-direct-buy",
            side: "buy",
            mint: args.mint,
            sourceSignature: args.sourceSignature,
            signature,
            startedAt: confirmStartedAt,
            success: false,
            error: msg,
            details: {
              mode: "background",
              accountPlan: accountPlan.source,
              executor: "rust",
              buyInstruction,
              attempt: "primary",
            },
          });
          console.warn(`PumpSwap confirmation failed after Rust broadcast: ${msg}`);
          const retrySignature = await retryRustOverflow("background", msg).catch((retryErr) => {
            const retryMsg = retryErr instanceof Error ? retryErr.message : String(retryErr);
            console.warn(`PumpSwap Rust overflow retry crashed: ${retryMsg}`);
            return null;
          });
          if (retrySignature) return;
          releaseRustBoughtMint(args.mint, `PumpSwap confirmation failed after Rust broadcast: ${msg}`);
          args.onConfirmFailure?.(args.mint, Number(expectedBaseOut), args.solInLamports, msg);
        });
      return {
        ok: true,
        signature,
        isSimulated: false,
        baseOut: expectedBaseOut,
        quoteIn: BigInt(args.solInLamports),
        mintMeta: await getTokenMeta(args.mint),
        confirmationPending: true,
      };
    }
    let finalSignature = signature;
    const confirmed = await confirmRustAttempt(signature, "blocking", buyInstruction, "primary");
    if (!confirmed.ok) {
      const retrySignature = await retryRustOverflow("blocking", confirmed.message).catch(() => null);
      if (!retrySignature) {
        releaseRustBoughtMint(args.mint, "PumpSwap Rust blocking confirmation failed");
        return { ok: false, recoverable: true, reason: `PumpSwap Rust confirmation failed: ${confirmed.message}` };
      }
      finalSignature = retrySignature;
    }
    return {
      ok: true,
      signature: finalSignature,
      isSimulated: false,
      baseOut: expectedBaseOut,
      quoteIn: BigInt(args.solInLamports),
      mintMeta: await getTokenMeta(args.mint),
    };
  }
  if (rustOnlyBuys) {
    const reason = rust && !rust.ok ? rust.error : "Rust PumpSwap executor unavailable";
    args.log(`Rust-only PumpSwap buy failed; TypeScript fallback disabled: ${reason}`);
    return { ok: false, recoverable: true, reason: `Rust-only PumpSwap buy failed: ${reason}` };
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

  const swapIx =
    buyInstruction === "buy"
      ? buildNativeIdlInstructionFromOrderedAccounts({
          program: "pump_amm",
          instruction: "buy",
          accounts,
          // The exact-output PumpSwap path spends up to max_quote_amount_in. Use minBaseOut
          // as the target so it behaves like the slippage guard on exact-quote buys.
          data: encodeBuy(minBaseOut, BigInt(args.solInLamports)),
          allowExtraAccounts: true,
        })
      : buildNativeIdlInstructionFromOrderedAccounts({
          program: "pump_amm",
          instruction: "buy_exact_quote_in",
          accounts,
          data: encodeBuyExactQuoteIn(BigInt(args.solInLamports), minBaseOut),
          allowExtraAccounts: true,
        });
  assertNativeIdlInstruction({
    program: "pump_amm",
    instruction: buyInstruction,
    ix: swapIx,
    allowExtraAccounts: true,
  });

  const { blockhash, lastValidBlockHeight } = await getCachedBlockhash();
  args.log(`PumpSwap blockhash ready: lastValidBlockHeight=${lastValidBlockHeight}`);
  const userBaseAtaExists = knownAccountExists(userBaseAta);
  const userQuoteAtaExists = warmWalletMode ? await accountExistsCached(userQuoteAta) : false;
  const setupIxs: TransactionInstruction[] = [];
  if (!userBaseAtaExists) {
    setupIxs.push(
      createAtaIdempotentIx({
        payer: args.user.publicKey,
        owner: args.user.publicKey,
        mint: mintKey,
        tokenProgramId: baseTokenProgramId,
      })
    );
  }
  if (!userQuoteAtaExists) {
    setupIxs.push(
      createAtaIdempotentIx({
        payer: args.user.publicKey,
        owner: args.user.publicKey,
        mint: new PublicKey(WRAPPED_SOL_MINT),
        tokenProgramId: TOKEN_PROGRAM_ID,
      })
    );
  }
  const closeIxs = keepWsolAtaOpen ? [] : [closeTokenAccountIx(userQuoteAta, args.user.publicKey)];
  const buildStartedAt = Date.now();
  let tx;
  let setupTx;
  try {
    const built = await buildSizeSafeV0Transaction({
      payer: args.user.publicKey,
      blockhash,
      // ATA creates are splittable: when the account-heavy buy + WSOL wrap won't fit, they move
      // to their own tx (sent + confirmed first) so the swap stays under the packet limit.
      setupInstructions: setupIxs,
      instructions: [
        ...computeIxs,
        SystemProgram.transfer({
          fromPubkey: args.user.publicKey,
          toPubkey: userQuoteAta,
          lamports: args.solInLamports,
        }),
        syncNativeIx(userQuoteAta),
        swapIx,
        ...closeIxs,
      ],
      signer: args.user,
      log: args.log,
      route: "pumpswap-direct-buy",
    });
    tx = built.swapTx;
    setupTx = built.setupTx;
    recordExecutionStage({
      stage: "build",
      route: "pumpswap-direct-buy",
      side: "buy",
      mint: args.mint,
      sourceSignature: args.sourceSignature,
      elapsedMs: Date.now() - buildStartedAt,
      success: true,
      details: {
        accountPlan: accountPlan.source,
        accountDiagnostics: accountPlan.diagnostics,
        splitSetup: built.setupTx != null,
        txBytes: built.diagnostics.bytes,
      },
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    recordExecutionStage({
      stage: "build",
      route: "pumpswap-direct-buy",
      side: "buy",
      mint: args.mint,
      sourceSignature: args.sourceSignature,
      elapsedMs: Date.now() - buildStartedAt,
      success: false,
      error: msg,
      details: { accountPlan: accountPlan.source, accountDiagnostics: accountPlan.diagnostics },
    });
    return { ok: false, recoverable: true, reason: `PumpSwap build failed: ${msg} | ${formatContext(ctx)}` };
  }

  if (args.log && false) args.log(userBaseAta.toBase58());
  args.log(`PumpSwap tx built (${elapsed()})`);
  if (setupTx) {
    // Oversized combined tx: ATAs were split out. Land + confirm them before the swap, which
    // references the freshly-created accounts.
    try {
      const setupSig = await broadcastTransactionWithSender(setupTx, {
        route: "pumpswap-setup",
        side: "buy",
        mint: args.mint,
        sourceSignature: args.sourceSignature,
      });
      args.log(`PumpSwap setup tx broadcast: ${setupSig}; confirming before swap...`);
      await confirmSignature(setupSig);
      args.log(`PumpSwap setup confirmed (${elapsed()})`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return { ok: false, recoverable: true, reason: `PumpSwap setup tx failed: ${msg}` };
    }
  }
  if (pumpSwapDirectPreflight) {
    const preflightStartedAt = Date.now();
    const sim = await readConnection.simulateTransaction(tx, {
      commitment: "processed",
      sigVerify: false,
      replaceRecentBlockhash: true,
    });
    if (sim.value.err) {
      const logSummary = summarizeLogs(sim.value.logs ?? []);
      const reason = [
        `PumpSwap preflight failed: ${JSON.stringify(sim.value.err)}`,
        formatContext(ctx),
        logSummary ? `logs=${logSummary}` : "",
      ].filter(Boolean).join(" | ");
      recordExecutionStage({
        stage: "preflight",
        route: "pumpswap-direct-buy",
        side: "buy",
        mint: args.mint,
        sourceSignature: args.sourceSignature,
        elapsedMs: Date.now() - preflightStartedAt,
        success: false,
        error: reason,
        details: { accountPlan: accountPlan.source, accountDiagnostics: accountPlan.diagnostics },
      });
      if (
        accountPlan.source !== "canonical-pool-state" &&
        isPumpSwapAccountPlanError(reason) &&
        (args.canonicalRetryAttempt ?? 0) < 1
      ) {
        args.log(
          `PumpSwap account-plan preflight failure (${classifyExecutionError(reason).category}); ` +
            "retrying with canonical pool-state accounts."
        );
        return tryPumpSwapDirectBuy({
          ...args,
          canonicalRetryAttempt: (args.canonicalRetryAttempt ?? 0) + 1,
        });
      }
      if (isPumpSwapOverflowError(reason) && (args.poolRefreshAttempt ?? 0) < 1) {
        args.log("PumpSwap overflow preflight failure; refreshing pool state and requoting once.");
        invalidatePumpSwapPoolState(accounts[0]!.pubkey);
        return tryPumpSwapDirectBuy({
          ...args,
          poolRefreshAttempt: (args.poolRefreshAttempt ?? 0) + 1,
        });
      }
      if (
        isPumpSwapOverflowError(reason) &&
        buyInstruction === "buy" &&
        (args.alternateBuyAttempt ?? 0) < 1
      ) {
        args.log("PumpSwap exact-output overflow persisted; retrying alternate exact-quote-in buy path.");
        return tryPumpSwapDirectBuy({
          ...args,
          buyInstruction: "buy_exact_quote_in",
          alternateBuyAttempt: (args.alternateBuyAttempt ?? 0) + 1,
        });
      }
      if (
        isPumpSwapOverflowError(reason) &&
        buyInstruction === "buy_exact_quote_in" &&
        (args.alternateBuyAttempt ?? 0) < 1
      ) {
        args.log("PumpSwap exact-quote overflow persisted; retrying alternate exact-output buy path.");
        return tryPumpSwapDirectBuy({
          ...args,
          buyInstruction: "buy",
          alternateBuyAttempt: (args.alternateBuyAttempt ?? 0) + 1,
        });
      }
      return {
        ok: false,
        recoverable: isRecoverablePumpSwapFailure(reason),
        reason,
      };
    }
    recordExecutionStage({
      stage: "preflight",
      route: "pumpswap-direct-buy",
      side: "buy",
      mint: args.mint,
      sourceSignature: args.sourceSignature,
      elapsedMs: Date.now() - preflightStartedAt,
      success: true,
      details: { accountPlan: accountPlan.source, accountDiagnostics: accountPlan.diagnostics },
    });
    args.log(`PumpSwap preflight ok (${elapsed()})`);
  }
  let signature: string | null = null;
  try {
    signature = await broadcastTransactionWithSender(tx, {
      preferHelius: (args.rebroadcastAttempt ?? 0) > 0,
      route: "pumpswap-direct-buy",
      side: "buy",
      mint: args.mint,
      sourceSignature: args.sourceSignature,
    });
    args.log(`PumpSwap broadcast: ${signature} (${elapsed()})`);
    if (fastMode) {
      // PumpSwap is a hot post-graduation entry path; do not block recording on confirmation.
      const confirmStartedAt = Date.now();
      confirmSignature(signature)
        .then(() => {
          recordPumpSwapConfirmStage({
            route: "pumpswap-direct-buy",
            side: "buy",
            mint: args.mint,
            sourceSignature: args.sourceSignature,
            signature: signature ?? undefined,
            startedAt: confirmStartedAt,
            success: true,
            details: { mode: "background", accountPlan: accountPlan.source },
          });
          args.onConfirmSuccess?.(args.mint, Number(expectedBaseOut), args.solInLamports, signature!);
        })
        .catch((err) => {
          const msg = err instanceof Error ? err.message : String(err);
          recordPumpSwapConfirmStage({
            route: "pumpswap-direct-buy",
            side: "buy",
            mint: args.mint,
            sourceSignature: args.sourceSignature,
            signature: signature ?? undefined,
            startedAt: confirmStartedAt,
            success: false,
            error: msg,
            details: { mode: "background", accountPlan: accountPlan.source },
          });
          console.warn(`PumpSwap confirmation failed after broadcast: ${msg}`);
          args.onConfirmFailure?.(args.mint, Number(expectedBaseOut), args.solInLamports, msg);
        });
    } else {
      const confirmStartedAt = Date.now();
      await confirmSignature(signature);
      recordPumpSwapConfirmStage({
        route: "pumpswap-direct-buy",
        side: "buy",
        mint: args.mint,
        sourceSignature: args.sourceSignature,
        signature,
        startedAt: confirmStartedAt,
        success: true,
        details: { mode: "blocking", accountPlan: accountPlan.source },
      });
      args.log(`PumpSwap confirmed (${elapsed()})`);
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (signature) {
      recordPumpSwapConfirmStage({
        route: "pumpswap-direct-buy",
        side: "buy",
        mint: args.mint,
        sourceSignature: args.sourceSignature,
        signature,
        startedAt: args.startedAt,
        success: false,
        error: msg,
        details: { mode: "blocking", accountPlan: accountPlan.source, rebroadcastAttempt: args.rebroadcastAttempt ?? 0 },
      });
    }
    const rebroadcastAttempt = args.rebroadcastAttempt ?? 0;
    if (isConfirmationTimeoutError(err) && rebroadcastAttempt < confirmationRebroadcastRetries) {
      // PumpSwap direct bypasses Jupiter's retry ladder, so rebuild here on suspected bundle drops.
      args.log(`PumpSwap buy suspected dropped (${msg}); rebuilding once with fresh blockhash...`);
      return tryPumpSwapDirectBuy({
        ...args,
        rebroadcastAttempt: rebroadcastAttempt + 1,
      });
    }
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

export async function tryPumpSwapDirectSell(args: {
  user: Keypair;
  mint: string;
  tokensInRaw: number | bigint;
  slippageBps: number;
  startedAt: number;
  log: (msg: string) => void;
  sourceSignature?: string;
  sourcePumpSwapAccounts?: SourcePumpSwapAccountMeta[] | string[];
  rebroadcastAttempt?: number;
  onConfirmFailure?: (mint: string, tokensInRaw: number, solOutLamports: number, reason: string) => void;
  onConfirmSuccess?: (mint: string, tokensInRaw: number, solOutLamports: number, signature: string) => void;
}): Promise<PumpSwapSellOutcome> {
  const elapsed = () => `${Date.now() - args.startedAt}ms`;
  const mintKey = new PublicKey(args.mint);
  const baseTokenProgramId = await getCachedMintOwner(mintKey);
  if (!baseTokenProgramId) {
    return { ok: false, recoverable: true, reason: "PumpSwap base mint account not found" };
  }

  const accountPlan = await resolvePumpSwapSellAccountPlan({
    user: args.user.publicKey,
    mint: mintKey,
    baseTokenProgram: baseTokenProgramId,
    sourcePumpSwapAccounts: args.sourcePumpSwapAccounts,
  });
  if (!accountPlan) {
    return { ok: false, recoverable: true, reason: "canonical PumpSwap sell accounts unavailable" };
  }
  args.log(`PumpSwap sell account plan: ${accountPlan.source}`);

  const { accounts, userQuoteAta } = replacePumpSwapUserAccounts({
    accounts: accountPlan.accounts,
    user: args.user.publicKey,
    baseMint: mintKey,
    baseTokenProgram: baseTokenProgramId,
  });
  if (accounts.length < PUMPSWAP_MIN_SELL_ACCOUNT_COUNT) {
    return { ok: false, recoverable: true, reason: `PumpSwap sell account list too short: ${accounts.length}` };
  }
  const poolAccount = await getCachedPumpSwapPoolAccount(accounts[0]!.pubkey);
  const poolError = validatePumpSwapPoolAccount(accounts[0]!.pubkey, poolAccount);
  if (poolError) return { ok: false, recoverable: true, reason: poolError };

  const tokensIn =
    typeof args.tokensInRaw === "bigint" ? args.tokensInRaw : BigInt(Math.floor(args.tokensInRaw));
  if (tokensIn <= 0n) {
    return { ok: false, recoverable: true, reason: "PumpSwap sell amount is zero" };
  }

  const expectedQuoteOut = await estimatePumpSwapSellQuoteFromPool({
    pool: accounts[0]!.pubkey,
    poolAccount,
    baseAmountRaw: Number(tokensIn),
  });
  if (expectedQuoteOut == null || expectedQuoteOut <= 0n) {
    return { ok: false, recoverable: true, reason: "PumpSwap sell quote unavailable" };
  }
  const minOutBps = Math.max(1, 10_000 - Math.min(9_999, Math.max(0, args.slippageBps)));
  const minQuoteOut = (expectedQuoteOut * BigInt(minOutBps)) / 10_000n;
  if (minQuoteOut <= 0n) {
    return { ok: false, recoverable: true, reason: "PumpSwap sell min output is zero" };
  }

  args.log(
    `PumpSwap direct sell: mint=${args.mint} pool=${accounts[0]!.pubkey.toBase58()} ` +
      `user=${args.user.publicKey.toBase58()} tokensIn=${tokensIn} expectedOut=${expectedQuoteOut} ` +
      `minOut=${minQuoteOut} slippage=${(args.slippageBps / 100).toFixed(2)}% ` +
      `accounts=${accounts.length} baseTokenProgram=${baseTokenProgramId.toBase58()}`
  );

  if (simulationMode) {
    const mintMeta = await getTokenMeta(args.mint);
    args.log("PumpSwap sell simulation mode: not sending tx");
    return {
      ok: true,
      signature: `sim-pumpswap-sell-${args.mint.slice(0, 8)}-${Date.now()}`,
      isSimulated: true,
      baseIn: tokensIn,
      quoteOut: expectedQuoteOut,
      mintMeta,
    };
  }

  const rustSourcePumpSwapAccounts = accounts.map((account) => ({
    pubkey: account.pubkey.toBase58(),
    isSigner: account.isSigner,
    isWritable: account.isWritable,
  }));
  const rustBuildStartedAt = Date.now();
  const rust = await executeRustPumpSwapSell({
    route: "pumpswap-direct-sell",
    mint: args.mint,
    tokensInRaw: Number(tokensIn),
    expectedQuoteOutRaw: Number(expectedQuoteOut),
    minQuoteOutRaw: Number(minQuoteOut),
    sourcePumpSwapAccounts: rustSourcePumpSwapAccounts,
    baseTokenProgramId: baseTokenProgramId.toBase58(),
  });
  if (rust?.ok) {
    recordExecutionStage({
      stage: "build",
      route: "pumpswap-direct-sell",
      side: "sell",
      mint: args.mint,
      sourceSignature: args.sourceSignature,
      elapsedMs: rust.buildMs ?? Date.now() - rustBuildStartedAt,
      success: true,
      details: {
        executor: "rust",
        senderPath: rust.senderPath,
        txBytes: rust.txBytes,
        broadcastMs: rust.broadcastMs,
        accountPlan: accountPlan.source,
      },
    });
    recordExecutionStage({
      stage: "broadcast",
      route: "pumpswap-direct-sell",
      side: "sell",
      mint: args.mint,
      sourceSignature: args.sourceSignature,
      signature: rust.signature,
      elapsedMs: rust.broadcastMs,
      success: true,
      details: {
        executor: "rust",
        senderPath: rust.senderPath,
        accountPlan: accountPlan.source,
      },
    });
    args.log(
      `Rust executor PumpSwap sell broadcast: ${rust.signature}` +
        (rust.elapsedMs != null ? ` (${rust.elapsedMs}ms)` : "")
    );
    if (fastMode) {
      const confirmStartedAt = Date.now();
      confirmSignature(rust.signature)
        .then(() => {
          recordPumpSwapConfirmStage({
            route: "pumpswap-direct-sell",
            side: "sell",
            mint: args.mint,
            sourceSignature: args.sourceSignature,
            signature: rust.signature,
            startedAt: confirmStartedAt,
            success: true,
            details: { mode: "background", accountPlan: accountPlan.source, executor: "rust" },
          });
          args.onConfirmSuccess?.(args.mint, Number(tokensIn), Number(expectedQuoteOut), rust.signature);
        })
        .catch((err) => {
          const msg = err instanceof Error ? err.message : String(err);
          recordPumpSwapConfirmStage({
            route: "pumpswap-direct-sell",
            side: "sell",
            mint: args.mint,
            sourceSignature: args.sourceSignature,
            signature: rust.signature,
            startedAt: confirmStartedAt,
            success: false,
            error: msg,
            details: { mode: "background", accountPlan: accountPlan.source, executor: "rust" },
          });
          console.warn(`PumpSwap sell confirmation failed after Rust broadcast: ${msg}`);
          args.onConfirmFailure?.(args.mint, Number(tokensIn), Number(expectedQuoteOut), msg);
        });
      return {
        ok: true,
        signature: rust.signature,
        isSimulated: false,
        baseIn: tokensIn,
        quoteOut: expectedQuoteOut,
        mintMeta: await getTokenMeta(args.mint),
        confirmationPending: true,
      };
    }
    const confirmStartedAt = Date.now();
    try {
      await confirmSignature(rust.signature);
      recordPumpSwapConfirmStage({
        route: "pumpswap-direct-sell",
        side: "sell",
        mint: args.mint,
        sourceSignature: args.sourceSignature,
        signature: rust.signature,
        startedAt: confirmStartedAt,
        success: true,
        details: { mode: "blocking", accountPlan: accountPlan.source, executor: "rust" },
      });
      args.onConfirmSuccess?.(args.mint, Number(tokensIn), Number(expectedQuoteOut), rust.signature);
      return {
        ok: true,
        signature: rust.signature,
        isSimulated: false,
        baseIn: tokensIn,
        quoteOut: expectedQuoteOut,
        mintMeta: await getTokenMeta(args.mint),
      };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      recordPumpSwapConfirmStage({
        route: "pumpswap-direct-sell",
        side: "sell",
        mint: args.mint,
        sourceSignature: args.sourceSignature,
        signature: rust.signature,
        startedAt: confirmStartedAt,
        success: false,
        error: msg,
        details: { mode: "blocking", accountPlan: accountPlan.source, executor: "rust" },
      });
      return { ok: false, recoverable: true, reason: `PumpSwap Rust sell confirmation failed: ${msg}` };
    }
  }
  if (rustOnlySells) {
    const reason = rust && !rust.ok ? rust.error : "Rust PumpSwap sell executor unavailable";
    args.log(`Rust-only PumpSwap sell failed; TypeScript fallback disabled: ${reason}`);
    return { ok: false, recoverable: true, reason: `Rust-only PumpSwap sell failed: ${reason}` };
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

  const swapIx = buildNativeIdlInstructionFromOrderedAccounts({
    program: "pump_amm",
    instruction: "sell",
    accounts,
    data: encodeSell(tokensIn, minQuoteOut),
    allowExtraAccounts: true,
  });
  assertNativeIdlInstruction({
    program: "pump_amm",
    instruction: "sell",
    ix: swapIx,
    allowExtraAccounts: true,
  });

  const { blockhash, lastValidBlockHeight } = await getCachedBlockhash();
  args.log(`PumpSwap sell blockhash ready: lastValidBlockHeight=${lastValidBlockHeight}`);
  const userQuoteAtaExists = warmWalletMode ? await accountExistsCached(userQuoteAta) : false;
  const setupIxs: TransactionInstruction[] = userQuoteAtaExists
    ? []
    : [
        createAtaIdempotentIx({
          payer: args.user.publicKey,
          owner: args.user.publicKey,
          mint: new PublicKey(WRAPPED_SOL_MINT),
          tokenProgramId: TOKEN_PROGRAM_ID,
        }),
  ];
  // Sells must unwrap WSOL back to native SOL so wallet balance and PnL accounting stay correct.
  const closeIxs = [closeTokenAccountIx(userQuoteAta, args.user.publicKey)];
  const buildStartedAt = Date.now();
  let tx;
  let setupTx;
  try {
    const built = await buildSizeSafeV0Transaction({
      payer: args.user.publicKey,
      blockhash,
      setupInstructions: setupIxs,
      instructions: [
        ...computeIxs,
        swapIx,
        ...closeIxs,
      ],
      signer: args.user,
      log: args.log,
      route: "pumpswap-direct-sell",
    });
    tx = built.swapTx;
    setupTx = built.setupTx;
    recordExecutionStage({
      stage: "build",
      route: "pumpswap-direct-sell",
      side: "sell",
      mint: args.mint,
      elapsedMs: Date.now() - buildStartedAt,
      success: true,
      details: { splitSetup: built.setupTx != null, txBytes: built.diagnostics.bytes },
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    recordExecutionStage({
      stage: "build",
      route: "pumpswap-direct-sell",
      side: "sell",
      mint: args.mint,
      elapsedMs: Date.now() - buildStartedAt,
      success: false,
      error: msg,
    });
    return { ok: false, recoverable: true, reason: `PumpSwap sell build failed: ${msg}` };
  }

  args.log(`PumpSwap sell tx built (${elapsed()})`);
  if (setupTx) {
    try {
      const setupSig = await broadcastTransactionWithSender(setupTx, {
        route: "pumpswap-setup",
        side: "sell",
        mint: args.mint,
      });
      args.log(`PumpSwap sell setup tx broadcast: ${setupSig}; confirming before swap...`);
      await confirmSignature(setupSig);
      args.log(`PumpSwap sell setup confirmed (${elapsed()})`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return { ok: false, recoverable: true, reason: `PumpSwap sell setup tx failed: ${msg}` };
    }
  }
  if (pumpSwapDirectPreflight) {
    const preflightStartedAt = Date.now();
    const sim = await readConnection.simulateTransaction(tx, {
      commitment: "processed",
      sigVerify: false,
      replaceRecentBlockhash: true,
    });
    if (sim.value.err) {
      const logSummary = summarizeLogs(sim.value.logs ?? []);
      recordExecutionStage({
        stage: "preflight",
        route: "pumpswap-direct-sell",
        side: "sell",
        mint: args.mint,
        elapsedMs: Date.now() - preflightStartedAt,
        success: false,
        error: JSON.stringify(sim.value.err),
        details: { logs: logSummary },
      });
      return {
        ok: false,
        recoverable: true,
        reason: [
          `PumpSwap sell preflight failed: ${JSON.stringify(sim.value.err)}`,
          logSummary ? `logs=${logSummary}` : "",
        ].filter(Boolean).join(" | "),
      };
    }
    recordExecutionStage({
      stage: "preflight",
      route: "pumpswap-direct-sell",
      side: "sell",
      mint: args.mint,
      elapsedMs: Date.now() - preflightStartedAt,
      success: true,
    });
    args.log(`PumpSwap sell preflight ok (${elapsed()})`);
  }

  let signature: string | null = null;
  try {
    signature = await broadcastTransactionWithSender(tx, {
      preferHelius: (args.rebroadcastAttempt ?? 0) > 0,
      route: "pumpswap-direct-sell",
      side: "sell",
      mint: args.mint,
    });
    args.log(`PumpSwap sell broadcast: ${signature} (${elapsed()})`);
    if (fastMode) {
      const confirmStartedAt = Date.now();
      confirmSignature(signature)
        .then(() => {
          recordPumpSwapConfirmStage({
            route: "pumpswap-direct-sell",
            side: "sell",
            mint: args.mint,
            signature: signature ?? undefined,
            startedAt: confirmStartedAt,
            success: true,
            details: { mode: "background" },
          });
          args.onConfirmSuccess?.(args.mint, Number(tokensIn), Number(expectedQuoteOut), signature!);
        })
        .catch((err) => {
          const msg = err instanceof Error ? err.message : String(err);
          recordPumpSwapConfirmStage({
            route: "pumpswap-direct-sell",
            side: "sell",
            mint: args.mint,
            signature: signature ?? undefined,
            startedAt: confirmStartedAt,
            success: false,
            error: msg,
            details: { mode: "background" },
          });
          console.warn(`PumpSwap sell confirmation failed after broadcast: ${msg}`);
          args.onConfirmFailure?.(args.mint, Number(tokensIn), Number(expectedQuoteOut), msg);
        });
    } else {
      const confirmStartedAt = Date.now();
      await confirmSignature(signature);
      recordPumpSwapConfirmStage({
        route: "pumpswap-direct-sell",
        side: "sell",
        mint: args.mint,
        signature,
        startedAt: confirmStartedAt,
        success: true,
        details: { mode: "blocking" },
      });
      args.log(`PumpSwap sell confirmed (${elapsed()})`);
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (signature) {
      recordPumpSwapConfirmStage({
        route: "pumpswap-direct-sell",
        side: "sell",
        mint: args.mint,
        signature,
        startedAt: args.startedAt,
        success: false,
        error: msg,
        details: { mode: "blocking", rebroadcastAttempt: args.rebroadcastAttempt ?? 0 },
      });
    }
    const rebroadcastAttempt = args.rebroadcastAttempt ?? 0;
    if (isConfirmationTimeoutError(err) && rebroadcastAttempt < confirmationRebroadcastRetries) {
      args.log(`PumpSwap sell suspected dropped (${msg}); rebuilding once with fresh blockhash...`);
      return tryPumpSwapDirectSell({
        ...args,
        rebroadcastAttempt: rebroadcastAttempt + 1,
      });
    }
    const logs = signature ? await getTransactionFailureLogs(signature) : [];
    const logSummary = summarizeLogs(logs);
    const reason = [
      `PumpSwap sell send/confirm failed: ${msg}`,
      signature ? `signature=${signature}` : "",
      logSummary ? `logs=${logSummary}` : "",
    ].filter(Boolean).join(" | ");
    return { ok: false, recoverable: isRecoverablePumpSwapFailure(err), reason };
  }

  const mintMeta = await getTokenMeta(args.mint);
  return {
    ok: true,
    signature,
    isSimulated: false,
    baseIn: tokensIn,
    quoteOut: expectedQuoteOut,
    mintMeta,
    confirmationPending: fastMode,
  };
}
