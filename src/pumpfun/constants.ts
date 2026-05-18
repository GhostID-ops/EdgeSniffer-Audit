import { PublicKey } from "@solana/web3.js";

/** Pump.fun bonding-curve program (mainnet). */
export const PUMP_FUN_PROGRAM_ID = new PublicKey("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P");

/** Pump fee program used by current pump.fun trade instructions. */
export const PUMP_FUN_FEE_PROGRAM_ID = new PublicKey("pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ");

/** Trading-fee recipient. Read on-chain from the global account; this is the historical default. */
export const PUMP_FUN_FEE_RECIPIENT = new PublicKey("CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM");

/**
 * Buyback fee recipient — required as a remaining account on Token-2022 pump.fun buys.
 * Without it the program rejects with `BuybackFeeRecipientMissing` (custom error 6062).
 * Constant across all pump.fun launches (verified by inspecting multiple source txs from
 * different wallets/routers). Used as a fallback when source-tx fetch races confirmation.
 */
export const PUMP_FUN_BUYBACK_FEE_RECIPIENT = new PublicKey(
  "5YxQFdt3Tr9zJLvkFccqXVUwhdTWJQc1fFg2YPbxvxeD"
);

/** SPL Token program (classic). Older pump.fun mints use this. */
export const TOKEN_PROGRAM_ID = new PublicKey("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");

/** Token-2022 program. Newer pump.fun mints use this — must detect per-mint via getAccountInfo. */
export const TOKEN_2022_PROGRAM_ID = new PublicKey("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb");

/** All token programs we recognize as valid mint owners. */
export const KNOWN_TOKEN_PROGRAM_IDS: readonly PublicKey[] = [TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID];

/** Associated Token Account program. */
export const ASSOCIATED_TOKEN_PROGRAM_ID = new PublicKey("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL");

/** SystemProgram, Rent sysvar — re-export for convenience. */
export const SYSTEM_PROGRAM_ID = new PublicKey("11111111111111111111111111111111");
export const RENT_SYSVAR_ID = new PublicKey("SysvarRent111111111111111111111111111111111");

/** Anchor discriminators (sha256("global:<name>")[0..8]). */
export const BUY_DISCRIMINATOR = Buffer.from([102, 6, 61, 18, 1, 218, 235, 234]);
export const BUY_EXACT_SOL_IN_DISCRIMINATOR = Buffer.from([56, 252, 116, 8, 158, 223, 205, 95]);
export const BUY_V2_DISCRIMINATOR = Buffer.from([184, 23, 238, 97, 103, 197, 211, 61]);
export const BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR = Buffer.from([194, 171, 28, 70, 104, 77, 91, 47]);
export const SELL_DISCRIMINATOR = Buffer.from([51, 230, 133, 164, 1, 127, 131, 173]);
export const SELL_V2_DISCRIMINATOR = Buffer.from([93, 246, 130, 60, 231, 233, 64, 178]);

/** Pump.fun trading fee, in basis points (1% historically). */
export const PUMP_FUN_TRADE_FEE_BPS = 100;

/**
 * Derive the bonding curve PDA for a given mint.
 * Seeds: ["bonding-curve", mint].
 */
export function deriveBondingCurvePda(mint: PublicKey): PublicKey {
  const [pda] = PublicKey.findProgramAddressSync(
    [Buffer.from("bonding-curve"), mint.toBuffer()],
    PUMP_FUN_PROGRAM_ID
  );
  return pda;
}

/**
 * Derive the global config PDA. Seeds: ["global"]. Holds canonical fee_recipient and other params.
 */
export function deriveGlobalPda(): PublicKey {
  const [pda] = PublicKey.findProgramAddressSync(
    [Buffer.from("global")],
    PUMP_FUN_PROGRAM_ID
  );
  return pda;
}

/**
 * Derive the event-authority PDA. Seeds: ["__event_authority"]. Required as readonly account on every ix.
 */
export function deriveEventAuthorityPda(): PublicKey {
  const [pda] = PublicKey.findProgramAddressSync(
    [Buffer.from("__event_authority")],
    PUMP_FUN_PROGRAM_ID
  );
  return pda;
}

export function deriveCreatorVaultPda(creator: PublicKey): PublicKey {
  const [pda] = PublicKey.findProgramAddressSync(
    [Buffer.from("creator-vault"), creator.toBuffer()],
    PUMP_FUN_PROGRAM_ID
  );
  return pda;
}

export function deriveGlobalVolumeAccumulatorPda(): PublicKey {
  const [pda] = PublicKey.findProgramAddressSync(
    [Buffer.from("global_volume_accumulator")],
    PUMP_FUN_PROGRAM_ID
  );
  return pda;
}

export function deriveUserVolumeAccumulatorPda(user: PublicKey): PublicKey {
  const [pda] = PublicKey.findProgramAddressSync(
    [Buffer.from("user_volume_accumulator"), user.toBuffer()],
    PUMP_FUN_PROGRAM_ID
  );
  return pda;
}

export function deriveFeeConfigPda(): PublicKey {
  const [pda] = PublicKey.findProgramAddressSync(
    [Buffer.from("fee_config"), PUMP_FUN_PROGRAM_ID.toBuffer()],
    PUMP_FUN_FEE_PROGRAM_ID
  );
  return pda;
}

export function deriveSharingConfigPda(mint: PublicKey): PublicKey {
  const [pda] = PublicKey.findProgramAddressSync(
    [Buffer.from("sharing-config"), mint.toBuffer()],
    PUMP_FUN_FEE_PROGRAM_ID
  );
  return pda;
}

/**
 * Derive the ATA address for (owner, mint, tokenProgram). The token program is part of the seeds,
 * so a Token-2022 mint has a different ATA than a classic-SPL mint with the same address.
 * Defaults to classic Token for backwards-compat with older callsites.
 */
export function deriveAta(
  owner: PublicKey,
  mint: PublicKey,
  tokenProgramId: PublicKey = TOKEN_PROGRAM_ID
): PublicKey {
  const [pda] = PublicKey.findProgramAddressSync(
    [owner.toBuffer(), tokenProgramId.toBuffer(), mint.toBuffer()],
    ASSOCIATED_TOKEN_PROGRAM_ID
  );
  return pda;
}
