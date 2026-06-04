import { Keypair } from "@solana/web3.js";
import bs58 from "bs58";
import { getPrivateKeyBase58 } from "./config.js";
import { getQuote, getSwap } from "./jupiter.js";
import { createSenderTransactionFromSwapResponse, broadcastTransactionWithSender } from "./sender.js";
import { confirmSignature } from "./confirm.js";

const DEFAULT_INPUT_MINT = "So11111111111111111111111111111111111111112";
const DEFAULT_OUTPUT_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const DEFAULT_AMOUNT = 1_000_000;
const DEFAULT_SLIPPAGE_BPS = 50;

async function main(): Promise<void> {
  const keypair = Keypair.fromSecretKey(
    bs58.decode(getPrivateKeyBase58())
  );

  const inputMint = process.env.SWAP_INPUT_MINT ?? DEFAULT_INPUT_MINT;
  const outputMint = process.env.SWAP_OUTPUT_MINT ?? DEFAULT_OUTPUT_MINT;
  const amount = parseInt(process.env.SWAP_AMOUNT ?? String(DEFAULT_AMOUNT), 10);
  const slippageBps = parseInt(process.env.SWAP_SLIPPAGE_BPS ?? String(DEFAULT_SLIPPAGE_BPS), 10);

  console.log("Request Jupiter quote...");
  const quote = await getQuote(inputMint, outputMint, amount, slippageBps);

  console.log("Request Jupiter swap...");
  const swapResponse = await getSwap(quote, keypair.publicKey.toBase58());

  console.log("Build Sender-ready transaction...");
  const transaction = await createSenderTransactionFromSwapResponse(
    swapResponse as { swapTransaction: string; [key: string]: unknown },
    keypair,
    { route: "jupiter-swap" }
  );

  console.log("Broadcast via Sender...");
  const signature = await broadcastTransactionWithSender(transaction, {
    route: "jupiter-swap",
    side: "swap",
    mint: outputMint,
  });
  console.log("Signature:", signature);

  console.log("Confirming (polling sendConnection)...");
  const status = await confirmSignature(signature);
  console.log("Status:", status);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
