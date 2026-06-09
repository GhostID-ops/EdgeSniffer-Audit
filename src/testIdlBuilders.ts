import { PublicKey, TransactionInstruction } from "@solana/web3.js";
import {
  assertIdlAccountOrder,
  assertNativeIdlInstruction,
  buildNativeIdlInstruction,
  buildNativeIdlInstructionFromOrderedAccounts,
  getNativeIdlInstruction,
  getNativeIdlDiscriminator,
} from "./idlNativeBuilder.js";
import { PUMPSWAP_PROGRAM_ID, encodeBuyExactQuoteIn, encodeSell } from "./pumpswap/route.js";
import { buildPumpFunBuyIx } from "./pumpfun/buy.js";
import { buildPumpFunSellIx, buildPumpFunSellV2Ix } from "./pumpfun/sell.js";
import { TOKEN_2022_PROGRAM_ID, TOKEN_PROGRAM_ID } from "./pumpfun/constants.js";

function assertDiscriminator(program: "pump" | "pump_amm", name: string, expected: number[]): void {
  const actual = [...getNativeIdlDiscriminator(program, name)];
  if (actual.join(",") !== expected.join(",")) {
    throw new Error(`${program}.${name} discriminator mismatch: ${actual.join(",")}`);
  }
}

assertIdlAccountOrder("pump", "buy", [
  "global",
  "fee_recipient",
  "mint",
  "bonding_curve",
  "associated_bonding_curve",
  "associated_user",
  "user",
  "system_program",
  "token_program",
  "creator_vault",
  "event_authority",
  "program",
  "global_volume_accumulator",
  "user_volume_accumulator",
  "fee_config",
  "fee_program",
]);

assertIdlAccountOrder("pump", "buy_exact_quote_in_v2", [
  "global",
  "base_mint",
  "quote_mint",
  "base_token_program",
  "quote_token_program",
  "associated_token_program",
  "fee_recipient",
  "associated_quote_fee_recipient",
  "buyback_fee_recipient",
  "associated_quote_buyback_fee_recipient",
  "bonding_curve",
  "associated_base_bonding_curve",
  "associated_quote_bonding_curve",
  "user",
  "associated_base_user",
  "associated_quote_user",
  "creator_vault",
  "associated_creator_vault",
  "sharing_config",
  "global_volume_accumulator",
  "user_volume_accumulator",
  "associated_user_volume_accumulator",
  "fee_config",
  "fee_program",
  "system_program",
  "event_authority",
  "program",
]);

assertIdlAccountOrder("pump_amm", "buy_exact_quote_in", [
  "pool",
  "user",
  "global_config",
  "base_mint",
  "quote_mint",
  "user_base_token_account",
  "user_quote_token_account",
  "pool_base_token_account",
  "pool_quote_token_account",
  "protocol_fee_recipient",
  "protocol_fee_recipient_token_account",
  "base_token_program",
  "quote_token_program",
  "system_program",
  "associated_token_program",
  "event_authority",
  "program",
  "coin_creator_vault_ata",
  "coin_creator_vault_authority",
  "global_volume_accumulator",
  "user_volume_accumulator",
  "fee_config",
  "fee_program",
]);

assertIdlAccountOrder("pump_amm", "sell", [
  "pool",
  "user",
  "global_config",
  "base_mint",
  "quote_mint",
  "user_base_token_account",
  "user_quote_token_account",
  "pool_base_token_account",
  "pool_quote_token_account",
  "protocol_fee_recipient",
  "protocol_fee_recipient_token_account",
  "base_token_program",
  "quote_token_program",
  "system_program",
  "associated_token_program",
  "event_authority",
  "program",
  "coin_creator_vault_ata",
  "coin_creator_vault_authority",
  "fee_config",
  "fee_program",
]);

assertDiscriminator("pump", "buy", [102, 6, 61, 18, 1, 218, 235, 234]);
assertDiscriminator("pump", "buy_exact_quote_in_v2", [194, 171, 28, 70, 104, 77, 91, 47]);
assertDiscriminator("pump_amm", "buy_exact_quote_in", [198, 46, 21, 82, 180, 217, 232, 112]);
assertDiscriminator("pump_amm", "sell", [51, 230, 133, 164, 1, 127, 131, 173]);

function dummyPubkey(seed: number): PublicKey {
  const bytes = Buffer.alloc(32);
  bytes.writeUInt32LE(seed, 0);
  return new PublicKey(bytes);
}

function idlMetas(program: "pump" | "pump_amm", instruction: string, seedBase: number) {
  return getNativeIdlInstruction(program, instruction).accounts.map((account, i) => ({
    pubkey: dummyPubkey(seedBase + i),
    isSigner: account.signer === true,
    isWritable: account.writable === true,
  }));
}

function testNativeInstructionAssertion(): void {
  const idlIx = getNativeIdlInstruction("pump_amm", "sell");
  const accounts: Record<string, PublicKey> = {};
  for (let i = 0; i < idlIx.accounts.length; i++) {
    accounts[idlIx.accounts[i]!.name] = dummyPubkey(i + 1);
  }
  const ix = buildNativeIdlInstruction({
    program: "pump_amm",
    instruction: "sell",
    accounts,
    data: Buffer.alloc(16),
  });
  assertNativeIdlInstruction({ program: "pump_amm", instruction: "sell", ix });

  const badKeys = ix.keys.map((key, idx) => (idx === 0 ? { ...key, isWritable: false } : key));
  const badIx = new TransactionInstruction({
    programId: ix.programId,
    keys: badKeys,
    data: ix.data,
  });
  try {
    assertNativeIdlInstruction({ program: "pump_amm", instruction: "sell", ix: badIx });
  } catch {
    return;
  }
  throw new Error("IDL instruction assertion did not reject a bad writable flag");
}

function testLiveBuildersAgainstIdl(): void {
  const user = dummyPubkey(10);
  const mint = dummyPubkey(11);
  const bondingCurve = dummyPubkey(12);
  const creator = dummyPubkey(13);

  assertNativeIdlInstruction({
    program: "pump",
    instruction: "buy",
    ix: buildPumpFunBuyIx({
      user,
      mint,
      bondingCurve,
      creator,
      tokenProgramId: TOKEN_PROGRAM_ID,
      amount: 1n,
      maxSolCost: 1n,
    }),
  });

  assertNativeIdlInstruction({
    program: "pump",
    instruction: "buy_exact_quote_in_v2",
    ix: buildPumpFunBuyIx({
      user,
      mint,
      bondingCurve,
      creator,
      tokenProgramId: TOKEN_2022_PROGRAM_ID,
      amount: 1n,
      maxSolCost: 1n,
      exactSolIn: true,
    }),
  });

  assertNativeIdlInstruction({
    program: "pump",
    instruction: "sell",
    ix: buildPumpFunSellIx({
      user,
      mint,
      bondingCurve,
      creator,
      tokenProgramId: TOKEN_PROGRAM_ID,
      amount: 1n,
      minSolOutput: 1n,
    }),
  });

  assertNativeIdlInstruction({
    program: "pump",
    instruction: "sell_v2",
    ix: buildPumpFunSellV2Ix({
      user,
      mint,
      bondingCurve,
      creator,
      baseTokenProgramId: TOKEN_2022_PROGRAM_ID,
      amount: 1n,
      minSolOutput: 1n,
      buybackFeeRecipient: dummyPubkey(14),
    }),
  });

  assertNativeIdlInstruction({
    program: "pump_amm",
    instruction: "buy_exact_quote_in",
    ix: new TransactionInstruction({
      programId: PUMPSWAP_PROGRAM_ID,
      keys: idlMetas("pump_amm", "buy_exact_quote_in", 100),
      data: encodeBuyExactQuoteIn(1n, 1n),
    }),
  });

  assertNativeIdlInstruction({
    program: "pump_amm",
    instruction: "sell",
    ix: new TransactionInstruction({
      programId: PUMPSWAP_PROGRAM_ID,
      keys: idlMetas("pump_amm", "sell", 200),
      data: encodeSell(1n, 1n),
    }),
  });
}

function testOrderedAccountBuilderUsesIdlFlags(): void {
  const wrongFlagMetas = idlMetas("pump_amm", "buy_exact_quote_in", 300).map((meta) => ({
    ...meta,
    isSigner: !meta.isSigner,
    isWritable: !meta.isWritable,
  }));
  const ix = buildNativeIdlInstructionFromOrderedAccounts({
    program: "pump_amm",
    instruction: "buy_exact_quote_in",
    accounts: wrongFlagMetas,
    data: encodeBuyExactQuoteIn(1n, 1n),
  });

  assertNativeIdlInstruction({
    program: "pump_amm",
    instruction: "buy_exact_quote_in",
    ix,
  });
}

testNativeInstructionAssertion();
testLiveBuildersAgainstIdl();
testOrderedAccountBuilderUsesIdlFlags();

console.log("IDL builder tests passed");
