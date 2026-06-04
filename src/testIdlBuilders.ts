import { assertIdlAccountOrder, getNativeIdlDiscriminator } from "./idlNativeBuilder.js";

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

console.log("IDL builder tests passed");

