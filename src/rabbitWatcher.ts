import * as YellowstoneGrpc from "@triton-one/yellowstone-grpc";
import type {
  CommitmentLevel,
  SubscribeRequest,
  SubscribeUpdate,
  SubscribeUpdateTransactionInfo,
} from "@triton-one/yellowstone-grpc";
import { PublicKey } from "@solana/web3.js";
import bs58 from "bs58";
import {
  shyftGrpcReconnectMs,
  shyftGrpcXToken,
  shyftRabbitStreamUrl,
} from "./config.js";
import {
  BUY_DISCRIMINATOR,
  BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR,
  BUY_EXACT_SOL_IN_DISCRIMINATOR,
  BUY_V2_DISCRIMINATOR,
  PUMP_FUN_PROGRAM_ID,
  SELL_DISCRIMINATOR,
  SELL_V2_DISCRIMINATOR,
} from "./pumpfun/constants.js";
import type { DetectedSwap, SourcePumpSwapAccountMeta } from "./types.js";
import type { SignatureDedupe } from "./watcher.js";

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const PUMPSWAP_PROGRAM_ID_BASE58 = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA";
const PUMPSWAP_BUY_DISCRIMINATOR = Buffer.from([102, 6, 61, 18, 1, 218, 235, 234]);
const PUMPSWAP_BUY_EXACT_QUOTE_IN_DISCRIMINATOR = Buffer.from([198, 46, 21, 82, 180, 217, 232, 112]);

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

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function pubkeyToBase58(bytes: Uint8Array): string {
  return new PublicKey(bytes).toBase58();
}

function signatureToBase58(bytes: Uint8Array): string {
  return bs58.encode(bytes);
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

function ixDataToBuffer(data: unknown): Buffer {
  if (data == null) return Buffer.alloc(0);
  if (Buffer.isBuffer(data)) return data;
  if (data instanceof Uint8Array) return Buffer.from(data);
  if (Array.isArray(data)) return Buffer.from(data);
  if (typeof data === "string") {
    try {
      return Buffer.from(bs58.decode(data));
    } catch {
      try {
        return Buffer.from(data, "base64");
      } catch {
        return Buffer.alloc(0);
      }
    }
  }
  return Buffer.alloc(0);
}

function discriminatorMatches(data: Buffer, discriminator: Buffer): boolean {
  return data.length >= discriminator.length && data.subarray(0, discriminator.length).equals(discriminator);
}

function isDirectPumpFunUser(keys: string[], isV2Layout: boolean, wallet: string): boolean {
  // Legacy buy: slot 6 is the signer/user. V2 buy: slot 13 is the signer/user.
  // A tracked wallet can appear elsewhere in aggregator/creator/fee txs; that is not a copy signal.
  return (isV2Layout ? keys[13] : keys[6]) === wallet;
}

function isMirrorQuoteMint(mint: string | undefined): boolean {
  return mint === WRAPPED_SOL_MINT || mint === USDC_MINT;
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
    commitment: 0 as CommitmentLevel,
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

function extractDirectPumpFunSwapFromRabbit(
  info: SubscribeUpdateTransactionInfo,
  wallet: string
): DetectedSwap | null {
  if (!info.meta || info.meta.err) return null;
  const message = info.transaction?.message;
  if (!message) return null;

  const accountKeys = (message.accountKeys ?? []).map(pubkeyToBase58);
  if (!accountKeys.includes(wallet)) return null;

  for (const ix of message.instructions ?? []) {
    const programIdIndex = Number(ix.programIdIndex);
    if (!Number.isInteger(programIdIndex)) continue;
    const programId = accountKeys[programIdIndex];
    if (programId !== PUMP_FUN_PROGRAM_ID.toBase58()) continue;

    const accountIndexes = (
      ix.accounts instanceof Uint8Array ? Array.from(ix.accounts) : Array.from(ix.accounts ?? [])
    )
      .map((idx) => Number(idx))
      .filter((idx) => Number.isInteger(idx) && idx >= 0);
    const sourceIxAccounts = accountIndexes.map((idx) => accountKeys[idx]!).filter(Boolean);
    const isLegacyLayout =
      sourceIxAccounts.length >= 16 &&
      sourceIxAccounts[2] != null &&
      sourceIxAccounts[11] === PUMP_FUN_PROGRAM_ID.toBase58();
    const isV2Layout =
      sourceIxAccounts.length >= 27 &&
      sourceIxAccounts[1] != null &&
      sourceIxAccounts[26] === PUMP_FUN_PROGRAM_ID.toBase58();
    if (!isLegacyLayout && !isV2Layout) continue;
    if (!isDirectPumpFunUser(sourceIxAccounts, isV2Layout, wallet)) continue;
    const mint = isV2Layout ? sourceIxAccounts[1] : sourceIxAccounts[2];
    if (!mint) continue;

    const data = ixDataToBuffer((ix as unknown as { data?: unknown }).data);
    const signature = signatureToBase58(info.signature);

    if (
      discriminatorMatches(data, BUY_DISCRIMINATOR) ||
      discriminatorMatches(data, BUY_V2_DISCRIMINATOR)
    ) {
      if (data.length < 24) continue;
      const maxSolCost = Number(data.readBigUInt64LE(16));
      if (!Number.isSafeInteger(maxSolCost) || maxSolCost <= 0) continue;
      return {
        signature,
        wallet,
        inputMint: WRAPPED_SOL_MINT,
        outputMint: mint,
        amountRaw: maxSolCost,
        isNativeInput: true,
        sourceIxAccounts,
        sourceIxDataBase64: data.toString("base64"),
      };
    }

    if (
      discriminatorMatches(data, BUY_EXACT_SOL_IN_DISCRIMINATOR) ||
      discriminatorMatches(data, BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR)
    ) {
      if (data.length < 16) continue;
      const spendableSol = Number(data.readBigUInt64LE(8));
      if (!Number.isSafeInteger(spendableSol) || spendableSol <= 0) continue;
      return {
        signature,
        wallet,
        inputMint: WRAPPED_SOL_MINT,
        outputMint: mint,
        amountRaw: spendableSol,
        isNativeInput: true,
        sourceIxAccounts,
        sourceIxDataBase64: data.toString("base64"),
      };
    }

    if (
      discriminatorMatches(data, SELL_DISCRIMINATOR) ||
      discriminatorMatches(data, SELL_V2_DISCRIMINATOR)
    ) {
      if (data.length < 16) continue;
      const tokenAmount = Number(data.readBigUInt64LE(8));
      if (!Number.isSafeInteger(tokenAmount) || tokenAmount <= 0) continue;
      return {
        signature,
        wallet,
        inputMint: mint,
        outputMint: WRAPPED_SOL_MINT,
        amountRaw: tokenAmount,
        isNativeInput: false,
      };
    }
  }

  return null;
}

function extractTopLevelPumpSwapBuyFromRabbit(
  info: SubscribeUpdateTransactionInfo,
  wallet: string
): DetectedSwap | null {
  if (!info.meta || info.meta.err) return null;
  const message = info.transaction?.message;
  if (!message) return null;

  const accountKeys = (message.accountKeys ?? []).map(pubkeyToBase58);
  if (!accountKeys.includes(wallet)) return null;

  for (const ix of message.instructions ?? []) {
    const programId = accountKeys[Number(ix.programIdIndex)];
    if (programId !== PUMPSWAP_PROGRAM_ID_BASE58) continue;
    const accountIndexes = (
      ix.accounts instanceof Uint8Array ? Array.from(ix.accounts) : Array.from(ix.accounts ?? [])
    )
      .map((idx) => Number(idx))
      .filter((idx) => Number.isInteger(idx) && idx >= 0);
    if (accountIndexes.length < 17) continue;
    const keys = accountIndexes.map((idx) => accountKeys[idx]!).filter(Boolean);
    // PumpSwap account slot 1 is the user signer. Do not treat tx-level account inclusion as a trade.
    if (keys[1] !== wallet) continue;
    const baseMint = keys[3];
    const quoteMint = keys[4];
    if (!baseMint || !isMirrorQuoteMint(quoteMint)) continue;

    const data = ixDataToBuffer((ix as unknown as { data?: unknown }).data);
    const isBuy = discriminatorMatches(data, PUMPSWAP_BUY_DISCRIMINATOR);
    const isExactQuoteIn = discriminatorMatches(data, PUMPSWAP_BUY_EXACT_QUOTE_IN_DISCRIMINATOR);
    if ((!isBuy && !isExactQuoteIn) || data.length < 24) continue;
    const quoteIn = Number(data.readBigUInt64LE(isExactQuoteIn ? 8 : 16));
    const minOrBaseOut = Number(data.readBigUInt64LE(isExactQuoteIn ? 16 : 8));
    if (!Number.isSafeInteger(quoteIn) || quoteIn <= 0) continue;

    const sourcePumpSwapAccounts = accountIndexes.map((idx) => accountMetaFromMessage(info, accountKeys, idx));
    if (sourcePumpSwapAccounts.some((account) => account == null)) continue;

    return {
      signature: signatureToBase58(info.signature),
      wallet,
      inputMint: quoteMint,
      outputMint: baseMint,
      amountRaw: quoteIn,
      isNativeInput: quoteMint === WRAPPED_SOL_MINT,
      counterpartyAmountRaw: Number.isSafeInteger(minOrBaseOut) && minOrBaseOut > 0 ? minOrBaseOut : undefined,
      sourceQuoteMint: quoteMint,
      sourceQuoteAmountRaw: quoteIn,
      sourcePumpSwapAccounts: sourcePumpSwapAccounts as SourcePumpSwapAccountMeta[],
    };
  }

  return null;
}

export type RabbitUnsubscribe = () => Promise<void>;

export async function subscribeRabbitWalletSwaps(
  trackedWallets: string[],
  processedSignatures: SignatureDedupe,
  onSwap: (swap: DetectedSwap) => void
): Promise<RabbitUnsubscribe> {
  if (!shyftRabbitStreamUrl || !shyftGrpcXToken) {
    console.warn("RabbitStream: SHYFT_RABBITSTREAM_URL or SHYFT_GRPC_X_TOKEN missing; skipping.");
    return async () => {};
  }

  const clientExport = YellowstoneGrpc.default as unknown;
  const ClientCtor = (
    typeof clientExport === "function" ? clientExport : (clientExport as MaybeWrappedDefault).default
  ) as YellowstoneClientConstructor | undefined;
  if (typeof ClientCtor !== "function") {
    throw new TypeError("RabbitStream: Yellowstone client constructor could not be loaded.");
  }

  let stopped = false;
  let activeStream: { destroy: () => void } | null = null;
  const client = new ClientCtor(shyftRabbitStreamUrl, shyftGrpcXToken, undefined);
  const request = buildRequest(trackedWallets);

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
            const swap =
              extractDirectPumpFunSwapFromRabbit(info, wallet) ??
              extractTopLevelPumpSwapBuyFromRabbit(info, wallet);
            if (!swap) continue;
            processedSignatures.add(signature);
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
            swap.detector = "shyft-rabbitstream";
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
            console.log(
              `RabbitStream detected direct pump.fun swap ${signature.slice(0, 8)}...` +
                (ageMs != null && Number.isFinite(ageMs) ? ` (age ${ageMs}ms)` : "")
            );
            onSwap(swap);
            return;
          }
        });

        await writeRequest(stream, request);
        console.log(
          `RabbitStream: subscribed to ${trackedWallets.length} wallet(s) via ${shyftRabbitStreamUrl} (direct pump.fun fast-path)`
        );
        await streamClosed;
      } catch (err) {
        if (stopped) break;
        const msg = err instanceof Error ? err.message : String(err);
        console.warn(`RabbitStream watcher error: ${msg}; reconnecting in ${shyftGrpcReconnectMs}ms`);
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
