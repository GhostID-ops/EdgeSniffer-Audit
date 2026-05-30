import "dotenv/config";
import { Connection } from "@solana/web3.js";

const signature = process.argv[2];
const wallet = process.argv[3];

if (!signature || !wallet) {
  throw new Error("Usage: npx tsx src/inspectTxRpc.ts <signature> <wallet>");
}

const rpcUrl = process.env.SECURE_RPC_URL || process.env.RPC_URL;
if (!rpcUrl) throw new Error("SECURE_RPC_URL is not set.");

const connection = new Connection(rpcUrl, "confirmed");

const tx = await connection.getParsedTransaction(signature, {
  commitment: "confirmed",
  maxSupportedTransactionVersion: 0,
});

if (!tx) throw new Error("Transaction not found.");

console.log(`signature=${signature}`);
console.log(`slot=${tx.slot} blockTime=${tx.blockTime} err=${JSON.stringify(tx.meta?.err ?? null)}`);

const keys = tx.transaction.message.accountKeys.map((k) => ({
  pubkey: k.pubkey.toBase58(),
  signer: k.signer,
  writable: k.writable,
}));
console.log(`feePayer=${keys[0]?.pubkey ?? "-"}`);
console.log("wallet account keys:");
for (const [idx, key] of keys.entries()) {
  if (key.pubkey === wallet) {
    console.log(`  idx=${idx} signer=${key.signer} writable=${key.writable}`);
  }
}

const walletIdx = keys.findIndex((k) => k.pubkey === wallet);
if (walletIdx >= 0) {
  const pre = tx.meta?.preBalances?.[walletIdx] ?? 0;
  const post = tx.meta?.postBalances?.[walletIdx] ?? 0;
  console.log(`nativeDeltaLamports=${post - pre}`);
}

const pre = new Map<string, { mint: string; amount: number; decimals: number }>();
for (const bal of tx.meta?.preTokenBalances ?? []) {
  if (bal.owner !== wallet) continue;
  pre.set(`${bal.accountIndex}:${bal.mint}`, {
    mint: bal.mint,
    amount: Number(bal.uiTokenAmount.amount ?? "0"),
    decimals: bal.uiTokenAmount.decimals,
  });
}

console.log("wallet token deltas:");
for (const bal of tx.meta?.postTokenBalances ?? []) {
  if (bal.owner !== wallet) continue;
  const key = `${bal.accountIndex}:${bal.mint}`;
  const before = pre.get(key) ?? { mint: bal.mint, amount: 0, decimals: bal.uiTokenAmount.decimals };
  const after = Number(bal.uiTokenAmount.amount ?? "0");
  const delta = after - before.amount;
  console.log(
    `  mint=${bal.mint} decimals=${bal.uiTokenAmount.decimals} pre=${before.amount} post=${after} delta=${delta}`
  );
}
