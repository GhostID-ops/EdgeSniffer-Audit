import { readConnection } from "./config.js";

const POLL_INTERVAL_MS = 1000;
const TIMEOUT_MS = 120_000;

function describeTransactionError(err: unknown): string {
  const raw = JSON.stringify(err);
  const match = raw.match(/"Custom":(\d+)/);
  if (match?.[1] === "6024") {
    return `${raw} (Jupiter InsufficientFunds: insufficient token balance, SOL for fees, or rent)`;
  }
  return raw;
}

/**
 * Confirm a transaction by polling getSignatureStatuses.
 * Stops on confirmed/finalized, throws on failure or timeout.
 */
export async function confirmSignature(
  signature: string
): Promise<"confirmed" | "finalized"> {
  const start = Date.now();

  while (Date.now() - start < TIMEOUT_MS) {
    const result = await readConnection.getSignatureStatuses([signature]);
    const status = result.value[0];

    if (status?.err) {
      throw new Error(`Transaction failed: ${describeTransactionError(status.err)}`);
    }
    if (status?.confirmationStatus === "finalized") {
      return "finalized";
    }
    if (status?.confirmationStatus === "confirmed") {
      return "confirmed";
    }

    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }

  // Final check - maybe the transaction confirmed but we missed it.
  console.log(`Confirmation timed out, doing final check for ${signature}...`);
  const finalResult = await readConnection.getSignatureStatuses([signature]);
  const finalStatus = finalResult.value[0];

  if (finalStatus?.err) {
    throw new Error(`Transaction failed: ${describeTransactionError(finalStatus.err)}`);
  }
  if (finalStatus?.confirmationStatus === "finalized") {
    console.log("Transaction was actually finalized (late confirmation)");
    return "finalized";
  }
  if (finalStatus?.confirmationStatus === "confirmed") {
    console.log("Transaction was actually confirmed (late confirmation)");
    return "confirmed";
  }

  console.log(`Transaction was submitted but did not confirm after ${TIMEOUT_MS}ms`);
  console.log(`Solscan: https://solscan.io/tx/${signature}`);
  console.log(`Solana Explorer: https://explorer.solana.com/tx/${signature}`);
  throw new Error(
    `Transaction ${signature} confirmation timed out after ${TIMEOUT_MS}ms; not recording trade`
  );
}
