import {
  Keypair,
  PublicKey,
  TransactionMessage,
  VersionedTransaction,
  AddressLookupTableAccount,
  type TransactionInstruction,
} from "@solana/web3.js";
import { buildTipInstructionSets } from "./tipBuilder.js";
import { executionAltAddresses, readConnection } from "./config.js";
import { recordExecutionStage } from "./executionTelemetry.js";

export const PACKET_DATA_SIZE = 1232;

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

function buildWithTipSet(args: {
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

export async function buildCompactV0Transaction(args: {
  payer: PublicKey;
  blockhash: string;
  instructions: TransactionInstruction[];
  signer: Keypair;
  log?: (msg: string) => void;
  route?: string;
}): Promise<VersionedTransaction> {
  const startedAt = Date.now();
  const route = args.route ?? "direct";
  const tipSets = buildTipInstructionSets(args.payer, { route: args.route });
  for (const tipSet of tipSets) {
    const tx = buildWithTipSet({
      ...args,
      instructions: [...args.instructions, ...tipSet.instructions],
    });
    const txBytes = transactionSizeBytes(tx);
    if (txBytes != null && txBytes <= PACKET_DATA_SIZE) {
      if (tipSet.label !== "all") {
        args.log?.(`Tx compacted to ${tipSet.label} tip only to fit ${PACKET_DATA_SIZE}B packet limit.`);
      }
      recordExecutionStage({
        stage: "build",
        route,
        elapsedMs: Date.now() - startedAt,
        success: true,
        details: {
          compactMode: "direct",
          tipSet: tipSet.label,
          txBytes,
          packetLimit: PACKET_DATA_SIZE,
          altCount: 0,
          instructionCount: args.instructions.length + tipSet.instructions.length,
        },
      });
      return tx;
    }
  }

  if (executionAltAddresses.length > 0) {
    const lookupTables = await getConfiguredAltAccounts();
    if (lookupTables.length > 0) {
      for (const tipSet of tipSets) {
        const tx = buildWithTipSet({
          ...args,
          instructions: [...args.instructions, ...tipSet.instructions],
          lookupTables,
        });
        const txBytes = transactionSizeBytes(tx);
        if (txBytes != null && txBytes <= PACKET_DATA_SIZE) {
          args.log?.(
            `Tx compacted with ${lookupTables.length} ALT(s) and ${tipSet.label} tip to fit ${PACKET_DATA_SIZE}B packet limit.`
          );
          recordExecutionStage({
            stage: "build",
            route,
            elapsedMs: Date.now() - startedAt,
            success: true,
            details: {
              compactMode: "alt",
              tipSet: tipSet.label,
              txBytes,
              packetLimit: PACKET_DATA_SIZE,
              altCount: lookupTables.length,
              instructionCount: args.instructions.length + tipSet.instructions.length,
            },
          });
          return tx;
        }
      }
    }
  }

  recordExecutionStage({
    stage: "build",
    route,
    elapsedMs: Date.now() - startedAt,
    success: false,
    error: `Transaction exceeds ${PACKET_DATA_SIZE}B packet limit after direct-route size fallbacks`,
    details: {
      compactMode: "failed",
      packetLimit: PACKET_DATA_SIZE,
      configuredAltCount: executionAltAddresses.length,
      tipSetCount: tipSets.length,
      instructionCount: args.instructions.length,
    },
  });
  throw new Error(`Transaction exceeds ${PACKET_DATA_SIZE}B packet limit after direct-route size fallbacks`);
}
