const JUPITER_LITE_QUOTE = "https://lite-api.jup.ag/swap/v1/quote";
const JUPITER_LITE_SWAP = "https://lite-api.jup.ag/swap/v1/swap";

export type QuoteResponse = {
  inputMint?: string;
  outputMint?: string;
  inAmount?: string;
  outAmount?: string;
  otherAmountThreshold?: string;
  swapMode?: string;
  slippageBps?: number;
  priceImpactPct?: string;
  [key: string]: unknown;
};

export type SwapResponse = {
  swapTransaction: string;
  lastValidBlockHeight?: number;
  [key: string]: unknown;
};

/**
 * Get a Jupiter quote for a swap.
 * @param inputMint - Input token mint address
 * @param outputMint - Output token mint address
 * @param amount - Raw amount (in smallest units, before decimals)
 * @param slippageBps - Slippage in basis points (e.g. 50 = 0.5%)
 */
export async function getQuote(
  inputMint: string,
  outputMint: string,
  amount: number,
  slippageBps: number = 50
): Promise<QuoteResponse> {
  const url = `${JUPITER_LITE_QUOTE}?inputMint=${encodeURIComponent(inputMint)}&outputMint=${encodeURIComponent(outputMint)}&amount=${amount}&slippageBps=${slippageBps}`;
  const response = await fetch(url);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Jupiter quote failed: ${response.status} ${text}`);
  }
  return (await response.json()) as QuoteResponse;
}

/** Get a swap transaction from Jupiter using a quote. */
export async function getSwap(
  quote: QuoteResponse,
  userPublicKey: string
): Promise<SwapResponse> {
  const response = await fetch(JUPITER_LITE_SWAP, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      quoteResponse: quote,
      userPublicKey,
      dynamicComputeUnitLimit: true,
    }),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Jupiter swap failed: ${response.status} ${text}`);
  }
  return (await response.json()) as SwapResponse;
}
