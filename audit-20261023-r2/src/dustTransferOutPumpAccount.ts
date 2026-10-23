import "dotenv/config";
import {
  Keypair,
  PublicKey,
  sendAndConfirmTransaction,
  SystemProgram,
  SYSVAR_RENT_PUBKEY,
  Transaction,
  TransactionInstruction,
} from "@solana/web3.js";
import bs58 from "bs58";
import { getPrivateKeyBase58, readConnection } from "./config.js";

const ASSOCIATED_TOKEN_PROGRAM_ID = new PublicKey("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL");
const TOKEN_2022_PROGRAM_ID = new PublicKey("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb");
const INCINERATOR = new PublicKey("1nc1nerator11111111111111111111111111111111");

function requireEnv(name: string, expected: string) {
  const actual = process.env[name] ?? "";
  if (actual.toLowerCase() !== expected.toLowerCase()) {
    throw new Error(`${name} must be ${expected}; got ${actual || "(unset)"}`);
  }
}

function u64Le(value: bigint): Buffer {
  const out = Buffer.alloc(8);
  out.writeBigUInt64LE(value);
  return out;
}

function deriveAta(owner: PublicKey, mint: PublicKey, tokenProgramId: PublicKey): PublicKey {
  return PublicKey.findProgramAddressSync(
    [owner.toBuffer(), tokenProgramId.toBuffer(), mint.toBuffer()],
    ASSOCIATED_TOKEN_PROGRAM_ID
  )[0];
}

function createAtaIdempotentIx(args: {
  payer: PublicKey;
  owner: PublicKey;
  mint: PublicKey;
  tokenProgramId: PublicKey;
}): TransactionInstruction {
  const ata = deriveAta(args.owner, args.mint, args.tokenProgramId);
  return new TransactionInstruction({
    programId: ASSOCIATED_TOKEN_PROGRAM_ID,
    keys: [
      { pubkey: args.payer, isSigner: true, isWritable: true },
      { pubkey: ata, isSigner: false, isWritable: true },
      { pubkey: args.owner, isSigner: false, isWritable: false },
      { pubkey: args.mint, isSigner: false, isWritable: false },
      { pubkey: SystemProgram.programId, isSigner: false, isWritable: false },
      { pubkey: args.tokenProgramId, isSigner: false, isWritable: false },
      { pubkey: SYSVAR_RENT_PUBKEY, isSigner: false, isWritable: false },
    ],
    data: Buffer.from([1]),
  });
}

function transferCheckedIx(args: {
  source: PublicKey;
  mint: PublicKey;
  destination: PublicKey;
  owner: PublicKey;
  amountRaw: bigint;
  decimals: number;
  tokenProgramId: PublicKey;
}): TransactionInstruction {
  return new TransactionInstruction({
    programId: args.tokenProgramId,
    keys: [
      { pubkey: args.source, isSigner: false, isWritable: true },
      { pubkey: args.mint, isSigner: false, isWritable: false },
      { pubkey: args.destination, isSigner: false, isWritable: true },
      { pubkey: args.owner, isSigner: true, isWritable: false },
    ],
    data: Buffer.concat([Buffer.from([12]), u64Le(args.amountRaw), Buffer.from([args.decimals])]),
  });
}

function closeAccountIx(args: {
  account: PublicKey;
  destination: PublicKey;
  owner: PublicKey;
  tokenProgramId: PublicKey;
}): TransactionInstruction {
  return new TransactionInstruction({
    programId: args.tokenProgramId,
    keys: [
      { pubkey: args.account, isSigner: false, isWritable: true },
      { pubkey: args.destination, isSigner: false, isWritable: true },
      { pubkey: args.owner, isSigner: true, isWritable: false },
    ],
    data: Buffer.from([9]),
  });
}

requireEnv("CONFIRM_DUST_TRANSFER_OUT", "yes");

const user = Keypair.fromSecretKey(bs58.decode(getPrivateKeyBase58()));
const mint = new PublicKey(process.argv[2] ?? "");
const recipient = process.argv[3] ? new PublicKey(process.argv[3]) : INCINERATOR;
const tokenProgramId = TOKEN_2022_PROGRAM_ID;

const accounts = await readConnection.getParsedTokenAccountsByOwner(user.publicKey, { mint });
const row = accounts.value.find((account) => {
  const amount = BigInt(account.account.data.parsed?.info?.tokenAmount?.amount ?? "0");
  return amount > 0n;
});
if (!row) {
  console.log(JSON.stringify({ status: "flat", mint: mint.toBase58(), wallet: user.publicKey.toBase58() }, null, 2));
  process.exit(0);
}

const info = row.account.data.parsed.info;
const amountRaw = BigInt(info.tokenAmount.amount);
const decimals = Number(info.tokenAmount.decimals ?? 0);
const destAta = deriveAta(recipient, mint, tokenProgramId);

const tx = new Transaction();
tx.add(createAtaIdempotentIx({ payer: user.publicKey, owner: recipient, mint, tokenProgramId }));
tx.add(transferCheckedIx({
  source: row.pubkey,
  mint,
  destination: destAta,
  owner: user.publicKey,
  amountRaw,
  decimals,
  tokenProgramId,
}));
tx.add(closeAccountIx({
  account: row.pubkey,
  destination: user.publicKey,
  owner: user.publicKey,
  tokenProgramId,
}));

const signature = await sendAndConfirmTransaction(readConnection, tx, [user], {
  commitment: "confirmed",
  skipPreflight: false,
});
console.log(JSON.stringify({
  status: "transferred_and_closed",
  wallet: user.publicKey.toBase58(),
  mint: mint.toBase58(),
  sourceAccount: row.pubkey.toBase58(),
  recipient: recipient.toBase58(),
  destinationAta: destAta.toBase58(),
  amountRaw: amountRaw.toString(),
  signature,
}, null, 2));
