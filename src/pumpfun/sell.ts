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
  SYSTEM_PROGRAM_ID,
  RENT_SYSVAR_ID,
  SELL_DISCRIMINATOR,
  deriveAta,
  deriveEventAuthorityPda,
  deriveGlobalPda,
} from "./constants.js";

/**
 * Encode args for the pump.fun `sell` instruction:
 *   amount: u64          — token raw amount to sell
 *   min_sol_output: u64  — slippage protection; tx fails if curve would pay less than this
 */
function encodeSellArgs(amount: bigint, minSolOutput: bigint): Buffer {
  const buf = Buffer.alloc(8 + 8 + 8);
  SELL_DISCRIMINATOR.copy(buf, 0);
  buf.writeBigUInt64LE(amount, 8);
  buf.writeBigUInt64LE(minSolOutput, 16);
  return buf;
}

/**
 * Build the `sell` TransactionInstruction. Account list mirrors the buy instruction; only the
 * data payload differs (different discriminator + min_sol_output instead of max_sol_cost).
 */
export function buildPumpFunSellIx(args: {
  user: PublicKey;
  mint: PublicKey;
  bondingCurve: PublicKey;
  amount: bigint;
  minSolOutput: bigint;
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
    data: encodeSellArgs(args.amount, args.minSolOutput),
  });
}

/**
 * Assemble a sell transaction: compute budget + sell ix + tip transfer. No create-ATA needed —
 * selling implies we already hold the tokens, so the ATA exists.
 *
 * `slippageBps` lowers `min_sol_output` from the curve-computed expected to give the tx room to
 * still land if the curve moved against us between quote and broadcast.
 */
export async function buildPumpFunSellTransaction(args: {
  user: Keypair;
  mint: PublicKey;
  bondingCurve: PublicKey;
  tokensIn: bigint;
  expectedSolOut: bigint;
  slippageBps: number;
}): Promise<VersionedTransaction> {
  // min_sol_output = expected × (1 - slippage). If the curve has moved more than slippage by
  // the time the tx lands, it fails — same fail-safe as the buy side's max_sol_cost.
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

  const sellIx = buildPumpFunSellIx({
    user: args.user.publicKey,
    mint: args.mint,
    bondingCurve: args.bondingCurve,
    amount: args.tokensIn,
    minSolOutput,
  });

  const { blockhash } = await readConnection.getLatestBlockhash("confirmed");

  const message = new TransactionMessage({
    payerKey: args.user.publicKey,
    recentBlockhash: blockhash,
    instructions: [...computeIxs, sellIx, tipIx],
  }).compileToV0Message();

  const tx = new VersionedTransaction(message);
  tx.sign([args.user]);
  return tx;
}
