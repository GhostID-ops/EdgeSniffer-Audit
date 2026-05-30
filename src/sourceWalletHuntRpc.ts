import "dotenv/config";
import {
  Connection,
  PublicKey,
  type ParsedTransactionWithMeta,
} from "@solana/web3.js";

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const USDC_MINT = "EPjFWdd5AufqSSqeM2N1xzybapC8G4wEGGkZwyTDt1v";

type Swap = {
  signature: string;
  timestamp: number;
  wallet: string;
  side: "buy" | "sell";
  mint: string;
  quoteMint: string;
  quoteRaw: number;
  tokenRaw: number;
};

type Hit = {
  mint: string;
  target: Swap;
  candidate: Swap;
  leadSeconds: number;
};

function short(s: string | null | undefined): string {
  if (!s) return "-";
  return `${s.slice(0, 4)}...${s.slice(-4)}`;
}

function fmtAge(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  return `${(seconds / 60).toFixed(1)}m`;
}

function fmtQuote(raw: number, mint: string): string {
  if (mint === WRAPPED_SOL_MINT) return `${(raw / 1e9).toFixed(4)} SOL`;
  if (mint === USDC_MINT) return `${(raw / 1e6).toFixed(2)} USDC`;
  return `${raw} raw`;
}

function accountKeys(tx: ParsedTransactionWithMeta): string[] {
  return tx.transaction.message.accountKeys.map((key) => key.pubkey.toBase58());
}

function nativeDelta(tx: ParsedTransactionWithMeta, wallet: string): number {
  const idx = accountKeys(tx).indexOf(wallet);
  if (idx < 0) return 0;
  const pre = tx.meta?.preBalances?.[idx] ?? 0;
  const post = tx.meta?.postBalances?.[idx] ?? 0;
  return post - pre;
}

function tokenDeltas(tx: ParsedTransactionWithMeta, wallet: string): Map<string, number> {
  const out = new Map<string, number>();
  const pre = tx.meta?.preTokenBalances ?? [];
  const post = tx.meta?.postTokenBalances ?? [];
  const byKey = new Map<string, { mint: string; pre: number; post: number }>();

  for (const bal of pre) {
    if (bal.owner !== wallet) continue;
    const key = `${bal.accountIndex}:${bal.mint}`;
    byKey.set(key, {
      mint: bal.mint,
      pre: Number(bal.uiTokenAmount.amount ?? "0"),
      post: 0,
    });
  }
  for (const bal of post) {
    if (bal.owner !== wallet) continue;
    const key = `${bal.accountIndex}:${bal.mint}`;
    const row = byKey.get(key) ?? { mint: bal.mint, pre: 0, post: 0 };
    row.post = Number(bal.uiTokenAmount.amount ?? "0");
    byKey.set(key, row);
  }

  for (const row of byKey.values()) {
    const delta = row.post - row.pre;
    if (delta !== 0) out.set(row.mint, (out.get(row.mint) ?? 0) + delta);
  }
  return out;
}

function extractSwap(tx: ParsedTransactionWithMeta | null, wallet: string): Swap | null {
  if (!tx || tx.meta?.err || !tx.blockTime) return null;
  const signature = tx.transaction.signatures[0] ?? "";
  const solNet = nativeDelta(tx, wallet);
  const deltas = tokenDeltas(tx, wallet);
  let bestMint: string | null = null;
  let bestDelta = 0;
  for (const [mint, delta] of deltas) {
    if (mint === WRAPPED_SOL_MINT || mint === USDC_MINT) continue;
    if (Math.abs(delta) > Math.abs(bestDelta)) {
      bestMint = mint;
      bestDelta = delta;
    }
  }
  if (!bestMint || bestDelta === 0) return null;

  const wsolDelta = deltas.get(WRAPPED_SOL_MINT) ?? 0;
  const usdcDelta = deltas.get(USDC_MINT) ?? 0;
  if (bestDelta > 0 && (wsolDelta < 0 || usdcDelta < 0 || solNet < 0)) {
    const quoteMint = usdcDelta < 0 ? USDC_MINT : WRAPPED_SOL_MINT;
    const quoteRaw =
      usdcDelta < 0
        ? Math.abs(usdcDelta)
        : wsolDelta < 0
        ? Math.abs(wsolDelta)
        : Math.abs(solNet);
    return {
      signature,
      timestamp: tx.blockTime,
      wallet,
      side: "buy",
      mint: bestMint,
      quoteMint,
      quoteRaw,
      tokenRaw: bestDelta,
    };
  }
  if (bestDelta < 0 && (wsolDelta > 0 || usdcDelta > 0 || solNet > 0)) {
    const quoteMint = usdcDelta > 0 ? USDC_MINT : WRAPPED_SOL_MINT;
    const quoteRaw =
      usdcDelta > 0
        ? Math.abs(usdcDelta)
        : wsolDelta > 0
        ? Math.abs(wsolDelta)
        : Math.abs(solNet);
    return {
      signature,
      timestamp: tx.blockTime,
      wallet,
      side: "sell",
      mint: bestMint,
      quoteMint,
      quoteRaw,
      tokenRaw: Math.abs(bestDelta),
    };
  }
  return null;
}

async function getParsedBatch(
  connection: Connection,
  signatures: string[]
): Promise<Array<ParsedTransactionWithMeta | null>> {
  const out: Array<ParsedTransactionWithMeta | null> = [];
  for (let i = 0; i < signatures.length; i += 10) {
    const batch = signatures.slice(i, i + 10);
    const txs = await connection.getParsedTransactions(batch, {
      commitment: "confirmed",
      maxSupportedTransactionVersion: 0,
    });
    out.push(...txs);
  }
  return out;
}

async function main(): Promise<void> {
  const rpcUrl = process.env.SECURE_RPC_URL || process.env.RPC_URL;
  if (!rpcUrl) throw new Error("SECURE_RPC_URL is not set.");
  const targetWallet = process.argv[2] ?? "B27vwq89vX3oDa8fFNGiqQWB2wRiSmsmD8Hw2JuRefC8";
  const walletTxLimit = Number(process.argv[3] ?? "250");
  const mintLimit = Number(process.argv[4] ?? "30");
  const maxLeadSeconds = Number(process.argv[5] ?? "240");
  const mintSigLimit = Number(process.argv[6] ?? "250");
  const summaryOnly = process.argv.includes("--summary-only");
  const walletOnly = process.argv.includes("--wallet-only");

  const connection = new Connection(rpcUrl, "confirmed");
  console.log(
    `RPC source-wallet hunt target=${targetWallet} walletTxLimit=${walletTxLimit} mintLimit=${mintLimit} lead=${maxLeadSeconds}s`
  );

  const sigInfos = await connection.getSignaturesForAddress(new PublicKey(targetWallet), {
    limit: walletTxLimit,
  });
  const targetTxs = await getParsedBatch(connection, sigInfos.map((s) => s.signature));
  const buys = targetTxs
    .map((tx) => extractSwap(tx, targetWallet))
    .filter((s): s is Swap => s != null && s.side === "buy")
    .sort((a, b) => b.timestamp - a.timestamp);

  const uniqueBuys: Swap[] = [];
  const seenMints = new Set<string>();
  for (const buy of buys) {
    if (seenMints.has(buy.mint)) continue;
    seenMints.add(buy.mint);
    uniqueBuys.push(buy);
    if (uniqueBuys.length >= mintLimit) break;
  }

  if (walletOnly) {
    const quoteSol = uniqueBuys
      .map((b) => (b.quoteMint === WRAPPED_SOL_MINT ? b.quoteRaw / 1e9 : b.quoteRaw / 1e6 / 150))
      .sort((a, b) => a - b);
    const avg = quoteSol.reduce((a, b) => a + b, 0) / Math.max(1, quoteSol.length);
    console.log("");
    console.log(`Target buys found: ${buys.length}; unique sampled: ${uniqueBuys.length}`);
    console.log(
      `Target buy size: avg=${avg.toFixed(5)} SOL median=${quoteSol[Math.floor(quoteSol.length / 2)]?.toFixed(5) ?? "-"} SOL min=${quoteSol[0]?.toFixed(5) ?? "-"} SOL max=${quoteSol[quoteSol.length - 1]?.toFixed(5) ?? "-"} SOL`
    );
    console.log("Recent unique buys:");
    for (const buy of uniqueBuys.slice(0, 20)) {
      console.log(
        `  ${short(buy.mint)} ${fmtQuote(buy.quoteRaw, buy.quoteMint)} tx=${short(buy.signature)}`
      );
    }
    return;
  }

  const hits: Hit[] = [];
  for (const [idx, buy] of uniqueBuys.entries()) {
    console.log(`Profiling ${idx + 1}/${uniqueBuys.length} ${short(buy.mint)} target=${fmtQuote(buy.quoteRaw, buy.quoteMint)}`);
    const mintSigs = await connection.getSignaturesForAddress(new PublicKey(buy.mint), {
      before: buy.signature,
      limit: mintSigLimit,
    });
    const leadSigs = mintSigs
      .filter((s) => buy.timestamp - (s.blockTime ?? 0) >= 0)
      .filter((s) => buy.timestamp - (s.blockTime ?? 0) <= maxLeadSeconds)
      .map((s) => s.signature);
    const txs = await getParsedBatch(connection, leadSigs);
    for (const tx of txs) {
      if (!tx || !tx.blockTime) continue;
      const keys = accountKeys(tx);
      for (const wallet of keys.slice(0, 20)) {
        if (wallet === targetWallet) continue;
        const swap = extractSwap(tx, wallet);
        if (!swap || swap.side !== "buy" || swap.mint !== buy.mint) continue;
        hits.push({
          mint: buy.mint,
          target: buy,
          candidate: swap,
          leadSeconds: buy.timestamp - swap.timestamp,
        });
      }
    }
  }

  const byWallet = new Map<string, Hit[]>();
  for (const hit of hits) {
    const arr = byWallet.get(hit.candidate.wallet) ?? [];
    arr.push(hit);
    byWallet.set(hit.candidate.wallet, arr);
  }
  const scores = [...byWallet.entries()]
    .map(([wallet, walletHits]) => {
      const mints = new Set(walletHits.map((h) => h.mint));
      const leads = walletHits.map((h) => h.leadSeconds).sort((a, b) => a - b);
      const quoteSol = walletHits.reduce((sum, h) => {
        if (h.candidate.quoteMint === WRAPPED_SOL_MINT) return sum + h.candidate.quoteRaw / 1e9;
        if (h.candidate.quoteMint === USDC_MINT) return sum + h.candidate.quoteRaw / 1e6 / 150;
        return sum;
      }, 0);
      return {
        wallet,
        hits: walletHits.length,
        tokens: mints.size,
        medianLead: leads[Math.floor(leads.length / 2)] ?? 0,
        avgLead: leads.reduce((a, b) => a + b, 0) / Math.max(1, leads.length),
        quoteSol,
        score: mints.size * 10 + walletHits.length + quoteSol,
      };
    })
    .filter((s) => s.tokens >= 2)
    .sort((a, b) => b.score - a.score);

  console.log("");
  console.log(`Target buys found: ${buys.length}; unique sampled: ${uniqueBuys.length}`);
  console.log(`Earlier same-mint buy hits: ${hits.length}`);
  if (uniqueBuys.length > 0) {
    const quoteSol = uniqueBuys
      .map((b) => (b.quoteMint === WRAPPED_SOL_MINT ? b.quoteRaw / 1e9 : b.quoteRaw / 1e6 / 150))
      .sort((a, b) => a - b);
    const avg = quoteSol.reduce((a, b) => a + b, 0) / quoteSol.length;
    console.log(
      `Target buy size: avg=${avg.toFixed(5)} SOL median=${quoteSol[Math.floor(quoteSol.length / 2)]?.toFixed(5)} SOL min=${quoteSol[0]?.toFixed(5)} SOL max=${quoteSol[quoteSol.length - 1]?.toFixed(5)} SOL`
    );
  }
  console.log("");
  console.log("Top repeated earlier buyers:");
  if (scores.length === 0) console.log("  No repeated candidate wallets found.");
  for (const s of scores.slice(0, 20)) {
    console.log(
      `  ${short(s.wallet)} tokens=${s.tokens} hits=${s.hits} medianLead=${fmtAge(s.medianLead)} avgLead=${fmtAge(s.avgLead)} size~${s.quoteSol.toFixed(3)} SOL wallet=${s.wallet}`
    );
  }

  if (!summaryOnly) {
    console.log("");
    console.log("Per-token evidence:");
    for (const buy of uniqueBuys) {
      console.log(`  ${short(buy.mint)} target=${fmtQuote(buy.quoteRaw, buy.quoteMint)} tx=${short(buy.signature)}`);
      const tokenHits = hits
        .filter((h) => h.mint === buy.mint)
        .sort((a, b) => a.leadSeconds - b.leadSeconds)
        .slice(0, 8);
      if (tokenHits.length === 0) {
        console.log("    no earlier same-mint buys in window");
        continue;
      }
      for (const h of tokenHits) {
        console.log(
          `    ${short(h.candidate.wallet)} lead=${fmtAge(h.leadSeconds)} size=${fmtQuote(h.candidate.quoteRaw, h.candidate.quoteMint)} wallet=${h.candidate.wallet}`
        );
      }
    }
  }
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
