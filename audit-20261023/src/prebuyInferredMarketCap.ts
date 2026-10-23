import { LAMPORTS_PER_SOL } from "@solana/web3.js";
import {
  localPumpfunSolPriceUsd,
  prebuyFilterFailOpen,
  prebuyInferredDefaultTokenDecimals,
  prebuyInferredTokenSupply,
  prebuyMaxMarketCapUsd,
  prebuyMinMarketCapUsd,
} from "./config.js";
import type { DetectedSwap } from "./types.js";
import { USDC_MINT, WRAPPED_SOL_MINT } from "./tokenRegistry.js";

export type InferredMarketCapFilterResult = {
  allowed: boolean;
  reason: string;
  marketCapUsd?: number | null;
};

function short(mint: string): string {
  return `${mint.slice(0, 8)}...`;
}

function formatUsd(value: number): string {
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `$${(value / 1_000).toFixed(1)}K`;
  return `$${value.toFixed(0)}`;
}

function failUnavailable(mint: string, detail: string): InferredMarketCapFilterResult {
  return {
    allowed: prebuyFilterFailOpen,
    reason: `prebuy inferred MC unavailable for ${short(mint)}: ${detail} (${prebuyFilterFailOpen ? "fail-open" : "fail-closed"})`,
  };
}

function quoteUsdForBuy(swap: DetectedSwap): number | null {
  if (swap.inputMint === WRAPPED_SOL_MINT) {
    if (localPumpfunSolPriceUsd == null) return null;
    return (swap.amountRaw / LAMPORTS_PER_SOL) * localPumpfunSolPriceUsd;
  }
  if (swap.inputMint === USDC_MINT) {
    return swap.amountRaw / 1_000_000;
  }
  return null;
}

export function checkPrebuyInferredMarketCap(swap: DetectedSwap): InferredMarketCapFilterResult {
  const mint = swap.outputMint;
  if (prebuyMinMarketCapUsd == null && prebuyMaxMarketCapUsd == null) {
    return { allowed: true, reason: "prebuy inferred MC skipped: no MC threshold configured" };
  }

  const quoteUsd = quoteUsdForBuy(swap);
  if (quoteUsd == null || !Number.isFinite(quoteUsd) || quoteUsd <= 0) {
    return failUnavailable(
      mint,
      swap.inputMint === WRAPPED_SOL_MINT
        ? "missing LOCAL_PUMPFUN_SOL_PRICE_USD/SOL_PRICE_USD for SOL source buy"
        : `unsupported quote mint ${short(swap.inputMint)}`
    );
  }

  const tokenRaw = swap.counterpartyAmountRaw;
  if (tokenRaw == null || !Number.isFinite(tokenRaw) || tokenRaw <= 0) {
    return failUnavailable(mint, "source token amount missing from detector");
  }

  const decimals =
    swap.counterpartyDecimals != null && Number.isFinite(swap.counterpartyDecimals)
      ? Math.max(0, Math.min(18, Math.floor(swap.counterpartyDecimals)))
      : prebuyInferredDefaultTokenDecimals;
  const tokensOut = tokenRaw / 10 ** decimals;
  if (!Number.isFinite(tokensOut) || tokensOut <= 0) {
    return failUnavailable(mint, `invalid source token amount (${tokenRaw} raw, decimals=${decimals})`);
  }

  const marketCapUsd = (quoteUsd / tokensOut) * prebuyInferredTokenSupply;
  if (!Number.isFinite(marketCapUsd) || marketCapUsd <= 0) {
    return failUnavailable(mint, "fill price produced invalid MC");
  }

  const label = `${formatUsd(marketCapUsd)} from source fill (${formatUsd(quoteUsd)} / ${tokensOut.toLocaleString(undefined, {
    maximumFractionDigits: 2,
  })} tokens, supply=${prebuyInferredTokenSupply.toLocaleString()})`;
  if (prebuyMinMarketCapUsd != null && marketCapUsd < prebuyMinMarketCapUsd) {
    return {
      allowed: false,
      reason: `prebuy inferred MC ${label} < ${formatUsd(prebuyMinMarketCapUsd)}`,
      marketCapUsd,
    };
  }
  if (prebuyMaxMarketCapUsd != null && marketCapUsd > prebuyMaxMarketCapUsd) {
    return {
      allowed: false,
      reason: `prebuy inferred MC ${label} > ${formatUsd(prebuyMaxMarketCapUsd)}`,
      marketCapUsd,
    };
  }

  return {
    allowed: true,
    reason: `prebuy inferred MC passed: ${label}`,
    marketCapUsd,
  };
}
