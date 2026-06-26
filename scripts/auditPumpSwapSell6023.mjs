// One-off audit: decode failed bot PumpSwap sells vs successful manual sells.
// Reads RPC from .env (SECURE_RPC_URL or HELIUS_API_KEY). No writes, read-only.
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

const PUMPSWAP = new PublicKey("pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA");
const SELL_DISC = Buffer.from([51, 230, 133, 164, 1, 127, 131, 173]);
const BUY_DISC = Buffer.from([102, 6, 61, 18, 1, 218, 235, 234]);
const BUY_EXACT_DISC = Buffer.from([198, 46, 21, 82, 180, 217, 232, 112]);

const SELL_IDL_NAMES = [
  "pool", "user", "global_config", "base_mint", "quote_mint",
  "user_base_token_account", "user_quote_token_account",
  "pool_base_token_account", "pool_quote_token_account",
  "protocol_fee_recipient", "protocol_fee_recipient_token_account",
  "base_token_program", "quote_token_program", "system_program",
  "associated_token_program", "event_authority", "program",
  "coin_creator_vault_ata", "coin_creator_vault_authority",
  "fee_config", "fee_program",
];

function dataToBuf(data) {
  if (data == null) return Buffer.alloc(0);
  if (Buffer.isBuffer(data)) return data;
  if (data instanceof Uint8Array) return Buffer.from(data);
  if (Array.isArray(data)) return Buffer.from(data);
  if (typeof data === "string") {
    // base58
    try { return Buffer.from(PublicKey.prototype ? bs58dec(data) : data); } catch { return Buffer.from(data, "base64"); }
  }
  return Buffer.alloc(0);
}
// minimal base58 decode
const ALPH = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
function bs58dec(s) {
  let bytes = [0];
  for (const c of s) {
    let carry = ALPH.indexOf(c);
    if (carry < 0) throw new Error("bad b58");
    for (let j = 0; j < bytes.length; j++) { carry += bytes[j] * 58; bytes[j] = carry & 0xff; carry >>= 8; }
    while (carry) { bytes.push(carry & 0xff); carry >>= 8; }
  }
  for (let k = 0; k < s.length && s[k] === "1"; k++) bytes.push(0);
  return Buffer.from(bytes.reverse());
}

function classifyDisc(buf) {
  const d = buf.subarray(0, 8);
  if (d.equals(SELL_DISC)) return "sell";
  if (d.equals(BUY_DISC)) return "buy";
  if (d.equals(BUY_EXACT_DISC)) return "buy_exact_quote_in";
  return "other";
}

async function decode(sig) {
  const tx = await conn.getTransaction(sig, { commitment: "confirmed", maxSupportedTransactionVersion: 0 });
  if (!tx) return { sig, error: "tx not found" };
  const msg = tx.transaction.message;
  const staticKeys = msg.staticAccountKeys;
  const loadedW = tx.meta?.loadedAddresses?.writable ?? [];
  const loadedR = tx.meta?.loadedAddresses?.readonly ?? [];
  const allKeys = [...staticKeys, ...loadedW, ...loadedR];

  const out = { sig, err: tx.meta?.err ?? null, fee: tx.meta?.fee, ixs: [] };

  const collect = (programIdIndex, accountIndexes, data, cpi) => {
    if (!allKeys[programIdIndex]?.equals(PUMPSWAP)) return;
    const buf = dataToBuf(data);
    const kind = classifyDisc(buf);
    if (kind === "other") return;
    const rec = {
      kind, cpi: !!cpi, accountCount: accountIndexes.length,
      args: {},
      accounts: accountIndexes.map((idx, pos) => ({
        pos,
        name: kind === "sell" ? (SELL_IDL_NAMES[pos] ?? `remaining[${pos - SELL_IDL_NAMES.length}]`) : `#${pos}`,
        pubkey: allKeys[idx]?.toBase58() ?? `??idx${idx}`,
        writable: msg.isAccountWritable(idx),
        signer: msg.isAccountSigner(idx),
      })),
    };
    if (buf.length >= 24) {
      rec.args.amount_in = buf.readBigUInt64LE(8).toString();
      rec.args.min_out = buf.readBigUInt64LE(16).toString();
    }
    rec.dataHex = buf.subarray(0, Math.min(buf.length, 24)).toString("hex");
    out.ixs.push(rec);
  };

  for (const ix of msg.compiledInstructions) {
    collect(ix.programIdIndex, ix.accountKeyIndexes ?? Array.from(ix.accounts ?? []), ix.data, false);
  }
  for (const g of tx.meta?.innerInstructions ?? []) {
    for (const ix of g.instructions) {
      collect(ix.programIdIndex, ix.accounts ?? ix.accountKeyIndexes ?? [], ix.data, true);
    }
  }
  out.logs = (tx.meta?.logMessages ?? []).filter((l) =>
    /pamm|pump|error|invoke|fee|overflow|6023|consumed/i.test(l)
  );
  return out;
}

function printDecoded(label, d) {
  console.log(`\n========== ${label} ==========`);
  console.log(`sig=${d.sig}`);
  console.log(`err=${JSON.stringify(d.err)}`);
  for (const ix of d.ixs) {
    console.log(`\n  [${ix.cpi ? "CPI" : "TOP"}] ${ix.kind} accounts=${ix.accountCount} data24=${ix.dataHex}`);
    console.log(`      amount_in=${ix.args.amount_in} min_out=${ix.args.min_out}`);
    for (const a of ix.accounts) {
      console.log(
        `      ${String(a.pos).padStart(2)} ${a.writable ? "W" : "-"}${a.signer ? "S" : "-"}  ${a.pubkey}  ${a.name}`
      );
    }
  }
  if (d.logs?.length) {
    console.log("  logs:");
    for (const l of d.logs) console.log("    " + l);
  }
}

function diff(failed, ok) {
  const fSell = failed.ixs.find((i) => i.kind === "sell");
  const oSell = ok.ixs.find((i) => i.kind === "sell");
  if (!fSell || !oSell) {
    console.log("\n### DIFF: missing a sell ix", { failedHas: !!fSell, okHas: !!oSell });
    return;
  }
  console.log("\n========== POSITIONAL DIFF (failed vs ok) ==========");
  console.log(`accountCount: failed=${fSell.accountCount} ok=${oSell.accountCount}`);
  console.log(`amount_in:    failed=${fSell.args.amount_in} ok=${oSell.args.amount_in}`);
  console.log(`min_out:      failed=${fSell.args.min_out} ok=${oSell.args.min_out}`);
  const max = Math.max(fSell.accounts.length, oSell.accounts.length);
  for (let i = 0; i < max; i++) {
    const a = fSell.accounts[i];
    const b = oSell.accounts[i];
    const same = a && b && a.pubkey === b.pubkey && a.writable === b.writable && a.signer === b.signer;
    if (same) continue;
    console.log(`  pos ${String(i).padStart(2)} ${b?.name ?? a?.name ?? ""}`);
    console.log(`     failed: ${a ? `${a.writable ? "W" : "-"}${a.signer ? "S" : "-"} ${a.pubkey}` : "(missing)"}`);
    console.log(`     ok:     ${b ? `${b.writable ? "W" : "-"}${b.signer ? "S" : "-"} ${b.pubkey}` : "(missing)"}`);
  }
}

const cases = {
  FIERROLAIS: {
    failed: "2JMfWbLEBxyVnjHLZZRq6jMWFKSZBr4PGdNoibpEoUuRfGunZL5myz9gPa2hKRxZSvxT9SzWhbWH6MTxaZZ4k51Q",
    ok: "4Z9GL2GGkKfVWUMR4wR9uVF8NaYcdcUWRgT7UP4wGNHcv539MVSEuXrm3PQjsqzajRQd8KoBoyMarQ3EUBmXTgYW",
  },
  RICHARD: {
    failed: "5xptbQ1bQAXFu1WJq48myFNyoySCPWfqrKviYhoseK3sfd5QU65L9j1yD36PPt2esHBwZVtUy8yBbGJe4cau2YiU",
    ok: "5UBjrYL6pk1Y3v6jgAAqtyEMDgnmsEP5qmNCPuUKqz7XjiW4cwJykonhoxuTWGH1n6mNBoostwQkHRURiV7qdbiU",
  },
};

for (const [name, c] of Object.entries(cases)) {
  const failed = await decode(c.failed);
  const ok = await decode(c.ok);
  printDecoded(`${name} FAILED BOT`, failed);
  printDecoded(`${name} OK MANUAL`, ok);
  diff(failed, ok);
}
