import {
  Keypair,
  TransactionMessage,
  VersionedTransaction,
  type PublicKey,
  type TransactionInstruction,
} from "@solana/web3.js";
import { buildTipInstructionSets } from "./tipBuilder.js";

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

export function buildCompactV0Transaction(args: {
  payer: PublicKey;
  blockhash: string;
  instructions: TransactionInstruction[];
  signer: Keypair;
  log?: (msg: string) => void;
}): VersionedTransaction {
  const tipSets = buildTipInstructionSets(args.payer);
  for (const tipSet of tipSets) {
    const message = new TransactionMessage({
      payerKey: args.payer,
      recentBlockhash: args.blockhash,
      instructions: [...args.instructions, ...tipSet.instructions],
    }).compileToV0Message();
    const tx = new VersionedTransaction(message);
    tx.sign([args.signer]);
    if (transactionFitsPacket(tx)) {
      if (tipSet.label !== "all") {
        args.log?.(`Tx compacted to ${tipSet.label} tip only to fit ${PACKET_DATA_SIZE}B packet limit.`);
      }
      return tx;
    }
  }

  throw new Error(`Transaction exceeds ${PACKET_DATA_SIZE}B packet limit after direct-route size fallbacks`);
}
