export type ExecutionErrorClass =
  | "account-constraint-seeds"
  | "account-constraint-mut"
  | "account-invalid-program"
  | "account-privilege"
  | "account-stale-source"
  | "account-layout"
  | "liquidity-overflow"
  | "liquidity-no-liquidity"
  | "liquidity"
  | "slippage"
  | "not-tradable"
  | "no-route"
  | "oversize"
  | "dropped-or-expired"
  | "rate-limit"
  | "insufficient-funds"
  | "confirmation-failed"
  | "sender-jito"
  | "sender-helius"
  | "sender-rpc"
  | "network"
  | "sender"
  | "unknown";

export type ClassifiedExecutionError = {
  category: ExecutionErrorClass;
  code?: string;
  recoverable: boolean;
  summary: string;
  hint: string;
};

export function classifyExecutionError(err: unknown): ClassifiedExecutionError {
  const msg = err instanceof Error ? err.message : String(err);
  const lower = msg.toLowerCase();
  const custom = msg.match(/Custom["):\s]*(\d+)/i)?.[1];

  if (lower.includes("constraintseeds") || custom === "2006") {
    return {
      category: "account-constraint-seeds",
      code: custom ?? "2006",
      recoverable: true,
      summary: "Derived PDA/account seed mismatch",
      hint: "Rebuild with canonical derived accounts or fresh pool/source state.",
    };
  }
  if (lower.includes("constraintmut") || custom === "2000") {
    return {
      category: "account-constraint-mut",
      code: custom ?? "2000",
      recoverable: true,
      summary: "Expected writable account was not writable",
      hint: "Rebuild account metas from canonical route layout.",
    };
  }
  if (lower.includes("invalidprogramid") || custom === "3008") {
    return {
      category: "account-invalid-program",
      code: custom ?? "3008",
      recoverable: true,
      summary: "Program account slot mismatch",
      hint: "Rebuild account list; do not trust source slot hints blindly.",
    };
  }
  if (lower.includes("privilegeescalation") || lower.includes("unauthorized signer or writable")) {
    return {
      category: "account-privilege",
      recoverable: true,
      summary: "Signer/writable privilege mismatch",
      hint: "Rebuild transaction with canonical writable/signature flags.",
    };
  }
  if (
    lower.includes("stale") ||
    lower.includes("expected at least") ||
    lower.includes("account list too short") ||
    custom === "6002" ||
    custom === "6004"
  ) {
    return {
      category: "account-stale-source",
      code: custom,
      recoverable: true,
      summary: "Source account hints are stale or incomplete",
      hint: "Ignore source tx account hints and rebuild from current on-chain state.",
    };
  }
  if (custom === "6023" || custom === "6042") {
    return {
      category: "liquidity-overflow",
      code: custom,
      recoverable: false,
      summary: "AMM math overflow / unsafe liquidity state",
      hint: "Skip this route; retrying immediately is unlikely to land.",
    };
  }
  if (lower.includes("insufficient liquidity") || lower.includes("no liquidity")) {
    return {
      category: "liquidity-no-liquidity",
      recoverable: false,
      summary: "Pool has no usable liquidity",
      hint: "Skip until pool state changes.",
    };
  }
  if (lower.includes("slippage") || lower.includes("6001")) {
    return {
      category: "slippage",
      code: custom ?? "6001",
      recoverable: true,
      summary: "Price moved beyond slippage guard",
      hint: "Fresh quote once; widen only if strategy accepts worse fills.",
    };
  }
  if (lower.includes("token_not_tradable") || lower.includes("not tradable")) {
    return {
      category: "not-tradable",
      recoverable: false,
      summary: "Aggregator/token index says token is not tradable",
      hint: "Use direct Pump.fun/PumpSwap route if available; otherwise skip.",
    };
  }
  if (lower.includes("no_routes_found") || lower.includes("no routes found")) {
    return {
      category: "no-route",
      recoverable: false,
      summary: "Aggregator found no executable route",
      hint: "Use direct route for pump mints; otherwise skip until indexed/liquid.",
    };
  }
  if (lower.includes("1232") || lower.includes("too large") || lower.includes("encoding overruns")) {
    return {
      category: "oversize",
      recoverable: true,
      summary: "Transaction exceeds packet/encoding limit",
      hint: "Compact transaction, drop optional tips/ATA setup, or use ALT-supported route.",
    };
  }
  if (
    lower.includes("not found in status polling") ||
    lower.includes("blockhash not found") ||
    lower.includes("transaction expired") ||
    lower.includes("block height exceeded")
  ) {
    return {
      category: "dropped-or-expired",
      recoverable: true,
      summary: "Broadcast likely dropped or blockhash expired",
      hint: "Rebuild with fresh blockhash and rebroadcast through alternate sender.",
    };
  }
  if (lower.includes("429") || lower.includes("rate limited") || lower.includes("max usage reached")) {
    return {
      category: "rate-limit",
      recoverable: true,
      summary: "RPC/API rate limit",
      hint: "Back off noisy API path or switch provider; avoid hot-path retries through same API.",
    };
  }
  if (lower.includes("insufficient funds") || lower.includes("insufficient lamports")) {
    return {
      category: "insufficient-funds",
      recoverable: false,
      summary: "Wallet balance is insufficient",
      hint: "Reduce size or top up SOL reserve.",
    };
  }
  if (lower.includes("transaction failed") || lower.includes("instructionerror")) {
    return {
      category: "confirmation-failed",
      code: custom,
      recoverable: custom != null,
      summary: "Transaction landed/finalized with program failure",
      hint: custom ? "Use custom error code and logs to choose a route-specific rebuild." : "Inspect program logs.",
    };
  }
  if (lower.includes("fetch failed") || lower.includes("timeout") || lower.includes("network")) {
    return {
      category: "network",
      recoverable: true,
      summary: "Network/RPC timeout",
      hint: "Retry via alternate endpoint or after short backoff.",
    };
  }
  if (lower.includes("jito")) {
    return {
      category: "sender-jito",
      recoverable: true,
      summary: "Jito sender path failed",
      hint: "Fallback to Helius Sender or RPC broadcast.",
    };
  }
  if (lower.includes("helius")) {
    return {
      category: "sender-helius",
      recoverable: true,
      summary: "Helius sender/RPC path failed",
      hint: "Fallback to Jito or normal RPC if configured.",
    };
  }
  if (lower.includes("sendrawtransaction")) {
    return {
      category: "sender-rpc",
      recoverable: true,
      summary: "Normal RPC broadcast failed",
      hint: "Try sender/Jito path or fresh blockhash.",
    };
  }
  if (lower.includes("sender") || lower.includes("jito") || lower.includes("sendtransaction")) {
    return {
      category: "sender",
      recoverable: true,
      summary: "Sender path failed",
      hint: "Retry through alternate landing path.",
    };
  }
  return {
    category: "unknown",
    code: custom,
    recoverable: true,
    summary: "Unclassified execution failure",
    hint: "Inspect raw error/logs and add a classifier rule if repeated.",
  };
}
