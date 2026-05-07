import { Keypair } from "@solana/web3.js";
import { getQuote, getSwap } from "./jupiter.js";
import {
  createSenderTransactionFromSwapResponse,
  broadcastTransactionWithSender,
} from "./sender.js";
import { confirmSignature } from "./confirm.js";

const SOL_MINT = "So11111111111111111111111111111111111111112";

export interface SwapResult {
  signature: string;
  status: "confirmed" | "finalized";
  inAmount?: string;
  outAmount?: string;
}

export async function buyTokenWithSol(
  outputMint: string,
  inputLamports: number,
  slippageBps: number,
  keypair: Keypair
): Promise<SwapResult> {
  return runSwap("BUY", SOL_MINT, outputMint, inputLamports, slippageBps, keypair);
}

export async function sellTokenToSol(
  inputMint: string,
  inputRawAmount: number,
  slippageBps: number,
  keypair: Keypair
): Promise<SwapResult> {
  return runSwap("SELL", inputMint, SOL_MINT, inputRawAmount, slippageBps, keypair);
}

function ts(): string {
  return new Date().toISOString().slice(11, 23);
}

function shortMint(mint: string): string {
  return `${mint.slice(0, 8)}…${mint.slice(-4)}`;
}

async function runSwap(
  label: "BUY" | "SELL",
  inputMint: string,
  outputMint: string,
  amount: number,
  slippageBps: number,
  keypair: Keypair
): Promise<SwapResult> {
  const tag = label === "BUY" ? shortMint(outputMint) : shortMint(inputMint);
  const start = Date.now();
  console.log(
    `[${ts()}] ${label} ${tag} · in=${amount} slippage=${slippageBps}bps`
  );

  let t = Date.now();
  const quote = await getQuote(inputMint, outputMint, amount, slippageBps);
  console.log(
    `[${ts()}] ${label} ${tag} · quote OK in ${Date.now() - t}ms · in=${
      quote.inAmount ?? "?"
    } out=${quote.outAmount ?? "?"} priceImpact=${
      quote.priceImpactPct ?? "?"
    }`
  );

  t = Date.now();
  const swap = await getSwap(quote, keypair.publicKey.toBase58());
  console.log(
    `[${ts()}] ${label} ${tag} · swap fetched in ${Date.now() - t}ms`
  );

  t = Date.now();
  const transaction = await createSenderTransactionFromSwapResponse(
    swap as { swapTransaction: string; [key: string]: unknown },
    keypair
  );
  console.log(
    `[${ts()}] ${label} ${tag} · tx built+signed in ${Date.now() - t}ms`
  );

  t = Date.now();
  const { signature, bundleId } = await broadcastTransactionWithSender(transaction);
  console.log(
    `[${ts()}] ${label} ${tag} · broadcasted in ${Date.now() - t}ms · sig=${signature.slice(
      0,
      16
    )}…${bundleId ? ` · bundle=${bundleId.slice(0, 16)}…` : ""}`
  );

  t = Date.now();
  const status = await confirmSignature(signature);
  const totalSec = ((Date.now() - start) / 1000).toFixed(2);
  console.log(
    `[${ts()}] ${label} ${tag} · ${status} in ${Date.now() - t}ms · TOTAL=${totalSec}s`
  );

  return {
    signature,
    status,
    inAmount: typeof quote.inAmount === "string" ? quote.inAmount : undefined,
    outAmount: typeof quote.outAmount === "string" ? quote.outAmount : undefined,
  };
}
