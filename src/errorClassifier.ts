export type ExecutionErrorClass =
  | "account-layout"
  | "liquidity"
  | "slippage"
  | "not-tradable"
  | "no-route"
  | "oversize"
  | "dropped-or-expired"
  | "network"
  | "sender"
  | "unknown";

export type ClassifiedExecutionError = {
  category: ExecutionErrorClass;
  code?: string;
  recoverable: boolean;
};

export function classifyExecutionError(err: unknown): ClassifiedExecutionError {
  const msg = err instanceof Error ? err.message : String(err);
  const lower = msg.toLowerCase();
  const custom = msg.match(/Custom["):\s]*(\d+)/i)?.[1];

  if (custom === "2006" || custom === "3008" || custom === "6002" || custom === "6004") {
    return { category: "account-layout", code: custom, recoverable: true };
  }
  if (custom === "6023" || custom === "6042") {
    return { category: "liquidity", code: custom, recoverable: false };
  }
  if (lower.includes("privilegeescalation") || lower.includes("unauthorized signer or writable")) {
    return { category: "account-layout", recoverable: true };
  }
  if (lower.includes("slippage") || lower.includes("6001")) {
    return { category: "slippage", code: custom ?? "6001", recoverable: true };
  }
  if (lower.includes("token_not_tradable") || lower.includes("not tradable")) {
    return { category: "not-tradable", recoverable: false };
  }
  if (lower.includes("no_routes_found") || lower.includes("no routes found")) {
    return { category: "no-route", recoverable: false };
  }
  if (lower.includes("1232") || lower.includes("too large") || lower.includes("encoding overruns")) {
    return { category: "oversize", recoverable: true };
  }
  if (
    lower.includes("not found in status polling") ||
    lower.includes("blockhash not found") ||
    lower.includes("transaction expired") ||
    lower.includes("block height exceeded")
  ) {
    return { category: "dropped-or-expired", recoverable: true };
  }
  if (lower.includes("fetch failed") || lower.includes("timeout") || lower.includes("network")) {
    return { category: "network", recoverable: true };
  }
  if (lower.includes("sender") || lower.includes("jito") || lower.includes("sendtransaction")) {
    return { category: "sender", recoverable: true };
  }
  return { category: "unknown", code: custom, recoverable: true };
}

