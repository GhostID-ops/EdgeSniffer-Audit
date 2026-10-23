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
  pumpSwapPrewarmTtlMs,
  pumpSwapPrewarmUseSourceRatio,
  readConnection,
  rustOnlyBuys,
  rustOnlySells,
  simulationMode,
  warmWalletMode,
} from "../config.js";
import { getDynamicPriorityFeeForRoute } from "../feeOracle.js";
import { getCachedBlockhash } from "../blockhashCache.js";
import { broadcastTransactionWithSender } from "../sender.js";
import { buildSizeSafeV0Transaction } from "../transactionSize.js";
import { confirmSignature, isConfirmationTimeoutError } from "../confirm.js";
import { getTokenMeta, type TokenMeta, USDC_MINT, WRAPPED_SOL_MINT } from "../tokenRegistry.js";
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
  PUMP_FUN_PROGRAM_ID,
  SYSTEM_PROGRAM_ID,
  TOKEN_PROGRAM_ID,
  deriveAta,
} from "../pumpfun/constants.js";

export const PUMPSWAP_PROGRAM_ID = new PublicKey("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA");
// Canonical global config PDA = findProgramAddress(["global_config"], program).
// Derive it instead of hardcoding: the previous literal (ADyA8hBd1ckwk...WqV7Zb)
// was a lookalike that does not exist on-chain, so every canonical buy/sell read
// a dead account ("protocol fee recipient unreadable") and only source-hint
// trades worked. Resolves to ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw.
export const PUMPSWAP_GLOBAL_CONFIG = PublicKey.findProgramAddressSync(
  [Buffer.from("global_config")],
  PUMPSWAP_PROGRAM_ID
)[0];
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
const PUMPSWAP_PROTOCOL_FEE_RECIPIENT_INDEX = 9;
const PUMPSWAP_PROTOCOL_FEE_RECIPIENT_ATA_INDEX = 10;
const PUMPSWAP_BASE_TOKEN_PROGRAM_INDEX = 11;
const PUMPSWAP_USER_VOLUME_ACCUMULATOR_INDEX = 20;
const PUMPSWAP_BUY_CASHBACK_ATA_INDEX = 23;
const PUMPSWAP_SELL_CASHBACK_ATA_INDEX = 21;
const PUMPSWAP_SELL_CASHBACK_USER_VOLUME_INDEX = 22;
const PUMPSWAP_MIN_BUY_ACCOUNT_COUNT = 23;
const PUMPSWAP_MIN_SELL_ACCOUNT_COUNT = 21;
const PUMPSWAP_CANONICAL_WRITABLE_INDEXES = new Set([0, 1, 5, 6, 7, 8, 10, 17, 20]);

function isPumpSwapCanonicalBuyWritable(index: number): boolean {
  return PUMPSWAP_CANONICAL_WRITABLE_INDEXES.has(index) || index >= PUMPSWAP_MIN_BUY_ACCOUNT_COUNT;
}
const PUMPSWAP_OVERFLOW_RETRY_MIN_OUT_BPS_RAW = Number.parseInt(
  process.env.PUMPSWAP_OVERFLOW_RETRY_MIN_OUT_BPS ?? "0",
  10
);
const PUMPSWAP_OVERFLOW_RETRY_MIN_OUT_BPS =
  Number.isFinite(PUMPSWAP_OVERFLOW_RETRY_MIN_OUT_BPS_RAW) && PUMPSWAP_OVERFLOW_RETRY_MIN_OUT_BPS_RAW > 0
    ? Math.min(10_000, PUMPSWAP_OVERFLOW_RETRY_MIN_OUT_BPS_RAW)
    : 0;

type PumpSwapAccountPlan = {
  accounts: AccountMeta[];
  source: "confirmed-source-metas" | "detector-pubkey-hints" | "canonical-pool-state" | "cached-buy-account-plan";
  diagnostics?: Record<string, unknown>;
};

type PumpSwapBuyInstruction = "buy" | "buy_exact_quote_in";

const pumpSwapBuyAccountPlanByMint = new Map<string, AccountMeta[]>();
const pumpSwapPrewarmByMint = new Map<string, { cachedAt: number; pool?: string; sourceSignature?: string }>();

function cloneAccountMetas(accounts: AccountMeta[]): AccountMeta[] {
  return accounts.map((account) => ({ ...account }));
}

function rememberPumpSwapBuyAccountPlan(mint: PublicKey, accounts: AccountMeta[]): void {
  pumpSwapBuyAccountPlanByMint.set(mint.toBase58(), cloneAccountMetas(accounts));
}

export type PumpSwapPrewarmResult = {
  mint: string;
  pool?: string;
  planCached: boolean;
  poolResolved: boolean;
  baseTokenProgram?: string;
  accountPlan?: PumpSwapAccountPlan["source"];
  accountCount?: number;
  skippedReason?: string;
  elapsedMs: number;
};

function applySourceProtocolFeeAccounts(
  accounts: AccountMeta[],
  hintedAccounts: AccountMeta[] | null | undefined
): AccountMeta[] {
  const recipient = hintedAccounts?.[PUMPSWAP_PROTOCOL_FEE_RECIPIENT_INDEX];
  const recipientAta = hintedAccounts?.[PUMPSWAP_PROTOCOL_FEE_RECIPIENT_ATA_INDEX];
  if (!recipient || !recipientAta) return accounts;

  const expectedAta = deriveAta(recipient.pubkey, new PublicKey(WRAPPED_SOL_MINT), TOKEN_PROGRAM_ID);
  if (!recipientAta.pubkey.equals(expectedAta)) return accounts;

  const next = cloneAccountMetas(accounts);
  if (next.length <= PUMPSWAP_PROTOCOL_FEE_RECIPIENT_ATA_INDEX) return accounts;
  next[PUMPSWAP_PROTOCOL_FEE_RECIPIENT_INDEX] = {
    pubkey: recipient.pubkey,
    isSigner: false,
    isWritable: false,
  };
  next[PUMPSWAP_PROTOCOL_FEE_RECIPIENT_ATA_INDEX] = {
    pubkey: recipientAta.pubkey,
    isSigner: false,
    isWritable: true,
  };
  return next;
}

// NOTE: a cached PumpSwap BUY account list must never be replayed as a SELL —
// the layouts differ (buy has extra volume-accumulator accounts), which caused
// on-chain AccountOwnedByWrongProgram (3007) failures. resolvePumpSwapSellAccountPlan
// now reuses only the buy plan's pool address and rebuilds the canonical sell
// layout from on-chain state.

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

// The canonical PumpSwap pool for a pump.fun graduated token is fully
// deterministic — no getProgramAccounts scan or source hint required. Migration
// creates it (pump.json `migrate`) with index 0, quote=WSOL, and creator equal
// to the per-mint `pool-authority` PDA owned by the pump.fun program. The pool
// itself is a PDA owned by the PumpSwap program (pump_amm.json `create_pool`):
//   pool_authority = PDA(["pool-authority", mint], PUMP_FUN_PROGRAM_ID)
//   pool           = PDA(["pool", u16le(0), pool_authority, mint, WSOL], PUMPSWAP_PROGRAM_ID)
export function derivePumpFunGraduatedPoolPda(mint: PublicKey): PublicKey {
  const [poolAuthority] = PublicKey.findProgramAddressSync(
    [Buffer.from("pool-authority"), mint.toBuffer()],
    PUMP_FUN_PROGRAM_ID
  );
  const indexLe = Buffer.alloc(2); // canonical migration index = 0
  const [pool] = PublicKey.findProgramAddressSync(
    [
      Buffer.from("pool"),
      indexLe,
      poolAuthority.toBuffer(),
      mint.toBuffer(),
      new PublicKey(WRAPPED_SOL_MINT).toBuffer(),
    ],
    PUMPSWAP_PROGRAM_ID
  );
  return pool;
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
    isWritable: isPumpSwapCanonicalBuyWritable(idx),
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

function validatePumpSwapBuyAccountLayout(accounts: AccountMeta[], mint: PublicKey, quoteMint = WRAPPED_SOL_MINT): string | null {
  if (accounts.length < PUMPSWAP_MIN_BUY_ACCOUNT_COUNT) {
    return `PumpSwap account list too short: ${accounts.length}`;
  }
  if (!accounts[PUMPSWAP_BASE_MINT_INDEX]?.pubkey.equals(mint)) {
    return "source PumpSwap accounts do not match target base mint";
  }
  if (accounts[PUMPSWAP_QUOTE_MINT_INDEX]?.pubkey.toBase58() !== quoteMint) {
    return "source PumpSwap accounts do not match requested quote mint";
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

  // Pool-address candidates, most-trusted first: the source sell ix pool (index
  // 0), then the pool from a successful buy of this mint this session. A
  // PumpSwap BUY account list is NOT a valid SELL layout — buy carries two extra
  // volume-accumulator accounts, so reusing it verbatim shifts the trailing
  // accounts and the program rejects the tx with AccountOwnedByWrongProgram
  // (3007). We therefore never reuse buy metas; we only take the buy plan's pool
  // address (index 0) and rebuild the correct canonical SELL layout from
  // verified on-chain pool state.
  const cachedBuyPlan = pumpSwapBuyAccountPlanByMint.get(args.mint.toBase58()) ?? null;
  const cachedBuyPool = cachedBuyPlan?.[0]?.pubkey ?? null;
  const poolHint = hintedAccounts?.[0]?.pubkey ?? cachedBuyPool;

  // A cashback / volume-tracking coin — our confirmed buy carried the cashback
  // tail beyond the canonical 23-account layout — MUST sell with the matching
  // user_volume_accumulator + cashback ATA tail. A naked 21-account sell template
  // (a non-cashback trader's sell, or the hand-built canonical layout below) is
  // missing those accounts and the pump AMM rejects the sell with Custom(6013),
  // which then re-triggers the hybrid TP on the still-full position in a loop.
  // Convert OUR own proven buy plan to the sell layout via the sanctioned
  // buyTemplateToSellTemplate helper so the tail is always consistent with the
  // buy that actually landed. Guarded + fail-safe: any layout drift returns null
  // and we fall through to the existing template/canonical resolution.
  if (cachedBuyPlan && cachedBuyPlan.length > PUMPSWAP_MIN_BUY_ACCOUNT_COUNT) {
    const converted = buyTemplateToSellTemplate(cachedBuyPlan);
    if (converted && !validatePumpSwapSellAccountLayout(converted, args.mint)) {
      return {
        accounts: applySourceProtocolFeeAccounts(converted, hintedAccounts),
        source: "cached-buy-account-plan",
        diagnostics: hintedAccounts
          ? comparePumpSwapAccounts(converted, hintedAccounts, "detector-pubkey-hints")
          : { comparedTo: "cached-buy-account-plan", candidateAccounts: converted.length },
      };
    }
  }

  // AGENTS.md: do not trust source/detector metas for PumpSwap sells. Build the
  // account list from a recent real on-chain SELL on the verified pool — that is
  // the current layout (incl. the per-token fee-program accounts), and the user
  // accounts are substituted downstream by the Rust executor.
  const template = await fetchPumpSwapPoolTradeTemplate({
    mint: args.mint,
    side: "sell",
    baseTokenProgram: args.baseTokenProgram,
    poolHint,
  });
  if (template) {
    return {
      accounts: applySourceProtocolFeeAccounts(template, hintedAccounts),
      source: "canonical-pool-state",
      diagnostics: hintedAccounts
        ? comparePumpSwapAccounts(template, hintedAccounts, "detector-pubkey-hints")
        : { comparedTo: "detector-pubkey-hints", candidateAccounts: 0 },
    };
  }

  // A newly graduated pool can be fully readable before any recent PumpSwap
  // sell is indexed. Do not fail that race after pool validation succeeds:
  // build the authoritative sell layout directly from canonical pool state.
  // Source/detector sell metas remain untrusted and are used only as a pool hint.
  const canonical = await buildCanonicalPumpSwapSellAccounts({
    user: args.user,
    mint: args.mint,
    baseTokenProgram: args.baseTokenProgram,
    poolHint,
  });
  if (canonical) {
    return {
      accounts: applySourceProtocolFeeAccounts(canonical, hintedAccounts),
      source: "canonical-pool-state",
      diagnostics: hintedAccounts
        ? comparePumpSwapAccounts(canonical, hintedAccounts, "detector-pubkey-hints")
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

type PumpSwapTxResponse = NonNullable<Awaited<ReturnType<typeof readConnection.getTransaction>>>;

// Extract a PumpSwap buy- or sell-instruction's full account meta list from a
// confirmed transaction (top-level or via inner CPI), preserving the on-chain
// signer/writable flags. Used to template the CURRENT on-chain account layout.
function pumpSwapAccountMetasFromTx(
  tx: PumpSwapTxResponse,
  mint: PublicKey,
  side: "buy" | "sell"
): AccountMeta[] | null {
  const message = tx.transaction.message;
  const loaded = tx.meta?.loadedAddresses
    ? [...tx.meta.loadedAddresses.writable, ...tx.meta.loadedAddresses.readonly]
    : [];
  const allKeys = [...message.staticAccountKeys, ...loaded];

  const match = (ix: {
    programIdIndex: number;
    accountKeyIndexes?: number[];
    accounts?: number[] | Uint8Array;
    data?: unknown;
  }): AccountMeta[] | null => {
    if (!allKeys[ix.programIdIndex]?.equals(PUMPSWAP_PROGRAM_ID)) return null;
    const disc = compiledInstructionDataToBuffer(ix.data).subarray(0, 8);
    const isBuy =
      disc.equals(PUMPSWAP_BUY_DISCRIMINATOR) || disc.equals(PUMPSWAP_BUY_EXACT_QUOTE_IN_DISCRIMINATOR);
    const isSell = disc.equals(PUMPSWAP_SELL_DISCRIMINATOR);
    if (side === "buy" ? !isBuy : !isSell) return null;
    const indexes = ix.accountKeyIndexes ?? Array.from(ix.accounts ?? []);
    const keys = indexes.map((idx) => allKeys[idx]);
    if (!keys[PUMPSWAP_BASE_MINT_INDEX]?.equals(mint)) return null;
    if (keys[PUMPSWAP_QUOTE_MINT_INDEX]?.toBase58() !== WRAPPED_SOL_MINT) return null;
    return indexes.map((idx) => ({
      pubkey: allKeys[idx]!,
      isSigner: message.isAccountSigner(idx),
      isWritable: message.isAccountWritable(idx),
    }));
  };

  for (const ix of message.compiledInstructions) {
    const found = match(ix);
    if (found) return found;
  }
  for (const group of tx.meta?.innerInstructions ?? []) {
    for (const ix of group.instructions) {
      const found = match(ix as Parameters<typeof match>[0]);
      if (found) return found;
    }
  }
  return null;
}

// Convert a PumpSwap BUY account template into the SELL layout. Per the pump_amm
// IDL the sell account list is identical to the buy list except it omits
// global_volume_accumulator (index 19) and user_volume_accumulator (index 20);
// the trailing per-token fee-program accounts are the same for both. This lets
// us produce a sell template from the much-more-common buy activity (e.g. a
// freshly graduated token that has no sells yet). We validate that indices 19/20
// really are the two volume accumulators before removing them, so any future
// layout drift fails closed instead of corrupting the sell.
export function buyTemplateToSellTemplate(buy: AccountMeta[]): AccountMeta[] | null {
  if (buy.length < PUMPSWAP_MIN_BUY_ACCOUNT_COUNT) return null;
  const user = buy[PUMPSWAP_USER_INDEX]?.pubkey;
  if (!user) return null;
  const globalVol = buy[19]?.pubkey;
  const userVol = buy[PUMPSWAP_USER_VOLUME_ACCUMULATOR_INDEX]?.pubkey;
  if (!globalVol?.equals(derivePumpSwapGlobalVolumeAccumulatorPda())) return null;
  if (!userVol?.equals(derivePumpSwapUserVolumeAccumulatorPda(user))) return null;
  const sell = [...buy.slice(0, 19), ...buy.slice(PUMPSWAP_USER_VOLUME_ACCUMULATOR_INDEX + 1, 23)];
  const remaining = buy.slice(23);
  const userVolume = derivePumpSwapUserVolumeAccumulatorPda(user);
  const cashbackAta = deriveAta(userVolume, new PublicKey(WRAPPED_SOL_MINT), TOKEN_PROGRAM_ID);
  if (remaining[0]?.pubkey.equals(cashbackAta)) {
    // Cashback buys append only the WSOL cashback ATA. Cashback sells require
    // that ATA followed by the matching PumpSwap user-volume PDA. Omitting the
    // PDA shifts the remaining-account tail and caused on-chain 6023 failures.
    sell.push(
      remaining[0],
      { pubkey: userVolume, isSigner: false, isWritable: true },
      ...remaining.slice(1)
    );
  } else {
    sell.push(...remaining);
  }
  return sell;
}

// Build a PumpSwap account list for the current on-chain layout by templating a
// recent real trade on the resolved pool. This auto-adapts to pump's evolving
// account set (e.g. the per-token fee-program accounts that the hand-built
// canonical builder does not know about). Only user-specific accounts are
// substituted downstream (by the Rust executor / replacePumpSwapUserAccounts),
// so reusing another trader's template is safe; we still verify pool, mints and
// base token program before trusting it. For sells, when no recent sell exists
// (common right after graduation) we derive the sell layout from a buy. Cached
// per (pool, side) for the session.
async function fetchPumpSwapPoolTradeTemplate(args: {
  mint: PublicKey;
  side: "buy" | "sell";
  baseTokenProgram: PublicKey;
  poolHint?: PublicKey | null;
  retryOnIndexingLag?: boolean;
}): Promise<AccountMeta[] | null> {
  const pool = await findPumpSwapPoolForMint(args.mint, args.poolHint, {
    retryOnIndexingLag: args.retryOnIndexingLag,
  });
  if (!pool) return null;
  const cacheKey = `${pool.pool.toBase58()}|${args.side}`;
  const cached = pumpSwapPoolTemplateCache.get(cacheKey);
  if (cached) return cloneAccountMetas(cached);

  let signatures;
  try {
    signatures = await readConnection.getSignaturesForAddress(pool.pool, {
      limit: PUMPSWAP_TEMPLATE_SIG_LIMIT,
    });
  } catch {
    return null;
  }
  const minCount =
    args.side === "buy" ? PUMPSWAP_MIN_BUY_ACCOUNT_COUNT : PUMPSWAP_MIN_SELL_ACCOUNT_COUNT;
  for (const sig of signatures) {
    if (sig.err) continue;
    const tx = await readConnection
      .getTransaction(sig.signature, { commitment: "confirmed", maxSupportedTransactionVersion: 0 })
      .catch(() => null);
    if (!tx) continue;
    let accounts = pumpSwapAccountMetasFromTx(tx, args.mint, args.side);
    // For sells, fall back to converting a buy on the same pool — a freshly
    // graduated token often has buys but no sells yet, and sell = buy minus the
    // two volume-accumulator accounts (pump_amm IDL).
    if (!accounts && args.side === "sell") {
      const buy = pumpSwapAccountMetasFromTx(tx, args.mint, "buy");
      if (buy) accounts = buyTemplateToSellTemplate(buy);
    }
    if (!accounts || accounts.length < minCount) continue;
    if (!accounts[0]?.pubkey.equals(pool.pool)) continue;
    if (!accounts[PUMPSWAP_BASE_TOKEN_PROGRAM_INDEX]?.pubkey.equals(args.baseTokenProgram)) continue;
    pumpSwapPoolTemplateCache.set(cacheKey, cloneAccountMetas(accounts));
    return cloneAccountMetas(accounts);
  }
  return null;
}

/**
 * pump-amm reads extra "cashback coin" accounts as `remaining_accounts` beyond the canonical
 * 23-account buy layout (the on-chain IDL still only declares 23). When the source's buy carried
 * more than the canonical count, omitting the tail makes the fee/volume math overflow on-chain
 * (AnchorError buy.rs Overflow / custom 6023), so the buy is missed entirely.
 *
 * The canonical/template plan correctly derives indices 0..22 for OUR wallet, and the Rust
 * executor substitutes the user-specific accounts by fixed index. The tail accounts are
 * pool/coin-level for these coins, so we clone them from the source template (the proven
 * on-chain account set). This only affects coins whose source buy exceeded the canonical
 * count, so normal (non-cashback) buys are unchanged.
 */
function appendSourceRemainingAccounts(
  plan: AccountMeta[],
  hinted: AccountMeta[] | null
): AccountMeta[] {
  if (!hinted || hinted.length <= plan.length) return plan;
  const remaining = hinted.slice(plan.length).map((a) => ({
    pubkey: a.pubkey,
    isSigner: false,
    isWritable: a.isWritable,
  }));
  return [...plan, ...remaining];
}

async function resolvePumpSwapBuyAccountPlan(args: {
  sourceSignature: string;
  mint: PublicKey;
  user: PublicKey;
  baseTokenProgram: PublicKey;
  sourcePumpSwapAccounts?: SourcePumpSwapAccountMeta[] | string[];
  quoteMint?: string;
  canonicalOnly?: boolean;
}): Promise<PumpSwapAccountPlan | null> {
  const hintedAccounts = sourcePumpSwapHintAccountsToMetas(args.sourcePumpSwapAccounts);
  const poolHint = hintedAccounts?.[0]?.pubkey ?? null;

  // USDC pools are not the deterministic WSOL graduation pool. Without source
  // or confirmed metas, fail closed instead of silently selecting a WSOL pool.
  if (args.quoteMint === USDC_MINT) {
    if (args.canonicalOnly) return null;
    if (hintedAccounts?.length && !validatePumpSwapBuyAccountLayout(hintedAccounts, args.mint, args.quoteMint)) {
      return {
        accounts: hintedAccounts,
        source: "detector-pubkey-hints",
        diagnostics: { detectorHintAccounts: hintedAccounts.length },
      };
    }
    const confirmedAccounts = await sourcePumpSwapAccountsWithShortRetry(args.sourceSignature, args.mint);
    if (confirmedAccounts && !validatePumpSwapBuyAccountLayout(confirmedAccounts, args.mint, args.quoteMint)) {
      return { accounts: confirmedAccounts, source: "confirmed-source-metas", diagnostics: {} };
    }
    return null;
  }

  // Prefer authoritative PumpSwap account plans for WSOL graduated buys. Detector
  // hints are fast, but this wallet repeatedly produced PrivilegeEscalation/6023
  // with detector-pubkey-hints; pool-state templates avoid trusting unverified
  // signer/writable/layout flags from the source payload.
  const template = await fetchPumpSwapPoolTradeTemplate({
    mint: args.mint,
    side: "buy",
    baseTokenProgram: args.baseTokenProgram,
    poolHint,
    retryOnIndexingLag: true,
  });
  if (template) {
    return {
      accounts: appendSourceRemainingAccounts(
        applySourceProtocolFeeAccounts(template, hintedAccounts),
        hintedAccounts
      ),
      source: "canonical-pool-state",
      diagnostics: hintedAccounts
        ? comparePumpSwapAccounts(template, hintedAccounts, "detector-pubkey-hints")
        : { comparedTo: "detector-pubkey-hints", candidateAccounts: 0 },
    };
  }

  const canonical = await buildCanonicalPumpSwapBuyAccounts({
    user: args.user,
    mint: args.mint,
    baseTokenProgram: args.baseTokenProgram,
    poolHint,
  });
  if (canonical) {
    return {
      accounts: appendSourceRemainingAccounts(
        applySourceProtocolFeeAccounts(canonical, hintedAccounts),
        hintedAccounts
      ),
      source: "canonical-pool-state",
      diagnostics: hintedAccounts
        ? comparePumpSwapAccounts(canonical, hintedAccounts, "detector-pubkey-hints")
        : { comparedTo: "detector-pubkey-hints", candidateAccounts: 0 },
    };
  }

  if (args.canonicalOnly) return null;

  // Last resort: if pool-state is not readable/indexed yet, use validated source
  // metas rather than missing the buy entirely. This is no longer the primary
  // path for graduated WSOL tokens.
  if (hintedAccounts?.length && !validatePumpSwapBuyAccountLayout(hintedAccounts, args.mint, args.quoteMint)) {
    return {
      accounts: hintedAccounts,
      source: "detector-pubkey-hints",
      diagnostics: { detectorHintAccounts: hintedAccounts.length, canonicalUnavailable: true },
    };
  }
  const confirmedAccounts = await sourcePumpSwapAccountsWithShortRetry(args.sourceSignature, args.mint);
  if (confirmedAccounts && !validatePumpSwapBuyAccountLayout(confirmedAccounts, args.mint, args.quoteMint)) {
    return { accounts: confirmedAccounts, source: "confirmed-source-metas", diagnostics: { canonicalUnavailable: true } };
  }
  return null;
}

export function replacePumpSwapUserAccounts(args: {
  accounts: AccountMeta[];
  user: PublicKey;
  baseMint: PublicKey;
  baseTokenProgram: PublicKey;
  quoteMint?: PublicKey;
  // Only PumpSwap BUYS carry a user_volume_accumulator at index 20. In the
  // current SELL layout index 20 is fee_program, so substituting it there would
  // corrupt the instruction. Defaults to true (buy behavior).
  replaceUserVolumeAccumulator?: boolean;
  replaceSellCashbackAccounts?: boolean;
}): { accounts: AccountMeta[]; userBaseAta: PublicKey; userQuoteAta: PublicKey } {
  const accounts = args.accounts.map((a) => ({ ...a }));
  const templateUser = accounts[PUMPSWAP_USER_INDEX]?.pubkey;
  const templateUserVolume = accounts[PUMPSWAP_USER_VOLUME_ACCUMULATOR_INDEX]?.pubkey;
  const userBaseAta = deriveAta(args.user, args.baseMint, args.baseTokenProgram);
  const userQuoteAta = deriveAta(args.user, args.quoteMint ?? new PublicKey(WRAPPED_SOL_MINT), TOKEN_PROGRAM_ID);

  accounts[PUMPSWAP_USER_INDEX] = { pubkey: args.user, isSigner: true, isWritable: true };
  accounts[PUMPSWAP_USER_BASE_ATA_INDEX] = { pubkey: userBaseAta, isSigner: false, isWritable: true };
  accounts[PUMPSWAP_USER_QUOTE_ATA_INDEX] = { pubkey: userQuoteAta, isSigner: false, isWritable: true };
  if (
    (args.replaceUserVolumeAccumulator ?? true) &&
    accounts.length > PUMPSWAP_USER_VOLUME_ACCUMULATOR_INDEX
  ) {
    accounts[PUMPSWAP_USER_VOLUME_ACCUMULATOR_INDEX] = {
      pubkey: derivePumpSwapUserVolumeAccumulatorPda(args.user),
      isSigner: false,
      isWritable: true,
    };
  }
  if (
    (args.replaceUserVolumeAccumulator ?? true) &&
    templateUser &&
    templateUserVolume?.equals(derivePumpSwapUserVolumeAccumulatorPda(templateUser)) &&
    accounts.length > PUMPSWAP_BUY_CASHBACK_ATA_INDEX
  ) {
    const userVolume = derivePumpSwapUserVolumeAccumulatorPda(args.user);
    accounts[PUMPSWAP_BUY_CASHBACK_ATA_INDEX] = {
      pubkey: deriveAta(userVolume, new PublicKey(WRAPPED_SOL_MINT), TOKEN_PROGRAM_ID),
      isSigner: false,
      isWritable: true,
    };
  }
  if (args.replaceSellCashbackAccounts && templateUser) {
    const templateVolume = derivePumpSwapUserVolumeAccumulatorPda(templateUser);
    const templateCashbackAta = deriveAta(
      templateVolume,
      new PublicKey(WRAPPED_SOL_MINT),
      TOKEN_PROGRAM_ID
    );
    const hasCashbackSellTail =
      accounts[PUMPSWAP_SELL_CASHBACK_ATA_INDEX]?.pubkey.equals(templateCashbackAta) &&
      accounts[PUMPSWAP_SELL_CASHBACK_USER_VOLUME_INDEX]?.pubkey.equals(templateVolume);
    if (hasCashbackSellTail) {
      const userVolume = derivePumpSwapUserVolumeAccumulatorPda(args.user);
      accounts[PUMPSWAP_SELL_CASHBACK_ATA_INDEX] = {
        pubkey: deriveAta(userVolume, new PublicKey(WRAPPED_SOL_MINT), TOKEN_PROGRAM_ID),
        isSigner: false,
        isWritable: true,
      };
      accounts[PUMPSWAP_SELL_CASHBACK_USER_VOLUME_INDEX] = {
        pubkey: userVolume,
        isSigner: false,
        isWritable: true,
      };
    }
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
const PUMPSWAP_SOURCE_META_ATTEMPTS = Math.max(
  1,
  Number.parseInt(process.env.PUMPSWAP_SOURCE_META_ATTEMPTS ?? "8", 10) || 8
);
const PUMPSWAP_SOURCE_META_RETRY_MS = Math.max(
  0,
  Number.parseInt(process.env.PUMPSWAP_SOURCE_META_RETRY_MS ?? "60", 10) || 60
);
const PUMPSWAP_STATIC_ACCOUNT_READ_ATTEMPTS = 3;
const PUMPSWAP_STATIC_ACCOUNT_READ_RETRY_MS = 40;
const PUMPSWAP_TEMPLATE_SIG_LIMIT = 12;
const PUMPSWAP_POOL_RESOLUTION_ATTEMPTS = Math.max(
  1,
  Number.parseInt(process.env.PUMPSWAP_POOL_RESOLUTION_ATTEMPTS ?? "4", 10) || 4
);
const PUMPSWAP_POOL_RESOLUTION_RETRY_MS = Math.max(
  0,
  Number.parseInt(process.env.PUMPSWAP_POOL_RESOLUTION_RETRY_MS ?? "35", 10) || 35
);
const pumpSwapPoolTemplateCache = new Map<string, AccountMeta[]>();
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

  // The global config is effectively immutable; a transient RPC read failure
  // must never break a buy/sell. Retry briefly, then fall back to the
  // last-known-good value (ignoring TTL) rather than returning null.
  for (let attempt = 0; attempt < PUMPSWAP_STATIC_ACCOUNT_READ_ATTEMPTS; attempt++) {
    try {
      const account = await readConnection.getAccountInfo(PUMPSWAP_GLOBAL_CONFIG, "processed");
      if (account) {
        pumpSwapGlobalConfigCache = { account, cachedAt: now };
        return account;
      }
    } catch {
      // fall through to retry / sticky fallback
    }
    if (attempt < PUMPSWAP_STATIC_ACCOUNT_READ_ATTEMPTS - 1) {
      await sleep(PUMPSWAP_STATIC_ACCOUNT_READ_RETRY_MS);
    }
  }
  return pumpSwapGlobalConfigCache?.account ?? null;
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

export async function prewarmPumpSwapBuyAccounts(args: {
  mint: string | PublicKey;
  user: PublicKey;
  sourceSignature?: string;
  quoteMint?: string;
}): Promise<PumpSwapPrewarmResult> {
  const startedAt = Date.now();
  const mint = typeof args.mint === "string" ? new PublicKey(args.mint) : args.mint;
  const mintBase58 = mint.toBase58();
  const baseTokenProgram = await getCachedMintOwner(mint);
  if (!baseTokenProgram) {
    return {
      mint: mintBase58,
      planCached: false,
      poolResolved: false,
      skippedReason: "base mint account unavailable",
      elapsedMs: Date.now() - startedAt,
    };
  }

  const plan = await resolvePumpSwapBuyAccountPlan({
    sourceSignature: args.sourceSignature ?? "pumpswap-prewarm",
    mint,
    user: args.user,
    baseTokenProgram,
    quoteMint: args.quoteMint ?? WRAPPED_SOL_MINT,
    canonicalOnly: true,
  });
  if (!plan) {
    return {
      mint: mintBase58,
      planCached: false,
      poolResolved: false,
      baseTokenProgram: baseTokenProgram.toBase58(),
      skippedReason: "canonical PumpSwap account plan unavailable",
      elapsedMs: Date.now() - startedAt,
    };
  }

  rememberPumpSwapBuyAccountPlan(mint, plan.accounts);
  const pool = plan.accounts[0]?.pubkey ?? null;
  let poolResolved = false;
  if (pool) {
    const poolAccount = await getCachedPumpSwapPoolAccount(pool);
    poolResolved = poolAccount != null && validatePumpSwapPoolAccount(pool, poolAccount) == null;
  }
  pumpSwapPrewarmByMint.set(mintBase58, {
    cachedAt: Date.now(),
    pool: pool?.toBase58(),
    sourceSignature: args.sourceSignature,
  });

  return {
    mint: mintBase58,
    pool: pool?.toBase58(),
    planCached: true,
    poolResolved,
    baseTokenProgram: baseTokenProgram.toBase58(),
    accountPlan: plan.source,
    accountCount: plan.accounts.length,
    elapsedMs: Date.now() - startedAt,
  };
}

// Read a candidate pool address on-chain and accept it only if it is genuinely
// the PumpSwap pool for this mint (PumpSwap-owned, base=mint, quote=WSOL). This
// is how we use untrusted hints/derivations safely: the address is only a
// candidate; the on-chain pool state is the source of truth.
async function validatedPumpSwapPoolCandidate(
  mint: PublicKey,
  candidate: PublicKey,
  forceRefresh = false
): Promise<{ pool: PublicKey; baseVault: PublicKey; quoteVault: PublicKey } | null> {
  const account = await getCachedPumpSwapPoolAccount(candidate, forceRefresh);
  if (validatePumpSwapPoolAccount(candidate, account)) return null;
  const state = readPoolStateFromAccount(candidate, account);
  if (!state || !state.baseMint.equals(mint) || state.quoteMint.toBase58() !== WRAPPED_SOL_MINT) {
    return null;
  }
  return { pool: candidate, baseVault: state.baseVault, quoteVault: state.quoteVault };
}

async function findPumpSwapPoolForMint(
  mint: PublicKey,
  poolHint?: PublicKey | null,
  options: { retryOnIndexingLag?: boolean } = {}
): Promise<{
  pool: PublicKey;
  baseVault: PublicKey;
  quoteVault: PublicKey;
} | null> {
  const cached = pumpSwapPoolCache.get(mint.toBase58());
  if (cached) return cached;

  // Resolve the pool without the heavy getProgramAccounts scan whenever possible.
  // The scan is slow and frequently unavailable right after graduation or on
  // throttled RPCs — the root cause of failed PumpSwap buys/sells. We try, in
  // order: (1) the pool address carried by the source/detector tx, and (2) the
  // fully deterministic pump.fun-graduated pool PDA. Both are only candidate
  // addresses — each is verified against on-chain pool state before use, so a
  // bad hint or a non-canonical pool simply falls through to the scan.
  const candidates: PublicKey[] = [];
  if (poolHint) candidates.push(poolHint);
  try {
    candidates.push(derivePumpFunGraduatedPoolPda(mint));
  } catch {
    // derivation only fails on an invalid mint; the scan below still covers it.
  }
  const candidateAttempts = options.retryOnIndexingLag ? PUMPSWAP_POOL_RESOLUTION_ATTEMPTS : 1;
  for (let attempt = 0; attempt < candidateAttempts; attempt++) {
    if (attempt > 0) await sleep(PUMPSWAP_POOL_RESOLUTION_RETRY_MS);
    for (const candidate of candidates) {
      const resolved = await validatedPumpSwapPoolCandidate(mint, candidate, attempt > 0);
      if (resolved) {
        pumpSwapPoolCache.set(mint.toBase58(), resolved);
        return resolved;
      }
    }
  }

  let accounts: Awaited<ReturnType<typeof readConnection.getProgramAccounts>>;
  try {
    accounts = await readConnection.getProgramAccounts(PUMPSWAP_PROGRAM_ID, {
      commitment: "confirmed",
      filters: [
        { memcmp: { offset: POOL_BASE_MINT_OFFSET, bytes: mint.toBase58() } },
        { memcmp: { offset: POOL_QUOTE_MINT_OFFSET, bytes: WRAPPED_SOL_MINT } },
      ],
    });
  } catch {
    // Many fast/cheap RPCs rate-limit or disable getProgramAccounts. The PDA
    // candidate above already covers canonical pump.fun graduations.
    return null;
  }
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

// Explains, in one line, why pool resolution failed for a mint. Used on the
// account-plan-unavailable path so the bot log states the real cause instead of
// a generic message — and its presence in the log proves the new code is live.
async function describePumpSwapPoolResolution(mint: PublicKey): Promise<string> {
  try {
    const derived = derivePumpFunGraduatedPoolPda(mint);
    const tag = `${derived.toBase58().slice(0, 8)}…`;
    const account = await getCachedPumpSwapPoolAccount(derived);
    if (!account) {
      return `derived pool ${tag} not found on read RPC (graduation indexing lag, or read RPC differs from where the token lives)`;
    }
    if (!account.owner.equals(PUMPSWAP_PROGRAM_ID)) {
      return `derived pool ${tag} owned by ${account.owner.toBase58().slice(0, 8)}…, not PumpSwap`;
    }
    const state = readPoolStateFromAccount(derived, account);
    if (!state) return `derived pool ${tag} data too short (${account.data.length}B)`;
    if (!state.baseMint.equals(mint)) return `derived pool ${tag} base-mint mismatch`;
    if (state.quoteMint.toBase58() !== WRAPPED_SOL_MINT) return `derived pool ${tag} quote is not WSOL`;
    const globalConfig = await getCachedPumpSwapGlobalConfigAccount();
    if (!readProtocolFeeRecipientFromGlobalConfig(globalConfig)) {
      return `derived pool ${tag} is valid but PumpSwap global config / protocol fee recipient is unreadable on read RPC`;
    }
    return `derived pool ${tag} resolves and validates — account plan should be available (stale cache? rebuild/restart the bot)`;
  } catch (err) {
    return `pool diagnostic error: ${err instanceof Error ? err.message : String(err)}`;
  }
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
  poolHint?: PublicKey | null;
}): Promise<AccountMeta[] | null> {
  const pool = await findPumpSwapPoolForMint(args.mint, args.poolHint);
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
  poolHint?: PublicKey | null;
}): Promise<AccountMeta[] | null> {
  const pool = await findPumpSwapPoolForMint(args.mint, args.poolHint);
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
  buyLockOwner?: string;
  sourceSolInLamports: number;
  sourceTokensOutRaw?: number;
  sourcePumpSwapInstruction?: PumpSwapBuyInstruction;
  sourcePumpSwapAccounts?: SourcePumpSwapAccountMeta[] | string[];
  quoteMint?: string;
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
  const quoteMint = args.quoteMint ?? WRAPPED_SOL_MINT;
  if (quoteMint !== WRAPPED_SOL_MINT && quoteMint !== USDC_MINT) {
    return { ok: false, recoverable: false, reason: "unsupported PumpSwap quote mint" };
  }
  if (args.sourceSolInLamports <= 0) {
    return { ok: false, recoverable: true, reason: "source PumpSwap spend unavailable" };
  }

  const elapsed = () => `${Date.now() - args.startedAt}ms`;
  const mintKey = new PublicKey(args.mint);
  const mintOwnerCacheHitBeforeResolve = pumpSwapMintOwnerCache.has(mintKey.toBase58());
  const planCacheHitBeforeResolve = pumpSwapBuyAccountPlanByMint.has(mintKey.toBase58());
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
    quoteMint,
    canonicalOnly: (args.canonicalRetryAttempt ?? 0) > 0,
  });
  if (!accountPlan) {
    const diag = await describePumpSwapPoolResolution(mintKey);
    args.log(`PumpSwap buy account plan unavailable for ${args.mint.slice(0, 8)}…: ${diag}`);
    return {
      ok: false,
      recoverable: true,
      reason: `PumpSwap account plan unavailable: ${diag}`,
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
    quoteMint: new PublicKey(quoteMint),
  });

  if (accounts.length < PUMPSWAP_MIN_BUY_ACCOUNT_COUNT) {
    return { ok: false, recoverable: true, reason: `PumpSwap account list too short: ${accounts.length}` };
  }

  const poolKey = accounts[0]!.pubkey.toBase58();
  const poolCacheHitBeforeFetch = pumpSwapPoolAccountCache.has(poolKey);
  const prewarmInfo = pumpSwapPrewarmByMint.get(mintKey.toBase58()) ?? null;
  const prewarmFresh =
    prewarmInfo != null &&
    Date.now() - prewarmInfo.cachedAt <= pumpSwapPrewarmTtlMs &&
    (!prewarmInfo.pool || prewarmInfo.pool === poolKey);
  const poolAccount = await getCachedPumpSwapPoolAccount(
    accounts[0]!.pubkey,
    (args.poolRefreshAttempt ?? 0) > 0
  );
  const poolError = validatePumpSwapPoolAccount(accounts[0]!.pubkey, poolAccount);
  if (poolError) return { ok: false, recoverable: true, reason: poolError };
  const poolState = readPoolStateFromAccount(accounts[0]!.pubkey, poolAccount);
  if (!poolState || !poolState.baseMint.equals(mintKey) || poolState.quoteMint.toBase58() !== quoteMint) {
    return { ok: false, recoverable: false, reason: "PumpSwap pool state does not match requested base/quote pair" };
  }

  // Guard every BigInt input: a blank/unparsed source amount is NaN, and BigInt(NaN)
  // throws "cannot be converted to a BigInt". This must not crash the buy — fall back to
  // the live pool quote below instead. (NaN here previously aborted graduated buys.)
  const srcTokensOut = args.sourceTokensOutRaw;
  const solIn = args.solInLamports;
  const srcSolIn = args.sourceSolInLamports;
  const sourceRatioOut =
    typeof srcTokensOut === "number" && Number.isFinite(srcTokensOut) && srcTokensOut > 0 &&
    typeof solIn === "number" && Number.isFinite(solIn) && solIn > 0 &&
    typeof srcSolIn === "number" && Number.isFinite(srcSolIn) && srcSolIn > 0
      ? (BigInt(Math.floor(srcTokensOut)) * BigInt(Math.floor(solIn))) /
        BigInt(Math.floor(srcSolIn))
      : null;
  const usePrewarmSourceRatio =
    pumpSwapPrewarmUseSourceRatio &&
    prewarmFresh &&
    sourceRatioOut != null &&
    sourceRatioOut > 0n;
  const quoteRpcReadsInBuild = usePrewarmSourceRatio ? 0 : 1;
  const rpcReadsInBuild =
    (mintOwnerCacheHitBeforeResolve ? 0 : 1) +
    (planCacheHitBeforeResolve ? 0 : 1) +
    (poolCacheHitBeforeFetch ? 0 : 1) +
    quoteRpcReadsInBuild;
  const livePoolOut = usePrewarmSourceRatio
    ? null
    : await estimatePumpSwapBuyQuoteFromPool({
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
  const buyInstruction = args.buyInstruction ?? "buy_exact_quote_in";

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
  if (usePrewarmSourceRatio) {
    args.log(
      `PumpSwap prewarm fast quote: using source ratio for ${args.mint.slice(0, 8)}... ` +
        `(planCacheHit=${planCacheHitBeforeResolve}, poolCacheHit=${poolCacheHitBeforeFetch}).`
    );
  }

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

  const rustAccountsForPlan = (plan: PumpSwapAccountPlan) =>
    plan.accounts.map((account) => ({
      pubkey: account.pubkey.toBase58(),
      isSigner: account.isSigner,
      isWritable: account.isWritable,
    }));
  const overflowRetryBuyInstruction: PumpSwapBuyInstruction = "buy_exact_quote_in";
  const overflowRetryMinBaseOut =
    PUMPSWAP_OVERFLOW_RETRY_MIN_OUT_BPS > 0
      ? (expectedBaseOut * BigInt(PUMPSWAP_OVERFLOW_RETRY_MIN_OUT_BPS)) / 10_000n
      : minBaseOut;
  const safeOverflowRetryMinBaseOut = overflowRetryMinBaseOut > 0n ? overflowRetryMinBaseOut : minBaseOut;
  const executeRustAttempt = async (
    attemptInstruction: PumpSwapBuyInstruction,
    attempt: "primary" | "overflow-retry" | "canonical-overflow-retry",
    plan: PumpSwapAccountPlan = accountPlan,
    sourceSignature: string = attempt === "primary" ? args.sourceSignature : `${args.sourceSignature}:${attempt}`,
    minBaseOutForAttempt: bigint = minBaseOut
  ) => {
    const rustBuildStartedAt = Date.now();
    const rust = await executeRustPumpSwapBuy({
      route: "pumpswap-direct-buy",
      mint: args.mint,
      sourceSignature,
      buyLockOwner: args.buyLockOwner,
      quoteMint,
      quoteAmountInRaw: args.solInLamports,
      expectedBaseOutRaw: Number(expectedBaseOut),
      minBaseOutRaw: Number(minBaseOutForAttempt),
      slippageBps: args.slippageBps,
      buyInstruction: attemptInstruction,
      sourcePumpSwapAccounts: rustAccountsForPlan(plan),
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
          accountPlan: plan.source,
          buyInstruction: attemptInstruction,
          attempt,
          mintOwnerCacheHit: mintOwnerCacheHitBeforeResolve,
          planCacheHit: planCacheHitBeforeResolve,
          poolCacheHit: poolCacheHitBeforeFetch,
          prewarmFresh,
          usedSourceRatioQuote: usePrewarmSourceRatio,
          skippedLiveReserveRead: usePrewarmSourceRatio,
          quoteRpcReadsInBuild,
          rpcReadsInBuild,
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
          accountPlan: plan.source,
          buyInstruction: attemptInstruction,
          attempt,
          mintOwnerCacheHit: mintOwnerCacheHitBeforeResolve,
          planCacheHit: planCacheHitBeforeResolve,
          poolCacheHit: poolCacheHitBeforeFetch,
          prewarmFresh,
          usedSourceRatioQuote: usePrewarmSourceRatio,
          quoteRpcReadsInBuild,
          rpcReadsInBuild,
        },
      });
      args.log(
        `Rust executor PumpSwap buy broadcast${attempt === "overflow-retry" ? " (overflow retry)" : attempt === "canonical-overflow-retry" ? " (canonical overflow retry)" : ""}: ` +
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
    attempt: "primary" | "overflow-retry" | "canonical-overflow-retry",
    plan: PumpSwapAccountPlan = accountPlan
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
          accountPlan: plan.source,
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
          accountPlan: plan.source,
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
    const retryWouldRepeatPrimary =
      accountPlan.source === "canonical-pool-state" &&
      buyInstruction === overflowRetryBuyInstruction &&
      safeOverflowRetryMinBaseOut === minBaseOut;
    if (retryWouldRepeatPrimary) {
      args.log(
        `PumpSwap overflow retry skipped: canonical ${buyInstruction} already failed with unchanged minOut=${minBaseOut}.`
      );
      return null;
    }
    releaseRustBoughtMint(args.mint, `PumpSwap overflow retry after ${buyInstruction} failed: ${originalMsg}`);
    args.log(
      `PumpSwap Rust overflow retry: ${buyInstruction} failed with 6023; ` +
        `retrying ${overflowRetryBuyInstruction} with ${
          PUMPSWAP_OVERFLOW_RETRY_MIN_OUT_BPS > 0
            ? `minOutBps=${PUMPSWAP_OVERFLOW_RETRY_MIN_OUT_BPS}`
            : `slippage minOut=${safeOverflowRetryMinBaseOut}`
        }.`
    );
    const retry = await executeRustAttempt(
      overflowRetryBuyInstruction,
      "overflow-retry",
      accountPlan,
      `${args.sourceSignature}:overflow-retry`,
      safeOverflowRetryMinBaseOut
    );
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
      overflowRetryBuyInstruction,
      "overflow-retry"
    );
    if (confirmed.ok) return retry.signature;
    if (!isPumpSwapOverflowError(confirmed.message) || accountPlan.source === "canonical-pool-state") {
      return null;
    }

    releaseRustBoughtMint(args.mint, `PumpSwap canonical overflow retry after ${overflowRetryBuyInstruction} failed: ${confirmed.message}`);
    const canonicalPlan = await resolvePumpSwapBuyAccountPlan({
      sourceSignature: args.sourceSignature,
      mint: mintKey,
      user: args.user.publicKey,
      baseTokenProgram: baseTokenProgramId,
      sourcePumpSwapAccounts: args.sourcePumpSwapAccounts,
      quoteMint,
      canonicalOnly: true,
    });
    if (!canonicalPlan) {
      args.log("PumpSwap canonical overflow retry unavailable: canonical account plan could not be built.");
      return null;
    }
    args.log(
      `PumpSwap canonical overflow retry: ${overflowRetryBuyInstruction} still failed with 6023; ` +
        `retrying buy_exact_quote_in with ${canonicalPlan.source}.`
    );
    const canonicalRetry = await executeRustAttempt(
      "buy_exact_quote_in",
      "canonical-overflow-retry",
      canonicalPlan,
      `${args.sourceSignature}:canonical-overflow-retry`,
      safeOverflowRetryMinBaseOut
    );
    if (!canonicalRetry?.ok) {
      args.log(
        `PumpSwap canonical overflow retry failed before confirmation: ` +
          `${canonicalRetry && !canonicalRetry.ok ? canonicalRetry.error : "Rust PumpSwap executor unavailable"}`
      );
      return null;
    }
    const canonicalConfirmed = await confirmRustAttempt(
      canonicalRetry.signature,
      mode,
      "buy_exact_quote_in",
      "canonical-overflow-retry",
      canonicalPlan
    );
    return canonicalConfirmed.ok ? canonicalRetry.signature : null;
  };

  const rust = await executeRustAttempt(buyInstruction, "primary");
  if (rust?.ok) {
    const signature = rust.signature;
    rememberPumpSwapBuyAccountPlan(mintKey, accountPlan.accounts);
    if (fastMode && quoteMint === WRAPPED_SOL_MINT) {
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
          rememberPumpSwapBuyAccountPlan(mintKey, accountPlan.accounts);
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
          args.onConfirmFailure?.(
            args.mint,
            Number(expectedBaseOut),
            args.solInLamports,
            `PumpSwap ${accountPlan.source} confirmation failed: ${msg}`
          );
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
        return {
          ok: false,
          recoverable: true,
          reason: `PumpSwap ${accountPlan.source} Rust confirmation failed: ${confirmed.message}`,
        };
      }
      finalSignature = retrySignature;
    }
    rememberPumpSwapBuyAccountPlan(mintKey, accountPlan.accounts);
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
    return {
      ok: false,
      recoverable: isRecoverablePumpSwapFailure(reason),
      reason: `Rust-only PumpSwap buy failed: ${reason}`,
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
  computeIxs.push(ComputeBudgetProgram.setComputeUnitPrice({ microLamports: getDynamicPriorityFeeForRoute("pumpswap-direct-buy") }));

  const swapIx =
    buyInstruction === "buy"
      ? buildNativeIdlInstructionFromOrderedAccounts({
          program: "pump_amm",
          instruction: "buy",
          accounts,
          data: encodeBuy(expectedBaseOut, BigInt(args.solInLamports)),
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
    const diag = await describePumpSwapPoolResolution(mintKey);
    args.log(`PumpSwap sell account plan unavailable for ${args.mint.slice(0, 8)}…: ${diag}`);
    return { ok: false, recoverable: true, reason: `canonical PumpSwap sell accounts unavailable: ${diag}` };
  }
  args.log(`PumpSwap sell account plan: ${accountPlan.source}`);

  const { accounts, userQuoteAta } = replacePumpSwapUserAccounts({
    accounts: accountPlan.accounts,
    user: args.user.publicKey,
    baseMint: mintKey,
    baseTokenProgram: baseTokenProgramId,
    replaceUserVolumeAccumulator: false, // sells have no user_volume_accumulator
    replaceSellCashbackAccounts: true,
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

  // Pass the raw (template) account list to the Rust executor; it performs the
  // single, side-correct user substitution (and does NOT touch index 20 for
  // sells). Mirrors the buy path, which also hands Rust the raw plan.
  const rustSourcePumpSwapAccounts = accountPlan.accounts.map((account) => ({
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
  computeIxs.push(ComputeBudgetProgram.setComputeUnitPrice({ microLamports: getDynamicPriorityFeeForRoute("pumpswap-direct-sell") }));

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
