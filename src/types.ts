/** A Jupiter swap detected from a tracked wallet's transaction. */
export type DetectedSwap = {
  signature: string;
  wallet: string;
  /** Feed/parser that first produced this signal (grpc, rabbitstream, ws-raw, poll, shredstream, etc.). */
  detector?: string;
  /** Millisecond timestamp from the upstream provider, when available. */
  providerCreatedAtMs?: number;
  /** Local wall-clock timestamp when this bot process received the provider event. */
  receivedAtMs?: number;
  /** Local wall-clock timestamp after parsing/classification produced this DetectedSwap. */
  detectedAtMs?: number;
  /** Upstream slot for the source transaction, when the detector provides it. */
  sourceSlot?: number;
  /** Best-effort transaction index within the upstream slot/entry stream, when available. */
  sourceTransactionIndex?: number;
  /** Input token mint (e.g. wrapped SOL). */
  inputMint: string;
  /** Output token mint. */
  outputMint: string;
  /** Amount in raw units (lamports for SOL). */
  amountRaw: number;
  /** True if input is native SOL (we clamp by min/max SOL). */
  isNativeInput: boolean;
  /**
   * Counterparty side amount in raw units. For a buy (SOL→token), this is the token amount the
   * source received. For a sell, the SOL the source received. Used to compute the source's effective
   * fill price for drift checks. Optional because not all extractors populate it.
   */
  counterpartyAmountRaw?: number;
  /** Decimals of the counterparty token. Needed alongside counterpartyAmountRaw for ratio math. */
  counterpartyDecimals?: number;
  /**
   * Base58 pubkeys of the source's pump.fun buy ix accounts, in order. When present, the bot
   * uses these directly (replacing only user-specific slots 5, 6, 13) instead of deriving and
   * fetching them. This is critical for Token-2022 pump.fun launches because slots fix1 (protocol
   * fee recipient) and REM0/REM1 (creator share + buyback recipient) rotate per-coin and aren't
   * derivable — they must come from the source tx, which isn't yet confirmable on hot launches.
   * Populated by the gRPC watcher when the source's tx contains a pump.fun buy ix. Undefined
   * for swaps from other detectors (WS) or non-pump.fun sources.
   */
  sourceIxAccounts?: string[];
  /**
   * Raw source pump.fun instruction data, base64 encoded. When present with
   * sourceIxAccounts, the bot can scale/replay the same Pump instruction layout
   * without waiting on a live bonding-curve RPC read.
   */
  sourceIxDataBase64?: string;
  /**
   * Base58 pubkeys of the source's PumpSwap AMM buy ix accounts, in order. When present, the
   * graduated-token direct path can reuse the source's exact pool/global/fee accounts without
   * waiting for getTransaction to become available.
   */
  sourcePumpSwapAccounts?: string[];
  /** Original quote mint used by the source wallet when it differs from the mirrored input. */
  sourceQuoteMint?: string;
  /** Original quote amount used by the source wallet, in raw units. */
  sourceQuoteAmountRaw?: number;
  /**
   * Internal recovery flag. When true, buy mirroring skips direct pump.fun/PumpSwap routes and
   * goes straight to the normal Jupiter path.
   */
  skipDirectBuyRoutes?: boolean;
  /** Number of failed-buy recovery attempts already used for this source signal. */
  buyRecoveryAttempt?: number;
  /** Last failure that caused this recovery attempt. */
  buyRecoveryReason?: string;
  /**
   * Internal recovery flag for pump.fun bonding-curve buys. When true, the direct buy path ignores
   * source ix account metas and rebuilds from fresh curve-derived accounts.
   */
  forceDerivedPumpFunBuy?: boolean;
};

/** A Pump.fun buy already built/signed/broadcast by the Rust sidecar hot path. */
export type RustHotBuy = {
  sourceSignature: string;
  wallet: string;
  mint: string;
  signature: string;
  sourceAmountRaw: number;
  solInLamports: number;
  tokensOutRaw: number;
  route?: string;
  senderPath?: string;
  elapsedMs?: number;
  buildMs?: number;
  broadcastMs?: number;
  txBytes?: number;
  sourceSlot?: number;
  sourceTransactionIndex?: number;
  detectedAtMs?: number;
  receivedAtMs?: number;
};
