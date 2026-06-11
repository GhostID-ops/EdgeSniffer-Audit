import type { VersionedTransaction } from "@solana/web3.js";
import bs58 from "bs58";
import { jitoAuthUuid, jitoBundleUrls, jitoRegionRaceEnabled } from "./config.js";

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
let nextRegionIndex = 0;

function isRateLimitError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return /rate.?limit|too many requests|429/i.test(msg);
}

async function sendBundleToUrl(
  url: string,
  txBase64: string
): Promise<string> {
  const body = JSON.stringify({
    jsonrpc: "2.0",
    id: 1,
    method: "sendBundle",
    params: [[txBase64], { encoding: "base64" }],
  });

  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (jitoAuthUuid) headers["x-jito-auth"] = jitoAuthUuid;

  const response = await fetch(url, {
    method: "POST",
    headers,
    body,
  });

  const json = (await response.json()) as {
    result?: string;
    error?: { message?: string; code?: number };
  };

  if (json.error) {
    const code = json.error.code != null ? ` (code ${json.error.code})` : "";
    throw new Error(`${json.error.message ?? "Jito sendBundle failed"}${code} via ${url}`);
  }
  if (typeof json.result !== "string") {
    throw new Error(`Jito sendBundle returned no bundle_id via ${url}. Raw: ${JSON.stringify(json)}`);
  }
  return json.result;
}

export async function broadcastViaJito(tx: VersionedTransaction): Promise<string> {
  const txBase64 = Buffer.from(tx.serialize()).toString("base64");
  const urls = jitoBundleUrls.length > 0 ? jitoBundleUrls : [];
  if (urls.length === 0) throw new Error("No Jito bundle URL configured");

  const start = nextRegionIndex % urls.length;
  nextRegionIndex = (nextRegionIndex + 1) % urls.length;
  const orderedUrls = [...urls.slice(start), ...urls.slice(0, start)];

  if (jitoRegionRaceEnabled && orderedUrls.length > 1) {
    const errors: string[] = [];
    try {
      await Promise.any(
        orderedUrls.map(async (url) => {
          try {
            return await sendBundleToUrl(url, txBase64);
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            errors.push(msg);
            throw err;
          }
        })
      );
    } catch {
      throw new Error(`Jito sendBundle failed in all configured regions: ${errors.join(" | ")}`);
    }
  } else {
    const errors: string[] = [];
    for (const url of orderedUrls) {
      try {
        await sendBundleToUrl(url, txBase64);
        break;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        errors.push(msg);
        if (!isRateLimitError(err) || orderedUrls.length === 1) {
          throw err;
        }
        console.warn(`Jito region rate-limited; trying next region: ${msg}`);
      }
    }

    if (errors.length === orderedUrls.length) {
      throw new Error(`Jito sendBundle failed in all configured regions: ${errors.join(" | ")}`);
    }
  }

  // sendBundle returns a bundle_id, but the caller needs the tx signature for confirmation.
  // The signature is already in the signed tx; extract from tx.signatures[0].
  const sig = tx.signatures[0];
  if (!sig) throw new Error("Jito sendBundle: signed tx has no signatures");
  return bs58.encode(sig);
}
