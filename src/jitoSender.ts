import type { VersionedTransaction } from "@solana/web3.js";
import bs58 from "bs58";
import { jitoBundleUrl } from "./config.js";

/**
 * Submit a single signed transaction as a 1-tx bundle to Jito's BlockEngine.
 *
 * Direct vs Helius Sender (which forwards to Jito under the hood):
 *   - Saves the Helius → Jito relay hop (~50-100ms on hot launches).
 *   - Returns the tx signature so the caller's confirmation poller works unchanged.
 *
 * Tip requirement: the transaction must already include a SystemProgram.transfer to one of
 * the 8 Jito tip accounts. The bot wires that in via `buildPumpFunBuyTransaction`.
 *
 * Bundle expiry: if the bundle isn't included by the next Jito leader slot, it's silently
 * dropped (NOT retried). Caller should treat broadcast as best-effort and rely on the
 * confirmation poll to detect drops.
 */
export async function broadcastViaJito(tx: VersionedTransaction): Promise<string> {
  const rawTx = bs58.encode(tx.serialize());
  const body = JSON.stringify({
    jsonrpc: "2.0",
    id: 1,
    method: "sendBundle",
    params: [[rawTx]],
  });

  const response = await fetch(jitoBundleUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });

  const json = (await response.json()) as {
    result?: string;
    error?: { message?: string; code?: number };
  };

  if (json.error) {
    const code = json.error.code != null ? ` (code ${json.error.code})` : "";
    throw new Error(`${json.error.message ?? "Jito sendBundle failed"}${code}`);
  }
  if (typeof json.result !== "string") {
    throw new Error(`Jito sendBundle returned no bundle_id. Raw: ${JSON.stringify(json)}`);
  }
  // sendBundle returns a bundle_id, but the caller needs the tx signature for confirmation.
  // The signature is already in the signed tx; extract from tx.signatures[0].
  const sig = tx.signatures[0];
  if (!sig) throw new Error("Jito sendBundle: signed tx has no signatures");
  return bs58.encode(sig);
}
