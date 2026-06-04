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

const altCache = new Map<string, AddressLookupTableAccount>();

async function getConfiguredAltAccounts(): Promise<AddressLookupTableAccount[]> {
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
  const tipSets = buildTipInstructionSets(args.payer, { route: args.route });
  for (const tipSet of tipSets) {
    const tx = buildWithTipSet({
      ...args,
      instructions: [...args.instructions, ...tipSet.instructions],
    });
    if (transactionFitsPacket(tx)) {
      if (tipSet.label !== "all") {
        args.log?.(`Tx compacted to ${tipSet.label} tip only to fit ${PACKET_DATA_SIZE}B packet limit.`);
      }
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
        if (transactionFitsPacket(tx)) {
          args.log?.(
            `Tx compacted with ${lookupTables.length} ALT(s) and ${tipSet.label} tip to fit ${PACKET_DATA_SIZE}B packet limit.`
          );
          return tx;
        }
      }
    }
  }

  throw new Error(`Transaction exceeds ${PACKET_DATA_SIZE}B packet limit after direct-route size fallbacks`);
}
