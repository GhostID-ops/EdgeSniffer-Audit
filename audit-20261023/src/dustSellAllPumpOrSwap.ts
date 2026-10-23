import "dotenv/config";
import { Connection, Keypair, PublicKey } from "@solana/web3.js";
import bs58 from "bs58";
import { getPrivateKeyBase58, readConnection } from "./config.js";
import { startRustPumpFunSidecar } from "./rustSidecar.js";
import { getBondingCurveState, quoteSellOnCurve } from "./pumpfun/curve.js";
import { tryPumpFunDirectSell } from "./pumpfun/route.js";
import { estimatePumpSwapSellQuote, tryPumpSwapDirectSell } from "./pumpswap/route.js";

const TOKEN_PROGRAM_IDS = [
  "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
  "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
];

type BalanceRow = {
  mint: string;
  amountRaw: bigint;
  uiAmount: string;
};

function requireEnv(name: string, expected: string) {
  const actual = process.env[name] ?? "";
  if (actual.toLowerCase() !== expected.toLowerCase()) {
    throw new Error(`${name} must be ${expected}; got ${actual || "(unset)"}`);
  }
}

function bigintJson(_key: string, value: unknown) {
  return typeof value === "bigint" ? value.toString() : value;
}

async function listPumpBalances(connection: Connection, owner: PublicKey): Promise<BalanceRow[]> {
  const rows: BalanceRow[] = [];
  for (const programId of TOKEN_PROGRAM_IDS) {
    const accounts = await connection.getParsedTokenAccountsByOwner(owner, {
      programId: new PublicKey(programId),
    });
    for (const account of accounts.value) {
      const info = account.account.data.parsed?.info;
      const mint = info?.mint;
      if (!mint || !mint.endsWith("pump")) continue;
      const amountRaw = BigInt(info.tokenAmount?.amount ?? "0");
      if (amountRaw <= 0n) continue;
      rows.push({ mint, amountRaw, uiAmount: info.tokenAmount?.uiAmountString ?? "0" });
    }
  }
  return rows.sort((a, b) => (b.amountRaw > a.amountRaw ? 1 : b.amountRaw < a.amountRaw ? -1 : 0));
}

async function getMintBalance(connection: Connection, owner: PublicKey, mint: string): Promise<BalanceRow | undefined> {
  return (await listPumpBalances(connection, owner)).find((row) => row.mint === mint);
}

async function waitForSignature(connection: Connection, signature: string): Promise<boolean> {
  const timeoutMs = Number(process.env.DUST_SELL_ALL_CONFIRM_TIMEOUT_MS ?? "45000");
  const pollMs = Number(process.env.DUST_SELL_ALL_CONFIRM_POLL_MS ?? "700");
  const startedAt = Date.now();
  while (Date.now() - startedAt <= timeoutMs) {
    const status = await connection.getSignatureStatuses([signature], { searchTransactionHistory: true });
    const value = status.value[0];
    if (value?.err) return false;
    if (value?.confirmationStatus === "confirmed" || value?.confirmationStatus === "finalized") return true;
    await new Promise((resolve) => setTimeout(resolve, pollMs));
  }
  return false;
}

process.env.RUST_HOT_SNIPER_ENABLED = "false";
process.env.RUST_HOT_SNIPER_PRIMARY = "false";
process.env.DUST_ENABLE_HOT_SNIPER = "false";

requireEnv("CONFIRM_DUST_SELL_ALL", "yes");
requireEnv("JUPITER_FALLBACK_ENABLED", "false");
requireEnv("RUST_ONLY_SELLS", "true");
requireEnv("RUST_SIDECAR_ENABLED", "true");
requireEnv("RUST_EXECUTOR_ENABLED", "true");

const user = Keypair.fromSecretKey(bs58.decode(getPrivateKeyBase58()));
const requestedMints = new Set(process.argv.slice(2).filter(Boolean));
const slippageBps = Number(process.env.DUST_SELL_ALL_SLIPPAGE_BPS ?? "5000");
const minSolOutLamports = BigInt(process.env.DUST_SELL_ALL_MIN_SOL_OUT_LAMPORTS ?? "1");
const maxPasses = Math.max(1, Math.floor(Number(process.env.DUST_SELL_ALL_MAX_PASSES ?? "2")));
const sellFractionBps = BigInt(Math.max(
  1,
  Math.min(10_000, Math.floor(Number(process.env.DUST_SELL_FRACTION_BPS ?? "10000")))
));

console.log("DUST SELL PUMP OR SWAP START");
console.log(JSON.stringify({
  wallet: user.publicKey.toBase58(),
  requestedMints: [...requestedMints],
  slippageBps,
  minSolOutLamports: minSolOutLamports.toString(),
  maxPasses,
  sellFractionBps: sellFractionBps.toString(),
}, null, 2));

const unsubscribe = await startRustPumpFunSidecar(new Set([user.publicKey.toBase58()]) as any, () => {}, undefined, undefined);

try {
  await new Promise((resolve) => setTimeout(resolve, Number(process.env.DUST_SELL_ALL_SIDECAR_WARMUP_MS ?? "500")));
  const results = [];
  for (const mint of requestedMints) {
    for (let pass = 1; pass <= maxPasses; pass++) {
      const before = await getMintBalance(readConnection, user.publicKey, mint);
      if (!before || before.amountRaw <= 0n) {
        results.push({ mint, pass, status: "flat" });
        break;
      }
      const tokensInRaw = (before.amountRaw * sellFractionBps) / 10_000n;
      if (tokensInRaw <= 0n) {
        results.push({ mint, pass, status: "sell_amount_zero", beforeRaw: before.amountRaw });
        break;
      }

      const startedAt = Date.now();
      const log = (msg: string) => console.log(`[dust:pump-or-swap] ${msg}`);
      let route = "pumpfun";
      let sell;
      const curve = await getBondingCurveState(new PublicKey(mint)).catch(() => null);
      if (curve && !curve.state.complete) {
        const quote = quoteSellOnCurve(curve.state, tokensInRaw);
        if (quote.solOut >= minSolOutLamports) {
          sell = await tryPumpFunDirectSell({
            user,
            mint,
            tokensInRaw,
            slippageBps,
            startedAt,
            sourceSignature: `manual-flatten-pumpfun-${Date.now()}-${mint}`,
            log,
          });
        }
      }

      if (!sell?.ok) {
        route = "pumpswap";
        const quote = await estimatePumpSwapSellQuote(mint, Number(tokensInRaw)).catch(() => null);
        if (quote == null || quote < minSolOutLamports) {
          results.push({
            mint,
            pass,
            status: "pumpswap_quote_unavailable_or_too_small",
            beforeRaw: before.amountRaw,
            tokensInRaw,
            quoteLamports: quote,
            pumpfunReason: sell?.reason,
          });
          break;
        }
        sell = await tryPumpSwapDirectSell({
          user,
          mint,
          tokensInRaw,
          slippageBps,
          startedAt,
          sourceSignature: `manual-flatten-pumpswap-${Date.now()}-${mint}`,
          log,
        });
      }

      console.log("DUST SELL PUMP OR SWAP RESULT");
      console.log(JSON.stringify({ mint, pass, route, sell }, bigintJson, 2));
      if (!sell.ok) {
        results.push({ mint, pass, route, status: "sell_not_ok", reason: sell.reason, beforeRaw: before.amountRaw });
        break;
      }

      const confirmed = await waitForSignature(readConnection, sell.signature);
      await new Promise((resolve) => setTimeout(resolve, Number(process.env.DUST_SELL_ALL_POST_WAIT_MS ?? "1500")));
      const after = await getMintBalance(readConnection, user.publicKey, mint);
      results.push({
        mint,
        pass,
        route,
        status: confirmed ? "confirmed" : "not_confirmed",
        signature: sell.signature,
        beforeRaw: before.amountRaw,
        soldRaw: tokensInRaw,
        afterRaw: after?.amountRaw ?? 0n,
        afterUi: after?.uiAmount ?? "0",
      });
      if (!after || after.amountRaw <= 0n) break;
    }
  }

  const finalBalances = (await listPumpBalances(readConnection, user.publicKey))
    .filter((row) => requestedMints.has(row.mint));
  console.log("DUST SELL PUMP OR SWAP SUMMARY");
  console.log(JSON.stringify({ results, finalBalances }, bigintJson, 2));
} finally {
  await unsubscribe().catch(() => {});
}
