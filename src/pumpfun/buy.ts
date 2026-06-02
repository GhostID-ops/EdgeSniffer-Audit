import {
  PublicKey,
  Keypair,
  TransactionInstruction,
  VersionedTransaction,
  SystemProgram,
  ComputeBudgetProgram,
  type AccountMeta,
} from "@solana/web3.js";
import {
  readConnection,
  computeUnitLimit,
  pumpfunExactSolInBuys,
} from "../config.js";
import { getDynamicPriorityFee } from "../feeOracle.js";
import { getCachedBlockhash } from "../blockhashCache.js";
import { buildCompactV0Transaction } from "../transactionSize.js";
import {
  PUMP_FUN_PROGRAM_ID,
  PUMP_FUN_FEE_PROGRAM_ID,
  PUMP_FUN_FEE_RECIPIENT,
  PUMP_FUN_BUYBACK_FEE_RECIPIENT,
  ASSOCIATED_TOKEN_PROGRAM_ID,
  SYSTEM_PROGRAM_ID,
  TOKEN_PROGRAM_ID,
  TOKEN_2022_PROGRAM_ID,
  BUY_DISCRIMINATOR,
  BUY_EXACT_SOL_IN_DISCRIMINATOR,
  BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR,
  BUY_V2_DISCRIMINATOR,
  deriveAta,
  deriveCreatorVaultPda,
  deriveEventAuthorityPda,
  deriveFeeConfigPda,
  deriveGlobalPda,
  deriveGlobalVolumeAccumulatorPda,
  deriveUserVolumeAccumulatorPda,
  deriveSharingConfigPda,
} from "./constants.js";

const WRAPPED_SOL_MINT = new PublicKey("So11111111111111111111111111111111111111112");

function encodeBuyArgs(amount: bigint, maxSolCost: bigint): Buffer {
  const buf = Buffer.alloc(25);
  BUY_DISCRIMINATOR.copy(buf, 0);
  buf.writeBigUInt64LE(amount, 8);
  buf.writeBigUInt64LE(maxSolCost, 16);
  buf.writeUInt8(0, 24); // OptionBool::None for track_volume.
  return buf;
}

function encodeBuyExactSolInArgs(spendableSolIn: bigint, minTokensOut: bigint): Buffer {
  const buf = Buffer.alloc(25);
  BUY_EXACT_SOL_IN_DISCRIMINATOR.copy(buf, 0);
  buf.writeBigUInt64LE(spendableSolIn, 8);
  buf.writeBigUInt64LE(minTokensOut, 16);
  buf.writeUInt8(0, 24); // OptionBool::None for track_volume.
  return buf;
}

function encodeBuyV2Args(amount: bigint, maxSolCost: bigint): Buffer {
  const buf = Buffer.alloc(24);
  BUY_V2_DISCRIMINATOR.copy(buf, 0);
  buf.writeBigUInt64LE(amount, 8);
  buf.writeBigUInt64LE(maxSolCost, 16);
  return buf;
}

function encodeBuyExactQuoteInV2Args(spendableQuoteIn: bigint, minTokensOut: bigint): Buffer {
  const buf = Buffer.alloc(24);
  BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR.copy(buf, 0);
  buf.writeBigUInt64LE(spendableQuoteIn, 8);
  buf.writeBigUInt64LE(minTokensOut, 16);
  return buf;
}

function dataMatches(data: Buffer, discriminator: Buffer): boolean {
  return data.length >= discriminator.length && data.subarray(0, discriminator.length).equals(discriminator);
}

function scaleU64(value: bigint, numerator: bigint, denominator: bigint): bigint {
  if (denominator <= 0n) throw new Error("Cannot scale source pump.fun instruction with zero source amount");
  const scaled = (value * numerator) / denominator;
  return scaled > 0n ? scaled : 1n;
}

function tokenProgramFromSourceIx(sourceIxAccounts: PublicKey[], mint: PublicKey): PublicKey {
  if (isV2SourceIxAccounts(sourceIxAccounts, mint)) {
    const tokenProgram = sourceIxAccounts[3];
    if (!tokenProgram) throw new Error("Pump.fun source V2 ix missing base token program account");
    return tokenProgram;
  }
  const tokenProgram = sourceIxAccounts[8];
  if (!tokenProgram) throw new Error("Pump.fun source legacy ix missing token program account");
  return tokenProgram;
}

export function scalePumpFunSourceBuyData(args: {
  sourceIxData: Buffer;
  solInLamports: bigint;
  slippageBps: number;
}): { exactSolIn: boolean; amount: bigint; maxSolCost: bigint; estimatedTokensOut: bigint } {
  const data = args.sourceIxData;
  const slippageBps = BigInt(Math.max(0, Math.min(10_000, args.slippageBps)));

  if (dataMatches(data, BUY_DISCRIMINATOR) || dataMatches(data, BUY_V2_DISCRIMINATOR)) {
    if (data.length < 24) throw new Error("Source pump.fun buy data is too short");
    const sourceTokensOut = data.readBigUInt64LE(8);
    const sourceMaxSol = data.readBigUInt64LE(16);
    const amount = scaleU64(sourceTokensOut, args.solInLamports, sourceMaxSol);
    const maxSolCost = (args.solInLamports * (10_000n + slippageBps)) / 10_000n;
    return { exactSolIn: false, amount, maxSolCost, estimatedTokensOut: amount };
  }

  if (
    dataMatches(data, BUY_EXACT_SOL_IN_DISCRIMINATOR) ||
    dataMatches(data, BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR)
  ) {
    if (data.length < 24) throw new Error("Source pump.fun exact-in data is too short");
    const sourceSpend = data.readBigUInt64LE(8);
    const sourceMinTokensOut = data.readBigUInt64LE(16);
    const scaledMin = scaleU64(sourceMinTokensOut, args.solInLamports, sourceSpend);
    const minTokensOut = (scaledMin * (10_000n - slippageBps)) / 10_000n;
    return {
      exactSolIn: true,
      amount: minTokensOut > 0n ? minTokensOut : 1n,
      maxSolCost: args.solInLamports,
      estimatedTokensOut: minTokensOut > 0n ? minTokensOut : 1n,
    };
  }

  throw new Error("Source pump.fun instruction is not a supported buy layout");
}

/**
 * Standard pump.fun buy ix writable-flag map. Hardcoded because flags from a source CPI
 * reflect the caller's permission grant, not the program's actual requirements — we always
 * set the flags pump.fun's IDL needs regardless of what the source's tx looked like.
 *
 *   slot 0  global               — readonly
 *   slot 1  fee_recipient        — writable (receives SOL fee)
 *   slot 2  mint                 — readonly
 *   slot 3  bonding_curve        — writable (state mutated)
 *   slot 4  associated_bonding_c — writable (tokens transferred out)
 *   slot 5  associated_user      — writable (tokens transferred in)  ← REPLACE
 *   slot 6  user                 — writable+signer                   ← REPLACE
 *   slot 7  system_program       — readonly
 *   slot 8  token_program        — readonly
 *   slot 9  creator_vault        — writable (creator SOL share)
 *   slot 10 event_authority      — readonly
 *   slot 11 pump.fun program     — readonly
 *   slot 12 global_volume_acc    — writable
 *   slot 13 user_volume_acc      — writable                          ← REPLACE
 *   slot 14 fee_config           — readonly
 *   slot 15 fee_program          — readonly
 *   slot 16+ remaining (per-coin recipients) — writable
 */
const PUMP_FUN_BUY_WRITABLE_FLAGS = [
  false, true, false, true, true, true, true, false,
  false, true, false, false, true, true, false, false,
];

const PUMP_FUN_BUY_V2_WRITABLE_FLAGS = [
  false, false, false, false, false, false, true, true, true,
  true, true, true, true, true, true, true, true, true,
  false, false, true, true, false, false, false, false, false,
];
const PUMP_FUN_BUY_V2_ACCOUNT_COUNT = PUMP_FUN_BUY_V2_WRITABLE_FLAGS.length;

function isV2SourceIxAccounts(
  sourceIxAccounts: PublicKey[] | undefined,
  mint: PublicKey
): boolean {
  return (
    sourceIxAccounts !== undefined &&
    sourceIxAccounts.length >= 27 &&
    sourceIxAccounts[1]?.equals(mint) === true &&
    sourceIxAccounts[26]?.equals(PUMP_FUN_PROGRAM_ID) === true
  );
}

export function buildPumpFunBuyIx(args: {
  user: PublicKey;
  mint: PublicKey;
  bondingCurve: PublicKey;
  creator: PublicKey;
  tokenProgramId: PublicKey;
  amount: bigint;
  maxSolCost: bigint;
  exactSolIn?: boolean;
  remainingAccounts?: AccountMeta[];
  /**
   * Source's exact pump.fun buy ix account list (16-18 entries). When provided, slots 0-15
   * use the source's pubkeys (so per-coin rotating recipients at fix1 are correct), with
   * only slots 5, 6, 13 substituted to our user's accounts. Slot 16+ are treated as the
   * remaining accounts (overriding the `remainingAccounts` arg). Without this, the bot
   * builds from constants/PDAs only — fine for classic SPL, broken for newer Token-2022
   * launches where fix1/REM0/REM1 rotate per-coin.
   */
  sourceIxAccounts?: PublicKey[];
}): TransactionInstruction {
  const associatedUser = deriveAta(args.user, args.mint, args.tokenProgramId);
  const userVolumeAccumulator = deriveUserVolumeAccumulatorPda(args.user);
  const associatedQuoteUser = deriveAta(args.user, WRAPPED_SOL_MINT, TOKEN_PROGRAM_ID);
  const associatedUserVolumeAccumulator = deriveAta(
    userVolumeAccumulator,
    WRAPPED_SOL_MINT,
    TOKEN_PROGRAM_ID
  );

  let keys: AccountMeta[];
  const isV2 =
    isV2SourceIxAccounts(args.sourceIxAccounts, args.mint) ||
    args.tokenProgramId.equals(TOKEN_2022_PROGRAM_ID);
  if (isV2 && args.sourceIxAccounts) {
    // V2 buy layout has no optional accounts but includes multiple user-specific slots.
    // Keep the 27 official Pump.fun accounts verbatim and replace only our wallet-owned
    // accounts. Routers may pass extra CPI accounts after slot 26; forwarding those makes
    // direct V2 buys exceed Solana's 1232-byte transaction cap.
    const src = args.sourceIxAccounts.slice(0, PUMP_FUN_BUY_V2_ACCOUNT_COUNT);
    keys = src.map((pubkey, i) => {
      let actual = pubkey;
      if (i === 13) actual = args.user;
      else if (i === 14) actual = associatedUser;
      else if (i === 15) actual = associatedQuoteUser;
      else if (i === 20) actual = userVolumeAccumulator;
      else if (i === 21) actual = associatedUserVolumeAccumulator;
      return {
        pubkey: actual,
        isSigner: i === 13,
        isWritable: PUMP_FUN_BUY_V2_WRITABLE_FLAGS[i] ?? true,
      };
    });
  } else if (args.sourceIxAccounts && args.sourceIxAccounts.length >= 16) {
    // Source-extracted path: use source's exact slots 0-15, replace user-specific slots.
    // Slot 16+ are the per-coin remaining accounts (fee recipients) — keep verbatim.
    const src = args.sourceIxAccounts;
    keys = src.slice(0, 16).map((pubkey, i) => {
      let actual = pubkey;
      if (i === 5) actual = associatedUser;
      else if (i === 6) actual = args.user;
      else if (i === 13) actual = userVolumeAccumulator;
      return {
        pubkey: actual,
        isSigner: i === 6,
        isWritable: PUMP_FUN_BUY_WRITABLE_FLAGS[i]!,
      };
    });
    for (let i = 16; i < src.length; i++) {
      keys.push({ pubkey: src[i]!, isSigner: false, isWritable: true });
    }
  } else if (isV2) {
    const global = deriveGlobalPda();
    const eventAuthority = deriveEventAuthorityPda();
    const associatedBaseBondingCurve = deriveAta(args.bondingCurve, args.mint, args.tokenProgramId);
    const associatedQuoteBondingCurve = deriveAta(args.bondingCurve, WRAPPED_SOL_MINT, TOKEN_PROGRAM_ID);
    const creatorVault = deriveCreatorVaultPda(args.creator);
    const associatedCreatorVault = deriveAta(creatorVault, WRAPPED_SOL_MINT, TOKEN_PROGRAM_ID);
    const associatedQuoteFeeRecipient = deriveAta(PUMP_FUN_FEE_RECIPIENT, WRAPPED_SOL_MINT, TOKEN_PROGRAM_ID);
    const associatedQuoteBuybackFeeRecipient = deriveAta(
      PUMP_FUN_BUYBACK_FEE_RECIPIENT,
      WRAPPED_SOL_MINT,
      TOKEN_PROGRAM_ID
    );
    const sharingConfig = deriveSharingConfigPda(args.mint);
    const globalVolumeAccumulator = deriveGlobalVolumeAccumulatorPda();
    const feeConfig = deriveFeeConfigPda();
    keys = [
      { pubkey: global, isSigner: false, isWritable: false },
      { pubkey: args.mint, isSigner: false, isWritable: false },
      { pubkey: WRAPPED_SOL_MINT, isSigner: false, isWritable: false },
      { pubkey: args.tokenProgramId, isSigner: false, isWritable: false },
      { pubkey: TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
      { pubkey: ASSOCIATED_TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
      { pubkey: PUMP_FUN_FEE_RECIPIENT, isSigner: false, isWritable: true },
      { pubkey: associatedQuoteFeeRecipient, isSigner: false, isWritable: true },
      { pubkey: PUMP_FUN_BUYBACK_FEE_RECIPIENT, isSigner: false, isWritable: true },
      { pubkey: associatedQuoteBuybackFeeRecipient, isSigner: false, isWritable: true },
      { pubkey: args.bondingCurve, isSigner: false, isWritable: true },
      { pubkey: associatedBaseBondingCurve, isSigner: false, isWritable: true },
      { pubkey: associatedQuoteBondingCurve, isSigner: false, isWritable: true },
      { pubkey: args.user, isSigner: true, isWritable: true },
      { pubkey: associatedUser, isSigner: false, isWritable: true },
      { pubkey: associatedQuoteUser, isSigner: false, isWritable: true },
      { pubkey: creatorVault, isSigner: false, isWritable: true },
      { pubkey: associatedCreatorVault, isSigner: false, isWritable: true },
      { pubkey: sharingConfig, isSigner: false, isWritable: false },
      { pubkey: globalVolumeAccumulator, isSigner: false, isWritable: false },
      { pubkey: userVolumeAccumulator, isSigner: false, isWritable: true },
      { pubkey: associatedUserVolumeAccumulator, isSigner: false, isWritable: true },
      { pubkey: feeConfig, isSigner: false, isWritable: false },
      { pubkey: PUMP_FUN_FEE_PROGRAM_ID, isSigner: false, isWritable: false },
      { pubkey: SYSTEM_PROGRAM_ID, isSigner: false, isWritable: false },
      { pubkey: eventAuthority, isSigner: false, isWritable: false },
      { pubkey: PUMP_FUN_PROGRAM_ID, isSigner: false, isWritable: false },
    ];
  } else {
    // Legacy derived path — works for classic SPL pump.fun where slots are all constants/PDAs.
    const global = deriveGlobalPda();
    const eventAuthority = deriveEventAuthorityPda();
    const associatedBondingCurve = deriveAta(args.bondingCurve, args.mint, args.tokenProgramId);
    const creatorVault = deriveCreatorVaultPda(args.creator);
    const globalVolumeAccumulator = deriveGlobalVolumeAccumulatorPda();
    const feeConfig = deriveFeeConfigPda();
    keys = [
      { pubkey: global, isSigner: false, isWritable: false },
      { pubkey: PUMP_FUN_FEE_RECIPIENT, isSigner: false, isWritable: true },
      { pubkey: args.mint, isSigner: false, isWritable: false },
      { pubkey: args.bondingCurve, isSigner: false, isWritable: true },
      { pubkey: associatedBondingCurve, isSigner: false, isWritable: true },
      { pubkey: associatedUser, isSigner: false, isWritable: true },
      { pubkey: args.user, isSigner: true, isWritable: true },
      { pubkey: SYSTEM_PROGRAM_ID, isSigner: false, isWritable: false },
      { pubkey: args.tokenProgramId, isSigner: false, isWritable: false },
      { pubkey: creatorVault, isSigner: false, isWritable: true },
      { pubkey: eventAuthority, isSigner: false, isWritable: false },
      { pubkey: PUMP_FUN_PROGRAM_ID, isSigner: false, isWritable: false },
      { pubkey: globalVolumeAccumulator, isSigner: false, isWritable: false },
      { pubkey: userVolumeAccumulator, isSigner: false, isWritable: true },
      { pubkey: feeConfig, isSigner: false, isWritable: false },
      { pubkey: PUMP_FUN_FEE_PROGRAM_ID, isSigner: false, isWritable: false },
      ...(args.remainingAccounts ?? []),
    ];
  }

  return new TransactionInstruction({
    keys,
    programId: PUMP_FUN_PROGRAM_ID,
    data: isV2
      ? args.exactSolIn
        ? encodeBuyExactQuoteInV2Args(args.maxSolCost, args.amount)
        : encodeBuyV2Args(args.amount, args.maxSolCost)
      : args.exactSolIn
        ? encodeBuyExactSolInArgs(args.maxSolCost, args.amount)
        : encodeBuyArgs(args.amount, args.maxSolCost),
  });
}

export function buildCreateAtaIdempotentIx(args: {
  payer: PublicKey;
  owner: PublicKey;
  mint: PublicKey;
  tokenProgramId: PublicKey;
}): TransactionInstruction {
  const ata = deriveAta(args.owner, args.mint, args.tokenProgramId);
  const keys = [
    { pubkey: args.payer, isSigner: true, isWritable: true },
    { pubkey: ata, isSigner: false, isWritable: true },
    { pubkey: args.owner, isSigner: false, isWritable: false },
    { pubkey: args.mint, isSigner: false, isWritable: false },
    { pubkey: SYSTEM_PROGRAM_ID, isSigner: false, isWritable: false },
    { pubkey: args.tokenProgramId, isSigner: false, isWritable: false },
  ];
  return new TransactionInstruction({
    keys,
    programId: ASSOCIATED_TOKEN_PROGRAM_ID,
    data: Buffer.from([1]),
  });
}

export async function buildPumpFunBuyTransaction(args: {
  user: Keypair;
  mint: PublicKey;
  bondingCurve: PublicKey;
  creator: PublicKey;
  tokenProgramId: PublicKey;
  tokensOut: bigint;
  solInLamports: bigint;
  slippageBps: number;
  remainingAccounts?: AccountMeta[];
  sourceIxAccounts?: PublicKey[];
}): Promise<VersionedTransaction> {
  const maxSolCost =
    (args.solInLamports * BigInt(10_000 + args.slippageBps)) / 10_000n;
  const minTokensOut =
    (args.tokensOut * BigInt(Math.max(1, 10_000 - args.slippageBps))) / 10_000n;

  const computeIxs: TransactionInstruction[] = [];
  if (computeUnitLimit > 0) {
    computeIxs.push(
      ComputeBudgetProgram.setComputeUnitLimit({
        units: Math.min(computeUnitLimit, 1_400_000),
      })
    );
  }
  computeIxs.push(
    ComputeBudgetProgram.setComputeUnitPrice({
      microLamports: getDynamicPriorityFee(),
    })
  );

  const ataIx = buildCreateAtaIdempotentIx({
    payer: args.user.publicKey,
    owner: args.user.publicKey,
    mint: args.mint,
    tokenProgramId: args.tokenProgramId,
  });
  const isV2 =
    isV2SourceIxAccounts(args.sourceIxAccounts, args.mint) ||
    args.tokenProgramId.equals(TOKEN_2022_PROGRAM_ID);

  const buyIx = buildPumpFunBuyIx({
    user: args.user.publicKey,
    mint: args.mint,
    bondingCurve: args.bondingCurve,
    creator: args.creator,
    tokenProgramId: args.tokenProgramId,
    amount: pumpfunExactSolInBuys ? minTokensOut : args.tokensOut,
    maxSolCost: pumpfunExactSolInBuys ? args.solInLamports : maxSolCost,
    exactSolIn: pumpfunExactSolInBuys,
    remainingAccounts: args.remainingAccounts,
    sourceIxAccounts: args.sourceIxAccounts,
  });
  const minAccounts = isV2 ? 27 : 16;
  if (buyIx.keys.length < minAccounts) {
    throw new Error(`Pump.fun buy instruction is stale: expected at least ${minAccounts} accounts, got ${buyIx.keys.length}`);
  }

  const { blockhash } = await getCachedBlockhash();
  return buildCompactV0Transaction({
    payer: args.user.publicKey,
    blockhash,
    instructions: [...computeIxs, ataIx, buyIx],
    signer: args.user,
  });
}

export async function buildPumpFunBuyReplayTransaction(args: {
  user: Keypair;
  mint: PublicKey;
  solInLamports: bigint;
  slippageBps: number;
  sourceIxAccounts: PublicKey[];
  sourceIxData: Buffer;
}): Promise<{ tx: VersionedTransaction; estimatedTokensOut: bigint }> {
  if (args.sourceIxAccounts.length < 16) {
    throw new Error("Pump.fun source replay requires source instruction accounts");
  }
  const tokenProgramId = tokenProgramFromSourceIx(args.sourceIxAccounts, args.mint);
  const scaled = scalePumpFunSourceBuyData({
    sourceIxData: args.sourceIxData,
    solInLamports: args.solInLamports,
    slippageBps: args.slippageBps,
  });

  const computeIxs: TransactionInstruction[] = [];
  if (computeUnitLimit > 0) {
    computeIxs.push(
      ComputeBudgetProgram.setComputeUnitLimit({
        units: Math.min(computeUnitLimit, 1_400_000),
      })
    );
  }
  computeIxs.push(
    ComputeBudgetProgram.setComputeUnitPrice({
      microLamports: getDynamicPriorityFee(),
    })
  );

  const ataIx = buildCreateAtaIdempotentIx({
    payer: args.user.publicKey,
    owner: args.user.publicKey,
    mint: args.mint,
    tokenProgramId,
  });

  const buyIx = buildPumpFunBuyIx({
    user: args.user.publicKey,
    mint: args.mint,
    bondingCurve: args.sourceIxAccounts[10] ?? args.sourceIxAccounts[3] ?? args.mint,
    creator: args.sourceIxAccounts[16] ?? args.user.publicKey,
    tokenProgramId,
    amount: scaled.amount,
    maxSolCost: scaled.maxSolCost,
    exactSolIn: scaled.exactSolIn,
    sourceIxAccounts: args.sourceIxAccounts,
  });

  const { blockhash } = await getCachedBlockhash();
  const tx = buildCompactV0Transaction({
    payer: args.user.publicKey,
    blockhash,
    instructions: [...computeIxs, ataIx, buyIx],
    signer: args.user,
  });
  return { tx, estimatedTokensOut: scaled.estimatedTokensOut };
}
