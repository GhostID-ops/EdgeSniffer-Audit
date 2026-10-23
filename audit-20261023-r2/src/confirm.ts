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

export type ConfirmationResult = {
  confirmationStatus: "confirmed" | "finalized";
  slot: number | null;
};

const lastConfirmationResults = new Map<string, ConfirmationResult>();
const MAX_CONFIRMATION_RESULTS = 2_000;

function rememberConfirmation(signature: string, result: ConfirmationResult): void {
  if (lastConfirmationResults.size >= MAX_CONFIRMATION_RESULTS) {
    const oldest = lastConfirmationResults.keys().next().value;
    if (oldest != null) lastConfirmationResults.delete(oldest);
  }
  lastConfirmationResults.set(signature, result);
}

export function getLastConfirmationResult(signature: string): ConfirmationResult | null {
  return lastConfirmationResults.get(signature) ?? null;
}

export function isConfirmationTimeoutError(err: unknown): boolean {
  return err instanceof ConfirmationTimeoutError;
}

/**
 * String-based variant for call sites that only have the error *message* (e.g. failure
 * callbacks that receive `reason: string`). Matches the messages thrown by
 * `ConfirmationTimeoutError` and the "submitted but did not confirm" fallback. Status
 * absence is not proof of failure: a Sender/Jito transaction can land after this read
 * RPC's status window, so callers should retain the optimistic lot rather than roll back.
 */
export function isConfirmationTimeoutMessage(reason: string): boolean {
  return /confirmation timed out after \d+ms|not found in status polling after \d+ms|suspected dropped transaction|did not confirm after \d+ms/i.test(
    reason
  );
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
  let lastPollError: unknown = null;

  while (Date.now() - start < timeoutMs) {
    let result;
    try {
      result = await readConnection.getSignatureStatuses([signature]);
      lastPollError = null;
    } catch (err) {
      // readConnection deliberately disables web3.js's unbounded 429 retry.
      // Keep this retry bounded, and never turn a transient RPC failure into
      // an alleged on-chain transaction failure.
      lastPollError = err;
      await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
      continue;
    }
    const status = result.value[0];
    const elapsedMs = Date.now() - start;

    if (status?.err) {
      throw new Error(`Transaction failed: ${JSON.stringify(status.err)}`);
    }
    if (status) {
      sawStatus = true;
    }
    if (status?.confirmationStatus === "finalized") {
      rememberConfirmation(signature, { confirmationStatus: "finalized", slot: status.slot ?? null });
      return "finalized";
    }
    if (status?.confirmationStatus === "confirmed") {
      rememberConfirmation(signature, { confirmationStatus: "confirmed", slot: status.slot ?? null });
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
  let finalResult;
  try {
    finalResult = await readConnection.getSignatureStatuses([signature]);
  } catch (err) {
    lastPollError = err;
    throw new ConfirmationTimeoutError(signature, Date.now() - start, sawStatus);
  }
  const finalStatus = finalResult.value[0];

  if (finalStatus?.err) {
    throw new Error(`Transaction failed: ${JSON.stringify(finalStatus.err)}`);
  }
  if (finalStatus?.confirmationStatus === "finalized") {
    console.log("Transaction was actually finalized (late confirmation)");
    rememberConfirmation(signature, { confirmationStatus: "finalized", slot: finalStatus.slot ?? null });
    return "finalized";
  }
  if (finalStatus?.confirmationStatus === "confirmed") {
    console.log("Transaction was actually confirmed (late confirmation)");
    rememberConfirmation(signature, { confirmationStatus: "confirmed", slot: finalStatus.slot ?? null });
    return "confirmed";
  }

  console.log(
    `Transaction was submitted but did not confirm after ${timeoutMs}ms` +
      (lastPollError ? ` (last status poll error: ${String(lastPollError)})` : "")
  );
  console.log(`Solscan: https://solscan.io/tx/${signature}`);
  console.log(`Solana Explorer: https://explorer.solana.com/tx/${signature}`);
  throw new ConfirmationTimeoutError(signature, timeoutMs, sawStatus);
}
