const JUPITER_LITE_QUOTE = "https://lite-api.jup.ag/swap/v1/quote";
const JUPITER_LITE_SWAP = "https://lite-api.jup.ag/swap/v1/swap";
const MAX_ERROR_BODY_CHARS = 500;

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

function compactBody(text: string): string {
  const compact = text.replace(/\s+/g, " ").trim();
  return compact.length > MAX_ERROR_BODY_CHARS
    ? `${compact.slice(0, MAX_ERROR_BODY_CHARS)}...`
    : compact;
}

function isJsonResponse(response: Response): boolean {
  return response.headers.get("content-type")?.toLowerCase().includes("application/json") ?? false;
}

async function readJsonOrThrow<T>(response: Response, label: string): Promise<T> {
  const text = await response.text();
  const body = compactBody(text);

  if (!response.ok) {
    throw new Error(`${label} failed: HTTP ${response.status}${body ? ` ${body}` : ""}`);
  }

  if (!isJsonResponse(response)) {
    throw new Error(
      `${label} returned non-JSON response (${response.headers.get("content-type") ?? "unknown content-type"}): ${body || "empty body"}`
    );
  }

  try {
    return JSON.parse(text) as T;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new Error(`${label} returned invalid JSON: ${msg}${body ? `; body: ${body}` : ""}`);
  }
}

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
  return readJsonOrThrow<QuoteResponse>(response, "Jupiter quote");
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
  return readJsonOrThrow<SwapResponse>(response, "Jupiter swap");
}
