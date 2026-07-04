import assert from "node:assert/strict";
import {
  classifyFetchStatus,
  extractFlowEvent,
  scoreEvent,
  windowFeatures,
  type FlowEvent,
  type HeliusTx,
} from "./walletHistoricalPreEntryAudit.js";

const mint = "TestMint111111111111111111111111111111111";
const trader = "Trader1111111111111111111111111111111111";
const pool = "Pool111111111111111111111111111111111111";

const tx: HeliusTx = {
  signature: "sig-buy",
  slot: 123,
  timestamp: 100,
  feePayer: trader,
  nativeTransfers: [
    {
      fromUserAccount: trader,
      toUserAccount: pool,
      amount: 500_000_000,
    },
  ],
  accountData: [
    {
      account: trader,
      nativeBalanceChange: -502_500_000,
      tokenBalanceChanges: [
        {
          userAccount: trader,
          mint,
          rawTokenAmount: { tokenAmount: "100000000", decimals: 6 },
        },
      ],
    },
    {
      account: pool,
      nativeBalanceChange: 500_000_000,
      tokenBalanceChanges: [
        {
          userAccount: pool,
          mint,
          rawTokenAmount: { tokenAmount: "-100000000", decimals: 6 },
        },
      ],
    },
  ],
};

const decoded = extractFlowEvent(tx, mint, 150);
assert(decoded);
assert.equal(decoded.wallet, trader);
assert.equal(decoded.slot, 123);
assert.equal(decoded.side, "buy");
assert.equal(decoded.sol, 0.5);
assert.equal(decoded.tokens, 100);

const events: FlowEvent[] = [
  decoded,
  {
    signature: "sig-buy-2",
    timestamp: 102,
    side: "buy",
    wallet: "Trader2222222222222222222222222222222222",
    sol: 0.4,
    tokens: 80,
    marketCapUsd: 3_000,
  },
  {
    signature: "sig-sell",
    timestamp: 103,
    side: "sell",
    wallet: trader,
    sol: 0.1,
    tokens: 20,
    marketCapUsd: 3_100,
  },
];

const context = windowFeatures(events, 104, 5);
assert.equal(context.buys, 2);
assert.equal(context.sells, 1);
assert.equal(context.uniqueWallets, 2);
assert(Math.abs(context.netBuySol - 0.8) < 1e-9);

const score = scoreEvent(
  { ...decoded, marketCapUsd: 2_500 },
  context,
  false,
  true,
  8,
  8,
  25
);
assert.equal(score, 66);

const coverageBase = {
  strictEvents: 0,
  sameSecondEvents: 0,
  entrySignatureFound: true,
  entryDecoded: true,
  entryWalletMatched: true,
  rawStrictPreEntryTransactions: 0,
  rawSameSecondTransactions: 0,
};
assert.equal(
  classifyFetchStatus({ ...coverageBase, strictEvents: 1 }),
  "measured"
);
assert.equal(
  classifyFetchStatus({ ...coverageBase, sameSecondEvents: 2 }),
  "same_second_only"
);
assert.equal(classifyFetchStatus(coverageBase), "first_seen_entry");
assert.equal(
  classifyFetchStatus({
    ...coverageBase,
    rawStrictPreEntryTransactions: 3,
  }),
  "pre_entry_decode_gap"
);
assert.equal(
  classifyFetchStatus({
    ...coverageBase,
    entrySignatureFound: false,
  }),
  "entry_not_found"
);
assert.equal(
  classifyFetchStatus({
    ...coverageBase,
    entryDecoded: false,
  }),
  "entry_decode_failed"
);

console.log("walletHistoricalPreEntryAudit tests passed");
