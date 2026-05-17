import {
  jupiterQuoteMinGapMs,
  jupiterQuoteRateLimitCooldownMs,
  jupiterQuoteRetries,
} from "./config.js";

const JUPITER_LITE_QUOTE = "https://lite-api.jup.ag/swap/v1/quote";
const JUPITER_LITE_SWAP = "https://lite-api.jup.ag/swap/v1/swap";
let quoteGate = Promise.resolve();
let nextQuoteAt = 0;
let quoteCooldownUntil = 0;

export class JupiterRateLimitError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "JupiterRateLimitError";
  }
}

export function isJupiterRateLimitError(err: unknown): boolean {
  return err instanceof JupiterRateLimitError || String(err).includes("Jupiter quote failed: 429");
}

async function sleep(ms: number): Promise<void> {
  if (ms > 0) await new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForQuoteSlot(): Promise<void> {
  const run = quoteGate.then(async () => {
    const now = Date.now();
    const waitMs = Math.max(nextQuoteAt - now, quoteCooldownUntil - now, 0);
    await sleep(waitMs);
    nextQuoteAt = Date.now() + jupiterQuoteMinGapMs;
  });
  quoteGate = run.catch(() => undefined);
  await run;
}

function noteQuoteRateLimit(response: Response): void {
  const retryAfter = response.headers.get("retry-after");
  const retryAfterMs = retryAfter ? Number(retryAfter) * 1000 : 0;
  quoteCooldownUntil = Math.max(
    quoteCooldownUntil,
    Date.now() + Math.max(jupiterQuoteRateLimitCooldownMs, retryAfterMs)
  );
}

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

export type QuoteOptions = {
  onlyDirectRoutes?: boolean;
  maxAccounts?: number;
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
  slippageBps: number = 50,
  options: QuoteOptions = {}
): Promise<QuoteResponse> {
  const params = new URLSearchParams({
    inputMint,
    outputMint,
    amount: String(amount),
    slippageBps: String(slippageBps),
  });
  if (options.onlyDirectRoutes) params.set("onlyDirectRoutes", "true");
  if (options.maxAccounts != null) params.set("maxAccounts", String(options.maxAccounts));
  const url = `${JUPITER_LITE_QUOTE}?${params.toString()}`;

  for (let attempt = 0; attempt <= jupiterQuoteRetries; attempt++) {
    await waitForQuoteSlot();
    const response = await fetch(url);
    if (response.ok) return (await response.json()) as QuoteResponse;

    const text = await response.text();
    if (response.status === 429) {
      noteQuoteRateLimit(response);
      if (attempt < jupiterQuoteRetries) continue;
      throw new JupiterRateLimitError(`Jupiter quote failed: 429 ${text}`);
    }

    throw new Error(`Jupiter quote failed: ${response.status} ${text}`);
  }

  throw new Error("Jupiter quote failed");
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
