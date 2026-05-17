import {
  confirmationNullStatusTimeoutMs,
  confirmationTimeoutMs,
  readConnection,
} from "./config.js";

const POLL_INTERVAL_MS = 1000;

export class ConfirmationTimeoutError extends Error {
  constructor(
    readonly signature: string,
    readonly timeoutMs: number,
    readonly sawStatus: boolean
  ) {
    super(
      sawStatus
        ? `Transaction ${signature} confirmation timed out after ${timeoutMs}ms; not recording trade`
        : `Transaction ${signature} not found in status polling after ${timeoutMs}ms; suspected dropped transaction`
    );
    this.name = "ConfirmationTimeoutError";
  }
}

export type ConfirmSignatureOptions = {
  timeoutMs?: number;
  nullStatusTimeoutMs?: number;
};

export function isConfirmationTimeoutError(err: unknown): boolean {
  return err instanceof ConfirmationTimeoutError;
}

/**
 * Confirm a transaction by polling getSignatureStatuses.
 * Stops on confirmed/finalized, throws on failure or timeout.
 */
export async function confirmSignature(
  signature: string,
  options: ConfirmSignatureOptions = {}
): Promise<"confirmed" | "finalized"> {
  const start = Date.now();
  const timeoutMs = Math.max(1_000, options.timeoutMs ?? confirmationTimeoutMs);
  const nullTimeoutMs = Math.max(
    1_000,
    options.nullStatusTimeoutMs ?? confirmationNullStatusTimeoutMs
  );
  let sawStatus = false;

  while (Date.now() - start < timeoutMs) {
    const result = await readConnection.getSignatureStatuses([signature]);
    const status = result.value[0];
    const elapsedMs = Date.now() - start;

    if (status?.err) {
      throw new Error(`Transaction failed: ${JSON.stringify(status.err)}`);
    }
    if (status) {
      sawStatus = true;
    }
    if (status?.confirmationStatus === "finalized") {
      return "finalized";
    }
    if (status?.confirmationStatus === "confirmed") {
      return "confirmed";
    }
    if (!sawStatus && elapsedMs >= nullTimeoutMs) {
      // During Jito drops the signature often never propagates; fail fast so callers can rebuild.
      throw new ConfirmationTimeoutError(signature, elapsedMs, false);
    }

    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }

  // Final check - maybe the transaction confirmed but we missed it.
  console.log(`Confirmation timed out, doing final check for ${signature}...`);
  const finalResult = await readConnection.getSignatureStatuses([signature]);
  const finalStatus = finalResult.value[0];

  if (finalStatus?.err) {
    throw new Error(`Transaction failed: ${JSON.stringify(finalStatus.err)}`);
  }
  if (finalStatus?.confirmationStatus === "finalized") {
    console.log("Transaction was actually finalized (late confirmation)");
    return "finalized";
  }
  if (finalStatus?.confirmationStatus === "confirmed") {
    console.log("Transaction was actually confirmed (late confirmation)");
    return "confirmed";
  }

  console.log(`Transaction was submitted but did not confirm after ${timeoutMs}ms`);
  console.log(`Solscan: https://solscan.io/tx/${signature}`);
  console.log(`Solana Explorer: https://explorer.solana.com/tx/${signature}`);
  throw new ConfirmationTimeoutError(signature, timeoutMs, sawStatus);
}
