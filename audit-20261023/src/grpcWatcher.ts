import * as YellowstoneGrpc from "@triton-one/yellowstone-grpc";
import type {
  SubscribeRequest,
  SubscribeUpdate,
  SubscribeUpdateTransactionInfo,
  CommitmentLevel,
} from "@triton-one/yellowstone-grpc";
import { PublicKey } from "@solana/web3.js";
import bs58 from "bs58";
import {
  shyftGrpcCommitment,
  shyftGrpcReconnectMs,
  shyftGrpcUrl,
  shyftGrpcXToken,
} from "./config.js";
import type { DetectedSwap, SourcePumpSwapAccountMeta } from "./types.js";
import type { SignatureDedupe } from "./watcher.js";
import {
  PUMPSWAP_BUY_DISCRIMINATOR,
  PUMPSWAP_BUY_EXACT_QUOTE_IN_DISCRIMINATOR,
  PUMPSWAP_SELL_DISCRIMINATOR,
} from "./pumpswap/route.js";
import { RAYDIUM_CPMM_PROGRAM_ID_BASE58, RAYDIUM_CPMM_SWAP_BASE_INPUT_DISCRIMINATOR } from "./raydiumCpmm/route.js";

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const PUMP_FUN_PROGRAM_ID_BASE58 = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
const PUMPSWAP_PROGRAM_ID_BASE58 = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA";
const pumpSwapGrpcShredstreamGraceMs = (() => {
  const explicit = process.env.PUMPSWAP_GRPC_SHREDSTREAM_GRACE_MS;
  const defaultMs = process.env.JITO_SHREDSTREAM_ENABLED === "true" ? 35 : 0;
  const parsed = Number(explicit ?? defaultMs);
  return Number.isFinite(parsed) ? Math.max(0, Math.floor(parsed)) : defaultMs;
})();

function isPumpSwapBuyForGrace(swap: DetectedSwap): boolean {
  return Boolean(swap.sourcePumpSwapAccounts?.length) && swap.inputMint === WRAPPED_SOL_MINT;
}

/**
 * Scan the source's tx (top-level + inner via CPI) for a pump.fun buy ix that matches `mint`,
 * and return the full account list in order. Routers (term9YPb…, FLASHX8…) wrap pump.fun in
 * a CPI, so we MUST walk inner instructions — the buy is usually not at the top level.
 *
 * The account list contains per-coin dynamic recipients (fix1 protocol fee, REM0/REM1
 * buyback+creator share) that rotate per launch and aren't derivable; without them the buy
 * fails with `BuybackFeeRecipientMissing` (6062). Returning these here lets the buy builder
 * use the source's exact accounts instead of guessing.
 */
function extractPumpFunBuyIxAccounts(
  info: SubscribeUpdateTransactionInfo,
  allKeysBase58: string[],
  mint: string
): { accounts: string[]; dataBase64: string } | null {
  const message = info.transaction?.message;
  const meta = info.meta;
  if (!message || !meta) return null;

  const tryIx = (
    programIdIndex: number,
    accIndexes: Uint8Array | number[] | undefined,
    dataRaw: unknown
  ): { accounts: string[]; dataBase64: string } | null => {
    if (!accIndexes) return null;
    const programId = allKeysBase58[programIdIndex];
    if (programId !== PUMP_FUN_PROGRAM_ID_BASE58) return null;
    const data = ixDataToBuffer(dataRaw);
    if (data.length < 8) return null;
    const idxArr = accIndexes instanceof Uint8Array ? Array.from(accIndexes) : accIndexes;
    if (idxArr.length < 16) return null;
    const keys = idxArr.map((i) => allKeysBase58[i]!);
    // Legacy buy layout: mint at slot 2, program self-ref at slot 11.
    const isLegacyBuy = keys[2] === mint && keys[11] === PUMP_FUN_PROGRAM_ID_BASE58;
    // V2 buy layout: base mint at slot 1, program self-ref at slot 26.
    const isV2Buy =
      idxArr.length >= 27 &&
      keys[1] === mint &&
      keys[26] === PUMP_FUN_PROGRAM_ID_BASE58;
    if (!isLegacyBuy && !isV2Buy) return null;
    return { accounts: keys, dataBase64: data.toString("base64") };
  };

  for (const ix of message.instructions ?? []) {
    const found = tryIx(ix.programIdIndex, ix.accounts, (ix as unknown as { data?: unknown }).data);
    if (found) return found;
  }
  for (const group of meta.innerInstructions ?? []) {
    for (const ix of group.instructions ?? []) {
      const found = tryIx(ix.programIdIndex, ix.accounts, (ix as unknown as { data?: unknown }).data);
      if (found) return found;
    }
  }
  return null;
}


function collectSourceProgramIds(
  info: SubscribeUpdateTransactionInfo,
  allKeysBase58: string[]
): string[] {
  const message = info.transaction?.message;
  const meta = info.meta;
  if (!message || !meta) return [];
  const ids = new Set<string>();
  const add = (programIdIndex: number | undefined) => {
    if (typeof programIdIndex !== "number") return;
    const id = allKeysBase58[programIdIndex];
    if (id) ids.add(id);
  };
  for (const ix of message.instructions ?? []) add(ix.programIdIndex);
  for (const group of meta.innerInstructions ?? []) {
    for (const ix of group.instructions ?? []) add(ix.programIdIndex);
  }
  return [...ids];
}

function ixDataToBuffer(data: unknown): Buffer {
  if (data instanceof Uint8Array) return Buffer.from(data);
  if (Array.isArray(data)) return Buffer.from(data);
  if (typeof data === "string") {
    try {
      return Buffer.from(bs58.decode(data));
    } catch {
      return Buffer.from(data, "base64");
    }
  }
  return Buffer.alloc(0);
}

function accountMetaFromMessage(
  info: SubscribeUpdateTransactionInfo,
  allKeysBase58: string[],
  index: number
): SourcePumpSwapAccountMeta | null {
  const message = info.transaction?.message;
  const meta = info.meta;
  if (!message || !meta) return null;
  const pubkey = allKeysBase58[index];
  if (!pubkey) return null;

  const staticCount = message.accountKeys.length;
  const loadedWritableCount = meta.loadedWritableAddresses.length;
  const header = message.header;
  const signerCount = header?.numRequiredSignatures ?? 0;
  const readonlySignedCount = header?.numReadonlySignedAccounts ?? 0;
  const readonlyUnsignedCount = header?.numReadonlyUnsignedAccounts ?? 0;

  if (index < staticCount) {
    const isSigner = index < signerCount;
    const isWritable = isSigner
      ? index < signerCount - readonlySignedCount
      : index < staticCount - readonlyUnsignedCount;
    return { pubkey, isSigner, isWritable };
  }

  const loadedIndex = index - staticCount;
  return {
    pubkey,
    isSigner: false,
    isWritable: loadedIndex < loadedWritableCount,
  };
}

/**
 * Capture the exact PumpSwap AMM account list from the source tx. This is the graduated-token
 * equivalent of sourceIxAccounts for pump.fun bonding-curve routes.
 */
function extractRaydiumCpmmIx(
  info: SubscribeUpdateTransactionInfo,
  allKeysBase58: string[],
  mint: string
): { accounts: SourcePumpSwapAccountMeta[]; instruction: "swap_base_input" } | null {
  const message = info.transaction?.message;
  const meta = info.meta;
  if (!message || !meta) return null;

  const tryIx = (
    programIdIndex: number,
    accIndexes: Uint8Array | number[] | undefined,
    dataRaw: unknown
  ): { accounts: SourcePumpSwapAccountMeta[]; instruction: "swap_base_input" } | null => {
    if (!accIndexes) return null;
    const programId = allKeysBase58[programIdIndex];
    if (programId !== RAYDIUM_CPMM_PROGRAM_ID_BASE58) return null;
    const data = ixDataToBuffer(dataRaw);
    if (!data.subarray(0, 8).equals(RAYDIUM_CPMM_SWAP_BASE_INPUT_DISCRIMINATOR)) return null;
    const idxArr = accIndexes instanceof Uint8Array ? Array.from(accIndexes) : accIndexes;
    if (idxArr.length < 13) return null;
    const keys = idxArr.map((i) => allKeysBase58[i]!);
    if (keys[10] !== mint && keys[11] !== mint) return null;
    if (![keys[10], keys[11]].includes(WRAPPED_SOL_MINT) && ![keys[10], keys[11]].includes(USDC_MINT)) {
      return null;
    }
    const accounts = idxArr.map((i) => accountMetaFromMessage(info, allKeysBase58, i));
    if (accounts.some((account) => account == null)) return null;
    return { accounts: accounts as SourcePumpSwapAccountMeta[], instruction: "swap_base_input" };
  };

  for (const ix of message.instructions ?? []) {
    const found = tryIx(ix.programIdIndex, ix.accounts, (ix as unknown as { data?: unknown }).data);
    if (found) return found;
  }
  for (const group of meta.innerInstructions ?? []) {
    for (const ix of group.instructions ?? []) {
      const found = tryIx(ix.programIdIndex, ix.accounts, (ix as unknown as { data?: unknown }).data);
      if (found) return found;
    }
  }
  return null;
}
function extractPumpSwapIx(
  info: SubscribeUpdateTransactionInfo,
  allKeysBase58: string[],
  mint: string
): { accounts: SourcePumpSwapAccountMeta[]; instruction: "buy" | "buy_exact_quote_in" } | null {
  const message = info.transaction?.message;
  const meta = info.meta;
  if (!message || !meta) return null;

  const tryIx = (
    programIdIndex: number,
    accIndexes: Uint8Array | number[] | undefined,
    dataRaw: unknown
  ): { accounts: SourcePumpSwapAccountMeta[]; instruction: "buy" | "buy_exact_quote_in" } | null => {
    if (!accIndexes) return null;
    const programId = allKeysBase58[programIdIndex];
    if (programId !== PUMPSWAP_PROGRAM_ID_BASE58) return null;
    const data = ixDataToBuffer(dataRaw);
    const discriminator = data.subarray(0, 8);
    const isBuy = discriminator.equals(PUMPSWAP_BUY_DISCRIMINATOR);
    const isExactQuoteIn = discriminator.equals(PUMPSWAP_BUY_EXACT_QUOTE_IN_DISCRIMINATOR);
    const isSell = discriminator.equals(PUMPSWAP_SELL_DISCRIMINATOR);
    if (!isBuy && !isExactQuoteIn && !isSell) {
      return null;
    }
    const idxArr = accIndexes instanceof Uint8Array ? Array.from(accIndexes) : accIndexes;
    if (idxArr.length < 17) return null;
    const keys = idxArr.map((i) => allKeysBase58[i]!);
    if (keys[3] !== mint || keys[4] !== WRAPPED_SOL_MINT) return null;
    const accounts = idxArr.map((i) => accountMetaFromMessage(info, allKeysBase58, i));
    if (accounts.some((account) => account == null)) return null;
    return {
      accounts: accounts as SourcePumpSwapAccountMeta[],
      instruction: isBuy ? "buy" : "buy_exact_quote_in",
    };
  };

  for (const ix of message.instructions ?? []) {
    const found = tryIx(ix.programIdIndex, ix.accounts, (ix as unknown as { data?: unknown }).data);
    if (found) return found;
  }
  for (const group of meta.innerInstructions ?? []) {
    for (const ix of group.instructions ?? []) {
      const found = tryIx(ix.programIdIndex, ix.accounts, (ix as unknown as { data?: unknown }).data);
      if (found) return found;
    }
  }
  return null;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function commitmentFromConfig(): CommitmentLevel {
  if (shyftGrpcCommitment === "confirmed") return 1 as CommitmentLevel;
  if (shyftGrpcCommitment === "finalized") return 2 as CommitmentLevel;
  return 0 as CommitmentLevel;
}

type YellowstoneClientConstructor = new (
  endpoint: string,
  xToken: string | undefined,
  channelOptions: undefined
) => {
  subscribe(): Promise<YellowstoneStream>;
};

type MaybeWrappedDefault = { default?: unknown };

type YellowstoneStream = {
  on(event: "data", listener: (update: SubscribeUpdate) => void): YellowstoneStream;
  on(event: "error", listener: (err: Error) => void): YellowstoneStream;
  on(event: "end" | "close", listener: () => void): YellowstoneStream;
  write(chunk: SubscribeRequest, cb: (err?: Error | null) => void): void;
  destroy(): void;
};

function pubkeyToBase58(bytes: Uint8Array): string {
  return new PublicKey(bytes).toBase58();
}

function signatureToBase58(bytes: Uint8Array): string {
  return bs58.encode(bytes);
}

function numberFromBigint(value: bigint): number | null {
  const n = Number(value);
  return Number.isSafeInteger(n) ? n : null;
}

type TokenSnap = { mint: string; amount: bigint; decimals: number };
type TokenNet = { net: bigint; decimals: number };

function isMirrorQuoteMint(mint: string | undefined): boolean {
  return mint === WRAPPED_SOL_MINT || mint === USDC_MINT;
}

function addTokenNet(
  tokenNet: Map<string, TokenNet>,
  mint: string | undefined,
  amountRaw: bigint,
  decimals: number | undefined
): void {
  if (!mint || amountRaw === 0n) return;
  const entry = tokenNet.get(mint) ?? { net: 0n, decimals: decimals ?? 0 };
  entry.net += amountRaw;
  entry.decimals = decimals ?? entry.decimals;
  tokenNet.set(mint, entry);
}

function getBestTokenNet(
  tokenNet: Map<string, TokenNet>,
  options: { excludeQuoteMints?: boolean } = {}
): {
  mint: string | null;
  net: bigint;
  abs: bigint;
  decimals: number;
} {
  let mint: string | null = null;
  let net = 0n;
  let abs = 0n;
  let decimals = 0;
  for (const [candidateMint, candidate] of tokenNet) {
    if (options.excludeQuoteMints && isMirrorQuoteMint(candidateMint)) continue;
    const candidateAbs = candidate.net < 0n ? -candidate.net : candidate.net;
    if (candidateAbs > abs) {
      mint = candidateMint;
      net = candidate.net;
      abs = candidateAbs;
      decimals = candidate.decimals;
    }
  }
  return { mint, net, abs, decimals };
}

function getBestQuoteNet(tokenNet: Map<string, TokenNet>, wantPositive: boolean): {
  mint: string | null;
  net: bigint;
  abs: bigint;
} {
  let mint: string | null = null;
  let net = 0n;
  let abs = 0n;
  for (const candidateMint of [WRAPPED_SOL_MINT, USDC_MINT]) {
    const candidate = tokenNet.get(candidateMint);
    if (!candidate) continue;
    if (wantPositive && candidate.net <= 0n) continue;
    if (!wantPositive && candidate.net >= 0n) continue;
    const candidateAbs = candidate.net < 0n ? -candidate.net : candidate.net;
    if (candidateAbs > abs) {
      mint = candidateMint;
      net = candidate.net;
      abs = candidateAbs;
    }
  }
  return { mint, net, abs };
}

export function extractSwapFromGrpcTransaction(
  info: SubscribeUpdateTransactionInfo,
  wallet: string
): DetectedSwap | null {
  if (!info.transaction?.message || !info.meta || info.meta.err) return null;

  const message = info.transaction.message;
  const meta = info.meta;
  const accountKeys = [
    ...message.accountKeys,
    ...meta.loadedWritableAddresses,
    ...meta.loadedReadonlyAddresses,
  ].map(pubkeyToBase58);
  const sourceProgramIds = collectSourceProgramIds(info, accountKeys);
  const walletIdx = accountKeys.indexOf(wallet);
  if (walletIdx < 0) return null;

  const preLamports = BigInt(meta.preBalances[walletIdx] ?? "0");
  const postLamports = BigInt(meta.postBalances[walletIdx] ?? "0");
  const fee = BigInt(meta.fee ?? "0");
  const signerCount = message.header?.numRequiredSignatures ?? 0;
  const isSigner = walletIdx < signerCount;
  const nativeNet = postLamports - preLamports + (isSigner ? fee : 0n);

  const preMap = new Map<number, TokenSnap>();
  for (const balance of meta.preTokenBalances) {
    if (balance.owner !== wallet) continue;
    preMap.set(balance.accountIndex, {
      mint: balance.mint,
      amount: BigInt(balance.uiTokenAmount?.amount ?? "0"),
      decimals: balance.uiTokenAmount?.decimals ?? 0,
    });
  }

  const tokenNet = new Map<string, TokenNet>();
  const handledIndices = new Set<number>();
  for (const balance of meta.postTokenBalances) {
    if (balance.owner !== wallet) continue;
    handledIndices.add(balance.accountIndex);
    const pre = preMap.get(balance.accountIndex);
    const preAmount = pre?.amount ?? 0n;
    const postAmount = BigInt(balance.uiTokenAmount?.amount ?? "0");
    addTokenNet(
      tokenNet,
      balance.mint,
      postAmount - preAmount,
      balance.uiTokenAmount?.decimals ?? pre?.decimals
    );
  }
  for (const [accountIndex, pre] of preMap) {
    if (!handledIndices.has(accountIndex)) {
      addTokenNet(tokenNet, pre.mint, -pre.amount, pre.decimals);
    }
  }

  const best = getBestTokenNet(tokenNet, { excludeQuoteMints: true });
  if (!best.mint || best.abs === 0n) return null;
  const signature = signatureToBase58(info.signature);

  if (nativeNet < 0n && best.net > 0n) {
    const quote = getBestQuoteNet(tokenNet, false);
    const sourceSpendRaw = quote.mint ? quote.abs : -nativeNet;
    const amountRaw = numberFromBigint(sourceSpendRaw);
    const counterpartyAmountRaw = numberFromBigint(best.net);
    if (amountRaw == null || counterpartyAmountRaw == null) return null;
    // Only buys (SOL→token) need source ix extraction. If the source's tx routes through
    // pump.fun (top-level or via router CPI), capture the account list so the buy builder
    // can use the exact per-coin recipients pump.fun rotates per launch.
    const sourcePumpFunIx =
      extractPumpFunBuyIxAccounts(info, accountKeys, best.mint) ?? undefined;
    const sourcePumpSwapIx =
      extractPumpSwapIx(info, accountKeys, best.mint) ?? undefined;
    const sourceRaydiumCpmmIx = extractRaydiumCpmmIx(info, accountKeys, best.mint) ?? undefined;
    return {
      signature,
      wallet,
      sourceProgramIds,
      inputMint: quote.mint ?? WRAPPED_SOL_MINT,
      outputMint: best.mint,
      amountRaw,
      isNativeInput: (quote.mint ?? WRAPPED_SOL_MINT) === WRAPPED_SOL_MINT,
      counterpartyAmountRaw,
      counterpartyDecimals: best.decimals,
      sourceIxAccounts: sourcePumpFunIx?.accounts,
      sourceIxDataBase64: sourcePumpFunIx?.dataBase64,
      sourcePumpSwapInstruction: sourcePumpSwapIx?.instruction,
      sourcePumpSwapAccounts: sourcePumpSwapIx?.accounts,
      sourceRaydiumCpmmInstruction: sourceRaydiumCpmmIx?.instruction,
      sourceRaydiumCpmmAccounts: sourceRaydiumCpmmIx?.accounts,
      sourceQuoteMint: quote.mint ?? WRAPPED_SOL_MINT,
      sourceQuoteAmountRaw: amountRaw,
    };
  }

  if (nativeNet > 0n && best.net < 0n) {
    const amountRaw = numberFromBigint(-best.net);
    const counterpartyAmountRaw = numberFromBigint(nativeNet);
    if (amountRaw == null || counterpartyAmountRaw == null) return null;
    const sourcePumpSwapIx = extractPumpSwapIx(info, accountKeys, best.mint) ?? undefined;
    const sourceRaydiumCpmmIx = extractRaydiumCpmmIx(info, accountKeys, best.mint) ?? undefined;
    return {
      signature,
      wallet,
      sourceProgramIds,
      inputMint: best.mint,
      outputMint: WRAPPED_SOL_MINT,
      amountRaw,
      isNativeInput: false,
      counterpartyAmountRaw,
      counterpartyDecimals: 9,
      sourcePumpSwapAccounts: sourcePumpSwapIx?.accounts,
      sourceRaydiumCpmmInstruction: sourceRaydiumCpmmIx?.instruction,
      sourceRaydiumCpmmAccounts: sourceRaydiumCpmmIx?.accounts,
    };
  }

  // Graduated-token wallets often route through USDC or a WSOL token account, so the wallet's
  // native SOL balance does not move. Preserve the quote mint so mirror mode can choose SOL,
  // USDC, or MATCH.
  if (nativeNet === 0n && best.net > 0n) {
    const quote = getBestQuoteNet(tokenNet, false);
    if (quote.mint) {
      const amountRaw = numberFromBigint(quote.abs);
      const counterpartyAmountRaw = numberFromBigint(best.net);
      if (amountRaw == null || counterpartyAmountRaw == null) return null;
      const sourcePumpSwapIx = extractPumpSwapIx(info, accountKeys, best.mint) ?? undefined;
      const sourceRaydiumCpmmIx = extractRaydiumCpmmIx(info, accountKeys, best.mint) ?? undefined;
      return {
        signature,
        wallet,
        sourceProgramIds,
        inputMint: quote.mint,
        outputMint: best.mint,
        amountRaw,
        isNativeInput: quote.mint === WRAPPED_SOL_MINT,
        counterpartyAmountRaw,
        counterpartyDecimals: best.decimals,
        sourcePumpSwapInstruction: sourcePumpSwapIx?.instruction,
        sourcePumpSwapAccounts: sourcePumpSwapIx?.accounts,
        sourceRaydiumCpmmInstruction: sourceRaydiumCpmmIx?.instruction,
        sourceRaydiumCpmmAccounts: sourceRaydiumCpmmIx?.accounts,
        sourceQuoteMint: quote.mint,
        sourceQuoteAmountRaw: amountRaw,
      };
    }
  }

  if (nativeNet === 0n && best.net < 0n) {
    const quote = getBestQuoteNet(tokenNet, true);
    if (quote.mint) {
      const amountRaw = numberFromBigint(-best.net);
      const counterpartyAmountRaw = numberFromBigint(quote.net);
      if (amountRaw == null || counterpartyAmountRaw == null) return null;
      const sourcePumpSwapIx = extractPumpSwapIx(info, accountKeys, best.mint) ?? undefined;
      const sourceRaydiumCpmmIx = extractRaydiumCpmmIx(info, accountKeys, best.mint) ?? undefined;
      return {
        signature,
        wallet,
        sourceProgramIds,
        inputMint: best.mint,
        outputMint: WRAPPED_SOL_MINT,
        amountRaw,
        isNativeInput: false,
        counterpartyAmountRaw,
        counterpartyDecimals: quote.mint === USDC_MINT ? 6 : 9,
        sourcePumpSwapAccounts: sourcePumpSwapIx?.accounts,
        sourceRaydiumCpmmInstruction: sourceRaydiumCpmmIx?.instruction,
        sourceRaydiumCpmmAccounts: sourceRaydiumCpmmIx?.accounts,
      };
    }
  }

  return null;
}

function buildRequest(trackedWallets: string[]): SubscribeRequest {
  return {
    accounts: {},
    slots: {},
    transactions: {
      trackedWallets: {
        vote: false,
        failed: false,
        signature: undefined,
        accountInclude: trackedWallets,
        accountExclude: [],
        accountRequired: [],
      },
    },
    transactionsStatus: {},
    blocks: {},
    blocksMeta: {},
    entry: {},
    accountsDataSlice: [],
    ping: undefined,
    commitment: commitmentFromConfig(),
  };
}

async function writeRequest(stream: YellowstoneStream, request: SubscribeRequest): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    stream.write(request, (err?: Error | null) => {
      if (err) reject(err);
      else resolve();
    });
  });
}

export type GrpcUnsubscribe = () => Promise<void>;

export async function subscribeGrpcWalletSwaps(
  trackedWallets: string[],
  processedSignatures: SignatureDedupe,
  onSwap: (swap: DetectedSwap) => void
): Promise<GrpcUnsubscribe> {
  if (!shyftGrpcUrl || !shyftGrpcXToken) {
    console.warn("gRPC: SHYFT_GRPC_URL or SHYFT_GRPC_X_TOKEN missing; skipping gRPC watcher.");
    return async () => {};
  }

  let stopped = false;
  let activeStream: { destroy: () => void } | null = null;
  const clientExport = YellowstoneGrpc.default as unknown;
  const ClientCtor = (
    typeof clientExport === "function" ? clientExport : (clientExport as MaybeWrappedDefault).default
  ) as YellowstoneClientConstructor | undefined;
  if (typeof ClientCtor !== "function") {
    throw new TypeError("gRPC: Yellowstone client constructor could not be loaded.");
  }
  const client = new ClientCtor(shyftGrpcUrl, shyftGrpcXToken, undefined);
  const request = buildRequest(trackedWallets);
  const pendingPumpSwapGrpcSignatures = new Set<string>();

  const run = async (): Promise<void> => {
    while (!stopped) {
      try {
        const stream = await client.subscribe();
        activeStream = stream;
        const streamClosed = new Promise<void>((resolve, reject) => {
          stream.on("error", (err) => {
            if (!stopped) reject(err);
            else resolve();
          });
          stream.on("end", resolve);
          stream.on("close", resolve);
        });

        stream.on("data", (update: SubscribeUpdate) => {
          const receivedAtMs = Date.now();
          const info = update.transaction?.transaction;
          if (!info) return;
          const signature = signatureToBase58(info.signature);
          if (processedSignatures.has(signature)) return;

          for (const wallet of trackedWallets) {
            const swap = extractSwapFromGrpcTransaction(info, wallet);
            if (!swap) continue;
            const providerCreatedAtMs = update.createdAt?.getTime();
            const sourceSlotRaw =
              (update.transaction as unknown as { slot?: number | string | bigint } | undefined)?.slot ??
              (update as unknown as { slot?: number | string | bigint }).slot;
            const sourceSlot =
              typeof sourceSlotRaw === "bigint"
                ? Number(sourceSlotRaw)
                : typeof sourceSlotRaw === "string"
                  ? Number(sourceSlotRaw)
                  : sourceSlotRaw;
            const sourceTransactionIndexRaw =
              (info as unknown as { index?: number | string | bigint }).index ??
              (update.transaction as unknown as { index?: number | string | bigint } | undefined)?.index;
            const sourceTransactionIndex =
              typeof sourceTransactionIndexRaw === "bigint"
                ? Number(sourceTransactionIndexRaw)
                : typeof sourceTransactionIndexRaw === "string"
                  ? Number(sourceTransactionIndexRaw)
                  : sourceTransactionIndexRaw;
            const detectedAtMs = Date.now();
            swap.detector = "shyft-grpc";
            swap.providerCreatedAtMs =
              typeof providerCreatedAtMs === "number" && Number.isFinite(providerCreatedAtMs)
                ? providerCreatedAtMs
                : undefined;
            swap.receivedAtMs = receivedAtMs;
            swap.detectedAtMs = detectedAtMs;
            swap.sourceSlot =
              typeof sourceSlot === "number" && Number.isFinite(sourceSlot) ? sourceSlot : undefined;
            swap.sourceTransactionIndex =
              typeof sourceTransactionIndex === "number" && Number.isFinite(sourceTransactionIndex)
                ? sourceTransactionIndex
                : undefined;
            const ageMs =
              swap.providerCreatedAtMs != null ? detectedAtMs - swap.providerCreatedAtMs : null;
            const pumpSwapGraceMs = isPumpSwapBuyForGrace(swap) ? pumpSwapGrpcShredstreamGraceMs : 0;
            const dispatch = () => {
              if (stopped || processedSignatures.has(signature)) return;
              processedSignatures.add(signature);
              console.log(
                `gRPC detected swap ${signature.slice(0, 8)}...` +
                  (ageMs != null && Number.isFinite(ageMs) ? ` (age ${ageMs}ms)` : "")
              );
              onSwap(swap);
            };
            if (pumpSwapGraceMs > 0) {
              if (pendingPumpSwapGrpcSignatures.has(signature)) return;
              pendingPumpSwapGrpcSignatures.add(signature);
              setTimeout(() => {
                pendingPumpSwapGrpcSignatures.delete(signature);
                dispatch();
              }, pumpSwapGraceMs);
            } else {
              dispatch();
            }
            return;
          }
        });

        await writeRequest(stream, request);
        console.log(
          `gRPC: subscribed to ${trackedWallets.length} wallet(s) via ${shyftGrpcUrl} (${shyftGrpcCommitment})`
        );
        await streamClosed;
      } catch (err) {
        if (stopped) break;
        const msg = err instanceof Error ? err.message : String(err);
        console.warn(`gRPC watcher error: ${msg}; reconnecting in ${shyftGrpcReconnectMs}ms`);
        await sleep(shyftGrpcReconnectMs);
      } finally {
        activeStream = null;
      }
    }
  };

  void run();

  return async () => {
    stopped = true;
    activeStream?.destroy();
  };
}

