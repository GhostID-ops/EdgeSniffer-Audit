import {
  PublicKey,
  Keypair,
  TransactionInstruction,
  TransactionMessage,
  VersionedTransaction,
  SystemProgram,
  ComputeBudgetProgram,
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
  PUMP_FUN_FEE_RECIPIENT,
  TOKEN_PROGRAM_ID,
  ASSOCIATED_TOKEN_PROGRAM_ID,
  SYSTEM_PROGRAM_ID,
  RENT_SYSVAR_ID,
  BUY_DISCRIMINATOR,
  deriveAta,
  deriveEventAuthorityPda,
  deriveGlobalPda,
} from "./constants.js";

/**
 * Encode the args for the pump.fun `buy` instruction:
 *   amount: u64 (token raw amount to buy — exact)
 *   max_sol_cost: u64 (slippage protection — tx fails if curve has moved past this)
 */
function encodeBuyArgs(amount: bigint, maxSolCost: bigint): Buffer {
  const buf = Buffer.alloc(8 + 8 + 8); // discriminator + 2× u64
  BUY_DISCRIMINATOR.copy(buf, 0);
  buf.writeBigUInt64LE(amount, 8);
  buf.writeBigUInt64LE(maxSolCost, 16);
  return buf;
}

/**
 * Build the `buy` TransactionInstruction. Account order is canonical for the pump.fun program:
 *   [global, fee_recipient, mint, bonding_curve, associated_bonding_curve, associated_user,
 *    user, system_program, token_program, rent, event_authority, program]
 */
export function buildPumpFunBuyIx(args: {
  user: PublicKey;
  mint: PublicKey;
  bondingCurve: PublicKey;
  amount: bigint;
  maxSolCost: bigint;
}): TransactionInstruction {
  const global = deriveGlobalPda();
  const eventAuthority = deriveEventAuthorityPda();
  const associatedBondingCurve = deriveAta(args.bondingCurve, args.mint);
  const associatedUser = deriveAta(args.user, args.mint);

  const keys = [
    { pubkey: global, isSigner: false, isWritable: false },
    { pubkey: PUMP_FUN_FEE_RECIPIENT, isSigner: false, isWritable: true },
    { pubkey: args.mint, isSigner: false, isWritable: false },
    { pubkey: args.bondingCurve, isSigner: false, isWritable: true },
    { pubkey: associatedBondingCurve, isSigner: false, isWritable: true },
    { pubkey: associatedUser, isSigner: false, isWritable: true },
    { pubkey: args.user, isSigner: true, isWritable: true },
    { pubkey: SYSTEM_PROGRAM_ID, isSigner: false, isWritable: false },
    { pubkey: TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
    { pubkey: RENT_SYSVAR_ID, isSigner: false, isWritable: false },
    { pubkey: eventAuthority, isSigner: false, isWritable: false },
    { pubkey: PUMP_FUN_PROGRAM_ID, isSigner: false, isWritable: false },
  ];

  return new TransactionInstruction({
    keys,
    programId: PUMP_FUN_PROGRAM_ID,
    data: encodeBuyArgs(args.amount, args.maxSolCost),
  });
}

/**
 * Build `createAssociatedTokenAccountIdempotent` instruction. Idempotent variant doesn't fail
 * if the ATA already exists — safe to include unconditionally on every buy.
 *
 * Instruction discriminator for AssociatedTokenAccount program: 1 (idempotent variant).
 */
export function buildCreateAtaIdempotentIx(args: {
  payer: PublicKey;
  owner: PublicKey;
  mint: PublicKey;
}): TransactionInstruction {
  const ata = deriveAta(args.owner, args.mint);
  const keys = [
    { pubkey: args.payer, isSigner: true, isWritable: true },
    { pubkey: ata, isSigner: false, isWritable: true },
    { pubkey: args.owner, isSigner: false, isWritable: false },
    { pubkey: args.mint, isSigner: false, isWritable: false },
    { pubkey: SYSTEM_PROGRAM_ID, isSigner: false, isWritable: false },
    { pubkey: TOKEN_PROGRAM_ID, isSigner: false, isWritable: false },
  ];
  return new TransactionInstruction({
    keys,
    programId: ASSOCIATED_TOKEN_PROGRAM_ID,
    data: Buffer.from([1]), // idempotent variant
  });
}

/**
 * Assemble the full transaction: compute budget + create-ATA-idempotent + pump.fun buy + tip.
 * Signs with the user keypair and returns a ready-to-broadcast VersionedTransaction.
 *
 * `slippageBps` controls max_sol_cost = solInLamports × (1 + slippageBps/10000).
 */
export async function buildPumpFunBuyTransaction(args: {
  user: Keypair;
  mint: PublicKey;
  bondingCurve: PublicKey;
  tokensOut: bigint;
  solInLamports: bigint;
  slippageBps: number;
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
  });

  const buyIx = buildPumpFunBuyIx({
    user: args.user.publicKey,
    mint: args.mint,
    bondingCurve: args.bondingCurve,
    amount: args.tokensOut,
    maxSolCost,
  });

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
