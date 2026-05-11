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
import {
  computeUnitLimit,
  readConnection,
  simulationMode,
  TIP_ACCOUNTS,
  tipLamports,
} from "../config.js";
import { getDynamicPriorityFee } from "../feeOracle.js";
import { broadcastTransactionWithSender } from "../sender.js";
import { confirmSignature } from "../confirm.js";
import { getTokenMeta, type TokenMeta, WRAPPED_SOL_MINT } from "../tokenRegistry.js";
import {
  ASSOCIATED_TOKEN_PROGRAM_ID,
  SYSTEM_PROGRAM_ID,
  TOKEN_PROGRAM_ID,
  deriveAta,
} from "../pumpfun/constants.js";

export const PUMPSWAP_PROGRAM_ID = new PublicKey("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA");
const BUY_EXACT_QUOTE_IN_DISCRIMINATOR = Buffer.from([198, 46, 21, 82, 180, 217, 232, 112]);
const SYNC_NATIVE_IX = Buffer.from([17]);
const CLOSE_ACCOUNT_IX = Buffer.from([9]);

export type PumpSwapBuildContext = {
  mint: string;
  pool: string;
  user: string;
  userBaseAta: string;
  userQuoteAta: string;
  sourceSignature: string;
  sourceSolInLamports: number;
  sourceTokensOutRaw: number;
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
    }
  | { ok: false; reason: string; recoverable: boolean };

function encodeBuyExactQuoteIn(spendableQuoteIn: bigint, minBaseOut: bigint): Buffer {
  const buf = Buffer.alloc(24);
  BUY_EXACT_QUOTE_IN_DISCRIMINATOR.copy(buf, 0);
  buf.writeBigUInt64LE(spendableQuoteIn, 8);
  buf.writeBigUInt64LE(minBaseOut, 16);
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

  for (const ix of message.compiledInstructions) {
    if (!allKeys[ix.programIdIndex]?.equals(PUMPSWAP_PROGRAM_ID)) continue;
    const data = Buffer.from(ix.data);
    if (!data.subarray(0, 8).equals(BUY_EXACT_QUOTE_IN_DISCRIMINATOR)) continue;
    const keys = ix.accountKeyIndexes.map((idx) => allKeys[idx]);
    if (!keys[3]?.equals(mint)) continue;
    return ix.accountKeyIndexes.map((idx) => ({
      pubkey: allKeys[idx]!,
      isSigner: message.isAccountSigner(idx),
      isWritable: message.isAccountWritable(idx),
    }));
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

  accounts[1] = { pubkey: args.user, isSigner: true, isWritable: true };
  accounts[5] = { pubkey: userBaseAta, isSigner: false, isWritable: true };
  accounts[6] = { pubkey: userQuoteAta, isSigner: false, isWritable: true };
  if (accounts.length > 20) {
    accounts[20] = {
      pubkey: derivePumpSwapUserVolumeAccumulatorPda(args.user),
      isSigner: false,
      isWritable: true,
    };
  }

  return { accounts, userBaseAta, userQuoteAta };
}

export async function tryPumpSwapDirectBuy(args: {
  user: Keypair;
  mint: string;
  sourceSignature: string;
  sourceSolInLamports: number;
  sourceTokensOutRaw?: number;
  solInLamports: number;
  slippageBps: number;
  startedAt: number;
  log: (msg: string) => void;
}): Promise<PumpSwapOutcome> {
  if (!args.sourceTokensOutRaw || args.sourceTokensOutRaw <= 0 || args.sourceSolInLamports <= 0) {
    return { ok: false, recoverable: true, reason: "source PumpSwap fill amounts unavailable" };
  }

  const elapsed = () => `${Date.now() - args.startedAt}ms`;
  const mintKey = new PublicKey(args.mint);
  const mintAccount = await readConnection.getAccountInfo(mintKey, "confirmed");
  if (!mintAccount) {
    return { ok: false, recoverable: true, reason: "PumpSwap base mint account not found" };
  }
  const baseTokenProgramId = mintAccount.owner;
  const sourceAccounts = await sourcePumpSwapAccounts(args.sourceSignature, mintKey);
  if (!sourceAccounts) {
    return { ok: false, recoverable: true, reason: "source tx has no PumpSwap buy_exact_quote_in instruction" };
  }

  const expectedBaseOut =
    (BigInt(Math.floor(args.sourceTokensOutRaw)) * BigInt(args.solInLamports)) /
    BigInt(Math.floor(args.sourceSolInLamports));
  const minBaseOut = (expectedBaseOut * BigInt(10_000 - args.slippageBps)) / 10_000n;
  if (minBaseOut <= 0n) {
    return { ok: false, recoverable: true, reason: "PumpSwap min output is zero" };
  }

  const { accounts, userBaseAta, userQuoteAta } = replacePumpSwapUserAccounts({
    accounts: sourceAccounts,
    user: args.user.publicKey,
    baseMint: mintKey,
    baseTokenProgram: baseTokenProgramId,
  });

  const ctx: PumpSwapBuildContext = {
    mint: args.mint,
    pool: accounts[0]?.pubkey.toBase58() ?? "unknown",
    user: args.user.publicKey.toBase58(),
    userBaseAta: userBaseAta.toBase58(),
    userQuoteAta: userQuoteAta.toBase58(),
    sourceSignature: args.sourceSignature,
    sourceSolInLamports: args.sourceSolInLamports,
    sourceTokensOutRaw: Math.floor(args.sourceTokensOutRaw),
    solInLamports: args.solInLamports,
    expectedBaseOut,
    minBaseOut,
    slippageBps: args.slippageBps,
    accountCount: accounts.length,
    baseTokenProgram: baseTokenProgramId.toBase58(),
  };

  args.log(`PumpSwap direct buy: ${formatContext(ctx)}`);

  if (accounts.length < 17) {
    return { ok: false, recoverable: true, reason: `PumpSwap account list too short: ${accounts.length}` };
  }

  const poolAccount = await readConnection.getAccountInfo(accounts[0]!.pubkey, "confirmed");
  const poolError = validatePumpSwapPoolAccount(accounts[0]!.pubkey, poolAccount);
  if (poolError) return { ok: false, recoverable: true, reason: poolError };

  if (simulationMode) {
    const mintMeta = await getTokenMeta(args.mint);
    args.log(`PumpSwap simulation mode: not sending tx (${formatContext(ctx)})`);
    return {
      ok: true,
      signature: `sim-pumpswap-${args.mint.slice(0, 8)}-${Date.now()}`,
      isSimulated: true,
      baseOut: minBaseOut,
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

  const tipAccount = TIP_ACCOUNTS[Math.floor(Math.random() * TIP_ACCOUNTS.length)]!;
  const tipIx = SystemProgram.transfer({
    fromPubkey: args.user.publicKey,
    toPubkey: new PublicKey(tipAccount),
    lamports: tipLamports,
  });

  const swapIx = new TransactionInstruction({
    programId: PUMPSWAP_PROGRAM_ID,
    keys: accounts,
    data: encodeBuyExactQuoteIn(BigInt(args.solInLamports), minBaseOut),
  });

  const [{ blockhash, lastValidBlockHeight }, currentBlockHeight] = await Promise.all([
    readConnection.getLatestBlockhash("confirmed"),
    readConnection.getBlockHeight("confirmed").catch(() => null),
  ]);
  const validFor =
    currentBlockHeight == null ? "unknown" : `${lastValidBlockHeight - currentBlockHeight} block(s)`;
  args.log(
    `PumpSwap blockhash ready: lastValidBlockHeight=${lastValidBlockHeight} validFor=${validFor}`
  );
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
      tipIx,
    ],
  }).compileToV0Message();
  const tx = new VersionedTransaction(message);
  tx.sign([args.user]);

  if (args.log && false) args.log(userBaseAta.toBase58());
  args.log(`PumpSwap tx built (${elapsed()})`);
  let signature: string | null = null;
  try {
    signature = await broadcastTransactionWithSender(tx);
    args.log(`PumpSwap broadcast: ${signature} (${elapsed()})`);
    await confirmSignature(signature);
  } catch (err) {
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
  args.log(`PumpSwap confirmed (${elapsed()})`);

  const mintMeta = await getTokenMeta(args.mint);
  return {
    ok: true,
    signature,
    isSimulated: false,
    baseOut: minBaseOut,
    quoteIn: BigInt(args.solInLamports),
    mintMeta,
  };
}
