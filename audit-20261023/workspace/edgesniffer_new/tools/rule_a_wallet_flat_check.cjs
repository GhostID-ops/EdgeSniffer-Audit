#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const { Connection, PublicKey } = require("@solana/web3.js");

function loadEnv(path) {
  const out = {};
  for (const line of fs.readFileSync(path, "utf8").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
    const idx = trimmed.indexOf("=");
    out[trimmed.slice(0, idx)] = trimmed.slice(idx + 1).replace(/^['"]|['"]$/g, "");
  }
  return out;
}

async function main() {
  const envPath = process.argv[2] || ".env";
  const wallet = process.argv[3] || "2qfCBm5bNcQhDuikruVk91QQm6yVH9ZjjTaPfCRUGFyd";
  const env = loadEnv(envPath);
  const rpc = env.SECURE_RPC_URL || env.RPC_URL || env.SOLANA_RPC_URL;
  if (!rpc) throw new Error("missing RPC URL in env");

  const owner = new PublicKey(wallet);
  const conn = new Connection(rpc, { commitment: "confirmed" });
  const programs = [
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
  ];

  const solLamports = await conn.getBalance(owner, "confirmed");
  const nonzero = [];
  for (const program of programs) {
    const rows = await conn.getParsedTokenAccountsByOwner(
      owner,
      { programId: new PublicKey(program) },
      "confirmed",
    );
    for (const { pubkey, account } of rows.value) {
      const info = account.data.parsed.info;
      const amountRaw = BigInt(info.tokenAmount.amount || "0");
      if (amountRaw > 0n) {
        nonzero.push({
          ata: pubkey.toBase58(),
          mint: info.mint,
          program,
          amountRaw: amountRaw.toString(),
          uiAmountString: info.tokenAmount.uiAmountString,
        });
      }
    }
  }

  console.log(JSON.stringify({
    wallet: owner.toBase58(),
    solLamports,
    sol: solLamports / 1e9,
    nonzeroTokenAccounts: nonzero.length,
    nonzero,
  }, null, 2));
}

main().catch((err) => {
  console.error(err.stack || String(err));
  process.exit(1);
});
