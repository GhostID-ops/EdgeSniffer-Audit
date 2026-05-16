import {
  PublicKey,
  Keypair,
  TransactionInstruction,
  TransactionMessage,
  VersionedTransaction,
  SystemProgram,
  ComputeBudgetProgram,
  type AccountMeta,
} from "@solana/web3.js";
import {
  readConnection,
  TIP_ACCOUNTS,
  tipLamports,
  computeUnitLimit,
} from "../config.js";
import { getDynamicPriorityFee } from "../feeOracle.js";
import { getCachedBlockhash } from "../blockhashCache.js";
import {
  PUMP_FUN_PROGRAM_ID,
  PUMP_FUN_FEE_PROGRAM_ID,
  PUMP_FUN_FEE_RECIPIENT,
  SYSTEM_PROGRAM_ID,
  SELL_DISCRIMINATOR,
  SELL_V2_DISCRIMINATOR,
  ASSOCIATED_TOKEN_PROGRAM_ID,
  TOKEN_PROGRAM_ID,
  deriveAta,
  deriveCreatorVaultPda,
  deriveEventAuthorityPda,
  deriveFeeConfigPda,
  deriveGlobalPda,
  deriveSharingConfigPda,
  deriveUserVolumeAccumulatorPda,
} from "./constants.js";
import { WRAPPED_SOL_MINT } from "../tokenRegistry.js";

function encodeSellArgs(amount: bigint, minSolOutput: bigint): Buffer {
  const buf = Buffer.alloc(25);
  SELL_DISCRIMINATOR.copy(buf, 0);
  buf.writeBigUInt64LE(amount, 8);
  buf.writeBigUInt64LE(minSolOutput, 16);
  buf.writeUInt8(0, 24); // OptionBool::None for track_volume.
  return buf;
}

function encodeSellV2Args(amount: bigint, minSolOutput: bigint): Buffer {
  const buf = Buffer.alloc(24);
  SELL_V2_DISCRIMINATOR.copy(buf, 0);
  buf.writeBigUInt64LE(amount, 8);
  buf.writeBigUInt64LE(minSolOutput, 16);
  return buf;
}

export function buildPumpFunSellIx(args: {
  user: PublicKey;
  mint: PublicKey;
  bondingCurve: PublicKey;
  creator: PublicKey;
  tokenProgramId: PublicKey;
  amount: bigint;
  minSolOutput: bigint;
  remainingAccounts?: AccountMeta[];
}): TransactionInstruction {
  const global = deriveGlobalPda();
  const eventAuthority = deriveEventAuthorityPda();
  const associatedBondingCurve = deriveAta(args.bondingCurve, args.mint, args.tokenProgramId);
  const associatedUser = deriveAta(args.user, args.mint, args.tokenProgramId);
  const creatorVault = deriveCreatorVaultPda(args.creator);
  const userVolumeAccumulator = deriveUserVolumeAccumulatorPda(args.user);
  const feeConfig = deriveFeeConfigPda();

  const keys = [
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
    { pubkey: userVolumeAccumulator, isSigner: false, isWritable: true },
    { pubkey: feeConfig, isSigner: false, isWritable: false },
    { pubkey: PUMP_FUN_FEE_PROGRAM_ID, isSigner: false, isWritable: false },
    ...(args.remainingAccounts ?? []),
  ];

  return new TransactionInstruction({
    keys,
    programId: PUMP_FUN_PROGRAM_ID,
    data: encodeSellArgs(args.amount, args.minSolOutput),
  });
}

export function buildPumpFunSellV2Ix(args: {
  user: PublicKey;
  mint: PublicKey;
  bondingCurve: PublicKey;
  creator: PublicKey;
  baseTokenProgramId: PublicKey;
  amount: bigint;
  minSolOutput: bigint;
  buybackFeeRecipient: PublicKey;
}): TransactionInstruction {
  const quoteMint = new PublicKey(WRAPPED_SOL_MINT);
  const global = deriveGlobalPda();
  const eventAuthority = deriveEventAuthorityPda();
  const associatedBaseBondingCurve = deriveAta(args.bondingCurve, args.mint, args.baseTokenProgramId);
  const associatedQuoteBondingCurve = deriveAta(args.bondingCurve, quoteMint, TOKEN_PROGRAM_ID);
  const associatedBaseUser = deriveAta(args.user, args.mint, args.baseTokenProgramId);
  const associatedQuoteUser = deriveAta(args.user, quoteMint, TOKEN_PROGRAM_ID);
  const creatorVault = deriveCreatorVaultPda(args.creator);
  const associatedCreatorVault = deriveAta(creatorVault, quoteMint, TOKEN_PROGRAM_ID);
  const associatedQuoteFeeRecipient = deriveAta(PUMP_FUN_FEE_RECIPIENT, quoteMint, TOKEN_PROGRAM_ID);
  const associatedQuoteBuybackFeeRecipient = deriveAta(args.buybackFeeRecipient, quoteMint, TOKEN_PROGRAM_ID);
  const sharingConfig = deriveSharingConfigPda(args.mint);
  const userVolumeAccumulator = deriveUserVolumeAccumulatorPda(args.user);
  const associatedUserVolumeAccumulator = deriveAta(userVolumeAccumulator, quoteMint, TOKEN_PROGRAM_ID);
  const feeConfig = deriveFeeConfigPda();

  const keys = [
    { pubkey: global, isSigner: false, isWritable: false },
    { pubkey: args.mint, isSigner: false, isWritable: false },
    { pubkey: quoteMint, isSigner: false, isWritable: false },
    { pubkey: args.baseTokenProgramId, isSigner: false, isWritable: false },
    { pubkey: TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
    { pubkey: ASSOCIATED_TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
    { pubkey: PUMP_FUN_FEE_RECIPIENT, isSigner: false, isWritable: true },
    { pubkey: associatedQuoteFeeRecipient, isSigner: false, isWritable: true },
    { pubkey: args.buybackFeeRecipient, isSigner: false, isWritable: true },
    { pubkey: associatedQuoteBuybackFeeRecipient, isSigner: false, isWritable: true },
    { pubkey: args.bondingCurve, isSigner: false, isWritable: true },
    { pubkey: associatedBaseBondingCurve, isSigner: false, isWritable: true },
    { pubkey: associatedQuoteBondingCurve, isSigner: false, isWritable: true },
    { pubkey: args.user, isSigner: true, isWritable: true },
    { pubkey: associatedBaseUser, isSigner: false, isWritable: true },
    { pubkey: associatedQuoteUser, isSigner: false, isWritable: true },
    { pubkey: creatorVault, isSigner: false, isWritable: true },
    { pubkey: associatedCreatorVault, isSigner: false, isWritable: true },
    { pubkey: sharingConfig, isSigner: false, isWritable: false },
    { pubkey: userVolumeAccumulator, isSigner: false, isWritable: true },
    { pubkey: associatedUserVolumeAccumulator, isSigner: false, isWritable: true },
    { pubkey: feeConfig, isSigner: false, isWritable: false },
    { pubkey: PUMP_FUN_FEE_PROGRAM_ID, isSigner: false, isWritable: false },
    { pubkey: SYSTEM_PROGRAM_ID, isSigner: false, isWritable: false },
    { pubkey: eventAuthority, isSigner: false, isWritable: false },
    { pubkey: PUMP_FUN_PROGRAM_ID, isSigner: false, isWritable: false },
  ];

  return new TransactionInstruction({
    keys,
    programId: PUMP_FUN_PROGRAM_ID,
    data: encodeSellV2Args(args.amount, args.minSolOutput),
  });
}

export async function buildPumpFunSellTransaction(args: {
  user: Keypair;
  mint: PublicKey;
  bondingCurve: PublicKey;
  creator: PublicKey;
  tokenProgramId: PublicKey;
  tokensIn: bigint;
  expectedSolOut: bigint;
  slippageBps: number;
  remainingAccounts?: AccountMeta[];
  useV2?: boolean;
  buybackFeeRecipient?: PublicKey;
}): Promise<VersionedTransaction> {
  const minSolOutput =
    (args.expectedSolOut * BigInt(10_000 - args.slippageBps)) / 10_000n;

  const tipAccount = TIP_ACCOUNTS[Math.floor(Math.random() * TIP_ACCOUNTS.length)]!;
  const tipIx = SystemProgram.transfer({
    fromPubkey: args.user.publicKey,
    toPubkey: new PublicKey(tipAccount),
    lamports: tipLamports,
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

  const sellIx =
    args.useV2 && args.buybackFeeRecipient
      ? buildPumpFunSellV2Ix({
          user: args.user.publicKey,
          mint: args.mint,
          bondingCurve: args.bondingCurve,
          creator: args.creator,
          baseTokenProgramId: args.tokenProgramId,
          amount: args.tokensIn,
          minSolOutput,
          buybackFeeRecipient: args.buybackFeeRecipient,
        })
      : buildPumpFunSellIx({
          user: args.user.publicKey,
          mint: args.mint,
          bondingCurve: args.bondingCurve,
          creator: args.creator,
          tokenProgramId: args.tokenProgramId,
          amount: args.tokensIn,
          minSolOutput,
          remainingAccounts: args.remainingAccounts,
        });
  if (sellIx.keys.length < 16) {
    throw new Error(`Pump.fun sell instruction is stale: expected at least 16 accounts, got ${sellIx.keys.length}`);
  }

  const { blockhash } = await getCachedBlockhash();

  const message = new TransactionMessage({
    payerKey: args.user.publicKey,
    recentBlockhash: blockhash,
    instructions: [...computeIxs, sellIx, tipIx],
  }).compileToV0Message();

  const tx = new VersionedTransaction(message);
  tx.sign([args.user]);
  return tx;
}
