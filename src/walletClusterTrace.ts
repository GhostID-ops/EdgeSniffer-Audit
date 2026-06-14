import "dotenv/config";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { heliusAddressTransactionsUrl } from "./config.js";
import { getTokenPairInfo } from "./tokenRegistry.js";

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const CACHE_DIR = path.resolve(process.cwd(), "data", "wallet-cluster-cache");

type HeliusTx = {
  signature: string;
  timestamp?: number;
  type?: string;
  source?: string;
  feePayer?: string;
  nativeTransfers?: Array<{
    fromUserAccount?: string;
    toUserAccount?: string;
    amount?: number;
  }>;
  tokenTransfers?: Array<{
    mint?: string;
    fromUserAccount?: string;
    toUserAccount?: string;
    tokenAmount?: number;
  }>;
  accountData?: Array<{
    account: string;
    nativeBalanceChange?: number;
    tokenBalanceChanges?: Array<{
      userAccount?: string;
      mint?: string;
      rawTokenAmount?: { tokenAmount?: string; decimals?: number };
    }>;
  }>;
};

type Swap = {
  wallet: string;
  signature: string;
  timestamp: number;
  side: "buy" | "sell";
  mint: string;
  solLamports: number;
  tokenRaw: number;
  decimals: number;
};

type TokenMove = {
  signature: string;
  timestamp: number;
  mint: string;
  from: string;
  to: string;
  amount: number | null;
};

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function short(addr: string): string {
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

function sol(lamports: number): number {
  return lamports / 1_000_000_000;
}

function fmtSol(lamports: number): string {
  return `${sol(lamports).toFixed(4)} SOL`;
}

function fmtAge(ts: number): string {
  return new Date(ts * 1000).toISOString();
}

async function cacheGet<T>(key: string): Promise<T | null> {
  try {
    return JSON.parse(await readFile(path.join(CACHE_DIR, key), "utf8")) as T;
  } catch {
    return null;
  }
}

async function cacheSet(key: string, value: unknown): Promise<void> {
  await mkdir(CACHE_DIR, { recursive: true });
  await writeFile(path.join(CACHE_DIR, key), JSON.stringify(value), "utf8");
}

async function fetchAddressTxs(address: string, limit: number, before?: string): Promise<HeliusTx[]> {
  const url = heliusAddressTransactionsUrl(address, before, limit);
  if (!url) throw new Error("HELIUS_API_KEY is not set");
  const response = await fetch(url, { signal: AbortSignal.timeout(15_000) });
  if (!response.ok) throw new Error(`Helius ${response.status}: ${(await response.text()).slice(0, 220)}`);
  return (await response.json()) as HeliusTx[];
}

async function fetchPagedAddressTxs(address: string, maxTxs: number): Promise<HeliusTx[]> {
  const key = `wallet-${address}-${maxTxs}.json`;
  const cached = await cacheGet<HeliusTx[]>(key);
  if (cached) return cached;
  const all: HeliusTx[] = [];
  let before: string | undefined;
  while (all.length < maxTxs) {
    const batch = await fetchAddressTxs(address, Math.min(100, maxTxs - all.length), before);
    if (batch.length === 0) break;
    all.push(...batch);
    before = batch[batch.length - 1]?.signature;
    await sleep(650);
  }
  await cacheSet(key, all);
  return all;
}

function dedupeTxs(txs: HeliusTx[]): HeliusTx[] {
  const bySig = new Map<string, HeliusTx>();
  for (const tx of txs) bySig.set(tx.signature, tx);
  return [...bySig.values()].sort((a, b) => (a.timestamp ?? 0) - (b.timestamp ?? 0));
}

function extractSwap(tx: HeliusTx, wallet: string): Swap | null {
  let nativeNet = 0;
  const tokenNet = new Map<string, { raw: number; decimals: number }>();

  for (const account of tx.accountData ?? []) {
    if (account.account === wallet) nativeNet += account.nativeBalanceChange ?? 0;
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.userAccount !== wallet) continue;
      const mint = change.mint;
      const raw = Number(change.rawTokenAmount?.tokenAmount ?? "0");
      const decimals = change.rawTokenAmount?.decimals ?? 0;
      if (!mint || !Number.isFinite(raw) || raw === 0) continue;
      const entry = tokenNet.get(mint) ?? { raw: 0, decimals };
      entry.raw += raw;
      entry.decimals = decimals;
      tokenNet.set(mint, entry);
    }
  }

  let mint: string | null = null;
  let tokenRaw = 0;
  let decimals = 0;
  for (const [candidate, net] of tokenNet) {
    if (candidate === WRAPPED_SOL_MINT) continue;
    if (Math.abs(net.raw) > Math.abs(tokenRaw)) {
      mint = candidate;
      tokenRaw = net.raw;
      decimals = net.decimals;
    }
  }

  const wsolNet = tokenNet.get(WRAPPED_SOL_MINT)?.raw ?? 0;
  const quoteLamports =
    tokenRaw > 0 && wsolNet < 0
      ? Math.abs(wsolNet)
      : tokenRaw < 0 && wsolNet > 0
        ? wsolNet
        : tokenRaw > 0 && nativeNet < 0
          ? Math.abs(nativeNet)
          : tokenRaw < 0 && nativeNet > 0
            ? nativeNet
            : 0;

  if (!mint || tokenRaw === 0 || quoteLamports <= 0) return null;
  return {
    wallet,
    signature: tx.signature,
    timestamp: tx.timestamp ?? 0,
    side: tokenRaw > 0 ? "buy" : "sell",
    mint,
    solLamports: quoteLamports,
    tokenRaw: Math.abs(tokenRaw),
    decimals,
  };
}

function directTokenMoves(tx: HeliusTx, a: string, b: string): TokenMove[] {
  const moves: TokenMove[] = [];
  for (const transfer of tx.tokenTransfers ?? []) {
    const from = transfer.fromUserAccount;
    const to = transfer.toUserAccount;
    const mint = transfer.mint;
    if (!from || !to || !mint) continue;
    const isDirect = (from === a && to === b) || (from === b && to === a);
    if (!isDirect) continue;
    moves.push({
      signature: tx.signature,
      timestamp: tx.timestamp ?? 0,
      mint,
      from,
      to,
      amount: typeof transfer.tokenAmount === "number" ? transfer.tokenAmount : null,
    });
  }
  return moves;
}

function directSolMoves(txs: HeliusTx[], a: string, b: string): Array<{ timestamp: number; signature: string; from: string; to: string; lamports: number }> {
  const out = [];
  for (const tx of txs) {
    for (const transfer of tx.nativeTransfers ?? []) {
      const from = transfer.fromUserAccount;
      const to = transfer.toUserAccount;
      const amount = transfer.amount ?? 0;
      if (!from || !to || amount <= 0) continue;
      if ((from === a && to === b) || (from === b && to === a)) {
        out.push({ timestamp: tx.timestamp ?? 0, signature: tx.signature, from, to, lamports: amount });
      }
    }
  }
  return out.sort((x, y) => y.timestamp - x.timestamp);
}

async function labelMint(mint: string): Promise<string> {
  try {
    const info = await getTokenPairInfo(mint);
    const symbol = info?.baseToken?.symbol;
    return symbol ? `${symbol} (${short(mint)})` : short(mint);
  } catch {
    return short(mint);
  }
}

async function main(): Promise<void> {
  const walletA = (process.argv[2] ?? "").trim();
  const walletB = (process.argv[3] ?? "").trim();
  const maxTxs = Number(process.argv[4] ?? "2500");
  if (!walletA || !walletB) {
    throw new Error("usage: tsx src/walletClusterTrace.ts WALLET_A WALLET_B [maxTxs]");
  }

  console.log(`Cluster trace ${short(walletA)} <-> ${short(walletB)} maxTxs=${maxTxs}`);
  const txs = dedupeTxs([...(await fetchPagedAddressTxs(walletA, maxTxs)), ...(await fetchPagedAddressTxs(walletB, maxTxs))]);
  const swaps = txs
    .flatMap((tx) => [extractSwap(tx, walletA), extractSwap(tx, walletB)])
    .filter((swap): swap is Swap => swap != null);
  const moves = txs.flatMap((tx) => directTokenMoves(tx, walletA, walletB)).sort((x, y) => y.timestamp - x.timestamp);
  const solMoves = directSolMoves(txs, walletA, walletB);

  const byMint = new Map<string, TokenMove[]>();
  for (const move of moves) {
    const rows = byMint.get(move.mint) ?? [];
    rows.push(move);
    byMint.set(move.mint, rows);
  }

  let aToB = 0;
  let bToA = 0;
  for (const move of moves) {
    if (move.from === walletA && move.to === walletB) aToB++;
    if (move.from === walletB && move.to === walletA) bToA++;
  }

  console.log("");
  console.log("Direct Link");
  console.log(`  Unique txs scanned: ${txs.length}`);
  console.log(`  Direct token transfers: ${moves.length}`);
  console.log(`  ${short(walletA)} -> ${short(walletB)}: ${aToB}`);
  console.log(`  ${short(walletB)} -> ${short(walletA)}: ${bToA}`);
  console.log(`  Shared transferred mints: ${byMint.size}`);
  console.log(`  Direct SOL transfers: ${solMoves.length}`);
  for (const row of solMoves.slice(0, 10)) {
    console.log(`    ${fmtAge(row.timestamp)} ${short(row.from)} -> ${short(row.to)} ${fmtSol(row.lamports)} sig=${row.signature.slice(0, 10)}`);
  }

  console.log("");
  console.log("Recent Direct Token Transfers");
  for (const move of moves.slice(0, 30)) {
    console.log(
      [
        fmtAge(move.timestamp),
        await labelMint(move.mint),
        `${short(move.from)} -> ${short(move.to)}`,
        move.amount == null ? "amount=-" : `amount=${move.amount}`,
        `sig=${move.signature.slice(0, 10)}`,
      ].join(" | ")
    );
  }

  console.log("");
  console.log("Per-Mint System Trace");
  for (const [mint, mintMoves] of [...byMint.entries()].slice(0, 25)) {
    const mintSwaps = swaps.filter((swap) => swap.mint === mint).sort((a, b) => a.timestamp - b.timestamp);
    const firstBuyA = mintSwaps.find((swap) => swap.wallet === walletA && swap.side === "buy");
    const firstBuyB = mintSwaps.find((swap) => swap.wallet === walletB && swap.side === "buy");
    const firstSellA = mintSwaps.find((swap) => swap.wallet === walletA && swap.side === "sell");
    const firstSellB = mintSwaps.find((swap) => swap.wallet === walletB && swap.side === "sell");
    const firstMove = mintMoves.sort((x, y) => x.timestamp - y.timestamp)[0]!;
    console.log("");
    console.log(`  ${await labelMint(mint)}`);
    console.log(`    first transfer: ${fmtAge(firstMove.timestamp)} ${short(firstMove.from)} -> ${short(firstMove.to)} amount=${firstMove.amount ?? "-"}`);
    console.log(`    ${short(walletA)} first buy: ${firstBuyA ? `${fmtAge(firstBuyA.timestamp)} ${fmtSol(firstBuyA.solLamports)}` : "-"}`);
    console.log(`    ${short(walletA)} first sell: ${firstSellA ? `${fmtAge(firstSellA.timestamp)} ${fmtSol(firstSellA.solLamports)}` : "-"}`);
    console.log(`    ${short(walletB)} first buy: ${firstBuyB ? `${fmtAge(firstBuyB.timestamp)} ${fmtSol(firstBuyB.solLamports)}` : "-"}`);
    console.log(`    ${short(walletB)} first sell: ${firstSellB ? `${fmtAge(firstSellB.timestamp)} ${fmtSol(firstSellB.solLamports)}` : "-"}`);
    for (const swap of mintSwaps.slice(0, 10)) {
      console.log(`      ${fmtAge(swap.timestamp)} ${short(swap.wallet)} ${swap.side} ${fmtSol(swap.solLamports)} sig=${swap.signature.slice(0, 10)}`);
    }
  }
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
