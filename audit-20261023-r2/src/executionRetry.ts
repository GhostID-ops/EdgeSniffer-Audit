import { classifyExecutionError, type ClassifiedExecutionError } from "./errorClassifier.js";

export type RetryAction =
  | "rebuild-derived-accounts"
  | "compact-transaction"
  | "fresh-blockhash-rebroadcast"
  | "fresh-quote"
  | "rebuild-pool-state"
  | "skip"
  | "retry";

export type RetryDecision = ClassifiedExecutionError & {
  action: RetryAction;
  shouldQuarantine: boolean;
};

export function decideRetryAction(err: unknown): RetryDecision {
  const raw = err instanceof Error ? err.message : String(err ?? "");
  const lower = raw.toLowerCase();
  const classified = classifyExecutionError(err);
  switch (classified.category) {
    case "account-constraint-seeds":
    case "account-constraint-mut":
    case "account-invalid-program":
    case "account-privilege":
    case "account-stale-source":
    case "account-layout":
      return {
        ...classified,
        action: "rebuild-derived-accounts",
        shouldQuarantine: false,
      };
    case "oversize":
      return {
        ...classified,
        action: "compact-transaction",
        shouldQuarantine: false,
      };
    case "dropped-or-expired":
    case "sender":
    case "sender-jito":
    case "sender-helius":
    case "sender-rpc":
    case "network":
    case "rate-limit":
      return {
        ...classified,
        action: "fresh-blockhash-rebroadcast",
        shouldQuarantine: false,
      };
    case "slippage":
      return {
        ...classified,
        action: "fresh-quote",
        shouldQuarantine: false,
      };
    case "liquidity-overflow":
      if (classified.program === "pump_amm") {
        if (lower.includes("canonical-pool-state")) {
          return {
            ...classified,
            action: "skip",
            shouldQuarantine: true,
          };
        }
        return {
          ...classified,
          action: "rebuild-pool-state",
          shouldQuarantine: false,
        };
      }
      return {
        ...classified,
        action: "skip",
        shouldQuarantine: true,
      };
    case "liquidity-no-liquidity":
    case "liquidity":
    case "not-tradable":
    case "no-route":
    case "insufficient-funds":
      return {
        ...classified,
        action: "skip",
        shouldQuarantine: true,
      };
    default:
      return {
        ...classified,
        action: classified.recoverable ? "retry" : "skip",
        shouldQuarantine: classified.recoverable === false,
      };
  }
}
