import assert from "node:assert/strict";
import { PublicKey, type AccountMeta } from "@solana/web3.js";
import {
  PUMPSWAP_PROGRAM_ID,
  derivePumpSwapUserVolumeAccumulatorPda,
  isRecoverablePumpSwapFailure,
  replacePumpSwapUserAccounts,
  validatePumpSwapPoolAccount,
} from "./pumpswap/route.js";
import {
  TOKEN_2022_PROGRAM_ID,
  TOKEN_PROGRAM_ID,
  deriveAta,
} from "./pumpfun/constants.js";
import { WRAPPED_SOL_MINT } from "./tokenRegistry.js";

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
  accounts[1] = { pubkey: key("source-user"), isSigner: true, isWritable: true };
  accounts[20] = { pubkey: key("source-volume"), isSigner: false, isWritable: true };

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
    replaced[20]!.pubkey.toBase58(),
    derivePumpSwapUserVolumeAccumulatorPda(user).toBase58(),
    "PumpSwap user volume PDA is replaced"
  );
  assert.notEqual(replaced[20]!.pubkey.toBase58(), accounts[20]!.pubkey.toBase58());
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
    "insufficient liquidity for PumpSwap pool",
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
  assert.equal(isRecoverablePumpSwapFailure(new Error("private key missing")), false);
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

function run(): void {
  testUserAccountReplacement();
  testPumpSwapPdaProgramOwner();
  testRecoverableFailureClassification();
  testPoolPrecheckSimulationCases();
  console.log("PumpSwap tests passed");
}

run();
