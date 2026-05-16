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
  computeUnitLimit,
} from "../config.js";
import { buildTipInstructions } from "../tipBuilder.js";
import { getDynamicPriorityFee } from "../feeOracle.js";
import { getCachedBlockhash } from "../blockhashCache.js";
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

export function buildPumpFunBuyIx(args: {
  user: PublicKey;
  mint: PublicKey;
  bondingCurve: PublicKey;
  creator: PublicKey;
  tokenProgramId: PublicKey;
  amount: bigint;
  maxSolCost: bigint;
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

  let keys: AccountMeta[];
  if (args.sourceIxAccounts && args.sourceIxAccounts.length >= 16) {
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
  sourceIxAccounts?: PublicKey[];
}): Promise<VersionedTransaction> {
  const maxSolCost =
    (args.solInLamports * BigInt(10_000 + args.slippageBps)) / 10_000n;

  const tipIxs = buildTipInstructions(args.user.publicKey);

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
    sourceIxAccounts: args.sourceIxAccounts,
  });
  if (buyIx.keys.length < 16) {
    throw new Error(`Pump.fun buy instruction is stale: expected at least 16 accounts, got ${buyIx.keys.length}`);
  }

  const { blockhash } = await getCachedBlockhash();

  const message = new TransactionMessage({
    payerKey: args.user.publicKey,
    recentBlockhash: blockhash,
    instructions: [...computeIxs, ataIx, buyIx, ...tipIxs],
  }).compileToV0Message();

  const tx = new VersionedTransaction(message);
  tx.sign([args.user]);
  return tx;
}
