import "dotenv/config";
import { Connection, PublicKey } from "@solana/web3.js";
import bs58 from "bs58";

const signature = process.argv[2];
const wallet = process.argv[3];

if (!signature || !wallet) {
  throw new Error("Usage: npx tsx src/inspectTxRpc.ts <signature> <wallet>");
}

const rpcUrl = process.env.SECURE_RPC_URL || process.env.RPC_URL;
if (!rpcUrl) throw new Error("SECURE_RPC_URL is not set.");

const connection = new Connection(rpcUrl, "confirmed");
const PUMPSWAP_PROGRAM_ID = new PublicKey("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA");

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

console.log("logs:");
for (const line of tx.meta?.logMessages ?? []) {
  console.log(`  ${line}`);
}

const raw = await connection.getTransaction(signature, {
  commitment: "confirmed",
  maxSupportedTransactionVersion: 0,
});
if (raw) {
  const staticKeys = raw.transaction.message.staticAccountKeys;
  const loaded = raw.meta?.loadedAddresses
    ? [...raw.meta.loadedAddresses.writable, ...raw.meta.loadedAddresses.readonly]
    : [];
  const allKeys = [...staticKeys, ...loaded];
  console.log("pumpswap instructions:");
  const printIx = (label: string, ix: { programIdIndex: number; accounts?: number[] | Uint8Array; accountKeyIndexes?: number[]; data?: unknown }) => {
    if (!allKeys[ix.programIdIndex]?.equals(PUMPSWAP_PROGRAM_ID)) return;
    const accounts = ix.accountKeyIndexes ?? Array.from(ix.accounts ?? []);
    let data = "";
    if (typeof ix.data === "string") {
      try {
        data = Buffer.from(bs58.decode(ix.data)).toString("hex");
      } catch {
        data = ix.data;
      }
    } else if (ix.data instanceof Uint8Array || Array.isArray(ix.data)) {
      data = Buffer.from(ix.data).toString("hex");
    }
    console.log(`  ${label}: accountCount=${accounts.length} data=${data}`);
    accounts.forEach((idx, accountIdx) => {
      const key = allKeys[idx];
      console.log(`    [${accountIdx}] txIdx=${idx} ${key?.toBase58() ?? "-"}`);
    });
  };
  raw.transaction.message.compiledInstructions.forEach((ix, idx) => printIx(`top[${idx}]`, ix));
  for (const group of raw.meta?.innerInstructions ?? []) {
    group.instructions.forEach((ix, idx) =>
      printIx(`inner[parent=${group.index} ix=${idx}]`, ix as unknown as {
        programIdIndex: number;
        accounts?: number[] | Uint8Array;
        accountKeyIndexes?: number[];
        data?: unknown;
      })
    );
  }
}
