import "dotenv/config";
import {
  Connection,
  Keypair,
  PublicKey,
  sendAndConfirmTransaction,
  Transaction,
  TransactionInstruction,
} from "@solana/web3.js";
import bs58 from "bs58";
import { getPrivateKeyBase58, readConnection } from "./config.js";

const TOKEN_PROGRAM_IDS = [
  "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
  "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
];

function requireEnv(name: string, expected: string) {
  const actual = process.env[name] ?? "";
  if (actual.toLowerCase() !== expected.toLowerCase()) {
    throw new Error(`${name} must be ${expected}; got ${actual || "(unset)"}`);
  }
}

requireEnv("CONFIRM_DUST_CLOSE_TINY", "yes");

const user = Keypair.fromSecretKey(bs58.decode(getPrivateKeyBase58()));
const requestedMints = new Set(process.argv.slice(2).filter(Boolean));
const maxRaw = BigInt(process.env.DUST_CLOSE_TINY_MAX_RAW ?? "10000000");
const connection: Connection = readConnection;

function u64Le(value: bigint): Buffer {
  const out = Buffer.alloc(8);
  out.writeBigUInt64LE(value);
  return out;
}

function burnInstruction(args: {
  account: PublicKey;
  mint: PublicKey;
  owner: PublicKey;
  amountRaw: bigint;
  programId: PublicKey;
}): TransactionInstruction {
  return new TransactionInstruction({
    programId: args.programId,
    keys: [
      { pubkey: args.account, isSigner: false, isWritable: true },
      { pubkey: args.mint, isSigner: false, isWritable: true },
      { pubkey: args.owner, isSigner: true, isWritable: false },
    ],
    data: Buffer.concat([Buffer.from([8]), u64Le(args.amountRaw)]),
  });
}

function burnCheckedInstruction(args: {
  account: PublicKey;
  mint: PublicKey;
  owner: PublicKey;
  amountRaw: bigint;
  decimals: number;
  programId: PublicKey;
}): TransactionInstruction {
  return new TransactionInstruction({
    programId: args.programId,
    keys: [
      { pubkey: args.account, isSigner: false, isWritable: true },
      { pubkey: args.mint, isSigner: false, isWritable: false },
      { pubkey: args.owner, isSigner: true, isWritable: false },
    ],
    data: Buffer.concat([Buffer.from([15]), u64Le(args.amountRaw), Buffer.from([args.decimals])]),
  });
}

function closeAccountInstruction(args: {
  account: PublicKey;
  destination: PublicKey;
  owner: PublicKey;
  programId: PublicKey;
}): TransactionInstruction {
  return new TransactionInstruction({
    programId: args.programId,
    keys: [
      { pubkey: args.account, isSigner: false, isWritable: true },
      { pubkey: args.destination, isSigner: false, isWritable: true },
      { pubkey: args.owner, isSigner: true, isWritable: false },
    ],
    data: Buffer.from([9]),
  });
}

console.log("DUST CLOSE TINY START");
console.log(JSON.stringify({
  wallet: user.publicKey.toBase58(),
  requestedMints: [...requestedMints],
  maxRaw: maxRaw.toString(),
}, null, 2));

const candidates = [];
for (const programIdText of TOKEN_PROGRAM_IDS) {
  const programId = new PublicKey(programIdText);
  const accounts = await connection.getParsedTokenAccountsByOwner(user.publicKey, { programId });
  for (const account of accounts.value) {
    const info = account.account.data.parsed?.info;
    const mint = info?.mint;
    if (!mint || !mint.endsWith("pump")) continue;
    if (requestedMints.size > 0 && !requestedMints.has(mint)) continue;
    const amountRaw = BigInt(info.tokenAmount?.amount ?? "0");
    if (amountRaw <= 0n || amountRaw > maxRaw) continue;
    candidates.push({
      account: account.pubkey,
      mint: new PublicKey(mint),
      mintText: mint,
    amountRaw,
    programId,
    uiAmount: info.tokenAmount?.uiAmountString ?? "0",
    decimals: Number(info.tokenAmount?.decimals ?? 0),
  });
  }
}

console.log("DUST CLOSE TINY CANDIDATES");
console.log(JSON.stringify(candidates.map((row) => ({
  account: row.account.toBase58(),
  mint: row.mintText,
  amountRaw: row.amountRaw.toString(),
  uiAmount: row.uiAmount,
  programId: row.programId.toBase58(),
})), null, 2));

const results = [];
for (const row of candidates) {
  const tx = new Transaction();
  const useBurnChecked = /^(true|1|yes)$/i.test(process.env.DUST_CLOSE_TINY_BURN_CHECKED ?? "");
  tx.add((useBurnChecked ? burnCheckedInstruction : burnInstruction)({
    account: row.account,
    mint: row.mint,
    owner: user.publicKey,
    amountRaw: row.amountRaw,
    decimals: row.decimals,
    programId: row.programId,
  }));
  tx.add(closeAccountInstruction({
    account: row.account,
    destination: user.publicKey,
    owner: user.publicKey,
    programId: row.programId,
  }));

  try {
    const signature = await sendAndConfirmTransaction(connection, tx, [user], {
      commitment: "confirmed",
      skipPreflight: false,
    });
    results.push({
      mint: row.mintText,
      account: row.account.toBase58(),
      amountRaw: row.amountRaw.toString(),
      status: "closed",
      signature,
    });
  } catch (err) {
    results.push({
      mint: row.mintText,
      account: row.account.toBase58(),
      amountRaw: row.amountRaw.toString(),
      status: "error",
      error: err instanceof Error ? err.message : String(err),
    });
  }
}

console.log("DUST CLOSE TINY SUMMARY");
console.log(JSON.stringify({ results }, null, 2));
