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
import {
  PUMP_FUN_PROGRAM_ID,
  PUMP_FUN_FEE_PROGRAM_ID,
  PUMP_FUN_FEE_RECIPIENT,
  ASSOCIATED_TOKEN_PROGRAM_ID,
  SYSTEM_PROGRAM_ID,
  BUY_DISCRIMINATOR,
  deriveAta,
  deriveCreatorVaultPda,
  deriveEventAuthorityPda,
  deriveFeeConfigPda,
  deriveGlobalPda,
  deriveGlobalVolumeAccumulatorPda,
  deriveUserVolumeAccumulatorPda,
} from "./constants.js";

function encodeBuyArgs(amount: bigint, maxSolCost: bigint): Buffer {
  const buf = Buffer.alloc(25);
  BUY_DISCRIMINATOR.copy(buf, 0);
  buf.writeBigUInt64LE(amount, 8);
  buf.writeBigUInt64LE(maxSolCost, 16);
  buf.writeUInt8(0, 24); // OptionBool::None for track_volume.
  return buf;
}

export function buildPumpFunBuyIx(args: {
  user: PublicKey;
  mint: PublicKey;
  bondingCurve: PublicKey;
  creator: PublicKey;
  tokenProgramId: PublicKey;
  amount: bigint;
  maxSolCost: bigint;
  remainingAccounts?: AccountMeta[];
}): TransactionInstruction {
  const global = deriveGlobalPda();
  const eventAuthority = deriveEventAuthorityPda();
  const associatedBondingCurve = deriveAta(args.bondingCurve, args.mint, args.tokenProgramId);
  const associatedUser = deriveAta(args.user, args.mint, args.tokenProgramId);
  const creatorVault = deriveCreatorVaultPda(args.creator);
  const globalVolumeAccumulator = deriveGlobalVolumeAccumulatorPda();
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
    { pubkey: globalVolumeAccumulator, isSigner: false, isWritable: false },
    { pubkey: userVolumeAccumulator, isSigner: false, isWritable: true },
    { pubkey: feeConfig, isSigner: false, isWritable: false },
    { pubkey: PUMP_FUN_FEE_PROGRAM_ID, isSigner: false, isWritable: false },
    ...(args.remainingAccounts ?? []),
  ];

  return new TransactionInstruction({
    keys,
    programId: PUMP_FUN_PROGRAM_ID,
    data: encodeBuyArgs(args.amount, args.maxSolCost),
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
}): Promise<VersionedTransaction> {
  const maxSolCost =
    (args.solInLamports * BigInt(10_000 + args.slippageBps)) / 10_000n;

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

  const ataIx = buildCreateAtaIdempotentIx({
    payer: args.user.publicKey,
    owner: args.user.publicKey,
    mint: args.mint,
    tokenProgramId: args.tokenProgramId,
  });

  const buyIx = buildPumpFunBuyIx({
    user: args.user.publicKey,
    mint: args.mint,
    bondingCurve: args.bondingCurve,
    creator: args.creator,
    tokenProgramId: args.tokenProgramId,
    amount: args.tokensOut,
    maxSolCost,
    remainingAccounts: args.remainingAccounts,
  });
  if (buyIx.keys.length < 16) {
    throw new Error(`Pump.fun buy instruction is stale: expected at least 16 accounts, got ${buyIx.keys.length}`);
  }

  const { blockhash } = await readConnection.getLatestBlockhash("confirmed");

  const message = new TransactionMessage({
    payerKey: args.user.publicKey,
    recentBlockhash: blockhash,
    instructions: [...computeIxs, ataIx, buyIx, tipIx],
  }).compileToV0Message();

  const tx = new VersionedTransaction(message);
  tx.sign([args.user]);
  return tx;
}
