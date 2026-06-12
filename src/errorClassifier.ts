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
  program?: "pump" | "pump_amm" | "unknown";
  errorName?: string;
  recoverable: boolean;
  summary: string;
  hint: string;
};

function inferProgram(lower: string): "pump" | "pump_amm" | "unknown" {
  if (
    lower.includes("pump_amm") ||
    lower.includes("pump-amm") ||
    lower.includes("pamm") ||
    lower.includes("program pammbay6oceh9fjkbrhgp5d4bd4swpmswmn52fmfxea")
  ) {
    return "pump_amm";
  }
  if (
    lower.includes("pump.fun") ||
    lower.includes("pump fun") ||
    lower.includes("program 6ef8rrecthr5dkzon8nwu78hrvfckubj14m5ubewf6p")
  ) {
    return "pump";
  }
  return "unknown";
}

function knownErrorName(program: "pump" | "pump_amm" | "unknown", code: string | undefined): string | undefined {
  if (!code) return undefined;
  if (program === "pump_amm") {
    return {
      "6004": "ExceededSlippage",
      "6023": "Overflow",
      "6040": "BuySlippageBelowMinBaseAmountOut",
    }[code];
  }
  if (program === "pump") {
    return {
      "6002": "TooMuchSolRequired",
      "6003": "TooLittleSolReceived",
      "6004": "MintDoesNotMatchBondingCurve",
      "6005": "BondingCurveComplete",
      "6021": "NotEnoughTokensToBuy",
      "6023": "NotEnoughTokensToSell",
      "6024": "Overflow",
      "6042": "BuySlippageBelowMinTokensOut",
      "6057": "BuybackFeeRecipientNotAuthorized",
      "6062": "BuybackFeeRecipientMissing",
    }[code];
  }
  return undefined;
}

export function classifyExecutionError(err: unknown): ClassifiedExecutionError {
  const msg = err instanceof Error ? err.message : String(err);
  const lower = msg.toLowerCase();
  const hexCustom = msg.match(/custom program error:\s*0x([0-9a-f]+)/i)?.[1];
  const custom =
    msg.match(/Custom["):\s]*(\d+)/i)?.[1] ??
    msg.match(/Error Number:\s*(\d+)/i)?.[1] ??
    (hexCustom ? String(parseInt(hexCustom, 16)) : undefined);
  const program = inferProgram(lower);
  const errorName = knownErrorName(program, custom);
  const base = { program, errorName };

  if (lower.includes("constraintseeds") || custom === "2006") {
    return {
      ...base,
      category: "account-constraint-seeds",
      code: custom ?? "2006",
      recoverable: true,
      summary: "Derived PDA/account seed mismatch",
      hint: "Rebuild with canonical derived accounts or fresh pool/source state.",
    };
  }
  if (lower.includes("constraintmut") || custom === "2000") {
    return {
      ...base,
      category: "account-constraint-mut",
      code: custom ?? "2000",
      recoverable: true,
      summary: "Expected writable account was not writable",
      hint: "Rebuild account metas from canonical route layout.",
    };
  }
  if (lower.includes("invalidprogramid") || custom === "3008") {
    return {
      ...base,
      category: "account-invalid-program",
      code: custom ?? "3008",
      recoverable: true,
      summary: "Program account slot mismatch",
      hint: "Rebuild account list; do not trust source slot hints blindly.",
    };
  }
  if (lower.includes("privilegeescalation") || lower.includes("unauthorized signer or writable")) {
    return {
      ...base,
      category: "account-privilege",
      recoverable: true,
      summary: "Signer/writable privilege mismatch",
      hint: "Rebuild transaction with canonical writable/signature flags.",
    };
  }
  if (
    lower.includes("idl writable flag mismatch") ||
    lower.includes("idl signer flag mismatch") ||
    lower.includes("idl account count mismatch") ||
    lower.includes("idl account list too short") ||
    lower.includes("idl discriminator mismatch")
  ) {
    return {
      ...base,
      category: "account-layout",
      recoverable: true,
      summary: "Local instruction layout/IDL mismatch",
      hint: "Update the route builder/IDL account metas or rebuild from verified source/canonical accounts.",
    };
  }
  if (
    lower.includes("stale") ||
    lower.includes("expected at least") ||
    lower.includes("account list too short") ||
    (program === "pump" && custom === "6004")
  ) {
    return {
      ...base,
      category: "account-stale-source",
      code: custom,
      recoverable: true,
      summary:
        program === "pump" && custom === "6004"
          ? "Pump.fun mint/bonding-curve account mismatch"
          : "Source account hints are stale or incomplete",
      hint: "Ignore source tx account hints and rebuild from current on-chain state.",
    };
  }
  if (
    (program === "pump_amm" && custom === "6023") ||
    (program === "pump" && (custom === "6021" || custom === "6023" || custom === "6024"))
  ) {
    return {
      ...base,
      category: "liquidity-overflow",
      code: custom,
      recoverable: false,
      summary:
        custom === "6023" && program === "pump"
          ? "Not enough token balance/reserves for requested sell amount"
          : "AMM math overflow / unsafe liquidity state",
      hint:
        custom === "6023" && program === "pump"
          ? "Refresh wallet/token balance and retry smaller sell if still holding."
          : "Skip this route; retrying immediately is unlikely to land.",
    };
  }
  if (lower.includes("insufficient liquidity") || lower.includes("no liquidity")) {
    return {
      ...base,
      category: "liquidity-no-liquidity",
      recoverable: false,
      summary: "Pool has no usable liquidity",
      hint: "Skip until pool state changes.",
    };
  }
  if (
    lower.includes("slippage") ||
    custom === "6002" ||
    custom === "6003" ||
    custom === "6042" ||
    (program === "pump_amm" && (custom === "6004" || custom === "6040"))
  ) {
    return {
      ...base,
      category: "slippage",
      code: custom,
      recoverable: true,
      summary: "Price moved beyond slippage guard",
      hint: "Fresh quote once; widen only if strategy accepts worse fills.",
    };
  }
  if (lower.includes("token_not_tradable") || lower.includes("not tradable")) {
    return {
      ...base,
      category: "not-tradable",
      recoverable: false,
      summary: "Aggregator/token index says token is not tradable",
      hint: "Use direct Pump.fun/PumpSwap route if available; otherwise skip.",
    };
  }
  if (lower.includes("no_routes_found") || lower.includes("no routes found")) {
    return {
      ...base,
      category: "no-route",
      recoverable: false,
      summary: "Aggregator found no executable route",
      hint: "Use direct route for pump mints; otherwise skip until indexed/liquid.",
    };
  }
  if (lower.includes("1232") || lower.includes("too large") || lower.includes("encoding overruns")) {
    return {
      ...base,
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
      ...base,
      category: "dropped-or-expired",
      recoverable: true,
      summary: "Broadcast likely dropped or blockhash expired",
      hint: "Rebuild with fresh blockhash and rebroadcast through alternate sender.",
    };
  }
  if (lower.includes("429") || lower.includes("rate limited") || lower.includes("max usage reached")) {
    return {
      ...base,
      category: "rate-limit",
      recoverable: true,
      summary: "RPC/API rate limit",
      hint: "Back off noisy API path or switch provider; avoid hot-path retries through same API.",
    };
  }
  if (lower.includes("insufficient funds") || lower.includes("insufficient lamports")) {
    return {
      ...base,
      category: "insufficient-funds",
      recoverable: false,
      summary: "Wallet balance is insufficient",
      hint: "Reduce size or top up SOL reserve.",
    };
  }
  if (lower.includes("transaction failed") || lower.includes("instructionerror")) {
    return {
      ...base,
      category: "confirmation-failed",
      code: custom,
      recoverable: custom != null,
      summary: "Transaction landed/finalized with program failure",
      hint: custom ? "Use custom error code and logs to choose a route-specific rebuild." : "Inspect program logs.",
    };
  }
  if (lower.includes("fetch failed") || lower.includes("timeout") || lower.includes("network")) {
    return {
      ...base,
      category: "network",
      recoverable: true,
      summary: "Network/RPC timeout",
      hint: "Retry via alternate endpoint or after short backoff.",
    };
  }
  if (lower.includes("jito")) {
    return {
      ...base,
      category: "sender-jito",
      recoverable: true,
      summary: "Jito sender path failed",
      hint: "Fallback to Helius Sender or RPC broadcast.",
    };
  }
  if (lower.includes("helius")) {
    return {
      ...base,
      category: "sender-helius",
      recoverable: true,
      summary: "Helius sender/RPC path failed",
      hint: "Fallback to Jito or normal RPC if configured.",
    };
  }
  if (lower.includes("sendrawtransaction")) {
    return {
      ...base,
      category: "sender-rpc",
      recoverable: true,
      summary: "Normal RPC broadcast failed",
      hint: "Try sender/Jito path or fresh blockhash.",
    };
  }
  if (lower.includes("sender") || lower.includes("jito") || lower.includes("sendtransaction")) {
    return {
      ...base,
      category: "sender",
      recoverable: true,
      summary: "Sender path failed",
      hint: "Retry through alternate landing path.",
    };
  }
  return {
    ...base,
    category: "unknown",
    code: custom,
    recoverable: true,
    summary: "Unclassified execution failure",
    hint: "Inspect raw error/logs and add a classifier rule if repeated.",
  };
}
