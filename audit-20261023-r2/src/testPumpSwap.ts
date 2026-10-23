import assert from "node:assert/strict";
import { PublicKey, type AccountMeta } from "@solana/web3.js";
import {
  PUMPSWAP_PROGRAM_ID,
  PUMPSWAP_BUY_EXACT_QUOTE_IN_DISCRIMINATOR,
  PUMPSWAP_SELL_DISCRIMINATOR,
  buyTemplateToSellTemplate,
  derivePumpSwapUserVolumeAccumulatorPda,
  encodeBuyExactQuoteIn,
  encodeSell,
  isRecoverablePumpSwapFailure,
  pumpSwapPubkeyHintsToMetas,
  replacePumpSwapUserAccounts,
  sourcePumpSwapHintAccountsToMetas,
  validatePumpSwapPoolAccount,
} from "./pumpswap/route.js";
import {
  TOKEN_2022_PROGRAM_ID,
  TOKEN_PROGRAM_ID,
  deriveAta,
} from "./pumpfun/constants.js";
import { WRAPPED_SOL_MINT } from "./tokenRegistry.js";
import { classifyExecutionError } from "./errorClassifier.js";

function key(seed: string): PublicKey {
  const bytes = Buffer.alloc(32);
  bytes.write(seed);
  return new PublicKey(bytes);
}

function metas(count: number): AccountMeta[] {
  return Array.from({ length: count }, (_, i) => ({
    pubkey: key(`acct-${i}`),
    isSigner: false,
    isWritable: true,
  }));
}

function testUserAccountReplacement(): void {
  const user = key("mirror-user");
  const baseMint = key("base-mint");
  const accounts = metas(27);
  const sourceUser = key("source-user");
  accounts[1] = { pubkey: sourceUser, isSigner: true, isWritable: true };
  accounts[14] = { pubkey: new PublicKey("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"), isSigner: false, isWritable: false };
  const sourceVolume = derivePumpSwapUserVolumeAccumulatorPda(sourceUser);
  accounts[20] = { pubkey: sourceVolume, isSigner: false, isWritable: true };
  accounts[23] = {
    pubkey: deriveAta(sourceVolume, new PublicKey(WRAPPED_SOL_MINT), TOKEN_PROGRAM_ID),
    isSigner: false,
    isWritable: true,
  };
  accounts[11] = { pubkey: TOKEN_2022_PROGRAM_ID, isSigner: false, isWritable: false };

  const { accounts: replaced, userBaseAta, userQuoteAta } = replacePumpSwapUserAccounts({
    accounts,
    user,
    baseMint,
    baseTokenProgram: TOKEN_2022_PROGRAM_ID,
  });

  assert.equal(replaced[1]!.pubkey.toBase58(), user.toBase58(), "user signer is replaced");
  assert.equal(replaced[1]!.isSigner, true);
  assert.equal(replaced[1]!.isWritable, true);
  assert.equal(userBaseAta.toBase58(), deriveAta(user, baseMint, TOKEN_2022_PROGRAM_ID).toBase58());
  assert.equal(userQuoteAta.toBase58(), deriveAta(user, new PublicKey(WRAPPED_SOL_MINT), TOKEN_PROGRAM_ID).toBase58());
  assert.equal(replaced[5]!.pubkey.toBase58(), userBaseAta.toBase58(), "base ATA is replaced");
  assert.equal(replaced[6]!.pubkey.toBase58(), userQuoteAta.toBase58(), "quote ATA is replaced");
  assert.equal(
    replaced[14]!.pubkey.toBase58(),
    accounts[14]!.pubkey.toBase58(),
    "PumpSwap associated token program is preserved"
  );
  assert.equal(
    replaced[20]!.pubkey.toBase58(),
    derivePumpSwapUserVolumeAccumulatorPda(user).toBase58(),
    "PumpSwap user volume PDA is replaced"
  );
  assert.notEqual(replaced[20]!.pubkey.toBase58(), accounts[20]!.pubkey.toBase58());
  assert.equal(replaced[20]!.isWritable, true, "PumpSwap user volume PDA is writable");
  assert.equal(replaced[11]!.isWritable, false, "base token program remains read-only");
  const mirrorVolume = derivePumpSwapUserVolumeAccumulatorPda(user);
  assert.equal(
    replaced[23]!.pubkey.toBase58(),
    deriveAta(mirrorVolume, new PublicKey(WRAPPED_SOL_MINT), TOKEN_PROGRAM_ID).toBase58(),
    "cashback ATA is rebound to the mirror wallet volume accumulator"
  );
  assert.equal(replaced[23]!.isWritable, true, "cashback ATA remains writable");

  const variantCashback = metas(27);
  variantCashback[1] = { pubkey: sourceUser, isSigner: true, isWritable: true };
  variantCashback[20] = { pubkey: sourceVolume, isSigner: false, isWritable: true };
  const rebound = replacePumpSwapUserAccounts({
    accounts: variantCashback,
    user,
    baseMint,
    baseTokenProgram: TOKEN_2022_PROGRAM_ID,
  }).accounts;
  assert.equal(
    rebound[23]!.pubkey.toBase58(),
    deriveAta(mirrorVolume, new PublicKey(WRAPPED_SOL_MINT), TOKEN_PROGRAM_ID).toBase58(),
    "a canonical volume layout rebinds variant cashback tails"
  );

  variantCashback[20] = { pubkey: key("noncanonical-volume"), isSigner: false, isWritable: true };
  const unrelatedTail = variantCashback[23]!.pubkey;
  const untouched = replacePumpSwapUserAccounts({
    accounts: variantCashback,
    user,
    baseMint,
    baseTokenProgram: TOKEN_2022_PROGRAM_ID,
  }).accounts;
  assert.equal(untouched[23]!.pubkey.toBase58(), unrelatedTail.toBase58(), "noncanonical layouts fail closed");
}

function testPumpSwapHintAccountFlags(): void {
  const pubkeys = Array.from({ length: 27 }, (_, i) => key(`hint-${i}`));
  const hinted = pumpSwapPubkeyHintsToMetas(pubkeys);

  assert.equal(hinted[1]!.isSigner, true, "hinted user account is the only signer");
  assert.equal(hinted[1]!.isWritable, true, "hinted user account is writable");
  assert.equal(hinted[14]!.isWritable, false, "associated token program remains read-only");
  assert.equal(hinted[20]!.isWritable, true, "user volume accumulator is writable");
  assert.equal(hinted[23]!.isWritable, true, "coin creator vault ATA is writable");

  for (const [idx, meta] of hinted.entries()) {
    if (idx !== 1) assert.equal(meta.isSigner, false, `hinted account ${idx} must not be a signer`);
  }

  const exact = sourcePumpSwapHintAccountsToMetas(
    pubkeys.map((pubkey, idx) => ({
      pubkey: pubkey.toBase58(),
      isSigner: idx === 1 || idx === 9,
      isWritable: idx === 2 || idx === 14,
    }))
  );
  assert.ok(exact);
  assert.equal(exact[9]!.isSigner, true, "exact source signer flag is preserved");
  assert.equal(exact[14]!.isWritable, true, "exact source writable flag is preserved");
}

function testPumpSwapPdaProgramOwner(): void {
  const user = new PublicKey("3EhSTyGTL2RyNKNoip5qpvJnH5YJ4pijn9nQgetXjSms");
  const [expected] = PublicKey.findProgramAddressSync(
    [Buffer.from("user_volume_accumulator"), user.toBuffer()],
    PUMPSWAP_PROGRAM_ID
  );
  assert.equal(derivePumpSwapUserVolumeAccumulatorPda(user).toBase58(), expected.toBase58());
  assert.equal(expected.toBase58(), "5BaDBcZPS1k9Ukiuvoru39TkgQJregSmpTVY2yhA4Rd5");
}

function testRecoverableFailureClassification(): void {
  const cases = [
    "Transaction failed: {\"InstructionError\":[6,{\"Custom\":2006}]}",
    "AnchorError caused by account: user_volume_accumulator. Error Code: ConstraintSeeds.",
    "SlippageToleranceExceeded",
    "blockhash not found",
    "Transaction expired: block height exceeded",
    "Sender did not return a transaction signature",
    "sendTransaction failed",
    "fetch failed",
    "network timeout",
  ];
  for (const msg of cases) {
    assert.equal(isRecoverablePumpSwapFailure(new Error(msg)), true, msg);
  }
  assert.equal(
    isRecoverablePumpSwapFailure(
      new Error("AnchorError thrown in programs/pump-amm/src/instructions/swap/buy.rs:754. Error Code: Overflow. Error Number: 6023. Error Message: Overflow.")
    ),
    false
  );
  assert.equal(isRecoverablePumpSwapFailure(new Error("insufficient liquidity for PumpSwap pool")), false);
  assert.equal(isRecoverablePumpSwapFailure(new Error("private key missing")), false);
}

function testProgramAwareErrorClassification(): void {
  const pumpAmmSlippage = classifyExecutionError(
    "Program pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA failed: custom program error: 0x1774 | {\"Custom\":6004}"
  );
  assert.equal(pumpAmmSlippage.program, "pump_amm");
  assert.equal(pumpAmmSlippage.errorName, "ExceededSlippage");
  assert.equal(pumpAmmSlippage.category, "slippage");
  assert.equal(pumpAmmSlippage.recoverable, true);

  const pumpMintMismatch = classifyExecutionError(
    "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P failed: {\"InstructionError\":[3,{\"Custom\":6004}]}"
  );
  assert.equal(pumpMintMismatch.program, "pump");
  assert.equal(pumpMintMismatch.errorName, "MintDoesNotMatchBondingCurve");
  assert.equal(pumpMintMismatch.category, "account-stale-source");
  assert.equal(pumpMintMismatch.recoverable, true);

  const pumpAmmOverflow = classifyExecutionError(
    "AnchorError thrown in programs/pump-amm/src/instructions/swap/buy.rs:754. Error Code: Overflow. Error Number: 6023. Error Message: Overflow."
  );
  assert.equal(pumpAmmOverflow.program, "pump_amm");
  assert.equal(pumpAmmOverflow.errorName, "Overflow");
  assert.equal(pumpAmmOverflow.category, "liquidity-overflow");
  assert.equal(pumpAmmOverflow.recoverable, false);

  const cashbackAta = classifyExecutionError(
    "Program pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA failed: Error Number: 6061"
  );
  assert.equal(cashbackAta.program, "pump_amm");
  assert.equal(cashbackAta.errorName, "InvalidCashbackAccumulatorAta");
}

function testPoolPrecheckSimulationCases(): void {
  const pool = key("pool");
  const wrongOwner = key("wrong-owner");
  const okAccount = {
    executable: false,
    lamports: 1,
    owner: PUMPSWAP_PROGRAM_ID,
    rentEpoch: 0,
    data: Buffer.from([1]),
  };
  assert.match(validatePumpSwapPoolAccount(pool, null) ?? "", /pool not found/);
  assert.match(
    validatePumpSwapPoolAccount(pool, { ...okAccount, owner: wrongOwner }) ?? "",
    /owner mismatch/
  );
  assert.match(
    validatePumpSwapPoolAccount(pool, { ...okAccount, data: Buffer.alloc(0) }) ?? "",
    /empty\/uninitialized/
  );
  assert.equal(validatePumpSwapPoolAccount(pool, okAccount), null);
}

function testBuyExactQuoteInEncoding(): void {
  const data = encodeBuyExactQuoteIn(180_000_000n, 123_456_789n);
  assert.equal(data.length, 25);
  assert.deepEqual(data.subarray(0, 8), PUMPSWAP_BUY_EXACT_QUOTE_IN_DISCRIMINATOR);
  assert.equal(data.readBigUInt64LE(8), 180_000_000n);
  assert.equal(data.readBigUInt64LE(16), 123_456_789n);
  assert.equal(data.readUInt8(24), 0, "track_volume is encoded as OptionBool::None");
}

function testSellEncoding(): void {
  const data = encodeSell(123_456_789n, 180_000_000n);
  assert.equal(data.length, 24);
  assert.deepEqual(data.subarray(0, 8), PUMPSWAP_SELL_DISCRIMINATOR);
  assert.equal(data.readBigUInt64LE(8), 123_456_789n);
  assert.equal(data.readBigUInt64LE(16), 180_000_000n);
}

function testCashbackBuyToSellConversion(): void {
  const sourceUser = key("cashback-source-user");
  const mirrorUser = key("cashback-mirror-user");
  const baseMint = key("cashback-base-mint");
  const buy = metas(27);
  buy[1] = { pubkey: sourceUser, isSigner: true, isWritable: true };
  const [globalVolume] = PublicKey.findProgramAddressSync(
    [Buffer.from("global_volume_accumulator")],
    PUMPSWAP_PROGRAM_ID
  );
  buy[19] = { pubkey: globalVolume, isSigner: false, isWritable: true };
  const sourceVolume = derivePumpSwapUserVolumeAccumulatorPda(sourceUser);
  buy[20] = { pubkey: sourceVolume, isSigner: false, isWritable: true };
  buy[23] = {
    pubkey: deriveAta(sourceVolume, new PublicKey(WRAPPED_SOL_MINT), TOKEN_PROGRAM_ID),
    isSigner: false,
    isWritable: true,
  };

  const sell = buyTemplateToSellTemplate(buy);
  assert.ok(sell);
  assert.equal(sell.length, 26, "cashback sell has the required 26 accounts");
  assert.equal(sell[21]!.pubkey.toBase58(), buy[23]!.pubkey.toBase58());
  assert.equal(sell[22]!.pubkey.toBase58(), sourceVolume.toBase58());

  const replaced = replacePumpSwapUserAccounts({
    accounts: sell,
    user: mirrorUser,
    baseMint,
    baseTokenProgram: TOKEN_2022_PROGRAM_ID,
    replaceUserVolumeAccumulator: false,
    replaceSellCashbackAccounts: true,
  }).accounts;
  const mirrorVolume = derivePumpSwapUserVolumeAccumulatorPda(mirrorUser);
  assert.equal(replaced[22]!.pubkey.toBase58(), mirrorVolume.toBase58());
  assert.equal(
    replaced[21]!.pubkey.toBase58(),
    deriveAta(mirrorVolume, new PublicKey(WRAPPED_SOL_MINT), TOKEN_PROGRAM_ID).toBase58()
  );
}

function run(): void {
  testUserAccountReplacement();
  testPumpSwapHintAccountFlags();
  testPumpSwapPdaProgramOwner();
  testRecoverableFailureClassification();
  testProgramAwareErrorClassification();
  testPoolPrecheckSimulationCases();
  testBuyExactQuoteInEncoding();
  testSellEncoding();
  testCashbackBuyToSellConversion();
  console.log("PumpSwap tests passed");
}

run();
