// One-off: decode the source's successful PumpSwap buy vs the bot's failed 6023 buys.
import fs from "node:fs";
import { Connection, PublicKey } from "@solana/web3.js";

function loadEnv() {
  const txt = fs.readFileSync(new URL("../.env", import.meta.url), "utf8");
  const env = {};
  for (const line of txt.split(/\r?\n/)) {
    const m = line.match(/^([A-Z0-9_]+)=(.*)$/);
    if (m) env[m[1]] = m[2];
  }
  return env;
}
const env = loadEnv();
const rpc =
  (env.SECURE_RPC_URL && env.SECURE_RPC_URL.length > 0 && env.SECURE_RPC_URL) ||
  (env.HELIUS_API_KEY ? `https://mainnet.helius-rpc.com/?api-key=${env.HELIUS_API_KEY}` : null);
if (!rpc) throw new Error("no rpc");
const conn = new Connection(rpc, "confirmed");

const PUMP_AMM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA";

const SIGS = {
  "SOURCE success (61qJiJfS)": "61qJiJfSpiYhA5AyueP2uS7bkC9Z3Hd26ybs9zkZDr9Zu2sqZehKSdXpustMcZxtkbU5q1yP8i7KYsymxwHP5tkZ",
  "BOT fail primary (5cXwJwKc)": "5cXwJwKcyeV7YDMTaF1GbqgvJMng3hrz6uWFhJZCxT56raxNYwY9XYDdePSAUQKNVXS4X9fH1gVdKkRw2nhV6xmC",
  "BOT fail retry (gvSL5VWE)": "gvSL5VWEVN1nLrAyQ9G2qZs9ZHVuAfEuH1vDpwfpws2b2KpY2NVhsb975GLMw3SWB1oLgJmW6xP6wdj57xHhAjS",
};

function allKeys(tx) {
  const msg = tx.transaction.message;
  const stat = msg.staticAccountKeys ?? [];
  const loaded = tx.meta?.loadedAddresses ?? { writable: [], readonly: [] };
  return [...stat, ...(loaded.writable ?? []), ...(loaded.readonly ?? [])].map((k) => k.toBase58());
}

function decodeAmounts(dataB64) {
  const buf = Buffer.from(dataB64, "base64");
  // pump_amm buy/buy_exact_quote_in: 8-byte discriminator then two u64 args
  if (buf.length < 24) return { hex: buf.toString("hex") };
  return {
    discriminator: buf.subarray(0, 8).toString("hex"),
    arg0_u64: buf.readBigUInt64LE(8).toString(),
    arg1_u64: buf.readBigUInt64LE(16).toString(),
    len: buf.length,
  };
}

for (const [label, sig] of Object.entries(SIGS)) {
  console.log("\n================ " + label + " ================");
  const tx = await conn.getTransaction(sig, { maxSupportedTransactionVersion: 0 });
  if (!tx) {
    console.log("  NOT FOUND on RPC");
    continue;
  }
  console.log("  slot:", tx.slot, "| err:", JSON.stringify(tx.meta?.err));
  const keys = allKeys(tx);
  const msg = tx.transaction.message;
  const cixs = msg.compiledInstructions ?? msg.instructions ?? [];
  cixs.forEach((ix, i) => {
    const progIdx = ix.programIdIndex;
    const prog = keys[progIdx];
    if (prog !== PUMP_AMM) return;
    const accIdx = ix.accountKeyIndexes ?? ix.accounts ?? [];
    const dataB64 = ix.data
      ? (typeof ix.data === "string" ? Buffer.from(ix.data, "base64").toString("base64") : Buffer.from(ix.data).toString("base64"))
      : "";
    console.log(`  --- pump_amm instruction at index ${i} (${accIdx.length} accounts) ---`);
    console.log("  data:", JSON.stringify(decodeAmounts(dataB64)));
    accIdx.forEach((ai, n) => {
      console.log(`    [${String(n).padStart(2)}] ${keys[ai]}`);
    });
  });
  // log relevant errors
  const logs = (tx.meta?.logMessages ?? []).filter((l) => /6023|overflow|Error|failed|insufficient/i.test(l));
  if (logs.length) {
    console.log("  relevant logs:");
    logs.forEach((l) => console.log("    " + l));
  }
}
