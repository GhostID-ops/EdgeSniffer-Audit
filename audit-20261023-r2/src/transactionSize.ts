import {
  ComputeBudgetProgram,
  Keypair,
  PublicKey,
  TransactionMessage,
  VersionedTransaction,
  AddressLookupTableAccount,
  type TransactionInstruction,
  type AccountMeta,
} from "@solana/web3.js";
import { buildTipInstructionSets } from "./tipBuilder.js";
import { executionAltAddresses, readConnection } from "./config.js";
import { recordExecutionStage } from "./executionTelemetry.js";
import { getExecutionFeeParamsForRoute } from "./executionFeeCaps.js";

export const PACKET_DATA_SIZE = 1232;

const COMPUTE_BUDGET_PROGRAM_ID = ComputeBudgetProgram.programId;

export function isOversizeError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return /encoding overruns|too large|exceeds.*size|packet/i.test(msg);
}

export function transactionFitsPacket(tx: VersionedTransaction): boolean {
  try {
    return tx.serialize().length <= PACKET_DATA_SIZE;
  } catch (err) {
    if (isOversizeError(err)) return false;
    throw err;
  }
}

function transactionSizeBytes(tx: VersionedTransaction): number | null {
  try {
    return tx.serialize().length;
  } catch (err) {
    if (isOversizeError(err)) return null;
    throw err;
  }
}

// --- Compute-budget instruction classification ------------------------------------------------
// ComputeBudgetInstruction tags (see solana-program ComputeBudgetInstruction enum):
//   0 RequestUnits (deprecated)  1 RequestHeapFrame  2 SetComputeUnitLimit
//   3 SetComputeUnitPrice        4 SetLoadedAccountsDataSizeLimit
export type ComputeBudgetInstructionType =
  | "RequestUnitsDeprecated"
  | "RequestHeapFrame"
  | "SetComputeUnitLimit"
  | "SetComputeUnitPrice"
  | "SetLoadedAccountsDataSizeLimit"
  | "Unknown"
  | "NotComputeBudget";

export function getComputeBudgetInstructionType(
  ix: TransactionInstruction
): ComputeBudgetInstructionType {
  if (!ix.programId.equals(COMPUTE_BUDGET_PROGRAM_ID)) return "NotComputeBudget";
  switch (ix.data[0]) {
    case 0:
      return "RequestUnitsDeprecated";
    case 1:
      return "RequestHeapFrame";
    case 2:
      return "SetComputeUnitLimit";
    case 3:
      return "SetComputeUnitPrice";
    case 4:
      return "SetLoadedAccountsDataSizeLimit";
    default:
      return "Unknown";
  }
}

// Compute-budget directives are singletons on-chain: a second SetComputeUnitLimit/Price/HeapFrame
// is ignored by the runtime but still costs ~1 account index + data bytes in the packet. When two
// builders (e.g. a route's compute ixs plus a tip helper, or a decompiled Jupiter tx) each add one,
// the duplicate is pure dead weight that can push a borderline tx over 1232B. We collapse them.
const DEDUP_COMPUTE_TYPES = new Set<ComputeBudgetInstructionType>([
  "RequestHeapFrame",
  "SetComputeUnitLimit",
  "SetComputeUnitPrice",
  "SetLoadedAccountsDataSizeLimit",
]);

function instructionSignature(ix: TransactionInstruction): string {
  const keys = ix.keys
    .map((k) => `${k.pubkey.toBase58()}${k.isSigner ? "s" : ""}${k.isWritable ? "w" : ""}`)
    .join(",");
  return `${ix.programId.toBase58()}|${Buffer.from(ix.data).toString("hex")}|${keys}`;
}

export type SanitizeResult = {
  instructions: TransactionInstruction[];
  removedDuplicateCompute: number;
  removedDuplicateInstructions: number;
};

/**
 * Remove dead weight before compiling:
 *  - For each singleton compute-budget directive, keep only the LAST occurrence (later overrides
 *    earlier on-chain anyway) and drop the rest.
 *  - Drop byte-for-byte duplicate instructions (same program, data, and account metas) — e.g. a
 *    setup helper and a route both emitting the same idempotent ATA-create or close.
 * Order is otherwise preserved. The swap instruction's account order is never touched.
 */
export function sanitizeInstructions(instructions: TransactionInstruction[]): SanitizeResult {
  const lastComputeIndexByType = new Map<ComputeBudgetInstructionType, number>();
  instructions.forEach((ix, i) => {
    const type = getComputeBudgetInstructionType(ix);
    if (DEDUP_COMPUTE_TYPES.has(type)) lastComputeIndexByType.set(type, i);
  });

  const seen = new Set<string>();
  const out: TransactionInstruction[] = [];
  let removedDuplicateCompute = 0;
  let removedDuplicateInstructions = 0;

  instructions.forEach((ix, i) => {
    const type = getComputeBudgetInstructionType(ix);
    if (DEDUP_COMPUTE_TYPES.has(type) && lastComputeIndexByType.get(type) !== i) {
      removedDuplicateCompute++;
      return;
    }
    const sig = instructionSignature(ix);
    if (seen.has(sig)) {
      removedDuplicateInstructions++;
      return;
    }
    seen.add(sig);
    out.push(ix);
  });

  return { instructions: out, removedDuplicateCompute, removedDuplicateInstructions };
}

// --- Address lookup tables --------------------------------------------------------------------
const altCache = new Map<string, AddressLookupTableAccount>();

export async function getConfiguredAltAccounts(): Promise<AddressLookupTableAccount[]> {
  const out: AddressLookupTableAccount[] = [];
  for (const address of executionAltAddresses) {
    const cached = altCache.get(address);
    if (cached) {
      out.push(cached);
      continue;
    }
    const key = new PublicKey(address);
    const resp = await readConnection.getAddressLookupTable(key);
    if (!resp.value) continue;
    altCache.set(address, resp.value);
    out.push(resp.value);
  }
  return out;
}

export async function warmExecutionAltAccounts(log?: (msg: string) => void): Promise<void> {
  if (executionAltAddresses.length === 0) return;
  const startedAt = Date.now();
  const accounts = await getConfiguredAltAccounts();
  log?.(
    `Execution ALT warmup: ${accounts.length}/${executionAltAddresses.length} configured table(s) loaded in ${
      Date.now() - startedAt
    }ms.`
  );
}

// --- Diagnostics ------------------------------------------------------------------------------
export type TransactionSizeDiagnostics = {
  txType: "v0";
  bytes: number | null;
  packetLimit: number;
  instructionCount: number;
  accountCount: number;
  staticAccountCount: number;
  altAccountCount: number;
  altTables: number;
  signerCount: number;
  /** Instructions sorted by account count, largest first (the usual size drivers). */
  topInstructions: Array<{ program: string; accounts: number; dataBytes: number }>;
};

function altKeySet(lookupTables: AddressLookupTableAccount[]): Set<string> {
  const set = new Set<string>();
  for (const table of lookupTables) {
    for (const key of table.state.addresses) set.add(key.toBase58());
  }
  return set;
}

/**
 * Compile a throwaway v0 message to measure exact serialized size and account layout without
 * signing. Used both to pick a fitting fallback and to explain *why* a tx is oversized.
 */
export function diagnoseV0Transaction(args: {
  payer: PublicKey;
  blockhash: string;
  instructions: TransactionInstruction[];
  lookupTables?: AddressLookupTableAccount[];
}): TransactionSizeDiagnostics {
  const lookupTables = args.lookupTables ?? [];
  const message = new TransactionMessage({
    payerKey: args.payer,
    recentBlockhash: args.blockhash,
    instructions: args.instructions,
  }).compileToV0Message(lookupTables);
  const tx = new VersionedTransaction(message);

  const altLoaded = message.addressTableLookups.reduce(
    (sum, lookup) => sum + lookup.writableIndexes.length + lookup.readonlyIndexes.length,
    0
  );
  const topInstructions = args.instructions
    .map((ix) => ({
      program: ix.programId.toBase58(),
      accounts: ix.keys.length,
      dataBytes: ix.data.length,
    }))
    .sort((a, b) => b.accounts - a.accounts)
    .slice(0, 4);

  return {
    txType: "v0",
    bytes: transactionSizeBytes(tx),
    packetLimit: PACKET_DATA_SIZE,
    instructionCount: args.instructions.length,
    accountCount: message.staticAccountKeys.length + altLoaded,
    staticAccountCount: message.staticAccountKeys.length,
    altAccountCount: altLoaded,
    altTables: lookupTables.length,
    signerCount: message.header.numRequiredSignatures,
    topInstructions,
  };
}

export function formatSizeDiagnostics(
  route: string,
  diag: TransactionSizeDiagnostics,
  extra?: Record<string, unknown>
): string {
  const top = diag.topInstructions
    .map((ix) => `${ix.program.slice(0, 4)}…(${ix.accounts}acct/${ix.dataBytes}B)`)
    .join(", ");
  const extraStr = extra
    ? " · " +
      Object.entries(extra)
        .map(([k, v]) => `${k}=${v}`)
        .join(" ")
    : "";
  return (
    `${route} ${diag.txType} tx: ${diag.bytes ?? ">limit"}B/${diag.packetLimit} · ` +
    `${diag.instructionCount} ix · ${diag.accountCount} accts ` +
    `(${diag.staticAccountCount} static + ${diag.altAccountCount} via ${diag.altTables} ALT) · ` +
    `${diag.signerCount} signer(s) · top: ${top}${extraStr}`
  );
}

// --- Build helpers ----------------------------------------------------------------------------
function compileSignedV0(args: {
  payer: PublicKey;
  blockhash: string;
  instructions: TransactionInstruction[];
  signer: Keypair;
  lookupTables?: AddressLookupTableAccount[];
}): VersionedTransaction {
  const message = new TransactionMessage({
    payerKey: args.payer,
    recentBlockhash: args.blockhash,
    instructions: args.instructions,
  }).compileToV0Message(args.lookupTables ?? []);
  const tx = new VersionedTransaction(message);
  tx.sign([args.signer]);
  return tx;
}

type FitAttempt = {
  tx: VersionedTransaction;
  bytes: number;
  tipSet: string;
  altCount: number;
};

/**
 * Try to produce a signed v0 tx that fits the packet limit, sweeping tip variants and (optionally)
 * lookup tables. Recomputes the exact serialized size after every variant. Returns the first fit,
 * or null if nothing fits.
 */
function findFittingTransaction(args: {
  payer: PublicKey;
  blockhash: string;
  baseInstructions: TransactionInstruction[];
  signer: Keypair;
  route: string;
  lookupTables: AddressLookupTableAccount[];
  includeTips: boolean;
}): FitAttempt | null {
  const tipSets = args.includeTips
    ? buildTipInstructionSets(args.payer, {
        route: args.route,
        jitoTipLamports: getExecutionFeeParamsForRoute(args.route).jitoTipLamports,
        heliusTipLamports: getExecutionFeeParamsForRoute(args.route).heliusTipLamports,
      })
    : [{ label: "none" as const, instructions: [] as TransactionInstruction[] }];

  // Without ALT first (cheapest, no RPC dependency), then with ALT if configured.
  const altVariants: AddressLookupTableAccount[][] = [[]];
  if (args.lookupTables.length > 0) altVariants.push(args.lookupTables);

  for (const lookupTables of altVariants) {
    for (const tipSet of tipSets) {
      const tx = compileSignedV0({
        payer: args.payer,
        blockhash: args.blockhash,
        instructions: [...args.baseInstructions, ...tipSet.instructions],
        signer: args.signer,
        lookupTables,
      });
      const bytes = transactionSizeBytes(tx);
      if (bytes != null && bytes <= PACKET_DATA_SIZE) {
        return { tx, bytes, tipSet: tipSet.label, altCount: lookupTables.length };
      }
    }
  }
  return null;
}

function leadingComputeBudgetCount(instructions: TransactionInstruction[]): number {
  let i = 0;
  while (i < instructions.length && instructions[i]!.programId.equals(COMPUTE_BUDGET_PROGRAM_ID)) i++;
  return i;
}

export type SizeSafeBuildResult = {
  swapTx: VersionedTransaction;
  /** Non-null only when setup (ATA creates etc.) had to be split into its own tx to fit. */
  setupTx: VersionedTransaction | null;
  diagnostics: {
    bytes: number;
    tipSet: string;
    altCount: number;
    split: boolean;
    removedDuplicateCompute: number;
    removedDuplicateInstructions: number;
  };
};

/**
 * Central size-safe v0 transaction builder. Every Pump.fun / PumpSwap route funnels through here.
 *
 * Strategy (each step recomputes exact serialized size):
 *   1. Sanitize: drop duplicate compute-budget directives and byte-identical instructions.
 *   2. Build setup + core in ONE tx, sweeping tip variants then lookup tables.
 *   3. If still oversized AND splittable setup instructions exist, move them to their own
 *      transaction so the swap fits on its own (caller sends setup first).
 *   4. If still oversized, HARD FAIL before signing/sending, with full diagnostics.
 *
 * `setupInstructions` are inserted right after the leading compute-budget run so they execute
 * before the swap; they are the only instructions eligible to be split out.
 */
export async function buildSizeSafeV0Transaction(args: {
  payer: PublicKey;
  blockhash: string;
  /** Ordered instructions including compute-budget and the swap, excluding splittable setup. */
  instructions: TransactionInstruction[];
  /** ATA creates etc. that may be split into a separate tx if the combined tx is too large. */
  setupInstructions?: TransactionInstruction[];
  signer: Keypair;
  log?: (msg: string) => void;
  route?: string;
  allowSetupSplit?: boolean;
}): Promise<SizeSafeBuildResult> {
  const startedAt = Date.now();
  const route = args.route ?? "direct";
  const setupInstructions = args.setupInstructions ?? [];
  const allowSetupSplit = args.allowSetupSplit ?? setupInstructions.length > 0;

  // 1. Sanitize the full instruction set (setup inserted after leading compute budget).
  const computeLead = leadingComputeBudgetCount(args.instructions);
  const combined = [
    ...args.instructions.slice(0, computeLead),
    ...setupInstructions,
    ...args.instructions.slice(computeLead),
  ];
  const sanitized = sanitizeInstructions(combined);
  if (sanitized.removedDuplicateCompute > 0 || sanitized.removedDuplicateInstructions > 0) {
    args.log?.(
      `Tx sanitize: removed ${sanitized.removedDuplicateCompute} duplicate compute-budget ix and ` +
        `${sanitized.removedDuplicateInstructions} duplicate instruction(s) before compile.`
    );
  }

  const lookupTables = executionAltAddresses.length > 0 ? await getConfiguredAltAccounts() : [];

  // 2. Combined single-transaction attempt.
  const combinedFit = findFittingTransaction({
    payer: args.payer,
    blockhash: args.blockhash,
    baseInstructions: sanitized.instructions,
    signer: args.signer,
    route,
    lookupTables,
    includeTips: true,
  });
  if (combinedFit) {
    if (combinedFit.tipSet !== "all") {
      args.log?.(`Tx compacted to ${combinedFit.tipSet} tip${combinedFit.altCount ? ` + ${combinedFit.altCount} ALT` : ""} to fit ${PACKET_DATA_SIZE}B.`);
    }
    recordExecutionStage({
      stage: "build",
      route,
      elapsedMs: Date.now() - startedAt,
      success: true,
      details: {
        compactMode: combinedFit.altCount > 0 ? "alt" : "direct",
        tipSet: combinedFit.tipSet,
        txBytes: combinedFit.bytes,
        packetLimit: PACKET_DATA_SIZE,
        altCount: combinedFit.altCount,
        instructionCount: sanitized.instructions.length,
        removedDuplicateCompute: sanitized.removedDuplicateCompute,
        removedDuplicateInstructions: sanitized.removedDuplicateInstructions,
      },
    });
    return {
      swapTx: combinedFit.tx,
      setupTx: null,
      diagnostics: {
        bytes: combinedFit.bytes,
        tipSet: combinedFit.tipSet,
        altCount: combinedFit.altCount,
        split: false,
        removedDuplicateCompute: sanitized.removedDuplicateCompute,
        removedDuplicateInstructions: sanitized.removedDuplicateInstructions,
      },
    };
  }

  // 3. Split setup into its own tx and retry the swap alone.
  if (allowSetupSplit && setupInstructions.length > 0) {
    const swapOnly = sanitizeInstructions(args.instructions).instructions;
    const swapFit = findFittingTransaction({
      payer: args.payer,
      blockhash: args.blockhash,
      baseInstructions: swapOnly,
      signer: args.signer,
      route,
      lookupTables,
      includeTips: true,
    });
    if (swapFit) {
      // Setup tx carries only a priority-fee ix + the setup instructions (no tip; it just needs
      // to land before the swap). Recompute its size and refuse to ship it oversized too.
      const setupFees = getExecutionFeeParamsForRoute(route);
      const setupBase = [
        ComputeBudgetProgram.setComputeUnitPrice({ microLamports: setupFees.priorityFeeMicroLamports }),
        ...sanitizeInstructions(setupInstructions).instructions,
      ];
      const setupTx = compileSignedV0({
        payer: args.payer,
        blockhash: args.blockhash,
        instructions: setupBase,
        signer: args.signer,
        lookupTables,
      });
      const setupBytes = transactionSizeBytes(setupTx);
      if (setupBytes != null && setupBytes <= PACKET_DATA_SIZE) {
        args.log?.(
          `Tx split: setup (${setupInstructions.length} ix, ${setupBytes}B) sent separately so swap ` +
            `(${swapFit.bytes}B, ${swapFit.tipSet} tip${swapFit.altCount ? ` + ${swapFit.altCount} ALT` : ""}) fits ${PACKET_DATA_SIZE}B.`
        );
        recordExecutionStage({
          stage: "build",
          route,
          elapsedMs: Date.now() - startedAt,
          success: true,
          details: {
            compactMode: "split-setup",
            tipSet: swapFit.tipSet,
            txBytes: swapFit.bytes,
            setupBytes,
            packetLimit: PACKET_DATA_SIZE,
            altCount: swapFit.altCount,
            instructionCount: swapOnly.length,
          },
        });
        return {
          swapTx: swapFit.tx,
          setupTx,
          diagnostics: {
            bytes: swapFit.bytes,
            tipSet: swapFit.tipSet,
            altCount: swapFit.altCount,
            split: true,
            removedDuplicateCompute: sanitized.removedDuplicateCompute,
            removedDuplicateInstructions: sanitized.removedDuplicateInstructions,
          },
        };
      }
    }
  }

  // 4. Hard fail with diagnostics — never ship an oversized tx.
  const diag = diagnoseV0Transaction({
    payer: args.payer,
    blockhash: args.blockhash,
    instructions: sanitized.instructions,
    lookupTables,
  });
  const detail = formatSizeDiagnostics(route, diag, {
    configuredAltTables: executionAltAddresses.length,
    splittableSetupIx: setupInstructions.length,
    sanitizedComputeRemoved: sanitized.removedDuplicateCompute,
  });
  args.log?.(`Oversize tx (hard fail): ${detail}`);
  recordExecutionStage({
    stage: "build",
    route,
    elapsedMs: Date.now() - startedAt,
    success: false,
    error: `Transaction exceeds ${PACKET_DATA_SIZE}B packet limit after size fallbacks`,
    details: {
      compactMode: "failed",
      packetLimit: PACKET_DATA_SIZE,
      bytes: diag.bytes,
      accountCount: diag.accountCount,
      staticAccountCount: diag.staticAccountCount,
      altAccountCount: diag.altAccountCount,
      configuredAltCount: executionAltAddresses.length,
      instructionCount: diag.instructionCount,
      topInstructions: diag.topInstructions,
    },
  });
  throw new Error(
    `Transaction exceeds ${PACKET_DATA_SIZE}B packet limit after size fallbacks (${detail})`
  );
}

/**
 * Backward-compatible single-transaction builder. Sanitizes, sweeps tips + ALT, recomputes size,
 * and hard-fails with diagnostics if nothing fits. Setup splitting is disabled — callers that pass
 * splittable setup separately should use buildSizeSafeV0Transaction directly.
 */
export async function buildCompactV0Transaction(args: {
  payer: PublicKey;
  blockhash: string;
  instructions: TransactionInstruction[];
  signer: Keypair;
  log?: (msg: string) => void;
  route?: string;
}): Promise<VersionedTransaction> {
  const result = await buildSizeSafeV0Transaction({ ...args, allowSetupSplit: false });
  return result.swapTx;
}

export type { AccountMeta };
